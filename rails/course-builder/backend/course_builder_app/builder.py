"""RAG retrieval and course-source assembly.

Ported from tools/snu-course-build.py (ai-notes). Runs synchronously — call from
a ThreadPoolExecutor. Returns a markdown string; the API layer writes it to disk or
streams it for download.
"""
from __future__ import annotations

import csv
import pathlib
import re
from typing import Callable

import numpy as np

from . import broker as _broker
from .corpus import ESCAPE_RE, load_matrix

MODULE_CONTEXT_CHARS = 24_000

CONDENSE_SYS = (
    "You turn product documentation excerpts into teaching source material. "
    "Use ONLY facts present in the excerpts. Never invent field names, table "
    "names, menu paths or version numbers. If the excerpts do not cover part "
    "of the topic, omit it silently rather than filling the gap. Write plain "
    "markdown prose and bullets, no headings, no preamble."
)


def _as_weight(value: object) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return 0.0


def _excerpt_header(hit: dict) -> str:
    heading = hit["heading"] or ""
    if heading == hit["title"]:
        heading = ""
    elif heading.startswith(hit["title"] + " > "):
        heading = heading[len(hit["title"]) + 3:]
    return (f"{hit['title']} -- {heading}") if heading else hit["title"]


def _search(
    rows: list[dict],
    mat: np.ndarray,
    query: str,
    k: int,
    embed_role: str,
    per_file: int = 3,
) -> list[dict]:
    q = np.asarray(_broker.embed_sync([query], embed_role)[0], dtype=np.float32)
    q /= (np.linalg.norm(q) + 1e-9)
    order = np.argsort(-(mat @ q))
    hits: list[dict] = []
    seen: dict[str, int] = {}
    for i in order:
        row = rows[i]
        if seen.get(row["path"], 0) >= per_file:
            continue
        seen[row["path"]] = seen.get(row["path"], 0) + 1
        hits.append(row)
        if len(hits) >= k:
            break
    return hits


# --- blueprint CSV ------------------------------------------------------------

def load_blueprints(csv_path: str) -> list[dict]:
    """Return [{id, name, domains: [{name, weight, skills}]}] from the blueprint CSV."""
    if not csv_path or not pathlib.Path(csv_path).exists():
        return []
    credentials: dict[str, dict] = {}
    with open(csv_path, encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            code = (r.get("credential_code") or "").strip()
            name = (r.get("credential_name") or "").strip()
            if not code:
                continue
            cred = credentials.setdefault(
                code, {"id": code, "name": name or code, "domains": {}})
            dom = (r.get("domain_name") or "").strip()
            if not dom:
                continue
            entry = cred["domains"].setdefault(
                dom, {"name": dom, "weight": r.get("weight_percent") or "", "skills": []})
            sub = (r.get("sub_skill") or "").strip()
            if sub and sub not in entry["skills"]:
                entry["skills"].append(sub)
    result = [
        {"id": c["id"], "name": c["name"], "domains": list(c["domains"].values())}
        for c in credentials.values()
    ]
    return sorted(result, key=lambda c: c["id"])


def _blueprint_modules(
    csv_path: str, code: str
) -> list[tuple[str, str, list[str]]]:
    want = code.strip().lower()
    for bp in load_blueprints(csv_path):
        if bp["id"].lower() == want or bp["name"].lower() == want:
            return [(d["name"], d["weight"], d["skills"]) for d in bp["domains"]]
    raise ValueError(f"no blueprint matched {code!r} in {csv_path or '(no CSV configured)'}")


# --- build job (runs in thread pool) ------------------------------------------

def run_build(
    db_path: str,
    embed_role: str,
    condense_role: str,
    prompt: str,
    blueprint_csv: str = "",
    blueprint: str = "",
    k: int = 12,
    raw: bool = False,
    title: str = "",
    progress: Callable[[str], None] | None = None,
) -> str:
    """Retrieve + assemble (+ optionally condense) a course-source .md string."""
    rows, mat = load_matrix(db_path)

    if not blueprint:
        raise ValueError(
            "A blueprint code is required (e.g. CIS-CSM). "
            "Free-form LLM planning is not supported in this version.")

    modules = _blueprint_modules(blueprint_csv, blueprint)
    if progress:
        progress(f"Blueprint {blueprint}: {len(modules)} modules")

    pool = k * max(1, len(modules))
    weighted = sum(_as_weight(w) for _, w, _ in modules) > 0

    built: list[dict] = []
    for name, weight, subs in modules:
        label = f"{name} ({weight}% of exam)" if weight else name
        budget = max(6, round(pool * _as_weight(weight) / 100)) if weighted else k
        queries = [name] + subs[:6]
        hits: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for q in queries:
            per_q = max(2, budget // len(queries))
            for h in _search(rows, mat, q, per_q, embed_role):
                key = (h["path"], h["heading"])
                if key not in seen:
                    seen.add(key)
                    hits.append(h)

        excerpts: list[str] = []
        cites: list[str] = []
        used = 0
        for h in hits:
            block = f"### {_excerpt_header(h)}\n{h['text']}"
            if used + len(block) > MODULE_CONTEXT_CHARS:
                break
            excerpts.append(block)
            used += len(block)
            url = ESCAPE_RE.sub(r"\1", h["canonical_url"]) or h["path"]
            if url not in cites:
                cites.append(url)

        if progress:
            progress(f"Retrieved {len(excerpts):2d} excerpts ← {label[:60]}")
        if excerpts:
            built.append({"label": label, "name": name, "cites": cites,
                          "body": "\n\n".join(excerpts)})

    if not built:
        raise ValueError("Nothing retrieved — check the index covers this topic")

    if not raw:
        if progress:
            progress(f"Condensing {len(built)} modules via {condense_role}…")
        for m in built:
            msgs = [
                {"role": "system", "content": CONDENSE_SYS},
                {"role": "user", "content": (
                    f"Module: {m['name']}\nCourse brief: {prompt.strip()}\n\n"
                    "Condense the excerpts below into teaching source material for this module. "
                    "Keep concrete specifics (field names, table names, navigation paths, "
                    "prerequisites). Drop navigation boilerplate and anything off-topic.\n\n"
                    "--- EXCERPTS ---\n" + m["body"]
                )},
            ]
            m["body"] = _broker.chat_sync(msgs, condense_role)
            if progress:
                progress(f"  {m['label'][:60]} → {len(m['body'])} chars")

    course_title = title.strip() or prompt.strip().rstrip(".")
    parts = [
        f"# {course_title}",
        "",
        f"> Course brief: {prompt.strip()}",
        "",
    ]
    for m in built:
        parts += [
            f"## {m['label']}",
            "",
            m["body"],
            "",
            "<!-- sources: %s -->" % "; ".join(m["cites"][:12]),
            "",
        ]
    return "\n".join(parts)
