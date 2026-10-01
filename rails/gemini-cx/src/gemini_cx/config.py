"""Runtime settings for the Gemini Enterprise CX rail.

Env-overridable so the same code runs standalone (broker on localhost, data under ``./data``)
and in the container (broker via ``host.docker.internal``, mutable state on a mounted named
volume at ``/srv/var``, seed knowledge base baked read-only at ``/srv/seed``).
"""
from __future__ import annotations

import os
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parents[2]  # rails/gemini-cx/

# Mutable state (SQLite chunk index + uploaded docs) — a mounted named volume in the container.
DATA_DIR = Path(os.environ.get("GEMINI_CX_DATA_DIR", str(_PKG_ROOT / "data")))
DB_PATH = os.environ.get("GEMINI_CX_DB", str(DATA_DIR / "gemini_cx.db"))
UPLOADS_DIR = Path(os.environ.get("GEMINI_CX_UPLOADS_DIR", str(DATA_DIR / "uploads")))

# The GECX corpus: one subfolder per collection, *.md inside. Baked into the image read-only
# and ingested on first boot. Authoring contract: seed/knowledge-base/README.md.
SEED_KB_DIR = Path(os.environ.get(
    "GEMINI_CX_SEED_DIR", str(_PKG_ROOT / "seed" / "knowledge-base")))

PORT = int(os.environ.get("GEMINI_CX_PORT", "8880"))

# Trust boundary: the gateway authenticates every request and sets X-Platform-* identity
# headers (stripping any client copy first). A request with NO identity header did not come
# through the gateway, so identity FAILS CLOSED unless this standalone-dev flag is set.
STANDALONE = os.environ.get("PLATFORM_STANDALONE", "").strip().lower() in ("1", "true", "yes")

# --- models: two broker models held concurrently ---------------------------------
# The broker keeps one HEAVY (generative) model resident at a time, with embedders free to
# stay loaded alongside. That is this rail's steady state while it is in use:
#   @gemini-cx-rag  heavy  — grounds and writes the answer
#   @embed          light  — retrieval over the GECX corpus
# What holds them between questions is the 30m keep_alive every chat sends, not boot (see
# WARM_ON_BOOT). See MODELS.md for the VRAM arithmetic and the swap-avoidance tradeoff against
# the smb-partner-enablement rail.
RAG_MODEL = os.environ.get("GEMINI_CX_RAG_MODEL", "@gemini-cx-rag")
EMBED_MODEL = os.environ.get("GEMINI_CX_EMBED_MODEL", "@embed")
# Load EMBED_MODEL and RAG_MODEL at container start, so the first question skips the cold load.
# OFF by default since 2026-10-01: on an 8 GB card it held both models (about 3.5 GB of VRAM,
# plus the runner's host RAM) for 30 minutes after every stack start, which is when memory is
# tightest, to save a few seconds on a first question that may not come. Every start also
# re-warmed them. GEMINI_CX_WARM_ON_BOOT=1 restores it; the first question loads them either way.
# On a 24 GB card the RAG role resolves to a 24B model and the warm held ~19.6 GB. Set it in
# deploy/.env: both composes pass it through, and a value compose never passes reaches nothing.
WARM_ON_BOOT = os.environ.get("GEMINI_CX_WARM_ON_BOOT", "").strip().lower() in ("1", "true", "yes")

TOP_K = int(os.environ.get("GEMINI_CX_TOP_K", "6"))
MAX_TOKENS = int(os.environ.get("GEMINI_CX_MAX_TOKENS", "800"))

# --- voice: "Read aloud" on every answer ----------------------------------------
# Kokoro-82M through the broker's /v1/tts_light — NOT /v1/tts, which evicts every heavy model
# per utterance and would destroy this rail's LLM+embedder co-residency. Kokoro is ~350 MB and
# coexists with both. See voice.py for the backend seam and MODELS.md for the VRAM budget.
#   auto     probe the broker's media worker; fall back to the browser when it is unavailable
#   broker   force Kokoro
#   browser  force the client's Web Speech API (zero GPU)
#   off      no voice
VOICE_BACKEND = os.environ.get("GEMINI_CX_VOICE_BACKEND", "auto").strip().lower()
VOICE_LANG = os.environ.get("GEMINI_CX_VOICE_LANG", "en")
# Female American English. Set explicitly rather than relying on Kokoro's default so the voice
# does not change under us if that default ever moves. 'af_' = American female.
VOICE_SPEAKER = os.environ.get("GEMINI_CX_VOICE_SPEAKER", "af_heart").strip()

# GECX has four status levels that its own marketing collapses into "available", so the
# grounding contract names them explicitly. The corpus marks status per capability; the model
# is told to carry that through rather than smoothing it away.
SYSTEM_PROMPT = (
    "You are a subject-matter assistant for Google Cloud's Gemini Enterprise for Customer "
    "Experience (GECX). Answer ONLY from the provided context. Cite sources inline as [1], "
    "[2].\n"
    "Rules you must not break:\n"
    "1. If the context does not cover the question, say so plainly and name what the reader "
    "should check instead. Never fill a gap with general knowledge about Google or AI.\n"
    "2. Never invent a price, percentage, latency figure, quota, or language list. If the "
    "context says a figure is unpublished, say it is unpublished.\n"
    "3. Preserve capability status exactly as the context states it — GA, Preview, coming "
    "soon, or announced-only are four different answers and must not be collapsed into "
    "'available'.\n"
    "4. Where the context flags a commonly confused pair (40+ text languages vs 10 "
    "audio-to-audio languages; Gemini Enterprise vs Gemini Enterprise for CX; handoff rules "
    "vs human escalation), state which one you are answering about.\n"
    "Be concrete and practitioner-facing: the reader is scoping or delivering work, not "
    "studying."
)


def ensure_dirs() -> None:
    """Create the mutable-state directories. Called by store.init() before touching SQLite —
    the container mounts an empty volume at /srv/var, so nothing below it exists on first boot.
    """
    for d in (DATA_DIR, UPLOADS_DIR):
        d.mkdir(parents=True, exist_ok=True)
