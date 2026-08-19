"""The platform's app catalog — the server-side source of truth for the rail.

Each entry is what the shell needs to draw a rail item: id, label, icon, status.
The gateway filters this per user (by entitlements) and hands the shell only the
apps that user may see, so the rail is no longer a static frontend constant.

'ready' apps load as federated remotes and have a backend the gateway proxies;
'soon' apps are roadmap placeholders (shown but not reachable). Which 'ready'
apps actually have a proxied backend is governed by GatewaySettings.enabled_apps.

ORDER IS DERIVED, NOT HAND-MAINTAINED. ``_ENTRIES`` below is in whatever order it
was written; ``APP_CATALOG`` is the sorted view and the only thing callers should
read. The old hand-curated order was chronological — the sequence rails happened
to be added — which means nothing to a user. It also could not be relied on: the
gateway filters this list per entitlement, so a curated global order guarantees no
position for any actual user. Two people with different entitlements saw the same
rail in different places regardless. Sorting removes a hand-maintained list (one
fewer thing to drift) and makes the result self-evident inside any subset.

Sort key: (status, nav_group, nav_order, label).

  * ``status`` first, so roadmap 'soon' placeholders sink below everything usable
    instead of interleaving with the rails a user can actually open.
  * ``nav_group`` defaults to the entry's own label, so an entry with no group
    sorts alphabetically among the groups — the common case needs no annotation.
  * ``nav_order`` breaks ties inside a group.
  * ``label`` last, as a stable final tiebreak.

Grouping exists for one real case: IEP is a second instance of the edu-suite
dashboard image, and plain alphabetisation puts Gemini CX between them. Both share
nav_group 'EDU-Suite' so they stay adjacent. Sorting is by LABEL rather than id
because the label is what a user reads ('SMB Partner', not
'smb-partner-enablement').

Note the coupling this accepts: renaming a label reorders the rail. That is the
price of a derived order, and it is cheaper than a list nobody remembers to
re-sort. rails/<id>/rail.json mirrors nav_group / nav_order for the rails that are
under contract, and tools/rail_conformance.py (RC003) holds the two in agreement.
The manifests are deliberately NOT read here — they are not in the gateway image.
"""

from __future__ import annotations

from typing import Any

# Written in any order; APP_CATALOG below is the sorted view. Add a rail here and it
# lands in the right place on its own.
_ENTRIES: list[dict[str, Any]] = [
    {"id": "edu-suite", "label": "EDU-Suite", "icon": "🎓", "status": "ready",
     "nav_group": "EDU-Suite", "nav_order": 0},
    # A second instance of the edu-suite dashboard, isolated to drafting IEP Present
    # Levels narratives (its own library/DB so student data stays separate). Grouped
    # with edu-suite so the pair reads as one thing in the rail.
    {"id": "iep", "label": "IEP Present Levels", "icon": "📝", "status": "ready",
     "nav_group": "EDU-Suite", "nav_order": 1},
    {"id": "recipe-book", "label": "Recipe Book", "icon": "🍳", "status": "ready"},
    # Browser terminal into the host (a real shell on ELSEWHERE). Needs an explicit
    # entitlement even for admins — only the seed owner is all-access. See
    # apps/workstation/README.md and apps/workstation/HARDENING.md (P1.1).
    {"id": "workstation", "label": "Workstation", "icon": "💻", "status": "ready"},
    # Local terminal games/toys (self-hosted in its own container; no host access).
    # Family-friendly, entitlement-gated like any rail.
    {"id": "terminal-fun", "label": "Terminal Fun", "icon": "🕹️", "status": "ready"},
    # AI Playground — a multi-demo rail (first demo: RAG over documents), broker-mediated,
    # with a live local<->NVIDIA-NIM generation toggle and WebSocket token streaming.
    {"id": "ai-playground", "label": "AI Playground", "icon": "🛝", "status": "ready"},
    {"id": "co-worker", "label": "Co-Worker", "icon": "💼", "status": "ready"},
    # SMB Partner Enablement — grounded RAG over Microsoft SMB partner SME content, with a
    # voice surface and a standalone mobile build served at /smb-partner-enablement/m/.
    {"id": "smb-partner-enablement", "label": "SMB Partner", "icon": "🤝", "status": "ready"},
    # Gemini Enterprise CX — grounded RAG over a Google Cloud GECX subject-matter corpus,
    # fronted by a curated question deck rather than a bare chat box. The emoji is only a
    # fallback: the shell draws the real Gemini spark via iconOverrides (see App.tsx).
    {"id": "gemini-cx", "label": "Gemini CX", "icon": "✨", "status": "ready"},
]

# 'ready' before 'soon'; anything unrecognised sorts last rather than crashing.
_STATUS_RANK = {"ready": 0, "soon": 1}


def nav_sort_key(entry: dict[str, Any]) -> tuple[int, str, int, str]:
    """The rail's display order. Exported so the conformance checker can recompute it
    rather than restating the rule and drifting from it."""
    label = str(entry.get("label") or "")
    return (
        _STATUS_RANK.get(str(entry.get("status") or ""), 9),
        str(entry.get("nav_group") or label).casefold(),
        int(entry.get("nav_order") or 0),
        label.casefold(),
    )


APP_CATALOG: list[dict[str, Any]] = sorted(_ENTRIES, key=nav_sort_key)

APP_IDS: set[str] = {a["id"] for a in APP_CATALOG}
