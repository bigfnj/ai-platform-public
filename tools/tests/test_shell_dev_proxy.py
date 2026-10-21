"""The shell's vite DEV proxy has its own entry for every app the catalog can mount.

The gateway mounts every app at /<id>/ (main.py: `/{app}/api/...` is proxied, `/{app}/...` is the
federation remote), and `npm run dev` on the shell reaches them only through the hand-written
`server.proxy` table in apps/platform/frontend/vite.config.ts. Nothing derives that table, so
nothing noticed that /iep-goals had no entry: it worked only because vite takes the FIRST key
that prefixes the URL, and '/iep' prefixes '/iep-goals/...'. Two rules, both checked here:
every 'ready' catalog id has its own key, and no key is listed after a shorter key that is a
prefix of it (which would shadow it).

Dev-only. The public snapshot drops a withheld rail's catalog entry and its vite lines together,
so this holds in both trees.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
VITE = REPO / "apps" / "platform" / "frontend" / "vite.config.ts"
CATALOG = REPO / "apps" / "platform" / "backend" / "platform_gateway_app" / "catalog.py"


def proxy_keys(text: str) -> list[str]:
    i = text.index("proxy: {")
    depth, j = 0, i + len("proxy: ")
    for j in range(i + len("proxy: "), len(text)):
        depth += {"{": 1, "}": -1}.get(text[j], 0)
        if depth == 0:
            break
    block = re.sub(r"//[^\n]*", "", text[i:j + 1])
    return re.findall(r"^\s*'(/[^']*)'\s*:", block, re.M)


def ready_ids(conformance) -> list[str]:
    entries = conformance._literal_assign(CATALOG, "_ENTRIES")
    assert entries, "catalog.py _ENTRIES could not be read as a literal"
    return [e["id"] for e in entries if e.get("status") == "ready"]


def test_the_proxy_table_and_the_catalog_are_read():
    keys = proxy_keys(VITE.read_text(encoding="utf-8"))
    assert "/api" in keys and len(keys) >= 8, keys


def test_every_ready_app_has_its_own_dev_proxy_entry(conformance):
    keys = set(proxy_keys(VITE.read_text(encoding="utf-8")))
    ids = ready_ids(conformance)
    assert len(ids) >= 5, ids
    missing = [f"/{i}" for i in ids if f"/{i}" not in keys]
    assert not missing, f"no vite dev-proxy entry for {missing} (apps/platform/frontend/vite.config.ts)"


def test_no_proxy_key_is_shadowed_by_a_shorter_one_above_it():
    keys = proxy_keys(VITE.read_text(encoding="utf-8"))
    # vite matches with a plain startsWith on the URL, so '/iep' takes '/iep-goals/...' too.
    shadowed = [(a, b) for n, a in enumerate(keys) for b in keys[n + 1:] if b != a and b.startswith(a)]
    assert not shadowed, ("a key listed after a shorter key that prefixes it never matches; "
                          f"move it above: {shadowed}")
