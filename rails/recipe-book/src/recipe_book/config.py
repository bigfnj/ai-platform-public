"""Runtime settings for the recipe-book rail.

Everything is env-overridable so the same code runs standalone (broker on
localhost, data under ``./data``) and in the container (broker via
``host.docker.internal``, data under ``/srv/var`` — a mounted named volume).
"""
from __future__ import annotations

import os
from pathlib import Path

# All mutable state lives here: the SQLite DB, the recipe corpus (markdown), and
# the generated per-recipe icons. A mounted named volume in the container.
DATA_DIR = Path(os.environ.get(
    "RECIPE_BOOK_DATA_DIR", str(Path(__file__).resolve().parents[2] / "data")))

DB_PATH = os.environ.get("RECIPE_BOOK_DB", str(DATA_DIR / "recipe_book.db"))
# The parsed recipe source: a tree of ``<Category>/<Title>.md`` cards.
RECIPES_DIR = Path(os.environ.get("RECIPE_BOOK_RECIPES_DIR", str(DATA_DIR / "recipes")))
# Generated clipart icons (SDXL), one PNG per recipe id.
ICONS_DIR = Path(os.environ.get("RECIPE_BOOK_ICONS_DIR", str(DATA_DIR / "icons")))

# Bundled seed corpus (baked into the image; committed under the rail's ``seed/`` dir).
# On a FRESH/empty volume the app hydrates RECIPES_DIR from this on first run, so a clean
# ``git clone`` + deploy comes up populated. It never overwrites an existing corpus.
SEED_RECIPES_DIR = Path(os.environ.get(
    "RECIPE_BOOK_SEED_DIR", str(Path(__file__).resolve().parents[2] / "seed" / "recipes")))

# Bundled seed icons: a tar.gz of downscaled per-recipe PNGs (committed under ``seed/``),
# unpacked into ICONS_DIR on a fresh/empty volume so a clean install ships recipes WITH
# icons (no image GPU / broker render needed). ``icons.reconcile`` then flips those recipes
# to icon_status='ready'. Lets the 8 GB / no-media installer ship a fully-illustrated corpus.
SEED_ICONS_ARCHIVE = Path(os.environ.get(
    "RECIPE_BOOK_SEED_ICONS", str(Path(__file__).resolve().parents[2] / "seed" / "icons.tgz")))

PORT = int(os.environ.get("RECIPE_BOOK_PORT", "8830"))

# "Send to Phone": push the shopping list into Google Tasks (checkable items that show up
# in the Google Tasks app / Calendar sidebar / Gmail on the phone). Google Keep has no consumer
# API, hence Tasks. This is a SHARED OAuth *app* (one Google Cloud project) — the id/secret and
# the registered redirect URI live here; each USER connects their own Google account in-app, so
# their refresh token is stored per-owner in the DB, not in env. See docs/GOOGLE_TASKS.md.
GTASKS_CLIENT_ID = os.environ.get("RECIPE_BOOK_GTASKS_CLIENT_ID", "")
GTASKS_CLIENT_SECRET = os.environ.get("RECIPE_BOOK_GTASKS_CLIENT_SECRET", "")
# Fernet key (urlsafe-base64, 32 bytes) that encrypts per-user Google refresh tokens at rest.
# Lives in deploy/.env (not on the data volume). Blank = tokens stored plaintext (legacy). See
# recipe_book.crypto; rotating it makes stored tokens undecryptable (users re-connect).
TOKEN_KEY = os.environ.get("RECIPE_BOOK_TOKEN_KEY", "")
# The OAuth redirect URI, registered on the Web client. Must be the gateway-fronted callback,
# e.g. https://<host>/recipe-book/api/gtasks/callback, so users can connect from their own phones.
GTASKS_REDIRECT_URI = os.environ.get("RECIPE_BOOK_GTASKS_REDIRECT_URI", "")
# The Google Tasks list the items land in (created on first use if it doesn't exist).
GTASKS_LIST_TITLE = os.environ.get("RECIPE_BOOK_GTASKS_LIST_TITLE", "Shopping List")


def gtasks_configured() -> bool:
    """True when the shared Google Tasks OAuth *app* is set up (so users can connect).
    Whether a given user is connected is a per-owner DB check (``db.gtasks_get``)."""
    return bool(GTASKS_CLIENT_ID and GTASKS_CLIENT_SECRET and GTASKS_REDIRECT_URI)

# Multi-tenant: the platform username that inherits the pre-existing single-tenant data
# (the meal plan / pantry / bar built before per-user scoping). Set on the compose
# service to the primary user so their data carries over the flip. Empty = leave the
# legacy default owner unclaimed.
PRIMARY_USER = os.environ.get("RECIPE_BOOK_PRIMARY_USER", "")

# Meal-plan history: keep this many days of past entries (browsable via the planner's
# ‹ Prev, and used to keep AI suggestions varied); a nightly job purges older ones.
PLAN_RETENTION_DAYS = int(os.environ.get("RECIPE_BOOK_PLAN_RETENTION_DAYS", "183"))  # ~6 months
# The AI planner avoids re-suggesting a meal planned within this many days (recent past
# or already-scheduled ahead), so the same dinners don't keep resurfacing.
PLAN_RECENCY_DAYS = int(os.environ.get("RECIPE_BOOK_PLAN_RECENCY_DAYS", "42"))  # ~6 weeks


def ensure_dirs() -> None:
    for d in (DATA_DIR, RECIPES_DIR, ICONS_DIR):
        d.mkdir(parents=True, exist_ok=True)
