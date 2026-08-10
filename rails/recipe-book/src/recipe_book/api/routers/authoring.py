"""Author recipes: AI-assisted draft from pasted text (or manual fields), an
interactive "clarify" refine loop, commit-to-corpus, and inline content edits.

Drafts are never persisted — they live in the client until the user commits. A
commit writes a real card into the markdown corpus (path-stable id); an edit writes
a content override reapplied at ingest. All model work goes through the broker.
"""
from __future__ import annotations

import difflib
import json
import re

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from recipe_book import authoring, bar, broker, db, extraction, icons, ingest, state
from recipe_book.api import deps
from recipe_book.catalog import primary_spirit, recipe_id as make_id

router = APIRouter()

# Where a non-admin's contributed recipe lands: the "To Try" staging bucket (already
# excluded from the meal-plan pools + auto-reclassification). Admins review/recategorize.
CONTRIB_CATEGORY = "To Try"

_DISTILL_SYS = (
    "You extract ONE structured recipe from the provided text or image(s). Return STRICT JSON with keys: "
    "title (string), meta (string: a short yield/time line, may be empty), "
    "ingredients (array, one ingredient per line, keep amounts and units EXACTLY), "
    "instructions (array, one step each), "
    "shopping_list (array, optional grocery items; may be empty), "
    "source (string: where it came from — site, publication, or author if visible; else empty), "
    "suggest_kind ('meal' or 'beverage'), "
    "suggest_category (best dish category, e.g. Entrees, Pasta, Soups, Breakfast, Salads & Dressings, "
    "Deserts, BBQ, Side Dishes, Thai; for a drink use Beverages), "
    "suggest_spirit (drink only: base spirit — Gin/Vodka/Rum/Whiskey/Tequila/Mezcal/Brandy/"
    "Wine & Sparkling/Liqueur/Non-Alcoholic; else empty), "
    "questions (array: anything ambiguous worth confirming; empty if none). "
    "Do NOT invent ingredients or steps. Preserve quantities and units verbatim."
)
_REFINE_SYS = (
    "You are refining a draft recipe held as JSON. Apply ONLY the user's requested change and "
    "return the FULL updated recipe as STRICT JSON with the same keys "
    "(title, meta, ingredients, instructions, shopping_list, questions). "
    "Keep everything the user did not ask to change. Preserve amounts and units verbatim."
)


def _clean_list(v) -> list[str]:
    return [str(x).strip() for x in (v or []) if str(x).strip()]


def _normalize(d: dict, *, kind: str, category: str, source: str = "") -> dict:
    return {
        "title": (d.get("title") or "").strip(),
        "meta": (d.get("meta") or "").strip(),
        "kind": kind,
        "category": category,
        "ingredients": _clean_list(d.get("ingredients")),
        "instructions": _clean_list(d.get("instructions")),
        "shopping_list": _clean_list(d.get("shopping_list")),
        "source": source,
    }


def distill(*, text: str = "", images: list[str] | None = None,
            kind_hint: str = "", category_hint: str = "") -> dict:
    """Text or vision -> {draft, questions, suggest}. When the caller has no explicit
    Kitchen/Bar + category yet (the import flow), the draft falls back to the model's
    suggestions so the form arrives pre-filled."""
    images = images or []
    hint = (f"Kind hint: {kind_hint or '(unknown - you decide)'} | "
            f"Category hint: {category_hint or '(unknown - you decide)'}")
    if images:
        user_msg = {"role": "user", "images": images,
                    "content": f"{hint}\n\nExtract the recipe shown in the attached image(s)."}
        model = broker.VISION_MODEL
    else:
        if not text.strip():
            raise HTTPException(status_code=400, detail="nothing to distill")
        user_msg = {"role": "user", "content": f"{hint}\n\nRECIPE TEXT:\n{text}"}
        model = broker.ASSISTANT_MODEL
    try:
        d = broker.chat_json(model, [{"role": "system", "content": _DISTILL_SYS}, user_msg])
    except broker.BrokerError as exc:
        raise HTTPException(status_code=503, detail=f"assistant unavailable: {exc}")

    s_kind = "beverage" if str(d.get("suggest_kind", "")).lower().startswith("bev") else "meal"
    suggest = {"kind": s_kind, "category": (d.get("suggest_category") or "").strip(),
               "spirit": (d.get("suggest_spirit") or "").strip(), "source": (d.get("source") or "").strip()}
    kind = kind_hint or suggest["kind"]
    category = "Beverages" if kind == "beverage" else (category_hint or suggest["category"] or "Entrees")
    draft = _normalize(d, kind=kind, category=category, source=suggest["source"])
    return {"draft": draft, "questions": _clean_list(d.get("questions")), "suggest": suggest}


class DraftReq(BaseModel):
    mode: str = "paste"        # paste | manual
    kind: str = "meal"         # meal | beverage
    category: str = ""
    text: str = ""             # paste mode
    title: str = ""            # manual mode
    meta: str = ""
    ingredients: list[str] = []
    instructions: list[str] = []
    source: str = ""


@router.post("/api/recipes/draft")
def draft(req: DraftReq) -> dict:
    """Turn pasted text (AI) or manual fields into an uncommitted structured draft."""
    if req.mode == "manual":
        d = _normalize(
            {"title": req.title, "meta": req.meta, "ingredients": req.ingredients,
             "instructions": req.instructions, "shopping_list": []},
            kind=req.kind, category=req.category, source=req.source)
        return {"draft": d, "questions": [],
                "suggest": {"kind": req.kind, "category": req.category, "spirit": "", "source": req.source}}
    return distill(text=req.text, kind_hint=req.kind, category_hint=req.category)


class RefineReq(BaseModel):
    draft: dict
    message: str


@router.post("/api/recipes/draft/refine")
def refine(req: RefineReq) -> dict:
    """Carry the draft + the user's chat message back through the assistant."""
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="empty message")
    kind = req.draft.get("kind", "meal")
    category = req.draft.get("category", "")
    payload = {k: req.draft.get(k) for k in ("title", "meta", "ingredients", "instructions", "shopping_list")}
    user = f"CURRENT DRAFT JSON:\n{json.dumps(payload, ensure_ascii=False)}\n\nUSER REQUEST:\n{req.message}"
    try:
        d = broker.chat_json(broker.ASSISTANT_MODEL,
                             [{"role": "system", "content": _REFINE_SYS},
                              {"role": "user", "content": user}])
    except broker.BrokerError as exc:
        raise HTTPException(status_code=503, detail=f"assistant unavailable: {exc}")
    merged = {
        "title": d.get("title") or req.draft.get("title", ""),
        "meta": d.get("meta") if d.get("meta") is not None else req.draft.get("meta", ""),
        "ingredients": d.get("ingredients") or req.draft.get("ingredients", []),
        "instructions": d.get("instructions") or req.draft.get("instructions", []),
        "shopping_list": d.get("shopping_list") if d.get("shopping_list") is not None else req.draft.get("shopping_list", []),
    }
    return {"draft": _normalize(merged, kind=kind, category=category, source=req.draft.get("source", "")),
            "questions": _clean_list(d.get("questions"))}


class CommitReq(BaseModel):
    title: str
    kind: str = "meal"
    category: str
    meta: str = ""
    ingredients: list[str]
    instructions: list[str]
    shopping_list: list[str] = []
    source: str = ""


@router.post("/api/recipes")
def create_recipe(req: CommitReq, background: BackgroundTasks,
                  ident: deps.Identity = Depends(deps.identity)) -> dict:
    """Commit a draft: write a real card into the corpus, ingest it, queue its icon.

    Anyone signed in may contribute a recipe, but a non-admin's is forced into the
    "To Try" staging category — they cannot file it directly into the curated book (only
    admins pick a category, here or later via the category-move edit)."""
    if not req.title.strip():
        raise HTTPException(status_code=400, detail="title required")
    # Non-admins can only contribute to the To-Try bucket; admins choose freely.
    contributor = ident.user is not None and not ident.is_admin
    category = CONTRIB_CATEGORY if contributor else req.category
    if not category.strip():
        raise HTTPException(status_code=400, detail="category required")
    ings, steps = _clean_list(req.ingredients), _clean_list(req.instructions)
    if not ings and not steps:
        raise HTTPException(status_code=400, detail="a recipe needs ingredients or steps")
    md = authoring.to_markdown(title=req.title, meta=req.meta, ingredients=ings,
                               instructions=steps, shopping_list=req.shopping_list, source=req.source)
    rel = authoring.write_card(category=category, title=req.title, markdown=md)
    rid = make_id(rel)
    con = db.connect()
    try:
        # honor the chosen category over auto-classification (Thai pass etc.)
        db.set_category_override(con, rid, category)
        ingest.ingest(con)  # incremental: parses just the new file, applies the override
        con.commit()
    finally:
        con.close()
    state.reload()
    background.add_task(_gen_icon, rid)
    return {"id": rid, "rel_path": rel, "category": category, "kind": req.kind}


class TitleReq(BaseModel):
    title: str


@router.put("/api/recipes/{recipe_id}/title")
def edit_title(recipe_id: str, req: TitleReq,
               _admin: deps.Identity = Depends(deps.require_admin)) -> dict:
    """Rename a recipe's display title — admin only. Persisted as a title override (survives
    rebuild) and applied to the live row. The recipe id is path-derived, so the stable id,
    file path, and any favorites / ratings / planner links are left untouched by a rename."""
    r = state.catalog().get(recipe_id)
    if not r:
        raise HTTPException(status_code=404, detail="recipe not found")
    title = req.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="title required")
    r.title = title
    con = db.connect()
    try:
        db.set_title_override(con, recipe_id, title)
        con.execute("UPDATE recipes SET title=? WHERE id=?", (title, recipe_id))
        con.commit()
    finally:
        con.close()
    state.reload()
    return {"updated": recipe_id, "title": title}


class ContentReq(BaseModel):
    ingredients: list[str]
    instructions: list[str]
    shopping_list: list[str] | None = None


@router.put("/api/recipes/{recipe_id}/content")
def edit_content(recipe_id: str, req: ContentReq,
                 _admin: deps.Identity = Depends(deps.require_admin)) -> dict:
    """Overwrite a recipe's ingredients / method (and optionally its shopping list) —
    admin only. Persisted as a content override (survives rebuild) and applied to the live row."""
    r = state.catalog().get(recipe_id)
    if not r:
        raise HTTPException(status_code=404, detail="recipe not found")
    ings, steps = _clean_list(req.ingredients), _clean_list(req.instructions)
    shop = None if req.shopping_list is None else _clean_list(req.shopping_list)

    # recompute bar enrichment from the new ingredients so a drink's base spirit / glass stay right
    r.ingredients, r.instructions = ings, steps
    if shop is not None:
        r.shopping_list = shop
    if r.kind == "beverage":
        bar.enrich(r)
        r.primary_spirit = primary_spirit(r.base_spirits)

    con = db.connect()
    try:
        db.set_content_override(con, recipe_id, ingredients=ings, instructions=steps, shopping_list=shop)
        con.execute(
            "UPDATE recipes SET ingredients=?, instructions=?, shopping_list=?, "
            "base_spirits=?, glass=?, technique=? WHERE id=?",
            (db._dump(r.ingredients), db._dump(r.instructions), db._dump(r.shopping_list),
             db._dump(r.base_spirits), r.glass, r.technique, recipe_id))
        con.commit()
    finally:
        con.close()
    state.reload()
    return {"updated": recipe_id, "ingredient_count": len(ings), "step_count": len(steps)}


# --- Phase 2: import from a URL / files / photos ---------------------------------

class UrlReq(BaseModel):
    url: str
    kind: str = ""
    category: str = ""


@router.post("/api/recipes/extract/url")
def extract_url_ep(req: UrlReq) -> dict:
    """Fetch a recipe URL, strip it to article text, distill to a draft (source = the URL)."""
    if not req.url.strip():
        raise HTTPException(status_code=400, detail="url required")
    try:
        text = extraction.extract_url(req.url)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"could not fetch that page: {exc}")
    if len(text) < 40:
        raise HTTPException(status_code=422, detail="no recipe text found at that URL")
    out = distill(text=text, kind_hint=req.kind, category_hint=req.category)
    if not out["draft"]["source"]:
        out["draft"]["source"] = out["suggest"]["source"] = req.url.strip()
    return out


@router.post("/api/recipes/extract/files")
def extract_files_ep(files: list[UploadFile] = File(...),
                     kind: str = Form(""), category: str = Form("")) -> dict:
    """Extract one recipe from one or more uploaded files/photos (multiple images are read
    together as a single recipe). For batch import, the client posts one file per call."""
    texts: list[str] = []
    images: list[str] = []
    for f in files:
        data = f.file.read()
        res = extraction.extract_file(f.filename or "", data)
        if res["text"]:
            texts.append(res["text"])
        images.extend(res["images"])
    text = "\n\n".join(texts)
    if not text and not images:
        raise HTTPException(status_code=422, detail="could not read anything from those files")
    return distill(text=text, images=images[:6], kind_hint=kind, category_hint=category)


_UNIT_RE = re.compile(
    r"\b(cups?|tbsp|tsp|teaspoons?|tablespoons?|oz|ounces?|lbs?|pounds?|g|grams?|kg|ml|l|"
    r"cloves?|cans?|packages?|pinch|dash|slices?|sprigs?)\b")


def _norm_ing(s: str) -> str:
    s = _UNIT_RE.sub(" ", re.sub(r"^[\d\s./-]+", "", s.lower().replace("**", "")))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]", " ", s)).strip()


class DupReq(BaseModel):
    title: str
    ingredients: list[str] = []
    exclude_id: str = ""


@router.post("/api/recipes/duplicate_check")
def duplicate_check(req: DupReq) -> dict:
    """Flag existing recipes that look like near-duplicates of a draft (title similarity
    and/or ingredient overlap), so the user can Replace or Keep both before saving."""
    title = req.title.strip().lower()
    if not title:
        return {"matches": []}
    ing = {x for x in (_norm_ing(i) for i in req.ingredients) if x}
    matches = []
    for r in state.catalog().recipes:
        if r.id == req.exclude_id:
            continue
        tscore = difflib.SequenceMatcher(None, title, r.title.lower()).ratio()
        oscore = 0.0
        if ing and r.ingredients:
            rset = {x for x in (_norm_ing(i) for i in r.ingredients) if x}
            if rset:
                oscore = len(ing & rset) / len(ing | rset)
        if tscore >= 0.72 or (tscore >= 0.5 and oscore >= 0.5):
            matches.append({"id": r.id, "title": r.title, "category": r.category,
                            "score": round(max(tscore, 0.5 * tscore + 0.5 * oscore), 2)})
    matches.sort(key=lambda m: -m["score"])
    return {"matches": matches[:5]}


def _gen_icon(recipe_id: str) -> None:
    con = db.connect()
    try:
        icons.generate(con, ids={recipe_id})
    except Exception:
        pass
    finally:
        con.close()
