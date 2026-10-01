"""Mutation harness for named broker tokens. NOT a pytest file -- it rewrites the tree.

    .venv\\Scripts\\python.exe services\\broker\\tests\\mutate_broker_tokens.py

This is an authentication control, so the only thing that matters is whether it can FAIL OPEN.
Every mutation below removes one guard and requires a named test to notice. Four of them are
the shapes that would be invisible in normal use:

  * a revoked token still working (the whole point of the feature)
  * an `inference` token reaching a mutating route (the scope being decorative)
  * revoking the LAST token reopening the broker to everyone (the footgun the store-exists
    check closes, found by writing the test rather than by reading the code)
  * the plaintext being persisted (which would make the hash pointless)

The last one is mutated against the STORE rather than the auth path, because a token list you
can read off disk is not protected by any amount of correct comparison.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BROKER = Path(__file__).resolve().parents[1]
REPO = BROKER.parents[1]
PY = REPO / ".venv" / "Scripts" / "python.exe"
SUITE = ["tests/test_auth_token.py", "tests/test_auth_token_file.py", "tests/test_tokens_store.py"]

MAIN = "app/main.py"
CFG = "app/config.py"

MUTATIONS: list[tuple[str, str, str, str, str]] = [
    (
        "a revoked token is still accepted",
        MAIN,
        "    match = next((r for r in named\n"
        "                  if secrets.compare_digest(str(r.get(\"hash\", \"\")), digest)), None)\n"
        "    if match is None:\n"
        "        raise HTTPException(status_code=401, detail=\"invalid or missing broker token\")",
        "    match = next((r for r in named\n"
        "                  if secrets.compare_digest(str(r.get(\"hash\", \"\")), digest)), None)\n"
        "    if match is None:\n"
        "        return",
        "test_a_revoked_token_is_refused",
    ),
    (
        "revoking the last token reopens the broker to everyone",
        MAIN,
        "    if not env_token and not settings.tokens_configured():",
        "    if not env_token and not named:",
        "test_an_EMPTY_store_still_closes_the_broker",
    ),
    (
        "the scope check is removed, so an inference token can repoint a role",
        MAIN,
        '    if _needs_full(request.method, request.url.path) and match.get("scope") != "full":',
        "    if False:",
        "test_inference_scope_cannot_repoint_a_role",
    ),
    (
        "the mutating-route list loses /v1/tokens, so a token can mint tokens",
        MAIN,
        '    ("GET", "/v1/tokens"),\n    ("POST", "/v1/tokens"),\n    ("DELETE", "/v1/tokens"),',
        '    ("GET", "/v1/zzz"),',
        "test_inference_scope_cannot_read_or_mint_tokens",
    ),
    (
        "the hash compare is replaced by a prefix match",
        MAIN,
        '                  if secrets.compare_digest(str(r.get("hash", "")), digest)), None)',
        '                  if str(r.get("prefix", "")).startswith(supplied)), None)',
        "test_a_token_that_is_a_prefix_of_a_live_one_is_refused",
    ),
    (
        "last-seen is never stamped, so the revoke workflow cannot tell what is in use",
        MAIN,
        '    request.app.state.token_seen[match["id"]] = time.time()',
        "    pass",
        "test_a_named_token_stamps_last_seen",
    ),
    (
        "the plaintext is persisted alongside the hash",
        CFG,
        '            "hash": hashlib.sha256(plain.encode("utf-8")).hexdigest(),',
        '            "hash": hashlib.sha256(plain.encode("utf-8")).hexdigest(),\n'
        '            "token": plain,',
        "test_the_plaintext_is_never_written_to_disk",
    ),
    (
        "a duplicate label is allowed, so two rows are indistinguishable when revoking",
        CFG,
        '        if any(r.get("label") == label for r in rows):',
        "        if False:",
        "test_a_duplicate_label_is_refused",
    ),
    (
        "an unknown scope is accepted and stored",
        CFG,
        "        if scope not in TOKEN_SCOPES:",
        "        if False:",
        "test_an_unknown_scope_is_refused",
    ),
]


def run_suite() -> tuple[int, str]:
    p = subprocess.run([str(PY), "-m", "pytest", *SUITE, "-q", "--no-header",
                        "-p", "no:cacheprovider"],
                       cwd=BROKER, capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def main() -> int:
    code, out = run_suite()
    if code != 0:
        print("BASELINE IS NOT GREEN -- fix that before trusting any mutation result.\n")
        print(out[-2500:])
        return 2
    print(f"baseline green: {out.strip().splitlines()[-1]}\n")

    results: list[tuple[str, str]] = []
    for label, relpath, old, new, expected in MUTATIONS:
        path = BROKER / relpath
        original = path.read_text(encoding="utf-8")
        n = original.count(old)
        if n != 1:
            print(f"  ERROR  {label}: pattern found {n} times in {relpath}, expected 1")
            results.append((label, "PATTERN"))
            continue
        try:
            path.write_text(original.replace(old, new), encoding="utf-8")
            code, out = run_suite()
            fired = code != 0 and expected in out
            verdict = "FIRED" if fired else ("FIRED-WRONG-TEST" if code != 0 else "SURVIVED")
            results.append((label, verdict))
            print(f"  {verdict:17} {label}")
        finally:
            path.write_text(original, encoding="utf-8")

    code, _ = run_suite()
    print(f"\nrestored tree: {'green' if code == 0 else 'STILL RED -- TREE IS DIRTY'}")
    bad = [r for r in results if r[1] != "FIRED"]
    print(f"{len(results) - len(bad)}/{len(results)} mutations fired the right test")
    for label, verdict in bad:
        print(f"  {verdict}: {label}")
    return 0 if not bad and code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
