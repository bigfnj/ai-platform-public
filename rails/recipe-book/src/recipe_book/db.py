"""SQLite storage: recipe content (DB-as-source-of-truth, parsed from the markdown
corpus) plus the owner-scoped personalization layer.

One connection per request, ``check_same_thread=False`` + WAL + a busy timeout, so
FastAPI's threadpool can run blocking DB work safely (single writer). The recipe
corpus is small, so the in-memory :class:`~recipe_book.catalog.Catalog` is rebuilt
from the ``recipes`` table on startup and after every re-import.
"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone

from recipe_book import config, crypto
from recipe_book.catalog import Catalog, Recipe, Section

# The default owner. Un-gated requests (standalone dev / tests, no gateway identity
# header) resolve to this seeded user; on the platform, a request's owner is resolved
# from X-Platform-User via ``resolve_owner``. owner_id is on every mutable row, so
# multi-tenancy was a flip, not a migration.
OWNER_ID = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS recipes (
  id            TEXT PRIMARY KEY,
  title         TEXT NOT NULL,
  category      TEXT,
  kind          TEXT,               -- meal | beverage
  rel_path      TEXT,
  meta          TEXT,
  source        TEXT,
  ingredients   TEXT,               -- JSON list
  instructions  TEXT,               -- JSON list
  shopping_list TEXT,               -- JSON list
  extra_sections TEXT,              -- JSON list of {heading,ordered,items}
  is_collection INTEGER DEFAULT 0,
  base_spirits  TEXT,               -- JSON list (beverages)
  glass         TEXT,
  technique     TEXT,
  icon_status   TEXT DEFAULT 'pending',
  content_hash  TEXT
);
CREATE INDEX IF NOT EXISTS idx_recipes_kind ON recipes(kind);
CREATE INDEX IF NOT EXISTS idx_recipes_category ON recipes(category);

CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY, name TEXT DEFAULT 'Default', created_at TEXT,
  platform_user TEXT);          -- the gateway X-Platform-User this row owns (NULL = default)

CREATE TABLE IF NOT EXISTS favorites (
  id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER, recipe_id TEXT,
  created_at TEXT, UNIQUE(owner_id, recipe_id));

CREATE TABLE IF NOT EXISTS ratings (
  id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER, recipe_id TEXT,
  stars INTEGER, note TEXT DEFAULT '', updated_at TEXT, UNIQUE(owner_id, recipe_id));

CREATE TABLE IF NOT EXISTS tags (
  id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER, name TEXT,
  color TEXT DEFAULT 'accent', UNIQUE(owner_id, name));

CREATE TABLE IF NOT EXISTS recipe_tags (
  id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER, recipe_id TEXT,
  tag_id INTEGER, UNIQUE(owner_id, recipe_id, tag_id));

CREATE TABLE IF NOT EXISTS meal_plan_entries (
  id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER, date TEXT, slot TEXT,
  recipe_id TEXT, title TEXT DEFAULT '', servings INTEGER DEFAULT 2, created_at TEXT);

CREATE TABLE IF NOT EXISTS pantry_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER, name TEXT, kind TEXT,
  UNIQUE(owner_id, name, kind));

CREATE TABLE IF NOT EXISTS bar_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER, name TEXT, kind TEXT,
  UNIQUE(owner_id, name, kind));

CREATE TABLE IF NOT EXISTS shopping_checks (
  id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER, item_key TEXT,
  checked INTEGER DEFAULT 0, UNIQUE(owner_id, item_key));

-- Manual category moves (recipe -> category), reapplied on every ingest so a user's
-- "Change category" survives a rebuild and wins over auto-classification.
CREATE TABLE IF NOT EXISTS category_overrides (
  recipe_id TEXT PRIMARY KEY, category TEXT, updated_at TEXT);

-- Manual content edits (ingredients / method / shopping), reapplied on every ingest
-- so an edited card survives a rebuild. Keyed by the path-stable recipe id, so the
-- edit stays attached through favorites / ratings / planner. NULL column = unchanged.
CREATE TABLE IF NOT EXISTS content_overrides (
  recipe_id TEXT PRIMARY KEY, ingredients TEXT, instructions TEXT,
  shopping_list TEXT, updated_at TEXT);

-- Manual dietary/allergen tag overrides (add/remove RELATIVE to the auto-classification),
-- reapplied on every load so a user's tag edits survive re-ingest and ingredient changes.
CREATE TABLE IF NOT EXISTS attribute_overrides (
  recipe_id TEXT PRIMARY KEY, add_tags TEXT, remove_tags TEXT, updated_at TEXT);

-- Manual title renames, reapplied on every ingest so a rename survives a rebuild. The recipe
-- id is path-derived (not title-derived), so a rename keeps the stable id + file path — only
-- the displayed title changes.
CREATE TABLE IF NOT EXISTS title_overrides (
  recipe_id TEXT PRIMARY KEY, title TEXT, updated_at TEXT);

-- Global app settings (single-tenant, so not owner-scoped). String values; callers
-- coerce. An absent key means "use the config/env default" (see recipe_book.settings).
CREATE TABLE IF NOT EXISTS app_settings (
  key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);

-- LLM-authored, per-recipe icon SUBJECT (a distinctive one-line description of what the
-- icon should depict). Replaces the coarse keyword->generic-subject heuristic so 70
-- chicken recipes don't all render an identical "roast chicken". Rebuilt on demand.
CREATE TABLE IF NOT EXISTS icon_prompts (
  recipe_id TEXT PRIMARY KEY, subject TEXT, updated_at TEXT);

-- Per-owner Google Tasks OAuth for the shopping list's "Send to Phone". Each user connects
-- their OWN Google account (in-app consent), so their list lands on their own phone. Only the
-- refresh token is stored here; the shared OAuth app id/secret live in config/.env.
CREATE TABLE IF NOT EXISTS gtasks_tokens (
  owner_id INTEGER PRIMARY KEY, refresh_token TEXT NOT NULL, email TEXT,
  list_title TEXT, connected_at TEXT);

-- Short-lived OAuth state nonces (CSRF): /connect stores state->owner, /callback verifies
-- and consumes it. Pruned opportunistically on create.
CREATE TABLE IF NOT EXISTS gtasks_oauth_state (
  state TEXT PRIMARY KEY, owner_id INTEGER, created_at TEXT);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    config.ensure_dirs()
    con = sqlite3.connect(config.DB_PATH, check_same_thread=False, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=5000")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def _migrate(con: sqlite3.Connection) -> None:
    """Additive migration for a pre-multi-tenant DB: add users.platform_user + a
    unique index over the non-null values. Idempotent."""
    cols = {r["name"] for r in con.execute("PRAGMA table_info(users)")}
    if "platform_user" not in cols:
        con.execute("ALTER TABLE users ADD COLUMN platform_user TEXT")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_platform_user "
                "ON users(platform_user) WHERE platform_user IS NOT NULL")


def init_db(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA)
    _migrate(con)
    if not con.execute("SELECT 1 FROM users WHERE id=?", (OWNER_ID,)).fetchone():
        con.execute("INSERT INTO users (id, name, created_at) VALUES (?, 'Default', ?)",
                    (OWNER_ID, _now()))
    # Claim the pre-existing single-tenant data (all under the default owner) for the
    # configured primary platform user, so their meal plan / pantry / bar carry over the
    # flip instead of showing up empty. Only claims a still-unclaimed default row.
    primary = (config.PRIMARY_USER or "").strip()
    if primary:
        con.execute(
            "UPDATE users SET platform_user=? WHERE id=? "
            "AND (platform_user IS NULL OR platform_user='')", (primary, OWNER_ID))
    con.commit()


def resolve_owner(con: sqlite3.Connection, username: str | None) -> int:
    """Map a gateway X-Platform-User to a stable integer owner_id, creating the user
    row on first sight. Empty/None (un-gated) -> the default owner."""
    username = (username or "").strip()
    if not username:
        return OWNER_ID
    row = con.execute("SELECT id FROM users WHERE platform_user=?", (username,)).fetchone()
    if row is not None:
        return int(row["id"])
    cur = con.execute("INSERT INTO users (name, created_at, platform_user) VALUES (?,?,?)",
                      (username, _now(), username))
    con.commit()
    return int(cur.lastrowid)


def list_users(con: sqlite3.Connection) -> list[str]:
    """Platform usernames that have a data row (for the admin's user picker)."""
    return [r["platform_user"] for r in con.execute(
        "SELECT platform_user FROM users "
        "WHERE platform_user IS NOT NULL AND platform_user != '' ORDER BY platform_user")]


# --- global app settings (kv) -----------------------------------------------

def get_setting(con: sqlite3.Connection, key: str) -> str | None:
    row = con.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(con: sqlite3.Connection, key: str, value: str) -> None:
    con.execute(
        "INSERT INTO app_settings (key, value, updated_at) VALUES (?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, value, _now()))
    con.commit()


# --- per-owner Google Tasks tokens ("Send to Phone") ------------------------

def gtasks_get(con: sqlite3.Connection, owner_id: int) -> dict | None:
    """This owner's stored Google Tasks connection, or None if not connected. The refresh
    token is decrypted on the way out (transparent to callers; see recipe_book.crypto)."""
    r = con.execute("SELECT refresh_token, email, list_title, connected_at "
                    "FROM gtasks_tokens WHERE owner_id=?", (owner_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["refresh_token"] = crypto.decrypt(d["refresh_token"])
    return d


def gtasks_set(con: sqlite3.Connection, owner_id: int, refresh_token: str,
               email: str | None, list_title: str) -> None:
    con.execute(
        "INSERT INTO gtasks_tokens (owner_id, refresh_token, email, list_title, connected_at) "
        "VALUES (?,?,?,?,?) ON CONFLICT(owner_id) DO UPDATE SET "
        "refresh_token=excluded.refresh_token, email=excluded.email, "
        "list_title=excluded.list_title, connected_at=excluded.connected_at",
        (owner_id, crypto.encrypt(refresh_token), email, list_title, _now()))
    con.commit()


def encrypt_gtasks_tokens_at_rest(con: sqlite3.Connection) -> int:
    """One-time migration: once a key is configured, encrypt any refresh tokens still stored
    as plaintext. Idempotent — rows that already decrypt as ciphertext are skipped, so it is
    safe to run on every startup. Returns the number of rows migrated."""
    if not crypto.enabled():
        return 0
    migrated = 0
    for r in con.execute("SELECT owner_id, refresh_token FROM gtasks_tokens").fetchall():
        stored = r["refresh_token"]
        if stored is None or crypto.is_ciphertext(stored):
            continue
        con.execute("UPDATE gtasks_tokens SET refresh_token=? WHERE owner_id=?",
                    (crypto.encrypt(stored), r["owner_id"]))
        migrated += 1
    if migrated:
        con.commit()
    return migrated


def gtasks_delete(con: sqlite3.Connection, owner_id: int) -> None:
    con.execute("DELETE FROM gtasks_tokens WHERE owner_id=?", (owner_id,))
    con.commit()


def gtasks_state_create(con: sqlite3.Connection, state: str, owner_id: int) -> None:
    """Record a CSRF nonce for a connect attempt (and prune stale ones ~15 min old)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat(timespec="seconds")
    con.execute("DELETE FROM gtasks_oauth_state WHERE created_at < ?", (cutoff,))
    con.execute("INSERT OR REPLACE INTO gtasks_oauth_state (state, owner_id, created_at) "
                "VALUES (?,?,?)", (state, owner_id, _now()))
    con.commit()


def gtasks_state_pop(con: sqlite3.Connection, state: str) -> int | None:
    """Consume a CSRF nonce, returning the owner_id that created it (or None if unknown)."""
    r = con.execute("SELECT owner_id FROM gtasks_oauth_state WHERE state=?", (state,)).fetchone()
    if r is None:
        return None
    con.execute("DELETE FROM gtasks_oauth_state WHERE state=?", (state,))
    con.commit()
    return int(r["owner_id"])


# --- recipe row <-> Recipe --------------------------------------------------

def _dump(v) -> str:
    return json.dumps(v, ensure_ascii=False)


def upsert_recipe(con: sqlite3.Connection, r: Recipe, content_hash: str) -> None:
    con.execute(
        """INSERT INTO recipes
             (id,title,category,kind,rel_path,meta,source,ingredients,instructions,
              shopping_list,extra_sections,is_collection,base_spirits,glass,technique,
              icon_status,content_hash)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET
             title=excluded.title, category=excluded.category, kind=excluded.kind,
             rel_path=excluded.rel_path, meta=excluded.meta, source=excluded.source,
             ingredients=excluded.ingredients, instructions=excluded.instructions,
             shopping_list=excluded.shopping_list, extra_sections=excluded.extra_sections,
             is_collection=excluded.is_collection, base_spirits=excluded.base_spirits,
             glass=excluded.glass, technique=excluded.technique,
             content_hash=excluded.content_hash""",
        (r.id, r.title, r.category, r.kind, r.rel_path, r.meta, r.source,
         _dump(r.ingredients), _dump(r.instructions), _dump(r.shopping_list),
         _dump([{"heading": s.heading, "ordered": s.ordered, "items": s.items}
                for s in r.extra_sections]),
         1 if r.is_collection else 0, _dump(r.base_spirits), r.glass, r.technique,
         r.icon_status, content_hash))


def _row_to_recipe(row: sqlite3.Row) -> Recipe:
    extras = [Section(heading=s["heading"], ordered=s["ordered"], items=s["items"])
              for s in json.loads(row["extra_sections"] or "[]")]
    return Recipe(
        id=row["id"], title=row["title"], category=row["category"], kind=row["kind"],
        rel_path=row["rel_path"], meta=row["meta"] or "", source=row["source"] or "",
        ingredients=json.loads(row["ingredients"] or "[]"),
        instructions=json.loads(row["instructions"] or "[]"),
        shopping_list=json.loads(row["shopping_list"] or "[]"),
        extra_sections=extras, is_collection=bool(row["is_collection"]),
        base_spirits=json.loads(row["base_spirits"] or "[]"),
        glass=row["glass"] or "", technique=row["technique"] or "",
        icon_status=row["icon_status"] or "pending")


def attribute_overrides(con: sqlite3.Connection) -> dict[str, dict]:
    """{recipe_id: {'add': set[str], 'remove': set[str]}} — manual tag deltas."""
    out: dict[str, dict] = {}
    for r in con.execute("SELECT recipe_id, add_tags, remove_tags FROM attribute_overrides"):
        out[r["recipe_id"]] = {"add": set(json.loads(r["add_tags"] or "[]")),
                               "remove": set(json.loads(r["remove_tags"] or "[]"))}
    return out


def set_attribute_override(con: sqlite3.Connection, recipe_id: str,
                           add: set[str] | list[str], remove: set[str] | list[str]) -> None:
    con.execute(
        "INSERT INTO attribute_overrides (recipe_id, add_tags, remove_tags, updated_at) "
        "VALUES (?,?,?,?) ON CONFLICT(recipe_id) DO UPDATE SET "
        "add_tags=excluded.add_tags, remove_tags=excluded.remove_tags, updated_at=excluded.updated_at",
        (recipe_id, _dump(sorted(set(add))), _dump(sorted(set(remove))), _now()))


def load_catalog(con: sqlite3.Connection) -> Catalog:
    rows = con.execute("SELECT * FROM recipes").fetchall()
    cat = Catalog([_row_to_recipe(r) for r in rows])
    # Layer manual tag overrides on top of the auto-classification.
    for rid, ov in attribute_overrides(con).items():
        r = cat.get(rid)
        if r is not None:
            r.attributes = (set(r.auto_attributes) - ov["remove"]) | ov["add"]
    return cat


def content_hashes(con: sqlite3.Connection) -> dict[str, str]:
    return {r["id"]: r["content_hash"]
            for r in con.execute("SELECT id, content_hash FROM recipes")}


def category_overrides(con: sqlite3.Connection) -> dict[str, str]:
    return {r["recipe_id"]: r["category"]
            for r in con.execute("SELECT recipe_id, category FROM category_overrides")}


def set_category_override(con: sqlite3.Connection, recipe_id: str, category: str) -> None:
    con.execute(
        "INSERT INTO category_overrides (recipe_id, category, updated_at) VALUES (?,?,?) "
        "ON CONFLICT(recipe_id) DO UPDATE SET category=excluded.category, updated_at=excluded.updated_at",
        (recipe_id, category, _now()))


def title_overrides(con: sqlite3.Connection) -> dict[str, str]:
    return {r["recipe_id"]: r["title"]
            for r in con.execute("SELECT recipe_id, title FROM title_overrides")}


def set_title_override(con: sqlite3.Connection, recipe_id: str, title: str) -> None:
    con.execute(
        "INSERT INTO title_overrides (recipe_id, title, updated_at) VALUES (?,?,?) "
        "ON CONFLICT(recipe_id) DO UPDATE SET title=excluded.title, updated_at=excluded.updated_at",
        (recipe_id, title, _now()))


def content_overrides(con: sqlite3.Connection) -> dict[str, dict]:
    """{recipe_id: {ingredients|instructions|shopping_list: list | None}}."""
    out: dict[str, dict] = {}
    for r in con.execute(
            "SELECT recipe_id, ingredients, instructions, shopping_list FROM content_overrides"):
        out[r["recipe_id"]] = {
            "ingredients": json.loads(r["ingredients"]) if r["ingredients"] is not None else None,
            "instructions": json.loads(r["instructions"]) if r["instructions"] is not None else None,
            "shopping_list": json.loads(r["shopping_list"]) if r["shopping_list"] is not None else None,
        }
    return out


def set_content_override(con: sqlite3.Connection, recipe_id: str, *,
                         ingredients: list | None = None, instructions: list | None = None,
                         shopping_list: list | None = None) -> None:
    con.execute(
        "INSERT INTO content_overrides (recipe_id, ingredients, instructions, shopping_list, updated_at) "
        "VALUES (?,?,?,?,?) ON CONFLICT(recipe_id) DO UPDATE SET "
        "ingredients=excluded.ingredients, instructions=excluded.instructions, "
        "shopping_list=excluded.shopping_list, updated_at=excluded.updated_at",
        (recipe_id,
         _dump(ingredients) if ingredients is not None else None,
         _dump(instructions) if instructions is not None else None,
         _dump(shopping_list) if shopping_list is not None else None, _now()))


def get_icon_subjects(con: sqlite3.Connection) -> dict[str, str]:
    """{recipe_id: llm_subject} — the distinctive per-recipe icon descriptions."""
    return {r["recipe_id"]: r["subject"]
            for r in con.execute("SELECT recipe_id, subject FROM icon_prompts")
            if r["subject"]}


def set_icon_subject(con: sqlite3.Connection, recipe_id: str, subject: str) -> None:
    con.execute(
        "INSERT INTO icon_prompts (recipe_id, subject, updated_at) VALUES (?,?,?) "
        "ON CONFLICT(recipe_id) DO UPDATE SET subject=excluded.subject, updated_at=excluded.updated_at",
        (recipe_id, subject.strip(), _now()))


def inventory_lists(con: sqlite3.Connection, table: str, owner: int = OWNER_ID) -> dict:
    """``{'on_hand': [...], 'staple': [...], 'unavailable': [...]}`` from an
    inventory table (``pantry_items`` | ``bar_items``)."""
    if table not in ("pantry_items", "bar_items"):
        raise ValueError(f"bad inventory table: {table}")
    out: dict[str, list] = {"on_hand": [], "staple": [], "unavailable": []}
    for r in con.execute(f"SELECT name, kind FROM {table} WHERE owner_id=?", (owner,)):
        out.setdefault(r["kind"], []).append(r["name"])
    return out
