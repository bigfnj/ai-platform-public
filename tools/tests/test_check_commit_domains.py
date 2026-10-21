"""tools/check_commit_domains.py: the guard on author and committer email domains.

Every test builds a throwaway repository and runs the tool the way a CI job would, as a
subprocess with a base and a head SHA, so what is asserted is the exit code and output a
workflow sees.
Disallowed identities use the reserved `.example` TLD; no real third-party domain appears here.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1] / "check_commit_domains.py"
ZERO = "0" * 40
OK = "owner-one@gmail.com"
NOREPLY = "12345+owner-one@users.noreply.github.com"
FOREIGN = "bob@corp.example"


class Repo:
    def __init__(self, root: Path):
        self.root = root
        cfg = root.parent / "gitconfig-empty"
        cfg.write_text("", encoding="utf-8")
        # Isolated from the box's own git config: a global commit.gpgsign or hook path must not
        # decide whether a fixture commit can be made.
        self.env = dict(os.environ, GIT_CONFIG_GLOBAL=str(cfg), GIT_CONFIG_NOSYSTEM="1",
                        GIT_AUTHOR_NAME="t", GIT_COMMITTER_NAME="t")
        root.mkdir()
        self.git("init", "-q")
        self.n = 0

    def git(self, *args: str) -> str:
        return subprocess.run(["git", "-C", str(self.root), *args], check=True, env=self.env,
                              capture_output=True, text=True).stdout.strip()

    def commit(self, author: str = OK, committer: str | None = None) -> str:
        self.n += 1
        (self.root / f"f{self.n}.txt").write_text(str(self.n), encoding="utf-8")
        self.git("add", "-A")
        env = dict(self.env, GIT_AUTHOR_EMAIL=author, GIT_COMMITTER_EMAIL=committer or author)
        subprocess.run(["git", "-C", str(self.root), "commit", "-q", "-m", f"c{self.n}"],
                       check=True, env=env, capture_output=True)
        return self.git("rev-parse", "HEAD")


def run(repo: Repo, base: str, head: str) -> tuple[int, str]:
    p = subprocess.run([sys.executable, str(TOOL), base, head, "--repo", str(repo.root)],
                       capture_output=True, text=True, env=repo.env, timeout=120)
    return p.returncode, p.stdout + p.stderr


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    return Repo(tmp_path / "r")


def test_allowed_domains_pass(repo):
    base = repo.commit()
    repo.commit(author=OK)
    head = repo.commit(author=NOREPLY)
    code, out = run(repo, base, head)
    assert code == 0, out
    assert "checked 2 commit(s); 0 disallowed" in out


def test_a_foreign_author_domain_fails_and_names_only_the_domain(repo):
    base = repo.commit()
    bad = repo.commit(author=FOREIGN, committer=OK)
    head = repo.commit()
    code, out = run(repo, base, head)
    assert code == 1, out
    assert f"{bad[:12]}  author domain not allowed: corp.example" in out
    # The log of a failing run must not republish the address itself.
    assert FOREIGN not in out


def test_a_foreign_committer_domain_fails(repo):
    base = repo.commit()
    bad = repo.commit(author=OK, committer=FOREIGN)
    code, out = run(repo, base, bad)
    assert code == 1, out
    assert f"{bad[:12]}  committer domain not allowed: corp.example" in out


def test_only_commits_in_the_range_are_judged(repo):
    """History already on the branch is not re-judged, or the commits that predate this guard
    would fail every push for ever."""
    repo.commit(author=FOREIGN)
    base = repo.commit()
    head = repo.commit()
    code, out = run(repo, base, head)
    assert code == 0, out
    assert "checked 1 commit(s)" in out


def test_a_merge_brings_its_side_branch_into_the_range(repo):
    """A merged branch's own commits are new to the target branch too, not only the merge."""
    base = repo.commit()
    repo.git("checkout", "-q", "-b", "side")
    bad = repo.commit(author=FOREIGN)
    repo.git("checkout", "-q", "-")
    repo.commit()
    merge_env = dict(repo.env, GIT_AUTHOR_EMAIL=OK, GIT_COMMITTER_EMAIL=OK)
    subprocess.run(["git", "-C", str(repo.root), "merge", "-q", "--no-ff", "-m", "m", "side"],
                   check=True, env=merge_env, capture_output=True)
    head = repo.git("rev-parse", "HEAD")
    code, out = run(repo, base, head)
    assert code == 1, out
    assert bad[:12] in out


@pytest.mark.parametrize("email", [
    "x@sub.gmail.com",               # a subdomain is a different domain
    "x@gmail.com.corp.example",      # an allowed name as a PREFIX
    "no-at-sign",                    # git accepts any string here
])
def test_lookalike_domains_are_refused(repo, email):
    base = repo.commit()
    head = repo.commit(author=email)
    code, out = run(repo, base, head)
    assert code == 1, out


def test_domain_case_is_ignored(repo):
    base = repo.commit()
    head = repo.commit(author="Owner-One@GMAIL.COM")
    code, out = run(repo, base, head)
    assert code == 0, out


@pytest.mark.parametrize("base", [ZERO, "", "ab" * 20])
def test_an_unknown_base_checks_the_tip_and_says_so(repo, base):
    repo.commit(author=FOREIGN)          # older than the tip: NOT judged in degraded mode
    head = repo.commit()
    code, out = run(repo, base, head)
    assert code == 0, out
    assert "::warning" in out and "DEGRADED" in out and "only the tip commit" in out
    assert "checked 1 commit(s)" in out


def test_a_degraded_run_still_fails_a_foreign_tip(repo):
    repo.commit()
    head = repo.commit(author=FOREIGN)
    code, out = run(repo, ZERO, head)
    assert code == 1, out
    assert "DEGRADED" in out


def test_a_bad_head_is_an_error_not_a_pass(repo):
    base = repo.commit()
    code, out = run(repo, base, "cd" * 20)
    assert code == 2, out
    assert "ERROR" in out


def test_the_allowlist_is_exactly_the_two_domains():
    import importlib.util
    spec = importlib.util.spec_from_file_location("_check_commit_domains", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.ALLOWED_DOMAINS == ("gmail.com", "users.noreply.github.com")
