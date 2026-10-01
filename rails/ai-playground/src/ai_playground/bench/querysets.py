"""Query-set management for the Embedding Lab.

A query set is a list of ``{q, targets:[source…]}`` — a natural-language query paired with the
corpus source name(s) that are correct answers. Seed sets are baked into the image and loaded
on first boot; users upload their own (owner-scoped) as JSON. Targets are matched against a
corpus chunk's ``source`` (its file name), so a query set pairs with a corpus.
"""
from __future__ import annotations

import json
import re

from ai_playground import config, db


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return s or "queryset"


def _normalize(raw: dict | list) -> tuple[str, list[dict]]:
    """Accept either {"name", "queries":[...]} or a bare [...] list of query objects."""
    if isinstance(raw, list):
        name, items = "Uploaded set", raw
    else:
        name, items = (raw.get("name") or "Uploaded set"), (raw.get("queries") or [])
    queries: list[dict] = []
    for it in items:
        q = (it.get("q") or it.get("query") or "").strip()
        targets = it.get("targets") or it.get("target") or []
        if isinstance(targets, str):
            targets = [targets]
        if q and targets:
            queries.append({"q": q, "targets": [str(t) for t in targets]})
    return name, queries


def ensure_seeds(con) -> None:
    """Load every seed query-set JSON not already present (kind='seed', shared)."""
    d = config.SEED_QUERYSETS_DIR
    if not d.exists():
        return
    for path in sorted(d.glob("*.json")):
        slug = _slug(path.stem)
        if db.queryset_exists(con, slug, None):
            continue
        try:
            # utf-8-SIG: these seeds are hand-edited on a Windows box, and a BOM makes
            # json.loads raise straight into the `continue` below — so the queryset silently
            # never appears, which reads as "the seed is missing" rather than "it is BOM'd".
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:  # noqa: BLE001 — skip a malformed seed, don't crash boot
            continue
        name, queries = _normalize(raw)
        if queries:
            db.add_queryset(con, slug=slug, name=name or path.stem, kind="seed",
                            owner=None, queries=queries)


def ingest(con, owner: str | None, name: str, raw: dict | list) -> dict:
    disp, queries = _normalize(raw)
    name = (name or disp).strip()
    if not queries:
        raise ValueError("no usable queries found (need objects with 'q' and 'targets')")
    base = _slug(name)
    slug, n = base, 2
    while db.queryset_exists(con, slug, owner):
        slug, n = f"{base}-{n}", n + 1
    qsid = db.add_queryset(con, slug=slug, name=name or base, kind="user",
                           owner=owner, queries=queries)
    return {"id": qsid, "slug": slug, "name": name or base, "queries": len(queries)}


def delete(con, qsid: int, owner: str | None, is_admin: bool) -> None:
    # any_owner: this is the one path that has to SEE a foreign set in order to refuse it (or,
    # as admin, remove it). It makes the ownership decision itself, right below.
    meta = db.get_queryset(con, qsid, owner, any_owner=True)
    if meta is None:
        # LookupError, not a silent return: see the note in corpora.delete. The route maps
        # this and PermissionError to the same 404 so the two are indistinguishable.
        raise LookupError("no such query set")
    if meta["kind"] == "seed" and not is_admin:
        raise PermissionError("seed query sets are read-only")
    # No `owner is not None` guard: a null owner owns nothing, and skipping the check for it
    # let a caller with a blank X-Platform-User delete any user's query set.
    if (meta["kind"] == "user" and meta["owner"] not in (None, owner)
            and not is_admin):
        raise PermissionError("not your query set")
    db.delete_queryset(con, qsid)
