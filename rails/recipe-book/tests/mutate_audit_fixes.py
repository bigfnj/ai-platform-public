"""Mutation harness for the 2026-10-08 audit fixes in recipe-book. NOT a pytest file.

    python rails\\recipe-book\\tests\\mutate_audit_fixes.py [label-substring ...]

Each guard is removed in turn and the NAMED test must fail. A verdict needs three things: the
suite fails, the EXPECTED test is in pytest's FAILED lines, and the run imported recipe_book
from THIS tree (the witness), so a mutation cannot "fire" against a copy it never touched.
Every child runs with RECIPE_BOOK_DATA_DIR in a temp directory, so nothing lands in the tree.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents
                            if (p / "tools" / "tests" / "mutation_runner.py").is_file())
                       / "tools" / "tests"))
import mutation_runner as mr  # noqa: E402

RAIL = Path(__file__).resolve().parents[1]
SRC = RAIL / "src"
PY = sys.executable
TIMEOUT = 240

G = "src/recipe_book/gtasks.py"
P = "src/recipe_book/api/routers/planner.py"
REV = "tests/test_gtasks_revocation.py"
SEM = "src/recipe_book/semantic.py"
SM = "tests/test_semantic_model.py"
IC = "tests/test_import_caps.py"
BF = "tests/test_background_failures.py"
CW = "tests/test_category_write.py"
BL = "tests/test_blocked_ingredient.py"
SC = "tests/test_semantic_concurrency.py"
CAT = "src/recipe_book/catalog.py"
AU = "src/recipe_book/api/routers/authoring.py"

# (label, file, old, new, expected test, suite)
MUTATIONS: list[tuple[str, str, str, str, str, str]] = [
    (
        "the route forgets the connection on the substring again",
        P,
        "        if exc.revoked:",
        "        if \"invalid_grant\" in msg:",
        "test_a_refresh_failure_that_is_not_invalid_grant_keeps_the_connection[text-only]",
        REV,
    ),
    (
        "every non-200 refresh is labelled revoked",
        G,
        "            if code == \"invalid_grant\":",
        "            if True:",
        "test_a_refresh_failure_that_is_not_invalid_grant_keeps_the_connection[503]",
        REV,
    ),
    (
        "the OAuth error is read from the text, not the parsed body",
        G,
        "    code = body.get(\"error\") if isinstance(body, dict) else None\n"
        "    return code if isinstance(code, str) else \"\"",
        "    return \"invalid_grant\" if \"invalid_grant\" in resp.text else \"\"",
        "test_only_a_parsed_invalid_grant_is_revoked",
        REV,
    ),
    (
        "record: one global lock is held across every refresh again",
        G,
        "    tok = _cached(refresh_token)\n    if tok:\n        return tok\n    with _lock:\n"
        "        per_token = _refreshing.setdefault(refresh_token, threading.Lock())",
        "    with _lock:\n        per_token = _refreshing.setdefault(\"\", threading.Lock())",
        "test_one_users_stalled_refresh_does_not_block_another_users_cached_token",
        REV,
    ),
    (
        "record: a waiter refreshes again instead of reading the token just cached",
        G,
        "        tok = _cached(refresh_token)          # refreshed by another request while this one"
        " waited\n        if tok:\n            return tok\n",
        "",
        "test_concurrent_refreshes_of_one_token_make_one_call",
        REV,
    ),
    (
        "embed slot: the default goes back to the literal bge-m3",
        "src/recipe_book/broker.py",
        'EMBED_MODEL = os.environ.get("RECIPE_BOOK_EMBED_MODEL", "@embed")',
        'EMBED_MODEL = os.environ.get("RECIPE_BOOK_EMBED_MODEL", "bge-m3")',
        "test_the_default_is_the_platform_embed_role",
        SM,
    ),
    (
        "embed slot: the full-stack compose stops passing it",
        "../../deploy/docker-compose.yml",
        "      RECIPE_BOOK_EMBED_MODEL: ${RECIPE_BOOK_EMBED_MODEL:-@embed}\n",
        "",
        "test_the_compose_service_passes_it",
        SM,
    ),
    (
        "embed slot: the manifest stops declaring it",
        "rail.json",
        '"slot": "embed",',
        '"slot": "retrieval-undeclared",',
        "test_the_manifest_declares_the_embed_slot_the_code_reads",
        SM,
    ),
    (
        "embed slot: the header draws no embedder chip",
        "src/recipe_book/api/app.py",
        '            ("embed", "Semantic search", broker.EMBED_MODEL),\n',
        "",
        "test_the_header_draws_a_chip_for_the_embedder",
        SM,
    ),
    (
        "record: a different width is scored by truncation again",
        SEM,
        "    if vectors and len(vec) != len(vectors[0]):",
        "    if False:",
        "test_a_different_width_falls_back_instead_of_truncating",
        SM,
    ),
    (
        "record: a repointed same-width embedder is not noticed",
        SEM,
        "            and _norm_tag(model) != _norm_tag(built):",
        "            and False:",
        "test_a_repointed_embedder_of_the_same_width_falls_back_to_lexical",
        SM,
    ),
    (
        "record: a bare tag reads as a different model (the @embed switch kills search)",
        SEM,
        '    return name if ":" in name else name + ":latest"',
        "    return name",
        "test_a_legacy_index_named_by_its_bare_tag_still_answers",
        SM,
    ),
    (
        "record: query skips the guard",
        SEM,
        "    why = _mismatch(used, qvec)",
        '    why = ""',
        "test_a_repointed_embedder_of_the_same_width_falls_back_to_lexical",
        SM,
    ),
    (
        "record: add folds a foreign vector in",
        SEM,
        "    why = _mismatch(used, vec)",
        '    why = ""',
        "test_add_refuses_a_vector_from_another_model",
        SM,
    ),
    (
        "record: build records the ref instead of the concrete model",
        SEM,
        "        model = model or used",
        '        model = model or ""',
        "test_the_index_records_the_concrete_model_it_was_built_with",
        SM,
    ),
    (
        "record: a rebuild leaves the stale mark",
        SEM,
        "        _STALE = \"\"\n        _LAST_ERROR = \"\"\n        _persist(ids, vectors, model)",
        "        _LAST_ERROR = \"\"\n        _persist(ids, vectors, model)",
        "test_a_rebuild_clears_the_stale_mark",
        SM,
    ),
    (
        "a category write for a missing recipe writes an override again",
        "src/recipe_book/api/routers/recipes.py",
        "    if state.catalog().get(recipe_id) is None:\n"
        "        raise HTTPException(status_code=404, detail=\"recipe not found\")\n"
        "    kind = \"beverage\"",
        "    kind = \"beverage\"",
        "test_a_missing_recipe_is_404_and_writes_no_override",
        CW,
    ),
    (
        "the file count is unbounded again",
        AU,
        "    if len(files) > extraction.MAX_FILES:",
        "    if False:",
        "test_too_many_files_is_413",
        IC,
    ),
    (
        "each file is read whole again",
        AU,
        "        if len(data) > extraction.MAX_FILE_BYTES:",
        "        if False:",
        "test_a_file_over_the_byte_cap_is_413",
        IC,
    ),
    (
        "a .docx inflates with no budget",
        "src/recipe_book/extraction.py",
        "    if xml > _MAX_DOCX_XML or sum(i.file_size for i in infos) > _MAX_DOCX_TOTAL:",
        "    if False:",
        "test_a_docx_that_inflates_past_the_budget_is_413",
        IC,
    ),
    (
        "the imported text reaches the assistant uncapped",
        AU,
        "    text = \"\\n\\n\".join(texts)[:extraction.MAX_TEXT_CHARS]",
        "    text = \"\\n\\n\".join(texts)",
        "test_the_text_sent_to_the_assistant_is_capped_like_the_url_import",
        IC,
    ),
    (
        "a failed reindex is swallowed again",
        "src/recipe_book/api/app.py",
        "                semantic.record_failure(exc)",
        "                pass",
        "test_the_reindex_route_records_a_failure",
        BF,
    ),
    (
        "a good build leaves the last failure showing",
        SEM,
        "        _STALE = \"\"\n        _LAST_ERROR = \"\"\n        _persist(ids, vectors, model)",
        "        _STALE = \"\"\n        _persist(ids, vectors, model)",
        "test_a_failed_build_is_recorded_and_a_good_one_clears_it",
        BF,
    ),
    (
        "a failed index add is silent again",
        AU,
        "        log.warning(\"semantic index did not take %s: %s\", recipe_id, exc)",
        "        pass",
        "test_a_failed_index_add_and_icon_are_logged",
        BF,
    ),
    (
        "a failed icon is silent again",
        AU,
        "        log.warning(\"icon for %s was not generated: %s\", recipe_id, exc)",
        "        pass",
        "test_a_failed_index_add_and_icon_are_logged",
        BF,
    ),
    (
        "the pairing 404 tells people to add bottles again",
        "src/recipe_book/api/routers/planner.py",
        "                \"Every cocktail in the book has been suggested for this day — try a wine.\"))",
        "                \"No cocktail in your Bar to pair — add bottles, or try a wine.\"))",
        "test_an_exhausted_cocktail_pool_does_not_blame_the_bar",
        BF,
    ),
    (
        "record: a wordless line is blocked again (the 4-cloves inversion)",
        CAT,
        "    if not words or not unavailable:\n        return False",
        "    if False:\n        return False",
        "test_a_wordless_line_does_not_hold_a_recipe_one_away",
        BL,
    ),
    (
        "record: match_pantry asks the coverage question for 'blocked' again",
        CAT,
        "                blocked = ingredient_is_blocked(ing, unavailable, beverage=bev)",
        "                blocked = ingredient_is_covered(ing, unavailable, beverage=bev)",
        "test_a_wordless_line_does_not_hold_a_recipe_one_away",
        BL,
    ),
    (
        "record: shopping_list asks the coverage question for 'blocked' again",
        CAT,
        "                blocked = ingredient_is_blocked(ing, avoid)",
        "                blocked = ingredient_is_covered(ing, avoid)",
        "test_a_wordless_line_is_not_put_on_the_shopping_list",
        BL,
    ),
    (
        "race: add() writes the index without the lock",
        SEM,
        "    with _LOCK:\n        why = _mismatch(used, vec)",
        "    if True:\n        why = _mismatch(used, vec)",
        "test_every_write_to_disk_holds_the_lock",
        SC,
    ),
    (
        "race: build() swaps and writes without the lock",
        SEM,
        "    with _LOCK:\n        _INDEX = {",
        "    if True:\n        _INDEX = {",
        "test_every_write_to_disk_holds_the_lock",
        SC,
    ),
    (
        "record: the faster scoring drops the norm (not cosine any more)",
        SEM,
        "    scored = [(rid, sum(map(mul, qvec, vec)) / (qnorm * vnorm))",
        "    scored = [(rid, sum(map(mul, qvec, vec)))",
        "test_the_ranking_is_exactly_cosine",
        SC,
    ),
    (
        "record: incremental ingest parses every card again",
        "src/recipe_book/ingest.py",
        "        if not full and existing.get(recipe_id(rel)) == h:\n"
        "            seen.add(recipe_id(rel))\n            unchanged += 1\n            continue\n",
        "",
        "test_incremental_ingest_skips_unchanged_cards_and_takes_an_edit",
        SC,
    ),
]


def _env(data_dir: str) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["RECIPE_BOOK_DATA_DIR"] = data_dir
    env.pop("PLATFORM_STANDALONE", None)
    return env


def witness(data_dir: str) -> str:
    p = mr.run_tree([PY, "-c", "import recipe_book.gtasks as g; print(g.__file__)"],
                    cwd=RAIL, capture_output=True, text=True, env=_env(data_dir), timeout=60)
    return p.stdout.strip()


def run_suite(suites: list[str], data_dir: str) -> tuple[int, str]:
    try:
        p = mr.run_tree([PY, "-m", "pytest", *suites, "-q", "--no-header", "-rf",
                         "-p", "no:cacheprovider"],
                        cwd=RAIL, capture_output=True, text=True, env=_env(data_dir),
                        timeout=TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or b"").decode("utf-8", "replace") if isinstance(exc.stdout, bytes) \
            else (exc.stdout or "")
        return 124, out + f"\nHARNESS TIMEOUT after {TIMEOUT}s"
    return p.returncode, p.stdout + p.stderr


def main() -> int:
    only = mr.parse_harness_args(MUTATIONS, filter=True).only
    chosen = [m for m in MUTATIONS if not only or any(o in m[0] for o in only)]
    suites = sorted({m[5] for m in chosen})
    with tempfile.TemporaryDirectory(prefix="rb-mutate-") as data_dir:
        where = witness(data_dir)
        expected_file = (RAIL / G).resolve()
        if not where or Path(where).resolve() != expected_file:
            print(f"WITNESS FAILED: the child imports gtasks.py from {where!r}, "
                  f"not {expected_file}")
            return 2
        print(f"witness: recipe_book imported from {Path(where).parent}")
        code, out = run_suite(suites, data_dir)
        if code != 0:
            print("BASELINE IS NOT GREEN -- fix that before trusting any mutation result.\n")
            print(out[-2500:])
            return 2
        print(f"baseline green: {out.strip().splitlines()[-1]}\n")

        results: list[tuple[str, str]] = []
        for label, relpath, old, new, expected, suite in chosen:
            path = (RAIL / relpath).resolve()
            original = path.read_bytes()
            n, mutated = mr.mutate_bytes(original, old, new)
            if n != 1:
                print(f"  ERROR  {label}: pattern found {n} times in {relpath}, expected 1")
                results.append((label, "PATTERN"))
                continue
            try:
                path.write_bytes(mutated)
                code, out = run_suite([suite], data_dir)
                named = mr.named_failed(out, expected)
                verdict = ("TIMEOUT" if code == 124 else
                           "FIRED" if code != 0 and named else
                           "FIRED-WRONG-TEST" if code != 0 else "SURVIVED")
                results.append((label, verdict))
                print(f"  {verdict:17} {label}")
                if verdict != "FIRED":
                    tail = [ln for ln in out.splitlines() if ln.strip()][-12:]
                    print("      " + "\n      ".join(tail))
            finally:
                mr.restore_exact(path, original)

        code, _ = run_suite(suites, data_dir)
    print(f"\nrestored tree: {'green' if code == 0 else 'STILL RED -- TREE IS DIRTY'}")
    bad = [r for r in results if r[1] != "FIRED"]
    print(f"{len(results) - len(bad)}/{len(results)} mutations fired the right test")
    for label, verdict in bad:
        print(f"  {verdict}: {label}")
    return 0 if not bad and code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
