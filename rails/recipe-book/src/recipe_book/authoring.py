"""Author new recipes and edit existing ones.

New recipes are written as real cards into the markdown corpus (the volume is the
source of truth), so they survive a full rebuild and get a path-stable id like any
shipped recipe. Edits are stored as content overrides (see ``db``) reapplied at
ingest, which keeps the recipe id stable so favorites / ratings / planner entries
stay attached and works uniformly for standalone and collection-derived cards.
"""
from __future__ import annotations

import re
from pathlib import Path

from recipe_book import config

_ILLEGAL = re.compile(r'[\\/:*?"<>|\n\r\t]+')
_BOLD_ONLY = re.compile(r"^\*\*.+\*\*$")


def slug_filename(title: str) -> str:
    name = _ILLEGAL.sub(" ", title).strip().strip(".")
    name = re.sub(r"\s+", " ", name)
    return (name or "Untitled")[:120]


def to_markdown(*, title: str, meta: str = "", ingredients: list[str],
                instructions: list[str], shopping_list: list[str] | None = None,
                source: str = "") -> str:
    """Render structured fields back into the card format ``parse_recipe_markdown``
    expects (``# Title`` / italic meta / ``## Ingredients`` / ``## Instructions`` /
    ``## Shopping List`` / ``--- *Source*``). Lines already wrapped in ``**bold**``
    are kept as group sub-headers rather than list items."""
    out: list[str] = [f"# {title.strip()}", ""]
    if meta.strip():
        out += [f"*{meta.strip()}*", ""]

    out.append("## Ingredients")
    for it in ingredients:
        it = it.strip()
        if not it:
            continue
        out.append(it if _BOLD_ONLY.match(it) else f"- {it}")
    out.append("")

    out.append("## Instructions")
    n = 0
    for it in instructions:
        it = it.strip()
        if not it:
            continue
        if _BOLD_ONLY.match(it):
            out.append(it)
        else:
            n += 1
            out.append(f"{n}. {it}")
    out.append("")

    shopping_list = [s.strip() for s in (shopping_list or []) if s.strip()]
    if shopping_list:
        out.append("## Shopping List")
        out += [f"- {s}" for s in shopping_list]
        out.append("")

    if source.strip():
        out += ["---", f"*Source: {source.strip()}*", ""]
    return "\n".join(out).rstrip() + "\n"


def write_card(*, category: str, title: str, markdown: str,
               overwrite_rel: str | None = None) -> str:
    """Write a card into the corpus and return its posix rel_path. ``overwrite_rel``
    rewrites that exact file; otherwise a unique ``<Category>/<slug>.md`` is created."""
    root = Path(config.RECIPES_DIR)
    if overwrite_rel:
        path = root / overwrite_rel
        path.parent.mkdir(parents=True, exist_ok=True)
        rel = overwrite_rel
    else:
        # Sanitise the category the same way as the filename: strip path separators + leading/
        # trailing dots so a crafted category ("../x", "..") can't write the card outside the corpus.
        safe_cat = _ILLEGAL.sub(" ", category).strip().strip(".") or "Uncategorized"
        folder = root / safe_cat
        folder.mkdir(parents=True, exist_ok=True)
        base = slug_filename(title)
        path = folder / f"{base}.md"
        n = 2
        while path.exists():
            path = folder / f"{base} ({n}).md"
            n += 1
        rel = path.relative_to(root).as_posix()
    path.write_text(markdown, encoding="utf-8")
    return rel.replace("\\", "/")
