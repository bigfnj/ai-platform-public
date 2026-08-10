"""Runtime settings for the bouquet rail.

Everything is env-overridable so the same code runs standalone (broker on
localhost, data under ``./var``, KB read from the repo's ``seed/``) and in the
container (broker via ``host.docker.internal``, data under ``/srv/var`` — a
mounted named volume — and the KB baked in at ``/srv/seed/knowledge-base``).
"""
from __future__ import annotations

import os
from pathlib import Path

# .../src/bouquet/config.py -> src/bouquet -> src -> <repo root>
_REPO_ROOT = Path(__file__).resolve().parents[2]

# The flower knowledge base: read-only reference data (profiles + cross-cutting
# references + licensed reference photos). Baked into the image (COPY seed) and
# never written at runtime, so it is read straight from here — no volume hydration.
KB_DIR = Path(os.environ.get(
    "BOUQUET_KB_DIR", str(_REPO_ROOT / "seed" / "knowledge-base")))

# All MUTABLE state (the analyses DB + saved uploaded photos). A mounted named
# volume in the container.
DATA_DIR = Path(os.environ.get("BOUQUET_DATA_DIR", str(_REPO_ROOT / "var")))
DB_PATH = os.environ.get("BOUQUET_DB", str(DATA_DIR / "bouquet.db"))
UPLOADS_DIR = Path(os.environ.get("BOUQUET_UPLOADS_DIR", str(DATA_DIR / "uploads")))


def pending_dir() -> Path:
    """Holding area for a full-res upload between identify and generate. Derived
    from ``UPLOADS_DIR`` (not a module constant) so a create_api override of the
    data dir is honoured. A pending file lives only for the human edit pause: a
    successful generate deletes its own, and the weekly sweep mops up abandoned ones."""
    return UPLOADS_DIR / "pending"


# Broker + model roles. The broker resolves a leading ``@`` role (roles.json) and a
# size-scoped wildcard, and owns the one-heavy-model VRAM policy.
BROKER_URL = os.environ.get("BOUQUET_BROKER_URL", "http://127.0.0.1:11500").rstrip("/")
# Vision model identifies the flowers from the photo (multimodal chat).
VISION_MODEL = os.environ.get("BOUQUET_VISION_MODEL", "@vision")
# Writer models: the description (Frenchies copy) and the expert analysis both run
# on the large chat model (qwen3.6:27b). The vision->writer evict/reload lands during
# the human edit pause, so its cost isn't felt. Split env vars so the two can be
# repointed independently later.
DESCRIPTION_MODEL = os.environ.get("BOUQUET_DESCRIPTION_MODEL", "@chat-large")
ANALYSIS_MODEL = os.environ.get("BOUQUET_ANALYSIS_MODEL", "@chat-large")

# A cold heavy-model load (a vision or chat model swapping in) can take a while.
BROKER_TIMEOUT = float(os.environ.get("BOUQUET_BROKER_TIMEOUT", "600"))

# Longest edge (px) the uploaded photo is downscaled to before it goes to the
# vision model. Pinned to 896: gemma3's SigLIP vision encoder is natively 896x896,
# and feeding it a LARGER image makes the model return empty content (verified —
# 1280px yields nothing, 896px identifies cleanly). Don't raise this without
# re-testing the resolved @vision model.
MAX_IMAGE_EDGE = int(os.environ.get("BOUQUET_MAX_IMAGE_EDGE", "896"))

# Longest edge (px) of the PERMANENT image kept per analysis. Originals are not
# retained (a decision, for storage + privacy): once generate runs, this derivative
# is the only image and the full-res pending upload is deleted.
DERIVATIVE_EDGE = int(os.environ.get("BOUQUET_DERIVATIVE_EDGE", "720"))

# After identify returns the draft, pre-load the writer model so the vision->writer
# swap overlaps the human edit pause (the writer is warm by the time generate runs).

PORT = int(os.environ.get("BOUQUET_PORT", "8840"))


WARM_WRITER = os.environ.get("BOUQUET_WARM_WRITER", "1").strip().lower() in {"1", "true", "yes", "on"}

# Retrieval-grounding: embed the uploaded photo (broker SigLIP), find the nearest KB
# reference photos, and feed the vision model a short candidate list. Best-effort — any
# failure falls back to ungrounded identify. Toggle + shortlist tuning are env-driven.
GROUNDING_ENABLED = os.environ.get("BOUQUET_GROUNDING", "1").strip().lower() in {"1", "true", "yes", "on"}
GROUNDING_K = int(os.environ.get("BOUQUET_GROUNDING_K", "8"))        # nearest reference photos
GROUNDING_MAX = int(os.environ.get("BOUQUET_GROUNDING_MAX", "5"))     # distinct flowers in the shortlist
# The SigLIP model the reference index was built with. MUST match the broker's do_embed_image
# default; a mismatch means a stale index in a different embedding space, so retrieval._index()
# disables grounding loudly rather than return garbage (dims can match across models).
GROUNDING_MODEL = os.environ.get("BOUQUET_GROUNDING_MODEL", "google/siglip2-base-patch16-384")


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


# Weekly cleanup sweep (in-process background task, following the platform's
# scheduled-maintenance pattern). Normal use self-cleans — a successful generate deletes its own pending
# upload — so this only mops up sessions that were uploaded but never generated.
CLEANUP_ENABLED = _env_bool("BOUQUET_CLEANUP_ENABLED", True)
CLEANUP_DOW = int(os.environ.get("BOUQUET_CLEANUP_DOW", "6"))        # 0=Mon … 6=Sun
CLEANUP_HOUR = int(os.environ.get("BOUQUET_CLEANUP_HOUR", "3"))       # local hour, 24h
ORPHAN_MAX_AGE_HOURS = int(os.environ.get("BOUQUET_ORPHAN_MAX_AGE_HOURS", "48"))
# Zone the weekly sweep is scheduled in. Resolved via zoneinfo (the tzdata wheel
# ships the IANA db, so this works even on the tzdata-less slim base image).
TIMEZONE = os.environ.get("TZ") or "America/Los_Angeles"


def ensure_dirs() -> None:
    """Create the mutable-state dirs on boot (idempotent). The KB dir is not
    created — it must already exist (baked in / committed)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    pending_dir().mkdir(parents=True, exist_ok=True)
