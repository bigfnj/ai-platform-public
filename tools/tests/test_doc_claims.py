"""Doc claims that can drift from the tree, held to the tree.

The owner's rule is that a README claim needs the same proof as a code claim, and the audit that
produced this file found the usual failure: a list or a count written once, true that day, and
wrong a few rails later (a rail count two short of the tree, a ports table missing two rails, an
installer page naming six optional rails when the installer offered seven). Each test below
pairs one such list in a doc with the source it describes, and reads BOTH from the files, so
neither is restated here.

Every parse has a vacuity guard: a pattern that silently matched nothing would otherwise pass
every assertion it feeds.

The public snapshot drops CLAUDE.md and replaces README.md with docs/README.public.md, and its
tree holds fewer rails. The CLAUDE.md test skips there with that reason; the rest run against
whatever the tree holds, so they hold in both.
"""
from __future__ import annotations

import ast
import json
import os
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
# Never read as a doc or a source: installed packages, tool state, and BUILD OUTPUT. A build output
# dir is not only `dist`: edu-suite's iep tile builds to `outDir: 'dist-iep'`, vite caches in .vite/,
# vitest --coverage writes into coverage/. The walks pruned `dist` alone, and the dead-URL scan reads
# .json and .txt, so a built dist-iep would have been read as if it were a doc. The same set as
# test_status_text_scan.py's (the same fix there).
_NOT_SOURCE = ("node_modules", ".venv", ".git", "__pycache__")


def _is_build_output(name: str) -> bool:
    return name in ("dist", "build", "coverage", ".vite") or name.startswith("dist-")


def _skip_dir(name: str) -> bool:
    return name in _NOT_SOURCE or _is_build_output(name)


def _walk(top):
    """os.walk, pruned of everything _skip_dir names, for every walk in this file."""
    for root, dirs, files in os.walk(top):
        dirs[:] = [d for d in dirs if not _skip_dir(d)]
        yield root, dirs, files


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def _manifests() -> list[tuple[str, dict]]:
    out = [(p.parent.name, json.loads(p.read_text(encoding="utf-8")))
           for p in sorted((REPO / "rails").glob("*/rail.json"))]
    assert out, "no rails/*/rail.json found, so every test below would pass on nothing"
    return out


# --- services/broker/README.md <-> BrokerSettings ------------------------------------------------

def _broker_fields() -> set[str]:
    tree = ast.parse(_read("services/broker/app/config.py"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "BrokerSettings")
    names = {n.target.id for n in cls.body
             if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
             and not n.target.id.startswith("_") and n.target.id != "model_config"}
    assert len(names) >= 10, f"parsed only {sorted(names)} from BrokerSettings"
    return {f"BROKER_{n.upper()}" for n in names}


def test_the_broker_readme_names_every_setting_and_no_other():
    """BROKER_CLI_TIMEOUT and BROKER_CLI_MODEL were settings nobody could find."""
    fields = _broker_fields()
    named = set(re.findall(r"\bBROKER_[A-Z0-9_]+\b", _read("services/broker/README.md")))
    assert not fields - named, f"settings the broker README never names: {sorted(fields - named)}"
    assert not named - fields, (f"BROKER_ names in the README that are not settings: "
                                f"{sorted(named - fields)}")


# --- README.md's commands -------------------------------------------------------------------------

def test_every_script_the_readme_names_exists():
    """The deploy block read "." then "ebuild-stale.ps1", because the backslash-r of
    `.\\rebuild-stale.ps1` was eaten as a carriage return in transit. A command naming a script
    that does not exist is the cheapest thing in a README to check, so it is checked."""
    names = set(re.findall(r"[A-Za-z0-9_.-]+\.ps1\b", _read("README.md")))
    assert names, "README.md names no .ps1 script, so this test checks nothing"
    have = set()
    for root, dirs, files in _walk(REPO):
        have.update(f for f in files if f.endswith(".ps1"))
    assert not names - have, f"README.md names scripts that do not exist: {sorted(names - have)}"


# --- no file sends the operator to an HTTPS address nothing serves -------------------------------

# Built, not written, so this file does not contain the string it forbids.
_DEAD_URL = "https://" + "platform.localhost"
_SCANNED = (".md", ".ps1", ".py", ".ts", ".tsx", ".yml", ".yaml", ".json", ".sh", ".txt")
# Dated records keep what was once said, and the entry recording its correction quotes it.
# Keyed by the STEM of a file directly under docs/, not by its path: these files are withheld
# from the public snapshot, and publish.py refuses a shipped tool that names a withheld path
# (withheld_refs). Nothing here opens them; in public they are simply absent.
_HISTORY = {
    "checkpoint": "the design record and build log; its 2026-07 steps are history",
    "engineering-record": "closed work, including the entry that records this fix",
    "BACKLOG": "open items quote the claim they describe",
}


def _is_history(rel: str) -> bool:
    parts = rel.split("/")
    return len(parts) == 2 and parts[0] == "docs" and parts[1].endswith(".md") \
        and parts[1][:-3] in _HISTORY


def _dead_url_hits(top: Path) -> list[str]:
    hits = []
    for root, dirs, files in _walk(top):
        for f in files:
            if not (f.endswith(_SCANNED) or f == "Caddyfile"):
                continue
            path = Path(root) / f
            rel = path.relative_to(top).as_posix()
            if _is_history(rel):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            hits += [f"{rel}:{n}" for n, ln in enumerate(text.splitlines(), 1) if _DEAD_URL in ln]
    return hits


def test_no_file_tells_the_operator_to_use_https_platform_localhost():
    """Nothing serves it: every Caddyfile site is http:// and both compose files publish only
    1111:80, so nothing listens on 443. Yet deploy/README.md, deploy/INSTRUCTIONS.md (twice),
    a tunnel setup doc (twice), a Caddyfile comment and the smoke-test guidance all sent
    operators there."""
    hits = _dead_url_hits(REPO)
    assert not hits, (f"{_DEAD_URL} is named here, and nothing serves it (Caddy is plain HTTP on "
                      f"host 1111; use http://localhost:1111): {hits}")


@pytest.mark.parametrize("out_dir", ["dist-iep", "dist", "build", "coverage", ".vite", "node_modules"])
def test_build_output_is_never_read_as_a_doc(tmp_path, out_dir):
    """edu-suite builds its iep tile to frontend/dist-iep. The walks pruned `dist` alone, so a built
    bundle there (.json, .txt, .js) would have been scanned as if an operator read it. A planted
    file under each build-output dir is not read; the same text under src/ is (the control)."""
    fe = tmp_path / "rails" / "a-rail" / "apps" / "dashboard" / "frontend"
    (fe / out_dir / "assets").mkdir(parents=True)
    (fe / out_dir / "assets" / "planted-build-output.json").write_text(f'{{"u": "{_DEAD_URL}"}}\n', encoding="utf-8")
    assert _dead_url_hits(tmp_path) == []
    (fe / "src").mkdir()
    (fe / "src" / "note.txt").write_text(_DEAD_URL + "\n", encoding="utf-8")
    assert _dead_url_hits(tmp_path) == ["rails/a-rail/apps/dashboard/frontend/src/note.txt:1"]


# --- no platform doc tells the operator to run a bare full-stack compose `up` --------------------

# The platform's operator docs, not each rail's own (a rail doc may describe its own compose file:
# one withheld rail has one). Markdown only: scripts legitimately bring stacks up.
_OPERATOR_DOCS = ("docs", "deploy", "services", "apps", "web")
# A deferred, dated plan for ANOTHER box, left as written for whoever picks it up.
_OTHER_BOX_PLANS = {"linux-host-Migration": "deferred 2026-07-23 plan for moving to a Linux host"}
_COMPOSE_UP = re.compile(r"\b(?:docker|podman)\s+compose\b(?P<pre>[^`\n#;|&]*?)\s+up\b"
                         r"(?P<args>[^`\n#;|&]*)")
# `up` flags that take a value, so the value is not mistaken for a service name.
_UP_VALUE_FLAGS = {"-t", "--timeout", "--scale", "--pull", "--wait-timeout", "--exit-code-from",
                   "--attach", "--no-attach"}
# Prose that names the bare command to FORBID it ("NEVER run a bare full-stack ...") is the rule
# itself, not advice. Fenced code blocks get no such exemption: a command there is one to run.
_FORBIDS = re.compile(r"\b(never|not|don't|instead of)\b", re.I)


def _names_a_service(args: str) -> bool:
    toks = args.split()
    for i, tok in enumerate(toks):
        if tok.startswith("-"):
            continue
        if i and toks[i - 1] in _UP_VALUE_FLAGS:
            continue
        return True
    return False


def _operator_docs():
    for f in sorted(REPO.glob("*.md")):
        yield f
    for top in _OPERATOR_DOCS:
        for root, dirs, files in _walk(REPO / top):
            for name in files:
                if name.endswith(".md"):
                    yield Path(root) / name


def bare_compose_ups(text: str) -> list[int]:
    """Line numbers where `text` tells the reader to run compose `up` naming no service."""
    out, fenced = [], False
    for n, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        for m in _COMPOSE_UP.finditer(line):
            if _names_a_service(m.group("args")):
                continue
            if not fenced and _FORBIDS.search(line):
                continue
            out.append(n)
            break
    return out


def test_the_bare_up_detector_reads_each_form():
    word = "compose"          # spelled apart, so this file never carries the forbidden command
    docker = "docker " + word
    sample = "\n".join([
        "```powershell",
        f"{docker} up -d --build",                                 # 2: bare, fenced
        f"{docker} up -d --build --no-deps <service>   # NEVER a bare full-stack `up`",
        f"{docker} -f deploy/x.yml up -d gateway",
        f"{docker} up -d -t 30",                                   # 5: a flag value, not a service
        f"{docker} up -d   # not the usual way",                   # 6: fenced, so no exemption
        "```",
        f"Prose: run `{docker} up -d` to start it.",               # 8: bare, advice
        f"**NEVER run a bare full-stack `{docker} up`**",           # 9: the rule itself
        f"`podman {word} up --build` and nothing else",            # 10: bare
    ])
    assert bare_compose_ups(sample) == [2, 5, 6, 8, 10]


def test_no_operator_doc_runs_a_bare_full_stack_compose_up():
    """CLAUDE.md's rule: never a bare full-stack `up`. It recreates caddy, which rebinds the host
    port and churns the Windows NAT, briefly dropping the whole machine's internet. Four places in
    two operator docs told the reader to do exactly that: deploy/README.md's build block and its
    credentials note, deploy/INSTRUCTIONS.md's first deploy and its drive-share note."""
    hits = []
    for path in _operator_docs():
        rel = path.relative_to(REPO).as_posix()
        stem = path.name[:-3]
        if _is_history(rel) or (rel.startswith("docs/") and stem in _OTHER_BOX_PLANS):
            continue
        hits += [f"{rel}:{n}" for n in bare_compose_ups(path.read_text(encoding="utf-8"))]
    assert not hits, (f"docs that tell the operator to run a bare full-stack compose up (name the "
                      f"service with --no-deps, or use rebuild-stale.ps1): {hits}")


# --- the rail lists -------------------------------------------------------------------------------

def test_claude_md_has_a_row_for_every_rail():
    path = REPO / "CLAUDE.md"
    if not path.is_file():
        pytest.skip("CLAUDE.md is not in this tree (it is private; the public snapshot drops it)")
    rows = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.startswith("|")]
    missing = [d for d, _ in _manifests() if not any(f"`rails/{d}`" in r for r in rows)]
    assert not missing, f"CLAUDE.md's rail table has no row for: {missing}"


def test_the_readme_names_every_rail():
    readme = _read("README.md")
    missing = [m["id"] for _, m in _manifests()
               if f"**{m['id']}**" not in readme and f"**{m.get('label', '')}**" not in readme]
    assert not missing, f"README.md's rail list does not name: {missing}"


def test_the_ports_table_matches_every_manifest():
    text = _read("docs/RAIL_CONTRACT.md")
    section = text.split("### Ports", 1)[1].split("\n### ", 1)[0]
    rows = {m.group(1): (int(m.group(2)), int(m.group(3)))
            for m in re.finditer(r"^\| *([a-z0-9-]+) *\| *(\d+)[^|]*\| *(\d+)", section, re.M)}
    assert rows, "parsed no rows from RAIL_CONTRACT's ports table"
    wrong = {}
    for _, m in _manifests():
        want = (m["ports"]["backend"], m["ports"]["dev"])
        if rows.get(m["id"]) != want:
            wrong[m["id"]] = {"table": rows.get(m["id"]), "rail.json": want}
    assert not wrong, f"RAIL_CONTRACT's ports table disagrees with the manifests: {wrong}"


def test_the_status_route_sentence_matches_the_manifests():
    text = " ".join(_read("docs/RAIL_CONTRACT.md").split())
    m = re.search(r"every rail except ([a-z0-9, -]+?) declares a `status_route`\*\* "
                  r"\(([a-z0-9-]+) at `([^`]+)`, every other one at `([^`]+)`", text)
    assert m, "the status_route sentence in RAIL_CONTRACT.md is gone or reworded; update this test"
    without = {s.strip() for s in re.split(r",| and ", m.group(1)) if s.strip()}
    odd_id, odd_route, usual = m.group(2), m.group(3), m.group(4)
    assert without, "parsed no rail names from the status_route sentence"
    wrong = []
    for _, man in _manifests():
        route = man.get("status_route")
        if (route is None) != (man["id"] in without):
            wrong.append(f"{man['id']}: status_route={route!r}")
        elif route is not None and route != (odd_route if man["id"] == odd_id else usual):
            wrong.append(f"{man['id']}: status_route={route!r}")
    assert not wrong, f"RAIL_CONTRACT's status_route sentence disagrees with: {wrong}"


# JSX use, not the bare word: workstation's vite.config.ts names ModelChips in a COMMENT, and a
# substring test let a chip-less rail pass (the harness's status_route mutation found it).
_RENDERS_CHIPS = re.compile(r"<ModelChips\b")


def _renders_model_chips(rail_dir: Path) -> bool:
    for root, dirs, files in _walk(rail_dir):
        for f in files:
            if f.endswith(".tsx") and _RENDERS_CHIPS.search(
                    (Path(root) / f).read_text(encoding="utf-8", errors="replace")):
                return True
    return False


def test_every_rail_with_a_status_route_renders_model_chips():
    lacking = [m["id"] for d, m in _manifests()
               if m.get("status_route") and not _renders_model_chips(REPO / "rails" / d)]
    assert not lacking, f"declare a status_route but render no <ModelChips>: {lacking}"


# --- docs/INSTALL.md <-> install.ps1 -------------------------------------------------------------

def test_install_md_names_exactly_the_optional_rails_the_installer_offers():
    ps1 = _read("deploy/installer/install.ps1")
    block = ps1.split("$OptionalRails = @(", 1)[1].split("\n)", 1)[0]
    offered = {t.split(" (")[0].strip() for t in re.findall(r"Text = '([^']+)'", block)}
    assert offered, "parsed no rails from install.ps1's $OptionalRails"

    doc = " ".join(_read("docs/INSTALL.md").split())
    seg = doc.split("then the optional ones:", 1)[1].split("That list is", 1)[0]
    seg = re.sub(r"\([^)]*\)", "", seg).rstrip(" .")
    named = {s.strip() for s in re.split(r",| and ", seg) if s.strip()}
    assert named == offered, (f"INSTALL.md names {sorted(named)}; install.ps1 offers "
                              f"{sorted(offered)}")
