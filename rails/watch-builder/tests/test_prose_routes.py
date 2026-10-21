"""Every API route this rail's own prose names is a route the app serves.

rail.json told readers that `/api/render` returns a sunlight-corrected variant, and config.py
said the frontend asks `/api/render` for one platform at a time. There has never been such a
route: a render is part of POST /api/build, and the frontend sends that no platform list. A
reader who believed either went looking for an endpoint that 404s. The prose that names routes
is the manifest, the README, the design record, and the comments and docstrings in the
backend and frontend source, so all of it is read.
"""
from __future__ import annotations

import re
from pathlib import Path

from watch_builder.api.app import app

RAIL = Path(__file__).resolve().parents[1]
# A path as prose spells it: /api/designs/{id}, /api/images/<id>/raw, `/api/ai/estimate?kind=`.
NAMED = re.compile(r"/api(?:/[A-Za-z0-9_{}<>.-]+)+")
PARAM = re.compile(r"\{[^}]*\}|<[^>]*>")


def served() -> set[str]:
    return {PARAM.sub("{}", r.path) for r in app.routes if hasattr(r, "path")}


def prose() -> list[Path]:
    return [RAIL / "rail.json", RAIL / "README.md", RAIL / "docs" / "DESIGN.md",
            *sorted((RAIL / "backend").rglob("*.py")),
            *sorted((RAIL / "frontend" / "src").glob("*.ts*"))]


def unserved(files: list[Path], routes: set[str]) -> list[str]:
    out = []
    for f in files:
        name = f.relative_to(RAIL).as_posix() if f.is_relative_to(RAIL) else f.name
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            for m in NAMED.findall(line):
                if PARAM.sub("{}", m).rstrip(".") not in routes:
                    out.append(f"{name}:{n} names {m}")
    return out


def test_the_scan_reads_real_prose():
    # The witness: a scan that found no file, or no route mention, would pass vacuously.
    files = prose()
    assert all(f.is_file() for f in files)
    text = "\n".join(f.read_text(encoding="utf-8") for f in files)
    assert {"/api/build", "/api/validate", "/api/ai/variations"} <= set(NAMED.findall(text))


def test_every_route_named_in_prose_is_served():
    assert unserved(prose(), served()) == []


def test_an_unserved_route_is_reported(tmp_path):
    f = tmp_path / "rail.json"
    f.write_text('"... so /api/render also returns a sunlight-corrected variant ..."\n'
                 '"GET /api/designs/{id} and /api/images/<id>/raw are served."\n', encoding="utf-8")
    assert unserved([f], served()) == ["rail.json:1 names /api/render"]
