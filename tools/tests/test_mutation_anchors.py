"""Every anchor of every Python mutation harness occurs exactly once in its target.

A mutation case replaces one anchor in one file; the harness reports PATTERN, and tests nothing,
when the anchor occurs any other number of times. Nothing counted them between runs, so a change
that moved the code under a case left it dead until somebody ran that harness: two
mutate_rails_probe.py cases sat at zero from the shell-a merge until the 2026-10-15 delta audit
counted 635 broker anchors by hand, and two more (mutate_doc_claims.py's Caddyfile case and
mutate_rc_strict.py's summary-line case) were found dead by this check's first run.

mutation_anchors.py reads each harness's tables without running it (how, in its docstring).
Nothing here starts a harness or a suite.
"""
from __future__ import annotations

import os
import sys
import textwrap
import warnings
from pathlib import Path

import pytest

import mutation_anchors as ma

REPO = Path(__file__).resolve().parents[2]


class AnchorsNotRead(UserWarning):
    """A harness that declares ANCHORS_UNREAD: named on every run, so it is never silent."""


def test_every_anchor_of_every_python_harness_occurs_once_in_its_target():
    report = ma.check(REPO)
    for line in report.unread:
        warnings.warn(AnchorsNotRead(f"anchors not counted: {line}"), stacklevel=1)
    print(f"{report.anchors} anchors in {report.harnesses} harnesses; not read: "
          f"{'; '.join(report.unread) or 'none'}")
    for line in report.notes:                           # a published tree's, said not judged
        print(f"note: {line}")
    assert report.failures == [], "\n".join(report.failures)
    # A reader that read nothing would pass the line above. 88 harnesses read and 3,231 anchors
    # on 2026-10-15; the public snapshot withholds many of them.
    assert report.harnesses >= (20 if ma.published(REPO) else 80), report
    assert report.anchors >= 10 * report.harnesses, report


# --- the reader, on harnesses of its own ---------------------------------------------------------

def _tree(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(text), encoding="utf-8")
    return tmp_path


_HEAD = """\
    import sys
    from pathlib import Path
    REPO = Path(__file__).resolve().parents[1]
    SRC = REPO / "src"
"""
_TAIL = """\

    if __name__ == "__main__":
        sys.exit(main())
"""


def _harness(body: str) -> str:
    return textwrap.dedent(_HEAD) + textwrap.dedent(body) + textwrap.dedent(_TAIL)


def test_a_dead_or_doubled_anchor_fails_naming_its_harness_and_its_case(tmp_path, monkeypatch):
    monkeypatch.setattr(ma, "published", lambda _repo: False)     # a checkout (see below)
    repo = _tree(tmp_path, {
        "src/app.py": "def a():\n    return 1\n\ndef b():\n    return 2\n\ndef b():\n    return 2\n",
        "tests/mutate_x.py": _harness('''
            MUTATIONS = [
                ("a returns 0", "app.py", "    return 1\\n", "    return 0\\n", "test_a"),
                ("a returns nothing", "app.py", "    return one\\n", "    pass\\n", "test_a"),
                ("b returns 0", "app.py", "def b():\\n    return 2\\n", "x", "test_b"),
            ]

            def main():
                for label, relpath, old, new, expected in MUTATIONS:
                    path = SRC / relpath
        '''),
    })
    report = ma.check(repo)
    assert report.harnesses == 1 and report.anchors == 3, report
    assert [f.split(" in ")[0] for f in report.failures] == [
        "tests/mutate_x.py: [a returns nothing] found 0 times",
        "tests/mutate_x.py: [b returns 0] found 2 times"], report.failures
    assert "src/app.py, expected 1" in report.failures[0]


def test_each_table_shape_is_read_by_the_harness_s_own_names(tmp_path):
    """Edit lists of (file, old, new) and of (old, new); controls; a starred layout; a case
    whose second edit is counted in what its first left; a deletion; a record row with further
    edits; a table with two row shapes; a control sharing a case's label, counted in the file as
    it is, not in what that case's edit left."""
    repo = _tree(tmp_path, {
        "src/one.py": "alpha = 1\nbeta = 2\n",
        "src/two.py": "gamma = 3\n",
        "tests/mutate_shapes.py": _harness('''
            import dataclasses

            @dataclasses.dataclass
            class Case:
                label: str
                file: str
                old: str
                new: str
                also: tuple = ()

            EDITS = [
                ("two files", [("src/one.py", "alpha = 1", "alpha = 0"),
                               ("src/two.py", "gamma = 3", "gamma = 0")], "t"),
                ("chained", [("src/one.py", "alpha = 1", "alpha = 9"),
                             ("src/one.py", "alpha = 9", "alpha = 8")], "t"),
                ("destroyed", [("src/one.py", "beta = 2", "beta = 7"),
                               ("src/one.py", "beta = 2", "beta = 6")], "t"),
            ]
            PAIRS = [("pairs", "src/two.py", [("gamma", "g"), ("g = 3", "g = 4")], "t", "x")]
            MUTATIONS = [("starred", "src/one.py", "beta", "b", "t", "suite"),
                         ("starred, short", "src/two.py", "gamma", "g", "t"),
                         ("deleted", "src/two.py", "", None, "t"),
                         ("record", "src/one.py", "alpha", "a", "t")]
            CONTROLS = [("record", "src/one.py", "alpha = 1", "alpha = 1")]   # a label shared
            RECORDS = [Case("a record", "src/one.py", "alpha", "omega", also=(("omega = 1", "x"),))]
            MIXED = [("full", "src/two.py", "gamma", "g", "t"),
                     ("edits only", [("src/one.py", "beta", "b")], "t")]

            def main():
                parse_harness_args(EDITS + PAIRS + RECORDS + MIXED)
                for label, edits, expected in EDITS:
                    for rel, old, new in edits:
                        p = REPO / rel
                for label, rel, edits, test, expect in PAIRS:
                    p = REPO / rel
                    for old, new in edits:
                        pass
                for label, relpath, old, new, expected, *rest in MUTATIONS:
                    p = REPO / relpath
                for label, relpath, old, new in CONTROLS:
                    p = REPO / relpath
                for entry in MIXED:
                    label, path, old, new, expected = entry
                    p = REPO / path
                for c in RECORDS:
                    p = REPO / c.file
        '''),
    })
    r = ma.read_harness(repo, "tests/mutate_shapes.py")
    assert r.problems == [] and r.missing == [] and r.unread == "", r
    got = [(a.case, a.file.name, a.old, a.count) for a in r.anchors]
    assert got == [
        ("two files", "one.py", "alpha = 1", 1), ("two files", "two.py", "gamma = 3", 1),
        ("chained", "one.py", "alpha = 1", 1), ("chained", "one.py", "alpha = 9", 1),
        ("destroyed", "one.py", "beta = 2", 1), ("destroyed", "one.py", "beta = 2", 0),
        ("pairs", "two.py", "gamma", 1), ("pairs", "two.py", "g = 3", 1),
        ("a record", "one.py", "alpha", 1), ("a record", "one.py", "omega = 1", 1),
        ("full", "two.py", "gamma", 1), ("edits only", "one.py", "beta", 1),
        ("starred", "one.py", "beta", 1), ("starred, short", "two.py", "gamma", 1),
        ("record", "one.py", "alpha", 1),
        ("record", "one.py", "alpha = 1", 1),       # counted in the file, not in what "record" left
    ], got


def test_a_row_with_no_target_edits_the_one_file_main_reads(tmp_path):
    repo = _tree(tmp_path, {
        "MAIN.txt": "the main file\n",
        "tests/mutate_plain.py": _harness('''
            MAIN = "MAIN.txt"
            MUTATIONS = [("main", "the main", "a main", "t")]

            def main():
                text = (REPO / MAIN).read_text()
                for label, old, new, expected in MUTATIONS:
                    text.count(old)
        '''),
    })
    r = ma.read_harness(repo, "tests/mutate_plain.py")
    assert [(a.case, a.file.name, a.count) for a in r.anchors] == [("main", "MAIN.txt", 1)], r


def test_a_harness_with_its_own_path_function_is_read_through_it(tmp_path):
    repo = _tree(tmp_path, {
        "deep/rail/src/view.ts": "export const x = 1\n",
        "tests/mutate_hooked.py": _harness('''
            MUTATIONS = [("x is 2", "rail", "view.ts", "x = 1", "x = 2", "check")]

            def anchor_target(case):
                return REPO / "deep" / case[1] / "src" / case[2]

            def main():
                for label, rail, file, old, new, expect in MUTATIONS:
                    pass
        '''),
    })
    r = ma.read_harness(repo, "tests/mutate_hooked.py")
    assert [(a.file.name, a.count) for a in r.anchors] == [("view.ts", 1)], r


def test_a_harness_it_cannot_read_must_say_why_and_one_that_reads_must_not(tmp_path):
    labels_only = _harness('''
        LABELS = ("one", "two")
        %s
        def main():
            parse_harness_args(LABELS)
    ''')
    readable = _harness('''
        MUTATIONS = [("x", "app.py", "x = 1", "x = 2", "t")]
        ANCHORS_UNREAD = "no reason left"

        def main():
            for label, relpath, old, new, expected in MUTATIONS:
                p = SRC / relpath
    ''')
    repo = _tree(tmp_path, {"src/app.py": "x = 1\n",
                            "tests/mutate_silent.py": labels_only % "",
                            "tests/mutate_excused.py": labels_only % 'ANCHORS_UNREAD = "in calls"',
                            "tests/mutate_stale.py": readable})
    report = ma.check(repo)
    assert report.unread == ["tests/mutate_excused.py: in calls"], report
    assert len(report.failures) == 2, report.failures
    assert report.failures[0].startswith("tests/mutate_silent.py: no table of cases with anchors")
    assert "declare ANCHORS_UNREAD" in report.failures[0]
    assert report.failures[1].startswith("tests/mutate_stale.py: declares ANCHORS_UNREAD, but "
                                         "every table reads")


def test_a_row_it_cannot_read_is_named(tmp_path):
    repo = _tree(tmp_path, {
        "src/app.py": "x = 1\n",
        "tests/mutate_odd.py": _harness('''
            MUTATIONS = [("fine", "app.py", "x = 1", "x = 2", "t"),
                         ("odd", 42, "x = 1", "x = 2", "t")]

            def main():
                for label, relpath, old, new, expected in MUTATIONS:
                    p = SRC / relpath
        '''),
    })
    r = ma.read_harness(repo, "tests/mutate_odd.py")
    assert [a.case for a in r.anchors] == ["fine"]
    assert r.problems == ["MUTATIONS: a row this cannot read: 'odd'"], r.problems


def test_a_published_tree_is_read_but_its_counts_and_missing_targets_are_not_judged(
        tmp_path, monkeypatch):
    """In a checkout a missing target and a dead anchor fail; in a published tree (publish's
    output, which drops and rewrites what withheld rails need, and whose harnesses skip that
    their own ways) the dead anchor is a note and the missing target nothing, while a row that
    cannot be read still fails. `published` is switched rather than planted: this file ships,
    and may not name the file whose absence tells a published tree (publish.withheld_refs)."""
    monkeypatch.setattr(ma, "published", lambda _repo: False)
    repo = _tree(tmp_path, {
        "src/app.py": "x = 1\n",
        "tests/mutate_gone.py": _harness('''
            MUTATIONS = [("gone", "nowhere.py", "x", "y", "t"),
                         ("dropped", "app.py", "y = 2", "y = 3", "t"),
                         ("odd", 42, "x = 1", "x = 2", "t")]

            def main():
                for label, relpath, old, new, expected in MUTATIONS:
                    p = SRC / relpath
        '''),
    })
    unreadable = "tests/mutate_gone.py: MUTATIONS: a row this cannot read: 'odd'"
    gone = "tests/mutate_gone.py: [gone] target not in this tree: "
    dropped = "tests/mutate_gone.py: [dropped] found 0 times in src/app.py, expected 1: 'y = 2'"
    checkout = ma.check(repo)
    assert len(checkout.failures) == 3 and checkout.notes == [], checkout
    assert checkout.failures[0].startswith(unreadable)
    assert checkout.failures[1].startswith(gone)
    assert checkout.failures[2] == dropped
    monkeypatch.setattr(ma, "published", lambda _repo: True)
    public = ma.check(repo)
    assert len(public.failures) == 1 and public.failures[0].startswith(unreadable), public
    assert public.notes == [dropped], public


def test_reading_runs_nothing_and_puts_the_interpreter_back(tmp_path):
    """The harness's own __main__ never runs; what its import changes is undone; nothing is
    written beside it."""
    repo = _tree(tmp_path, {
        "src/app.py": "x = 1\n",
        "tests/mutate_noisy.py": _harness('''
            import os
            os.environ["ANCHORS_TEST_SET_AT_IMPORT"] = "1"
            sys.path.insert(0, "somewhere-else")
            import json, mutate_noisy_helper  # noqa: F401  (a module beside it)
            MUTATIONS = [("x", "app.py", "x = 1", "x = 2", "t")]

            def main():
                (REPO / "RAN").write_text("main() ran")
                for label, relpath, old, new, expected in MUTATIONS:
                    p = SRC / relpath
        '''),
        "tests/mutate_noisy_helper.py": "VALUE = 1\n",
    })
    path, modules, env = list(sys.path), set(sys.modules), dict(os.environ)
    r = ma.read_harness(repo, "tests/mutate_noisy.py")
    assert [a.count for a in r.anchors] == [1]
    assert not (repo / "RAN").exists()
    assert sys.path == path and dict(os.environ) == env
    assert not {m for m in set(sys.modules) - modules if "noisy" in m}
    assert not list(repo.rglob("__pycache__"))


@pytest.mark.parametrize("ending", ["\n", "\r\n"])
def test_an_anchor_is_counted_in_either_line_ending_as_the_runner_counts_it(tmp_path, ending):
    repo = _tree(tmp_path, {
        "tests/mutate_eol.py": _harness('''
            MUTATIONS = [("two lines", "app.py", "a = 1\\nb = 2\\n", "a = 0\\n", "t")]

            def main():
                for label, relpath, old, new, expected in MUTATIONS:
                    p = SRC / relpath
        '''),
    })
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_bytes(("a = 1" + ending + "b = 2" + ending).encode())
    assert [a.count for a in ma.read_harness(repo, "tests/mutate_eol.py").anchors] == [1]
