"""The platform's app catalog — the server-side source of truth for the rail.

Each entry is what the shell needs to draw a rail item: id, label, icon, status.
The gateway filters this per user (by entitlements) and hands the shell only the
apps that user may see, so the rail is no longer a static frontend constant.

'ready' apps load as federated remotes and have a backend the gateway proxies;
'soon' apps are roadmap placeholders (shown but not reachable). Which 'ready'
apps actually have a proxied backend is governed by GatewaySettings.enabled_apps.
"""

from __future__ import annotations

APP_CATALOG: list[dict[str, str]] = [
    {"id": "edu-suite", "label": "EDU-Suite", "icon": "🎓", "status": "ready"},
    {"id": "iep", "label": "IEP Present Levels", "icon": "📝", "status": "ready"},
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
]

APP_IDS: set[str] = {a["id"] for a in APP_CATALOG}
