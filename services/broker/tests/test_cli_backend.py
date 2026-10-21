"""The Claude CLI text backend: argv contract, translation, and every failure as a fallback.

Two things are asserted here that are easy to lose and expensive to lose quietly.

**The argv IS the contract.** `--tools ""`, `--strict-mcp-config`, `--disable-slash-commands`
and `--no-session-persistence` are not tuning. Dropping one gives the call tools it should not
have, lets the owner's MCP servers and skills load into a rail's prompt, or writes a session
file that AgentFlow then announces as an agent waiting for a human. None of those four would
fail a test that only checked "the call returned text", so each is asserted by name.

**Every failure path raises `CliUnavailable`.** That is what makes the backend safe to turn on:
the broker treats the exception as "use Ollama instead". A failure path that raised something
else would escape as a 502 and break a rail that worked fine the day before. So the tests do
not merely check that bad input is rejected -- they check the TYPE, because the type is the
fallback.

**A call with images is the same contract on a second wire format.** It goes as ONE stream-json
user message on stdin, with every isolation flag the text path has, and a call without images
must build exactly the argv and stdin it always did. Both are pinned as literal goldens, in
test_cli_backend_images.py.

The subprocess is faked. A real-CLI call lives in `LiveTests` (deselected by default, like
job-aid's board tests) because it spends the owner's subscription quota and needs a login.

The tests are split by concern (2026-10-19, when this one file neared publish's data ceiling):
  test_cli_backend.py           the argv contract, message translation, JSON extraction,
                                failure classification, executable resolution, LiveTests
  test_cli_backend_temp.py      each call's own TEMP
  test_cli_backend_call.py      the call itself
  test_cli_backend_images.py    the text path's goldens, and a call with images
  test_cli_backend_prefill.py   a prefill, emulated
  test_cli_backend_streamed.py  a streamed text call
  cli_backend_fakes.py          the fake subprocess, the fixtures, the shared helpers
"""
import asyncio
import base64
import json
import os
import pathlib
import shutil
import struct
import sys
import tempfile
import time
import unittest
import zlib

import pytest

from app import cli_backend
from app.cli_backend import (
    ClaudeCliClient,
    CliUnavailable,
    classify_failure,
    extract_json,
    flatten_messages,
    resolve_cli_exe,
)

# The fakes and helpers the test_cli_backend*.py files share. The fixtures come in by name,
# which is how pytest finds them here; group_kills and _system_temp are autouse.
from cli_backend_fakes import (  # noqa: F401
    REQUIRED_FLAGS,
    _client,
    _left_under,
    _system_temp,
    group_kills,
)

#: The system temp directory as this process found it, before any test redirects `tempfile`.
_REAL_TEMP = tempfile.gettempdir()


# --- argv contract ---------------------------------------------------------

@pytest.mark.parametrize("flag", REQUIRED_FLAGS)
def test_argv_carries_each_isolation_flag(flag):
    assert flag in _client().build_argv("", want_json=False)


def test_argv_disables_tools_with_an_empty_string():
    """`--tools` followed by "" is what disables them; `--tools` alone would take the NEXT
    argument as a tool name, silently turning the flag into a parse error or a tool list."""
    argv = _client().build_argv("", want_json=False)
    assert argv[argv.index("--tools") + 1] == ""


def test_argv_is_print_mode_with_json_output():
    argv = _client().build_argv("", want_json=False)
    assert "-p" in argv
    assert argv[argv.index("--output-format") + 1] == "json"


def test_argv_passes_the_system_prompt():
    argv = _client().build_argv("be terse", want_json=False)
    assert argv[argv.index("--system-prompt") + 1] == "be terse"


def test_argv_omits_the_system_prompt_when_empty():
    """An empty `--system-prompt ""` is not the same as omitting it: it would REPLACE the
    CLI's default prompt with nothing rather than leaving the default in place."""
    assert "--system-prompt" not in _client().build_argv("", want_json=False)


def test_json_mode_appends_the_instruction_to_the_system_prompt():
    argv = _client().build_argv("be terse", want_json=True)
    prompt = argv[argv.index("--system-prompt") + 1]
    assert prompt.startswith("be terse")
    assert "raw JSON" in prompt


def test_json_mode_still_sends_an_instruction_with_no_caller_system_prompt():
    argv = _client().build_argv("", want_json=True)
    assert "raw JSON" in argv[argv.index("--system-prompt") + 1]


def test_argv_loads_no_user_project_or_local_settings():
    """`--setting-sources ""` is the one that matters most and saves the most. Without it the
    owner's hooks, `env` block, model pin and plugins all apply to a call the platform makes
    on a rail's behalf -- and a hook runs under the BROKER's service account, not theirs.
    Measured: 14,183 input tokens without it, 820 with."""
    argv = _client().build_argv("", want_json=False)
    assert argv[argv.index("--setting-sources") + 1] == ""


def test_argv_excludes_the_owners_personal_claude_md_by_default():
    """Claude Code reads the user-scope CLAUDE.md and rules on every invocation, so without
    this the operator's own instructions to their coding agent were prepended to a rail's
    prompt, and a rail's question arrived behind them. Measured at 14,184 input tokens against
    5,086.

    No mock can catch this (a fake CLI has no CLAUDE.md), which is why it survived 52 green
    tests and showed up on the first real call."""
    argv = _client().build_argv("", want_json=False)
    settings = json.loads(argv[argv.index("--settings") + 1])
    assert "**/.claude/CLAUDE.md" in settings["claudeMdExcludes"]
    assert "**/.claude/rules/**" in settings["claudeMdExcludes"]


def test_an_operator_can_override_the_settings_json():
    argv = _client(settings_json='{"x":1}').build_argv("", want_json=False)
    assert argv[argv.index("--settings") + 1] == '{"x":1}'


def test_blanking_the_settings_omits_the_flag():
    """An explicit opt-out stays possible; it just is not the default."""
    assert "--settings" not in _client(settings_json="").build_argv("", want_json=False)


def test_argv_omits_model_unless_configured():
    assert "--model" not in _client().build_argv("", want_json=False)
    assert "--model" in _client(model="claude-x").build_argv("", want_json=False)


def test_env_disables_auto_memory_and_carries_the_token():
    env = _client(oauth_token="tok-123").build_env()
    assert env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "tok-123"


def test_env_disables_the_auto_updater():
    """The binary is in the owner's npm root; the broker runs as LocalSystem. An update
    triggered from a broker call would rewrite the owner's install as SYSTEM."""
    assert _client().build_env()["DISABLE_AUTOUPDATER"] == "1"


def test_env_keeps_the_broker_credentials_out_of_the_child(monkeypatch):
    """The child is a model call on a rail's behalf. It used to inherit the broker's whole
    environment, BROKER_AUTH_TOKEN (full scope over the GPU and the role map) included."""
    secrets_in_env = {"BROKER_AUTH_TOKEN": "full-scope", "BROKER_CLI_OAUTH_TOKEN": "oauth",
                      "HF_TOKEN": "hf", "NVIDIA_API_KEY": "nim", "ANTHROPIC_API_KEY": "api",
                      "SOME_SERVICE_PASSWORD": "pw"}
    for k, v in secrets_in_env.items():
        monkeypatch.setenv(k, v)
    env = _client().build_env()
    leaked = sorted(k for k in env if k.upper() in secrets_in_env)
    assert leaked == [], f"the CLI child inherited {leaked}"
    assert not any(v in env.values() for v in secrets_in_env.values())


def test_env_still_carries_what_the_cli_needs(monkeypatch):
    """The other half of an allowlist: the OS, the account's own login, locale and the way
    out to the network. Dropping any of these turns the backend off by a different route."""
    keep = {"PATH": "p", "SYSTEMROOT": "C:\\Windows", "USERPROFILE": "C:\\Users\\x",
            "APPDATA": "a", "HOME": "h", "TEMP": "t", "HTTPS_PROXY": "proxy",
            "NODE_EXTRA_CA_CERTS": "ca", "CLAUDE_CONFIG_DIR": "cfg", "LC_ALL": "C.UTF-8"}
    for k, v in keep.items():
        monkeypatch.setenv(k, v)
    env = {k.upper(): v for k, v in _client().build_env().items()}
    assert {k: env.get(k) for k in keep} == keep


def test_env_omits_the_token_key_when_unset(monkeypatch):
    """Absent, not empty. An empty CLAUDE_CODE_OAUTH_TOKEN outranks the ambient login in the
    CLI's own precedence order, so writing "" would log the broker OUT rather than leave it
    to whatever credential the account already has."""
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in _client().build_env()


# --- message translation ---------------------------------------------------

def test_single_user_turn_is_verbatim():
    system, prompt = flatten_messages([{"role": "user", "content": "hello"}])
    assert (system, prompt) == ("", "hello")


def test_system_turns_are_split_out_and_joined():
    system, prompt = flatten_messages([
        {"role": "system", "content": "a"},
        {"role": "system", "content": "b"},
        {"role": "user", "content": "q"},
    ])
    assert system == "a\n\nb"
    assert prompt == "q"


def test_multi_turn_is_labelled():
    _, prompt = flatten_messages([
        {"role": "user", "content": "one"},
        {"role": "assistant", "content": "two"},
        {"role": "user", "content": "three"},
    ])
    assert prompt == "User: one\n\nAssistant: two\n\nUser: three"


def test_images_refuse_rather_than_being_dropped():
    """Dropping the image would answer a question about a picture the model never saw."""
    with pytest.raises(CliUnavailable) as exc:
        flatten_messages([{"role": "user", "content": "what is this", "images": ["b64"]}])
    assert exc.value.reason == "vision_unsupported"


# --- JSON extraction -------------------------------------------------------

@pytest.mark.parametrize("raw,expect", [
    ('{"a": 1}', {"a": 1}),
    ('```json\n{"a": 1}\n```', {"a": 1}),
    ('Here you go:\n{"a": 1}\nhope that helps', {"a": 1}),
    ('{"a": {"b": [1, 2]}}', {"a": {"b": [1, 2]}}),
    ('[{"a": 1}]', [{"a": 1}]),
])
def test_extract_json_finds_the_value(raw, expect):
    assert json.loads(extract_json(raw)) == expect


@pytest.mark.parametrize("wrap", ["{}", 'Here: {} -- not {"rival": 12345}'],
                         ids=["alone", "in-prose"])
def test_extract_json_ignores_braces_inside_strings(wrap):
    """A `}` inside a string value must not end the scan -- the classic way a brace-counting
    extractor returns a truncated document that still parses. In prose too: a reply that is the
    value alone is parsed whole, and only one with prose around it reaches the scan, where a
    value measured short at the brace (13 characters) would lose to a smaller rival (16)."""
    raw = wrap.replace("{}", '{"text": "a } brace", "n": 1}', 1)
    assert json.loads(extract_json(raw)) == {"text": "a } brace", "n": 1}


@pytest.mark.parametrize("wrap", ["{}", "Here: {} -- done"], ids=["alone", "in-prose"])
def test_extract_json_handles_an_escaped_quote(wrap):
    raw = wrap.replace("{}", r'{"text": "say \"hi\" }", "n": 2}')
    assert json.loads(extract_json(raw)) == {"text": 'say "hi" }', "n": 2}


def test_extract_json_skips_a_false_opener():
    """The first `{` does not open the real value. A scanner that gave up on the first
    candidate would miss the document that follows it."""
    assert json.loads(extract_json('not { json at all\n{"a": 1}')) == {"a": 1}


def test_extract_json_prefers_the_real_answer_over_an_empty_object_in_the_preamble():
    """THE nasty one. A reply mentioning `{}` before the answer made a first-match scanner
    return `{}`: syntactically perfect, validation passes, no fallback fires, and a rail
    that asked for nine sections writes nine blank ones and reports success. A blank document delivered as a
    finished one is exactly what this module exists to refuse."""
    raw = 'Empty sections stay as {} -- here is the result:\n{"strengths": "real text"}'
    assert json.loads(extract_json(raw)) == {"strengths": "real text"}


def test_extract_json_prefers_the_largest_of_several_values():
    raw = '[] then {"a": 1, "b": 2} then {}'
    assert json.loads(extract_json(raw)) == {"a": 1, "b": 2}


@pytest.mark.parametrize("raw", ["", "no json here", "{unclosed", "{'single': 'quotes'}"])
def test_extract_json_returns_none_when_there_is_none(raw):
    assert extract_json(raw) is None


def _per_opener_walk(text: str) -> str | None:
    """extract_json as it was until 2026-10-14, verbatim: from every opener, a walk to the end of
    its value. Quadratic (8,000 unclosed `[` took 1.3 s), and kept here only as the oracle the
    one-pass scan must agree with."""
    if not text:
        return None
    best: str | None = None
    best_len = -1
    for i, ch in enumerate(text):
        if ch not in "{[":
            continue
        close = "}" if ch == "{" else "]"
        depth = 0
        in_str = False
        esc = False
        for j in range(i, len(text)):
            c = text[j]
            if esc:
                esc = False
                continue
            if c == "\\" and in_str:
                esc = True
                continue
            if c == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if c == ch:
                depth += 1
            elif c == close:
                depth -= 1
                if depth == 0:
                    span = text[i:j + 1]
                    if len(span) > best_len:
                        try:
                            parsed = json.dumps(json.loads(span))
                        except json.JSONDecodeError:
                            break
                        best, best_len = parsed, len(span)
                    break
    return best


def _random_value(rng, depth: int):
    pick = rng.random()
    if depth <= 0 or pick < 0.3:
        return rng.choice([0, 7, True, None, "", "a", '{"', "[x]", 'q"}', "back\\slash", "{}"])
    if pick < 0.65:
        return [_random_value(rng, depth - 1) for _ in range(rng.randint(0, 3))]
    return {rng.choice(["a", "b", "{", '"', "]"]): _random_value(rng, depth - 1)
            for _ in range(rng.randint(0, 3))}


def _random_reply(rng) -> str:
    """Prose and JSON in pieces, some cut short: every bracket, quote and backslash case the scan
    has (an odd quote before a value, a value inside another's string, an escape at a piece's
    edge, a value cut off part way)."""
    parts = []
    for _ in range(rng.randint(1, 6)):
        if rng.random() < 0.45:
            parts.append("".join(rng.choice('{}[]"\\ a1,:') for _ in range(rng.randint(0, 14))))
        else:
            value = json.dumps(_random_value(rng, 3))
            parts.append(value[:rng.randint(0, len(value))] if rng.random() < 0.3 else value)
    return "".join(parts)


def test_extract_json_agrees_with_the_per_opener_walk():
    """The one-pass scan finds exactly what a walk from every opener found, on 5,000 seeded
    replies, the longest-then-leftmost choice included."""
    import random
    rng = random.Random(20261014)
    for _ in range(5000):
        raw = _random_reply(rng)
        assert extract_json(raw) == _per_opener_walk(raw), raw


@pytest.mark.parametrize("raw,expect", [
    # A quote in the prose before the value: each opener reads as if the text began there.
    ('the 27" screen is {"a": 1}', {"a": 1}),
    # A value inside a string of a value that was cut off is still a candidate.
    ('{"note": "see [1, 2]", "cut', [1, 2]),
    # The second `[` is inside the first one's string, so it starts a walk of its own; the `\"`
    # puts both inside a string, and from there they share one, closing at the same `]`.
    ('so ["a[\\"" ] it is', ['a["']),
    # A backslash before an ordinary character (`\t`): that character is the escaped one, and
    # the quote after it still ends the string.
    ('saved {"path": "C:\\temp", "n": 1} there', {"path": "C:\temp", "n": 1}),
], ids=["odd-quote-before", "inside-a-cut-string", "two-walks-join", "escape-then-letter"])
def test_extract_json_reads_each_opener_from_where_it_stands(raw, expect):
    assert json.loads(extract_json(raw)) == expect
    assert extract_json(raw) == _per_opener_walk(raw)


@pytest.mark.parametrize("lead,run,count", [
    ("", "[", 20000), ("", "{", 20000), ("", '{"a": [', 10000), ('"', "[", 20000),
    ("", "[", 64000)], ids=["brackets", "braces", "nested", "after-a-quote", "64k"])
def test_extract_json_is_linear_in_unclosed_openers(lead, run, count):
    """8,000 unclosed `[` took the per-opener walk 1.3 s and 20,000 took it 8.2 s, in the event
    loop, so a 64 KiB run would have frozen every route for about 90 s. One pass: milliseconds."""
    raw = lead + run * count
    began = time.perf_counter()
    assert extract_json(raw) is None
    assert time.perf_counter() - began < 0.5


def test_extract_json_refuses_a_value_nested_past_the_parser():
    """The per-opener walk let the parser's RecursionError out (not a JSON error, so not caught),
    and the broker counted the call as a CLI failure named after the exception. Too deep to read
    is no JSON, and a fragment of it is not the answer either."""
    began = time.perf_counter()
    assert extract_json("[" * 20000 + "]" * 20000) is None
    assert extract_json("prose " + "[" * 20000 + "]" * 20000 + " {}") is None
    # An UNCLOSED run that deep is no candidate at all, so a value after it is still the answer,
    # as the per-opener walk found it (in 8 s).
    assert json.loads(extract_json("[" * 20000 + ' {"a": 1}')) == {"a": 1}
    assert time.perf_counter() - began < 0.5


@pytest.mark.parametrize("raw", ['{"a": 1}', 'so {"a": 1} it is'], ids=["whole", "inside"])
def test_extract_json_refuses_a_value_too_deep_to_write_back(monkeypatch, raw):
    """The parser reads a little deeper than json.dumps writes (16,000 levels parse and cannot be
    written back on this box's Python), so the write can fail where the read did not: no answer,
    not a raise. Simulated, since where the two limits fall depends on the C stack."""
    def too_deep(*_a, **_k):
        raise RecursionError("maximum recursion depth exceeded while encoding a JSON object")

    monkeypatch.setattr(cli_backend.json, "dumps", too_deep)
    assert extract_json(raw) is None


def test_extract_json_stops_when_failing_candidates_cost_more_than_the_budget(monkeypatch):
    """Nested candidates that all fail late cost a parse each: 2,000 of them over 400 KB would
    read 800 MB. The decoder's reading of failures is bounded at four times the text."""
    raw = "[" * 2000 + "1," * 200_000 + "x" + "]" * 2000
    calls = []
    real = cli_backend._JSON_DECODER

    class _Counting:
        def raw_decode(self, s, idx=0):
            calls.append(idx)
            return real.raw_decode(s, idx)

    monkeypatch.setattr(cli_backend, "_JSON_DECODER", _Counting())
    assert extract_json(raw) is None
    # Each failure reads ~400 KB of the 404 KB text; four times the text is four of them.
    assert 1 <= len(calls) <= 5


def test_extract_json_reads_a_whole_value_once():
    """A reply that is one value and nothing more is parsed as it stands, the case the JSON
    instruction and a schema ask for."""
    value = [{"i": i, "v": "x" * 20} for i in range(3000)]
    began = time.perf_counter()
    assert json.loads(extract_json(json.dumps(value))) == value
    assert time.perf_counter() - began < 0.5


# --- failure classification ------------------------------------------------

@pytest.mark.parametrize("text,reason", [
    ("Claude usage limit reached", "rate_limited"),
    ("HTTP 429 Too Many Requests", "rate_limited"),
    ("Please run /login to continue", "not_signed_in"),
    ("Invalid API key / not authenticated", "not_signed_in"),
    # Verbatim from a real bad setup-token, 2026-10-07. Filed as `failed` before the fix.
    ("Failed to authenticate. API Error: 401 Invalid bearer token", "not_signed_in"),
    ("segmentation fault", "failed"),
    ("", "failed"),
])
def test_classify_failure(text, reason):
    assert classify_failure(text) == reason


# --- executable resolution -------------------------------------------------

def test_resolve_rejects_a_configured_path_that_does_not_exist():
    assert resolve_cli_exe("C:/definitely/not/here/claude.exe") is None


def test_resolve_accepts_a_configured_real_file(tmp_path):
    exe = tmp_path / "claude.exe"
    exe.write_bytes(b"")
    assert resolve_cli_exe(str(exe)) == str(exe)


def test_resolve_ignores_a_cmd_shim_found_on_path(tmp_path, monkeypatch):
    """npm installs claude.cmd / claude.ps1 beside the real binary and `which` prefers the
    shim on Windows. A shim needs a shell, and the broker launches without one.

    The files are REAL and `Path.is_file` is left alone. The first version of this test
    patched `is_file` to always return False, which made every return path in the function
    None regardless of the suffix check -- so deleting the guard it names left it passing.
    A check that cannot fail is not a check."""
    shim = tmp_path / "claude.cmd"
    shim.write_text("@echo off")
    monkeypatch.setattr("shutil.which", lambda n: str(shim) if "claude" in n else None)
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "nohome")
    assert resolve_cli_exe("") != str(shim)


def test_resolve_accepts_a_real_exe_found_on_path(tmp_path, monkeypatch):
    """The other half: without this, the test above passes for a function that always
    returns None and the suffix rule still is not being exercised."""
    exe = tmp_path / "claude.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr("shutil.which", lambda n: str(exe) if "claude" in n else None)
    assert resolve_cli_exe("") == str(exe)


def test_resolve_rejects_a_configured_cmd_shim(tmp_path):
    """`Get-Command claude` on this box returns claude.ps1, and pasting that into
    BROKER_CLI_EXE used to be accepted on is_file() alone -- after which status reported the
    backend ready while every call died with WinError 193 and was labelled `not_installed`,
    i.e. "absent" about a CLI that was present and misconfigured."""
    shim = tmp_path / "claude.ps1"
    shim.write_text("# shim")
    assert resolve_cli_exe(str(shim)) is None


#: The opt-in these tests check THEMSELVES. Deselecting them by name (run-tests.ps1's
#: `-k "not LiveTests"`) was the only guard, so a bare `pytest services/broker/tests` on a box
#: with `claude` installed ran them, and every one is a real call that spends the plan of
#: whoever the CLI is signed in as. `run-tests.ps1 -IncludeLive` sets it; the mutation
#: harnesses strip it from every suite they run (tools/tests/mutation_runner.py).
LIVE_OPT_IN = "CLI_LIVE"


def claude_md_above(path) -> pathlib.Path | None:
    """The nearest CLAUDE.md in `path` or in any folder above it, or None: install-services'
    Test-BrokerCliEnv rule for BROKER_CLI_WORKDIR, in Python. Claude Code reads every one of
    them into a call made there."""
    here = pathlib.Path(os.path.abspath(path))
    for folder in (here, *here.parents):
        if (folder / "CLAUDE.md").is_file():
            return folder / "CLAUDE.md"
    return None


def live_base(candidates) -> tuple[str | None, list[str]]:
    """Where a live call's workdir and temp root may go: the first of `candidates` that exists
    with no CLAUDE.md in it or above it, and why each one before it was refused."""
    refused: list[str] = []
    for cand in candidates:
        if not cand:
            continue
        if not os.path.isdir(cand):
            refused.append(f"{cand} does not exist")
            continue
        found = claude_md_above(cand)
        if found is not None:
            refused.append(f"{cand} has {found} at or above it")
            continue
        return cand, refused
    return None, refused


def _live_candidates() -> list[str]:
    """CLI_LIVE_DIR if set, then (Windows) the Public profile, then the system temp directory,
    which on a workstation is usually under a profile that keeps a CLAUDE.md."""
    return [os.environ.get("CLI_LIVE_DIR", ""),
            os.environ.get("PUBLIC", "") if sys.platform == "win32" else "", _REAL_TEMP]


def test_the_live_workdir_chooser_refuses_a_tree_with_a_claude_md_above_it(tmp_path):
    """A live call made under a user profile that keeps a CLAUDE.md reads it into the prompt,
    so the measurement is not of the isolated call the broker makes. The chooser refuses such
    a tree whatever the depth, naming the file, and moves on to the next candidate."""
    profile = tmp_path / "Users" / "someone"
    temp = profile / "AppData" / "Local" / "Temp"
    temp.mkdir(parents=True)
    planted = profile / "CLAUDE.md"
    planted.write_text("an agent guide\n", encoding="utf-8")
    public = tmp_path / "Users" / "Public"
    public.mkdir()
    base, refused = live_base([str(temp)])
    assert base is None and len(refused) == 1 and str(planted) in refused[0], refused
    # Nothing planted above Public: chosen, unless this box keeps a CLAUDE.md higher up (a
    # user profile can, and pytest's temp is under one), and then that one is named.
    base, refused = live_base([str(temp), "", str(public)])
    above = claude_md_above(tmp_path)
    if above is None:
        assert base == str(public) and len(refused) == 1, refused
    else:
        assert base is None and str(above) in refused[1], refused
    missing = tmp_path / "missing"
    assert live_base([str(missing)]) == (None, [f"{missing} does not exist"])


@unittest.skipUnless(os.environ.get(LIVE_OPT_IN) == "1",
                     f"spends the owner's real Claude quota: set {LIVE_OPT_IN}=1 to run "
                     "(run-tests.ps1 -IncludeLive does)")
class LiveTests(unittest.TestCase):
    """Spends the owner's real Claude subscription quota. Skipped unless CLI_LIVE=1.

    `unittest.TestCase` because that is how every other LiveTests in this repo is written, and
    because pytest collects a TestCase subclass whatever it is named -- a plain `class
    LiveTests:` is silently NOT collected, which would make this read as green while never
    running. Found exactly that way.

    Two calls, deliberately. The faked tests above prove the translation; they cannot prove
    that the REAL CLI accepts this argv, which is the one thing a mock can never tell you.
    The JSON call is here rather than faked-only because `format="json"` is an approximation,
    and whether a real model honours it is a question about the model, not about the code.

    Every call runs as the broker's would: in an empty workdir with no CLAUDE.md in it or above
    it (`live_base`; until 2026-10-14 four of these ran in pytest's own directory and two
    under the system temp, both under this repo's or a profile's CLAUDE.md), with its temp
    directories under a root of the test's own, and each test ends by checking that the calls
    left nothing in either.
    """

    def setUp(self) -> None:
        base, refused = live_base(_live_candidates())
        if base is None:
            self.skipTest("no directory without a CLAUDE.md in it or above it for the live "
                          "workdir, so a call would read one into its prompt: "
                          + "; ".join(refused) + ". Set CLI_LIVE_DIR to one that has none.")
        self.base = tempfile.mkdtemp(prefix="cli-live-", dir=base)
        self.addCleanup(shutil.rmtree, self.base, True)
        self.work = os.path.join(self.base, "work")
        os.mkdir(self.work)
        self.temp_root = os.path.join(self.base, "tmp")

    def _client(self) -> ClaudeCliClient:
        exe = resolve_cli_exe("")
        if not exe:
            self.skipTest("claude CLI not installed")
        return ClaudeCliClient(exe, timeout=120.0, workdir=self.work, temp_root=self.temp_root)

    def _left_nothing(self) -> None:
        """The workdir is as empty as it began, and no call's temp directory outlived it."""
        self.assertEqual(os.listdir(self.work), [])
        self.assertEqual(_left_under(self.temp_root), [])

    @unittest.skipUnless(sys.platform == "win32", "CLI path resolution is Windows-shaped")
    def test_a_real_call_round_trips(self):
        out = asyncio.run(self._client().chat(
            [{"role": "system", "content": "You answer with one word."},
             {"role": "user", "content": "Reply with the single word: ZEBRA"}]))
        self.assertIn("ZEBRA", out["message"]["content"].upper())
        self.assertGreater(out["cli"]["input_tokens"], 0)
        self._left_nothing()

    @unittest.skipUnless(sys.platform == "win32", "CLI path resolution is Windows-shaped")
    def test_a_real_schema_call_is_enforced_by_the_cli(self):
        """`--json-schema` is the capability this path used to refuse as nonexistent."""
        out = asyncio.run(self._client().chat(
            [{"role": "system", "content": "You translate English to Spanish."},
             {"role": "user", "content": 'Translate "red".'}],
            format={"type": "object",
                    "properties": {"en": {"type": "string"}, "es": {"type": "string"}},
                    "required": ["en", "es"], "additionalProperties": False}))
        parsed = json.loads(out["message"]["content"])
        self.assertEqual(set(parsed), {"en", "es"})
        self.assertIn("rojo", parsed["es"].lower())
        self._left_nothing()

    @unittest.skipUnless(sys.platform == "win32", "CLI path resolution is Windows-shaped")
    def test_a_real_json_call_returns_parseable_json(self):
        out = asyncio.run(self._client().chat(
            [{"role": "system", "content": "You translate English to Spanish."},
             {"role": "user", "content": 'Translate "red". Keys: en, es.'}],
            format="json"))
        parsed = json.loads(out["message"]["content"])
        self.assertEqual(parsed["en"].lower(), "red")
        self.assertIn("rojo", parsed["es"].lower())
        self._left_nothing()

    @unittest.skipUnless(sys.platform == "win32", "CLI path resolution is Windows-shaped")
    def test_a_real_image_call_round_trips(self):
        """The stream-json image path against the real CLI: the one thing the fakes above
        cannot say is whether the CLI accepts this argv and this stdin line. Measured by hand
        on 2026-10-09 (claude 2.1.293, Haiku 5.5): a red square answered "Red" in about 6 s."""
        out = asyncio.run(self._client().chat(
            [{"role": "user", "content": "What colour is this square? Answer with one word.",
              "images": [base64.b64encode(_solid_png(32, (220, 20, 20))).decode("ascii")]}]))
        self.assertIn("red", out["message"]["content"].lower())
        self.assertGreater(out["cli"]["input_tokens"], 0)
        self._left_nothing()

    @unittest.skipUnless(sys.platform == "win32", "CLI path resolution is Windows-shaped")
    def test_stream_text(self):
        """The partial-message argv against the real CLI: text arrives in several frames while
        the process is still running, the done frame carries usage, and nothing is written to
        the workdir. Measured by hand on 2026-10-13 (claude 2.1.296, Haiku 5.5): a count to
        twelve came as six text deltas whose join was exactly the result line."""
        client = self._client()
        spawned: list = []
        real_spawn = client._spawn

        async def spawn(argv):
            proc, temp = await real_spawn(argv)
            spawned.append(proc)
            return proc, temp

        client._spawn = spawn

        async def go():
            running, done = [], None
            async for frame in client.chat_stream(
                    [{"role": "system", "content": "You answer briefly."},
                     {"role": "user", "content": "Count from one to thirty in words, one per "
                                                 "line, and nothing else."}]):
                if frame["done"]:
                    done = frame
                else:
                    running.append(spawned[0].returncode is None)
            return running, done

        running, done = asyncio.run(go())
        self.assertGreaterEqual(sum(running), 2, "fewer than two frames before the CLI exited")
        self.assertGreater(done["cli"]["input_tokens"], 0)
        self.assertGreater(done["cli"]["output_tokens"], 0)
        self._left_nothing()

    @unittest.skipUnless(sys.platform == "win32", "CLI path resolution is Windows-shaped")
    def test_prefill_live(self):
        """Prefill emulation against the real model, in the three shapes smb-partner-enablement's
        Scenario Builder sends (a phrase ending in a space, a bold quote, a heading line), one
        buffered and two streamed: does the model begin with the opening it was given, so the
        call is served rather than falling back as `prefill_mismatch`? The fakes cannot say;
        whether a model honours the instruction is a question about the model."""
        client = self._client()
        brief = ("A Microsoft partner sells to a 40-seat retail chain that runs Microsoft 365 "
                 "Business Basic and has had two phishing incidents this year.")

        def ask(instruction: str, opening: str) -> list[dict]:
            return [{"role": "system", "content": "You coach Microsoft partners. Be brief."},
                    {"role": "user", "content": f"{brief}\n\n{instruction}"},
                    {"role": "assistant", "content": opening}]

        out = asyncio.run(client.chat(ask(
            "Write the single most important next move for this partner, as one short "
            "paragraph beginning 'Your next move:'.", "Your next move: ")))
        said = out["message"]["content"]
        self.assertTrue(said.strip())
        self.assertFalse(said.lstrip().lower().startswith("your next move"), said[:80])

        async def stream(msgs) -> str:
            return "".join([f["message"]["content"] async for f in client.chat_stream(msgs)])

        qa = asyncio.run(stream(ask(
            "Write the two objections this customer is most likely to raise, each as a bold "
            "heading in the customer's own words, followed by a one-sentence response.", '**"')))
        self.assertTrue(qa.strip())
        self.assertFalse(qa.startswith('**"'), qa[:80])
        value = asyncio.run(stream(ask(
            "Write a Value Summary with the headings '## Where the value comes from' and "
            "'## What to measure'. No numbers.", "## Where the value comes from\n")))
        self.assertIn("## What to measure", value)
        self.assertFalse(value.lstrip().startswith("## Where the value"), value[:80])
        self._left_nothing()


def _solid_png(size: int, rgb: tuple[int, int, int]) -> bytes:
    """A real, decodable size x size PNG of one colour, from the standard library alone (the
    broker's own dependencies carry no imaging library)."""
    def chunk(kind: bytes, body: bytes) -> bytes:
        return (struct.pack(">I", len(body)) + kind + body
                + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))
    row = b"\x00" + bytes(rgb) * size
    return (bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(row * size)) + chunk(b"IEND", b""))
