"""Runtime settings for the ai-playground rail.

Env-overridable so the same code runs standalone (broker on localhost, data under
``./data``) and in the container (broker via ``host.docker.internal``, data on a mounted
named volume at ``/srv/var``, seed corpora under ``/srv/seed``).
"""
from __future__ import annotations

import os
from pathlib import Path

# All mutable state (SQLite DB + uploaded docs) lives here — a mounted named volume in
# the container. Defaults to a repo-local ./data for standalone dev.
DATA_DIR = Path(os.environ.get(
    "AI_PLAYGROUND_DATA_DIR", str(Path(__file__).resolve().parents[2] / "data")))
DB_PATH = os.environ.get("AI_PLAYGROUND_DB", str(DATA_DIR / "ai_playground.db"))
UPLOADS_DIR = Path(os.environ.get("AI_PLAYGROUND_UPLOADS_DIR", str(DATA_DIR / "uploads")))

# Embedding Lab (bench) demo. ONNX model assets (int8 graphs + tokenizers fetched from Hugging
# Face) live here — on the same mounted volume as the DB, so they survive restarts. The optional
# direct-Ollama URL lets an admin pull a new broker embedder from the UI; absent/unreachable, the
# UI just shows the `ollama pull <model>` command to run on the broker box.
MODELS_DIR = Path(os.environ.get("AI_PLAYGROUND_MODELS_DIR", str(DATA_DIR / "models")))
OLLAMA_URL = os.environ.get("AI_PLAYGROUND_OLLAMA_URL", "http://host.docker.internal:11434").rstrip("/")

# Seed corpora baked into the image (read-only): one subfolder of *.md per corpus. In the
# container this is overridden to /srv/seed/corpora (the installed package can't see the
# repo tree). Ingested on first boot via the broker.
SEED_CORPORA_DIR = Path(os.environ.get(
    "AI_PLAYGROUND_SEED_DIR", str(Path(__file__).resolve().parents[2] / "seed" / "corpora")))

# Seed query sets for the Embedding Lab (baked into the image beside the seed corpora): one
# JSON per set, {"name", "queries":[{"q","targets":[source…]}]}. Loaded into SQLite on boot.
SEED_QUERYSETS_DIR = Path(os.environ.get(
    "AI_PLAYGROUND_SEED_QUERYSETS_DIR",
    str(Path(__file__).resolve().parents[2] / "seed" / "querysets")))

PORT = int(os.environ.get("AI_PLAYGROUND_PORT", "8850"))

# Trust boundary: in the platform the gateway authenticates every request and sets the
# X-Platform-* identity headers (stripping any client copy first). A request with NO identity
# header therefore did not come through the gateway — in the deployed topology that means a
# direct-to-rail call from a sibling container, which must NOT be treated as an admin/owner.
# So identity/require_admin FAIL CLOSED on a missing header unless this standalone-dev flag is set.
STANDALONE = os.environ.get("AI_PLAYGROUND_STANDALONE", "").strip().lower() in ("1", "true", "yes")

# Generation model. Standalone default is NVIDIA's own local Nemotron (installed, 4B, fast,
# non-reasoning) so the demo is end-to-end NVIDIA even before the NIM toggle. In the
# container this is overridden to the per-rail role ``@ai-playground`` (roles.json), which
# also attributes queued jobs to this rail in the admin queue.
CHAT_MODEL = os.environ.get("AI_PLAYGROUND_CHAT_MODEL", "nemotron-3-nano:4b")
# Retrieval embedder — always local through the broker (@embed -> bge-m3), for BOTH the
# local and the NVIDIA-NIM generation modes (only generation flips; retrieval stays local).
EMBED_MODEL = os.environ.get("AI_PLAYGROUND_EMBED_MODEL", "@embed")

TOP_K = int(os.environ.get("AI_PLAYGROUND_TOP_K", "4"))
MAX_TOKENS = int(os.environ.get("AI_PLAYGROUND_MAX_TOKENS", "800"))

# Answer-grounding contract (kept identical to the standalone enablement kit).
SYSTEM_PROMPT = (
    "You are a technical enablement assistant. Answer ONLY from the provided context. "
    "Cite sources inline as [1], [2]. If the context doesn't cover it, say so plainly."
)


def ensure_dirs() -> None:
    for d in (DATA_DIR, UPLOADS_DIR, MODELS_DIR):
        d.mkdir(parents=True, exist_ok=True)
