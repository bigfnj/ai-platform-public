"""Per-rail model slots — the source of truth for the admin 'Rails' settings.

Each rail selects its model(s) from an env var that points at a per-rail broker ROLE
(e.g. ``RECIPE_BOOK_VISION_MODEL=@recipe-vision``). This catalog maps each rail model slot to
that role plus a human description, so an admin can repoint ONE rail's model without moving
others that would otherwise share a generic class (``@chat`` feeds several rails). The
gateway resolves each role via the broker to show the concrete model a slot currently uses,
and writes changes back through the broker's ``/v1/roles`` (roles.json overlay, hot-read).

A rail is shown only if it's in ``GatewaySettings.enabled_apps`` (installed here). Only chat /
vision (generative) LLM slots are surfaced — embedders, image (FLUX/SDXL) and TTS are out of
scope for this panel.

Each slot declares:
  kind    — "chat" (text), "vision" (needs a multimodal model), or "image" (a media-worker
            image backend, NOT an Ollama model). The picker only offers models valid for the
            slot's kind: an "image" slot offers the media backends; a "vision" slot offers only
            vision-capable Ollama models; "chat" offers any generative Ollama model.
  default — the model/glob the slot shipped with, shown as a revert target if a change misfires.

Embedders and TTS remain out of scope for this panel.
"""

from __future__ import annotations

from typing import Any

from platform_gateway_app.catalog import APP_CATALOG

# Media (image) backends the broker's media worker can load. These are HuggingFace models,
# not Ollama, so an "image" slot offers exactly these instead of the installed-model list.
MEDIA_IMAGE_BACKENDS: list[dict[str, str]] = [
    {"name": "flux-schnell", "label": "FLUX.1-schnell", "note": "higher quality, slower"},
    {"name": "sdxl-turbo", "label": "SDXL-Turbo", "note": "fast, lighter"},
]
_MEDIA_IMAGE_NAMES = {b["name"] for b in MEDIA_IMAGE_BACKENDS}

# rail id (matches APP_CATALOG ids) -> ordered model slots.
RAIL_MODEL_SLOTS: dict[str, list[dict[str, str]]] = {
    "edu-suite": [
        {"slot": "content", "label": "Content generation", "role": "edu", "kind": "chat",
         "env": "EDU_LLM_MODEL", "default": "mistral-small3*:24b",
         "description": "Bilingual EN / es-MX content — Just Translate, CVC worksheets, TeachTown units."},
    ],
    "iep": [
        {"slot": "writer", "label": "Present Levels writer", "role": "iep", "kind": "chat",
         "env": "IEP_LLM_MODEL", "default": "qwen3.6*:27b",
         "description": "Drafts IEP Present Levels narratives — long-form, higher quality."},
    ],
    "recipe-book": [
        {"slot": "assistant", "label": "Culinary assistant", "role": "recipe", "kind": "chat",
         "env": "RECIPE_BOOK_ASSISTANT_MODEL", "default": "gemma4*:26b",
         "description": "The cooking AI — meal plans, recipe help, pantry & bar reasoning."},
        {"slot": "vision", "label": "Recipe-photo reader", "role": "recipe-vision", "kind": "vision",
         "env": "RECIPE_BOOK_VISION_MODEL", "default": "gemma4*:26b",
         "description": "Reads a photographed recipe when importing or authoring a card."},
        {"slot": "icon", "label": "Recipe icon images", "role": "recipe-icon", "kind": "image",
         "env": "RECIPE_BOOK_ICON_MODEL", "default": "flux-schnell",
         "description": "Renders the illustrated icon for each recipe card (image model, not an LLM)."},
    ],
    "terminal-fun": [
        {"slot": "assistant", "label": "Terminal assistant", "role": "terminal-fun", "kind": "chat",
         "env": "TERMINAL_FUN_LLM_MODEL", "default": "gemma4*:12b",
         "description": "The in-terminal AI helper and live toy-tuning."},
    ],
    "ai-playground": [
        {"slot": "generation", "label": "RAG generation (local)", "role": "ai-playground", "kind": "chat",
         "env": "AI_PLAYGROUND_CHAT_MODEL", "default": "nemotron-3-nano:4b",
         "description": "Generates the grounded, cited answer in the RAG demo (local mode). NVIDIA's own "
                        "Nemotron by default; the in-demo NIM toggle flips generation to the NVIDIA cloud."},
    ],
    "smb-partner-enablement": [
        {"slot": "reasoning", "label": "Partner answer model", "role": "smb-partner-rag", "kind": "chat",
         "env": "SMB_PARTNER_RAG_MODEL", "default": "llama3.2*:3b",
         "description": "Writes the grounded answer over the SME knowledge base. Keep this 3B-class: "
                        "this rail holds it resident ALONGSIDE the embedder (and a voice model), which "
                        "a 4B+ model will not fit beside on an 8 GB card."},
    ],
}

# Roles this panel is allowed to repoint (guards the PUT: no editing generic @chat etc. here).
RAIL_SLOT_ROLES: set[str] = {
    s["role"] for slots in RAIL_MODEL_SLOTS.values() for s in slots
}
# Roles that back an "image" slot — validated against the media backends, not Ollama models.
IMAGE_SLOT_ROLES: set[str] = {
    s["role"] for slots in RAIL_MODEL_SLOTS.values() for s in slots if s["kind"] == "image"
}

_CATALOG_BY_ID: dict[str, dict[str, str]] = {a["id"]: a for a in APP_CATALOG}


def build_rails_view(roles_view: list[dict[str, Any]], enabled: set[str]) -> list[dict[str, Any]]:
    """Join the catalog with the broker's resolved roles, filtered to installed rails."""
    by_role = {r.get("role"): r for r in roles_view}
    rails: list[dict[str, Any]] = []
    for rail_id, slots in RAIL_MODEL_SLOTS.items():
        if rail_id not in enabled:
            continue
        meta = _CATALOG_BY_ID.get(rail_id, {})
        out_slots = []
        for s in slots:
            info = by_role.get(s["role"], {})
            resolved = info.get("resolved")
            # An image slot's model is a media backend (present iff it's a known backend);
            # everything else uses the broker's Ollama-installed flag.
            installed = (resolved in _MEDIA_IMAGE_NAMES) if s["kind"] == "image" \
                else bool(info.get("installed"))
            out_slots.append({
                "slot": s["slot"],
                "label": s["label"],
                "role": s["role"],
                "kind": s["kind"],
                "env": s["env"],
                "default": s["default"],
                "description": s["description"],
                "model": resolved,
                "pattern": info.get("pattern"),
                "installed": installed,
            })
        rails.append({
            "id": rail_id,
            "label": meta.get("label", rail_id),
            "icon": meta.get("icon", ""),
            "slots": out_slots,
        })
    return rails


def model_options(models: list[dict[str, Any]],
                  disabled: frozenset[str] | set[str] = frozenset()) -> list[dict[str, Any]]:
    """Installed models offered in the slot dropdowns — generative only (drop embedders).
    ``vision`` says whether the model can take image input, so the UI can hide non-vision
    models from a vision slot. Models an admin has Disabled in the model pool are excluded, so
    they can't be picked for a rail (an existing role already pointing at one still resolves)."""
    return [
        {"name": m.get("name"), "class": m.get("class"),
         "parameter_size": m.get("parameter_size"), "vision": bool(m.get("vision"))}
        for m in models
        if m.get("class") != "embed" and m.get("name") and m.get("name") not in disabled
    ]


def media_options() -> list[dict[str, str]]:
    """The fixed media (image) backends an "image" slot may choose from."""
    return list(MEDIA_IMAGE_BACKENDS)


def is_valid_image_model(model: str) -> bool:
    """Whether a submitted value is a known media image backend (for PUT validation)."""
    return model in _MEDIA_IMAGE_NAMES
