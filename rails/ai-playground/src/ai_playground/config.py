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
# Whether the weekly "Embedding model update" may re-pull the model @embed resolves to (the
# PLATFORM's retrieval embedder, bge-m3 today). Off by default: a new upstream digest for that tag
# changes the weights under every rail's stored vectors (this rail's corpora, gemini-cx, smb,
# recipe-book...) with nothing re-embedding them, so retrieval degrades silently everywhere.
# Opt in only alongside a planned re-index. The run record says when it was skipped and why.
REPULL_EMBEDDER = os.environ.get("AI_PLAYGROUND_REPULL_EMBEDDER", "").strip().lower() in (
    "1", "true", "yes")

# Seed corpora baked into the image (read-only): one subfolder of *.md per corpus. In the
# container this is overridden to /srv/seed/corpora (the installed package can't see the
# repo tree). Ingested on first boot via the broker.
SEED_CORPORA_DIR = Path(os.environ.get(
    "AI_PLAYGROUND_SEED_DIR", str(Path(__file__).resolve().parents[2] / "seed" / "corpora")))

# Which seed corpus the RAG demo lands on. Named, not positional: the demo's sample questions
# are written for one specific corpus, so the default has to be a deliberate choice rather than
# a side effect of ingest order. ensure_seeds() ingests this one first so it also sorts first
# for anyone reading the list, but the API reports the slug and the client selects by it.
DEFAULT_CORPUS_SLUG = os.environ.get("AI_PLAYGROUND_DEFAULT_CORPUS", "nvidia-stack").strip()

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
STANDALONE = os.environ.get("PLATFORM_STANDALONE", "").strip().lower() in ("1", "true", "yes")

# Generation model. The per-rail role ``@ai-playground`` (roles.json -> NVIDIA's own local
# Nemotron: installed, 4B, fast, non-reasoning) so the demo is end-to-end NVIDIA even before
# the NIM toggle. Defaulting to the ROLE rather than the model name keeps Admin -> Rails
# authoritative in standalone dev too, and attributes queued jobs to this rail in the admin
# queue. Repoint the role, not this line.
CHAT_MODEL = os.environ.get("AI_PLAYGROUND_CHAT_MODEL", "@ai-playground")
# Retrieval embedder — always local through the broker (@embed -> bge-m3), for BOTH the
# local and the NVIDIA-NIM generation modes (only generation flips; retrieval stays local).
EMBED_MODEL = os.environ.get("AI_PLAYGROUND_EMBED_MODEL", "@embed")
# Drafts the "about" line for a model an admin adds to the Embedding Lab (bench/describe.py).
# A clean instruction-follower; the class role @chat. Declared as the `describe` model slot so
# the chips and tools/rail_smoke.py's scoped role check can see it; it used to be read inside
# bench/describe.py, a call no manifest named.
DESCRIBE_MODEL = os.environ.get("AI_PLAYGROUND_DESCRIBE_MODEL", "@chat")

# POST /api/nim/probe spends the deployment's NVIDIA credits on a real 1-token completion
# (it has to: being listed in /v1/models is not being entitled to call it -- see nim.py). It is
# a button a human presses now and then, so a handful per minute is generous; this limit exists
# because "a named caller" is not a budget. A sibling container on the compose network, or a UI
# that re-renders in a loop, can otherwise bill the account as fast as it can issue requests.
NIM_PROBE_LIMIT = int(os.environ.get("AI_PLAYGROUND_NIM_PROBE_LIMIT", "6"))
NIM_PROBE_WINDOW_SECONDS = float(os.environ.get("AI_PLAYGROUND_NIM_PROBE_WINDOW", "60"))

# The probe is the CHEAP door. The expensive one is /ws/rag with backend "nim": a real streamed
# answer of up to MAX_TOKENS (800) per question on the same key, and it had no limit at all
# (measured 2026-10-08: 50 questions from one caller, 50 completions, 0 refusals). Two budgets,
# because each covers the other's hole:
#   * per caller, so one person cannot spend everyone's allowance. The key is X-Platform-User,
#     which the gateway sets and strips from clients, but a sibling container on the compose
#     network talks to the rail directly and can put any name it likes there;
#   * one ceiling for the whole rail, keyed on nothing the caller sends, so rotating the name
#     buys nothing. Every NVIDIA call draws on it: answers, the toggle's probe and the weekly
#     check. 30 per 5 minutes is far above a human demo (one question every 10 s, sustained)
#     and caps the worst case at 30 x 800 = 24,000 generated tokens per window.
# A refusal is a 429 (HTTP) or an error frame (WebSocket) naming the budget and the wait, and it
# is charged BEFORE the call, so a refused request spends nothing.
NIM_CHAT_LIMIT = int(os.environ.get("AI_PLAYGROUND_NIM_CHAT_LIMIT", "10"))
NIM_CHAT_WINDOW_SECONDS = float(os.environ.get("AI_PLAYGROUND_NIM_CHAT_WINDOW", "300"))
NIM_GLOBAL_LIMIT = int(os.environ.get("AI_PLAYGROUND_NIM_GLOBAL_LIMIT", "30"))
NIM_GLOBAL_WINDOW_SECONDS = float(os.environ.get("AI_PLAYGROUND_NIM_GLOBAL_WINDOW", "300"))

# Upload ceilings, the E5 norm finance and job-aid follow. Both routes read the whole body,
# decoded it in full and (for documents) embedded every chunk on the shared GPU, with no bound:
# measured 2026-09-11, a 40 MB "document" was accepted and ingested in 25.3 s and a 40 MB
# "query set" was read and parsed before its 400. A demo corpus is a handful of .md/.txt files
# (the shipped seeds total 0.1 MB), so 8 MB is generous; a query set is a short JSON list.
MAX_UPLOAD_BYTES = int(os.environ.get("AI_PLAYGROUND_MAX_UPLOAD_BYTES", str(8 * 1024 * 1024)))
MAX_QUERYSET_BYTES = int(os.environ.get("AI_PLAYGROUND_MAX_QUERYSET_BYTES", str(1024 * 1024)))
# User corpora one owner may hold. Each upload makes a NEW corpus (slug-2, slug-3, ...) whose
# chunks and vectors live in SQLite for good, so without a ceiling disk use had none either.
MAX_CORPORA_PER_USER = int(os.environ.get("AI_PLAYGROUND_MAX_CORPORA_PER_USER", "20"))
# How many corpora's (chunks, vector matrix) stay in memory for retrieval. It was unbounded:
# every corpus ever queried stayed resident for the life of the process (an n x 1024 float32
# matrix each, 4 MB per 1,000 chunks). Least recently used goes first.
CORPUS_CACHE_SIZE = int(os.environ.get("AI_PLAYGROUND_CORPUS_CACHE_SIZE", "16"))

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
