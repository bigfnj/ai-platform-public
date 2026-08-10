"""Recipe browse / search / detail, plus catalog facets and per-recipe icon."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

import re

from recipe_book import config, db, overlays, state
from recipe_book import semantic as sem
from recipe_book.api import deps

router = APIRouter()

# --- natural-language query parsing for AI search ---------------------------
# Turn "recipes with lettuce and garlic, but exclude cilantro" into structured
# include/exclude terms. Words after a negation marker become exclusions; framing/
# command words are dropped so "show me all recipes with chicken" -> include ["chicken"].
_STOP = {
    "a", "an", "the", "with", "and", "or", "but", "of", "for", "to", "in", "on", "at", "by", "as",
    "then", "also", "just", "very", "really", "too", "still", "including", "include", "includes",
    "some", "any", "all", "both", "every", "each", "that", "this", "these", "those",
    "things", "thing", "stuff", "something", "anything", "one", "only", "plus",
    "show", "find", "get", "give", "list", "display", "search", "see", "want", "need",
    "looking", "look", "make", "made", "making", "have", "has", "had", "using", "use",
    "recipe", "recipes", "dish", "dishes", "meal", "meals", "food", "foods", "idea", "ideas",
    "me", "my", "mine", "we", "our", "us", "you", "your", "please", "can", "could",
    "would", "like", "about", "kind", "kinds", "type", "types", "which", "what",
}
_NEG = re.compile(r"\b(?:without|w/o|excluding|excludes|exclude|except|but\s+not|minus|omit|omitting|hold\s+the|no\s+more)\b")


def _content_terms(s: str) -> list[str]:
    return [t for t in re.findall(r"[a-z]+", s.lower()) if len(t) >= 3 and t not in _STOP]


def _parse_search(q: str) -> tuple[list[str], list[str]]:
    """(include, exclude) terms from a natural-language recipe query. Everything after the
    first negation marker (without / exclude / but not / …) becomes an exclusion."""
    ql = q.lower()
    m = _NEG.search(ql)
    if m:
        return _content_terms(ql[:m.start()]), _content_terms(ql[m.end():])
    return _content_terms(ql), []


# --- dietary / allergen concept -> attribute filters ------------------------
# Map phrases like "vegetarian", "gluten free", "no pork" to the recipe attribute tags
# (from attributes.classify). These are the concept queries plain keyword search can't do.
_REQUIRE_ATTR = [
    (re.compile(r"\bvegan\b"), "vegan"),
    (re.compile(r"\bvegetarian\b|\bveggie\b|\bmeatless\b|\bno meat\b|\bmeat[- ]?free\b"), "vegetarian"),
    (re.compile(r"\bpescatarian\b|\bpescetarian\b"), "pescatarian"),
    (re.compile(r"\bgluten[- ]?free\b|\bceliac\b|\bcoeliac\b"), "gluten-free"),
    (re.compile(r"\bdairy[- ]?free\b|\bno dairy\b|\blactose[- ]?free\b|\bnon[- ]?dairy\b"), "dairy-free"),
    (re.compile(r"\bnut[- ]?free\b|\bno nuts?\b"), "nut-free"),
    (re.compile(r"\begg[- ]?free\b|\beggless\b"), "egg-free"),
    (re.compile(r"\bsoy[- ]?free\b|\bno soy\b"), "soy-free"),
    (re.compile(r"\bspicy\b|\bspiciest\b"), "spicy"),
]
_EXCLUDE_ATTR = [
    (re.compile(r"\bno pork\b|\bpork[- ]?free\b|\bno bacon\b"), "contains-pork"),
    (re.compile(r"\bno beef\b|\bbeef[- ]?free\b"), "contains-beef"),
    (re.compile(r"\bno (?:chicken|poultry)\b|\bpoultry[- ]?free\b"), "contains-poultry"),
    (re.compile(r"\bno shellfish\b|\bshellfish[- ]?free\b|\bno shrimp\b"), "contains-shellfish"),
    (re.compile(r"\bno fish\b|\bfish[- ]?free\b"), "contains-fish"),
    (re.compile(r"\bno sesame\b|\bsesame[- ]?free\b"), "contains-sesame"),
    (re.compile(r"\bno coconut\b|\bcoconut[- ]?free\b"), "contains-coconut"),
    (re.compile(r"\bno peanuts?\b|\bpeanut[- ]?free\b"), "contains-peanut"),
]


def _dietary_filters(q: str) -> tuple[set[str], set[str], str]:
    """(require_attrs, exclude_attrs, query with those phrases removed for lexical parsing)."""
    stripped = q.lower()
    req: set[str] = set()
    exc: set[str] = set()
    for rx, attr in _REQUIRE_ATTR:
        if rx.search(stripped):
            req.add(attr)
            stripped = rx.sub(" ", stripped)
    for rx, attr in _EXCLUDE_ATTR:
        if rx.search(stripped):
            exc.add(attr)
            stripped = rx.sub(" ", stripped)
    return req, exc, stripped.strip()


@router.get("/api/stats")
def stats() -> dict:
    return state.catalog().stats()


@router.get("/api/categories")
def categories() -> dict:
    return {"categories": state.catalog().categories}


@router.get("/api/spirits")
def spirits() -> dict:
    return {"spirits": state.catalog().spirits}


@router.get("/api/recipes")
def list_recipes(q: str = "", kind: str = "all", category: str | None = None,
                 spirit: str | None = None, fav: bool = False, tag: int | None = None,
                 semantic: bool = False, limit: int = 60, offset: int = 0,
                 owner_id: int = Depends(deps.owner_id)) -> dict:
    con = db.connect()
    try:
        ids: set[str] | None = None
        if fav:
            ids = {r["recipe_id"] for r in con.execute(
                "SELECT recipe_id FROM favorites WHERE owner_id=?", (owner_id,))}
        if tag is not None:
            tids = {r["recipe_id"] for r in con.execute(
                "SELECT recipe_id FROM recipe_tags WHERE owner_id=? AND tag_id=?",
                (owner_id, tag))}
            ids = tids if ids is None else (ids & tids)
        cat = state.catalog()

        # Semantic path: rank by embedding similarity, then apply the same filters,
        # preserving rank order. Falls back to lexical if the index/broker isn't ready.
        if semantic and q.strip():
            try:
                # Rank on the positive part only — the words after "exclude/without/…" shouldn't
                # pull their own topic into the ranking (they're removed lexically below).
                sem_q = _NEG.split(q, 1)[0].strip() or q
                ranked = sem.query(sem_q, top_k=500)
            except Exception:
                ranked = []
            if ranked:
                # A "chicken" search must not surface desserts just because they're food-shaped,
                # and multi-term / negative queries must be honored. Parse into include + exclude
                # terms, then over the semantically-ranked candidates (above a similarity FLOOR):
                # drop anything containing an EXCLUDE term, and prefer recipes that contain ALL the
                # include terms ("lettuce AND garlic"), then any of them, then similarity-only.
                req_attrs, exc_attrs, q_lex = _dietary_filters(q)
                inc, exc = _parse_search(q_lex)
                floor = max(0.38, 0.75 * ranked[0][1])

                def _hay(r) -> str:
                    return f"{r.title} {r.category} {' '.join(r.ingredients)}".lower()

                def _has(term: str, hay: str) -> bool:
                    return re.search(r"\b" + re.escape(term), hay) is not None

                all_m: list = []
                any_m: list = []
                floored: list = []
                for rid, score in ranked:
                    if score < floor:
                        break
                    r = cat.get(rid)
                    if not r:
                        continue
                    if kind != "all" and r.kind != kind:
                        continue
                    if category and r.category != category:
                        continue
                    if spirit and spirit not in r.base_spirits:
                        continue
                    if ids is not None and rid not in ids:
                        continue
                    if req_attrs and not req_attrs <= r.attributes:
                        continue  # dietary requirement (vegetarian / gluten-free / …)
                    if exc_attrs and exc_attrs & r.attributes:
                        continue  # dietary exclusion (no pork / no shellfish / …)
                    hay = _hay(r)
                    if exc and any(_has(t, hay) for t in exc):
                        continue  # honor exclusions ("but not pasta")
                    floored.append(r)
                    if inc:
                        if all(_has(t, hay) for t in inc):
                            all_m.append(r)
                        elif any(_has(t, hay) for t in inc):
                            any_m.append(r)
                # All-terms match wins ("lettuce AND garlic"); then partial; else similarity-only
                # (a conceptual query whose words aren't literal ingredients). Exclusions always apply.
                hits_all = (all_m or any_m or floored) if inc else floored
                total = len(hits_all)
                page = hits_all[offset: offset + max(1, min(limit, 500))]
                items = overlays.decorate(con, [h.summary() for h in page], owner=owner_id)
                return {"items": items, "total": total, "limit": limit, "offset": offset, "semantic": True}

        hits, total = cat.search(q, kind=kind, category=category, spirit=spirit,
                                 ids=ids, limit=limit, offset=offset)
        items = overlays.decorate(con, [h.summary() for h in hits], owner=owner_id)
        return {"items": items, "total": total, "limit": limit, "offset": offset, "semantic": False}
    finally:
        con.close()


@router.get("/api/recipes/{recipe_id}")
def get_recipe(recipe_id: str, owner_id: int = Depends(deps.owner_id)) -> dict:
    r = state.catalog().get(recipe_id)
    if not r:
        raise HTTPException(status_code=404, detail="recipe not found")
    con = db.connect()
    try:
        return overlays.decorate(con, [r.detail()], owner=owner_id)[0]
    finally:
        con.close()



class CategoryReq(BaseModel):
    category: str


@router.put("/api/recipes/{recipe_id}/category")
def set_category(recipe_id: str, req: CategoryReq,
                 _admin: deps.Identity = Depends(deps.require_admin)) -> dict:
    """Manually move a recipe to a category (admin only). Persisted as an override so it
    survives rebuilds and wins over auto-classification."""
    cat = req.category.strip()
    if not cat:
        raise HTTPException(status_code=400, detail="empty category")
    kind = "beverage" if cat.lower() == "beverages" else "meal"
    con = db.connect()
    try:
        con.execute("UPDATE recipes SET category=?, kind=? WHERE id=?", (cat, kind, recipe_id))
        db.set_category_override(con, recipe_id, cat)
        con.commit()
    finally:
        con.close()
    state.reload()
    return {"recipe_id": recipe_id, "category": cat, "kind": kind}


class AttributesReq(BaseModel):
    attributes: list[str]


@router.put("/api/recipes/{recipe_id}/attributes")
def set_attributes(recipe_id: str, req: AttributesReq,
                   _admin: deps.Identity = Depends(deps.require_admin)) -> dict:
    """Manually set a recipe's dietary/allergen tags (admin only). Stored as an add/remove
    delta vs the auto-classification, so it survives re-ingest and later ingredient edits."""
    from recipe_book import attributes as attrmod
    r = state.catalog().get(recipe_id)
    if r is None:
        raise HTTPException(status_code=404, detail="recipe not found")
    desired = {t for t in req.attributes if t in set(attrmod.ALL_TAGS)}
    auto = set(r.auto_attributes)
    con = db.connect()
    try:
        db.set_attribute_override(con, recipe_id, desired - auto, auto - desired)
        con.commit()
    finally:
        con.close()
    state.reload()
    return {"recipe_id": recipe_id, "attributes": sorted(desired)}


@router.get("/api/icon/{recipe_id}")
def icon(recipe_id: str):
    """Serve the generated SDXL clipart for a recipe, if it exists (else 404 →
    the frontend falls back to a category glyph)."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", recipe_id):  # confine to ICONS_DIR (no traversal)
        return Response(status_code=404)
    p = config.ICONS_DIR / f"{recipe_id}.png"
    if p.exists():
        return FileResponse(str(p), media_type="image/png")
    return Response(status_code=404)
