"""The Claude Code CLI as a chat backend (text, and images over stream-json), for the platform
OWNER's own requests only.

Ollama is the broker's general text backend and stays that way. This module adds ONE more
destination for a chat call, subject to a rule that is a licence condition rather than a
preference: Anthropic permits OAuth (subscription) credentials for "ordinary use" by the
subscriber and explicitly does not permit routing requests "through Free, Pro, or Max plan
credentials on behalf of their users". So a CLI call is legitimate only when the person who
asked IS the subscriber. The owner check lives in `Broker.cli_decision`; everything here
assumes that decision has already been made and concerns itself with running the process
correctly.

Three design rules follow, and all three are load-bearing:

* **Every failure is a FALLBACK, never an error.** A missing CLI, an expired login, a usage
  limit, a timeout, a non-JSON answer to a JSON request -- all raise `CliUnavailable`, and the
  broker then serves the call from Ollama and announces the substitution. The worst case for a
  rail is a few seconds of latency, never a broken job. A rail must not acquire a new way to
  fail because the owner turned this on. `Broker.chat` therefore catches BaseException-free
  `Exception` around the whole call, not just this module's own type: a guarantee that holds
  only for anticipated errors is not a guarantee.
* **Translation refuses rather than approximates.** Where an Ollama concept cannot be honoured
  this refuses and falls back: an image in a format Claude does not read, past Claude's own size
  or count limits, or on any turn that is not the user's (see `vision_message`). Where it can only be
  approximated (`format="json"` without a schema, a prefill: see `split_prefill`) the result is
  VALIDATED before it is returned.
  Where the CLI can honour it exactly (`--json-schema`, an `images:` part as a base64 image block
  over `--input-format stream-json`) that is used in preference to approximating.
* **Nothing of the owner's personal environment enters a rail's prompt.** `--setting-sources ""`
  loads no user, project or local settings, so their hooks, `env` overrides, model pin and
  plugins cannot reach a call the platform makes on a rail's behalf. Measured on claude 2.1.292,
  one cold call each, same prompt: 14,183 input tokens with no isolation, 5,085 with
  `claudeMdExcludes` alone, **820 with `--setting-sources ""`**. The token saving is the small
  half; a `UserPromptSubmit` hook running under the broker's service account on every rail call
  is the real one.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import json
import os
import re
import shutil
import signal
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

import anyio

#: How long to wait for the process tree to die after a kill. Short on purpose: this runs
#: inside a request, and an unreapable child is a log line, not a reason to hold the caller.
_REAP_TIMEOUT = 10.0

#: The only platform fact _kill_tree and chat() branch on, as a module attribute a test can set
#: (rather than replacing the module's whole `sys`). Windows takes the tree with taskkill /T;
#: everywhere else the CLI is started as the leader of its own session, so its process GROUP is
#: the tree, and the group is what gets killed.
_POSIX = sys.platform != "win32"


def _killpg(pgid: int) -> None:
    """SIGKILL a whole process group. A seam of its own so tests of the fake-process paths can
    record the call instead of signalling a real group with a made-up id."""
    os.killpg(pgid, signal.SIGKILL)

#: Bounds on what the child can make the broker HOLD, enforced while it is still writing.
#: stdout is read in `_READ_CHUNK` pieces and the call is refused (and the tree killed) the
#: moment the running total passes `_STDOUT_CAP`, so a runaway child costs at most the cap plus
#: one chunk. It used to be a post-hoc check after `communicate()`, which had already read the
#: whole stream into RAM -- a child that never stopped writing was buffered until the timeout,
#: and only then refused. stderr is drained to the end (a child blocked on a full stderr pipe
#: would never exit) but only its first `_STDERR_CAP` bytes are kept.
_STDOUT_CAP = 8 * 1024 * 1024
_STDERR_CAP = 64 * 1024
_READ_CHUNK = 64 * 1024

#: Belt and braces beside `--setting-sources ""`. That flag already keeps the owner's
#: user-scope `~/.claude/CLAUDE.md` out (measured: 820 tokens with it, 816 with both, i.e. no
#: difference), but `--settings` is documented to apply even when setting SOURCES are off, so
#: this stays as the guard that still holds if an operator re-enables a source.
DEFAULT_SETTINGS_JSON = json.dumps(
    {"claudeMdExcludes": ["**/.claude/CLAUDE.md", "**/.claude/rules/**"]})

#: Appended to the system prompt for `format="json"` WITHOUT a schema. With a schema the CLI
#: enforces the shape itself and this is not used.
_JSON_INSTRUCTION = (
    "Reply with a single raw JSON value and nothing else. "
    "No prose before or after it, no markdown code fences, no explanation."
)

#: Appended to the system prompt of a call that ends in a prefill (`split_prefill`). Ollama
#: continues a trailing assistant turn and `claude -p` has no such turn, so the model is asked to
#: BEGIN its reply with that text, and the reply is verified before any of it is returned
#: (`continuation`). The opening is shown with its outer whitespace trimmed: a trailing space or
#: line break is invisible in an instruction, so `continuation` reads it as a boundary instead.
_PREFILL_INSTRUCTION = (
    "Your reply has already been started, and you are continuing it. Begin your reply with "
    "exactly the text between the two marker lines below, character for character, then "
    "continue naturally from where it stops. Put nothing before it, and never repeat or mention "
    "the marker lines or this instruction.\n"
    "<<<\n{opening}\n>>>"
)

#: Ollama `options` keys the CLI has no equivalent for. Recorded on the response and logged
#: rather than silently discarded: a rail that asked for temperature 0.3 and got the model's
#: default is entitled to know, even though refusing the call over it would be worse.
_SAMPLING_KEYS = ("temperature", "top_p", "top_k", "seed", "num_predict", "num_ctx",
                  "repeat_penalty", "stop")

#: Whether this client sends a message's `images` to the CLI (`vision_message`, over
#: stream-json). Reported as `vision` in the CLI block of /v1/status and of the Rails tab's one
#: read (`Broker.cli_state` / `cli_summary`), so a gateway can tell this broker from an older one
#: that refused every image call at the door (`vision_unsupported`) and sends no such field. It
#: says what this CODE carries, never whether the backend is ready (`ready` and `blockers` say
#: that) or whether a role may use it (`cli_decision` decides).
#: tests/test_cli_routing.py runs an image call through the real client whenever this is True.
SENDS_IMAGES = True

#: Whether this client streams a TEXT answer as the CLI writes it (`chat_stream`, over
#: `--output-format stream-json --include-partial-messages`). Reported as `streams` in the same
#: two CLI blocks as `vision`, for the same reason: a gateway can tell this broker from an older
#: one, whose /v1/chat/stream never reached the CLI and which sends no such field. What this
#: CODE carries, never whether the backend is ready or a role may use it.
STREAMS_TEXT = True

#: Once a streamed answer has begun, the longest the CLI may go without writing a byte before
#: the call is refused as `stalled` and its tree killed. The FIRST token has its own deadline,
#: what is left of the call's budget (`ClaudeCliClient.timeout`), because a model that thinks
#: first can be silent for a long time before it; after it, a minute of nothing is a stream
#: that stopped. Read at call time, so a test can shorten it.
_STREAM_IDLE_S = 60.0

#: What an image call may carry, at Claude's own limits or below. Source: the Vision guide,
#: https://platform.claude.com/docs/en/build-with-claude/vision ("Request limits", read
#: 2026-10-09): "10 MB (base64-encoded) when using the Claude API directly", "5 MB
#: (base64-encoded) on Amazon Bedrock and Google Cloud"; 100 or 600 images per request
#: depending on the model, but "If a single API request contains more than 20 images, a stricter
#: per-image dimension limit applies to every image"; and the API's 32 MB request size limit.
#: The strictest figure each time, because the child inherits CLAUDE_* and so can be pointed at
#: Bedrock or Vertex by a CLAUDE_CODE_USE_* variable the broker never sets:
#:   * 5,000,000 base64 characters per image (the partner platforms' 5 MB, read as decimal so
#:     it is inside the limit whichever megabyte the guide means);
#:   * 20 images per call, so the 2000 px many-image rule never applies (8000 px does);
#:   * 24,000,000 base64 characters for every image in the call together, which leaves the
#:     prompt and the CLI's own envelope 8 MB of the 32 MB request.
#: Pixel dimensions are not checked here, and neither is anything past an image's first bytes
#: (see `sniff_image_type`). What the API makes of an image over 8000 px, or of a valid header
#: over bytes that are no picture, has NOT been measured: the call falls back if the CLI exits
#: nonzero or marks its result line `is_error`, and would deliver the API's error sentence as
#: the answer if it did neither.
_IMAGE_MAX_B64 = 5_000_000
_IMAGE_MAX_COUNT = 20
_IMAGES_MAX_TOTAL_B64 = 24_000_000

#: The formats Claude reads (same guide, "Supported formats": JPEG, PNG, GIF, WebP), told apart
#: by their own first bytes. The media type sent is ALWAYS one of these four, sniffed from the
#: decoded bytes. Ollama's `images` carries none, so nothing a caller declares reaches the CLI.
#: A header is ALL that is checked: these bytes say which type to announce, not that the rest
#: decodes as an image.
_PNG_MAGIC = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])
_JPEG_MAGIC = bytes([0xFF, 0xD8, 0xFF])
_GIF_MAGICS = (b"GIF87a", b"GIF89a")

#: Substrings that classify a failure from the CLI's own stderr/result text, lowercased.
#: Order matters: the specific sign-in and limit phrases are checked before the generic ones.
_REASON_SIGNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("rate_limited", ("usage limit", "rate limit", "rate_limit", "429", "too many requests",
                      "quota")),
    # "failed to authenticate ... 401 invalid bearer token" is what a bad setup-token actually
    # produces (seen 2026-10-07, when a browser sign-in code was saved in place of the token).
    # The first list missed it -- "authentication" is not a substring of "authenticate" -- and
    # filed it as `failed`, which tells an operator nothing about what to fix.
    ("not_signed_in", ("log in", "login", "sign in", "signin", "not authenticated",
                       "authenticate", "unauthorized", "oauth token", "credentials",
                       "invalid bearer", "invalid token", "invalid api key", "401")),
)


# What the CLI child may inherit from the broker's environment. Exact names (compared
# upper-cased, as Windows treats them) plus a few prefixes. Everything else stays behind.
_CHILD_ENV_NAMES = frozenset({
    # the OS itself
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "OS",
    "PROCESSOR_ARCHITECTURE", "PROCESSOR_IDENTIFIER", "NUMBER_OF_PROCESSORS",
    "COMPUTERNAME", "USERDOMAIN", "USERNAME", "USER", "LOGNAME", "SHELL", "TERM",
    # where the account's files are: the CLI's own login lives under these
    "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA",
    "ALLUSERSPROFILE", "PUBLIC", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432",
    "COMMONPROGRAMFILES", "COMMONPROGRAMFILES(X86)", "COMMONPROGRAMW6432",
    "TEMP", "TMP", "TMPDIR",
    # locale and time
    "LANG", "LANGUAGE", "TZ",
    # the network path out
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS", "NODE_USE_SYSTEM_CA",
})
_CHILD_ENV_PREFIXES = ("CLAUDE_", "LC_", "XDG_")


def _child_may_inherit(name: str) -> bool:
    key = name.upper()
    return key in _CHILD_ENV_NAMES or key.startswith(_CHILD_ENV_PREFIXES)


#: Every CLI call runs with a TEMP of its own (TMP too, and TMPDIR on POSIX: `build_env`): a
#: directory made under the temp root (`call_temp_root`) as the call starts, and removed with
#: everything in it once the call's tree is dead (`_end_call`), however the call ended. The CLI
#: writes under TEMP on every call, `--no-session-persistence` or not. Measured 2026-10-14
#: (claude 2.1.296, one buffered call, TEMP pointed at an empty directory): `claude\<workdir
#: slug>\<session id>\scratchpad\`, four directories, no file in any. The session id is new on
#: every call, so while the child inherited the broker's TEMP they built up in the service
#: account's temp, one per call, for as long as the broker ran.
_CALL_TEMP_PREFIX = "call-"
#: The only names the startup sweep removes (`sweep_call_temps`): what `_new_call_temp` makes.
_CALL_TEMP_NAME = re.compile(r"call-[0-9a-f]{32}")
#: The temp root's name beside BROKER_CLI_WORKDIR, when BROKER_CLI_TEMP_ROOT is unset.
_TEMP_ROOT_NAME = "cli-tmp"
#: A removal that fails, typically a file filter (antivirus, DLP) still scanning what the dead
#: tree left, is tried this many times in all, the first pause between tries
#: `_TEMP_REMOVE_PAUSE` seconds and each one doubled (0.1 + 0.2 + 0.4 + 0.8: at most 1.5 s of
#: waiting). One that outlasts them is logged once and left to the startup sweep. Read at call
#: time.
_TEMP_REMOVE_TRIES = 5
_TEMP_REMOVE_PAUSE = 0.1
#: The startup sweep removes a call's directory once nothing in it has changed for this long:
#: every call is over long before (Broker.sweep_cli_temp waits three BROKER_CLI_TIMEOUTs
#: instead when that is longer), and a younger one may be another broker's call in flight on
#: the same root.
_TEMP_STALE_S = 3600.0


def call_temp_root(temp_root: str = "", workdir: str = "") -> str:
    """Where a CLI call's own temp directory is made: `temp_root` (BROKER_CLI_TEMP_ROOT), else
    `cli-tmp` beside `workdir` (BROKER_CLI_WORKDIR). Beside, not inside: the workdir must stay
    empty, and a sibling shares the place install-services checked the workdir for (outside the
    repo and every OneDrive folder, no CLAUDE.md in any folder above it). A client built with
    neither, which the broker never runs (an unset workdir is the `workdir_unset` blocker),
    uses `claude-cli-tmp` in the system temp directory, so its calls clean up after themselves
    too."""
    root = (temp_root or "").strip()
    if root:
        return root
    work = (workdir or "").strip()
    if work:
        return str(Path(os.path.abspath(work)).parent / _TEMP_ROOT_NAME)
    return os.path.join(tempfile.gettempdir(), "claude-cli-tmp")


class CliUnavailable(RuntimeError):
    """The CLI cannot serve this call. ALWAYS recoverable: the caller falls back to Ollama.

    `reason` is a short stable slug so the broker can count and report them without parsing
    prose: `not_installed`, `not_signed_in`, `rate_limited`, `timeout`, `vision_unsupported`,
    `prefill_unsupported`, `prefill_mismatch`, `bad_json`, `bad_stream`, `empty`, `too_large`,
    `failed`, and on a streamed call `stalled` and `stream_mismatch`. `vision_unsupported` is an
    image this path will not send (see `vision_message`), never "a call that carries images";
    `prefill_unsupported` is a call whose last turn is neither the user's nor a prefill this
    path can emulate (see `split_prefill`), `prefill_mismatch` a reply that did not begin with
    the prefill it was asked to continue (see `continuation`);
    `bad_stream` is a stream-json transcript that ended without its `result` line, or a stream
    that began a second message; `stalled` is a streamed answer that went quiet after it had
    begun; `stream_mismatch` is a result line that disagrees with the text already streamed.
    """

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"claude cli unavailable ({reason}){': ' + detail if detail else ''}")
        self.reason = reason
        self.detail = detail


def _is_executable_path(p: Path) -> bool:
    """A real binary, never a `.cmd` / `.ps1` shim.

    The broker launches with `create_subprocess_exec` and no shell (the repo's standing rule
    for every subprocess), and a shim is a script only a shell can run. npm installs both
    spellings side by side and `which` prefers the shim on Windows, so the `.exe` has to be
    asked for by name.
    """
    return p.is_file() and p.suffix.lower() in (".exe", "")


def resolve_cli_exe(configured: str = "") -> str | None:
    """Absolute path to a real `claude` EXECUTABLE, or None.

    The shim rule applies to a CONFIGURED path too, which it did not at first. An operator
    running `Get-Command claude` on this box is handed `...\\npm\\claude.ps1`, and pasting that
    into `BROKER_CLI_EXE` used to pass `is_file()` and be accepted -- after which status
    reported the backend ready while every call died in WinError 193 and was reported as
    `not_installed`, i.e. "the CLI is absent" about a CLI that was present and misconfigured.
    """
    cand = (configured or "").strip()
    if cand:
        p = Path(cand)
        return str(p) if _is_executable_path(p) else None
    for name in ("claude.exe", "claude"):
        found = shutil.which(name)
        if found and _is_executable_path(Path(found)):
            return found
    # npm's global root, where the real binary sits next to the shims `which` finds first.
    appdata = os.environ.get("APPDATA", "")
    if appdata:
        npm = (Path(appdata) / "npm" / "node_modules" / "@anthropic-ai" / "claude-code"
               / "bin" / "claude.exe")
        if npm.is_file():
            return str(npm)
    local = Path.home() / ".local" / "bin" / "claude.exe"
    return str(local) if local.is_file() else None


#: The characters extract_json's scan acts on. Every other character matters only as the one
#: after a backslash inside a string, and the scan sees that as a gap after the backslash.
_JSON_MARKS = re.compile(r'[\[\]{}"\\]')
_JSON_DECODER = json.JSONDecoder()
#: How much the decoder may read, in characters, in candidates that turn out not to parse,
#: before extract_json gives up: four times the text, and never less than this floor. A real
#: reply spends at most about the text once (an invalid outer value, then the value inside it);
#: only a pile of long nested candidates that all fail can reach it, and each costs a parse.
_JSON_PARSE_FLOOR = 64 * 1024
#: From this many characters a JSON answer is read in a thread (`_extract_json_off_loop`), below
#: it inline. 64 Ki characters with prose around the value cost extract_json about 3.7 ms here,
#: under the GIL's 5 ms switch interval: a thread could not give the loop a turn before the scan
#: ended anyway, and would add its own round trip (about 0.1 ms). Past it the cost grows past the
#: interval (7 ms at 128 KiB, about 60 ms a MiB), and a thread hands the loop a turn at least
#: every interval while its Python runs.
_JSON_THREAD_FROM = 64 * 1024


class _JsonScan:
    """Openers that have read the same characters in the same string state since the latest of
    them joined, so from here on they see every character alike and one count per bracket kind
    serves them all (kind 0 is `{}`, kind 1 is `[]`). `pending[kind]` maps the count at which an
    opener's value ends to the openers that end there; `size` is how many are still open."""

    __slots__ = ("level", "pending", "size")

    def __init__(self) -> None:
        self.level = [0, 0]
        self.pending: list[dict[int, list[int]]] = [{}, {}]
        self.size = 0

    def open(self, kind: int, at: int) -> None:
        self.pending[kind].setdefault(self.level[kind], []).append(at)
        self.level[kind] += 1
        self.size += 1

    def close(self, kind: int, at: int, spans: list[tuple[int, int]]) -> bool:
        """A closer of `kind` at `at`: the openers it ends become spans. True when none is left
        open, so the caller can drop this scan."""
        self.level[kind] -= 1
        ended = self.pending[kind].pop(self.level[kind], None)
        if ended:
            spans.extend((start, at) for start in ended)
            self.size -= len(ended)
        return self.size == 0


def _join_scans(a: _JsonScan | None, b: _JsonScan | None) -> _JsonScan | None:
    """Two scans that have just reached the same string state: one scan from here on. The
    smaller moves into the larger, its counts shifted into the larger's, so no opener is moved
    more than log2(n) times."""
    if a is None:
        return b
    if b is None:
        return a
    if a.size < b.size:
        a, b = b, a
    for kind in (0, 1):
        shift = a.level[kind] - b.level[kind]
        into = a.pending[kind]
        for level, openers in b.pending[kind].items():
            into.setdefault(level + shift, []).extend(openers)
    a.size += b.size
    return a


def _json_spans(text: str) -> list[tuple[int, int]]:
    """Every `(start, end)` such that `text[start]` is `{` or `[` and `text[end]` is the bracket
    that closes it, in one pass.

    Each opener is read as if the text began there: outside a string, counting only its own kind
    of bracket, so a quote in the prose before a value (a 27" screen) cannot turn the value's
    strings inside out, and a value that sits inside another one's string is still found. That
    is what the per-opener walk this replaced did, at a cost of one walk to the end of the text
    per opener (8,000 unclosed `[` took 1.3 s). Here the openers that are in the same string
    state share a walk: at any point there are at most three (outside a string, inside one, just
    after a backslash inside one), and two that reach the same state share it from then on."""
    spans: list[tuple[int, int]] = []
    out: _JsonScan | None = None    # openers outside a string here
    inside: _JsonScan | None = None  # inside one
    esc: _JsonScan | None = None     # inside one, just after a backslash
    last = -2
    for mark in _JSON_MARKS.finditer(text):
        at = mark.start()
        if esc is not None and at != last + 1:
            # An ordinary character followed the backslash: escaped, and the string goes on.
            inside, esc = _join_scans(inside, esc), None
        last = at
        c = text[at]
        if c == '"':
            out, inside, esc = inside, _join_scans(out, esc), None
        elif c == "\\":
            # Outside a string a backslash is nothing; inside, it escapes what follows.
            inside, esc = esc, inside
        else:
            kind = 0 if c in "{}" else 1
            if c in "{[":
                if out is None:
                    out = _JsonScan()
                out.open(kind, at)
            elif out is not None and out.close(kind, at, spans):
                out = None
            if esc is not None:
                inside, esc = _join_scans(inside, esc), None
    return spans


def extract_json(text: str) -> str | None:
    """The LARGEST parseable JSON value in `text`, re-serialised, or None if there is none.

    Largest, not first, and that distinction is the whole point. Returning the first parseable
    candidate meant a reply like ``the empty sections stay as {} -- here is the result:
    {"strengths": ...}`` yielded ``{}``: syntactically perfect, validation passes, no fallback
    fires, and a rail that asked for nine sections writes nine blank ones and reports success.
    A blank document delivered as a finished one is precisely the plausible-looking garbage
    this module exists to refuse, and the first version of this function produced it.

    A reply that is one JSON value and nothing else (what the JSON instruction and a schema ask
    for) is parsed as it stands. Otherwise every `{` / `[` is a candidate opener ending at its
    matching close (`_json_spans`, strings and escapes respected, so a brace inside a string
    value cannot end it early), and the candidates are tried longest first, the leftmost of
    equals first, until one parses. Linear in the text; the CLI call runs it in a thread from
    `_JSON_THREAD_FROM` characters (`_extract_json_off_loop`).

    None, as well, when a candidate is nested deeper than the parser can read (a RecursionError:
    a fragment of it is not the answer either), or when candidates that fail to parse have cost
    the decoder more than `_JSON_PARSE_FLOOR` or four times the text, whichever is more.
    """
    if not text:
        return None
    body = text.strip()
    if body[:1] in ("{", "["):
        try:
            value = json.loads(body)
        except (ValueError, RecursionError):
            # Not one value alone, or not readable as one (an unclosed run nested past the
            # parser is no candidate at all, and a value after it still counts): look inside.
            pass
        else:
            return _rewritten(value)
    budget = max(_JSON_PARSE_FLOOR, 4 * len(text))
    spans = _json_spans(text)
    spans.sort(key=lambda span: (span[0] - span[1], span[0]))
    for start, _ in spans:
        # A value that parses from an opener ends where that opener's walk ended (the walk
        # follows the same strings and brackets), so a parse that succeeds is the whole span.
        try:
            value, _ = _JSON_DECODER.raw_decode(text, start)
        except RecursionError:
            return None
        except json.JSONDecodeError as exc:
            budget -= exc.pos - start + 1
            if budget < 0:
                return None
            continue
        return _rewritten(value)
    return None


async def _extract_json_off_loop(text: str) -> str | None:
    """extract_json(text), in a thread for a text of `_JSON_THREAD_FROM` characters or more.

    Its scan is Python, so in a thread it still holds the GIL, but gives it up every switch
    interval, and the loop's other requests go on between: a reply with prose around a 3 MiB
    value held the loop about 210 ms inline. What a thread cannot split is one C call, the
    decoder reading the value it found and the writer serialising it, so a reply that large
    still holds the loop for those (about 50 ms at 3 MiB). A call cancelled meanwhile leaves its
    thread to finish (it is linear, and the CLI's stdout is capped at 8 MiB) and drops the
    answer."""
    if len(text) < _JSON_THREAD_FROM:
        return extract_json(text)
    return await asyncio.to_thread(extract_json, text)


def _rewritten(value: Any) -> str | None:
    """extract_json's answer, serialised, or None for a value nested past what the writer can
    follow: the parser reads a little deeper than it writes (on Python 3.14 here, 16,000 levels
    parse and cannot be written back)."""
    try:
        return json.dumps(value)
    except RecursionError:
        return None


def flatten_messages(messages: list[dict[str, Any]]) -> tuple[str, str]:
    """Ollama's `messages` array -> `(system_prompt, prompt)` for `claude -p`.

    System turns are joined and handed to `--system-prompt`. The rest become one prompt. A
    single user turn -- what every call in this repo actually sends -- passes through verbatim,
    because wrapping a prompt in scaffolding the caller did not write silently changes what the
    model was asked.

    The TEXT path only. `ClaudeCliClient.chat` sends a call whose messages carry `images` through
    `vision_message` instead, so an image never arrives here; it still raises
    `CliUnavailable("vision_unsupported")` for one rather than dropping it, because answering
    about a picture the model never saw is the confident-nonsense failure the broker refuses
    elsewhere, and a guard that holds only while the caller routes correctly is not a guard.
    """
    systems: list[str] = []
    turns: list[tuple[str, str]] = []
    for m in messages:
        if m.get("images"):
            raise CliUnavailable("vision_unsupported",
                                 "a message carries images; the CLI path is text-only")
        role = str(m.get("role") or "user")
        content = m.get("content")
        if not isinstance(content, str):
            content = "" if content is None else str(content)
        if role == "system":
            if content.strip():
                systems.append(content)
        else:
            turns.append((role, content))
    system = "\n\n".join(systems)
    if len(turns) == 1:
        return system, turns[0][1]
    labelled = [f"{'Assistant' if r == 'assistant' else 'User'}: {c}" for r, c in turns]
    return system, "\n\n".join(labelled)


def split_prefill(messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    """`(messages, prefill)`: a call whose last turn (system turns aside) is an assistant turn
    of plain text, as the messages without that turn and its text; any other call unchanged,
    with `""`.

    Ollama reads a trailing assistant turn as a PREFILL: the start of the answer, which the
    model continues, and its answer is the continuation alone (smb-partner-enablement's Scenario
    Builder opens a pass with "Your next move: " and joins the two). `claude -p` has no such
    turn: flattened as `Assistant: ...` it was answered afresh, repeating or contradicting the
    opening. So the CLI path EMULATES it: the turn leaves the prompt, the system prompt asks for
    a reply that begins with its text (`prefill_system`), and the reply is verified and cut to
    what follows that text (`continuation`) before any of it is returned. A reply that does not
    begin with it is `prefill_mismatch`, a fallback.

    Refused before anything starts, `CliUnavailable("prefill_unsupported")`, so the local model
    answers it as it was put, whenever the shape is not plainly that one:

    * a last turn whose role is neither "user" nor exactly "assistant" ("Assistant", "model",
      "tool": the role is a free string on the wire, and Ollama's own spelling is the one it
      continues). A missing or empty role is the user's, as the text path reads it;
    * a prefill carrying images (no text to begin a reply with, and a picture the assistant
      showed would arrive as the asker's own: see `vision_message`);
    * a prefill whose content is not a string, or is blank;
    * a prefill that does not follow a user turn (nothing for it to answer, or a second
      assistant turn in a row).

    The detail names the role and never the turn's text. A call with no turn at all is left to
    the `empty` refusal."""
    last: int | None = None
    for i, m in enumerate(messages):
        if str(m.get("role") or "user") != "system":
            last = i
    if last is None:
        return messages, ""
    turn = messages[last]
    role = str(turn.get("role") or "user")
    if role == "user":
        return messages, ""
    if role != "assistant":
        raise CliUnavailable("prefill_unsupported",
                             f"the last turn is {role[:40]!r}, neither the user's nor an "
                             "assistant turn the CLI path can continue")
    if turn.get("images"):
        raise CliUnavailable("prefill_unsupported",
                             "the assistant turn to continue carries images")
    content = turn.get("content")
    if not isinstance(content, str) or not content.strip():
        raise CliUnavailable("prefill_unsupported",
                             "the assistant turn to continue has no text to begin a reply with")
    rest = messages[:last] + messages[last + 1:]
    before = [str(m.get("role") or "user") for m in rest]
    before = [r for r in before if r != "system"]
    if not before or before[-1] != "user":
        raise CliUnavailable("prefill_unsupported",
                             "the assistant turn to continue does not follow a user turn")
    return rest, content


def prefill_system(system: str, prefill: str) -> str:
    """The system prompt of a call that emulates `prefill` (`split_prefill`): the caller's own,
    then the instruction to begin the reply with the prefill's text."""
    return f"{system}\n\n{_PREFILL_INSTRUCTION.format(opening=prefill.strip())}".strip()


#: `continuation`'s answer while a streamed reply is still too short to judge.
_UNDECIDED = object()


def continuation(reply: str, prefill: str, *, final: bool = True) -> Any:
    """What follows `prefill` in `reply`: the text Ollama returns for a prefilled call, which
    the caller appends to its prefill. None when the reply does not begin with it.

    Leading whitespace is not content on either side: the reply's is skipped (a model may open
    on a blank line) and so is the prefill's. The prefill must then begin the reply exactly,
    case and punctuation included; a reply that bolds the opening, rewords it or leads with a
    preamble is None. The prefill's TRAILING whitespace is a boundary rather than text, because
    the instruction cannot show it to the model:

    * ending in a space or a tab, the reply must break there as well (whitespace or nothing
      next), so "Dear " is not begun by "Dearest";
    * ending in a line break, the reply must break the line there, so a heading is not begun by
      the same heading extended along its line;
    * where the reply's whitespace at that point starts with the prefill's, the rest is kept as
      the reply wrote it (a heading followed by a blank line keeps the blank line); otherwise
      the prefill's whitespace stands for the reply's, so `prefill + continuation` never
      doubles a space.

    A prefill that ends without whitespace is continued as written, mid-word if need be ("**"
    by "**What"). With `final` the reply is whole, and a blank continuation is returned as ""
    (the caller refuses it as `empty`). While a stream is arriving (`final=False`) this answers
    `_UNDECIDED` until the reply holds something past the prefill other than whitespace, or has
    already left it, so a stream holds every frame back until it is decided."""
    text = reply.lstrip()
    core = prefill.strip()
    trail = prefill[len(prefill.rstrip()):]
    if len(text) < len(core):
        return _UNDECIDED if not final and core.startswith(text) else None
    if not text.startswith(core):
        return None
    rest = text[len(core):]
    if not rest.strip():
        return "" if final else _UNDECIDED
    if not trail:
        return rest
    lead = rest[:len(rest) - len(rest.lstrip())]
    if not lead:
        return None     # the reply runs on through the word break the prefill ends with
    if "\n" in trail and "\n" not in lead:
        return None     # the line goes on where the prefill ends it
    return rest[len(trail):] if lead.startswith(trail) else rest[len(lead):]


def _prefill_mismatch() -> CliUnavailable:
    """The refusal of a reply that did not begin with its prefill. The detail quotes neither
    text: both are rail content, and the detail is logged and kept on /v1/status."""
    return CliUnavailable("prefill_mismatch",
                          "the reply did not begin with the text it was asked to continue")


def sniff_image_type(raw: bytes) -> str | None:
    """The media type Claude should be told, read from the image's own first bytes, or None for
    anything that does not START like PNG, JPEG, GIF or WebP.

    The magic bytes only. Nothing here checks that the bytes after them decode as an image, so
    the 8-byte PNG signature alone, or a valid header over arbitrary bytes, is sent as that type
    (test_only_the_header_is_checked pins this). Such a file is the API's to reject. The call
    then falls back if the CLI exits nonzero or marks its result line `is_error`; if it ever did
    neither, the API's error sentence would reach the rail as the answer. Which of these the CLI
    does for a rejected image: not measured (every real call so far sent a valid image).
    Decoding with Pillow's `Image.verify` was the alternative and is not used: Pillow is not a
    broker dependency (pyproject.toml; tests/test_declared_dependencies.py holds the imports to
    that list). The broker's service interpreter on this box (the repo .venv, per
    deploy/install-services.ps1) can import it today, Pillow 12.3.0, only because pdfplumber,
    pytesseract and python-pptx require it; the broker's own install does not promise it."""
    if raw.startswith(_PNG_MAGIC):
        return "image/png"
    if raw.startswith(_JPEG_MAGIC):
        return "image/jpeg"
    if raw[:6] in _GIF_MAGICS:
        return "image/gif"
    if len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return None


def _image_source(b64: Any) -> dict[str, str]:
    """One Ollama-style image (a bare base64 string) -> a Claude base64 image source.

    Decoded strictly, the way Ollama's own decoder reads the same field (Go's standard base64,
    which skips CR and LF and nothing else): a data URL, a character outside the alphabet or a
    wrong padding is refused, not repaired, because the local model would refuse it too and a
    repaired image is a different image. The bytes are then re-encoded, so what Claude receives
    is exactly what was sniffed. Held in memory only; nothing here touches the disk."""
    if not isinstance(b64, str):
        raise CliUnavailable("vision_unsupported", "an image that is not a base64 string")
    text = b64.replace("\r", "").replace("\n", "")
    if len(text) > _IMAGE_MAX_B64:
        raise CliUnavailable("vision_unsupported",
                             f"an image of {len(text)} base64 characters; the cap is "
                             f"{_IMAGE_MAX_B64}")
    try:
        raw = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        raise CliUnavailable("vision_unsupported", "an image that is not valid base64") from None
    media_type = sniff_image_type(raw)
    if media_type is None:
        raise CliUnavailable("vision_unsupported",
                             "an image that is not PNG, JPEG, GIF or WebP")
    return {"type": "base64", "media_type": media_type,
            "data": base64.b64encode(raw).decode("ascii")}


def vision_message(messages: list[dict[str, Any]]) -> tuple[str, bytes]:
    """Ollama's `messages`, at least one carrying `images` -> `(system_prompt, stdin)` for
    `claude -p --input-format stream-json`: ONE user message, as one JSON line.

    The text is flattened exactly as `flatten_messages` flattens it (system turns to
    `--system-prompt`; one turn verbatim, several labelled `User:` / `Assistant:`), each turn as
    a text block, and each turn's images as base64 image blocks just before its text, because
    Claude reads an image best ahead of the words about it. Joining the text blocks with a blank
    line gives back the text path's prompt. A blank text block is left out (the API refuses an
    empty one); images alone are a complete question.

    Refused with `vision_unsupported`, so the local vision model answers instead: an image on a
    system turn (`--system-prompt` carries text only, and moving the picture into the user turn
    would change what was asked), an image on any other non-user turn, whatever the role string
    says ("assistant", "Assistant", "model", "tool"; for the same reason: this
    stdin is ONE user message, so the picture would arrive as one the person asking had shown,
    when the conversation says the assistant showed it; dropping it instead would leave the
    model answering about a picture it never saw), more than `_IMAGE_MAX_COUNT` images, more
    than `_IMAGES_MAX_TOTAL_B64` base64 characters of them together, and any one image
    `_image_source` will not send. Images on every user turn are sent, not just the last one's.
    """
    systems: list[str] = []
    turns: list[tuple[str, str, list[Any]]] = []
    count = 0
    for m in messages:
        role = str(m.get("role") or "user")
        content = m.get("content")
        if not isinstance(content, str):
            content = "" if content is None else str(content)
        images = m.get("images") or []
        if not isinstance(images, list):
            raise CliUnavailable("vision_unsupported", "`images` on a message is not a list")
        if role == "system":
            if images:
                raise CliUnavailable("vision_unsupported",
                                     "an image on a system message; --system-prompt is text only")
            if content.strip():
                systems.append(content)
        else:
            # Any role but exactly "user" (a missing or empty role is read as "user" above, as
            # the text path reads it): the role is a free string on the wire, so an "Assistant",
            # "model" or "tool" turn's picture would arrive as the asker's own too.
            if images and role != "user":
                raise CliUnavailable("vision_unsupported",
                                     f"an image on a {role!r} message; this call is one user "
                                     "message, so the picture would arrive as the asker's own")
            turns.append((role, content, images))
            count += len(images)
    if count > _IMAGE_MAX_COUNT:
        raise CliUnavailable("vision_unsupported",
                             f"{count} images in one call; the cap is {_IMAGE_MAX_COUNT}")
    blocks: list[dict[str, Any]] = []
    total = 0
    for role, content, images in turns:
        for b64 in images:
            source = _image_source(b64)
            total += len(source["data"])
            if total > _IMAGES_MAX_TOTAL_B64:
                raise CliUnavailable("vision_unsupported",
                                     f"more than {_IMAGES_MAX_TOTAL_B64} base64 characters of "
                                     "images in one call")
            blocks.append({"type": "image", "source": source})
        text = (content if len(turns) == 1
                else f"{'Assistant' if role == 'assistant' else 'User'}: {content}")
        if text.strip():
            blocks.append({"type": "text", "text": text})
    line = {"type": "user", "message": {"role": "user", "content": blocks}}
    # ASCII-escaped JSON on one line, then the newline that ends a stream-json message.
    return "\n\n".join(systems), (json.dumps(line) + "\n").encode("ascii")


def _last_result(stdout: str) -> dict[str, Any] | None:
    """The LAST line of type "result" in a stream-json transcript, or None. Lines that are not
    JSON objects are skipped: the transcript is read for its verdict, not validated whole."""
    found: dict[str, Any] | None = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("type") == "result":
            found = obj
    return found


def _exit_failure_from(last: dict[str, Any] | None, stderr: str, code: int | None,
                       lines: int) -> CliUnavailable:
    """What a stream-json call (an image call, or a streamed text call) whose CLI exited nonzero
    is refused with, from the transcript's last `result` line (`last`, or None), stderr, the
    exit code and how many lines the transcript had. It is ALWAYS refused: a result line that
    reads as a success does not outweigh the exit code.

    The detail is the CLI's own words or nothing, because it is printed on the service log's
    `CLI FALLBACK` (or `CLI INTERRUPTED`) line and kept as `cli.fallbacks.last.detail` on
    /v1/status, and the broker keeps rail content out of both. So it is the sentence on an
    `is_error` result line (the CLI's error), else stderr. Never another transcript line: an
    assistant line holds the model's answer, which on an image call can be its description of a
    rail's photo or of a page a user uploaded; a text delta holds the same answer as it was
    written; a result line that is not an error holds it whole; a line cut off by a kill can be
    any of these; and the init record says nothing about the fault. With neither, a fixed
    sentence naming the exit code and how many lines the transcript had, and the reason
    `failed`: a reason read out of the model's own words ("log in", "quota") would misfile the
    failure as well as quote it."""
    msg = ""
    if last is not None and last.get("is_error"):
        msg = str(last.get("result") or last.get("error") or "").strip()
    msg = msg or stderr.strip()
    if msg:
        return CliUnavailable(classify_failure(msg), msg[:300])
    return CliUnavailable("failed", f"the CLI exited with code {code} and printed no error "
                                    f"sentence; its transcript ({lines} lines) is not recorded")


def _stream_exit_failure(stdout: str, stderr: str, code: int | None) -> CliUnavailable:
    """What an image call whose CLI exited nonzero is refused with: `_exit_failure_from` over its
    buffered transcript."""
    lines = sum(1 for line in stdout.splitlines() if line.strip())
    return _exit_failure_from(_last_result(stdout), stderr, code, lines)


def stream_frame(model: str, content: str) -> dict[str, Any]:
    """One frame of a streamed answer, in Ollama's /api/chat stream shape, the shape every rail's
    stream reader already parses (`frame["message"]["content"]`). The done frame is this with
    `content` empty and `done`, `done_reason` and `cli` set (see `answer_frames`)."""
    return {"model": model,
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "message": {"role": "assistant", "content": content}, "done": False}


def _done_frame(model: str, done_reason: str, cli: dict[str, Any]) -> dict[str, Any]:
    return {**stream_frame(model, ""), "done": True, "done_reason": done_reason, "cli": cli}


def answer_frames(out: dict[str, Any]) -> list[dict[str, Any]]:
    """A buffered answer from `ClaudeCliClient.chat` as the two frames a stream of it ends with:
    the whole content in one frame, then the done frame carrying its `cli` block. For a streamed
    call that must be answered buffered (a `format`, or images: see Broker.chat_stream)."""
    model = str(out.get("model") or "claude-cli")
    content = str((out.get("message") or {}).get("content") or "")
    return [stream_frame(model, content),
            _done_frame(model, str(out.get("done_reason") or "stop"),
                        dict(out.get("cli") or {"backend": "claude"}))]


def _stream_tail(streamed: str, result: str) -> str | None:
    """What of the result line's answer is still to be sent once `streamed` has been, or None
    when the two disagree.

    The result line is the verdict, and the deltas were a preview of it. Measured on claude
    2.1.296 the two are identical, and with no delta at all (an answer that arrived whole) the
    tail is the whole answer. A result that EXTENDS the deltas (a delta lost on the way) sends
    the rest. Outer whitespace is not a disagreement (a result trimmed of the newline the last
    delta ended with). Anything else is: the rail has already shown text the CLI's own verdict
    does not contain, so the call is refused rather than patched."""
    if result.startswith(streamed):
        return result[len(streamed):]
    shown, verdict = streamed.strip(), result.strip()
    if verdict.startswith(shown):
        return verdict[len(shown):]
    return None


def dropped_options(options: dict[str, Any] | None, think: bool | None) -> list[str]:
    """Which caller instructions this backend cannot honour. Reported, never silently eaten."""
    out = [k for k in _SAMPLING_KEYS if options and k in options]
    if think is not None:
        out.append("think")
    return out


def classify_failure(text: str, *, default: str = "failed") -> str:
    """Map the CLI's own error text onto a `CliUnavailable.reason` slug."""
    low = (text or "").lower()
    for reason, signs in _REASON_SIGNS:
        if any(s in low for s in signs):
            return reason
    return default


async def _feed(stdin: Any, data: bytes) -> None:
    """Write the prompt and close stdin. A child that exits without reading it (a bad flag, an
    expired login) closes the pipe first; its exit code tells that story, so a broken pipe
    here is not a second failure."""
    if stdin is None:
        return
    try:
        stdin.write(data)
        await stdin.drain()
    except (BrokenPipeError, ConnectionResetError):
        pass
    finally:
        with contextlib.suppress(Exception):
            stdin.close()


async def _read_capped(stream: Any) -> bytes:
    """All of stdout, or `CliUnavailable("too_large")` the moment it passes `_STDOUT_CAP`.

    The caps are read from the module at call time, not bound as defaults, so a test can
    lower them without writing gigabytes."""
    buf = bytearray()
    while True:
        chunk = await stream.read(_READ_CHUNK)
        if not chunk:
            return bytes(buf)
        buf += chunk
        if len(buf) > _STDOUT_CAP:
            raise CliUnavailable("too_large", f"more than {_STDOUT_CAP} bytes of stdout")


# How long stderr may stay open once stdout has closed and the CLI has exited.
_STDERR_GRACE = 5.0


async def _read_head(stream: Any, head: bytearray | None = None) -> bytes:
    """The first `_STDERR_CAP` bytes of stderr. The rest is read and thrown away, because a
    child blocked writing into a full pipe never exits and the call would wait out its
    timeout; keeping only the head is what makes draining it safe. `head` may be passed in so
    a caller that stops waiting still has what was read so far."""
    head = bytearray() if head is None else head
    while True:
        chunk = await stream.read(_READ_CHUNK)
        if not chunk:
            return bytes(head)
        room = _STDERR_CAP - len(head)
        if room > 0:
            head += chunk[:room]


async def _exchange(proc: asyncio.subprocess.Process, data: bytes) -> tuple[bytes, bytes]:
    """`communicate()` with a memory bound: feed stdin, drain stderr, read stdout under the cap,
    then reap. On any exit other than a clean one (the cap, a timeout cancelling this, an
    error) the helper tasks are cancelled here; killing the process is the caller's `finally`.
    """
    feeder = asyncio.ensure_future(_feed(proc.stdin, data))
    err_head = bytearray()
    err_reader = asyncio.ensure_future(_read_head(proc.stderr, err_head))
    try:
        out = await _read_capped(proc.stdout)
        await feeder
        await proc.wait()
        # The answer is complete and the CLI has exited. stderr gets a short grace, not the
        # call's whole timeout: it used to be awaited to EOF first, so anything still holding
        # that pipe open (a grandchild the CLI left behind) held a finished answer until the
        # timeout fired and turned it into a fallback. Still drained concurrently from the
        # start, so a chatty child can never block on a full pipe.
        try:
            err = await asyncio.wait_for(asyncio.shield(err_reader), timeout=_STDERR_GRACE)
        except asyncio.TimeoutError:
            err = bytes(err_head)
        return out, err
    finally:
        pending = [t for t in (feeder, err_reader) if not t.done()]
        for t in pending:
            t.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


async def _stderr_within_grace(reader: asyncio.Future[bytes], head: bytearray) -> bytes:
    """What stderr held once the CLI exited: all of it if it closes within `_STDERR_GRACE`, else
    the head read so far. `_exchange`'s rule, for the streamed call."""
    try:
        return await asyncio.wait_for(asyncio.shield(reader), _STDERR_GRACE)
    except asyncio.TimeoutError:
        return bytes(head)


async def _kill_tree(proc: asyncio.subprocess.Process) -> None:
    """Kill the CLI and its children.

    `proc.kill()` reaches the launcher only. The real work happens in a node child which
    survives its parent and would keep a timed-out generation running -- and billing -- after
    the broker stopped waiting for it. On Windows `taskkill /T` is the only way to take the
    tree from here. On POSIX chat() starts the CLI in a session of its own, so the whole tree
    is one process group and killing the group takes the children with it; that used to be a
    plain `proc.kill()` there, which orphaned them. The direct kill stays as the fallback.
    """
    if proc.returncode is not None:
        return
    if not _POSIX:
        with contextlib.suppress(BaseException):
            killer = await asyncio.create_subprocess_exec(
                "taskkill", "/F", "/T", "/PID", str(proc.pid),
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            await asyncio.wait_for(killer.wait(), timeout=_REAP_TIMEOUT)
    else:
        # Immediate, so no bound needed. A group already gone is ProcessLookupError, harmless.
        with contextlib.suppress(BaseException):
            _killpg(proc.pid)
    with contextlib.suppress(BaseException):
        proc.kill()
    # A pipe we stopped reading at the cap is PAUSED by asyncio's flow control, so it never
    # sees the EOF the kill produced and its transport is never closed (asyncio reports it as
    # leaked when it is collected). The tree is dead, so what is left in the pipe is finite:
    # read it to EOF and throw it away, which lets the transport close itself.
    #
    # Reap and drain TOGETHER, never wait-then-drain: asyncio reports a subprocess's exit only
    # once its pipe transports have closed, so proc.wait() on a paused pipe cannot return until
    # the drain runs. In the old order it sat out the whole _REAP_TIMEOUT first: measured
    # 2026-10-07 on Linux (python:3.11-slim), 5 of 12 capped calls took 10 s to return, and
    # test_a_real_runaway_child_is_killed_at_the_cap failed 5 of 8 runs; drain-first, 0 of 12.
    # Each part stays bounded, in case something outside the tree still holds a write end.
    async def _bounded(aw: Any) -> None:
        with contextlib.suppress(BaseException):
            await asyncio.wait_for(aw, timeout=_REAP_TIMEOUT)

    await asyncio.gather(_bounded(proc.wait()),
                         *(_bounded(_discard(s)) for s in (proc.stdout, proc.stderr)
                           if s is not None))


async def _discard(stream: Any) -> None:
    while await stream.read(_READ_CHUNK):
        pass


#: Reaps in flight (see _reap), held here until each finishes: the event loop keeps only a weak
#: reference to a task, and a reap collected half way is a tree left running.
_REAPERS: set[asyncio.Task[None]] = set()


async def settle(fut: asyncio.Future[Any]) -> None:
    """Wait for `fut` to finish, through any cancellation of the caller, then re-raise it.

    For the work a CLI call must finish before it gives its slot back: the tree's kill (`_reap`)
    and the task a CLI client's own `finally` runs in (Broker._watch, Broker._while_there). A
    caller that returned before that work ended would free its slot while the tree it ran was
    still alive, and the next call could start one more tree than BROKER_CLI_CONCURRENCY allows.

    Until a cancellation arrives this is a plain wait: everything awaited here is bounded on its
    own (`_kill_tree` bounds each step at `_REAP_TIMEOUT`). Once one arrives the wait goes on,
    for at most `_REAP_TIMEOUT` more, so a kill that hangs costs the cancelled caller that long
    and never more; `fut` itself keeps running. Two kinds of cancellation have to be outlasted:

    * anyio's, which is LEVEL-triggered: Starlette cancels a disconnected stream through an anyio
      task group (ASGI spec 2.3, what uvicorn reports), and anyio delivers the cancellation again
      at every await until the task leaves the cancelled scope. `asyncio.shield` alone protects
      `fut` but not the wait, which the next delivery ends. A shielded anyio CancelScope stops
      the deliveries for as long as the wait lasts, and anyio restarts them when it ends.
    * a native one (`Task.cancel()`: a server's graceful-shutdown timeout, an outer
      `asyncio.wait_for`), which arrives once; anyio's shield cannot stop it, so the loop below
      catches it and waits on.
    """
    if fut.done():
        return
    try:
        await asyncio.wait({fut})
        return
    except asyncio.CancelledError as exc:
        cancelled = exc
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _REAP_TIMEOUT
    with anyio.CancelScope(shield=True):
        while not fut.done():
            left = deadline - loop.time()
            if left <= 0:
                break
            try:
                await asyncio.wait({fut}, timeout=left)
            except asyncio.CancelledError:
                continue
    raise cancelled


async def _remove_call_temp(path: str) -> bool:
    """Remove a call's temp directory and everything in it; True once it is gone.

    In a thread, so a file filter that stalls a delete stalls no other request. A failure is
    tried again, up to `_TEMP_REMOVE_TRIES` tries in all with doubling pauses, because a filter
    scanning a file the dead tree left can refuse the delete for a moment. One that outlasts
    them is logged once, by path and error and never by content, and left to the next
    startup's sweep (`sweep_call_temps`)."""
    pause = _TEMP_REMOVE_PAUSE
    for attempt in range(_TEMP_REMOVE_TRIES):
        try:
            await asyncio.to_thread(shutil.rmtree, path)
            return True
        except FileNotFoundError:
            return True
        except OSError as exc:
            failure = exc
        if attempt + 1 < _TEMP_REMOVE_TRIES:
            await asyncio.sleep(pause)
            pause *= 2
    print(f"[broker] CLI temp directory {path} could not be removed after "
          f"{_TEMP_REMOVE_TRIES} tries ({failure.__class__.__name__}: "
          f"{failure.strerror or failure}); the next startup's sweep removes it",
          file=sys.stderr, flush=True)
    return False


async def _end_call(proc: asyncio.subprocess.Process, temp: str) -> None:
    """What every CLI call leaves, undone once it ends however it ended: the tree killed if it
    still runs (`_kill_tree` returns at once for one that exited), THEN the call's temp
    directory removed. Never the other way round: a live child holds its files open, and could
    write the directory back."""
    await _kill_tree(proc)
    if temp:
        await _remove_call_temp(temp)


async def _reap(proc: asyncio.subprocess.Process, temp: str = "") -> None:
    """`_end_call(proc, temp)`: the tree's kill, then the removal of the call's temp directory,
    carried to the end even when the caller is cancelled while it waits, and waited for: the
    caller returns once the tree is dead and its directory gone (or `_REAP_TIMEOUT` after a
    cancellation arrived, see `settle`). Every call ends here, one that finished included, so
    a cancellation can skip neither half.

    The kill runs as a task of its own, held in `_REAPERS` until it finishes, so a cancellation
    reaches the caller's wait and never the kill. That matters because a cancellation can arrive
    more than once. A streamed call is cancelled when its client disconnects: uvicorn reports
    ASGI spec 2.3, for which Starlette's StreamingResponse runs the body in an anyio task group
    and cancels it on `http.disconnect`, and anyio cancellation is LEVEL-triggered, delivered
    again at every await until the task leaves the cancelled scope. Awaited in that task,
    `_kill_tree`'s spawn of `taskkill` is cancelled before it starts, `_kill_tree` swallows that
    (it suppresses everything around the kill, so a reap can never be what fails a call), and
    only the launcher dies: the CLI's node child keeps generating, and billing, for nobody.

    The wait goes through `settle` rather than a bare `asyncio.shield`: a shielded await is ended
    by the next delivery, and a caller that returned then freed its CLI slot while taskkill was
    still running.
    """
    task = asyncio.ensure_future(_end_call(proc, temp))
    _REAPERS.add(task)
    task.add_done_callback(_REAPERS.discard)
    await settle(task)


def _last_touched(path: str) -> float:
    """The newest modification time of `path` and of anything under it, links not followed."""
    newest = os.lstat(path).st_mtime
    for dirpath, dirnames, filenames in os.walk(path):
        for name in (*dirnames, *filenames):
            with contextlib.suppress(OSError):
                newest = max(newest, os.lstat(os.path.join(dirpath, name)).st_mtime)
    return newest


def sweep_call_temps(root: str, *, older_than: float = _TEMP_STALE_S,
                     name: re.Pattern[str] = _CALL_TEMP_NAME) -> tuple[int, int]:
    """Remove every call temp directory under `root` in which nothing has changed for
    `older_than` seconds: what a removal that outlasted its tries left (`_remove_call_temp`),
    or a broker that died mid-call. `(removed, left)`, `left` counting the stale ones that
    still would not go.

    Only names a call makes (`call-` and 32 hex digits, `_CALL_TEMP_NAME`) and only real
    directories (a link or junction is never followed or removed), so nothing else kept under
    the root is touched. A younger one is left: another broker on the same root may be running
    that call. A root that does not exist yet (no call has run) is nothing to do. `name` is the
    media jobs' rule over the system temp instead (media.sweep_job_dirs)."""
    try:
        entries = list(os.scandir(root))
    except OSError:
        return 0, 0
    now = time.time()
    removed = left = 0
    for entry in entries:
        if not name.fullmatch(entry.name):
            continue
        try:
            if (entry.is_symlink() or getattr(entry, "is_junction", lambda: False)()
                    or not entry.is_dir(follow_symlinks=False)):
                continue
            if now - _last_touched(entry.path) < older_than:
                continue
            shutil.rmtree(entry.path)
            removed += 1
        except FileNotFoundError:
            removed += 1
        except OSError:
            left += 1
    return removed, left


class ClaudeCliClient:
    """One configured `claude -p` invocation, shaped to the broker's chat contract.

    Stateless between calls on purpose: `--no-session-persistence` plus a fresh process means
    no conversation is carried, nothing is written to the owner's session history, and
    AgentFlow (which watches those session folders for agents waiting on a prompt) does not
    announce the platform's own calls as sessions needing attention. What the CLI still writes
    under TEMP goes in a directory of the call's own, removed when the call ends (`_reap`).
    """

    def __init__(
        self,
        exe: str,
        *,
        timeout: float = 180.0,
        workdir: str = "",
        oauth_token: str = "",
        # Defaults to the CLAUDE.md excludes above; pass "" to deliberately send none.
        settings_json: str = DEFAULT_SETTINGS_JSON,
        model: str = "",
        # A streamed answer's whole length, counted from its first token (chat_stream). None:
        # the same as `timeout`.
        answer_timeout: float | None = None,
        # Where each call's own temp directory is made (call_temp_root): "" is `cli-tmp`
        # beside the workdir.
        temp_root: str = "",
    ) -> None:
        self.exe = exe
        self.timeout = timeout
        self.workdir = workdir
        self.oauth_token = oauth_token
        self.settings_json = settings_json
        self.model = model
        self.answer_timeout = timeout if answer_timeout is None else answer_timeout
        self.temp_root = temp_root

    # --- command construction (pure, so a test can assert the argv) ---------

    def build_argv(self, system: str, *, want_json: bool,
                   schema: dict[str, Any] | None = None,
                   stream_json: bool = False, partial: bool = False) -> list[str]:
        """The exact argv. Pure and separately testable, because this list IS the contract:
        a flag silently dropped here is the owner's hooks running on a rail's call, a tool the
        call should not have, or a session file AgentFlow will report.

        `stream_json` is an image call: the question arrives on stdin as one stream-json user
        message (`vision_message`) and the answer as a stream-json transcript, which `-p` emits
        only with `--verbose`. Only the I/O format differs; every isolation flag below is the
        same list for both, and a call without images builds exactly the argv it always did.

        `partial` is a streamed TEXT call (`chat_stream`): the prompt on stdin as plain text, as
        on the text path, and the answer as a stream-json transcript with
        `--include-partial-messages`, which adds a `stream_event` line per token (claude
        2.1.296's own --help: it works only with --print and --output-format=stream-json).
        Text only, so it refuses JSON, a schema or stream-json input with a ValueError: those
        go buffered (Broker.chat_stream), never through this argv."""
        if partial and (want_json or schema is not None or stream_json):
            raise ValueError("a partial-message stream is plain text: no JSON, no schema and "
                             "no stream-json input")
        sys_prompt = system
        if want_json and schema is None:
            sys_prompt = f"{system}\n\n{_JSON_INSTRUCTION}".strip()
        io_format = (["--input-format", "stream-json", "--output-format", "stream-json",
                      "--verbose"] if stream_json else ["--output-format", "json"])
        if partial:
            io_format = ["--output-format", "stream-json", "--verbose", "--include-partial-messages"]
        argv = [
            self.exe,
            "-p",
            *io_format,
            # No tools at all. This is a model call, not an agent run.
            "--tools", "",
            "--strict-mcp-config",
            "--disable-slash-commands",
            "--no-session-persistence",
            # Load NO user/project/local settings. Without this the owner's hooks, `env`
            # block, model pin and plugins all apply to a call the platform makes on a rail's
            # behalf -- and a hook would run under the broker's service account, not theirs.
            # Also the single largest context saving available: 14,183 -> 820 input tokens.
            "--setting-sources", "",
        ]
        if self.settings_json:
            argv += ["--settings", self.settings_json]
        if schema is not None:
            # The CLI enforces the shape itself, which is strictly better than asking for JSON
            # in prose and validating afterwards.
            argv += ["--json-schema", json.dumps(schema)]
        if self.model:
            argv += ["--model", self.model]
        if sys_prompt:
            argv += ["--system-prompt", sys_prompt]
        return argv

    def build_env(self, temp: str = "") -> dict[str, str]:
        """The child's environment: an ALLOWLIST of the broker's, never the whole of it.

        The broker's environment holds every credential it uses: BROKER_AUTH_TOKEN (full scope
        over the GPU and the role map), the CLI OAuth token in its BROKER_ form, upstream and
        cloud keys. The child is a model call made on a rail's behalf and needs none of them,
        so it used to inherit all of them for no reason. What it does need is the OS, its own
        login (HOME / USERPROFILE / APPDATA and any CLAUDE_* setting), locale and the network
        path out (proxies, extra CA certificates). ANTHROPIC_* is left out on purpose: an API
        key there would bill the call to an API account instead of the owner's subscription.

        `temp` is the call's own temp directory (`_new_call_temp`, every call has one): TEMP and
        TMP point there, and TMPDIR on POSIX (or wherever the broker's own environment has
        one), in place of the broker's, so nothing the CLI writes under them outlives the call.
        """
        env = {k: v for k, v in os.environ.items() if _child_may_inherit(k)}
        if temp:
            env["TEMP"] = env["TMP"] = temp
            if _POSIX or "TMPDIR" in env:
                env["TMPDIR"] = temp
        # The owner's personal memory has no bearing on a rail's prompt, and is both a context
        # cost and a privacy surprise in a call made on behalf of a platform job.
        env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
        # Never self-update from inside the broker. The binary lives in the OWNER's npm root
        # (%APPDATA%\npm under their profile) while the broker runs as LocalSystem, so an
        # update triggered here would rewrite the owner's install as SYSTEM -- or half-write
        # it mid-call. The owner updates their CLI; the broker only ever runs it.
        env["DISABLE_AUTOUPDATER"] = "1"
        if self.oauth_token:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = self.oauth_token
        return env

    # --- the call ------------------------------------------------------------

    def _new_call_temp(self) -> str:
        """Make this call's own temp directory under `call_temp_root` and return its path.

        Mode 0o700: on POSIX the owner's alone, and on Windows (Python 3.12.4 and later) an ACL
        for the account the broker runs as, SYSTEM and the Administrators group only, as
        `tempfile.mkdtemp` makes, whatever the root lets others do. A root that cannot be made
        is a fallback (`failed`), named in the detail."""
        root = call_temp_root(self.temp_root, self.workdir)
        path = os.path.join(root, f"{_CALL_TEMP_PREFIX}{uuid.uuid4().hex}")
        try:
            os.makedirs(root, exist_ok=True)
            os.mkdir(path, 0o700)
        except OSError as exc:
            raise CliUnavailable("failed", f"no temp directory for the call under {root} "
                                           f"({exc.strerror or exc})") from exc
        return path

    async def _spawn(self, argv: list[str]) -> tuple[asyncio.subprocess.Process, str]:
        """Start the CLI: no shell, the three pipes, the empty workdir, the allowlisted env with
        a temp directory of the call's own (`_new_call_temp`). Returns the process and that
        directory, which the caller hands to `_reap` with it on every exit. One place for every
        call shape, so a streamed call cannot start differently from a buffered one."""
        temp = self._new_call_temp()
        started = False
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.workdir or None,
                env=self.build_env(temp),
                # POSIX: the CLI leads a session of its own, so its pid is also its process
                # group's, and _kill_tree can take every descendant with one killpg.
                **({"start_new_session": True} if _POSIX else {}),
            )
            started = True
        except (OSError, ValueError) as exc:
            # OSError: a missing binary, or WinError 193 from a shim that slipped through.
            # ValueError: an env value carrying an embedded NUL, which is what a token file
            # decoded from the wrong encoding produces.
            raise CliUnavailable("not_installed", str(exc)) from exc
        finally:
            if not started:
                # No child ran in it: a spawn that failed, or was cancelled before it finished.
                shutil.rmtree(temp, ignore_errors=True)
        return proc, temp

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        format: str | dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
        think: bool | None = None,
    ) -> dict[str, Any]:
        """Run one turn. Returns an OLLAMA-SHAPED response, because that is what every rail in
        this repo already reads (`data["message"]["content"]`). Making the new backend fit the
        old shape keeps the translation in one file instead of in every rail.
        """
        # Before anything else, text call or image call: a trailing assistant turn leaves the
        # prompt and becomes an instruction (split_prefill), or the call is refused.
        messages, prefill = split_prefill(messages)
        schema = format if isinstance(format, dict) else None
        want_json = schema is not None or format == "json"
        if prefill and want_json:
            # A schema is enforced on the whole reply by the CLI, and a bare json answer is
            # rebuilt from the largest JSON value in it (extract_json): neither leaves an
            # opening to verify and cut off, so the local model continues this one.
            raise CliUnavailable("prefill_unsupported",
                                 "a prefill with a format; the CLI path emulates text prefills only")
        # An image anywhere makes this a stream-json call; without one it is the text path,
        # argv and stdin exactly as before images could be sent.
        stream = any(m.get("images") for m in messages)
        if stream:
            system, stdin = vision_message(messages)
        else:
            system, prompt = flatten_messages(messages)
            if not prompt.strip():
                raise CliUnavailable("empty", "no user content to send")
            stdin = prompt.encode("utf-8")
        if prefill:
            system = prefill_system(system, prefill)

        argv = self.build_argv(system, want_json=want_json, schema=schema, stream_json=stream)
        proc, temp = await self._spawn(argv)

        try:
            out, err = await asyncio.wait_for(
                _exchange(proc, stdin), timeout=self.timeout)
        except asyncio.TimeoutError as exc:
            raise CliUnavailable("timeout", f"no answer within {self.timeout:.0f}s") from exc
        finally:
            # In a FINALLY, not in the except clauses it replaced. media.py already learned
            # this: an except-based reap misses the paths nobody enumerated -- cancellation, a
            # transport error, MemoryError on the buffer, interpreter shutdown -- and each one
            # leaves a generation running against the owner's subscription with nobody reading
            # it. Covering every abandoned path by construction is the only version that holds.
            # Through _reap, so a cancellation delivered again while the kill runs (anyio's,
            # when a stream that asked for a format is cancelled) cannot stop it half way; and
            # on every exit, a finished call's too, because the reap removes the call's temp
            # directory once the tree is dead.
            await _reap(proc, temp)

        stdout = out.decode("utf-8", errors="replace")
        stderr = err.decode("utf-8", errors="replace")

        if proc.returncode != 0:
            # With --output-format json the CLI reports its own errors IN the result object on
            # stdout, with stderr empty. Classifying the raw stdout meant classifying a JSON
            # envelope, and the recorded detail was its first 300 characters -- usage counters
            # and a session id, cut off before the one sentence that said what was wrong.
            msg = stderr.strip()
            if stream:
                # The same sentence, from the transcript's own is_error result line, and never
                # any other line of the transcript (see _stream_exit_failure).
                raise _stream_exit_failure(stdout, stderr, proc.returncode)
            try:
                body = json.loads(stdout)
                if isinstance(body, dict) and body.get("result"):
                    msg = str(body["result"]).strip()
            except json.JSONDecodeError:
                pass
            msg = msg or stdout.strip()
            raise CliUnavailable(classify_failure(msg), msg[:300])

        content, usage, stop = (self._parse_stream(stdout, stderr) if stream
                                else self._parse_result(stdout, stderr))
        if want_json:
            if stop == "max_tokens":
                # A JSON answer cut off at the token limit is not the answer: the largest value
                # left in it is an inner fragment of the wrong shape (a 3,000-item array cut
                # short came back as one of its items), which would be delivered as a success.
                raise CliUnavailable("bad_json", "asked for JSON and the answer was cut off at "
                                                 "the token limit (stop_reason max_tokens)")
            extracted = await _extract_json_off_loop(content)
            if extracted is None:
                raise CliUnavailable(
                    "bad_json", "asked for JSON and the answer did not contain a JSON value")
            content = extracted
        if prefill:
            # Ollama answers a prefill with its continuation alone, and the caller joins the
            # two; so does this, once the reply is seen to begin with the prefill.
            cont = continuation(content, prefill)
            if cont is None:
                raise _prefill_mismatch()
            if not cont.strip():
                raise CliUnavailable("empty", "the reply was the prefill and nothing more")
            content = cont
        dropped = dropped_options(options, think)
        return {
            "model": self.model or "claude-cli",
            "message": {"role": "assistant", "content": content},
            "done": True,
            # Ollama's word for a reply cut off at the token limit, as chat_stream reports it.
            "done_reason": "length" if stop == "max_tokens" else "stop",
            # Namespaced rather than faked into Ollama's own eval-count fields: a token count
            # from a different billing system landing in `prompt_eval_count` would be read as
            # GPU work by anything that aggregates those.
            "cli": {"backend": "claude", **usage,
                    **({"dropped": dropped} if dropped else {})},
        }

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        *,
        options: dict[str, Any] | None = None,
        think: bool | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Run one TEXT turn and yield the answer as the CLI writes it: content frames in
        Ollama's stream shape (`stream_frame`), then one done frame carrying `cli`, the usage
        `chat()` reports, with `done_reason` "length" when the model ran out of tokens.

        Every failure raises CliUnavailable, before the first frame or after some; which, the
        caller knows (Broker.chat_stream falls back before the first and reports an
        interruption after). The rules:

        * Refused before anything starts: a prefill this path cannot emulate (`split_prefill`),
          any image (the text flattener's `vision_unsupported`: an image call goes buffered),
          no user content.
        * A prefill it can emulate is held back: no frame is yielded until the reply is seen to
          begin with the prefill (`continuation`), and then only what follows it. A reply that
          leaves the prefill is `prefill_mismatch` as soon as it does, before any frame, so
          the caller falls back with nothing shown.
        * Deadlines: the first text within `timeout` (what is left of the call's budget), else
          `timeout`; once it has begun, a byte at least every `_STREAM_IDLE_S` of waiting, else
          `stalled`; and the whole answer within `answer_timeout` of its first token, else
          `timeout`.
        * Only `text_delta`s are content. Thinking, signatures and tool input never are, and a
          SECOND message beginning after content was sent is `bad_stream`: a turn the model
          took on its own, which this stream has no way to show as one.
        * The last `result` line is still the verdict, read as `chat()` reads one: a nonzero
          exit is refused whatever it says (`_exit_failure_from`), no result line is
          `bad_stream`, `is_error` is classified. Its text must agree with what was streamed
          (`_stream_tail`): with no delta it is sent whole, a longer one sends the rest, a
          different one is `stream_mismatch`.
        * stdout is read in `_READ_CHUNK` pieces and split into lines here (a StreamReader's
          readline gives up at 64 KiB, and a long answer's result line is longer), under the
          same `_STDOUT_CAP`; stderr is drained alongside and only its head kept.

        The tree is killed on every exit the process outlives, a consumer that stops reading and
        closes the generator included, and the call's temp directory removed on every exit once
        the tree is dead, both through `_reap`."""
        messages, prefill = split_prefill(messages)
        system, prompt = flatten_messages(messages)
        if not prompt.strip():
            raise CliUnavailable("empty", "no user content to send")
        if prefill:
            system = prefill_system(system, prefill)
        held = bool(prefill)    # frames held back until the reply is seen to begin with it
        argv = self.build_argv(system, want_json=False, partial=True)
        name = self.model or "claude-cli"
        dropped = dropped_options(options, think)
        proc, temp = await self._spawn(argv)
        feeder = asyncio.ensure_future(_feed(proc.stdin, prompt.encode("utf-8")))
        err_head = bytearray()
        err_reader = asyncio.ensure_future(_read_head(proc.stderr, err_head))
        clock = asyncio.get_running_loop().time
        began = clock()
        first: float | None = None          # when the first text arrived
        streamed: list[str] = []
        stop_reason = ""
        last: dict[str, Any] | None = None  # the latest `result` line
        lines = total = 0
        pending = bytearray()               # a line not yet ended by its newline
        try:
            while True:
                now = clock()
                if first is None:
                    # The first text: within what is left of the call's budget.
                    wait = began + self.timeout - now
                    why = ("timeout", f"no text within {self.timeout:.0f}s")
                elif first + self.answer_timeout - now <= _STREAM_IDLE_S:
                    wait = first + self.answer_timeout - now
                    why = ("timeout", f"the answer did not finish within "
                                      f"{self.answer_timeout:.0f}s of its first token")
                else:
                    wait = _STREAM_IDLE_S
                    why = ("stalled", f"nothing for {_STREAM_IDLE_S:.0f}s once the answer had "
                                      f"begun")
                if wait <= 0:
                    raise CliUnavailable(*why)
                try:
                    chunk = await asyncio.wait_for(proc.stdout.read(_READ_CHUNK), timeout=wait)
                except asyncio.TimeoutError:
                    raise CliUnavailable(*why) from None
                if chunk:
                    total += len(chunk)
                    if total > _STDOUT_CAP:
                        raise CliUnavailable("too_large",
                                             f"more than {_STDOUT_CAP} bytes of stdout")
                    pending += chunk
                    if b"\n" not in chunk:
                        continue
                    *complete, rest = pending.split(b"\n")
                    pending = bytearray(rest)
                else:
                    complete, pending = [pending], bytearray()
                for raw in complete:
                    line = bytes(raw).strip()
                    if not line:
                        continue
                    lines += 1
                    try:
                        obj = json.loads(line)
                    except (ValueError, RecursionError):
                        continue            # not JSON: neither a frame nor the verdict
                    if not isinstance(obj, dict):
                        continue
                    if obj.get("type") == "result":
                        last = obj
                        continue
                    event = obj.get("event") if obj.get("type") == "stream_event" else None
                    if not isinstance(event, dict):
                        continue
                    kind, delta = event.get("type"), event.get("delta")
                    if kind == "message_start":
                        if streamed:
                            raise CliUnavailable("bad_stream", "a second message began after "
                                                               "the answer had started streaming")
                    elif kind == "message_delta" and isinstance(delta, dict):
                        if isinstance(delta.get("stop_reason"), str):
                            stop_reason = delta["stop_reason"]
                    elif kind == "content_block_delta" and isinstance(delta, dict):
                        # Only text. A thinking model's reasoning (thinking_delta, then its
                        # signature_delta) and a tool call's arguments (input_json_delta) are
                        # not the answer, and none of them is ever sent as content.
                        text = delta.get("text") if delta.get("type") == "text_delta" else None
                        if isinstance(text, str) and text:
                            if first is None:
                                first = clock()
                            streamed.append(text)
                            if held:
                                # Nothing reaches the caller before the reply is seen to begin
                                # with the prefill, so one that ignored the instruction falls
                                # back with nothing shown; then only what follows it is sent.
                                cont = continuation("".join(streamed), prefill, final=False)
                                if cont is None:
                                    raise _prefill_mismatch()
                                if cont is _UNDECIDED:
                                    continue
                                held = False
                                text = cont
                            yield stream_frame(name, text)
                if not chunk:
                    break
            # The transcript is complete: the exit code next, inside the deadline in force.
            left = (began + self.timeout if first is None
                    else first + self.answer_timeout) - clock()
            try:
                await asyncio.wait_for(proc.wait(), timeout=max(left, 0.001))
            except asyncio.TimeoutError:
                raise CliUnavailable("timeout", "the CLI did not exit once its transcript "
                                                "ended") from None
            # stderr gets the buffered path's short grace (see _exchange), never the budget.
            stderr = (await _stderr_within_grace(err_reader, err_head)).decode(
                "utf-8", errors="replace")
            if proc.returncode:
                raise _exit_failure_from(last, stderr, proc.returncode, lines)
            if last is None:
                if not total:
                    raise CliUnavailable(
                        "empty", (stderr or "the CLI produced no output").strip()[:300])
                raise CliUnavailable("bad_stream", "the stream-json transcript has no result "
                                                   "line")
            content, usage, result_stop = self._result_fields(last)
            tail = _stream_tail("".join(streamed), content)
            if tail is None:
                raise CliUnavailable("stream_mismatch", "the result line disagrees with the "
                                                        "text already streamed")
            if held:
                # Still undecided when the deltas ended, or the answer came whole on the result
                # line: judged now, on the whole reply.
                cont = continuation("".join(streamed) + tail, prefill)
                if cont is None:
                    raise _prefill_mismatch()
                if not cont.strip():
                    raise CliUnavailable("empty", "the reply was the prefill and nothing more")
                tail = cont
            if tail:
                yield stream_frame(name, tail)
            stop = stop_reason or result_stop
            yield _done_frame(name, "length" if stop == "max_tokens" else "stop",
                              {"backend": "claude", **usage,
                               **({"dropped": dropped} if dropped else {})})
        finally:
            for helper in (feeder, err_reader):
                helper.cancel()
            # The tree FIRST, the helpers after: under a cancellation delivered again (anyio's),
            # the gather below may never return, and the kill must not wait behind it. On every
            # exit, a finished stream's too: the reap also removes the call's temp directory.
            await _reap(proc, temp)  # the consumer left, a refusal above, or the answer ended
            await asyncio.gather(feeder, err_reader, return_exceptions=True)

    @staticmethod
    def _parse_result(stdout: str, stderr: str) -> tuple[str, dict[str, Any], str]:
        """`--output-format json` gives one result object; tolerate a bare-text answer too."""
        text = stdout.strip()
        if not text:
            raise CliUnavailable("empty", (stderr or "the CLI produced no output").strip()[:300])
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return text, {}, ""
        if not isinstance(data, dict):
            return text, {}, ""
        return ClaudeCliClient._result_fields(data)

    @staticmethod
    def _parse_stream(stdout: str, stderr: str) -> tuple[str, dict[str, Any], str]:
        """`--output-format stream-json` prints the transcript one JSON object per line, and the
        verdict is its LAST `result` line, which carries the fields the json format's single
        object does (measured on claude 2.1.293: result, is_error, usage, modelUsage). A
        transcript without one never finished, so it is refused, never pieced together from the
        assistant lines before it; the bare-text answer the json path tolerates is the same
        refusal here, because stream-json never prints one."""
        if not stdout.strip():
            raise CliUnavailable("empty", (stderr or "the CLI produced no output").strip()[:300])
        data = _last_result(stdout)
        if data is None:
            raise CliUnavailable("bad_stream", "the stream-json transcript has no result line")
        return ClaudeCliClient._result_fields(data)

    @staticmethod
    def _result_fields(data: dict[str, Any]) -> tuple[str, dict[str, Any], str]:
        """One result object -> `(answer, usage, stop_reason)`, the same for both output formats.
        `stop_reason` is the API's (`end_turn`, `max_tokens`, ...; claude 2.1.299's result schema
        has it on every result object, null when there is none), or "" when it is absent."""
        if data.get("is_error"):
            detail = str(data.get("result") or data.get("error") or "")[:300]
            raise CliUnavailable(classify_failure(detail), detail)
        result = data.get("result")
        if not isinstance(result, str) or not result.strip():
            raise CliUnavailable("empty", "the CLI returned a result object with no text")
        usage: dict[str, Any] = {}
        u = data.get("usage")
        if isinstance(u, dict):
            # Coerced defensively: these come from the CLI, and a float or a nested object
            # here used to raise straight out of a SUCCESSFUL call.
            def _n(key: str) -> int:
                try:
                    return int(u.get(key) or 0)
                except (TypeError, ValueError):
                    return 0
            usage["input_tokens"] = sum(_n(k) for k in (
                "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
            usage["output_tokens"] = _n("output_tokens")
        for key in ("duration_ms", "total_cost_usd", "num_turns"):
            if key in data:
                usage[key] = data[key]
        stop = data.get("stop_reason")
        return result, usage, stop if isinstance(stop, str) else ""
