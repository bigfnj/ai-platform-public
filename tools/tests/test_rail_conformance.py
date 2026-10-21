"""tools/rail_conformance.py - the parsing helpers underneath the 34 rules.

The rules themselves read the live tree and are covered by running the checker. What is
tested here is the layer below: the hand-rolled parsers each rule depends on. Those are the
dangerous part, because a parser that returns "" or None on text it does not understand makes
its rule report a PASS on a rail it never actually read - the silent-coverage-hole failure the
tool's own comments keep describing (`_deskpet_registered` reported three registered rails as
unregistered; `_ps_array` truncated at the first ')' and blamed rails sitting three lines down).

Nothing here needs a live stack. Every parser takes a string, and every path-based helper is
pointed at tmp_path.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1]
REPO = TOOLS.parent


def manifest(conformance, tmp_path: Path, name: str, data: dict):
    d = tmp_path / "rails" / name
    d.mkdir(parents=True, exist_ok=True)
    p = d / "rail.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return conformance.Manifest(path=p, data=data)


# =============================================================================
# rel() / read()
# =============================================================================

class TestRelAndRead:
    def test_rel_is_repo_relative_and_posix(self, conformance):
        assert conformance.rel(conformance.REPO / "tools" / "x.py") == "tools/x.py"

    def test_rel_of_a_path_outside_the_repo_is_left_absolute(self, conformance, tmp_path):
        """relative_to raises for an unrelated path; the helper must not."""
        out = conformance.rel(tmp_path / "x.py")
        assert "x.py" in out

    def test_rel_of_the_repo_root_itself(self, conformance):
        assert conformance.rel(conformance.REPO) == "."

    def test_read_returns_file_contents(self, conformance, tmp_path):
        p = tmp_path / "a.txt"
        p.write_text("hello\n", encoding="utf-8")
        assert conformance.read(p) == "hello\n"

    def test_read_of_a_missing_file_is_empty_not_an_error(self, conformance, tmp_path):
        assert conformance.read(tmp_path / "nope.txt") == ""

    def test_read_of_a_directory_is_empty(self, conformance, tmp_path):
        assert conformance.read(tmp_path) == ""

    def test_read_tolerates_undecodable_bytes(self, conformance, tmp_path):
        """errors='replace': a rule must not crash the whole run on one odd file."""
        p = tmp_path / "b.txt"
        p.write_bytes(b"ok \xff\xfe more")
        assert "ok" in conformance.read(p)

    def test_read_is_cached_but_never_stale(self, conformance, tmp_path):
        """read() is cached (a full run read 451 paths 2,425 times), keyed on mtime and size:
        a rewritten file is read again, a second read of an unchanged one is the cache's."""
        import os
        p = tmp_path / "c.txt"
        p.write_text("one\n", encoding="utf-8")
        assert conformance.read(p) == "one\n"
        assert conformance._READ_CACHE[str(p)][2] == "one\n"
        p.write_text("two\n", encoding="utf-8")
        st = p.stat()
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))  # a coarse clock cannot hide it
        assert conformance.read(p) == "two\n"
        p.write_text("three, longer\n", encoding="utf-8")
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))  # same mtime, new size
        assert conformance.read(p) == "three, longer\n"

    def test_parsing_a_rail_with_an_invalid_escape_prints_nothing(self, conformance):
        """smb's non-raw docstring holding `\\s` made every run print a SyntaxWarning (3.12+)."""
        import warnings
        with warnings.catch_warnings(record=True) as seen:
            warnings.simplefilter("always")
            conformance._ast_parse('"""split on (?<=[.!?])\\s+ and rejoin"""\nx = 1\n')
        assert [w for w in seen if issubclass(w.category, (SyntaxWarning, DeprecationWarning))] == []
        with pytest.raises(SyntaxError):
            conformance._ast_parse("def (:\n")


# =============================================================================
# _literal_assign() - ast.literal_eval instead of importing the gateway
# =============================================================================

class TestLiteralAssign:
    def _py(self, tmp_path: Path, src: str) -> Path:
        p = tmp_path / "m.py"
        p.write_text(src, encoding="utf-8")
        return p

    def test_plain_assignment(self, conformance, tmp_path):
        p = self._py(tmp_path, '_ENTRIES = [{"id": "a"}]\n')
        assert conformance._literal_assign(p, "_ENTRIES") == [{"id": "a"}]

    def test_annotated_assignment(self, conformance, tmp_path):
        p = self._py(tmp_path, 'X: list[int] = [1, 2]\n')
        assert conformance._literal_assign(p, "X") == [1, 2]

    def test_a_dict_literal(self, conformance, tmp_path):
        p = self._py(tmp_path, 'RAIL_MODEL_SLOTS = {"a": [{"slot": "s"}]}\n')
        assert conformance._literal_assign(p, "RAIL_MODEL_SLOTS") == {"a": [{"slot": "s"}]}

    def test_a_non_literal_value_is_none_not_a_guess(self, conformance, tmp_path):
        """'APP_CATALOG is now a sorted(...) call and cannot be literal-eval'd.' None means
        'this tool cannot tell', which is a different problem from 'not registered'."""
        p = self._py(tmp_path, 'APP_CATALOG = sorted(_ENTRIES, key=lambda e: e["label"])\n')
        assert conformance._literal_assign(p, "APP_CATALOG") is None

    def test_a_name_that_is_not_there(self, conformance, tmp_path):
        p = self._py(tmp_path, 'OTHER = 1\n')
        assert conformance._literal_assign(p, "_ENTRIES") is None

    def test_a_file_that_does_not_parse(self, conformance, tmp_path):
        p = self._py(tmp_path, 'X = [\n')
        assert conformance._literal_assign(p, "X") is None

    def test_a_missing_file(self, conformance, tmp_path):
        assert conformance._literal_assign(tmp_path / "gone.py", "X") is None

    def test_only_module_level_assignments_are_seen(self, conformance, tmp_path):
        p = self._py(tmp_path, 'def f():\n    X = [1]\n    return X\n')
        assert conformance._literal_assign(p, "X") is None

    def test_the_first_module_level_assignment_wins(self, conformance, tmp_path):
        """It returns on the first match rather than taking the last. Pinned because it is
        the opposite of Python's own semantics: a rebound registry would be read at its
        initial value, not its effective one."""
        p = self._py(tmp_path, 'X = [1]\nX = [2]\n')
        assert conformance._literal_assign(p, "X") == [1]

    def test_a_falsy_literal_is_returned_as_itself(self, conformance, tmp_path):
        """[] means 'the rail is not registered' and must be distinguishable from None,
        which means 'unparseable'. Blurring the two blames every rail for one refactor."""
        p = self._py(tmp_path, '_ENTRIES = []\n')
        assert conformance._literal_assign(p, "_ENTRIES") == []
        assert conformance._literal_assign(p, "_ENTRIES") is not None


def test_gateway_catalog_reads_the_real_registry(conformance):
    """A live cross-check: the entries must be readable, or RC003 checks nothing at all."""
    entries = conformance.gateway_catalog()
    assert entries is not None, "_ENTRIES became unreadable - RC003 is now blind"
    assert entries and all("id" in e for e in entries)


def test_gateway_rail_slots_is_a_dict(conformance):
    assert isinstance(conformance.gateway_rail_slots(), dict)


# =============================================================================
# _func_body() - 'crude but adequate'
# =============================================================================

class TestFuncBody:
    SRC = ('class Config:\n'
           '    def app_backends(self):\n'
           '        return {"finance": 1}\n'
           '\n'
           '    def resolved_app_dists(self):\n'
           '        return {"other": 2}\n')

    def test_an_indented_method_body_stops_at_the_next_method(self, conformance):
        body = conformance._func_body(self.SRC, "app_backends")
        assert "finance" in body
        assert "resolved_app_dists" not in body

    def test_the_last_method_runs_to_the_end(self, conformance):
        body = conformance._func_body(self.SRC, "resolved_app_dists")
        assert "other" in body

    def test_a_missing_name_is_empty(self, conformance):
        assert conformance._func_body(self.SRC, "nope") == ""

    def test_a_top_level_def_is_found(self, conformance):
        src = 'def top():\n    return 1\n'
        assert "return 1" in conformance._func_body(src, "top")

    def test_a_top_level_def_runs_to_the_end_of_the_file(self, conformance):
        """Known limitation of the crude scan: the terminator is an INDENTED `def`, so a
        module of top-level functions is not sliced. Pinned because the helper is only ever
        pointed at the gateway's config class, where every def is a method."""
        src = 'def top():\n    return 1\n\ndef other():\n    return 2\n'
        assert "other" in conformance._func_body(src, "top")

    def test_empty_source(self, conformance):
        assert conformance._func_body("", "anything") == ""


# =============================================================================
# _compose_service() - regex, because pyyaml is not stdlib
# =============================================================================

class TestComposeService:
    COMPOSE = ('services:\n'
               '  gateway:\n'
               '    image: a\n'
               '    ports: ["1:1"]\n'
               '  finance:   # a trailing comment\n'
               '    image: b\n'
               '  last:\n'
               '    image: c\n')

    def test_a_service_block_stops_at_the_next_service(self, conformance):
        body = conformance._compose_service(self.COMPOSE, "gateway")
        assert "image: a" in body and "ports:" in body
        assert "image: b" not in body

    def test_a_service_line_may_carry_a_comment(self, conformance):
        body = conformance._compose_service(self.COMPOSE, "finance")
        assert body is not None and "image: b" in body
        assert "image: c" not in body

    def test_the_last_service_runs_to_the_end(self, conformance):
        assert "image: c" in conformance._compose_service(self.COMPOSE, "last")

    def test_a_missing_service_is_none_not_empty(self, conformance):
        """None and '' mean different things: absent vs present-but-empty."""
        assert conformance._compose_service(self.COMPOSE, "nope") is None

    def test_a_name_that_is_a_prefix_of_another_does_not_match_it(self, conformance):
        src = '  finance-worker:\n    image: w\n  finance:\n    image: f\n'
        assert "image: f" in conformance._compose_service(src, "finance")

    def test_regex_metacharacters_in_the_name_are_literal(self, conformance):
        assert conformance._compose_service(self.COMPOSE, "gate.ay") is None

    def test_a_service_with_an_empty_body(self, conformance):
        src = '  empty:\n  next:\n    image: x\n'
        assert conformance._compose_service(src, "empty") == "\n"


# =============================================================================
# _deskpet_registered() - two JS forms in one map
# =============================================================================

class TestDeskpetRegistered:
    LINES_TS = ('const OTHER = 1;\n'
                'const RAIL: Record<string, string[]> = {\n'
                '  "recipe-book": recipeBook,\n'
                '  bouquet,\n'
                '  // a commented-out rail\n'
                "  'finance': financeLines,\n"
                '  workstation,\n'
                '};\n'
                'const AFTER = 2;\n')

    def test_both_key_forms_are_recognised(self, conformance):
        """'Matching only the quoted form reported finance, iep and workstation as
        unregistered when they are registered.' Shorthand keys are the other half."""
        assert conformance._deskpet_registered(self.LINES_TS) == \
            {"recipe-book", "bouquet", "finance", "workstation"}

    def test_only_the_rail_map_is_read(self, conformance):
        ids = conformance._deskpet_registered(self.LINES_TS)
        assert "OTHER" not in ids and "AFTER" not in ids

    def test_comment_lines_are_ignored(self, conformance):
        assert not any("comment" in i for i in
                       conformance._deskpet_registered(self.LINES_TS))

    def test_a_trailing_comment_does_not_join_the_id(self, conformance):
        src = 'const RAIL = {\n  bouquet, // the flowers one\n};\n'
        assert conformance._deskpet_registered(src) == {"bouquet"}

    def test_single_and_double_quotes_both_work(self, conformance):
        src = 'const RAIL = {\n  "a": x,\n  \'b\': y,\n};\n'
        assert conformance._deskpet_registered(src) == {"a", "b"}

    def test_an_empty_map(self, conformance):
        assert conformance._deskpet_registered('const RAIL = {\n};\n') == set()

    def test_no_rail_map_at_all(self, conformance):
        assert conformance._deskpet_registered("const OTHER = 1;\n") == set()


# =============================================================================
# _ps_array() - depth-counted, because labels contain parentheses
# =============================================================================

class TestPsArray:
    PS = ("$choices = @(\n"
          "  @{ Id = 'recipe-book'; Label = 'Recipe Book (ships with seed)' }\n"
          "  @{ Id = 'finance';     Label = 'Finance' }\n"
          ")\n"
          "$other = 1\n")

    def test_a_parenthesis_inside_a_label_does_not_truncate(self, conformance):
        """'Scanning to the first ) truncates the list, and the rule would then report a
        rail as missing while it is sitting three lines further down.'"""
        out = conformance._ps_array(self.PS, r"\$choices\s*=\s*@\(")
        assert "recipe-book" in out and "finance" in out

    def test_the_array_stops_at_its_own_closer(self, conformance):
        out = conformance._ps_array(self.PS, r"\$choices\s*=\s*@\(")
        assert "$other" not in out
        assert out.endswith(")")

    def test_the_result_starts_at_the_opening_token(self, conformance):
        out = conformance._ps_array(self.PS, r"\$choices\s*=\s*@\(")
        assert out.startswith("@(")

    def test_a_missing_anchor_is_empty(self, conformance):
        assert conformance._ps_array(self.PS, r"\$nope\s*=\s*@\(") == ""

    def test_nested_arrays_are_counted(self, conformance):
        src = "$a = @(\n  @( 'x' )\n  'y'\n)\ntail\n"
        out = conformance._ps_array(src, r"\$a\s*=\s*@\(")
        assert "'x'" in out and "'y'" in out and "tail" not in out

    def test_an_unbalanced_array_yields_empty_rather_than_the_rest_of_the_file(self,
                                                                              conformance):
        assert conformance._ps_array("$a = @(\n  'x'\n", r"\$a\s*=\s*@\(") == ""


# =============================================================================
# _walk() - rglob with pruning
# =============================================================================

class TestWalk:
    def test_matches_are_found_recursively_and_sorted(self, conformance, tmp_path):
        (tmp_path / "b").mkdir()
        (tmp_path / "b" / "two.py").write_text("", encoding="utf-8")
        (tmp_path / "one.py").write_text("", encoding="utf-8")
        found = conformance._walk(tmp_path, "*.py")
        assert found == sorted(found)
        assert {p.name for p in found} == {"one.py", "two.py"}

    @pytest.mark.parametrize("skipped", ["node_modules", ".venv", "dist", "__pycache__",
                                         ".git", "native", "site-packages", ".pytest_cache"])
    def test_vendored_trees_are_pruned(self, conformance, tmp_path, skipped):
        """'rails/ spends ~30s walking third-party trees to find nothing.' Pruning is also
        correctness: a vite config inside node_modules is not a rail's config."""
        d = tmp_path / skipped
        d.mkdir()
        (d / "vite.config.ts").write_text("", encoding="utf-8")
        assert conformance._walk(tmp_path, "vite*.config.ts") == []

    def test_the_glob_is_applied_to_the_filename(self, conformance, tmp_path):
        (tmp_path / "vite.config.ts").write_text("", encoding="utf-8")
        (tmp_path / "vite.mobile.config.ts").write_text("", encoding="utf-8")
        (tmp_path / "other.ts").write_text("", encoding="utf-8")
        assert {p.name for p in conformance._walk(tmp_path, "vite*.config.ts")} == \
            {"vite.config.ts", "vite.mobile.config.ts"}

    def test_an_unreadable_directory_is_skipped_not_fatal(self, conformance, tmp_path):
        assert conformance._walk(tmp_path / "does-not-exist", "*.py") == []

    def test_an_empty_tree(self, conformance, tmp_path):
        assert conformance._walk(tmp_path, "*.py") == []


# =============================================================================
# Manifest - the derivations every source-reading rule depends on
# =============================================================================

class TestManifest:
    def test_id_from_the_manifest(self, conformance, tmp_path):
        assert manifest(conformance, tmp_path, "iep", {"id": "iep-goals"}).id == "iep-goals"

    def test_id_falls_back_to_the_directory(self, conformance, tmp_path):
        assert manifest(conformance, tmp_path, "bouquet", {}).id == "bouquet"

    def test_catalog_ids_is_the_id_first_then_also_serves(self, conformance, tmp_path):
        """'One image here backs two catalog tiles: the edu-suite dashboard serves both
        edu-suite and iep.'"""
        m = manifest(conformance, tmp_path, "edu-suite",
                     {"id": "edu-suite", "also_serves": ["iep"]})
        assert m.catalog_ids() == ["edu-suite", "iep"]

    def test_catalog_ids_without_also_serves(self, conformance, tmp_path):
        assert manifest(conformance, tmp_path, "x", {"id": "x"}).catalog_ids() == ["x"]

    def test_a_malformed_also_serves_is_ignored(self, conformance, tmp_path):
        m = manifest(conformance, tmp_path, "x", {"id": "x", "also_serves": "iep"})
        assert m.catalog_ids() == ["x"]

    def test_slots_and_panel_slots(self, conformance, tmp_path):
        m = manifest(conformance, tmp_path, "x", {"id": "x", "model_slots": [
            {"slot": "chat", "admin_panel": True}, {"slot": "embed"}]})
        assert len(m.slots()) == 2
        assert [s["slot"] for s in m.panel_slots()] == ["chat"]

    def test_slots_of_a_malformed_manifest_is_empty(self, conformance, tmp_path):
        assert manifest(conformance, tmp_path, "x", {"model_slots": "no"}).slots() == []
        assert manifest(conformance, tmp_path, "x", {}).slots() == []

    def test_backend_dir_follows_the_layout(self, conformance, tmp_path):
        src = manifest(conformance, tmp_path, "x",
                       {"id": "x", "package": "p", "layout": "src"}).backend_dir()
        assert src == tmp_path / "rails" / "x" / "src" / "p"
        back = manifest(conformance, tmp_path, "y",
                        {"id": "y", "package": "p"}).backend_dir()
        assert back == tmp_path / "rails" / "y" / "backend" / "p"

    def test_package_path_overrides_the_layout(self, conformance, tmp_path):
        m = manifest(conformance, tmp_path, "edu-suite",
                     {"id": "edu-suite", "package": "dashboard", "layout": "src",
                      "package_path": "apps/dashboard/src/dashboard"})
        assert m.backend_dir() == tmp_path / "rails" / "edu-suite" / "apps/dashboard/src/dashboard"

    def test_compose_service_defaults_to_the_id(self, conformance, tmp_path):
        assert manifest(conformance, tmp_path, "x", {"id": "x"}).compose_service() == "x"

    def test_compose_service_override(self, conformance, tmp_path):
        """'edu-suite's service is called dashboard - the rail was named after what it
        became, the service after what it was.'"""
        m = manifest(conformance, tmp_path, "edu-suite",
                     {"id": "edu-suite", "compose_service": "dashboard"})
        assert m.compose_service() == "dashboard"

    def test_container_port_overrides_the_nominal_port(self, conformance, tmp_path):
        m = manifest(conformance, tmp_path, "iep",
                     {"id": "iep-goals", "container_port": 8800, "ports": {"backend": 8802}})
        assert m.container_port() == 8800

    def test_container_port_falls_back_to_ports_backend(self, conformance, tmp_path):
        m = manifest(conformance, tmp_path, "x", {"id": "x", "ports": {"backend": 8840}})
        assert m.container_port() == 8840

    def test_a_non_integer_container_port_is_ignored(self, conformance, tmp_path):
        m = manifest(conformance, tmp_path, "x",
                     {"id": "x", "container_port": "8800", "ports": {"backend": 8840}})
        assert m.container_port() == 8840

    def test_container_port_with_nothing_declared(self, conformance, tmp_path):
        assert manifest(conformance, tmp_path, "x", {"id": "x"}).container_port() is None


class TestManifestSources:
    def _rail(self, conformance, tmp_path, data):
        m = manifest(conformance, tmp_path, "x", data)
        return m

    def test_py_sources_reads_the_package(self, conformance, tmp_path):
        m = self._rail(conformance, tmp_path, {"id": "x", "package": "p", "layout": "src"})
        pkg = m.backend_dir()
        pkg.mkdir(parents=True)
        (pkg / "a.py").write_text("", encoding="utf-8")
        (pkg / "b.txt").write_text("", encoding="utf-8")
        assert [p.name for p in m.py_sources()] == ["a.py"]

    def test_py_sources_skips_pycache(self, conformance, tmp_path):
        m = self._rail(conformance, tmp_path, {"id": "x", "package": "p", "layout": "src"})
        pkg = m.backend_dir()
        (pkg / "__pycache__").mkdir(parents=True)
        (pkg / "__pycache__" / "a.py").write_text("", encoding="utf-8")
        (pkg / "real.py").write_text("", encoding="utf-8")
        assert [p.name for p in m.py_sources()] == ["real.py"]

    def test_py_sources_includes_extra_packages(self, conformance, tmp_path):
        """'edu-suite's dashboard delegates every broker call to packages/edu-media-core, so
        reading only the dashboard package finds no token handling and reports a rail that is
        in fact correct.'"""
        m = self._rail(conformance, tmp_path,
                       {"id": "x", "package": "p", "layout": "src",
                        "extra_packages": ["shared/lib"]})
        m.backend_dir().mkdir(parents=True)
        (m.backend_dir() / "a.py").write_text("", encoding="utf-8")
        extra = tmp_path / "rails" / "x" / "shared" / "lib"
        extra.mkdir(parents=True)
        (extra / "b.py").write_text("", encoding="utf-8")
        assert {p.name for p in m.py_sources()} == {"a.py", "b.py"}

    def test_py_sources_of_a_missing_package_is_empty(self, conformance, tmp_path):
        m = self._rail(conformance, tmp_path, {"id": "x", "package": "nope"})
        assert m.py_sources() == []

    def test_frontend_src_default_location(self, conformance, tmp_path):
        m = self._rail(conformance, tmp_path, {"id": "x"})
        d = tmp_path / "rails" / "x" / "frontend" / "src"
        d.mkdir(parents=True)
        assert m.frontend_src() == d

    def test_frontend_src_follows_a_relocated_frontend(self, conformance, tmp_path):
        """'edu-suite serves its tiles from apps/dashboard/frontend/src, and hardcoding the
        common layout meant every TS-reading rule silently skipped it.'"""
        m = self._rail(conformance, tmp_path,
                       {"id": "x", "package_path": "apps/dashboard/src/dashboard"})
        d = tmp_path / "rails" / "x" / "apps" / "dashboard" / "frontend" / "src"
        d.mkdir(parents=True)
        assert m.frontend_src() == d

    def test_ts_sources_finds_ts_and_tsx(self, conformance, tmp_path):
        m = self._rail(conformance, tmp_path, {"id": "x"})
        d = tmp_path / "rails" / "x" / "frontend" / "src"
        d.mkdir(parents=True)
        (d / "a.ts").write_text("", encoding="utf-8")
        (d / "b.tsx").write_text("", encoding="utf-8")
        (d / "c.css").write_text("", encoding="utf-8")
        assert {p.name for p in m.ts_sources()} == {"a.ts", "b.tsx"}

    def test_ts_sources_without_a_frontend(self, conformance, tmp_path):
        assert self._rail(conformance, tmp_path, {"id": "x"}).ts_sources() == []

    def test_style_text_prefers_theme_css(self, conformance, tmp_path):
        m = self._rail(conformance, tmp_path, {"id": "x"})
        d = tmp_path / "rails" / "x" / "frontend" / "src"
        d.mkdir(parents=True)
        (d / "theme.css").write_text("/* css */", encoding="utf-8")
        (d / "module.tsx").write_text("// tsx", encoding="utf-8")
        text, where = m.style_text()
        assert "/* css */" in text and where.endswith("theme.css")

    def test_style_text_falls_back_to_the_module(self, conformance, tmp_path):
        """'terminal-fun ships a template literal in module.tsx instead... the theming rules
        apply to the CSS wherever it is written.'"""
        m = self._rail(conformance, tmp_path, {"id": "x"})
        d = tmp_path / "rails" / "x" / "frontend" / "src"
        d.mkdir(parents=True)
        (d / "module.tsx").write_text("// tsx", encoding="utf-8")
        text, where = m.style_text()
        assert "// tsx" in text and where.endswith("module.tsx")

    def test_style_text_with_neither(self, conformance, tmp_path):
        assert self._rail(conformance, tmp_path, {"id": "x"}).style_text() == ("", "")


# =============================================================================
# Finding / the rule registry
# =============================================================================

class TestFindings:
    def test_line_formats_level_rule_rail_and_message(self, conformance):
        f = conformance.Finding(rule="RC001", rail="finance", level="fail",
                                message="missing key", where="rails/finance/rail.json")
        line = f.line()
        assert line.startswith("FAIL RC001")
        assert "finance" in line and "missing key" in line
        assert "[rails/finance/rail.json]" in line

    def test_line_without_a_location(self, conformance):
        f = conformance.Finding(rule="RC002", rail="x", level="warn", message="hm")
        assert f.line().startswith("WARN RC002")
        assert "[" not in f.line()

    def test_f_helper_defaults_to_fail(self, conformance, tmp_path):
        m = manifest(conformance, tmp_path, "x", {"id": "x"})
        f = conformance.F("RC001", m, "boom")
        assert f.level == "fail" and f.rail == "x" and f.rule == "RC001"

    def test_f_helper_can_warn(self, conformance, tmp_path):
        m = manifest(conformance, tmp_path, "x", {"id": "x"})
        assert conformance.F("RC024", m, "no quips", level="warn").level == "warn"


class TestRuleRegistry:
    def test_the_registry_is_populated(self, conformance):
        """Deliberately a floor, not an exact count. Rules are added over time (RC028 landed
        while this suite was being written), and a test that has to be edited every time one
        is added teaches people to edit tests. What must hold is that the decorator still
        registers them at all: an empty RULES list means `main()` checks nothing and reports
        a clean run."""
        assert len(conformance.RULES) >= 27

    def test_rule_ids_are_unique(self, conformance):
        ids = [rid for rid, _, _ in conformance.RULES]
        assert len(ids) == len(set(ids))

    def test_every_rule_has_a_summary_and_is_callable(self, conformance):
        for rid, summary, fn in conformance.RULES:
            assert summary.strip(), f"{rid} has no contract statement"
            assert callable(fn)

    def test_rule_ids_are_well_formed(self, conformance):
        import re
        for rid, _, _ in conformance.RULES:
            assert re.fullmatch(r"RC\d{3}", rid), rid

    def test_the_id_sequence_has_no_gaps(self, conformance):
        nums = sorted(int(rid[2:]) for rid, _, _ in conformance.RULES)
        assert nums == list(range(1, len(nums) + 1))

    def test_every_rule_is_documented_in_the_contract(self, conformance):
        """'an id and a one-line summary that doubles as the contract statement in the
        report and in docs/RAIL_CONTRACT.md.' A rule nobody documented is a rule nobody
        agreed to."""
        doc = (REPO / "docs" / "RAIL_CONTRACT.md").read_text(encoding="utf-8")
        undocumented = [rid for rid, _, _ in conformance.RULES if rid not in doc]
        assert not undocumented, f"rules missing from RAIL_CONTRACT.md: {undocumented}"

    def test_the_contract_documents_no_rule_that_is_gone(self, conformance):
        import re
        doc = (REPO / "docs" / "RAIL_CONTRACT.md").read_text(encoding="utf-8")
        documented = set(re.findall(r"RC\d{3}", doc))
        registered = {rid for rid, _, _ in conformance.RULES}
        assert not documented - registered, \
            f"RAIL_CONTRACT.md describes rules that no longer exist: {documented - registered}"


class TestRuleFiles:
    """The rules are written in tools/rail_conformance_rules/ and run into the checker's one
    namespace (rail_conformance.py, "where the rules live"), which is what lets a test point
    REPO, COMPOSE or a reader at a fixture and reach every rule. These hold what one namespace
    asks of the files."""

    HOST = "rail_conformance.py"

    @classmethod
    def _files(cls, conformance) -> dict:
        """{the checker's file name, or a rule file's: its parsed source}."""
        import ast
        paths = [Path(conformance.__file__), *sorted(conformance.RULE_DIR.glob("*.py"))]
        assert paths[0].name == cls.HOST and len(paths) > 1, paths
        return {p.name: ast.parse(p.read_text(encoding="utf-8")) for p in paths}

    @staticmethod
    def _binds(tree) -> dict:
        """{name: what binds it} at a file's top level: ("import", module, name) for an import,
        ("own",) for a def, a class or an assignment."""
        import ast
        out: dict = {}
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for a in node.names:
                    out[a.asname or a.name.split(".")[0]] = (
                        "import", getattr(node, "module", None) or "", a.name)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                out[node.name] = ("own",)
            elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                for t in node.targets if isinstance(node, ast.Assign) else [node.target]:
                    for n in ast.walk(t):
                        if isinstance(n, ast.Name):
                            out[n.id] = ("own",)
        return out

    def test_every_rule_file_runs_once_in_the_order_of_its_name(self, conformance):
        """A file that never runs ships rules that check nothing, and one that runs twice
        registers its rules twice. Run in the order of their names, a file named for the ids it
        holds runs where those ids go."""
        files = sorted(p.stem for p in conformance.RULE_DIR.glob("*.py"))
        assert files, f"no rule file in {conformance.RULE_DIR}, so nothing was checked"
        assert conformance._RULE_FILES == files

    def test_no_two_files_bind_one_name(self, conformance):
        """In one namespace the file that runs later wins a name everywhere, so a helper bound
        in two files is silently the later one's in both. The same import in two files binds
        the same object."""
        seen: dict = {}
        for f, tree in self._files(conformance).items():
            for name, what in self._binds(tree).items():
                seen.setdefault(name, {})[f] = what
        twice = {n: sorted(by) for n, by in seen.items()
                 if len(by) > 1 and (("own",) in by.values() or len(set(by.values())) > 1)}
        assert twice == {}

    def test_no_file_uses_a_name_only_another_rule_file_binds(self, conformance):
        """A rule file uses the checker's names and its own; a helper two files use is defined
        in the checker. Otherwise moving one file's helper breaks another file's rule, which
        then reports as a crashed rule rather than at the change."""
        import ast
        files = self._files(conformance)
        binds = {f: set(self._binds(t)) for f, t in files.items()}
        wrong = {}
        for f, tree in files.items():
            mine = binds[f] | binds[self.HOST]
            elsewhere = set().union(*(b for g, b in binds.items() if g != f)) - mine
            used = {n.id for n in ast.walk(tree)
                    if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
            if used & elsewhere:
                wrong[f] = sorted(used & elsewhere)
        assert wrong == {}

    def test_the_checkers_docstring_is_its_own(self, conformance):
        """A rule file runs into the checker's namespace, so a docstring of its own would become
        the checker's __doc__, which main() prints as the --help text."""
        import ast
        own = ast.get_docstring(self._files(conformance)[self.HOST], clean=False)
        assert own and conformance.__doc__ == own


# =============================================================================
# Shared definitions
# =============================================================================

def test_the_checker_and_the_generator_share_one_template_module(conformance):
    """'The template generator and this checker share one definition of what invariant
    means, so they cannot disagree about it.'"""
    mod = conformance._rail_template()
    assert mod.FACADE == ("roles", "models", "status")
    assert hasattr(mod, "invariants") and hasattr(mod, "render")


class TestChipRailWithNoResolver:
    """RC022 and RC023 on a rail that renders chips but carries no modelstate.py.

    Both rules used to SKIP that rail ("does no model work"), so the one check that compares
    resolvers against the template could not see a rail that had replaced it with its own.
    """
    DATA = {"id": "x", "package": "xapp", "status_route": "/api/capabilities",
            "model_slots": [{"slot": "writer", "kind": "chat", "role": "writer"}]}

    def _m(self, conformance, tmp_path):
        m = manifest(conformance, tmp_path, "x", self.DATA)
        (tmp_path / "rails" / "x" / "backend" / "xapp").mkdir(parents=True)
        return m

    def test_rc023_reports_the_missing_resolver(self, conformance, tmp_path):
        out = conformance.rc023(self._m(conformance, tmp_path), [])
        assert [f.level for f in out] == ["fail"]
        assert "modelstate.py is missing" in out[0].message
        assert "sync --rail x" in out[0].message

    def test_rc022_requires_the_facade_rather_than_skipping(self, conformance, tmp_path):
        out = conformance.rc022(self._m(conformance, tmp_path), [])
        assert [f.level for f in out] == ["fail"]
        assert "<no broker facade found>" in out[0].message

    def test_a_rail_with_no_chips_is_still_exempt(self, conformance, tmp_path):
        """The other direction: meeting-atlas, workstation and ai-voice render no chips and
        must not be told to carry a resolver they have no use for."""
        m = manifest(conformance, tmp_path, "y",
                     {"id": "y", "package": "yapp", "status_route": None, "model_slots": []})
        (tmp_path / "rails" / "y" / "backend" / "yapp").mkdir(parents=True)
        assert conformance.rc023(m, []) == []
        assert [f.level for f in conformance.rc022(m, [])] == ["skip"]


def test_chip_states_are_the_four_shared_names(conformance):
    """'A rail that computes a different set is not slightly different, it is lying to an
    operator who has learned what the colours mean everywhere else.'"""
    assert conformance.CHIP_STATES == ("missing", "cold", "warming", "loaded")


class TestRc012StatusDots:
    """RC012's literal-hex dot finding, and the fix it tells the rail to make.

    The message used to say "use the shared status tokens (--critical / --warning / --good)",
    the fixed colours the shared dots had to stop using because they miss 3:1 against the page
    on many palettes. Advice that sends the reader to the wrong token is worse than none.
    """
    def _rail(self, conformance, tmp_path, css):
        m = manifest(conformance, tmp_path, "x", {"id": "x"})
        d = tmp_path / "rails" / "x" / "frontend" / "src"
        d.mkdir(parents=True)
        (d / "theme.css").write_text(css, encoding="utf-8")
        return m

    @pytest.mark.parametrize("state", ["missing", "cold", "warming", "loaded"])
    def test_a_literal_dot_is_told_to_use_its_chip_token(self, conformance, tmp_path, state):
        m = self._rail(conformance, tmp_path, f".x .cw-dot.{state} {{ background: #58a6ff; }}")
        out = [f for f in conformance.rc012(m, []) if "status dot" in f.message]
        assert [f.level for f in out] == ["fail"]
        assert f"var(--chip-{state})" in out[0].message
        assert "THEMING.md rule 4" in out[0].message
        for wrong in ("--critical", "--warning", "--good"):
            assert wrong not in out[0].message

    def test_a_dot_painted_with_its_chip_token_passes(self, conformance, tmp_path):
        m = self._rail(conformance, tmp_path, ".x .cw-dot.cold { background: var(--chip-cold); }")
        assert [f for f in conformance.rc012(m, []) if "status dot" in f.message] == []

    @pytest.mark.parametrize("state,painted", [
        ("missing", "var(--critical)"), ("warming", "var(--warning)"), ("loaded", "var(--good)"),
        ("cold", "var(--info)"), ("cold", "var(--info, #58a6ff)"), ("missing", "var(--critical )"),
    ])
    def test_a_dot_on_a_fixed_status_token_is_flagged(self, conformance, tmp_path, state, painted):
        """Not only a literal: the fixed tokens are one colour for both modes and miss 3:1 on
        many palettes, which is why the shared dots moved off them. The finding still names the
        right token to use, in its advice."""
        m = self._rail(conformance, tmp_path, f".x .x-dot.{state} {{ background: {painted}; }}")
        out = [f for f in conformance.rc012(m, []) if "status dot" in f.message]
        assert [f.level for f in out] == ["fail"]
        assert painted.split(",")[0].rstrip(")") in out[0].message
        advice = out[0].message.split("; paint it", 1)[1]
        assert f"var(--chip-{state})" in advice
        for wrong in ("--critical", "--warning", "--good", "--info"):
            assert wrong not in advice

    @pytest.mark.parametrize("painted", ["var(--good-text)", "var(--critical-text)", "var(--info-soft)"])
    def test_a_longer_token_name_is_not_mistaken_for_a_fixed_one(self, conformance, tmp_path, painted):
        """The name ends where the token ends: --good-text is a different (per-mode) token, and
        a \\b boundary would have matched it as --good."""
        m = self._rail(conformance, tmp_path, f".x .x-dot.loaded {{ background: {painted}; }}")
        assert [f for f in conformance.rc012(m, []) if "status dot" in f.message] == []

    @pytest.mark.parametrize("state", ["missing", "cold", "warming", "loaded"])
    def test_the_token_it_names_exists_in_the_shared_sheet(self, conformance, state):
        """The advice is only right while the token is real: a rename in web-core must fail
        here, not leave RC012 pointing every rail at a name that paints nothing."""
        css = (REPO / "web" / "src" / "styles.css").read_text(encoding="utf-8")
        assert f"--chip-{state}:" in css


class TestRc012Scope:
    """RC012 fails a selector that can match outside the rail.

    The federation runtime never removes a remote's stylesheet, so after one visit a rail's CSS
    is the whole platform's for the rest of the session. recipe-book's top-level .btn .chip
    .empty .stars ... and ai-playground's .chip .cite .caret .src restyled the shell's Log Out
    button, every .empty panel and other rails' chips. The rule only checked that a
    wrapper block existed.
    """
    def _rail(self, conformance, tmp_path, name, css, where="theme.css", wrapper=None):
        m = manifest(conformance, tmp_path, name, {"id": name, "css_wrapper": wrapper or name})
        d = tmp_path / "rails" / name / "frontend" / "src"
        d.mkdir(parents=True, exist_ok=True)
        (d / where).write_text(css, encoding="utf-8")
        return m

    def _leaks(self, conformance, m, all_=()):
        return conformance.unscoped_selectors(m, [m, *all_])

    @pytest.mark.parametrize("css", [
        ".zq { color: red; } .zq .btn { x: 1; } .zq > .chip { x: 1; }",
        ".zq.dark .empty { x: 1; } .zq-panel { x: 1; } .zq-panel .chip { x: 1; }",
        ":root[data-theme='dark'] .zq { --a: 1; } html[data-x] .zq .btn { x: 1; }",
        ".zqx-thing { x: 1; } .zqx-thing:hover .btn { x: 1; } input.zqx-field { x: 1; }",
        "@keyframes spin { from { x: 1; } 50% { x: 2; } to { x: 3; } } .zq { animation: spin 1s; }",
        "@font-face { font-family: z; src: url(z.woff); } .zq { x: 1; }",
    ])
    def test_scoped_forms_pass(self, conformance, tmp_path, css):
        m = self._rail(conformance, tmp_path, "zq", css)
        assert self._leaks(conformance, m) == []

    @pytest.mark.parametrize("css,sel,why", [
        (".btn { x: 1; }", ".btn", "also used by web-core"),
        (".zq {} .empty { x: 1; }", ".empty", "also used by web-core"),
        ("h3 { x: 1; }", "h3", "no class"),
        ("* { box-sizing: border-box; }", "*", "no class"),
        (":root { --zq-a: 1; }", ":root", "no class"),
        ("@media (max-width: 9px) { .chip { x: 1; } }", ".chip", "also used by web-core"),
        (".askbar { x: 1; }", ".askbar", "no prefix of this rail's own"),
        (".zq {} .zq .a, .chip.on { x: 1; }", ".chip.on", "also used by web-core"),
        (".tag-zq { x: 1; }", ".tag-zq", "prefix 'tag' also used by web-core"),
    ])
    def test_unscoped_forms_fail_by_name(self, conformance, tmp_path, css, sel, why):
        m = self._rail(conformance, tmp_path, "zq", css)
        leaks = self._leaks(conformance, m)
        assert [s for s, _ in leaks] == [sel], leaks
        assert why in leaks[0][1], leaks

    def test_a_prefix_shared_with_another_rail_is_not_this_rails_own(self, conformance, tmp_path):
        """Prefix ownership is what keeps an unscoped class safe, so a second rail using the
        same prefix, even under its own wrapper, makes it a collision waiting to happen."""
        a = self._rail(conformance, tmp_path, "za", ".zp-row { x: 1; }")
        b = self._rail(conformance, tmp_path, "zb", ".zb .zp-cell { x: 1; }")
        leaks = self._leaks(conformance, a, [b])
        assert [s for s, _ in leaks] == [".zp-row"] and "also used by zb" in leaks[0][1]
        assert self._leaks(conformance, a) == []

    def test_a_module_tsx_is_read_for_its_injected_css_only(self, conformance, tmp_path):
        src = ("const x = props.btn ? a.chip : b  // .empty in a comment\n"
               "const CSS = `\n.zq { x: 1; }\n.zq .btn { x: 1; }\n.chip { x: 1; }\n`\n")
        m = self._rail(conformance, tmp_path, "zq", src, where="module.tsx")
        assert [s for s, _ in self._leaks(conformance, m)] == [".chip"]

    def test_rc012_fails_with_a_count_and_the_selectors(self, conformance, tmp_path):
        m = self._rail(conformance, tmp_path, "zq", ".zq { x: 1; } .btn { x: 1; } .empty { x: 1; }")
        out = [f for f in conformance.rc012(m, [m]) if "can match outside the rail" in f.message]
        assert [f.level for f in out] == ["fail"]
        assert out[0].message.startswith("2 selector(s)") and ".btn" in out[0].message
        assert "Scope each under '.zq '" in out[0].message

    @pytest.mark.parametrize("rail,wrapper,lines,want", [
        # Excerpts of the two files as the audit found them: top-level rules, verbatim.
        ("recipe-book", "recipe-book", [
            ".recipe-book { color: var(--ink); }",
            ".btn { font: inherit; border: 1px solid var(--rule); }",
            ".chip { display: inline-block; padding: 2px 8px; }",
            ".empty { color: var(--muted); padding: 30px; }",
            ".stars { color: var(--accent-text); }",
            ".seg button.on { background: var(--grad-accent, var(--accent)); }",
        ], {".btn", ".chip", ".empty", ".stars", ".seg button.on"}),
        ("ai-playground", "ai-playground", [
            ".ai-playground { --ac: var(--accent, #4f7cff); }",
            ".chip { border: 1px solid var(--bd); }",
            ".cite { font-weight: 700; }",
            ".caret { animation: blink 1s steps(2) infinite; }",
            ".src { border: 1px solid var(--bd); }",
        ], {".chip", ".cite", ".caret", ".src"}),
    ])
    def test_the_audited_files_fail(self, conformance, tmp_path, rail, wrapper, lines, want):
        m = self._rail(conformance, tmp_path, rail, "\n".join(lines), wrapper=wrapper)
        assert {s for s, _ in self._leaks(conformance, m)} == want

    def test_a_selector_list_is_split_at_its_top_level_commas_only(self, conformance, tmp_path):
        """`:is(h1, h2)` is one selector; splitting inside the parentheses made ` h2)` a
        classless selector of its own."""
        m = self._rail(conformance, tmp_path, "zq", ".zq :is(h1, h2), .zq [data-x='a,b'] { x: 1; }")
        assert self._leaks(conformance, m) == []
        assert conformance._selector_list(".a :is(b, c), .d") == [".a :is(b, c)", " .d"]


# bouquet's standalone.css as it once shipped, verbatim.
BOUQUET_STANDALONE = (':root { --page: #faf8f6; }\n'
                      ':root[data-theme="dark"] { --page: #14121a; color-scheme: dark; }\n\n'
                      'html, body { margin: 0; background: var(--page); min-height: 100vh; }\n'
                      '#root { padding: 20px; }\n')


class TestRc012ShippedSheets:
    """RC012 judges every stylesheet of the rail's own that the build ships, not only theme.css.

    Vite builds index.html by default, so main.tsx and its imports are in the bundle, and with
    cssCodeSplit:false remoteEntry.js injects every stylesheet the build emits into the shell.
    bouquet's dev page sheet, imported by main.tsx, inset the whole platform by 20px while
    RC012, reading theme.css alone, passed.
    """
    def _rail(self, conformance, tmp_path, files: dict[str, str]):
        m = manifest(conformance, tmp_path, "zq", {"id": "zq", "css_wrapper": "zq"})
        fe = tmp_path / "rails" / "zq" / "frontend"
        (fe / "src").mkdir(parents=True, exist_ok=True)
        (fe / "src" / "theme.css").write_text(".zq { color: red; }\n", encoding="utf-8")
        for name, text in files.items():
            (fe / name).parent.mkdir(parents=True, exist_ok=True)
            (fe / name).write_text(text, encoding="utf-8")
        return m

    def _shipped(self, conformance, m):
        return [(Path(w).name, via) for _t, w, via in conformance.shipped_stylesheets(m)]

    def test_a_sheet_main_imports_is_judged_and_fails(self, conformance, tmp_path):
        m = self._rail(conformance, tmp_path, {
            "src/main.tsx": 'import Mod from "./module";\nimport "./standalone.css";\n',
            "src/module.tsx": 'import "./theme.css";\n',
            "src/standalone.css": BOUQUET_STANDALONE})
        out = [f for f in conformance.rc012(m, [m]) if "standalone.css" in f.message]
        assert [f.level for f in out] == ["fail"], out
        assert "imported by" in out[0].message and "main.tsx" in out[0].message
        for sel in (":root", "html", "body", "#root"):
            assert f"\n      {sel}  <-" in out[0].message, sel

    def test_a_sheet_no_module_imports_is_not_shipped(self, conformance, tmp_path):
        """The fix's shape: the dev page sheet stays in src/, linked by a serve-only plugin."""
        m = self._rail(conformance, tmp_path, {
            "src/main.tsx": 'import Mod from "./module";\n// import "./standalone.css";\n'
                            '/* import "./standalone.css" */\n',
            "src/standalone.css": BOUQUET_STANDALONE})
        assert self._shipped(conformance, m) == []
        assert [f for f in conformance.rc012(m, [m]) if f.level == "fail"] == []

    def test_every_import_form_counts(self, conformance, tmp_path):
        m = self._rail(conformance, tmp_path, {
            "src/a.tsx": "const s = import('./dyn.css')\nimport raw from './q.css?inline'\n",
            "src/deep/b.ts": 'import "../up.css"\n',
            "src/dyn.css": "body { x: 1; }", "src/q.css": "body { x: 1; }",
            "src/up.css": "body { x: 1; }"})
        assert sorted(n for n, _ in self._shipped(conformance, m)) == ["dyn.css", "q.css", "up.css"]

    def test_index_html_links_and_inline_styles_count(self, conformance, tmp_path):
        m = self._rail(conformance, tmp_path, {
            "index.html": '<html><head><link rel="stylesheet" href="/src/page.css">'
                          '<link rel="stylesheet" href="https://fonts.example/x.css">'
                          '<!-- <link rel="stylesheet" href="/src/gone.css"> -->'
                          '<style>body { margin: 0 }</style></head></html>',
            "src/page.css": "html { x: 1; }", "src/gone.css": "html { x: 1; }"})
        got = self._shipped(conformance, m)
        assert [n for n, _ in got] == ["page.css", "index.html"], got
        assert "inline <style>" in got[1][1]

    def test_theme_css_and_package_css_are_not_judged_here(self, conformance, tmp_path):
        """theme.css is RC012's own read already; a package's CSS is not the rail's to scope
        (RC032 reads it in the bundle)."""
        m = self._rail(conformance, tmp_path, {
            "src/module.tsx": "import './theme.css'\nimport '@xterm/xterm/css/xterm.css'\n"})
        assert self._shipped(conformance, m) == []


# A built bundle's stylesheet, minified the way vite emits it.
BUILT_LEAKY = (':root{--page: #faf8f6}:root[data-theme=dark]{--page: #14121a;color-scheme:dark}'
               'html,body{margin:0;background:var(--page);min-height:100vh}#root{padding:20px}'
               '.zq{color:red}.zq .a{x:1}')


class TestRc032BuiltBundle:
    """RC032 reads the stylesheets the build emitted, which is what remoteEntry.js injects."""
    def _built(self, conformance, tmp_path, css: str | None, wrapper="zq"):
        m = manifest(conformance, tmp_path, "zq", {"id": "zq", "css_wrapper": wrapper})
        src = tmp_path / "rails" / "zq" / "frontend" / "src"
        src.mkdir(parents=True, exist_ok=True)
        if css is not None:
            assets = src.parent / "dist" / "assets"
            assets.mkdir(parents=True, exist_ok=True)
            (assets / "remoteEntry.js").write_text('w(["style-x.css"],!1,"./module")', encoding="utf-8")
            (assets / "style-x.css").write_text(css, encoding="utf-8")
        return m

    def test_rc032_fails_a_bundle_with_global_rules(self, conformance, tmp_path):
        out = conformance.rc032(self._built(conformance, tmp_path, BUILT_LEAKY), [])
        assert [f.level for f in out] == ["fail"], out
        for sel in (":root", ":root[data-theme=dark]", "html", "body", "#root"):
            assert sel in out[0].message
        assert "holds 5 global rule(s)" in out[0].message and "style-x.css" in out[0].message

    @pytest.mark.parametrize("css", [
        ".zq{color:red}.zq .a{x:1}@keyframes zq-spin{from{x:0}to{x:1}}",
        ":root[data-theme=dark] .zq{x:1}html[data-x] .zq .b{x:1}",
        ".zq :is(h1,h2){x:1}.xterm .xterm-helpers{x:1}@media(max-width:9px){.zq{x:2}}",
    ])
    def test_scoped_and_class_led_rules_pass(self, conformance, tmp_path, css):
        assert conformance.rc032(self._built(conformance, tmp_path, css), []) == []

    @pytest.mark.parametrize("css,sel", [
        ("button{x:1}", "button"), ("*{box-sizing:border-box}", "*"),
        ("@media(max-width:9px){body{x:1}}", "body"), (".zq{x:1}h3,.zq .h{x:1}", "h3"),
    ])
    def test_each_global_form_fails_by_name(self, conformance, tmp_path, css, sel):
        out = conformance.rc032(self._built(conformance, tmp_path, css), [])
        assert [f.level for f in out] == ["fail"] and f": {sel}." in out[0].message, out

    def test_an_unbuilt_rail_is_a_skip_under_the_unbuilt_tree_allowance(self, conformance, tmp_path):
        out = conformance.rc032(self._built(conformance, tmp_path, None), [])
        assert [(f.level, f.allowance) for f in out] == [("skip", "unbuilt-tree")]


def test_inherit_only_tokens_are_declared(conformance):
    assert conformance.INHERIT_ONLY_TOKENS == ("accent", "muted", "good")


def test_load_manifests_reads_the_real_rails(conformance):
    manifests, errs = conformance.load_manifests()
    assert manifests, "no rail manifests found - every rule would check nothing"
    assert errs == [], f"a rail.json does not parse: {[e.message for e in errs]}"


def test_load_manifests_reports_a_broken_manifest_rather_than_raising(conformance, tmp_path,
                                                                     monkeypatch):
    d = tmp_path / "broken"
    d.mkdir()
    (d / "rail.json").write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(conformance, "RAILS", tmp_path)
    manifests, errs = conformance.load_manifests()
    assert manifests == []
    assert len(errs) == 1 and errs[0].rule == "RC001"


def _vite_rc002(conformance, tmp_path, monkeypatch, other_dir: str):
    """A rail whose id differs from its directory (as iep-goals' does), declaring dev port
    5399, with its own vite config on 5399 and a second config in `other_dir` on 5399 too."""
    rails = tmp_path / "rails"
    m = manifest(conformance, tmp_path, "x", {"id": "x-goals", "ports": {"dev": 5399}})
    for d in {"x", other_dir}:
        fe = rails / d / "frontend"
        fe.mkdir(parents=True, exist_ok=True)
        (fe / "vite.config.ts").write_text("export default {\n  server: {\n    port: 5399,\n  },\n}\n",
                                           encoding="utf-8")
    monkeypatch.setattr(conformance, "RAILS", rails)
    conformance._all_vite_ports.cache_clear()
    try:
        return conformance.rc002(m, [m])
    finally:
        conformance._all_vite_ports.cache_clear()


def test_rc002_a_rail_does_not_clash_with_its_own_vite_config(conformance, tmp_path, monkeypatch):
    assert _vite_rc002(conformance, tmp_path, monkeypatch, other_dir="x") == []


def test_rc002_still_names_another_rails_vite_config(conformance, tmp_path, monkeypatch):
    out = _vite_rc002(conformance, tmp_path, monkeypatch, other_dir="other")
    assert [f.message for f in out] == ["dev port 5399 is also claimed by other"], out


def test_unmanifested_rails_ignores_an_empty_scaffold(conformance, tmp_path, monkeypatch):
    """A gitignored local scaffold (bouquet's, once) is every subdirectory, zero files.
    Reporting it as outside the contract is noise, and noise is how a report stops being
    read.'"""
    scaffold = tmp_path / "scaffold" / "frontend" / "src"
    scaffold.mkdir(parents=True)
    monkeypatch.setattr(conformance, "RAILS", tmp_path)
    assert conformance.unmanifested_rails() == []


def test_unmanifested_rails_reports_a_real_one(conformance, tmp_path, monkeypatch):
    d = tmp_path / "stray" / "frontend" / "src"
    d.mkdir(parents=True)
    (d / "module.tsx").write_text("//", encoding="utf-8")
    monkeypatch.setattr(conformance, "RAILS", tmp_path)
    assert conformance.unmanifested_rails() == ["stray"]


def test_unmanifested_rails_ignores_a_manifested_one(conformance, tmp_path, monkeypatch):
    d = tmp_path / "proper"
    (d / "frontend" / "src").mkdir(parents=True)
    (d / "frontend" / "src" / "module.tsx").write_text("//", encoding="utf-8")
    (d / "rail.json").write_text(json.dumps({"id": "proper"}), encoding="utf-8")
    monkeypatch.setattr(conformance, "RAILS", tmp_path)
    assert conformance.unmanifested_rails() == []


def test_unmanifested_rails_finds_a_relocated_frontend(conformance, tmp_path, monkeypatch):
    """A multi-app rail shaped like edu-suite (frontend at apps/dashboard/frontend/) and no
    manifest. Keying on <dir>/frontend alone made it invisible: outside the contract, and
    nothing said so."""
    d = tmp_path / "multi" / "apps" / "dashboard" / "frontend"
    (d / "src").mkdir(parents=True)
    (d / "vite.config.ts").write_text("export default {}\n", encoding="utf-8")
    monkeypatch.setattr(conformance, "RAILS", tmp_path)
    assert conformance.unmanifested_rails() == ["multi"]


def test_unmanifested_rails_ignores_a_directory_with_no_frontend(conformance, tmp_path,
                                                                 monkeypatch):
    """Files but no frontend anywhere (a docs folder, a backend-only experiment) is not a
    rail the shell could mount, so it is not reported."""
    d = tmp_path / "notes"
    d.mkdir()
    (d / "README.md").write_text("notes\n", encoding="utf-8")
    monkeypatch.setattr(conformance, "RAILS", tmp_path)
    assert conformance.unmanifested_rails() == []


def test_unmanifested_rails_ignores_a_vite_config_inside_node_modules(conformance, tmp_path,
                                                                      monkeypatch):
    """A dependency's own vite config is not this rail's frontend; _walk prunes there."""
    d = tmp_path / "deps" / "node_modules" / "some-lib"
    d.mkdir(parents=True)
    (d / "vite.config.js").write_text("export default {}\n", encoding="utf-8")
    monkeypatch.setattr(conformance, "RAILS", tmp_path)
    assert conformance.unmanifested_rails() == []


# =============================================================================
# RC003 - an unreadable catalog
# =============================================================================

def test_rc003_names_the_literal_it_actually_reads(conformance, tmp_path, monkeypatch):
    """gateway_catalog() reads `_ENTRIES`. The warning said APP_CATALOG, which is a sorted(...)
    call and cannot be literal-eval'd, so it sent the reader to the wrong line."""
    monkeypatch.setattr(conformance, "gateway_catalog", lambda: None)
    m = manifest(conformance, tmp_path, "x", {"id": "x"})
    out = conformance.rc003(m, [m])
    assert [f.level for f in out] == ["warn"], out
    assert "_ENTRIES" in out[0].message and "APP_CATALOG" not in out[0].message


# =============================================================================
# RC015 - a rail with a status route polls it
# =============================================================================

class TestRc015Polls:
    """web-core's usePoll (web/src/poll.ts) is a poll. The rule knew only setInterval, so every
    rail that moved its chips poll onto the shared poller (no tick while hidden, none
    overlapping) read as frozen."""

    def _rail(self, conformance, tmp_path, module_src: str):
        m = manifest(conformance, tmp_path, "x", {"id": "x", "status_route": "/api/capabilities"})
        src = m.frontend_src()
        src.mkdir(parents=True, exist_ok=True)
        (src / "api.ts").write_text(
            "export const api = { capabilities: () => getJSON('/api/capabilities') }\n", encoding="utf-8")
        (src / "module.tsx").write_text(module_src, encoding="utf-8")
        return m

    def test_a_use_poll_call_counts_as_polling(self, conformance, tmp_path):
        m = self._rail(conformance, tmp_path,
                       "usePoll(async () => { setCaps(await api.capabilities()) }, 6000, [])\n")
        assert conformance.rc015(m, [m]) == []

    def test_a_start_poll_call_counts_as_polling(self, conformance, tmp_path):
        m = self._rail(conformance, tmp_path,
                       "const stop = startPoll(() => api.capabilities().then(setCaps), 6000)\n")
        assert conformance.rc015(m, [m]) == []

    def test_a_raw_interval_still_counts(self, conformance, tmp_path):
        m = self._rail(conformance, tmp_path,
                       "const id = setInterval(() => api.capabilities().then(setCaps), 6000)\n")
        assert conformance.rc015(m, [m]) == []

    def test_a_one_shot_fetch_is_still_flagged(self, conformance, tmp_path):
        m = self._rail(conformance, tmp_path,
                       "import { usePoll } from '@web-core'\n// TODO: move this onto usePoll\n"
                       "useEffect(() => { api.capabilities().then(setCaps) }, [])\n")
        out = conformance.rc015(m, [m])
        assert [f.level for f in out] == ["fail"], out


# =============================================================================
# TOKEN_ENV_NAMES - the two spellings of the shared broker secret (RC005 / RC014)
# =============================================================================

class TestBrokerTokenNames:
    """RC005 reads what a rail READS; RC014 reads what deploy/ WRITES. They were taught the
    BROKER_AUTH_TOKEN_FILE form together, on purpose: teaching one and not the other turns the
    checker red the moment a rail or a compose file moves, and a red checker that everyone
    knows to ignore protects nothing.

    The subtle one is prefix collision. "BROKER_AUTH_TOKEN" is a proper prefix of
    "BROKER_AUTH_TOKEN_FILE", so a substring test cannot tell a rail that reads only the file
    form from one that reads the value form — and a compose regex that stops at the shorter
    name matches a service that in fact passes only the longer one. Both directions are pinned
    below, because both would read as a PASS.
    """

    SLOTS = [{"slot": "chat", "role": "chat", "env": "X_MODEL", "admin_panel": False}]

    def _rail(self, conformance, tmp_path, source: str):
        m = manifest(conformance, tmp_path, "tokenrail",
                     {"id": "tokenrail", "package": "tokenrail_app", "env_prefix": "TOKENRAIL_",
                      "model_slots": self.SLOTS})
        pkg = m.root / "backend" / "tokenrail_app"
        pkg.mkdir(parents=True, exist_ok=True)
        (pkg / "broker.py").write_text(source, encoding="utf-8")
        return m

    # --- RC005: what the rail reads ----------------------------------------

    def test_both_names_are_canonical(self, conformance):
        assert conformance.TOKEN_ENV_NAMES == ("BROKER_AUTH_TOKEN", "BROKER_AUTH_TOKEN_FILE")

    def test_rc005_accepts_the_value_form(self, conformance, tmp_path):
        m = self._rail(conformance, tmp_path,
                       'import os\n_T = os.environ.get("BROKER_AUTH_TOKEN", "")\n')
        assert conformance.rc005(m, []) == []

    def test_rc005_accepts_the_file_form_alone(self, conformance, tmp_path):
        """A rail on a stack that has fully switched over reads only the path. It is correct,
        and before this change the rule called it 'never reads BROKER_AUTH_TOKEN'."""
        m = self._rail(conformance, tmp_path,
                       'import os\n_T = os.environ.get("BROKER_AUTH_TOKEN_FILE", "")\n')
        assert conformance.rc005(m, []) == []

    def test_rc005_still_fails_a_rail_that_reads_neither(self, conformance, tmp_path):
        m = self._rail(conformance, tmp_path, 'import os\n_T = os.environ.get("NOPE", "")\n')
        out = conformance.rc005(m, [])
        assert len(out) == 1 and out[0].rule == "RC005"

    def test_rc005_still_fails_a_prefixed_only_field(self, conformance, tmp_path):
        """The original defect: CO_WORKER_BROKER_AUTH_TOKEN and nothing else. Adding a second
        canonical name must not blunt the rule that found it."""
        m = self._rail(conformance, tmp_path,
                       "class S:\n    tokenrail_broker_auth_token: str = ''\n")
        out = conformance.rc005(m, [])
        assert len(out) == 1 and "TOKENRAIL_" in out[0].message

    def test_rc005_is_not_fooled_by_the_name_in_a_comment(self, conformance, tmp_path):
        """AST constants, not raw text — the property that made the rule work at all."""
        m = self._rail(conformance, tmp_path,
                       "import os\n# we should read BROKER_AUTH_TOKEN_FILE one day\n")
        assert len(conformance.rc005(m, [])) == 1

    @pytest.mark.parametrize("first", [
        'import os\np = os.environ.get("BROKER_AUTH_TOKEN_FILE", "")\nv = os.environ.get("BROKER_AUTH_TOKEN", "")\n',
        'import os\np = os.environ.get("BROKER_AUTH_TOKEN_FILE", "")\n',
    ])
    def test_rc005_fails_a_reader_left_behind_in_a_rail_that_moved(self, conformance, tmp_path,
                                                                   first):
        """Per module, not per rail: job-aid's llm/client.py read the file form and its
        broker_status.py only the value, and the rail passed because between them it named
        both constants. At the switchover the second reader's calls 401."""
        m = self._rail(conformance, tmp_path, first)
        (m.root / "backend" / "tokenrail_app" / "status.py").write_text(
            'import os\n_T = os.environ.get("BROKER_AUTH_TOKEN", "")\n', encoding="utf-8")
        out = conformance.rc005(m, [])
        assert [f.level for f in out] == ["fail"]
        assert "status.py reads BROKER_AUTH_TOKEN but never BROKER_AUTH_TOKEN_FILE" in out[0].message
        assert "broker.py reads the file form" in out[0].message

    def test_rc005_passes_a_rail_whose_readers_all_read_the_value(self, conformance, tmp_path):
        """Consistently on the value form is right for the deployment it is on (TOKEN_ENV_NAMES)."""
        m = self._rail(conformance, tmp_path, 'import os\n_T = os.environ.get("BROKER_AUTH_TOKEN", "")\n')
        (m.root / "backend" / "tokenrail_app" / "status.py").write_text(
            'import os\n_T = os.environ.get("BROKER_AUTH_TOKEN", "")\n', encoding="utf-8")
        assert conformance.rc005(m, []) == []

    # --- RC014: what deploy/ writes ----------------------------------------

    def _compose(self, conformance, tmp_path, monkeypatch, body: str):
        cf = tmp_path / "docker-compose.yml"
        cf.write_text(f"services:\n  tokenrail:\n{body}", encoding="utf-8")
        monkeypatch.setattr(conformance, "COMPOSE", cf)
        return manifest(conformance, tmp_path, "tokenrail",
                        {"id": "tokenrail", "model_slots": self.SLOTS})

    def test_rc014_accepts_the_value_form(self, conformance, tmp_path, monkeypatch):
        m = self._compose(conformance, tmp_path, monkeypatch,
                          "    environment:\n      BROKER_AUTH_TOKEN: ${BROKER_AUTH_TOKEN:-}\n")
        assert conformance.rc014(m, []) == []

    def test_rc014_accepts_the_file_form_alone(self, conformance, tmp_path, monkeypatch):
        """The switched-over compose file. Without this, flipping the stack reports nine
        services as 'passes no broker token' while every one of them authenticates."""
        m = self._compose(
            conformance, tmp_path, monkeypatch,
            "    environment:\n      BROKER_AUTH_TOKEN_FILE: /run/secrets/broker_token\n")
        assert conformance.rc014(m, []) == []

    def test_rc014_still_warns_when_no_token_is_passed(self, conformance, tmp_path, monkeypatch):
        m = self._compose(conformance, tmp_path, monkeypatch,
                          "    environment:\n      TZ: UTC\n")
        out = conformance.rc014(m, [])
        assert len(out) == 1 and out[0].level == "warn"

    def test_rc014_still_fails_a_prefixed_only_spelling(self, conformance, tmp_path, monkeypatch):
        m = self._compose(
            conformance, tmp_path, monkeypatch,
            "    environment:\n      TOKENRAIL_BROKER_AUTH_TOKEN: ${BROKER_AUTH_TOKEN:-}\n")
        out = conformance.rc014(m, [])
        assert len(out) == 1 and out[0].level == "fail"

    def test_rc014_prefixed_file_form_is_also_a_fail(self, conformance, tmp_path, monkeypatch):
        """The prefix trap in its nastiest shape: a name that ends in the canonical one but is
        not it. `(?:_FILE)?` widened the regex, so the whole-name comparison is what still
        catches this."""
        m = self._compose(
            conformance, tmp_path, monkeypatch,
            "    environment:\n      TOKENRAIL_BROKER_AUTH_TOKEN_FILE: /run/secrets/t\n")
        out = conformance.rc014(m, [])
        assert len(out) == 1 and out[0].level == "fail"


# =============================================================================
# RC016 - built vs unbuilt
# =============================================================================

class TestRC016BuiltVsUnbuilt:
    """RC016 failed EVERY rail on any tree where nothing had been built, because `dist/` is
    gitignored so the directory it checks does not exist in a fresh clone or a publish
    snapshot. That red-lit `public-snapshot-dryrun.yml` on every push for at least a month
    (gates 1-6 clean, gate 7 nine RC016 fails), and a merge gate that is always red cannot
    distinguish a real leak from its own standing failure.

    The fix after that was comparative ("missing while a sibling is built"), and that failed
    every unbuilt rail as soon as ONE rail was built: a partial local build made
    `--strict --allow unbuilt-tree` fail on nine rails whose defaults were fine. So each default
    is now judged on its own shape, and each case below is a real tree on disk: unbuilt (skip
    under the allowance), rotted (outside the rail, or its parent gone: fail), broken (built
    directory without remoteEntry.js: fail) and built (clean), in every mix.
    """

    # The states a rail's dist can be in, each built as a real tree under tmp_path/rails/<n>.
    UNBUILT, BUILT, BROKEN, OUTSIDE, ORPHAN = "unbuilt", "built", "broken", "outside", "orphan"

    def _rails(self, conformance, tmp_path, monkeypatch, states: dict[str, str],
               sub: str = "frontend/dist"):
        """One rail per entry of `states`, its default laid out on disk in that state."""
        mans, defaults = [], {}
        for n, state in states.items():
            mans.append(manifest(conformance, tmp_path, n, {"id": n}))
            rail = tmp_path / "rails" / n
            dist = rail / sub
            if state == self.OUTSIDE:
                # The pre-monorepo shape: a standalone checkout's dist, here even BUILT, so the
                # only thing wrong is where it is.
                dist = tmp_path / "standalone" / n / "frontend" / "dist"
                (dist / "assets").mkdir(parents=True)
                (dist / "assets" / "remoteEntry.js").write_text("//", encoding="utf-8")
            elif state == self.ORPHAN:
                dist = rail / "frontend-old" / "dist"   # inside the rail, parent never made
            else:
                dist.parent.mkdir(parents=True, exist_ok=True)
                if state in (self.BUILT, self.BROKEN):
                    (dist / "assets").mkdir(parents=True)
                if state == self.BUILT:
                    (dist / "assets" / "remoteEntry.js").write_text("//", encoding="utf-8")
            defaults[f"{n}_dist"] = str(dist)
        monkeypatch.setattr(conformance, "gateway_field_defaults", lambda: (defaults, ""))
        return mans

    def _run(self, conformance, mans):
        return {m.id: conformance.rc016(m, mans) for m in mans}

    def test_an_unbuilt_tree_SKIPS_rather_than_failing_every_rail(
            self, conformance, tmp_path, monkeypatch):
        """The regression that red-lit CI. Nothing is built, so nothing rotted."""
        mans = self._rails(conformance, tmp_path, monkeypatch,
                           {"alpha": self.UNBUILT, "beta": self.UNBUILT})
        results = self._run(conformance, mans)
        assert set(results) == {"alpha", "beta"}, results
        for rail, out in results.items():
            assert len(out) == 1, out
            assert out[0].level == "skip", out[0].level
            assert out[0].allowance == "unbuilt-tree"
            assert "unbuilt" in out[0].message and f"{rail}_dist" in out[0].message

    def test_a_PARTIAL_build_skips_the_unbuilt_rail_instead_of_failing_it(
            self, conformance, tmp_path, monkeypatch):
        """A partly built tree: one rail built, the rest not. The comparative rule failed the rest
        (nine of seventeen fails on that tree came from this); a sibling's build says nothing
        about this rail's default."""
        mans = self._rails(conformance, tmp_path, monkeypatch,
                           {"alpha": self.BUILT, "beta": self.UNBUILT, "gamma": self.UNBUILT})
        res = self._run(conformance, mans)
        assert res["alpha"] == []
        for rail in ("beta", "gamma"):
            assert [f.level for f in res[rail]] == ["skip"], res[rail]
            assert res[rail][0].allowance == "unbuilt-tree"

    @pytest.mark.parametrize("state, says", [
        ("outside", "outside this rail's directory"),
        ("orphan", "which does not exist"),
    ], ids=["outside-the-rail", "parent-gone"])
    @pytest.mark.parametrize("others", ["unbuilt", "built"], ids=["tree-unbuilt", "tree-built"])
    def test_a_ROTTED_default_FAILS_whatever_else_is_built(
            self, conformance, tmp_path, monkeypatch, state, says, others):
        """The defect RC016 was written for: five defaults pointing at pre-monorepo paths. It
        must fail on its own, so it fails in an unbuilt tree too, which the comparative rule
        could not do (it skipped there)."""
        mans = self._rails(conformance, tmp_path, monkeypatch,
                           {"alpha": others, "beta": state})
        out = conformance.rc016(mans[1], mans)
        assert [f.level for f in out] == ["fail"], out
        assert "beta_dist" in out[0].message and "rotted" in out[0].message
        assert says in out[0].message

    def test_a_BROKEN_build_FAILS(self, conformance, tmp_path, monkeypatch):
        """The directory exists, so the old rule passed it, but resolved_app_dists() requires
        assets/remoteEntry.js and skips the rail without one: a half-finished or failed build
        that serves nothing."""
        mans = self._rails(conformance, tmp_path, monkeypatch,
                           {"alpha": self.UNBUILT, "beta": self.BROKEN})
        out = conformance.rc016(mans[1], mans)
        assert [f.level for f in out] == ["fail"], out
        assert "remoteEntry.js" in out[0].message and "beta_dist" in out[0].message

    def test_a_fully_built_tree_is_clean(self, conformance, tmp_path, monkeypatch):
        mans = self._rails(conformance, tmp_path, monkeypatch,
                           {"alpha": self.BUILT, "beta": self.BUILT})
        assert self._run(conformance, mans) == {"alpha": [], "beta": []}

    @pytest.mark.parametrize("state, level", [("unbuilt", "skip"), ("built", None)],
                             ids=["unbuilt", "built"])
    def test_a_relocated_dist_dash_dir_is_judged_the_same_way(
            self, conformance, tmp_path, monkeypatch, state, level):
        """edu-suite's iep tile builds to apps/dashboard/frontend/dist-iep. Its shape is
        neither `frontend/dist` nor a top-level frontend, and it must still read as unbuilt
        or clean, never as rotted."""
        mans = self._rails(conformance, tmp_path, monkeypatch, {"alpha": state},
                           sub="apps/dashboard/frontend/dist-iep")
        out = conformance.rc016(mans[0], mans)
        assert [f.level for f in out] == ([level] if level else []), out

    def test_two_unbuilt_tiles_on_one_rail_are_ONE_skip_and_a_rotted_one_is_not_hidden(
            self, conformance, tmp_path, monkeypatch):
        """Coverage counts one skip per rail, so a two-tile rail (also_serves) must not emit
        two. And an unbuilt tile must not mask a rotted sibling tile on the same rail."""
        m = manifest(conformance, tmp_path, "duo", {"id": "duo", "also_serves": ["solo"]})
        front = tmp_path / "rails" / "duo" / "frontend"
        front.mkdir(parents=True)
        defaults = {"duo_dist": str(front / "dist"), "solo_dist": str(front / "dist-solo")}
        monkeypatch.setattr(conformance, "gateway_field_defaults", lambda: (defaults, ""))
        out = conformance.rc016(m, [m])
        assert [f.level for f in out] == ["skip"], out
        assert "duo_dist" in out[0].message and "solo_dist" in out[0].message
        defaults["solo_dist"] = str(tmp_path / "standalone" / "solo" / "dist")
        out = conformance.rc016(m, [m])
        assert [f.level for f in out] == ["fail"], out
        assert "solo_dist" in out[0].message


# =============================================================================
# RC009 / RC010 - the vite config follows a relocated frontend
# =============================================================================

class TestViteConfigFollowsTheFrontend:
    """RC009 and RC010 hardcoded `<root>/frontend/vite.config.ts`. edu-suite's lives at
    apps/dashboard/frontend/, so both rules read "", returned a bare [], and were counted as
    having run: a wrong dev port, proxy target, federation name or base on that rail would have
    passed. frontend_src() already knew the way there and RC017 used it; these two did not.

    Each wrong value below is one the old code could not see. The relocated rail is the case
    that matters; the default-layout one pins that following the frontend did not cost the
    rails that never moved.
    """

    RELOCATED = "apps/dashboard/src/dashboard"

    def _rail(self, conformance, tmp_path, monkeypatch, vite: str | None, relocated=True):
        data = {"id": "x", "federation_name": "x_fed", "ports": {"dev": 5210, "backend": 8800}}
        if relocated:
            data["package_path"] = self.RELOCATED
        m = manifest(conformance, tmp_path, "x", data)
        front = m.root / ("apps/dashboard/frontend" if relocated else "frontend")
        (front / "src").mkdir(parents=True)
        if vite is not None:
            (front / "vite.config.ts").write_text(vite, encoding="utf-8")
        # RC010's shell half reads the real shell; point it at an empty directory so these
        # tests see only what the rail's own config produces.
        monkeypatch.setattr(conformance, "SHELL", tmp_path / "no-shell")
        return m

    GOOD = ("export default defineConfig({\n  base: '/x/',\n"
            "  plugins: [federation({ name: 'x_fed' })],\n"
            "  server: { port: 5210, proxy: { '/x/api': { target: 'http://127.0.0.1:8800' } } },\n"
            "})\n")

    def test_vite_config_sits_beside_a_relocated_frontend(self, conformance, tmp_path,
                                                          monkeypatch):
        m = self._rail(conformance, tmp_path, monkeypatch, self.GOOD)
        assert m.vite_config() == m.root / "apps" / "dashboard" / "frontend" / "vite.config.ts"
        assert m.vite_config().is_file()

    def test_a_relocated_rail_that_agrees_is_clean(self, conformance, tmp_path, monkeypatch):
        m = self._rail(conformance, tmp_path, monkeypatch, self.GOOD)
        assert conformance.rc009(m, []) == []
        assert conformance.rc010(m, []) == []

    def test_rc009_reads_a_relocated_dev_port(self, conformance, tmp_path, monkeypatch):
        m = self._rail(conformance, tmp_path, monkeypatch,
                       self.GOOD.replace("port: 5210", "port: 5299"))
        out = conformance.rc009(m, [])
        assert [f.level for f in out] == ["fail"], out
        assert "5299" in out[0].message
        # rel() leaves a path outside the repo (tmp_path) native, so normalise the separator.
        assert Path(out[0].where).as_posix().endswith("apps/dashboard/frontend/vite.config.ts")

    def test_rc009_reads_a_relocated_proxy_target(self, conformance, tmp_path, monkeypatch):
        m = self._rail(conformance, tmp_path, monkeypatch,
                       self.GOOD.replace("127.0.0.1:8800", "127.0.0.1:8899"))
        out = conformance.rc009(m, [])
        assert [f.level for f in out] == ["fail"] and "8899" in out[0].message, out

    def test_rc010_reads_a_relocated_federation_name(self, conformance, tmp_path, monkeypatch):
        m = self._rail(conformance, tmp_path, monkeypatch,
                       self.GOOD.replace("name: 'x_fed'", "name: 'y_fed'"))
        out = conformance.rc010(m, [])
        assert [f.level for f in out] == ["fail"] and "'y_fed'" in out[0].message, out

    def test_rc010_reads_a_relocated_base(self, conformance, tmp_path, monkeypatch):
        m = self._rail(conformance, tmp_path, monkeypatch,
                       self.GOOD.replace("base: '/x/'", "base: '/elsewhere/'"))
        out = conformance.rc010(m, [])
        assert [f.level for f in out] == ["fail"] and "/elsewhere/" in out[0].message, out

    def test_the_default_layout_is_still_read(self, conformance, tmp_path, monkeypatch):
        m = self._rail(conformance, tmp_path, monkeypatch,
                       self.GOOD.replace("port: 5210", "port: 5299"), relocated=False)
        assert m.vite_config() == m.root / "frontend" / "vite.config.ts"
        assert [f.level for f in conformance.rc009(m, [])] == ["fail"]

    def test_a_missing_config_WARNS_rather_than_passing(self, conformance, tmp_path,
                                                         monkeypatch):
        """The bare [] was the defect: 'could not find it' and 'found it and it agrees' printed
        identically. Every manifest declares a dev port and a federation name, so there is
        always something this rule should have been able to read."""
        m = self._rail(conformance, tmp_path, monkeypatch, vite=None)
        for rule in (conformance.rc009, conformance.rc010):
            out = rule(m, [])
            assert [f.level for f in out] == ["warn"], (rule.__name__, out)
            assert "NOT checked" in out[0].message
            assert Path(out[0].where).as_posix().endswith("apps/dashboard/frontend/vite.config.ts")

    def test_rc010_shell_half_still_runs_without_a_rail_config(self, conformance, tmp_path,
                                                               monkeypatch):
        """Only the vite half is blind when the config is missing; an early return would
        silence the three shell-side checks as well."""
        m = self._rail(conformance, tmp_path, monkeypatch, vite=None)
        shell = tmp_path / "shell" / "src"
        shell.mkdir(parents=True)
        (shell / "App.tsx").write_text("const A = lazy(() => import('other/module'))\n",
                                       encoding="utf-8")
        monkeypatch.setattr(conformance, "SHELL", shell)
        levels = sorted(f.level for f in conformance.rc010(m, []))
        assert levels == ["fail", "warn"], levels

    def test_the_real_edu_suite_config_is_the_one_read(self, conformance):
        """On the real tree, not a fixture: the rail this defect was found on."""
        man = REPO / "rails" / "edu-suite" / "rail.json"
        if not man.is_file():
            pytest.skip("edu-suite is withheld from this tree (the public snapshot), so "
                        "there is no relocated frontend here to prove the rule follows")
        m = conformance.Manifest(path=man, data=json.loads(man.read_text(encoding="utf-8")))
        assert m.vite_config() == (REPO / "rails" / "edu-suite" / "apps" / "dashboard"
                                   / "frontend" / "vite.config.ts")
        assert m.vite_config().is_file()
        assert [f for f in conformance.rc009(m, []) if f.level != "skip"] == []


# =============================================================================
# --strict: a warning fails the run, unless a NAMED vacuous condition is allowed
# =============================================================================

class TestStrict:
    """`warn` never failed a run. Every "this rule could not look" warning announced itself and
    exited 0, and publish gate 7 printed `clean` over it: make catalog.py's _ENTRIES a
    non-literal and RC003 warned on every rail while the gate passed.

    --strict fails on a warning and on an inert rule (one that skipped every rail). The way out
    is an allowance named in VACUOUS and carried by the exact finding that is vacuous, so the
    tests below are mostly about what an allowance must NOT cover.

    main() runs for real here; only its inputs are replaced: two throwaway rails and a list of
    stand-in rules.
    """

    def _main(self, conformance, tmp_path, monkeypatch, rules, *argv, rails=("r0", "r1")):
        import sys
        mans = [manifest(conformance, tmp_path, r, {"id": r}) for r in rails]
        monkeypatch.setattr(conformance, "RAILS", tmp_path / "rails")
        monkeypatch.setattr(conformance, "load_manifests", lambda: (list(mans), []))
        monkeypatch.setattr(conformance, "RULES", list(rules))
        monkeypatch.setattr(sys, "argv", ["rail_conformance.py", *argv])
        return conformance.main()

    @staticmethod
    def _rule(fn, rid="RC900"):
        return (rid, "a stand-in rule", fn)

    def _warn(self, conformance, allowance=""):
        return self._rule(lambda m, _a: [conformance.F("RC900", m, "could not look", "",
                                                       level="warn", allowance=allowance)])

    # --- the defect -------------------------------------------------------

    def test_a_warning_still_passes_without_strict(self, conformance, tmp_path, monkeypatch):
        """Default behaviour is unchanged: the announce-rather-than-pass design is right."""
        assert self._main(conformance, tmp_path, monkeypatch, [self._warn(conformance)]) == 0

    def test_an_unallowed_warning_fails_strict(self, conformance, tmp_path, monkeypatch,
                                                capsys):
        code = self._main(conformance, tmp_path, monkeypatch, [self._warn(conformance)],
                          "--strict")
        assert code == 1
        assert "strict: 2 warning(s) not allowed (FAIL)" in capsys.readouterr().out

    def test_a_crashed_rule_fails_strict(self, conformance, tmp_path, monkeypatch):
        """main() turns a rule's exception into a WARN so it cannot mask the others. That warn
        carries no allowance, so strict mode must refuse it."""
        def boom(_m, _a):
            raise RuntimeError("parser drift")
        assert self._main(conformance, tmp_path, monkeypatch, [self._rule(boom)]) == 0
        assert self._main(conformance, tmp_path, monkeypatch, [self._rule(boom)],
                          "--strict", "--allow", "unbuilt-tree",
                          "--allow", "pseudonyms-absent") == 1

    def test_an_inert_rule_fails_strict(self, conformance, tmp_path, monkeypatch):
        """A rule that skipped every rail checked nothing: the same "could not look", at rule
        scale, so --strict counts it."""
        rules = [self._rule(lambda m, _a: [conformance.S("RC900", m, "nothing here")])]
        assert self._main(conformance, tmp_path, monkeypatch, rules) == 0
        assert self._main(conformance, tmp_path, monkeypatch, rules, "--strict") == 1

    def test_a_clean_run_passes_strict(self, conformance, tmp_path, monkeypatch):
        rules = [self._rule(lambda _m, _a: [])]
        assert self._main(conformance, tmp_path, monkeypatch, rules, "--strict") == 0

    def test_no_manifest_at_all_fails_strict(self, conformance, tmp_path, monkeypatch, capsys):
        """Zero manifests: every rule ran over nothing, none could be called inert, and the run
        printed "no findings" and exited 0 under --strict (audit 2026-10-14). Not allowable:
        --allow cannot excuse it."""
        rules = [self._rule(lambda _m, _a: [])]
        assert self._main(conformance, tmp_path, monkeypatch, rules, rails=()) == 0
        assert "WARN  no rail manifest was found" in capsys.readouterr().out
        assert self._main(conformance, tmp_path, monkeypatch, rules, "--strict", "--allow", "unbuilt-tree",
                          "--allow", "pseudonyms-absent", rails=()) == 1
        assert "strict: 1 warning(s) not allowed (FAIL)" in capsys.readouterr().out

    # --- the plain summary line (not strict-specific) ----------------------

    def test_the_summary_counts_an_inert_rule_as_a_warning(self, conformance, tmp_path,
                                                           monkeypatch, capsys):
        """The report printed `WARN RC0xx checked nothing this run` and then, directly under
        it, `0 fail, 0 warn`. The summary must agree with the lines above it."""
        rules = [self._rule(lambda m, _a: [conformance.S("RC900", m, "nothing here")])]
        self._main(conformance, tmp_path, monkeypatch, rules)
        out = capsys.readouterr().out
        assert "RC900  checked nothing this run" in out
        assert "0 fail, 1 warn (incl. 1 inert rule(s))" in out, out

    def test_the_summary_adds_inert_rules_to_finding_warnings(self, conformance, tmp_path,
                                                              monkeypatch, capsys):
        rules = [self._warn(conformance),
                 self._rule(lambda m, _a: [conformance.S("RC901", m, "nothing")], "RC901")]
        self._main(conformance, tmp_path, monkeypatch, rules)
        assert "0 fail, 3 warn (incl. 1 inert rule(s))" in capsys.readouterr().out

    def test_the_summary_without_inert_rules_is_unchanged(self, conformance, tmp_path,
                                                          monkeypatch, capsys):
        self._main(conformance, tmp_path, monkeypatch, [self._warn(conformance)])
        out = capsys.readouterr().out
        assert "0 fail, 2 warn\n" in out and "inert rule" not in out

    # --- what an allowance covers, and what it must not -------------------

    def test_a_named_allowance_excuses_its_own_warning(self, conformance, tmp_path,
                                                       monkeypatch, capsys):
        rules = [self._warn(conformance, "pseudonyms-absent")]
        code = self._main(conformance, tmp_path, monkeypatch, rules,
                          "--strict", "--allow", "pseudonyms-absent")
        out = capsys.readouterr().out
        assert code == 0, out
        # Allowed is not hidden: the warning is still printed, marked.
        assert "could not look  (allowed: pseudonyms-absent)" in out
        assert "allowances used: pseudonyms-absent x2" in out

    def test_an_allowance_is_never_implied(self, conformance, tmp_path, monkeypatch):
        """Carrying a tag is not enough; the caller has to name it."""
        rules = [self._warn(conformance, "pseudonyms-absent")]
        assert self._main(conformance, tmp_path, monkeypatch, rules, "--strict") == 1
        assert self._main(conformance, tmp_path, monkeypatch, rules,
                          "--strict", "--allow", "unbuilt-tree") == 1

    def test_an_allowance_does_not_cover_another_warning_from_the_same_rule(
            self, conformance, tmp_path, monkeypatch):
        """Keyed on the condition, not the rule. RC016 has a vacuous skip AND a real
        'cannot import the gateway settings' warning; excusing the rule would excuse both."""
        def mixed(m, _a):
            tag = "pseudonyms-absent" if m.id == "r0" else ""
            return [conformance.F("RC900", m, "could not look", "", level="warn", allowance=tag)]
        assert self._main(conformance, tmp_path, monkeypatch, [self._rule(mixed)],
                          "--strict", "--allow", "pseudonyms-absent") == 1

    def test_an_allowance_never_excuses_a_FAIL(self, conformance, tmp_path, monkeypatch):
        rules = [self._rule(lambda m, _a: [conformance.F("RC900", m, "wrong", "",
                                                         allowance="pseudonyms-absent")])]
        assert self._main(conformance, tmp_path, monkeypatch, rules,
                          "--strict", "--allow", "pseudonyms-absent") == 1

    def test_an_inert_rule_whose_skips_share_an_allowance_is_excused(
            self, conformance, tmp_path, monkeypatch, capsys):
        rules = [self._rule(lambda m, _a: [conformance.S("RC900", m, "unbuilt",
                                                         allowance="unbuilt-tree")])]
        assert self._main(conformance, tmp_path, monkeypatch, rules, "--strict") == 1
        code = self._main(conformance, tmp_path, monkeypatch, rules,
                          "--strict", "--allow", "unbuilt-tree")
        out = capsys.readouterr().out
        assert code == 0, out
        assert "so it is currently decoration  (allowed: unbuilt-tree)" in out

    def test_an_inert_rule_with_MIXED_skip_reasons_is_not_excused(
            self, conformance, tmp_path, monkeypatch):
        """Every rail skipped, but only one for the vacuous reason. The other skip is a
        different "checked nothing", and the allowance does not speak for it."""
        def mixed(m, _a):
            tag = "unbuilt-tree" if m.id == "r0" else ""
            return [conformance.S("RC900", m, "skipped", allowance=tag)]
        assert self._main(conformance, tmp_path, monkeypatch, [self._rule(mixed)],
                          "--strict", "--allow", "unbuilt-tree") == 1

    # --- the CLI ----------------------------------------------------------

    def test_strict_and_warn_only_are_refused_together(self, conformance, tmp_path,
                                                       monkeypatch):
        with pytest.raises(SystemExit) as exc:
            self._main(conformance, tmp_path, monkeypatch, [], "--strict", "--warn-only")
        assert exc.value.code == 2

    def test_an_unknown_allowance_is_refused(self, conformance, tmp_path, monkeypatch):
        """A typo must not silently allow nothing; it must not run at all."""
        with pytest.raises(SystemExit) as exc:
            self._main(conformance, tmp_path, monkeypatch, [], "--strict", "--allow", "all")
        assert exc.value.code == 2

    def test_allow_without_strict_is_refused(self, conformance, tmp_path, monkeypatch):
        with pytest.raises(SystemExit) as exc:
            self._main(conformance, tmp_path, monkeypatch, [], "--allow", "unbuilt-tree")
        assert exc.value.code == 2

    def test_json_mode_exits_the_same_way(self, conformance, tmp_path, monkeypatch, capsys):
        rules = [self._warn(conformance)]
        assert self._main(conformance, tmp_path, monkeypatch, rules, "--json") == 0
        capsys.readouterr()
        assert self._main(conformance, tmp_path, monkeypatch, rules, "--json", "--strict") == 1
        doc = json.loads(capsys.readouterr().out)
        assert doc["strict"] is True and len(doc["unallowed"]) == 2

    def test_every_allowance_states_its_reason(self, conformance):
        assert set(conformance.VACUOUS) == {"pseudonyms-absent", "unbuilt-tree"}
        for name, why in conformance.VACUOUS.items():
            assert len(why) > 80, f"{name}: an allowance needs its justification"

    # --- the two real conditions, through the real rules ------------------

    def test_rc018_absent_names_carry_their_allowance(self, conformance, tmp_path, monkeypatch):
        """The REAL rule, run where the gitignored pseudonyms file cannot exist."""
        monkeypatch.setattr(conformance, "REPO", tmp_path)
        m = manifest(conformance, tmp_path, "iep", {"id": "iep-goals"})
        out = conformance.rc018(m, [m])
        assert [(f.level, f.allowance) for f in out] == [("warn", "pseudonyms-absent")]

    def test_rc018_absent_names_pass_strict_only_when_allowed(self, conformance, tmp_path,
                                                              monkeypatch):
        monkeypatch.setattr(conformance, "REPO", tmp_path)
        rc018 = next(r for r in conformance.RULES if r[0] == "RC018")
        assert self._main(conformance, tmp_path, monkeypatch, [rc018], "--strict",
                          rails=("iep-goals",)) == 1
        assert self._main(conformance, tmp_path, monkeypatch, [rc018], "--strict",
                          "--allow", "pseudonyms-absent", rails=("iep-goals",)) == 0

    def _unbuilt(self, conformance, tmp_path, monkeypatch, defaults=True, built=()):
        """r0 and r1 with their defaults at their own frontend/dist; `built` names the rails
        whose dist is a real build (assets/remoteEntry.js), the rest are unbuilt."""
        dists = {n: tmp_path / "rails" / n / "frontend" / "dist" for n in ("r0", "r1")}
        for n, d in dists.items():
            d.parent.mkdir(parents=True, exist_ok=True)
            if n in built:
                (d / "assets").mkdir(parents=True)
                (d / "assets" / "remoteEntry.js").write_text("//", encoding="utf-8")
        monkeypatch.setattr(
            conformance, "gateway_field_defaults",
            (lambda: ({f"{n}_dist": str(d) for n, d in dists.items()}, ""))
            if defaults else (lambda: (None, "ImportError: no pydantic here")))
        return next(r for r in conformance.RULES if r[0] == "RC016")

    def test_a_PARTIAL_build_passes_strict_with_the_allowance(self, conformance, tmp_path,
                                                              monkeypatch, capsys):
        """The same partly built tree, through main(): r0 built, r1 not. The comparative rule
        failed r1 and so failed `--strict --allow unbuilt-tree` on a tree with nothing wrong in
        it."""
        rc016 = self._unbuilt(conformance, tmp_path, monkeypatch, built=("r0",))
        code = self._main(conformance, tmp_path, monkeypatch, [rc016],
                          "--strict", "--allow", "unbuilt-tree")
        out = capsys.readouterr().out
        assert code == 0, out
        assert "0 fail" in out and "1 skipped" in out, out

    def test_a_rotted_default_fails_strict_even_with_the_allowance(self, conformance, tmp_path,
                                                                   monkeypatch):
        """The allowance speaks for an unbuilt rail, never for a default pointing elsewhere."""
        rc016 = self._unbuilt(conformance, tmp_path, monkeypatch)
        defaults, _ = conformance.gateway_field_defaults()
        defaults["r1_dist"] = str(tmp_path / "standalone" / "r1" / "frontend" / "dist")
        monkeypatch.setattr(conformance, "gateway_field_defaults", lambda: (defaults, ""))
        assert self._main(conformance, tmp_path, monkeypatch, [rc016],
                          "--strict", "--allow", "unbuilt-tree") == 1

    def test_an_unbuilt_tree_passes_strict_only_when_allowed(self, conformance, tmp_path,
                                                             monkeypatch):
        """The REAL RC016 on a tree with no dist, which is every CI checkout and every publish
        snapshot. Without the allowance strict mode is unusable there; with it, passes."""
        rc016 = self._unbuilt(conformance, tmp_path, monkeypatch)
        assert self._main(conformance, tmp_path, monkeypatch, [rc016], "--strict") == 1
        assert self._main(conformance, tmp_path, monkeypatch, [rc016],
                          "--strict", "--allow", "unbuilt-tree") == 0

    def test_rc016_unreadable_settings_are_NOT_excused_by_unbuilt_tree(
            self, conformance, tmp_path, monkeypatch):
        """Same rule, different condition: an import failure means RC016 inspected nothing,
        and in CI the gateway deps are installed precisely so this does not happen."""
        rc016 = self._unbuilt(conformance, tmp_path, monkeypatch, defaults=False)
        assert self._main(conformance, tmp_path, monkeypatch, [rc016],
                          "--strict", "--allow", "unbuilt-tree") == 1


class TestFilterNamesMustExist:
    """`--rail no-such-rail` and `--rule RC999` selected nothing, printed "no findings" and
    exited 0, also under --strict: a clean bill for a rail or rule never checked. A name
    that matches nothing is now a usage error, exit 2, whatever the exit mode."""

    _main = TestStrict._main

    def _pass(self):
        return TestStrict._rule(lambda m, _a: [])

    @pytest.mark.parametrize("argv,needle", [
        (("--rail", "no-such-rail"), "no rail named no-such-rail"),
        (("--rule", "RC999"), "no rule named RC999"),
        (("--strict", "--rule", "RC999"), "no rule named RC999"),
        (("--warn-only", "--rail", "r0", "--rail", "typo"), "no rail named typo"),
    ])
    def test_a_name_that_matches_nothing_exits_2(self, conformance, tmp_path, monkeypatch,
                                                 capsys, argv, needle):
        with pytest.raises(SystemExit) as exc:
            self._main(conformance, tmp_path, monkeypatch, [self._pass()], *argv)
        assert exc.value.code == 2
        assert needle in capsys.readouterr().err

    def test_real_names_still_run(self, conformance, tmp_path, monkeypatch):
        assert self._main(conformance, tmp_path, monkeypatch, [self._pass()],
                          "--rail", "r1", "--rule", "RC900", "--strict") == 0


class TestRc030ShadowedRoutes:
    """Starlette matches in declaration order: job-aid's `GET /api/jobs/export.xlsx` sat below
    `GET /api/jobs/{job_id}`, so the export URL was parsed as a job_id (a 422) and its handler
    was never reached."""

    @pytest.mark.parametrize("general,specific,hidden", [
        ("/api/jobs/{job_id}", "/api/jobs/export.xlsx", True),
        ("/api/jobs/{job_id}", "/api/jobs/{other}", False),          # two params: not narrower
        ("/api/jobs/export.xlsx", "/api/jobs/{job_id}", False),      # the right order
        ("/api/jobs/{job_id}", "/api/jobs/{job_id}", False),         # the same route
        ("/api/jobs/{job_id:int}", "/api/jobs/export.xlsx", False),  # int never takes a name
        ("/api/jobs/{job_id:int}", "/api/jobs/42", True),
        ("/files/{p:path}", "/files/a/b.txt", True),
        ("/a/{x}/b", "/a/lit/b", True),
        ("/a/{x}/b", "/a/lit/c", False),
        ("/a/{x}", "/a/lit/more", False),                             # a different length
    ])
    def test_which_routes_hide_which(self, conformance, general, specific, hidden):
        assert conformance._route_matches(general.strip("/").split("/"),
                                          specific.strip("/").split("/")) is hidden

    def _rail(self, conformance, tmp_path, src):
        m = manifest(conformance, tmp_path, "rr", {"id": "rr", "package": "rr_app"})
        pkg = m.root / "backend" / "rr_app"
        pkg.mkdir(parents=True, exist_ok=True)
        (pkg / "app.py").write_text(src, encoding="utf-8")
        return m

    def test_the_job_aid_shape_fails_naming_both_routes(self, conformance, tmp_path):
        m = self._rail(conformance, tmp_path,
                       "app = FastAPI()\n@app.get('/api/jobs/{job_id}')\ndef one(job_id: int): ...\n"
                       "@app.get('/api/jobs/export.xlsx')\ndef export(): ...\n")
        out = conformance.rc030(m, [])
        assert [f.level for f in out] == ["fail"]
        assert "GET /api/jobs/{job_id}" in out[0].message and "GET /api/jobs/export.xlsx (:4)" in out[0].message

    @pytest.mark.parametrize("src", [
        "@app.get('/api/jobs/export.xlsx')\ndef e(): ...\n@app.get('/api/jobs/{job_id}')\ndef o(i): ...\n",
        "@app.get('/api/jobs/{job_id}')\ndef o(i): ...\n@app.post('/api/jobs/export.xlsx')\ndef e(): ...\n",
        "@app.get('/api/jobs/{job_id}')\ndef o(i): ...\n@router.get('/api/jobs/export.xlsx')\ndef e(): ...\n",
    ])
    def test_the_right_order_another_method_or_router_passes(self, conformance, tmp_path, src):
        assert conformance.rc030(self._rail(conformance, tmp_path, src), []) == []


class TestRc031UnslottedModels:
    """RC013 holds a slot's default to an @role; a model read outside every slot was invisible.
    recipe-book read RECIPE_BOOK_EMBED_MODEL with the literal default bge-m3 and no embed slot."""

    SLOTS = [{"slot": "chat", "role": "rr", "env": "RR_CHAT_MODEL", "kind": "chat"}]

    def _rail(self, conformance, tmp_path, src, **extra):
        m = manifest(conformance, tmp_path, "rr", {"id": "rr", "package": "rr_app",
                                                    "model_slots": self.SLOTS, **extra})
        pkg = m.root / "backend" / "rr_app"
        pkg.mkdir(parents=True, exist_ok=True)
        (pkg / "broker.py").write_text("import os\n" + src, encoding="utf-8")
        return m

    def test_a_concrete_model_outside_every_slot_fails(self, conformance, tmp_path):
        m = self._rail(conformance, tmp_path, 'EMBED = os.environ.get("RR_EMBED_MODEL", "bge-m3")\n')
        out = conformance.rc031(m, [])
        assert [f.level for f in out] == ["fail"]
        assert "RR_EMBED_MODEL defaults to the concrete model 'bge-m3'" in out[0].message
        assert out[0].where.endswith("broker.py:2")

    @pytest.mark.parametrize("src", [
        'E = os.environ.get("RR_EMBED_MODEL", "@embed")\n',     # an @role: the panel governs it
        'E = os.getenv("RR_EMBED_MODEL", "")\n',                # no default model at all
        'C = os.environ.get("RR_CHAT_MODEL", "qwen3:8b")\n',    # a slot's env: RC013's business
        'T = os.environ.get("RR_TIMEOUT", "30")\n',             # not a model
    ], ids=["at-role", "no-default", "slot-env", "not-a-model"])
    def test_what_is_not_an_unslotted_model_passes(self, conformance, tmp_path, src):
        assert conformance.rc031(self._rail(conformance, tmp_path, src), []) == []

    def test_a_declared_exemption_passes_and_a_reasonless_or_stale_one_fails(self, conformance, tmp_path):
        src = 'C = os.getenv("RR_CLOUD_MODEL", "meta/llama-x")\n'
        ok = self._rail(conformance, tmp_path, src,
                        unslotted_models=[{"env": "RR_CLOUD_MODEL", "why": "a cloud model id"}])
        assert conformance.rc031(ok, []) == []
        bare = self._rail(conformance, tmp_path, src, unslotted_models=[{"env": "RR_CLOUD_MODEL", "why": " "}])
        assert any("needs both an env and a why" in f.message for f in conformance.rc031(bare, []))
        stale = self._rail(conformance, tmp_path, src, unslotted_models=[
            {"env": "RR_CLOUD_MODEL", "why": "a cloud model id"}, {"env": "RR_GONE_MODEL", "why": "was"}])
        assert [f.message for f in conformance.rc031(stale, [])] == [
            "unslotted_models names RR_GONE_MODEL, which nothing in the rail reads with a concrete "
            "default: a stale exemption"]

    @pytest.mark.parametrize("rail,env", [("ai-playground", "AI_PLAYGROUND_NVIDIA_CHAT_MODEL"),
                                          ("bouquet", "BOUQUET_GROUNDING_MODEL")])
    def test_the_shipped_exemptions_carry_a_reason(self, rail, env):
        p = REPO / "rails" / rail / "rail.json"
        if not p.exists():
            pytest.skip(f"{rail} is not in this tree")
        entries = json.loads(p.read_text(encoding="utf-8")).get("unslotted_models", [])
        assert [e["env"] for e in entries] == [env] and entries[0]["why"].strip()


# =============================================================================
# RC033 - the gateway SERVICE's compose block restates every enabled rail
# =============================================================================

class TestRc033GatewayBlock:
    """watch-builder passed every registration rule and was still absent from the running stack:
    the gateway's own compose block lacked its URL, its dist env, the read-only mount behind that
    env and its depends_on, and each of those fails silently (a missing tile, or a proxy that
    hits the gateway itself). RC033 reads that block. These pin each finding, the exemption for a
    rail the gateway does not serve, and the two ways the rule can be unable to look."""

    GOOD = (
        "services:\n"
        "  gateway:                       # shell host + reverse proxy\n"
        "    image: platform-gateway:latest\n"
        "    depends_on:\n"
        "      - other\n"
        "      - zr\n"
        "    environment:\n"
        "      PLATFORM_APP_ZR_URL: http://zr:8999\n"
        "      PLATFORM_ZR_DIST: /app/zr-dist\n"
        "    volumes:\n"
        "      - ../rails/zr/frontend/dist:/app/zr-dist:ro\n"
        "  zr:\n"
        "    image: zr\n"
    )
    ENABLED = 'class S:\n    enabled_apps: Annotated[tuple[str, ...], NoDecode] = ("other", "zr")\n'

    def _run(self, conformance, tmp_path, monkeypatch, compose=None, cfg=None, **data):
        cf = tmp_path / "docker-compose.yml"
        cf.write_text(self.GOOD if compose is None else compose, encoding="utf-8")
        gw = tmp_path / "gateway"
        gw.mkdir(exist_ok=True)
        if cfg is not False:
            (gw / "config.py").write_text(self.ENABLED if cfg is None else cfg, encoding="utf-8")
        monkeypatch.setattr(conformance, "COMPOSE", cf)
        monkeypatch.setattr(conformance, "GATEWAY", gw)
        m = manifest(conformance, tmp_path, "zr", {"id": "zr", "ports": {"backend": 8999}, **data})
        return conformance.rc033(m, [m])

    @staticmethod
    def _edit(old: str, new: str) -> str:
        assert TestRc033GatewayBlock.GOOD.count(old) == 1, old
        return TestRc033GatewayBlock.GOOD.replace(old, new)

    def test_a_block_that_restates_the_rail_passes(self, conformance, tmp_path, monkeypatch):
        assert self._run(conformance, tmp_path, monkeypatch) == []

    @pytest.mark.parametrize("old,new,want", [
        ("      PLATFORM_APP_ZR_URL: http://zr:8999\n", "",
         "does not set 'PLATFORM_APP_ZR_URL'"),
        ("      PLATFORM_ZR_DIST: /app/zr-dist\n", "",
         "is missing 'PLATFORM_ZR_DIST'"),
        ("      - ../rails/zr/frontend/dist:/app/zr-dist:ro\n", "",
         "PLATFORM_ZR_DIST points at '/app/zr-dist' but no read-only mount"),
        (":/app/zr-dist:ro\n", ":/app/zr-dist:rw\n",
         "PLATFORM_ZR_DIST points at '/app/zr-dist' but no read-only mount"),
        (":/app/zr-dist:ro\n", ":/app/zr-dist\n",
         "PLATFORM_ZR_DIST points at '/app/zr-dist' but no read-only mount"),
        # the drift that bites: the env names one path and the mount delivers another
        (":/app/zr-dist:ro\n", ":/app/zr-old-dist:ro\n",
         "PLATFORM_ZR_DIST points at '/app/zr-dist' but no read-only mount"),
        # The block still says `zr` in the URL and the mount path, so a substring test passes it.
        ("      - zr\n", "", "does not depend_on 'zr'"),
        ("      - zr\n", "      - zr-worker\n", "does not depend_on 'zr'"),
        ("      PLATFORM_APP_ZR_URL:", "      OLD_PLATFORM_APP_ZR_URL:",
         "does not set 'PLATFORM_APP_ZR_URL'"),
    ], ids=["url", "dist", "mount", "mount-rw", "mount-no-mode", "mount-elsewhere", "depends_on",
            "depends_on-sibling", "url-longer-key"])
    def test_each_missing_restatement_fails_by_name(self, conformance, tmp_path, monkeypatch,
                                                    old, new, want):
        out = self._run(conformance, tmp_path, monkeypatch, compose=self._edit(old, new))
        assert [f.level for f in out] == ["fail"], out
        assert want in out[0].message, out[0].message

    def test_the_url_finding_names_where_the_proxy_would_go(self, conformance, tmp_path,
                                                            monkeypatch):
        out = self._run(conformance, tmp_path, monkeypatch, container_port=8800,
                        compose=self._edit("      PLATFORM_APP_ZR_URL: http://zr:8999\n", ""))
        assert "/zr/api/*" in out[0].message and "127.0.0.1:8800" in out[0].message

    @pytest.mark.parametrize("old,new,want", [
        ("      PLATFORM_APP_ZR_URL:", "      # PLATFORM_APP_ZR_URL:", "'PLATFORM_APP_ZR_URL'"),
        ("PLATFORM_APP_ZR_URL: http://zr:8999", "PLATFORM_APP_ZR_URL:", "'PLATFORM_APP_ZR_URL'"),
        ("PLATFORM_APP_ZR_URL: http://zr:8999", "PLATFORM_APP_ZR_URL:   # set in deploy/.env",
         "'PLATFORM_APP_ZR_URL'"),
        ("      PLATFORM_ZR_DIST:", "      # PLATFORM_ZR_DIST:", "'PLATFORM_ZR_DIST'"),
        ("      - ../rails/zr/", "      # - ../rails/zr/", "no read-only mount"),
        ("      - zr\n", "      # - zr\n", "does not depend_on 'zr'"),
    ], ids=["url-commented", "url-blank", "url-blank-commented", "dist-commented",
            "mount-commented", "depends_on-commented"])
    def test_a_commented_out_or_blank_restatement_fails(self, conformance, tmp_path, monkeypatch,
                                                        old, new, want):
        """The URL and the mount were substring searches over the block, so a line commented out
        to switch a rail off, or a blank pass-through of an unset host variable, still passed."""
        out = self._run(conformance, tmp_path, monkeypatch, compose=self._edit(old, new))
        assert [f.level for f in out] == ["fail"], out
        assert want in out[0].message, out[0].message

    @pytest.mark.parametrize("compose", [
        # smb-partner-enablement's shape: the container path is not /app/<id>-dist
        GOOD.replace("/app/zr-dist", "/app/zr-partner-dist"),
        # quoted values and trailing comments are YAML, not a different setting
        GOOD.replace("PLATFORM_ZR_DIST: /app/zr-dist", 'PLATFORM_ZR_DIST: "/app/zr-dist"  # served')
            .replace("      - ../rails/zr/frontend/dist:/app/zr-dist:ro",
                     '      - "../rails/zr/frontend/dist:/app/zr-dist:ro"   # read-only')
            .replace("      - zr\n", "      - zr   # the rail\n")
            .replace("http://zr:8999", "http://zr:8999  # container port"),
    ], ids=["own-container-path", "quoted-and-commented"])
    def test_what_the_block_may_legitimately_vary_passes(self, conformance, tmp_path, monkeypatch,
                                                         compose):
        assert compose != self.GOOD
        assert self._run(conformance, tmp_path, monkeypatch, compose=compose) == []

    @pytest.mark.parametrize("svc,ok", [("zr", False), ("zr-svc", True)])
    def test_depends_on_names_the_compose_service_not_the_id(self, conformance, tmp_path,
                                                            monkeypatch, svc, ok):
        """edu-suite's service is `dashboard`: the gateway depends on the service."""
        compose = self._edit("      - zr\n", f"      - {svc}\n")
        out = self._run(conformance, tmp_path, monkeypatch, compose=compose, compose_service="zr-svc")
        if ok:
            assert out == []
        else:
            assert [f.level for f in out] == ["fail"] and "does not depend_on 'zr-svc'" in out[0].message

    def test_a_rail_the_gateway_does_not_serve_is_a_counted_skip(self, conformance, tmp_path,
                                                                 monkeypatch):
        """A disabled rail has no business in the gateway's block, so a block with nothing for it
        is not a failure; it is a skip, so a run where every rail is exempted reads as inert."""
        bare = "services:\n  gateway:\n    image: g\n    depends_on:\n      - other\n"
        out = self._run(conformance, tmp_path, monkeypatch, compose=bare,
                        cfg='enabled_apps = ("other",)\n')
        assert [f.level for f in out] == ["skip"] and "enabled_apps" in out[0].message

    def test_a_single_quoted_enabled_id_is_read_as_enabled(self, conformance, tmp_path,
                                                           monkeypatch):
        out = self._run(conformance, tmp_path, monkeypatch, cfg="enabled_apps = ('other', 'zr')\n",
                        compose=self._edit("      - zr\n", ""))
        assert [f.level for f in out] == ["fail"], out

    @pytest.mark.parametrize("cfg", [
        "enabled_apps: tuple[str, ...] = tuple(ALL_RAILS)\n",
        "class S:\n    other_setting = 1\n",
    ], ids=["not-a-literal", "absent"])
    def test_an_enabled_apps_it_cannot_read_warns_and_still_checks(self, conformance, tmp_path,
                                                                   monkeypatch, cfg):
        """It used to read that as "no rail is enabled", so RC033 exempted every rail, reported
        nothing and counted as having run."""
        out = self._run(conformance, tmp_path, monkeypatch, cfg=cfg,
                        compose=self._edit("      - zr\n", ""))
        assert [f.level for f in out] == ["warn", "fail"], out
        assert "could not read the gateway's enabled_apps" in out[0].message
        assert "does not depend_on 'zr'" in out[1].message

    def test_a_missing_gateway_config_is_not_an_exemption(self, conformance, tmp_path, monkeypatch):
        out = self._run(conformance, tmp_path, monkeypatch, cfg=False,
                        compose=self._edit("      - zr\n", ""))
        assert [f.level for f in out] == ["fail"], out

    def test_no_gateway_service_warns_that_nothing_was_inspected(self, conformance, tmp_path,
                                                                monkeypatch):
        out = self._run(conformance, tmp_path, monkeypatch,
                        compose="services:\n  gateway-old:\n    image: g\n  zr:\n    image: zr\n")
        assert [f.level for f in out] == ["warn"] and "inspected NOTHING" in out[0].message

    @pytest.mark.parametrize("cfg,want", [
        ('class S:\n    enabled_apps: Annotated[tuple[str, ...], NoDecode] = ("a", "b-c")\n',
         {"a", "b-c"}),
        ("enabled_apps = ['a', 'b']\n", {"a", "b"}),
        ("enabled_apps = ()\n", set()),
        ('class S:\n    # enabled_apps was ("x")\n    enabled_apps = ("a",)  # not ("b")\n', {"a"}),
        ("enabled_apps = tuple(X)\n", None),
        ("enabled_apps = ('a', 1)\n", None),
        ("other = ('a',)\n", None),
        ("def broken(:\n", None),
    ], ids=["annotated", "list", "empty", "comments", "expression", "non-string", "absent",
            "syntax-error"])
    def test_the_enabled_apps_reader(self, conformance, cfg, want):
        got = conformance._enabled_app_ids(cfg)
        assert (None if got is None else set(got)) == want

    def test_the_real_gateway_config_names_real_rails(self, conformance):
        """Witness that the reader is not degenerate on the file it exists for."""
        ids = conformance._enabled_app_ids(conformance.gateway_config_text())
        manifests, _errs = conformance.load_manifests()
        assert ids and ids & {m.id for m in manifests}, ids


# =============================================================================
# RC034 - every FastAPI app a rail builds turns off its schema and docs
# =============================================================================

class TestRc034DocsOff:
    """FastAPI registers /openapi.json, /docs and /redoc outside every route's dependencies.
    Two rails left them on while every other had closed them, and nothing checked it."""

    def _rail(self, conformance, tmp_path, src, name="app.py"):
        m = manifest(conformance, tmp_path, "dd", {"id": "dd", "package": "dd_app"})
        pkg = m.root / "backend" / "dd_app"
        pkg.mkdir(parents=True, exist_ok=True)
        (pkg / name).write_text("from fastapi import FastAPI\nimport fastapi\n" + src,
                                encoding="utf-8")
        return m

    def test_an_app_with_no_keywords_fails_on_both_naming_the_line(self, conformance, tmp_path):
        out = conformance.rc034(self._rail(conformance, tmp_path, 'app = FastAPI(title="d")\n'), [])
        assert [f.level for f in out] == ["fail", "fail"], out
        assert "openapi_url" in out[0].message and "docs_url" in out[1].message
        assert {f.where for f in out} == {"%s:3" % conformance.rel(
            tmp_path / "rails" / "dd" / "backend" / "dd_app" / "app.py")}

    def test_only_openapi_url_off_still_fails_on_docs_url(self, conformance, tmp_path):
        out = conformance.rc034(self._rail(conformance, tmp_path,
                                           "app = FastAPI(openapi_url=None)\n"), [])
        assert [(f.level, "docs_url" in f.message) for f in out] == [("fail", True)], out

    def test_only_docs_url_off_still_fails_on_openapi_url(self, conformance, tmp_path):
        out = conformance.rc034(self._rail(conformance, tmp_path,
                                           "app = FastAPI(docs_url=None)\n"), [])
        assert [(f.level, "openapi_url" in f.message) for f in out] == [("fail", True)], out

    def test_both_off_passes_and_redoc_url_is_not_required(self, conformance, tmp_path):
        for src in ("app = FastAPI(docs_url=None, openapi_url=None)\n",
                    "app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, title='d')\n",
                    "app = fastapi.FastAPI(openapi_url=None, docs_url=None)\n"):
            assert conformance.rc034(self._rail(conformance, tmp_path, src), []) == [], src

    def test_a_path_string_fails(self, conformance, tmp_path):
        out = conformance.rc034(self._rail(conformance, tmp_path,
                                           "app = FastAPI(docs_url=None, "
                                           "openapi_url='/api/openapi.json')\n"), [])
        assert [f.level for f in out] == ["fail"] and "'/api/openapi.json'" in out[0].message

    @pytest.mark.parametrize("src,key", [
        ("app = FastAPI(docs_url=None, openapi_url=URL)\n", "openapi_url"),
        ("app = FastAPI(docs_url=pick(), openapi_url=None)\n", "docs_url"),
        ("app = FastAPI(**opts)\n", "openapi_url"),
    ], ids=["a-name", "a-call", "kwargs"])
    def test_a_value_it_cannot_read_warns_never_passes(self, conformance, tmp_path, src, key):
        out = conformance.rc034(self._rail(conformance, tmp_path, src), [])
        assert out and {f.level for f in out} == {"warn"}, out
        assert any(key in f.message and "cannot read" in f.message for f in out), out

    def test_kwargs_beside_the_two_keywords_by_name_passes(self, conformance, tmp_path):
        src = "app = FastAPI(docs_url=None, openapi_url=None, **opts)\n"
        assert conformance.rc034(self._rail(conformance, tmp_path, src), []) == []

    def test_the_attribute_form_is_judged_like_the_bare_name(self, conformance, tmp_path):
        out = conformance.rc034(self._rail(conformance, tmp_path,
                                           "app = fastapi.FastAPI(docs_url=None)\n"), [])
        assert [f.level for f in out] == ["fail"] and "openapi_url" in out[0].message

    def test_each_app_is_judged_on_its_own(self, conformance, tmp_path):
        """openmaic builds two apps. A rule that stopped at the first closed one, or read the
        file as a whole, would pass a second app left open."""
        src = ("app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)\n"
               "shim = FastAPI(title='shim')\n")
        out = conformance.rc034(self._rail(conformance, tmp_path, src), [])
        assert [f.level for f in out] == ["fail", "fail"], out
        assert {f.where.rsplit(":", 1)[1] for f in out} == {"4"}

    def test_an_app_in_a_second_module_is_found(self, conformance, tmp_path):
        m = self._rail(conformance, tmp_path, "app = FastAPI(docs_url=None, openapi_url=None)\n")
        (m.root / "backend" / "dd_app" / "llm.py").write_text(
            "from fastapi import FastAPI\nshim = FastAPI(openapi_url=None)\n", encoding="utf-8")
        out = conformance.rc034(m, [])
        assert [(f.level, f.where.endswith("llm.py:2")) for f in out] == [("fail", True)], out

    def test_no_fastapi_app_is_a_skip_not_a_pass(self, conformance, tmp_path):
        out = conformance.rc034(self._rail(conformance, tmp_path, "def make():\n    return 1\n"), [])
        assert [f.level for f in out] == ["skip"] and "no FastAPI app" in out[0].message

    def test_an_unparseable_module_warns(self, conformance, tmp_path):
        out = conformance.rc034(self._rail(conformance, tmp_path, "def broken(:\n"), [])
        assert [f.level for f in out] == ["warn"] and "cannot read" in out[0].message

    def test_every_rail_in_this_tree_builds_an_app_the_rule_reads(self, conformance):
        """Witness on the tree the rule exists for: every rail here builds a FastAPI app and
        closes it. An empty result is a read app, never the skip, which is a finding of its
        own, so this also fails if the rule stops finding apps at all."""
        manifests, _errs = conformance.load_manifests()
        assert manifests
        assert {m.id: conformance.rc034(m, []) for m in manifests} == {
            m.id: [] for m in manifests}
