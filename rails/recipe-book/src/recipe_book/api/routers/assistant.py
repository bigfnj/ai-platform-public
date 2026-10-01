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
    model: str | None = None          # optional override — validated by _resolve_model, never
                                      # passed to the broker as given


def _resolve_model(requested: str | None) -> str:
    """The model a broker call runs on: the caller's pick, else the rail's own default.

    The default is the server's (``@recipe`` unless pinned) and is trusted as configuration.
    A CLIENT-supplied name is not: it used to go straight through as
    ``broker.chat(req.model or ...)``, so the caller chose what the shared 4090 loads — the
    same hole finance closed on 2026-08-06. It is validated against what the broker actually
    offers, and the two kinds of reference are checked against the two different lists:

    * a concrete tag must be an INSTALLED model;
    * a ``@role`` must be a role the broker DEFINES — roles never appear in the model list, so
      finance's version rejects every one of them (including its own default). Do not "fix"
      this by folding roles into the installed check.

    When the broker is unreachable there is nothing to validate against, so the check is
    skipped and the chat call below fails cleanly with its own 502.
    """
    model = (requested or "").strip()
    if not model:
        return broker.ASSISTANT_MODEL
    if model.startswith("@"):
        try:
            known = {str(r.get("role") or "") for r in broker.roles()}
        except broker.BrokerError:
            known = set()
        if known and model[1:] not in known:
            raise HTTPException(status_code=400, detail=f"no such model role '{model}'")
        return model
    installed = {m["name"] for m in broker.picker_models().get("models", []) if m.get("name")}
    if installed and model not in installed:
        raise HTTPException(status_code=400, detail=f"model '{model}' is not installed")
    return model


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
    model = _resolve_model(req.model)
    ctx = _recipe_context(req.recipe_id)
    task = _task(req)
    extra = f"\n\nUser note: {req.prompt}" if (req.prompt and req.mode != "ask") else ""
    user = (f"{ctx}\n\n{task}{extra}" if ctx else f"{task}{extra}")
    try:
        markdown = broker.chat(model, [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user},
        ], options={"temperature": 0.5, "num_ctx": 8192})
    except broker.BrokerError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"markdown": markdown, "mode": req.mode}


@router.get("/api/models")
def models() -> dict:
    return broker.picker_models()
