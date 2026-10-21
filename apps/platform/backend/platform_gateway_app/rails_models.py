"""Per-rail model slots — the source of truth for the admin 'Rails' settings.

Each rail selects its model(s) from an env var that points at a per-rail broker ROLE
(e.g. ``FINANCE_FRAUD_MODEL=@finance-fraud``). This catalog maps each rail model slot to
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
  cli     : optional, ``True`` when this rail's call sites pass ``requester`` (the person
            asking) for this slot on ``/v1/chat`` or ``/v1/chat/stream`` (buffered or
            streamed, both count) AND its chip opts the slot into ``cli_slots``; absent means
            not wired. Only a wired slot's Claude switch can change
            anything: the broker refuses every call that names no requester. Static, because
            the gateway cannot see a rail's call sites; tests/test_rails_cli_switch.py holds it
            equal to each rail's ``cli_slots`` opt-in, read from the rail's own source. A rail
            that wires a slot sets it here in the same change. A "chat" or "vision" slot may
            carry it (CLI_SLOT_KINDS); an "image" slot never does. No role backs both an image
            slot and a text or vision slot: the switch route refuses an image slot by its
            ROLE, so a role they shared would give the image slot a switch
            (tests/test_rails_cli_switch.py holds this too).
  stream  : optional, ``True`` when this rail sends the slot's ROLE on a ``/v1/chat/stream``
            call that names a requester: the role reaches its broker facade's stream with a
            name, so an owner's answer on it can be a Claude CLI stream. Per role, not per call:
            a role is marked when ANY of its rail's calls on it stream, even if others are
            buffered, because the switch and the broker route by role. Only a wired slot
            (``cli``) may carry it. The switch route refuses to turn ON a role that any slot
            marks this way unless the broker's ``cli`` block says ``streams``: a broker from
            before streamed CLI calls keeps every stream local while the rail's chip reads
            Claude. Static for the reason ``cli`` is; tests/test_rails_cli_switch.py holds it
            equal to each rail's own source, slot for slot.

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
# "cli": True on a slot declares that its rail passes `requester` on that slot's /v1/chat or
# /v1/chat/stream calls and lists the slot in its chip's `cli_slots` (docs/RAIL_CONTRACT.md).
# "stream": True declares that at least one of those calls is a /v1/chat/stream, so turning the
# role's Claude switch on needs a broker that streams to the CLI (the module docstring).
RAIL_MODEL_SLOTS: dict[str, list[dict[str, Any]]] = {
    "recipe-book": [
        {"slot": "assistant", "label": "Culinary assistant", "role": "recipe", "kind": "chat",
         "env": "RECIPE_BOOK_ASSISTANT_MODEL", "default": "gemma4*:26b", "cli": True,
         "description": "The cooking AI — meal plans, recipe help, pantry & bar reasoning."},
        # The photo import names the person asking on @recipe-vision (recipe-book's
        # cli_requester, None while an admin views as another user), so the slot is wired.
        {"slot": "vision", "label": "Recipe-photo reader", "role": "recipe-vision", "kind": "vision",
         "env": "RECIPE_BOOK_VISION_MODEL", "default": "gemma4*:26b", "cli": True,
         "description": "Reads a photographed recipe when importing or authoring a card."},
        {"slot": "icon", "label": "Recipe icon images", "role": "recipe-icon", "kind": "image",
         "env": "RECIPE_BOOK_ICON_MODEL", "default": "flux-schnell",
         "description": "Renders the illustrated icon for each recipe card (image model, not an LLM)."},
    ],
    "terminal-fun": [
        {"slot": "assistant", "label": "Terminal assistant", "role": "terminal-fun", "kind": "chat",
         "env": "TERMINAL_FUN_LLM_MODEL", "default": "gemma4*:12b", "cli": True,
         "description": "The in-terminal AI helper and live toy-tuning."},
    ],
    "ai-playground": [
        # The RAG demo's answer streams (/ws/rag), its buffered /api/rag/ask does not: one role.
        {"slot": "generation", "label": "RAG generation", "role": "ai-playground", "kind": "chat",
         "env": "AI_PLAYGROUND_CHAT_MODEL", "default": "nemotron-3-nano:4b", "cli": True,
         "stream": True,
         "description": "Generates the grounded, cited answer in the RAG demo. NVIDIA's own "
                        "Nemotron by default; the in-demo NIM toggle flips generation to the NVIDIA cloud."},
    ],
    "co-worker": [
        {"slot": "synthesis", "label": "Synthesis", "role": "co-worker-synthesis", "kind": "chat",
         "env": "CO_WORKER_SYNTHESIS_MODEL", "default": "mistral-small3*:24b", "cli": True,
         "description": "Turns the harvested email / calendar / Teams items into the prioritized "
                        "executive brief. Reads whole items, not retrieved chunks."},
    ],
    "smb-partner-enablement": [
        # The retrieval slot shares the generic @embed role; embedders are out of this panel's
        # scope by design, so only the generative slot appears here. Its socket answers and
        # Scenario Builder passes stream; /api/ask is buffered.
        {"slot": "reasoning", "label": "LLM", "role": "smb-partner-rag", "kind": "chat",
         "env": "SMB_PARTNER_RAG_MODEL", "default": "mistral-small3*:24b", "cli": True,
         "stream": True,
         "description": "Writes the grounded answer over the retrieved SMB partner SME chunks."},
    ],
    "gemini-cx": [
        # /ws/ask streams the answer; /api/ask is buffered.
        {"slot": "reasoning", "label": "LLM", "role": "gemini-cx-rag", "kind": "chat",
         "env": "GEMINI_CX_RAG_MODEL", "default": "mistral-small3*:24b", "cli": True,
         "stream": True,
         "description": "Writes the grounded answer over the retrieved Gemini Enterprise CX corpus, "
                        "refusing figures it cannot cite."},
    ],
    "openmaic": [
        # The retrieval slot shares the generic @embed role and is out of this panel's scope,
        # so only the generative slot appears here. Wired only where OPENMAIC_CLI_REQUESTER is
        # set: the shim then names that account on the app's calls while it is the app's only
        # user, and names nobody anywhere else, whatever the switch says. The shim's
        # streamed completions reach /v1/chat/stream, its buffered ones /v1/chat.
        {"slot": "reasoning", "label": "LLM", "role": "openmaic", "kind": "chat", "cli": True,
         "stream": True,
         "env": "OPENMAIC_LLM_MODEL", "default": "mistral-small3*:24b",
         "description": "Writes the generated course content: the slides, quiz questions and "
                        "simulation scripts the AI teacher delivers."},
    ],
    "watch-builder": [
        {"slot": "designer", "label": "Designer", "role": "watch-builder-designer",
         "kind": "chat", "env": "WATCH_BUILDER_DESIGNER_MODEL",
         "default": "mistral-small3*:24b", "cli": True,
         "description": "Writes the watchface DESIGN DOCUMENT: a schema-constrained JSON "
                        "layout, never C. Drives both describe-a-face and the variation "
                        "explorer; the rail compiles the result deterministically."},
    ],
}

# The slot kinds the Claude switch and its model choice are for. A "vision" slot gets them like a
# "chat" slot: the switch only lets the broker send an OWNER's own call on the role to the CLI,
# and it can be turned ON only for a wired slot (the `cli` flag), so a vision role stays local
# until its rail passes who is asking on its photo calls. Every CLI failure falls back to the
# local model. An "image" slot renders pictures through the media worker, which no Claude model
# stands in for, so it has neither.
CLI_SLOT_KINDS: frozenset[str] = frozenset({"chat", "vision"})


def role_sets(catalog: dict[str, list[dict[str, Any]]]) -> dict[str, set[str]]:
    """Every role set below, worked out from one catalog. The module's own are this applied to
    RAIL_MODEL_SLOTS; a test that adds a synthetic rail recomputes them with it, so the rail goes
    through the same rules the switch route does.

    Each is one the gateway reads (main.py imports them all; test_rails_cli_switch.py holds it).
    CHAT_SLOT_ROLES, the roles with a TEXT slot, went on 2026-10-14: nothing had read it since the
    switch moved to CLI_SLOT_ROLES, and only tests used it, to pick a text role, which they now
    read off RAIL_MODEL_SLOTS itself."""
    slots = [s for rail in catalog.values() for s in rail]
    return {
        # Roles this panel is allowed to repoint (guards the PUT: no editing generic @chat etc.).
        "RAIL_SLOT_ROLES": {s["role"] for s in slots},
        # Roles that back an "image" slot: validated against the media backends, not Ollama.
        "IMAGE_SLOT_ROLES": {s["role"] for s in slots if s["kind"] == "image"},
        # Roles that back a "vision" slot.
        "VISION_SLOT_ROLES": {s["role"] for s in slots if s["kind"] == "vision"},
        # Roles with at least one text or vision slot: the only ones the Claude switch may touch.
        "CLI_SLOT_ROLES": {s["role"] for s in slots if s["kind"] in CLI_SLOT_KINDS},
        # Roles whose rail passes `requester` for at least one slot (the `cli` flag above), so
        # switching Claude on for them can serve an owner's own call.
        "CLI_WIRED_ROLES": {s["role"] for s in slots if s.get("cli") is True},
        # Roles some slot marks as streamed (the `stream` flag above): switching Claude on for
        # one needs a broker that streams to the CLI. Per role, like VISION_SLOT_ROLES, so a
        # slot that shares the role with a streaming one is held to it too.
        "STREAM_SLOT_ROLES": {s["role"] for s in slots if s.get("stream") is True},
    }


_ROLE_SETS = role_sets(RAIL_MODEL_SLOTS)
RAIL_SLOT_ROLES: set[str] = _ROLE_SETS["RAIL_SLOT_ROLES"]
IMAGE_SLOT_ROLES: set[str] = _ROLE_SETS["IMAGE_SLOT_ROLES"]
VISION_SLOT_ROLES: set[str] = _ROLE_SETS["VISION_SLOT_ROLES"]
CLI_SLOT_ROLES: set[str] = _ROLE_SETS["CLI_SLOT_ROLES"]
CLI_WIRED_ROLES: set[str] = _ROLE_SETS["CLI_WIRED_ROLES"]
STREAM_SLOT_ROLES: set[str] = _ROLE_SETS["STREAM_SLOT_ROLES"]

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
            # The broker's own `installed` decides, for image slots too: it knows whether the
            # media worker can start (interpreter present, media enabled), which a name check
            # here cannot. Deciding by name made a box with no media venv show its image slot
            # healthy while every render failed. A broker too old to judge image roles
            # (no `installed` key on the row) keeps the name check.
            if s["kind"] == "image" and "installed" not in info:
                installed = image_backend(resolved) is not None
            else:
                installed = bool(info.get("installed"))
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
                # The catalog's static `cli` flag: this rail passes who is asking for this slot.
                "cli_wired": s.get("cli") is True,
                # Whether the slot has the Claude switch and model choice at all. The shell
                # draws the row from this (rails_view.ts hasCliSwitch) instead of keeping a
                # second copy of the kinds that could drift from the route's.
                "cli_switchable": s["kind"] in CLI_SLOT_KINDS,
            })
        rails.append({
            "id": rail_id,
            "label": meta.get("label", rail_id),
            "icon": meta.get("icon", ""),
            "slots": out_slots,
        })
    # Which OTHER installed rails a slot's role also feeds. Repointing a slot rewrites its
    # ROLE, so a role two rails share moves both: iep-goals' writer is @iep and its translator
    # @edu, which IEP Present Levels and EDU-Suite use too. The tab said a change repoints only
    # that rail; `shared_with` is the truth it can show instead (labels, [] when unshared).
    users: dict[str, list[str]] = {}
    for rail in rails:
        for slot in rail["slots"]:
            users.setdefault(slot["role"], []).append(rail["label"])
    for rail in rails:
        for slot in rail["slots"]:
            others = [lbl for lbl in users[slot["role"]] if lbl != rail["label"]]
            slot["shared_with"] = sorted(set(others))
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


def image_backend(name: str | None) -> str | None:
    """The MEDIA_IMAGE_BACKENDS name `name` spells in any case (`FLUX-Schnell` is flux-schnell),
    or None for a name that is not one. The gateway's twin of the broker's `image_backend`,
    which /v1/image, the roles view and the role audit all ask, so an image slot's PUT accepts
    every spelling the broker serves, and saves the backend's own."""
    folded = name.lower() if isinstance(name, str) else ""
    return next((n for n in _MEDIA_IMAGE_NAMES if n == folded), None)
