"""Broker configuration (env-driven, 12-factor)."""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple

from pydantic import PrivateAttr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.cli_backend import DEFAULT_SETTINGS_JSON
from app.fsutil import replace_with_retry


def _decode_token(raw: bytes) -> str | None:
    """Bytes from a token file -> a usable token, or None if it is not readable text.

    Three encodings, because the README tells the operator to run `claude setup-token` in
    PowerShell and PowerShell 5.1's `>` and `Out-File` write **UTF-16LE with a BOM**. Decoding
    those as UTF-8 raises `UnicodeDecodeError`, which is a `ValueError` and sailed straight
    past the `except OSError` this used to have -- out of `cli_state()`, which `Broker.status()`
    calls OUTSIDE its "status must never raise" guard. A mistyped encoding therefore took
    `/v1/status` to a 500 and every chat to a 502: the exact opposite of the quiet degradation
    this function's asymmetry with `_resolve_auth_token_file` exists to provide.

    The no-BOM UTF-16 case is worse than loud: it decodes as UTF-8 into `s\\x00k\\x00-...`,
    survives `.strip()`, and reaches `create_subprocess_exec` as an env value with embedded
    NULs. So a decoded token is also rejected if it contains control characters -- a real
    token never does.
    """
    text: str | None = None
    for enc in ("utf-8-sig", "utf-16", "utf-16-le"):
        try:
            text = raw.decode(enc)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if text is None:
        return None
    token = text.strip().strip("﻿\x00").strip()
    if not token or any(ch < " " for ch in token):
        return None
    return token

_BROKER_DIR = Path(__file__).resolve().parent.parent  # services/broker/
_REPO_ROOT = _BROKER_DIR.parent.parent                # the ai-platform repo root
# Host roots that live OUTSIDE the repo: the shared speech/media venvs and the model
# assets. Derived from the repo location (…/<root>/projects/ai-platform) rather than
# hard-coded, so relocating the tree does not need a code edit. Every value below is
# still env-overridable (BROKER_*) for layouts that differ.
_AI_WORK = _REPO_ROOT.parent.parent
_VENVS = _AI_WORK / "venvs"                              # shared speech/media venvs

# What a role may be called. ONE definition, used both to reject a bad name in set_role()
# and to ignore one when reading roles.json back in — those two disagreed until 2026-09-09,
# so a `_comment` key could not be written through the API but did come back out as a role.
_ROLE_NAME = re.compile(r"[a-z0-9][a-z0-9-]*")

# What a named token may reach. Two levels, not a per-endpoint matrix: a permissions matrix in
# an admin panel nobody audits is a scope that drifts, and the real distinction here is binary.
#
#   inference  run work on the GPU and read state. Chat, embeddings, media, voice, the whole
#              OpenAI surface, and every GET. This is what a remote workstation needs.
#   full       also CHANGE the platform: repoint a role, disable a model, load/unload/cancel.
#              Only the gateway and this box's own tooling should ever hold one.
TOKEN_SCOPES = ("inference", "full")

# Serialises the read-modify-write in add_token / revoke_token. Both read the whole store,
# change one row and write it back, so without this a mint racing a revoke CLOBBERS it and
# the revoked token comes back -- losing a revocation is the one failure here that is not
# merely inconvenient. A threading.Lock and not a file lock on purpose: the broker runs as a
# single uvicorn process with no --workers (deploy/install-services.ps1:139), so in-process
# is the whole concurrency surface. If it ever grows workers this has to become a file lock,
# and that comment is the reminder.
_TOKENS_LOCK = threading.Lock()

# The same serialisation for set_cli_role, for the same reason: two switches at once each read
# the whole file, change one key and write it back, and without this the second write drops the
# first one's change. A lost "off" is the expensive direction.
_CLI_ROLES_LOCK = threading.Lock()


#: The way back from an invalid switch file, in the refusal itself. Deleting the file is the
#: documented reset, and it is not a neutral one: every switch made in the Rails tab is lost and
#: BROKER_CLI_ROLES decides again, which can switch back on a role somebody switched off.
_CLI_ROLES_RESET = ("Fix it and switch again, or delete it: with no file, every role goes back "
                    "to BROKER_CLI_ROLES (the service defaults) and every switch made here is "
                    "lost.")

#: BROKER_CLI_MODELS' default: the Claude models a role may be given in the switch file, in the
#: order the Rails tab offers them.
DEFAULT_CLI_MODELS = "claude-haiku-5-5,claude-sonnet-5-5,claude-opus-5-5"

#: set_cli_role's `model` when the call leaves the role's model as it is.
KEEP_MODEL: Any = object()


class CliRoles(NamedTuple):
    """What decides which roles may use the CLI right now, and with which model. See
    BrokerSettings.cli_effective."""
    roles: set[str]
    source: str
    problem: str
    note: str
    #: role -> the Claude model the switch file gives it, for every role the file names with one
    #: BROKER_CLI_MODELS allows, switched on or off. Empty with no file and while it is invalid.
    models: dict[str, str]


class _SwitchFile(NamedTuple):
    """One parse of BROKER_CLI_ROLES_FILE (BrokerSettings._parse_cli_roles)."""
    switches: dict[str, bool]
    problem: str
    ignored: list[str]
    models: dict[str, str]
    #: (role, model) for each model the file gives a role that BROKER_CLI_MODELS does not allow.
    refused: list[tuple[str, str]]


def _bad_switch_file(problem: str) -> _SwitchFile:
    return _SwitchFile({}, problem, [], {}, [])


def _shown(value: Any) -> str:
    """A value from the switch file as a refusal quotes it: JSON, cut to 40 characters."""
    try:
        return json.dumps(value)[:40]
    except (ValueError, RecursionError):
        return type(value).__name__


def _switch_object_problem(value: dict[str, Any]) -> str:
    """Why a switch written as an object is not one, or "" when it is. The object form is
    ``{"enabled": true|false, "model": "<id>"|null}``; "model" may be left out. Its "enabled" is
    held to the true-or-false rule by the caller, like a bare switch."""
    extra = sorted(str(k) for k in value if k not in ("enabled", "model"))
    if extra:
        names = ", ".join(repr(k[:40]) for k in extra[:4]) + (" ..." if len(extra) > 4 else "")
        return f'may hold only "enabled" and "model", not {names}'
    if "enabled" not in value:
        return 'must say "enabled": true or false'
    model = value.get("model")
    if model is not None and not isinstance(model, str):
        return f'"model" must be a model id or null, not {_shown(model)}'
    return ""


class CliRolesFileError(RuntimeError):
    """BROKER_CLI_ROLES_FILE cannot take a switch: it is not set, is invalid, or cannot be
    written. The broker answers 409 with this message: it is a state of the box to fix, not a
    malformed request."""


class RolesFileError(RuntimeError):
    """roles.json cannot be read, so set_role cannot repoint a role without rewriting the file
    from nothing. The broker answers 409 with this message, like CliRolesFileError."""


class SettingsWriteError(RuntimeError):
    """A settings file (roles.json, tokens.json, disabled.json) could not be written: its atomic
    replace still failed after the retries (app/fsutil.py), or its temp file could not be
    written. Nothing was changed. The broker answers 409 with this message, like
    CliRolesFileError: a state of the box, not a malformed request."""


def _write_settings_file(path: Path, body: str) -> None:
    """Write `body` over `path` through a temp file beside it and an atomic replace, so a
    concurrent hot-read never sees half a file. The replace is retried while a file filter
    holds the file (app/fsutil.py).

    An OSError that outlasts the retries, or a temp file that cannot be written, raises
    SettingsWriteError naming the file (never its path), after removing the temp file. It used
    to escape as a 500 from PUT /v1/roles, PUT /v1/disabled or POST/DELETE /v1/tokens and leave
    `<name>.tmp` behind. The removal is best effort: a filter can hold the temp file too, and
    then it stays until the next write of the same file truncates and replaces it."""
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(body, encoding="utf-8")
        replace_with_retry(tmp, path)
    except OSError as exc:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise SettingsWriteError(
            f"{path.name} could not be written ({exc.strerror or type(exc).__name__}), so "
            f"nothing was changed. Something on the broker host is holding the file (an "
            f"endpoint scanner, an editor) or the broker cannot write there; try again.") from exc


def _file_signature(path: Path) -> tuple[int, int, int, int]:
    """What tokens() compares to tell whether its last parse still holds: the file's identity
    (an atomic replace puts a NEW file in place, so a new id) and its write time and size.
    A stat, not an open: it does not hold the file against a writer's replace. Raises OSError,
    like Path.stat."""
    st = path.stat()
    return (st.st_dev, st.st_ino, st.st_mtime_ns, st.st_size)


def _parse_tokens(raw: bytes) -> list[dict[str, Any]]:
    """tokens.json's bytes -> its usable rows; [] for anything malformed, a file that is not
    UTF-8 (UnicodeDecodeError, a ValueError) or is nested too deeply included (see tokens())."""
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, RecursionError):
        return []
    if not isinstance(data, list):
        return []
    out = []
    for row in data:
        if not isinstance(row, dict):
            continue
        if not (row.get("id") and row.get("hash") and row.get("scope") in TOKEN_SCOPES):
            continue
        out.append(row)
    return out


# Model ROLES (classes): a rail asks the broker for "@<role>" (e.g. "@chat") instead of a
# specific model name. The broker expands the role to the pattern below, then resolves the
# newest installed match. To repoint a whole class of model (pull a newer one, or swap
# families), edit ONE entry here or in services/broker/roles.json (hot-read every request —
# no broker restart needed once this code is live).
DEFAULT_ROLES: dict[str, str] = {
    "chat": "mistral-small3*:24b",   # default instruction-follower (finance, recipe-book, job-aid, edu)
    "chat-fast": "gemma4*:12b",       # snappy / light (terminal-fun)
    "chat-large": "qwen3.6*:27b",    # higher-quality long-form (iep)
    "reasoning": "qwen3.6*:27b",   # deep reasoning (finance fraud)
    "code": "qwen3.6*:27b",
    "vision": "gemma4*:26b",
    "embed": "bge-m3*",
    # --- per-rail roles (one role per rail model slot) -----------------------
    # Each rail's model env var points at its OWN role (e.g. FINANCE_FRAUD_MODEL=@finance-fraud)
    # so the admin "Rails" settings can repoint a single rail without moving others that would
    # otherwise share a generic class. Seeded to the SAME model each slot resolves to today, so
    # introducing them is behaviour-neutral until an admin changes one. Edited live via
    # /v1/roles (roles.json overlay, hot-read) — no rail restart.
    "edu": "mistral-small3*:24b",             # edu-suite bilingual content
    "recipe": "gemma4*:26b",                    # recipe-book culinary assistant
    "recipe-vision": "gemma4*:26b",            # recipe-book recipe-photo reader
    "terminal-fun": "gemma4*:12b",              # terminal-fun assistant
    # Co-Worker: synthesizes whole harvested items (email / calendar / Teams) into the
    # executive brief. Long-form instruction-following over whole documents, not chunks —
    # the same shape of job as job-aid and edu, so it gets the same model.
    "co-worker-synthesis": "mistral-small3*:24b",
    # SMB Partner Enablement + Gemini Enterprise CX: grounded RAG. Each keeps its generative
    # model and @embed resident together so a question costs no swap. Both rails' MODELS.md
    # argue for 3B/4B-class models — that arithmetic is for an 8 GB card and does not apply
    # here: on the 24 GB 4090 the embedder co-resides with a full-size model comfortably.
    #
    # DELIBERATELY THE SAME MODEL FOR BOTH. gemini-cx's MODELS.md names gemma4*:12b as its
    # full-stack default, and that is a trap: gemma4 is a THINKING model, the public repo
    # always overrode this role down to non-thinking gemma3:4b, so the 12b default was never
    # actually exercised. The rail asks for num_predict=800 (GEMINI_CX_MAX_TOKENS) and gemma4
    # spends all 800 on the thinking phase — done_reason 'length', content EMPTY, every
    # question silently answerless. Measured on the real 8-chunk prompt: 800 -> 0 chars of
    # answer + 3066 of thinking; it needs ~4000 to emit anything. mistral-small3.2 answers the
    # same prompt in 233 eval tokens with no thinking at all, well inside the rail's budget.
    # Sharing it with smb-partner-rag also means moving between the two RAG rails costs no
    # model swap, which is the property gemini-cx's MODELS.md is written to protect.
    "smb-partner-rag": "mistral-small3*:24b",
    "gemini-cx-rag": "mistral-small3*:24b",
    # Present in roles.json but absent here until now: on a box whose overlay is missing
    # or malformed, @ai-playground fell through roles().get(name, name) to the literal
    # string "ai-playground" and reached Ollama as a model name -- a 404 the broker wrapped
    # as a 502, never "no such role". The overlay may only ever REPOINT, never introduce.
    "ai-playground": "nemotron-3-nano:4b",
    # OpenMAIC course generation, on its streaming path. Not a thinking model, which keeps the
    # role safe whatever a caller sends: /v1/chat/stream does forward `think` (main.py's
    # chat_stream, ollama.py chat_stream; tests/test_chat_think.py), but a rail that omits it
    # gets the model's default, and a thinking model's default would spend the budget thinking.
    "openmaic": "mistral-small3*:24b",
    # Media (image) role — resolves to a media worker backend, NOT an Ollama model.
    "recipe-icon": "flux-schnell",             # recipe-book per-recipe icon image generator
    # ai-voice rail: text normalization before TTS runs on the default chat model.
    # Watch Builder: writes a watchface DESIGN DOCUMENT (schema-constrained JSON), never C.
    # Long-form instruction-following against a fixed schema, the same shape of job as
    # co-worker-synthesis, so it gets the same model and costs no swap between them.
    # Deliberately NOT a thinking model: the rail asks for 3000 tokens and a thinking model
    # would spend the budget before emitting the document, which is the gemini-cx trap above.
    "watch-builder-designer": "mistral-small3*:24b",
}


# Voice-studio engines. Each voice's `engine` (from the voice registry) maps to the
# interpreter + CLI entry that synthesizes it, under a root the DEPLOYMENT supplies via
# BROKER_VOICE_ENGINES_DIR. Overlaid by services/broker/voice_engines.json if present.
# Each engine's CLI contract: <python> <entry> --voice <id> --text @<file> --out <wav>.
#
# The root is configuration, not a constant, because the broker must not name a rail. It
# used to resolve the ai-voice rail's native/ directly, which made one rail load-bearing for a
# platform service: deleting ai-voice broke /v1/voice/* with no error until a job ran.
# Dispatch stays HERE rather than moving into the rail, because synthesis takes the GPU
# gate and evicts every resident heavy model — arbitration only the broker can do.
#
# Empty root => no engines => voice reports unavailable, which is the honest state for a
# platform with the ai-voice rail removed.
def voice_engine_table(root: Path | None, tts_python: str) -> dict[str, dict[str, str]]:
    if root is None:
        return {}
    return {
        "chatterbox": {
            "python": str(root / ".venv-chatterbox" / "Scripts" / "python.exe"),
            "entry": str(root / "engines" / "chatterbox-finetuning" / "synth_cli.py"),
            "cwd": str(root / "engines" / "chatterbox-finetuning"),
        },
        "rvc": {
            "python": str(root / ".venv-rvc310" / "Scripts" / "python.exe"),
            "entry": str(root / "engines" / "rvc_synth_cli.py"),
            "cwd": str(root),
        },
        # XTTS is the exception with no venv of its own: it shares the broker's speech venv,
        # because both paths run the same Coqui XTTS through edu_media_core.tts and keeping
        # two coqui installs in sync would be a standing liability.
        # It is also the only engine here CAPABLE of a language other than English — but every
        # voice in the registry today is "language": "en", so no Spanish voice is registered.
        # (Platform-wide Spanish read-aloud is a different path: Kokoro via /v1/tts_light.)
        "xtts": {
            "python": tts_python,
            "entry": str(root / "engines" / "xtts_synth_cli.py"),
            "cwd": str(root),
        },
    }


class BrokerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BROKER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # No host/port settings: nothing read them. The bind is uvicorn's command line, and the
    # installed service (deploy/install-services.ps1) binds 0.0.0.0:11500 so containers can
    # reach it; BROKER_AUTH_TOKEN is what keeps that safe, not a loopback address. A
    # BROKER_HOST=127.0.0.1 that changed nothing read as a protection the box did not have.

    # Shared secret for the control plane. When set (BROKER_AUTH_TOKEN), every /v1/* route requires
    # a matching `Authorization: Bearer <token>` (or X-Broker-Token) header, so a rogue container or
    # LAN host on 0.0.0.0:11500 can't drive the GPU / repoint roles. Empty = open (dev / rollout).
    auth_token: str = ""

    # The same secret as a FILE PATH (BROKER_AUTH_TOKEN_FILE), and the form that wins when both
    # are set. Clients gained this so the shared token stops appearing in `docker inspect`, crash
    # dumps and /proc/<pid>/environ; the validator has to accept it too or the two halves of the
    # handshake read different sources and every rail 401s. Contents are stripped — a secret file
    # written by `echo` ends in a newline, and comparing that against a header value that does not
    # is an authentication failure with no visible cause.
    auth_token_file: str = ""

    # Named REMOTE brokers this one may delegate a role to (upstreams.json, hot-read like
    # roles.json). See upstreams() for the file shape and delegate_ref() for the syntax.
    upstreams_file: str = ""

    # Backend GPU model server. Ollama today; swappable later.
    ollama_base_url: str = "http://127.0.0.1:11434"

    # Timeout (seconds) for backend calls. Cold heavy-model loads are slow,
    # so this is generous. None would mean no timeout at all.
    ollama_timeout: float = 600.0

    # keep_alive to apply when a /v1/load doesn't specify one. Default "30m" so a
    # manually loaded model auto-unloads after 30 min idle instead of camping VRAM
    # (-1 = resident indefinitely; any Go duration like "5m" also works).
    # Sent through _normalize_keep_alive before hitting Ollama, so an env value
    # of "-1" is coerced to the integer -1 (a bare "-1" is not a valid duration).
    default_load_keep_alive: int | str = "30m"

    # Comma-separated substrings that mark a model name as an *embedding* model
    # (light; may stay resident alongside one heavy model). Everything else is
    # treated as heavy/generative and subject to the one-at-a-time policy.
    embed_name_hints: str = "embed,bge,nomic-embed,mxbai,gte,e5,minilm"

    # Optional path to a JSON {role: glob} file that overlays DEFAULT_ROLES. Hot-read on
    # each resolve so edits take effect with no restart. Empty => services/broker/roles.json.
    roles_file: str = ""

    # Optional path to a JSON list of admin-disabled model names (availability control: hidden
    # from pickers/UI + unloaded, still served if a role resolves to it). Empty => disabled.json.
    disabled_file: str = ""
    # Named per-host access tokens. See the tokens_path/tokens block below.
    tokens_file: str = ""
    # tokens()'s last parse: (the file's _file_signature, its rows), or None. Not a setting.
    _tokens_seen: tuple[tuple[int, int, int, int], list[dict[str, Any]]] | None = PrivateAttr(
        default=None)

    # --- media (XTTS voice / SDXL image) ------------------------------------
    # torch does not release VRAM in-process (empty_cache is insufficient), so
    # media inference runs in a short-lived WORKER PROCESS that exits to reclaim
    # VRAM. The worker uses edu-suite's existing CUDA venv + edu_media_core, so
    # the broker's own (Ollama-only) venv stays light. All paths are host-native
    # by design (the GPU layer runs native on Windows) and env-overridable
    # (BROKER_MEDIA_*). Set BROKER_MEDIA_ENABLED=false where there's no torch venv.
    media_enabled: bool = True
    # Interpreter with torch / diffusers + edu_media_core importable.
    # This is the SHARED media venv outside the repo, not a rail-local one. (Until the
    # 2026-08-20 relocation this defaulted to a .venv inside edu-suite, which had not existed
    # for some time — every working deployment was silently relying on BROKER_MEDIA_PYTHON
    # being injected by deploy/install-services.ps1. Now the default is the real path.)
    media_python: str = str(_VENVS / "media-venv" / "Scripts" / "python.exe")
    # OPTIONAL separate interpreter for the speech ops (tts / tts_batch).
    #
    # Speech and image want different stacks. Measured 2026-08-15: the image venv runs
    # transformers 5.5.4, while `coqui-tts` 0.27.5 pulls transformers 5.15.0 — so a
    # shared venv means one of them gets a version it was not tested against, and the
    # working image path (recipe-book, bouquet, CVC) is the one at risk. coqui-tts also
    # declines to declare torch at all, so its CUDA build is chosen per-venv. Same reason
    # the ai-voice rail gives each of its engines a separate venv.
    # Empty => speech falls back to media_python (single-venv setups still work).
    tts_python: str = ""
    # edu_media_core source root, prepended to the worker's sys.path. It lives in packages/,
    # NOT under a rail: image generation and XTTS are platform capabilities that recipe-book
    # icons and bouquet vision depend on, and while this pointed into the edu-suite rail,
    # removing that one rail would have broken media for rails that never heard of it.
    media_core_src: str = str(_REPO_ROOT / "packages" / "edu-media-core" / "src")
    # XTTS reference voice clips (english_reference.wav / spanish_reference.wav), kept inside
    # the package so it stays self-contained.
    media_voices_dir: str = str(_REPO_ROOT / "packages" / "edu-media-core" / "voices")
    # Per-job timeout (seconds); a cold model load + a batch can be slow.
    media_timeout: float = 1200.0
    # Hugging Face ids /v1/embed_image may load, comma-separated; the first is the default.
    # An allowlist because the id reaches AutoModel.from_pretrained in the media worker:
    # unchecked, any holder of a broker token chose what the worker downloaded into the HF
    # cache, which is an unbounded fetch onto the system drive.
    image_embed_models: str = "google/siglip2-base-patch16-384"
    # How many UNGATED CPU media jobs (read-aloud, dictation, image embedding) may run at once.
    # Each is its own 350-500 MB interpreter; past this they queue among themselves, never
    # behind the GPU gate.
    light_media_concurrency: int = 2

    # --- Kokoro-82M light TTS (platform-wide read-aloud) --------------------
    # A ~350 MB ONNX voice that runs in the media worker WITHOUT the GPU gate and without
    # evicting anything, so read-aloud can be offered on every rail without ever disturbing
    # the resident chat model. onnxruntime + soundfile only — no torch — which is why it can
    # be added to the media venv without risk to the image path's torch+cuNNN install.
    # Empty paths disable /v1/tts_light (it 502s with a message naming these vars).
    #
    # NOT a replacement for the XTTS `tts` op: that one clones a voice from a reference clip
    # and returns per-segment timings for highlight-sync, which Kokoro does not do. Both stay.
    # Kokoro gets its OWN interpreter, for a hard reason rather than tidiness: kokoro-onnx
    # requires numpy>=2.0.2 and simple-lama-inpainting (image inpainting) requires
    # numpy<2.0.0. Installing Kokoro into the media venv silently upgrades numpy underneath
    # the whole image path — recipe-book icons, edu-suite images, bouquet vision — which is
    # precisely how that venv got broken before. Empty falls back to media_python.
    # Left empty on purpose: empty disables /v1/tts_light. deploy/install-services.ps1
    # injects these (venvs/kokoro-venv + models/kokoro/*) as BROKER_KOKORO_*.
    kokoro_python: str = ""
    kokoro_model_path: str = ""    # kokoro-v1.0*.onnx
    kokoro_voices_path: str = ""   # voices-v1.0.bin
    # The platform-wide default playback voice, applied when a caller names none. Server-side
    # so it can be changed for everyone without rebuilding a frontend. `voice` and
    # `lang_code` must agree — an es voice under lang 'a' produces garbled audio.
    kokoro_voice: str = "af_heart"  # American English, female
    kokoro_lang_code: str = "a"     # 'a' American English, 'b' British, 'e' Spanish

    # --- faster-whisper STT (platform-wide dictation) -----------------------
    # CPU/int8 on purpose. Speech INPUT is the one call where someone is sitting there having
    # just stopped talking, so it must never queue behind the GPU gate or evict the model
    # they are about to ask something. A short utterance transcribes in ~1-2s on CPU.
    #
    # Torch-free (CTranslate2), so it shares the light-speech venv with Kokoro rather than
    # needing a fourth one — verified compatible, no numpy conflict. Its own setting anyway,
    # so the two can be split later without touching call sites. Empty falls back to
    # kokoro_python, then media_python.
    whisper_python: str = ""
    # MULTILINGUAL "small", not "small.en". The .en models are English-ONLY: they ignore the
    # language parameter (warning: "using 'en' instead") and turn Spanish into nonsense —
    # measured here, "El estudiante identificará la idea central..." came back as "de
    # estudiante ... de cunibel." On a bilingual EN/es_MX platform that is not a tradeoff.
    # Multilingual small transcribed BOTH clips verbatim, and was faster on the Spanish one
    # (2.0s vs 5.0s) since it is not fighting the language; it costs ~1.6s on English.
    whisper_model: str = "small"        # weights pulled to the HF cache on first call
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"

    # --- voice studio (ai-voice rail) ---------------------------------------
    # Per-voice synthesis dispatches to an engine's OWN venv (Chatterbox/RVC/...),
    # each a short-lived worker that exits to reclaim VRAM. The registry maps
    # voice_id -> engine + assets; voice_engine_table() maps engine -> interpreter.
    voice_enabled: bool = True
    # Root of the host-native voice engines, supplied by the deployment
    # (BROKER_VOICE_ENGINES_DIR -> install-services.ps1). Empty means no engines are wired,
    # and voice reports unavailable rather than failing at job time.
    voice_engines_dir: str = ""
    # Empty => <voice_engines_dir>/voices/registry.json.
    voice_registry: str = ""
    voice_engines_file: str = ""   # overlay JSON; empty => services/broker/voice_engines.json
    voice_timeout: float = 1200.0

    # --- Claude CLI text backend (the OWNER's own requests only) ------------
    #
    # A second TEXT backend beside Ollama, and the only one that leaves this box. It exists
    # under a licence condition rather than a preference: Anthropic permits subscription
    # (OAuth) credentials for "ordinary use" by the subscriber, and does not permit routing
    # requests "through Free, Pro, or Max plan credentials on behalf of their users". So a
    # call may go to the CLI only when the person who asked IS the subscriber.
    #
    # EVERY default here is off or empty, and that is the design: the feature is inert until
    # an operator names both the owners and the eligible roles. There is no "on by default"
    # reading of this block, because the failure mode of getting it wrong is spending someone
    # else's subscription on someone else's data.
    cli_enabled: bool = False
    # Absolute path to claude.exe. Empty => resolved on PATH / npm's global root. Never a
    # .cmd or .ps1 shim: the broker launches without a shell (see resolve_cli_exe).
    cli_exe: str = ""
    # Platform usernames whose requests MAY use the CLI. Comma-separated. Empty => nobody,
    # which is the whole feature switched off however enabled/roles are set. Every name must be
    # an account of the ONE subscriber whose credential the broker holds (cli_oauth_token[_file]
    # or the login of the account it runs as): listing another person spends that subscriber's
    # plan on them, which is the use the subscription terms forbid.
    cli_owners: str = ""
    # Role names (without the leading @) eligible for the CLI. Comma-separated. Empty =>
    # none. A role-level allowlist rather than "all chat", so turning this on for one rail's
    # assistant cannot quietly send every other rail's calls off the box too.
    cli_roles: str = ""
    # The per-role switch the admin Rails tab writes: a JSON object of role name -> true/false.
    # EXPLICIT, with no default path, on purpose. services/ is published wholesale, so a
    # default under services/broker/ would be an untracked-but-unignored file in a public tree,
    # and every test that builds BrokerSettings without paths would read this box's live
    # switches. Unset, or set to a file that does not exist yet: BROKER_CLI_ROLES decides,
    # exactly as before. Present and valid: the FILE decides, for adding a role as well as for
    # removing one. Present and malformed: no role is eligible (see cli_effective).
    # Hot-read like roles.json, so after the one restart that sets it every switch is live.
    cli_roles_file: str = ""
    cli_timeout: float = 180.0
    # How many CLI calls may run at once. Every call is its own claude.exe, and nothing else
    # bounds them: widening the eligible roles would otherwise mean as many processes as there
    # are owner requests in flight. A call over the limit waits for a slot inside the SAME
    # cli_timeout budget as the call itself, so a rail still sees its fallback start no later
    # than cli_timeout after it asked (see Broker.chat).
    cli_concurrency: int = 2
    # Working directory for the CLI process: point this at an EMPTY directory so no project
    # instructions are read into a rail's prompt. REQUIRED: unset is the `workdir_unset`
    # blocker, because the fallback would be the broker's own cwd, which is the repo root and
    # therefore carries a CLAUDE.md.
    cli_workdir: str = ""
    # Where each CLI call's own TEMP directory is made (and removed once the call's tree is
    # dead; the startup sweep removes any left behind). Empty => `cli-tmp` beside cli_workdir,
    # which shares its checked place. Set it outside the repo and every OneDrive folder, with no
    # CLAUDE.md above it and not inside cli_workdir, which must stay empty (install-services'
    # Test-BrokerCliEnv refuses anything else).
    cli_temp_root: str = ""
    # A `claude setup-token` one-year OAuth token, for running under a service account that
    # has no interactive login of its own. Prefer the _FILE form; see cli_oauth_token_file.
    cli_oauth_token: str = ""
    cli_oauth_token_file: str = ""
    # The model for every call on a role the switch file gives no model of its own (see
    # cli_models). Empty => the CLI's own default, which is what the owner gets interactively.
    cli_model: str = ""
    # The Claude models a role may be given, by id, comma-separated, in the order the Rails tab
    # offers them. A role's own model lives in BROKER_CLI_ROLES_FILE beside its switch. One this
    # list does not allow is refused on write, and ignored with a note when hand-written there,
    # so the switch file never puts any other id on `--model`. Empty => no role can be given one.
    cli_models: str = DEFAULT_CLI_MODELS
    # JSON handed to `claude --settings`. Defaults to excluding the user-scope CLAUDE.md and
    # rules of the account the CLI runs as, which Claude Code otherwise reads into EVERY call --
    # the operator's own instructions prepended to a rail's prompt, measured at 14,184 input
    # tokens against 5,086 without them. Blank it only if you mean to send them.
    cli_settings_json: str = DEFAULT_SETTINGS_JSON

    @model_validator(mode="after")
    def _resolve_auth_token_file(self) -> "BrokerSettings":
        """Collapse BROKER_AUTH_TOKEN_FILE into `auth_token`, so require_token() keeps reading
        one field and nothing downstream has to learn about the second spelling.

        A configured-but-unreadable file RAISES, and that asymmetry with the clients is
        deliberate. On a client an empty token means "send no header" and the broker answers
        401 — loud. Here an empty token means the control plane is OPEN: every /v1/* route
        stops requiring a bearer, and the only symptom is that everything keeps working. A
        typo'd path must not be able to silently disarm the gate it was added to strengthen,
        so the broker refuses to start instead.
        """
        path = (self.auth_token_file or "").strip()
        if path:
            refusal = ("Refusing to start: falling back to an empty token would leave every "
                       "/v1/* route unauthenticated with no visible symptom.")
            not_utf8 = (f"BROKER_AUTH_TOKEN_FILE={path!r} is not UTF-8 text; save it as UTF-8 "
                        f"(with or without a BOM). {refusal}")
            try:
                self.auth_token = Path(path).read_text(encoding="utf-8-sig").strip()
            except OSError as exc:
                raise ValueError(
                    f"BROKER_AUTH_TOKEN_FILE={path!r} could not be read ({exc.strerror}). "
                    f"{refusal}"
                ) from exc
            except ValueError as exc:
                # A UnicodeDecodeError: a file that is not UTF-8, most often one saved as UTF-16
                # (what PowerShell 5.1's `>` and Out-File write). It stopped the broker as it
                # should, but with the raw codec error in place of this sentence.
                raise ValueError(not_utf8) from exc
            if any(ch < " " for ch in self.auth_token):
                # UTF-16LE with no BOM IS valid UTF-8, each ASCII character followed by a NUL, so
                # it decoded, kept its NULs through strip(), and the broker started gated on a
                # token nobody can send. A real token holds no control character: the rule
                # _decode_token applies to the CLI token. Refused, never decoded as UTF-16 the way
                # that one is: a wrong guess here is a broker no client can reach.
                raise ValueError(not_utf8)
        return self

    # --- Claude CLI backend helpers -----------------------------------------

    def cli_owner_set(self) -> set[str]:
        """Usernames allowed to reach the CLI, stripped and matched EXACTLY, case included.

        This used to be lowercased on both sides, and that made it a licence hole: gateway
        usernames were unique case-sensitively, so an admin could create `Owner-One` beside
        `owner-one` and the second account passed this check and spent the owner's
        subscription. The gateway now keeps usernames unique case-insensitively and forwards
        the stored spelling, so one person has exactly one spelling here; write it as the
        account is stored. A miss shows as `not_owner` in `/v1/status` refusals.
        """
        return {o.strip() for o in self.cli_owners.split(",") if o.strip()}

    def image_embed_model_list(self) -> list[str]:
        """The allowed image embedders, in order; the first is the default."""
        return [m.strip() for m in self.image_embed_models.split(",") if m.strip()]

    def cli_role_set(self) -> set[str]:
        """The BROKER_CLI_ROLES env set, without the leading @. Only the seed: what is
        eligible right now is `cli_effective`, which every reader must use."""
        return {r.strip().lstrip("@").lower() for r in self.cli_roles.split(",") if r.strip()}

    def cli_model_list(self) -> list[str]:
        """BROKER_CLI_MODELS: the Claude models a role may be given, in order, each id once."""
        out: list[str] = []
        for m in self.cli_models.split(","):
            m = m.strip()
            if m and m not in out:
                out.append(m)
        return out

    def cli_role_model(self, role: str, models: dict[str, str]) -> str:
        """The `--model` a CLI call on `role` passes: the model the switch file gives it
        (``models``, from cli_effective), else BROKER_CLI_MODEL, else "" for none, which leaves
        the CLI's own default."""
        return models.get(role) or (self.cli_model or "").strip()

    def _parse_cli_roles(self, raw: str, known: dict[str, str] | None = None) -> _SwitchFile:
        """One parse of the text of BROKER_CLI_ROLES_FILE: ``switches``, ``problem``,
        ``ignored``, and each role's Claude model (``models``, and ``refused``).

        Strict about SHAPE, because being lenient there fails open: anything but a JSON object
        whose every value is a switch makes the whole file invalid (``problem``). A switch is JSON
        true or false, or an object that holds one as "enabled" beside an optional "model" (an id,
        or null for none): ``{"enabled": true, "model": "claude-haiku-5-5"}``. An object with any
        other key, without "enabled", or with a "model" that is not a string or null is not one.
        Coercing values would read the string "false" as truthy and switch a role ON. That
        includes a parse the decoder cannot finish at all: a RecursionError from nesting deeper
        than its stack is a ValueError's cousin, not a JSONDecodeError, and it used to escape
        cli_decision (an owner's call got a 500, not a fallback) and cli_state (/v1/status 500).

        A role's MODEL is held to BROKER_CLI_MODELS. One the list allows is in ``models``; one it
        does not is left out and named in ``refused``, which cli_effective reports as a note
        WITHOUT a blocker. The role's switch stands either way and its calls use the default
        model, so a model id nobody allowed never reaches `--model`.

        A key that starts with `_` is an ANNOTATION, the convention roles.json already uses
        (`"_comment": "switched on for the pilot"`). It is skipped whatever its value, before
        the value check, so a comment cannot fail the file closed, and it is not named in
        ``ignored``: nobody wrote it by mistake. No role name starts with `_` (_ROLE_NAME), so
        skipping one can never switch a role on or off. Every other key is held to the
        true-or-false rule above.

        Lenient about NAMES, because a key no role answers to cannot route a call either way. A
        key that is not a role roles() knows (a role renamed or removed in roles.json, a typo)
        is left out of ``switches`` and named in ``ignored``, which cli_state reports in
        `roles_problem` WITHOUT a blocker. Failing the whole file closed over one, as this used
        to, turned Claude off for every role after an unrelated roles.json edit, and the Rails
        tab could not repair it, because the switch route refuses an invalid file. The next
        switch writes the file without them.

        ``known`` is the role map to judge names against, roles() when omitted. set_cli_role
        passes the one it already read, so the file is judged and rewritten against one read of
        roles.json.
        """
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            return _bad_switch_file(f"not valid JSON ({exc.msg} at line {exc.lineno})")
        except (ValueError, RecursionError) as exc:
            return _bad_switch_file(f"not valid JSON ({type(exc).__name__})")
        if not isinstance(data, dict):
            return _bad_switch_file("must be a JSON object of role name to true or false")
        known = self.roles() if known is None else known
        allowed = set(self.cli_model_list())
        out: dict[str, bool] = {}
        ignored: list[str] = []
        models: dict[str, str] = {}
        refused: list[tuple[str, str]] = []
        for key, value in data.items():
            if str(key).startswith("_"):
                continue
            model: Any = None
            if isinstance(value, dict):
                shape = _switch_object_problem(value)
                if shape:
                    return _bad_switch_file(f"{key!r} {shape}")
                value, model = value["enabled"], value.get("model")
            if not isinstance(value, bool):
                return _bad_switch_file(f"{key!r} must be true or false, not {_shown(value)}")
            if _ROLE_NAME.fullmatch(str(key)) and key in known:
                out[str(key)] = value
                if model is not None and model in allowed:
                    models[str(key)] = model
                elif model is not None:
                    refused.append((str(key), model))
            else:
                ignored.append(str(key))
        return _SwitchFile(out, "", sorted(ignored), models, refused)

    def cli_effective(self, roles_read: tuple[dict[str, str], str] | None = None) -> CliRoles:
        """``(roles, source, problem, note, models)``: the role names eligible for the CLI RIGHT
        NOW, and the Claude model the switch file gives each role it names (see CliRoles).

        ``roles_read`` is a `roles_read()` answer the caller already holds (cli_state reads it
        for `roles_problem`), so the switch file is judged against that one read of roles.json
        instead of two more. None reads it here, once, only when the file exists: the names are
        judged against it and the note's word on roles.json comes from the same read.

        The ONE answer to "which roles may use the CLI". `Broker.cli_decision` (which routes the
        call) and `Broker.cli_state` (which /v1/status reports, and which every rail's chip reads
        through `cli_serves`) both read this, so a switched-off role leaves the chips in the same
        poll that stops it being routed. A second list that one
        reader honoured and the other did not would paint a chip "Claude" for a call the broker
        refuses.

          * BROKER_CLI_ROLES_FILE unset, or set to a file that does not exist: the env set,
            source "env". Today's behaviour, unchanged.
          * the file present and valid: the roles it switches ON, source "file". AUTHORITATIVE
            both ways: a role it does not list is not eligible, whatever the env says.
          * the file present and unreadable or malformed: NO roles, source "file", and the
            reason as ``problem``. Fail closed. Falling back to the env set would silently
            re-enable a role a super-admin switched off, and `cli_state` names the problem as a
            blocker.
          * the file present and valid but naming keys no role here answers to: the roles it
            switches ON, source "file", and those keys as ``note``. Not a blocker: they cannot
            route a call either way (see _parse_cli_roles). While roles.json cannot be read,
            a role only it defines is one of those keys, and the note says so instead: the
            switch is kept, because set_cli_role refuses an ON until roles.json is fixed and
            carries every key forward on an OFF.
          * the file present and valid but giving a role a model BROKER_CLI_MODELS does not
            allow: the switch stands, the model is left out of ``models``, and ``note`` names
            it. Not a blocker: the role's calls use the default model.

        A file that is set but ABSENT is the documented reset: deleting it hands the decision
        back to BROKER_CLI_ROLES, and `cli_state` reports that as `roles_file_set` true with
        `roles_source` "env", which the Rails tab shows.

        ``models`` holds only what the file says, and nothing with no file. The model a call
        passes is cli_role_model's answer, which falls back to BROKER_CLI_MODEL.
        """
        path = (self.cli_roles_file or "").strip()
        if not path:
            return CliRoles(self.cli_role_set(), "env", "", "", {})
        try:
            raw = Path(path).read_text(encoding="utf-8-sig")
        except FileNotFoundError:
            return CliRoles(self.cli_role_set(), "env", "", "", {})
        except OSError as exc:
            return CliRoles(set(), "file",
                            f"BROKER_CLI_ROLES_FILE could not be read ({exc.strerror})", "", {})
        except UnicodeDecodeError:
            return CliRoles(set(), "file", "BROKER_CLI_ROLES_FILE is not UTF-8 text", "", {})
        known, overlay_problem = roles_read if roles_read is not None else self.roles_read()
        parsed = self._parse_cli_roles(raw, known)
        switches, problem, ignored = parsed.switches, parsed.problem, parsed.ignored
        if problem:
            return CliRoles(set(), "file", f"BROKER_CLI_ROLES_FILE is invalid: {problem}", "", {})
        notes: list[str] = []
        if ignored:
            names = ", ".join(repr(k[:40]) for k in ignored[:8]) + (" ..." if len(ignored) > 8
                                                                    else "")
            # From the read the names were judged against, never a second one.
            if overlay_problem:
                notes.append(f"BROKER_CLI_ROLES_FILE names {names}, which this broker cannot "
                             f"see while roles.json cannot be read. Kept: a switch made "
                             f"meanwhile carries them forward.")
            else:
                notes.append(f"BROKER_CLI_ROLES_FILE names {names}, which this broker has no "
                             f"role for. Ignored, and the next switch drops them.")
        if parsed.refused:
            gives = ", ".join(f"{r!r} the model {m[:60]!r}" for r, m in parsed.refused[:8]) + (
                " ..." if len(parsed.refused) > 8 else "")
            allowed = ", ".join(self.cli_model_list()) or "none"
            notes.append(f"BROKER_CLI_ROLES_FILE gives {gives}, which BROKER_CLI_MODELS "
                         f"({allowed}) does not allow. Ignored: those calls use the default "
                         f"model, and the next switch drops it.")
        note = " ".join(notes)
        return CliRoles({r for r, on in switches.items() if on}, "file", "", note, parsed.models)

    def set_cli_role(self, role: str, enabled: bool | None, model: Any = KEEP_MODEL
                     ) -> dict[str, bool]:
        """Switch ONE role's CLI eligibility on or off, choose its Claude model, or both, in
        BROKER_CLI_ROLES_FILE. ``enabled`` None leaves the switch as it is. ``model`` is an id
        BROKER_CLI_MODELS allows, None to clear the role's own (its calls then use
        BROKER_CLI_MODEL, else the CLI's own default), or KEEP_MODEL, the default, to leave it.
        Returns the switches as written (annotations and models are kept in the file, not
        returned). Hot: the next call and the next /v1/status read it.

        Per role, never a full-set write: the gateway's model toggle learned that a failed read
        followed by a full-set write silently re-enables everything. Read, change one key and
        write back under a lock, through a temp file and an atomic replace, so a hot read never
        sees half a file and two switches at once cannot drop each other's change.

        A role with a model is written as ``{"enabled": <bool>, "model": "<id>"}`` and one
        without as a bare true or false, so a file nobody gave a model keeps the format it
        always had. Choosing a model leaves the switch as it is, which for a role the file does
        not name is off: the file decides alone once it exists.

        The FIRST write seeds the file from the current effective set, which with no file is
        the env set: switching one role on must not drop the roles BROKER_CLI_ROLES already
        had. An env entry that roles() does not know is left out of the seed: written into the
        file it would only be ignored and noted, and it could never be served locally.

        A key the file holds that roles() does not know is DROPPED by this write: it could not
        route a call, and keeping it would carry the note `cli_state` reports for ever. So is a
        model BROKER_CLI_MODELS does not allow (its role keeps its switch, as a bare one). An
        annotation (a key starting with `_`) is KEPT, value and all: it was written on purpose.

        While roles.json cannot be read (roles_read()'s problem), roles() falls back to
        DEFAULT_ROLES, so a role only roles.json defines looks unknown, and the rule above would
        drop its switch: one switch of any other role, made while roles.json was saved as UTF-16
        by PowerShell 5.1, silently lost every overlay-only role's ON for good. So meanwhile:
          * switching a role ON, or choosing or clearing a model, is refused, with the reason,
            until the file is fixed;
          * switching a role OFF still works, because OFF is the kill switch (an owner who has
            just pasted something sensitive wants it now, not after a roles.json repair). That
            write changes the one switch and carries EVERY key forward unchanged, known or not,
            its own model and every other role's included, so nothing is dropped; with no file
            yet it seeds from every env entry, for the same reason. The role must be a built-in
            one or a key the file (or that seed) already holds: any other name is not on
            anywhere, so there is nothing to switch off.

        Raises ValueError for a role this broker does not know, a model BROKER_CLI_MODELS does
        not allow, or a call that changes nothing (the caller's mistakes), and
        CliRolesFileError when roles.json cannot be read and the switch is ON or a model is
        chosen, or the switch file is not set, is invalid, or cannot be written. An invalid file
        is REFUSED rather than replaced: re-seeding it from the env set would switch back on
        whatever the broken file had switched off. The refusal says so, and says what deleting
        the file does, because that is the one way back the tab cannot offer.
        """
        role = role.strip()
        if not _ROLE_NAME.fullmatch(role):
            raise ValueError(f"invalid role name {role!r}")
        choose = model is not KEEP_MODEL
        if enabled is None and not choose:
            raise ValueError("nothing to change: give the switch, a model, or both")
        if choose and model is not None:
            allowed = self.cli_model_list()
            model = model.strip() if isinstance(model, str) else model
            if model not in allowed:
                raise ValueError(
                    f"{_shown(model)} is not a Claude model this broker allows "
                    f"(BROKER_CLI_MODELS: {', '.join(allowed) or 'none'})")
        # ONE read of roles.json, judged here and used for every name below, so a file that
        # breaks after this check cannot make the write drop a role it defines.
        overlay, overlay_problem = self._read_overlay()
        if overlay_problem and (enabled or choose):
            raise CliRolesFileError(
                f"{overlay_problem}, so no Claude switch can be turned on and no Claude model "
                f"chosen: this broker cannot see the roles that file defines. Fix it and switch "
                f"again. Switching a role off still works.")
        known = {**DEFAULT_ROLES, **overlay}
        if role not in known and not overlay_problem:
            raise ValueError(f"unknown role {role!r}")
        path = (self.cli_roles_file or "").strip()
        if not path:
            raise CliRolesFileError(
                "BROKER_CLI_ROLES_FILE is not set, so the eligible roles come from "
                "BROKER_CLI_ROLES and cannot be switched here. Set it on the broker (one "
                "restart) and every switch after that is live.")
        p = Path(path)
        notes: dict[str, Any] = {}
        models: dict[str, str] = {}
        with _CLI_ROLES_LOCK:
            try:
                raw = p.read_text(encoding="utf-8-sig")
            except FileNotFoundError:
                # While roles.json cannot be read, `known` is the defaults alone, so the seed
                # keeps every env entry that could be a role: an overlay-only one among them is
                # switched on by the env today and must still be after this OFF.
                current = {r: True for r in sorted(self.cli_role_set())
                           if (_ROLE_NAME.fullmatch(r) if overlay_problem else r in known)}
            except OSError as exc:
                raise CliRolesFileError(
                    f"BROKER_CLI_ROLES_FILE could not be read ({exc.strerror})") from exc
            except UnicodeDecodeError as exc:
                raise CliRolesFileError(
                    f"BROKER_CLI_ROLES_FILE is not UTF-8 text, so no role is eligible. "
                    f"{_CLI_ROLES_RESET}") from exc
            else:
                parsed = self._parse_cli_roles(raw, known)
                current, problem, models = parsed.switches, parsed.problem, parsed.models
                if problem:
                    raise CliRolesFileError(
                        f"BROKER_CLI_ROLES_FILE is invalid ({problem}), so no role is "
                        f"eligible. {_CLI_ROLES_RESET}")
                # Parsed once already, so this cannot fail.
                notes = {str(k): v for k, v in json.loads(raw).items() if str(k).startswith("_")}
                if overlay_problem:
                    # Every key carried forward unchanged, known or not. Judged against the
                    # defaults alone, the rule above would drop every overlay-only role's switch.
                    # _parse_cli_roles has already held every one of them to the switch shape.
                    current = {str(k): v for k, v in json.loads(raw).items()
                               if not str(k).startswith("_")}
            if overlay_problem and role not in known and role not in current:
                raise ValueError(
                    f"unknown role {role!r}: {overlay_problem}, so only a built-in role or one "
                    f"the switch file already names can be switched off meanwhile")
            if overlay_problem:
                # An OFF, the one change allowed meanwhile: its switch, and nothing else, so a
                # role written as an object keeps its model and every other key is as it was.
                held = current.get(role)
                current[role] = {**held, "enabled": False} if isinstance(held, dict) else False
                written = current
            else:
                if enabled is not None:
                    current[role] = bool(enabled)
                else:
                    current.setdefault(role, False)
                if choose and model is None:
                    models.pop(role, None)
                elif choose:
                    models[role] = model
                written = {r: ({"enabled": on, "model": models[r]} if r in models else on)
                           for r, on in current.items()}
            try:
                body = json.dumps({**dict(sorted(notes.items())), **dict(sorted(written.items()))},
                                  indent=2) + "\n"
            except (ValueError, RecursionError) as exc:
                raise CliRolesFileError(
                    f"BROKER_CLI_ROLES_FILE holds an annotation that cannot be written back "
                    f"({type(exc).__name__}); remove it and switch again.") from exc
            tmp = p.with_name(p.name + ".tmp")
            try:
                tmp.write_text(body, encoding="utf-8")
                replace_with_retry(tmp, p)
            except OSError as exc:
                # The temp file goes with the refusal, as _write_settings_file's does.
                with contextlib.suppress(OSError):
                    tmp.unlink(missing_ok=True)
                raise CliRolesFileError(
                    f"BROKER_CLI_ROLES_FILE could not be written ({exc.strerror})") from exc
        return {r: (v.get("enabled") is True if isinstance(v, dict) else v is True)
                for r, v in written.items()}

    def cli_token(self) -> tuple[str, str]:
        """``(token, problem)``. Hot-read, like roles.json, so rotating the token does not
        need a broker restart.

        An unreadable token FILE returns ``("", "...")`` rather than raising, and that is the
        opposite of what `_resolve_auth_token_file` does three methods up. The asymmetry is
        deliberate and worth stating, because the two look like the same code: an empty
        BROKER_AUTH_TOKEN means the control plane is OPEN, so a typo there must stop the
        broker starting. An empty CLI token means this backend is OFF, which is the safe
        state -- refusing to start would take the whole GPU layer down over an optional
        feature. The cost of degrading quietly is paid by returning the reason, which
        /v1/status reports; see the "must SAY so" rule in `cli_state`.
        """
        path = (self.cli_oauth_token_file or "").strip()
        if path:
            try:
                raw = Path(path).read_bytes()
            except OSError as exc:
                return "", f"BROKER_CLI_OAUTH_TOKEN_FILE could not be read ({exc.strerror})"
            token = _decode_token(raw)
            if token is None:
                return "", ("BROKER_CLI_OAUTH_TOKEN_FILE is not readable text (try UTF-8; "
                            "PowerShell's > and Out-File write UTF-16)")
            return token, ""
        return (self.cli_oauth_token or "").strip(), ""

    def voice_engines_root(self) -> Path | None:
        v = (self.voice_engines_dir or "").strip()
        return Path(v) if v else None

    def voice_registry_path(self) -> Path | None:
        """The voice catalog. Explicit setting wins; otherwise it sits inside the engines root."""
        if self.voice_registry.strip():
            return Path(self.voice_registry.strip())
        root = self.voice_engines_root()
        return None if root is None else root / "voices" / "registry.json"

    def embed_hints(self) -> list[str]:
        return [h.strip().lower() for h in self.embed_name_hints.split(",") if h.strip()]

    def voice_engines(self) -> dict[str, dict[str, str]]:
        """engine -> {python, entry, cwd}: the configured table overlaid by the
        voice_engines.json file (if present). Read fresh each call, like roles().

        An overlay that cannot be read, is not UTF-8 (UnicodeDecodeError, a ValueError) or is
        nested deeper than the parser's stack (RecursionError) is ignored like a malformed one,
        for the reason roles.json is (overlay_roles): each used to raise through every voice
        route."""
        merged = {k: dict(v) for k, v in
                  voice_engine_table(self.voice_engines_root(), self.tts_python
                                     or self.media_python).items()}
        path = Path(self.voice_engines_file) if self.voice_engines_file else _BROKER_DIR / "voice_engines.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(data, dict):
                for k, v in data.items():
                    if isinstance(v, dict):
                        merged[str(k)] = {str(kk): str(vv) for kk, vv in v.items()}
        except (OSError, ValueError, RecursionError):
            pass
        return merged

    def upstreams_path(self) -> Path:
        """The upstreams.json registry (BROKER_UPSTREAMS_FILE or services/broker/upstreams.json)."""
        return Path(self.upstreams_file) if self.upstreams_file else _BROKER_DIR / "upstreams.json"

    def upstreams(self) -> dict[str, dict[str, str]]:
        """Named remote brokers this one may delegate to, `{name: {"url":..., "token":...}}`.

        A FILE rather than an env var, for the same two reasons roles.json is one: it is hot-read,
        so adding a box takes effect on the next request instead of on a restart, and it keeps a
        remote broker's bearer token out of compose — where the platform-wide BROKER_AUTH_TOKEN
        already lives and would otherwise be joined by one secret per remote.

        `local` is implicit, always present, and may not be redefined here: it is this broker's own
        Ollama, and a registry entry claiming that name would make "local" mean two things
        depending on which code path asked.

        Unreadable or malformed => {} rather than an exception. Delegation is an enhancement; a
        broken registry must degrade to local-only, not take the GPU layer down with it. That
        includes a file that is not UTF-8 or is nested too deeply to parse, which used to raise:
        with a role delegated, audit_roles reads this at startup, so the broker did not start.
        """
        try:
            data = json.loads(self.upstreams_path().read_text(encoding="utf-8-sig"))
        except (OSError, ValueError, RecursionError):
            return {}
        if not isinstance(data, dict):
            return {}
        out: dict[str, dict[str, str]] = {}
        for name, spec in data.items():
            if not _ROLE_NAME.fullmatch(str(name)) or str(name) == "local":
                continue
            if not isinstance(spec, dict) or not str(spec.get("url", "")).strip():
                continue
            out[str(name)] = {"url": str(spec["url"]).rstrip("/"),
                              "token": str(spec.get("token", ""))}
        return out

    def delegate_ref(self, value: str, registry: dict[str, dict[str, str]] | None = None
                     ) -> tuple[str | None, str]:
        """Split a role value into `(upstream_name, model_ref)`. `registry` is an `upstreams()`
        answer the caller already holds (a sweep reads it once for every role); None reads it.

        The separator is a DOUBLE colon — `offsite::mistral-small3*:24b` — and that is the whole
        reason this is unambiguous. A single colon is already the model/tag separator, so
        `gemma3:4b` would make an upstream named `gemma3` and a model named `4b`; worse, it would
        do so only on the box where somebody happened to name an upstream after a model family.
        No Ollama model name contains `::`, so the two syntaxes cannot collide by accident.

        An unknown upstream name returns `(None, value)` — the whole string is then resolved
        locally, fails to match an installed model, and surfaces as a MISSING chip. That is the
        honest outcome: silently falling back to a local model of the same name would run the
        wrong box's GPU and report success.
        """
        if "::" not in value:
            return None, value
        name, _, rest = value.partition("::")
        registry = self.upstreams() if registry is None else registry
        return (name, rest) if name in registry else (None, value)

    def roles_path(self) -> Path:
        """The roles.json overlay file (BROKER_ROLES_FILE or services/broker/roles.json)."""
        return Path(self.roles_file) if self.roles_file else _BROKER_DIR / "roles.json"

    def roles(self) -> dict[str, str]:
        """The role->pattern map: DEFAULT_ROLES overlaid with roles.json (if present).
        Read fresh each call so editing roles.json takes effect without a broker restart.

        Keys that are not legal role names are IGNORED rather than merged. roles.json is a
        hand-edited file, so it grows `_comment` / `_local_delta` annotations, and every one
        of them used to come back out as a role: listed by GET /v1/roles, offered in the admin
        picker, and audited at startup — one downstream map reported 18 roles for 16 real ones,
        with an annotation classified as an embedder because its prose contained the word.
        `set_role()` has always refused to CREATE such a name; reading them back in was the
        half that disagreed. Same pattern object for both, so the two cannot drift apart."""
        merged = dict(DEFAULT_ROLES)
        merged.update(self.overlay_roles())
        return merged

    def overlay_roles(self) -> dict[str, str]:
        """Only the roles THIS box set, without the DEFAULT_ROLES backstop underneath.

        roles() merges the two, which is right at resolve time and is exactly what hides a
        problem at audit time: a role absent from roles.json still resolves, to a default
        sized for the 24 GB card this repo was developed on. On a lean install that is a pin
        nobody reviewed, and the rail meets it as a model that will not load. Separating the
        two lets the startup audit say which of the two a bad role came from, which is the
        difference between "fix your roles.json" and "you never had one".

        utf-8-SIG, not utf-8, and that applies to every hand-editable JSON this class reads.
        These files are edited on a Windows box, and PowerShell's Set-Content / Out-File write
        a BOM by default. Read as plain utf-8 the BOM makes json.loads raise, the except below
        swallows it, and the whole overlay silently disappears — every role quietly reverting
        to the 24 GB DEFAULT_ROLES with nothing said. Found exactly that way.

        A file that is not UTF-8 at all (UnicodeDecodeError), or nested deeper than the parser's
        stack (RecursionError), degrades the same way a malformed one does. Both used to raise:
        through every resolve, and through cli_state, which reads roles() to judge the switch
        file and is called OUTSIDE status()'s never-raise guard, so /v1/status answered 500.

        Degrading must not be SILENT, though: roles_read() says why, cli_state reports it in
        `roles_problem` (the Rails tab shows it). While it holds, every role only this file
        defines looks unknown, so set_cli_role refuses an ON (an OFF carries every key forward)
        and set_role refuses a repoint, which would rewrite the file from nothing."""
        return self._read_overlay()[0]

    def roles_read(self) -> tuple[dict[str, str], str]:
        """``(roles(), problem)`` from ONE read of roles.json, for a caller that needs both
        (cli_state, which hands the pair to cli_effective). `problem` is why roles.json cannot be
        read right now, or "" when it can (an absent file is fine: the defaults are the whole
        map); it names the file and the reason, never its path."""
        overlay, problem = self._read_overlay()
        merged = dict(DEFAULT_ROLES)
        merged.update(overlay)
        return merged, problem

    def _roles_label(self) -> str:
        path = self.roles_path()
        return "roles.json" if path.name == "roles.json" else f"roles.json ({path.name})"

    def _read_overlay(self) -> tuple[dict[str, str], str]:
        """``(overlay, problem)``: roles.json's legal role keys, and why it could not be read."""
        data, problem = self._load_roles_json()
        if problem:
            return {}, problem
        try:
            return {str(k): str(v) for k, v in data.items() if _ROLE_NAME.fullmatch(str(k))}, ""
        except RecursionError:
            return {}, f"{self._roles_label()} holds a value nested too deeply to read"

    def _load_roles_json(self) -> tuple[dict[str, Any], str]:
        """``(data, problem)``: roles.json as parsed, EVERY key (annotations too), and why it could
        not be read. An absent file is ``({}, "")``: the defaults are the whole map. ONE read,
        shared by _read_overlay and set_role, so both name a problem in the same words."""
        path = self.roles_path()
        label = self._roles_label()
        try:
            raw = path.read_text(encoding="utf-8-sig")
        except FileNotFoundError:
            return {}, ""
        except UnicodeDecodeError:
            return {}, (f"{label} is not UTF-8 text (Windows PowerShell 5.1's > and Out-File "
                        f"write UTF-16; save it as UTF-8)")
        except OSError as exc:
            return {}, f"{label} could not be read ({exc.strerror})"
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            return {}, f"{label} is not valid JSON ({exc.msg} at line {exc.lineno})"
        except (ValueError, RecursionError) as exc:
            return {}, f"{label} is not valid JSON ({type(exc).__name__})"
        if not isinstance(data, dict):
            return {}, f"{label} must be a JSON object of role name to model pattern"
        return data, ""

    def set_role(self, role: str, pattern: str) -> None:
        """Persist a single role->pattern mapping into the roles.json overlay (creating
        the file if absent). Hot: ``roles()`` re-reads the file each resolve, so the change
        takes effect on the next request with no broker restart. Only KNOWN roles may be set
        (a typo can't create a dead role), and the value is length/charset-guarded.

        Refused (RolesFileError, the route's 409) while roles.json cannot be read. This used to
        start again from an empty dict when the file was not valid JSON or not an object, so one
        repoint in the Rails tab wiped every mapping the file held, overlay-only roles included.
        roles.json then read clean again, the next Claude switch was accepted, and it dropped
        the ON switch of every role only roles.json had defined, for good. A file that was not
        UTF-8 or nested too deep raised a 500 instead. Judged and rewritten from ONE read, so
        there is no gap between a check and a second read for the file to break in.

        Refused too (SettingsWriteError, also a 409) when the file cannot be written, a held
        file outlasting the retries included; that was a 500 and a leftover roles.json.tmp."""
        role = role.strip()
        pattern = pattern.strip()
        if not _ROLE_NAME.fullmatch(role):
            raise ValueError(f"invalid role name {role!r}")
        if not pattern or len(pattern) > 128:
            raise ValueError("model pattern must be 1..128 characters")
        current, problem = self._load_roles_json()
        if problem:
            raise RolesFileError(
                f"{problem}, so no role can be repointed: written now, the file would lose every "
                f"mapping it holds. Fix it and repoint again.")
        if role not in DEFAULT_ROLES and role not in current:
            raise ValueError(f"unknown role {role!r}")
        # Refuse a delegation to a box that is not registered. Saved unchecked it would resolve
        # locally instead (delegate_ref falls back on an unknown name), so the panel would report
        # the role as pointing off-site while every call ran on this card.
        if "::" in pattern:
            name = pattern.partition("::")[0]
            if name not in self.upstreams():
                known = sorted(self.upstreams()) or ["(none registered)"]
                raise ValueError(f"unknown upstream {name!r}; registered: {', '.join(known)}")
        current[str(role)] = str(pattern)
        _write_settings_file(self.roles_path(), json.dumps(current, indent=2) + "\n")

    # --- named access tokens --------------------------------------------------
    #
    # WHY THESE EXIST alongside BROKER_AUTH_TOKEN. The env token is ONE shared secret: to stop
    # trusting a single machine you have to rotate it and then re-deploy every rail that sends
    # it. A named token is per-host and revocable on its own, so "that workstation is gone" is
    # one click instead of a rotation.
    #
    # WHY A FILE beside roles.json and disabled.json, rather than the gateway's database. The
    # broker runs NATIVE under NSSM and the gateway runs in a container, so the broker cannot
    # read the gateway's SQLite at all. This is the same seam roles/disabled already cross, and
    # it buys the same property: hot-read, so a revoke takes effect on the very next request
    # with no restart.
    #
    # WHAT IS STORED IS A HASH. The plaintext is returned exactly once, at creation, and never
    # again -- a credential list you can screenshot is not a credential list.

    def tokens_path(self) -> Path:
        """The tokens.json store (BROKER_TOKENS_FILE or services/broker/tokens.json)."""
        return Path(self.tokens_file) if self.tokens_file else _BROKER_DIR / "tokens.json"

    def tokens_configured(self) -> bool:
        """Whether a token store EXISTS, regardless of whether it holds any rows.

        This is the enforcement switch, not `tokens()` being non-empty, and the difference is a
        footgun worth closing: revoke the last named token on a box with no BROKER_AUTH_TOKEN
        and an emptiness test would silently reopen the broker to anyone. "I revoked everything"
        must mean "nobody is authorised", not "authorisation is off". Creating the file is the
        operator opting in; deleting it is opting out.
        """
        return self.tokens_path().is_file()

    def tokens(self) -> list[dict[str, Any]]:
        """Live named tokens, hot-read. Each is {id, label, scope, prefix, hash, created}.

        Returns [] on a missing or malformed file rather than raising: this is read on the auth
        path, and a corrupt store must fail CLOSED (nobody authenticates) rather than take the
        broker down. Malformed includes not UTF-8 (UnicodeDecodeError, a ValueError: what
        Windows PowerShell 5.1's `>` writes) and nested too deeply to parse (RecursionError).
        Both used to raise out of require_token, so every request was a 500, /healthz included.

        PARSED ONCE PER VERSION OF THE FILE, not once per request. require_token reads this for
        every named-token call, and each read held the file open in a threadpool thread: one
        of the openers a writer's atomic replace has to wait out (app/fsutil.py), so the auth
        path itself turned a mint or a revoke under load into a refusal. Now a stat decides
        (`_file_signature`: an atomic replace is a new file, so a new id, as well as a new write
        time), and the file is opened only when it has changed. This broker's own writes drop
        the cache (`_write_tokens`), so a revoke lands on the next request even on a file
        system whose ids and times do not move. A read that FAILS is not cached: a moment's
        sharing violation must not lock every named token out until the file next changes.
        Each call returns fresh copies, so a caller that appends (add_token) changes nothing
        the next request reads.
        """
        path = self.tokens_path()
        try:
            sig = _file_signature(path)
        except OSError:
            self._tokens_seen = None
            return []
        seen = self._tokens_seen
        if seen is None or seen[0] != sig:
            try:
                raw = path.read_bytes()
            except OSError:
                return []
            seen = (sig, _parse_tokens(raw))
            self._tokens_seen = seen
        return [dict(r) for r in seen[1]]

    def _write_tokens(self, rows: list[dict[str, Any]]) -> None:
        try:
            _write_settings_file(self.tokens_path(), json.dumps(rows, indent=2) + "\n")
        finally:
            # Written or refused, the next read goes to the file (see tokens()).
            self._tokens_seen = None

    def add_token(self, label: str, scope: str) -> tuple[str, dict[str, Any]]:
        """Mint a token. Returns (plaintext, stored-row). The plaintext is never persisted.

        The label is the whole point of the feature -- it is how you find the right row to
        revoke months later -- so an empty one is refused rather than defaulted. A store that
        cannot be written raises SettingsWriteError (the route's 409) and mints nothing.
        """
        label = label.strip()
        if not label or len(label) > 64:
            raise ValueError("a label of 1..64 characters is required")
        if not re.fullmatch(r"[\w .:/-]+", label):
            raise ValueError("label may contain letters, digits, space and . : / - _ only")
        if scope not in TOKEN_SCOPES:
            raise ValueError(f"scope must be one of {sorted(TOKEN_SCOPES)}")
        plain = "bt_" + secrets.token_urlsafe(32)
        row = {
            "id": secrets.token_hex(8),
            "label": label,
            "scope": scope,
            # For identification in the UI only. Short enough to be useless as a secret and
            # long enough to tell two tokens apart.
            "prefix": plain[:11],
            "hash": hashlib.sha256(plain.encode("utf-8")).hexdigest(),
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        with _TOKENS_LOCK:
            rows = self.tokens()
            if any(r.get("label") == label for r in rows):
                raise ValueError(f"a token labelled {label!r} already exists")
            rows.append(row)
            self._write_tokens(rows)
        return plain, row

    def revoke_token(self, token_id: str) -> bool:
        """Drop a token by id. Returns whether it was found. Effective immediately (hot-read).
        A store that cannot be written raises SettingsWriteError (the route's 409): the token
        is NOT revoked, and the refusal says so rather than a 500 saying nothing."""
        with _TOKENS_LOCK:
            rows = self.tokens()
            keep = [r for r in rows if r.get("id") != token_id]
            if len(keep) == len(rows):
                return False
            self._write_tokens(keep)
        return True

    def disabled_path(self) -> Path:
        """The disabled.json overlay file (BROKER_DISABLED_FILE or services/broker/disabled.json)."""
        return Path(self.disabled_file) if self.disabled_file else _BROKER_DIR / "disabled.json"

    def disabled(self) -> set[str]:
        """Admin-disabled model names (hot-read from disabled.json each call). Empty if absent,
        or if the file cannot be parsed: not JSON, not UTF-8, or nested too deeply. The last two
        used to raise, which failed every `@role` chat (resolve_ref reads this)."""
        try:
            data = json.loads(self.disabled_path().read_text(encoding="utf-8-sig"))
            if isinstance(data, list):
                return {str(n) for n in data}
        except (OSError, ValueError, RecursionError):
            pass
        return set()

    def set_disabled(self, names: list[str]) -> None:
        """Persist the full disabled-name set (atomic write; hot on the next read). Names are
        length/charset-guarded so a garbage payload can't poison the file. A file that cannot
        be written raises SettingsWriteError (the route's 409)."""
        clean = sorted({n.strip() for n in names
                        if n and n.strip() and len(n) <= 128 and re.fullmatch(r"[\w.:/-]+", n.strip())})
        _write_settings_file(self.disabled_path(), json.dumps(clean, indent=2) + "\n")
