"""tools/rail_conformance.py - the parsing helpers underneath the 27 rules.

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


# =============================================================================
# Shared definitions
# =============================================================================

def test_the_checker_and_the_generator_share_one_template_module(conformance):
    """'The template generator and this checker share one definition of what invariant
    means, so they cannot disagree about it.'"""
    mod = conformance._rail_template()
    assert mod.FACADE == ("roles", "models", "status")
    assert hasattr(mod, "invariants") and hasattr(mod, "render")


def test_chip_states_are_the_four_shared_names(conformance):
    """'A rail that computes a different set is not slightly different, it is lying to an
    operator who has learned what the colours mean everywhere else.'"""
    assert conformance.CHIP_STATES == ("missing", "cold", "warming", "loaded")


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


def test_unmanifested_rails_ignores_an_empty_scaffold(conformance, tmp_path, monkeypatch):
    """'rails/bouquet/ is a gitignored local scaffold: every subdirectory, zero files.
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

    The rule's question is now comparative, so these three cases are the whole contract.
    """

    def _rails(self, conformance, tmp_path, monkeypatch, built: list[str], names=("alpha", "beta")):
        """Two rails; `built` names the ones whose dist directory exists."""
        mans = []
        for n in names:
            d = tmp_path / "dists" / n
            if n in built:
                d.mkdir(parents=True, exist_ok=True)
            mans.append(manifest(conformance, tmp_path, n, {"id": n}))
        monkeypatch.setattr(
            conformance, "gateway_field_defaults",
            lambda: ({f"{n}_dist": str(tmp_path / "dists" / n) for n in names}, ""))
        return mans

    def test_an_unbuilt_tree_SKIPS_rather_than_failing_every_rail(
            self, conformance, tmp_path, monkeypatch):
        """The regression that red-lit CI. Nothing is built, so nothing rotted."""
        mans = self._rails(conformance, tmp_path, monkeypatch, built=[])
        for m in mans:
            out = conformance.rc016(m, mans)
            assert len(out) == 1, out
            assert out[0].level == "skip", out[0].level
            assert "unbuilt" in out[0].message

    def test_a_rail_missing_its_dist_while_a_SIBLING_is_built_still_FAILS(
            self, conformance, tmp_path, monkeypatch):
        """The defect RC016 was written for: five defaults pointing at pre-monorepo paths
        while the rest resolved. Narrowing the rule must not cost this."""
        mans = self._rails(conformance, tmp_path, monkeypatch, built=["alpha"])
        rotted = [f for f in conformance.rc016(mans[1], mans) if f.level == "fail"]
        assert len(rotted) == 1, rotted
        assert "beta_dist" in rotted[0].message
        assert "OTHER rails in this tree are built" in rotted[0].message

    def test_a_fully_built_tree_is_clean(self, conformance, tmp_path, monkeypatch):
        mans = self._rails(conformance, tmp_path, monkeypatch, built=["alpha", "beta"])
        for m in mans:
            assert conformance.rc016(m, mans) == []
