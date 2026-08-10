"""Recipe/cocktail assistant — buffered broker chat, optionally grounded in a
specific recipe. Modes: ask · substitute · scale · menu · pairing."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from recipe_book import broker, state

router = APIRouter()

_SYSTEM = ("You are a concise, practical kitchen & cocktail assistant inside a personal "
           "recipe app. Answer in Markdown, be specific and brief, and respect the recipe's "
           "style. When unsure, say so rather than inventing quantities.")


def _recipe_context(recipe_id: str | None) -> str:
    if not recipe_id:
        return ""
    r = state.catalog().get(recipe_id)
    if not r:
        return ""
    parts = [f"Recipe: {r.title}  (category {r.category}, {r.kind})"]
    if r.meta:
        parts.append(r.meta)
    if r.base_spirits:
        parts.append("Base spirits: " + ", ".join(r.base_spirits))
    if r.ingredients:
        parts.append("Ingredients:\n" + "\n".join(f"- {i}" for i in r.ingredients))
    if r.instructions:
        parts.append("Method:\n" + "\n".join(f"{n}. {s}" for n, s in enumerate(r.instructions, 1)))
    return "\n".join(parts)


class AssistReq(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    mode: str = "ask"                 # ask | substitute | scale | menu | pairing
    recipe_id: str | None = None
    prompt: str = ""
    servings: int | None = None
    model: str | None = None


def _task(req: AssistReq) -> str:
    if req.mode == "substitute":
        return ("Suggest smart substitutions for ingredients the cook may lack, noting the "
                "flavor or technique impact of each swap.")
    if req.mode == "double":
        return ("Double this recipe: multiply every ingredient quantity by 2, keeping the ratios. "
                "Give the full doubled ingredient list, and flag anything that does NOT simply "
                "double (pan/pot size, cook time, leavening, and salt & strong seasonings).")
    if req.mode == "scale":
        return (f"Rescale this recipe to {req.servings or 4} servings. Give the adjusted "
                "quantities and flag any timing, pan-size, or seasoning caveats.")
    if req.mode == "menu":
        return ("Build a cohesive menu around this dish: complementary sides/courses and one "
                "drink or cocktail pairing, each with a one-line reason.")
    if req.mode == "pairing":
        return ("Suggest 2–3 pairings that complement this (food for a drink, or a drink for a "
                "dish), each with a one-line rationale.")
    return req.prompt or "Answer the user's question about this recipe."


@router.post("/api/assistant")
def assistant(req: AssistReq) -> dict:
    ctx = _recipe_context(req.recipe_id)
    task = _task(req)
    extra = f"\n\nUser note: {req.prompt}" if (req.prompt and req.mode != "ask") else ""
    user = (f"{ctx}\n\n{task}{extra}" if ctx else f"{task}{extra}")
    try:
        markdown = broker.chat(req.model or broker.ASSISTANT_MODEL, [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user},
        ], options={"temperature": 0.5, "num_ctx": 8192})
    except broker.BrokerError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"markdown": markdown, "mode": req.mode}


@router.get("/api/models")
def models() -> dict:
    return broker.models()
