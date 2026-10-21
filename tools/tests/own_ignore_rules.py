"""Whether a tree's OWN .gitignore files ignore a path, asked of git without the tree's checkout.

A `git check-ignore` run in the checkout answers for every rule git reads there, and some of those
are the machine's, not the tree's: a global excludes file (core.excludesFile, which defaults to
~/.config/git/ignore), the system config, an init.templateDir that seeds a new repository's
info/exclude. A test that asked there passed on a box that ignores the path by its own setup, for
a tree that ships no such rule and would commit the file anywhere else. And in a tree that is not
a checkout (CI's dry run checks the public snapshot as a built directory) check-ignore exits 128.
So the question goes to a scratch repository that holds only the tree's .gitignore files on the
way to the path, with no global or system config read and core.excludesFile naming an empty file.

Used by tools/tests/test_lean_broker_files.py (deploy/local/) and rails/ai-voice's
test_db_connections.py (var/), whose conftest puts tools/tests on sys.path as every suite's does.
Moved here from test_lean_broker_files.py on 2026-10-20 so the two share it. Not a test file.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def ignored_by_its_own_rules(tree: Path, rel: str, scratch: Path) -> bool:
    """Whether git ignores `rel` (repo-relative, '/') by the .gitignore files `tree` itself holds
    on the way to it, asked of a scratch repository under `scratch` that holds only those files."""
    repo, empty = scratch / "repo", scratch / "empty"
    repo.mkdir(parents=True)
    empty.write_text("", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=str(empty), GIT_CONFIG_NOSYSTEM="1")
    subprocess.run(["git", "init", "-q", str(repo)], env=env, check=True, timeout=60,
                   capture_output=True)
    parts = rel.split("/")
    for depth in range(len(parts)):     # the root's .gitignore, then each directory's on the way
        here = Path(*parts[:depth])
        if (tree / here / ".gitignore").is_file():
            (repo / here).mkdir(parents=True, exist_ok=True)
            shutil.copyfile(tree / here / ".gitignore", repo / here / ".gitignore")
    r = subprocess.run(["git", "-C", str(repo), "-c", f"core.excludesFile={empty.as_posix()}",
                        "check-ignore", "-q", rel],
                       env=env, capture_output=True, text=True, timeout=60)
    if r.returncode not in (0, 1):
        raise RuntimeError(f"git check-ignore exited {r.returncode}: {r.stderr.strip()}")
    return r.returncode == 0
