"""Refuse commits whose author or committer email is outside an allowlist of domains.

    python tools/check_commit_domains.py <base-sha> <head-sha> [--repo PATH]

Checks every commit reachable from HEAD and not from BASE, which is "the commits this push or
pull request adds": a CI job that runs it passes a push's before/after SHAs or a pull request's
base/head, and a pre-push hook the range it is about to push. History
that is already on the branch is not re-judged, so older commits made before this guard existed
do not fail every future push; that history is a separate decision, not this check's.

An ALLOWLIST, not a denylist. A denylist has to name the identity it keeps out, which would put
that identity into a tracked, mirrored file, and it fails open the moment a commit arrives under
any domain nobody thought to list. The comparison is exact on the whole domain, case-folded:
a suffix test would accept any domain that merely ENDS in an allowed one.

Exit codes: 0 every checked commit is allowed; 1 at least one is not; 2 the check could not run
(a bad SHA, git missing). An error is never a pass.

When BASE is unknown (all zeros, empty, or not in the clone even after one fetch attempt) only
HEAD itself can be judged. That is announced on every such run, as a GitHub `::warning::`
annotation and in plain text, and never passed off as a full check.

Offending commits are reported by SHA and DOMAIN only, never the full address: the domain is
what the policy is about, and the log of a failing run should not republish the address.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

ALLOWED_DOMAINS = ("gmail.com", "users.noreply.github.com")

_ZERO = "0" * 40


class GitError(RuntimeError):
    pass


def _git(repo: str, *args: str) -> str:
    # No credential prompt: run by hand against an HTTPS remote, the one fetch below would
    # otherwise sit waiting for a password nobody is there to type.
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    try:
        p = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=120, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitError(f"git {' '.join(args)}: {exc}") from exc
    if p.returncode != 0:
        raise GitError(f"git {' '.join(args)} exited {p.returncode}: {p.stderr.strip()}")
    return p.stdout


def _has_commit(repo: str, sha: str) -> bool:
    try:
        _git(repo, "cat-file", "-e", f"{sha}^{{commit}}")
        return True
    except GitError:
        return False


def domain_of(email: str) -> str:
    """The part after the LAST `@`, case-folded. No `@` at all yields "" (never allowed)."""
    return email.rsplit("@", 1)[1].strip().lower() if "@" in email else ""


def is_allowed(email: str) -> bool:
    return domain_of(email) in ALLOWED_DOMAINS


def resolve_range(repo: str, base: str, head: str) -> tuple[list[str], str | None]:
    """The rev-list arguments to check, and a degradation notice when only HEAD can be."""
    base = (base or "").strip()
    if base and base != _ZERO and not _has_commit(repo, base):
        # A force-push leaves the old tip out of a fresh clone, but the server usually still
        # serves it by SHA. One attempt; failure is handled below, not raised.
        try:
            _git(repo, "fetch", "--quiet", "--no-tags", "origin", base)
        except GitError:
            pass
    if not base or base == _ZERO or not _has_commit(repo, base):
        why = "no base commit" if not base or base == _ZERO else f"base {base[:12]} is not available"
        return ([head, "--no-walk"],
                f"{why}; checked only the tip commit {head[:12]}, not the whole push")
    return ([f"{base}..{head}"], None)


def check(repo: str, base: str, head: str) -> tuple[int, list[str]]:
    lines: list[str] = []
    if not _has_commit(repo, head):
        raise GitError(f"head {head!r} is not a commit in {repo}")
    revs, degraded = resolve_range(repo, base, head)
    if degraded:
        lines.append(f"::warning title=commit domains degraded::{degraded}")
        lines.append(f"DEGRADED: {degraded}")
    out = _git(repo, "log", "--format=%H%x1f%ae%x1f%ce", *revs)
    commits = [ln.split("\x1f") for ln in out.splitlines() if ln.strip()]
    bad = 0
    for sha, author, committer in commits:
        for role, email in (("author", author), ("committer", committer)):
            if not is_allowed(email):
                bad += 1
                lines.append(f"  {sha[:12]}  {role} domain not allowed: "
                             f"{domain_of(email) or '(no domain)'}")
    lines.append(f"checked {len(commits)} commit(s); {bad} disallowed author/committer "
                 f"domain(s); allowed: {', '.join(ALLOWED_DOMAINS)}")
    return (1 if bad else 0), lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("base", help="the commit the push started from (all zeros or '' if none)")
    ap.add_argument("head", help="the commit the push ends at")
    ap.add_argument("--repo", default=".", help="repository to inspect (default: cwd)")
    args = ap.parse_args(argv)
    try:
        code, lines = check(args.repo, args.base, args.head)
    except GitError as exc:
        print(f"ERROR: cannot check commit domains: {exc}")
        return 2
    print("\n".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main())
