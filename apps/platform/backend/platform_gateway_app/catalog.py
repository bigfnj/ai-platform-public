"""The platform's app catalog — the server-side source of truth for the rail.

Each entry is what the shell needs to draw a rail item: id, label, icon, status.
The gateway filters this per user (by entitlements) and hands the shell only the
apps that user may see, so the rail is no longer a static frontend constant.

'ready' apps load as federated remotes and have a backend the gateway proxies;
'soon' apps are roadmap placeholders (shown but not reachable). Which 'ready'
apps actually have a proxied backend is governed by GatewaySettings.enabled_apps.

ORDER IS DERIVED, NOT HAND-MAINTAINED. ``_ENTRIES`` below is written in whatever order is
convenient; ``APP_CATALOG`` is the sorted view and the only thing callers read. The gateway
returns it in this order and the shell renders it as given (neither re-sorts), so this is the
one place the rail's order is decided. The rule is **alphabetical by label**, with one
exception: the edu-family tiles share a ``nav_group`` so they stay together instead of being
split by whatever sorts between them (Finance and Gemini CX would otherwise land between
EDU-Suite and the two IEP tiles).

Sort key: (status, nav_group, nav_order, label).
  * ``status`` first, so any future 'soon' placeholder sinks below the usable rails.
  * ``nav_group`` defaults to the entry's own label, so an ungrouped rail simply sorts
    alphabetically among the groups — the common case needs no annotation.
  * ``nav_order`` orders the members within a group.
  * ``label`` is the final tiebreak.
The coupling this accepts: renaming a label reorders the rail. That is cheaper than a
hand-maintained order nobody remembers to re-sort.
"""

from __future__ import annotations

from typing import Any

# Written in any order; APP_CATALOG below is the sorted view.
_ENTRIES: list[dict[str, Any]] = [
    # The edu-family: one dashboard image (EDU-Suite) plus two isolated instances (IEP
    # Present Levels, IEP Goals). Same nav_group so plain alphabetisation does not wedge
    # Finance / Gemini CX between them; nav_order fixes the sequence inside the group.
    {"id": "recipe-book", "label": "Recipe Book", "icon": "🍳", "status": "ready",
     "description": "A cookbook of roughly 900 recipes and cocktails with generated illustrations, a meal planner "
                    "that works in real dates, and a pantry and bar you can cook from."
    },
    # Browser terminal into the host (a real shell on ELSEWHERE). Needs an explicit
    # entitlement even for admins — only the seed owner is all-access. See
    # apps/workstation/README.md and apps/workstation/HARDENING.md (P1.1).
    {"id": "workstation", "label": "Workstation", "icon": "💻", "status": "ready",
     "description": "A browser terminal into this machine over SSH, plus launchers that open a desktop "
                    "application from the host as a single seamless window on whatever device you are using."
    },
    # Local terminal games/toys (self-hosted in its own container; no host access).
    # Family-friendly, entitlement-gated like any rail.
    {"id": "terminal-fun", "label": "Terminal Fun", "icon": "🕹️", "status": "ready",
     "description": "A browser terminal into a couple of dozen sandboxed games and toys, with an assistant that "
                    "can explain what you are looking at and retune the toys while they run."
    },
    # AI Playground — a multi-demo rail (first demo: RAG over documents), broker-mediated,
    # with a live local<->NVIDIA-NIM generation toggle and WebSocket token streaming.
    {"id": "ai-playground", "label": "AI Playground", "icon": "🛝", "status": "ready",
     "description": "A showcase of what the platform's AI plumbing can actually do: ask questions of a document "
                    "set and get answers with citations, or benchmark embedding models head-to-head — GPU against "
                    "CPU — to see which one retrieves better on your own corpus."
    },
    # Voice Studio — synthesize + A/B trained and community voices. Heavy engines run
    # native and are dispatched by the broker, so the rail itself is torch-free.
    # Co-Worker — an external harvest process drops email/calendar/Teams items into an
    # inbox directory; the rail synthesizes them into a prioritized executive brief.
    # 🧭 rather than the 💼 it carried in the public repo: job-aid already owns 💼 here,
    # and two identical briefcases in one rail is a worse bug than a changed emoji.
    {"id": "co-worker", "label": "Co-Worker", "icon": "🧭", "status": "ready",
     "description": "Turns a pile of harvested email, calendar invites and chat messages into one prioritized "
                    "executive brief, so a morning of triage becomes a page you can read in two minutes."
    },
    # Meeting Atlas — indexes Meetily recording folders and rolls them up by day/week/month.
    # Does NO inference and never calls the broker (status_route: null, model_slots: []):
    # re-transcription and summarisation are owned by an external task that writes sidecar
    # files this rail reads, the same writer/reader split as co-worker's inbox.
    {"id": "meeting-atlas", "label": "Meeting Atlas", "icon": "🗓️", "status": "ready",
     "description": "Indexes your meeting recordings and rolls them up by day, week and month — and treats every "
                    "generated summary as a claim, checking each action item's quote against the real transcript "
                    "and flagging invented dates or reused evidence."
    },
    # SMB Partner Enablement — grounded RAG over Microsoft SMB partner SME content, with a
    # voice surface and a standalone mobile build served at /smb-partner-enablement/m/.
    {"id": "smb-partner-enablement", "label": "SMB Partner", "icon": "🤝", "status": "ready",
     "description": "Grounded answers over Microsoft SMB partner enablement material, with a scenario builder for "
                    "customer conversations and a voice surface for hands-free use."
    },
    # Gemini Enterprise CX — grounded RAG over a Google Cloud GECX subject-matter corpus,
    # fronted by a curated question deck rather than a bare chat box. The emoji is only a
    # fallback: the shell draws the real Gemini spark via iconOverrides (see App.tsx).
    {"id": "gemini-cx", "label": "Gemini CX", "icon": "✨", "status": "ready",
     "description": "Grounded answers about Google Cloud's Gemini Enterprise for Customer Experience, drawn from "
                    "a curated corpus. Every claim is cited, and a deck of starter questions gets you going."
    },
    # OpenMAIC — a thin wrapper, not the application: the upstream Next.js courseware engine
    # runs as its own container and the rail reverse-proxies it under /openmaic/api/app/, so
    # the gateway's user gate stays in front of it. See rails/openmaic/rail.json notes.
    {"id": "openmaic", "label": "OpenMAIC", "icon": "🎓", "status": "ready",
     "description": "Turns a topic or an uploaded document into an interactive class: generated slides, quizzes "
                    "and simulations delivered by an AI teacher with narration and a live whiteboard. Runs the "
                    "open-source OpenMAIC courseware engine against this platform's own models."
    },
]

# 'ready' before 'soon'; anything unrecognised sorts last rather than crashing.
_STATUS_RANK = {"ready": 0, "soon": 1}


def nav_sort_key(entry: dict[str, Any]) -> tuple[int, str, int, str]:
    """The rail's display order: alphabetical by label, with the edu-family grouped."""
    label = str(entry.get("label") or "")
    return (
        _STATUS_RANK.get(str(entry.get("status") or ""), 9),
        str(entry.get("nav_group") or label).casefold(),
        int(entry.get("nav_order") or 0),
        label.casefold(),
    )


APP_CATALOG: list[dict[str, Any]] = sorted(_ENTRIES, key=nav_sort_key)

APP_IDS: set[str] = {a["id"] for a in APP_CATALOG}
