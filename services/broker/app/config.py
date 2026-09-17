"""Broker configuration (env-driven, 12-factor)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

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
    # OpenMAIC: writes whole courses — outline, slide bodies, quiz items — from a topic or an
    # uploaded document. Long-form instruction-following over documents, the same shape of job
    # as edu and co-worker-synthesis, so it gets the same model. Unlike the RAG rails it has no
    # embedder to co-reside with during generation; retrieval happens earlier, against @embed.
    "openmaic": "mistral-small3*:24b",
    # Present in roles.json but NOT here until now: on a box whose overlay is missing or
    # malformed, @ai-playground fell through to the literal string "ai-playground" and
    # reached Ollama as a model name -- a 404 wrapped in a 502, not a "no such role".
    "ai-playground": "nemotron-3-nano:4b",
    # Media (image) role — resolves to a media worker backend, NOT an Ollama model.
    "recipe-icon": "flux-schnell",             # recipe-book per-recipe icon image generator
    # ai-voice rail: text normalization before TTS runs on the default chat model.
}


# Voice-studio engines. Each voice's `engine` (from the voice registry) maps to the
# interpreter + CLI entry that synthesizes it, under a root the DEPLOYMENT supplies via
# BROKER_VOICE_ENGINES_DIR. Overlaid by services/broker/voice_engines.json if present.
# Each engine's CLI contract: <python> <entry> --voice <id> --text @<file> --out <wav>.
#
# The root is configuration, not a constant, because the broker must not name a rail. It
# used to resolve rails/ai-voice/native directly, which made one rail load-bearing for a
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

    # Where the broker listens.
    host: str = "127.0.0.1"
    port: int = 11500

    # Shared secret for the control plane. When set (BROKER_AUTH_TOKEN), every /v1/* route requires
    # a matching `Authorization: Bearer <token>` (or X-Broker-Token) header, so a rogue container or
    # LAN host on 0.0.0.0:11500 can't drive the GPU / repoint roles. Empty = open (dev / rollout).
    auth_token: str = ""

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
    # 2026-08-20 relocation this defaulted to rails/edu-suite/.venv, which had not existed
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
    # icons and bouquet vision depend on, and while this pointed into rails/edu-suite,
    # removing that one rail would have broken media for rails that never heard of it.
    media_core_src: str = str(_REPO_ROOT / "packages" / "edu-media-core" / "src")
    # XTTS reference voice clips (english_reference.wav / spanish_reference.wav), kept inside
    # the package so it stays self-contained.
    media_voices_dir: str = str(_REPO_ROOT / "packages" / "edu-media-core" / "voices")
    # Per-job timeout (seconds); a cold model load + a batch can be slow.
    media_timeout: float = 1200.0

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
        voice_engines.json file (if present). Read fresh each call, like roles()."""
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
        except (OSError, json.JSONDecodeError):
            pass
        return merged

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
        broken registry must degrade to local-only, not take the GPU layer down with it.
        """
        try:
            data = json.loads(self.upstreams_path().read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
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

    def delegate_ref(self, value: str) -> tuple[str | None, str]:
        """Split a role value into `(upstream_name, model_ref)`.

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
        return (name, rest) if name in self.upstreams() else (None, value)

    def overlay_roles(self) -> dict[str, str]:
        """Only the roles THIS box set, without the DEFAULT_ROLES backstop underneath.

        roles() merges the two, which is right at resolve time and is exactly what hides a
        problem at audit time: a role absent from roles.json still resolves, to a default
        sized for the 24 GB card this repo was developed on. On a lean install that is a pin
        nobody reviewed, and the rail meets it as a model that will not load. Separating the
        two lets the startup audit say which of the two a bad role came from, which is the
        difference between "fix your roles.json" and "you never had one"."""
        # utf-8-SIG, not utf-8. These are hand-edited files on a Windows box, and PowerShell's
        # Set-Content / Out-File write a BOM by default. Read as plain utf-8 the BOM makes
        # json.loads raise, the except below swallows it, and the whole overlay silently
        # disappears -- every role quietly reverting to the 24 GB DEFAULT_ROLES with nothing said.
        # Found exactly that way: a roles.json written by PowerShell, reporting the defaults back.
        try:
            data = json.loads(self.roles_path().read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(k): str(v) for k, v in data.items() if _ROLE_NAME.fullmatch(str(k))}

    def set_role(self, role: str, pattern: str) -> None:
        """Persist a single role->pattern mapping into the roles.json overlay (creating
        the file if absent). Hot: ``roles()`` re-reads the file each resolve, so the change
        takes effect on the next request with no broker restart. Only KNOWN roles may be set
        (a typo can't create a dead role), and the value is length/charset-guarded."""
        role = role.strip()
        pattern = pattern.strip()
        if not _ROLE_NAME.fullmatch(role):
            raise ValueError(f"invalid role name {role!r}")
        if not pattern or len(pattern) > 128:
            raise ValueError("model pattern must be 1..128 characters")
        if role not in self.roles():
            raise ValueError(f"unknown role {role!r}")
        # Refuse a delegation to a box that is not registered. Saved unchecked it would resolve
        # locally instead (delegate_ref falls back on an unknown name), so the panel would report
        # the role as pointing off-site while every call ran on this card.
        if "::" in pattern:
            name = pattern.partition("::")[0]
            if name not in self.upstreams():
                known = sorted(self.upstreams()) or ["(none registered)"]
                raise ValueError(f"unknown upstream {name!r}; registered: {', '.join(known)}")
        path = self.roles_path()
        try:
            current = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(current, dict):
                current = {}
        except (OSError, json.JSONDecodeError):
            current = {}
        current[str(role)] = str(pattern)
        # Write via a temp file + atomic replace so a concurrent hot-read never sees a
        # half-written file.
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)

    def disabled_path(self) -> Path:
        """The disabled.json overlay file (BROKER_DISABLED_FILE or services/broker/disabled.json)."""
        return Path(self.disabled_file) if self.disabled_file else _BROKER_DIR / "disabled.json"

    def disabled(self) -> set[str]:
        """Admin-disabled model names (hot-read from disabled.json each call). Empty if absent."""
        try:
            data = json.loads(self.disabled_path().read_text(encoding="utf-8-sig"))
            if isinstance(data, list):
                return {str(n) for n in data}
        except (OSError, json.JSONDecodeError):
            pass
        return set()

    def set_disabled(self, names: list[str]) -> None:
        """Persist the full disabled-name set (atomic write; hot on the next read). Names are
        length/charset-guarded so a garbage payload can't poison the file."""
        clean = sorted({n.strip() for n in names
                        if n and n.strip() and len(n) <= 128 and re.fullmatch(r"[\w.:/-]+", n.strip())})
        path = self.disabled_path()
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(clean, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
