"""Broker configuration (env-driven, 12-factor)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import ClassVar

from pydantic_settings import BaseSettings, SettingsConfigDict

_BROKER_DIR = Path(__file__).resolve().parent.parent  # services/broker/

# Model ROLES (classes): a rail asks the broker for "@<role>" (e.g. "@chat") instead of a
# specific model name. The broker expands the role to the pattern below, then resolves the
# newest installed match. To repoint a whole class of model (pull a newer one, or swap
# families), edit ONE entry here or in services/broker/roles.json (hot-read every request —
# no broker restart needed once this code is live).
DEFAULT_ROLES: dict[str, str] = {
    "chat": "mistral-small3*:24b",   # default instruction-follower (recipe-book, edu)
    "chat-fast": "gemma4*:12b",       # snappy / light (terminal-fun)
    "chat-large": "qwen3.6*:27b",    # higher-quality long-form (iep)
    "reasoning": "qwen3.6*:27b",   # deep reasoning
    "code": "qwen3.6*:27b",
    "vision": "gemma4*:26b",
    "embed": "bge-m3*",
    # --- per-rail roles (one role per rail model slot) -----------------------
    # Each rail's model env var points at its OWN role (e.g. RECIPE_BOOK_VISION_MODEL=@recipe-vision)
    # so the admin "Rails" settings can repoint a single rail without moving others that would
    # otherwise share a generic class. Seeded to the SAME model each slot resolves to today, so
    # introducing them is behaviour-neutral until an admin changes one. Edited live via
    # /v1/roles (roles.json overlay, hot-read) — no rail restart.
    "edu": "mistral-small3*:24b",             # edu-suite bilingual content
    "iep": "qwen3.6*:27b",                     # IEP Present Levels writer
    "recipe": "gemma4*:26b",                    # recipe-book culinary assistant
    "recipe-vision": "gemma4*:26b",            # recipe-book recipe-photo reader
    "terminal-fun": "gemma4*:12b",              # terminal-fun assistant
    # SMB Partner Enablement. Deliberately a SMALL generative model: this rail keeps its LLM
    # and the embedder resident together and adds a voice model on top, so a 3B-class model is
    # what fits alongside on an 8 GB card. See rails/smb-partner-enablement/MODELS.md.
    "smb-partner-rag": "llama3.2*:3b",
    # Gemini Enterprise CX. Also a co-resident pair (generative + @embed), but with no voice
    # model competing for the card, so this one can afford a larger generative model than
    # smb-partner-rag does. On an 8 GB box override it down to gemma3:4b in roles.json — see
    # rails/gemini-cx/MODELS.md for the arithmetic.
    "gemini-cx-rag": "gemma4*:12b",
    # Media (image) role — resolves to a media worker backend, NOT an Ollama model.
    "recipe-icon": "flux-schnell",             # recipe-book per-recipe icon image generator
    # NOTE: there is deliberately no voice ROLE. Voice is a platform capability served by
    # /v1/tts_light, and its default speaker lives in BROKER_KOKORO_VOICE below. A role called
    # @smb-partner-voice used to sit here resolving to "kokoro" — the only light TTS backend —
    # so it could never select anything, no rail ever read it, and the admin panel excludes TTS
    # by design. It looked like a control and was not one.
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
    media_enabled: bool = False
    # Interpreter with torch / diffusers / TTS + edu_media_core importable.
    media_python: str = ""
    # Interpreter for the VOICE ops (kokoro_tts, transcribe) when they must not share the
    # image/XTTS venv. Empty falls back to media_python.
    #
    # These need separating because their dependency floors collide: kokoro-onnx requires
    # numpy>=2.0.2, while simple-lama-inpainting (in the image stack) requires numpy<2.0.0.
    # Installing both into one venv silently moves numpy under every image path, which is the
    # kind of breakage that shows up as garbled output rather than an import error. Voice is
    # also torch-free, so its venv stays small and cannot disturb a CUDA install.
    media_python_voice: str = ""
    # edu_media_core source root, prepended to the worker's sys.path.
    media_core_src: str = ""
    # XTTS reference voice clips (english_reference.wav / spanish_reference.wav).
    media_voices_dir: str = ""
    # Kokoro ONNX model file (kokoro-v1.0.fp16-gpu.onnx for DirectML, etc.).
    kokoro_model_path: str = ""
    # Kokoro voices embedding file (voices-v1.0.bin).
    kokoro_voices_path: str = ""
    # Default Kokoro voice + language, applied when a caller does not name one. Deliberately
    # a BROKER setting rather than a frontend constant or a per-rail env var: voice is now a
    # platform capability, so changing it for everyone should not require rebuilding a rail.
    #
    # VOICE AND LANGUAGE MUST TRAVEL TOGETHER. Kokoro voice ids are language-scoped by their
    # prefix ('af_'/'am_' = American female/male, 'bf_'/'bm_' = British, 'ef_'/'em_' = Spanish),
    # and lang_code selects the phonemiser ('a'=en-us, 'b'=en-gb, 'e'=es, 'f'=fr, 'j'=ja).
    # A Spanish voice under lang_code 'a' is phonemised as English and comes out as noise, so
    # a caller overriding one should override both.
    kokoro_voice: str = "af_heart"
    kokoro_lang_code: str = "a"
    # faster-whisper STT. CPU/int8 by default: speech input must never evict the resident
    # RAG model, and a short question transcribes in ~1.7s on CPU anyway. Weights come from
    # the HuggingFace cache, so no path setting is needed — only the model id.
    #
    # MULTILINGUAL, NEVER A '.en' VARIANT. This defaulted to "small.en", which is English-only
    # and silently IGNORES the `language` parameter rather than erroring — so a Spanish
    # utterance came back as English-shaped nonsense with language reported as "en". Measured
    # on this box: "La reunion con el cliente es el martes por la manana" transcribed as
    # "La reu?a un con el cliente es el martes por la manana" under small.en, and verbatim
    # under small. Guarded by tests/test_whisper_model.py, which asserts the default is not a
    # '.en' build, because the failure is silent and plausible-looking.
    whisper_model: str = "small"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    # Per-job timeout (seconds); a cold model load + a batch can be slow.
    media_timeout: float = 1200.0

    # Media ops served by the VOICE interpreter when one is configured. Both are torch-free
    # and want numpy>=2; everything else in the media worker wants the image/XTTS venv.
    VOICE_OPS: ClassVar[frozenset[str]] = frozenset({"kokoro_tts", "transcribe"})

    def media_python_for(self, op: str) -> str:
        """The interpreter that should run `op`.

        Voice ops prefer media_python_voice and fall back to media_python, so a deployment that
        has only ever configured one interpreter keeps working unchanged. Routing per op rather
        than per call site means a future image/XTTS venv cannot quietly capture voice.
        """
        if op in self.VOICE_OPS and self.media_python_voice:
            return self.media_python_voice
        return self.media_python

    def embed_hints(self) -> list[str]:
        return [h.strip().lower() for h in self.embed_name_hints.split(",") if h.strip()]

    def roles_path(self) -> Path:
        """The roles.json overlay file (BROKER_ROLES_FILE or services/broker/roles.json)."""
        return Path(self.roles_file) if self.roles_file else _BROKER_DIR / "roles.json"

    def roles(self) -> dict[str, str]:
        """The role->pattern map: DEFAULT_ROLES overlaid with roles.json (if present).
        Read fresh each call so editing roles.json takes effect without a broker restart."""
        merged = dict(DEFAULT_ROLES)
        try:
            data = json.loads(self.roles_path().read_text(encoding="utf-8"))
            if isinstance(data, dict):
                merged.update({str(k): str(v) for k, v in data.items()})
        except (OSError, json.JSONDecodeError):
            pass
        return merged

    def set_role(self, role: str, pattern: str) -> None:
        """Persist a single role->pattern mapping into the roles.json overlay (creating
        the file if absent). Hot: ``roles()`` re-reads the file each resolve, so the change
        takes effect on the next request with no broker restart. Only KNOWN roles may be set
        (a typo can't create a dead role), and the value is length/charset-guarded."""
        role = role.strip()
        pattern = pattern.strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", role):
            raise ValueError(f"invalid role name {role!r}")
        if not pattern or len(pattern) > 128:
            raise ValueError("model pattern must be 1..128 characters")
        if role not in self.roles():
            raise ValueError(f"unknown role {role!r}")
        path = self.roles_path()
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
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
            data = json.loads(self.disabled_path().read_text(encoding="utf-8"))
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
