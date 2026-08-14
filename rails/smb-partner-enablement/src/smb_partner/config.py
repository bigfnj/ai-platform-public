"""Runtime settings for the SMB Partner Enablement rail.

Env-overridable so the same code runs standalone (broker on localhost, data under
``./data``) and in the container (broker via ``host.docker.internal``, mutable state on a
mounted named volume at ``/srv/var``, seed knowledge base baked read-only at ``/srv/seed``).
"""
from __future__ import annotations

import os
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parents[2]  # rails/smb-partner-enablement/

# Mutable state (SQLite index + uploaded SME docs) — a mounted named volume in the container.
DATA_DIR = Path(os.environ.get("SMB_PARTNER_DATA_DIR", str(_PKG_ROOT / "data")))
DB_PATH = os.environ.get("SMB_PARTNER_DB", str(DATA_DIR / "smb_partner.db"))
UPLOADS_DIR = Path(os.environ.get("SMB_PARTNER_UPLOADS_DIR", str(DATA_DIR / "uploads")))
AUDIO_CACHE_DIR = Path(os.environ.get("SMB_PARTNER_AUDIO_DIR", str(DATA_DIR / "audio")))

# The SME knowledge base: one subfolder per collection, *.md inside. Baked into the image
# read-only and ingested on first boot. Authoring contract: seed/knowledge-base/README.md.
SEED_KB_DIR = Path(os.environ.get(
    "SMB_PARTNER_SEED_DIR", str(_PKG_ROOT / "seed" / "knowledge-base")))

PORT = int(os.environ.get("SMB_PARTNER_PORT", "8870"))

# Trust boundary: the gateway authenticates every request and sets X-Platform-* identity
# headers (stripping any client copy first). A request with NO identity header did not come
# through the gateway, so identity FAILS CLOSED unless this standalone-dev flag is set.
STANDALONE = os.environ.get("SMB_PARTNER_STANDALONE", "").strip().lower() in ("1", "true", "yes")

# --- models: TWO broker models held concurrently --------------------------------
# The broker's policy is one HEAVY (generative) model resident at a time, with embedders
# free to stay loaded alongside. That is exactly this rail's steady state:
#   @smb-partner-rag  heavy  — grounds + writes the answer
#   @embed            light  — retrieval over the SME corpus, resident throughout
# Both are live in VRAM for the whole session; a turn costs no model swap.
RAG_MODEL = os.environ.get("SMB_PARTNER_RAG_MODEL", "@smb-partner-rag")
EMBED_MODEL = os.environ.get("SMB_PARTNER_EMBED_MODEL", "@embed")

# Voice. See voice.py — "auto" probes the broker's media worker and falls back to the
# browser's speech synthesis, which is what actually carries the mobile experience today.
VOICE_BACKEND = os.environ.get("SMB_PARTNER_VOICE_BACKEND", "auto").strip().lower()
VOICE_LANG = os.environ.get("SMB_PARTNER_VOICE_LANG", "en")
VOICE_SPEAKER = os.environ.get("SMB_PARTNER_VOICE_SPEAKER", "").strip()

TOP_K = int(os.environ.get("SMB_PARTNER_TOP_K", "5"))
MAX_TOKENS = int(os.environ.get("SMB_PARTNER_MAX_TOKENS", "700"))

# Spoken answers must stay short — a partner on a phone between meetings will not listen to
# 700 tokens. The voice path asks for a tighter budget than the on-screen answer.
VOICE_MAX_TOKENS = int(os.environ.get("SMB_PARTNER_VOICE_MAX_TOKENS", "220"))

SYSTEM_PROMPT = (
    "You are an enablement assistant for Microsoft SMB partners. Answer ONLY from the "
    "provided context. Cite sources inline as [1], [2]. If the context does not cover the "
    "question, say so plainly and name what the partner should look up instead. Be concrete "
    "and commercial: partners are selling, not studying."
)

# The spoken persona. Same grounding contract, but shaped for the ear rather than the eye.
VOICE_SYSTEM_PROMPT = (
    "You are an enablement assistant for Microsoft SMB partners, speaking out loud. Answer "
    "ONLY from the provided context. Keep it under 90 words, lead with the answer, and use "
    "plain spoken sentences — no markdown, no bullet characters, no inline citations. If the "
    "context does not cover it, say so in one sentence."
)


def ensure_dirs() -> None:
    for d in (DATA_DIR, UPLOADS_DIR, AUDIO_CACHE_DIR):
        d.mkdir(parents=True, exist_ok=True)
