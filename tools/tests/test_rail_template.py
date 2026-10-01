"""tools/rail_template.py - the generator that is also the enforcement.

`check` regenerates each tier-1 file and diffs it against disk, so the derivations tested here
(where the package lives, which import line modelstate.py gets, which files count as
invariant) are the same ones that decide whether a rail is called drifted. A wrong derivation
does not fail loudly; it checks the wrong file, or no file, and reports a pass.

Every test builds a synthetic rail under tmp_path. Nothing writes into rails/.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1]


def make_rail(root: Path, name: str, data: dict, template_mod) -> object:
    """A Rail backed by a real manifest on disk under tmp_path."""
    d = root / "rails" / name
    d.mkdir(parents=True, exist_ok=True)
    man = d / "rail.json"
    man.write_text(json.dumps(data), encoding="utf-8")
    return template_mod.Rail(man)


# =============================================================================
# Rail - the manifest-derived paths
# =============================================================================

class TestRailIdentity:
    def test_id_comes_from_the_manifest(self, template, tmp_path):
        r = make_rail(tmp_path, "iep", {"id": "iep-goals"}, template)
        assert r.id == "iep-goals"

    def test_id_falls_back_to_the_directory_name(self, template, tmp_path):
        r = make_rail(tmp_path, "bouquet", {}, template)
        assert r.id == "bouquet"

    def test_root_is_the_manifest_directory(self, template, tmp_path):
        r = make_rail(tmp_path, "x", {"id": "x"}, template)
        assert r.root == tmp_path / "rails" / "x"


class TestPackageDir:
    def test_src_layout(self, template, tmp_path):
        r = make_rail(tmp_path, "x", {"id": "x", "package": "xapp", "layout": "src"}, template)
        assert r.package_dir == tmp_path / "rails" / "x" / "src" / "xapp"

    def test_backend_layout_is_the_default(self, template, tmp_path):
        r = make_rail(tmp_path, "x", {"id": "x", "package": "xapp"}, template)
        assert r.package_dir == tmp_path / "rails" / "x" / "backend" / "xapp"

    def test_any_layout_other_than_src_means_backend(self, template, tmp_path):
        r = make_rail(tmp_path, "x", {"id": "x", "package": "xapp", "layout": "backend"},
                      template)
        assert r.package_dir == tmp_path / "rails" / "x" / "backend" / "xapp"

    def test_package_path_overrides_both_layouts(self, template, tmp_path):
        """edu-suite is a multi-app rail whose dashboard backend is at
        apps/dashboard/src/dashboard. Guessing wrong means every source rule checks nothing."""
        r = make_rail(tmp_path, "edu-suite",
                      {"id": "edu-suite", "package": "dashboard", "layout": "src",
                       "package_path": "apps/dashboard/src/dashboard"}, template)
        assert r.package_dir == tmp_path / "rails" / "edu-suite" / "apps/dashboard/src/dashboard"


class TestBrokerImport:
    """The one line that legitimately varies inside the generated modelstate.py."""

    def test_default_is_the_rails_own_broker(self, template, tmp_path):
        r = make_rail(tmp_path, "x", {"id": "x"}, template)
        assert r.broker_import == "from . import broker"

    def test_empty_string_is_the_default_too(self, template, tmp_path):
        r = make_rail(tmp_path, "x", {"id": "x", "broker_module": "   "}, template)
        assert r.broker_import == "from . import broker"

    def test_a_dotted_module_reaches_a_platform_package(self, template, tmp_path):
        """edu-suite reaches packages/edu-media-core rather than a local broker.py, which is
        why this is derived and not hardcoded."""
        r = make_rail(tmp_path, "edu-suite",
                      {"id": "edu-suite", "broker_module": "edu_media_core.broker_media"},
                      template)
        assert r.broker_import == "from edu_media_core import broker_media as broker"

    def test_a_deeply_dotted_module_splits_at_the_last_dot(self, template, tmp_path):
        r = make_rail(tmp_path, "x", {"id": "x", "broker_module": "a.b.c.mod"}, template)
        assert r.broker_import == "from a.b.c import mod as broker"

    def test_a_local_alias_is_aliased_to_broker(self, template, tmp_path):
        r = make_rail(tmp_path, "x", {"id": "x", "broker_module": "broker_media"}, template)
        assert r.broker_import == "from . import broker_media as broker"

    def test_whitespace_is_stripped(self, template, tmp_path):
        r = make_rail(tmp_path, "x", {"id": "x", "broker_module": "  broker_media  "},
                      template)
        assert r.broker_import == "from . import broker_media as broker"

    def test_the_import_line_is_valid_python(self, template, tmp_path):
        for mod in ("", "broker_media", "edu_media_core.broker_media", "a.b.c.mod"):
            r = make_rail(tmp_path, "x", {"id": "x", "broker_module": mod}, template)
            compile(r.broker_import, "<import>", "exec")


class TestInvariantPaths:
    def _pkg(self, template, tmp_path, **extra) -> object:
        r = make_rail(tmp_path, "x", {"id": "x", "package": "xapp", "layout": "src", **extra},
                      template)
        r.package_dir.mkdir(parents=True, exist_ok=True)
        return r

    def test_identity_in_the_api_subpackage_wins(self, template, tmp_path):
        """'Rails with an api/ subpackage keep it there.' Both may exist on disk; the api/
        copy is the one the generator owns."""
        r = self._pkg(template, tmp_path)
        (r.package_dir / "api").mkdir()
        (r.package_dir / "api" / "identity.py").write_text("", encoding="utf-8")
        (r.package_dir / "identity.py").write_text("", encoding="utf-8")
        assert r.identity_path() == r.package_dir / "api" / "identity.py"

    def test_identity_beside_the_package_is_the_fallback(self, template, tmp_path):
        r = self._pkg(template, tmp_path)
        (r.package_dir / "identity.py").write_text("", encoding="utf-8")
        assert r.identity_path() == r.package_dir / "identity.py"

    def test_no_identity_file_is_none_not_a_guess(self, template, tmp_path):
        assert self._pkg(template, tmp_path).identity_path() is None

    def test_modelstate_path(self, template, tmp_path):
        r = self._pkg(template, tmp_path)
        assert r.modelstate_path() is None
        (r.package_dir / "modelstate.py").write_text("", encoding="utf-8")
        assert r.modelstate_path() == r.package_dir / "modelstate.py"

    def test_invariants_lists_only_files_that_exist(self, template, tmp_path):
        r = self._pkg(template, tmp_path)
        assert template.invariants(r) == []
        (r.package_dir / "identity.py").write_text("", encoding="utf-8")
        assert [n for n, _ in template.invariants(r)] == ["identity.py"]
        (r.package_dir / "modelstate.py").write_text("", encoding="utf-8")
        assert [n for n, _ in template.invariants(r)] == ["identity.py", "modelstate.py"]

    def test_a_rail_with_no_invariants_is_checked_zero_times(self, template, tmp_path):
        """Consequence worth pinning: `check` counts what it finds, so a rail whose package
        dir is wrong contributes nothing and cannot drift."""
        r = make_rail(tmp_path, "x", {"id": "x", "package": "wrong"}, template)
        assert template.invariants(r) == []


# =============================================================================
# render() - byte-identical means newline-identical
# =============================================================================

class TestRender:
    def test_the_broker_import_placeholder_is_filled(self, template, tmp_path, monkeypatch):
        tmpl = tmp_path / "t"
        tmpl.mkdir()
        (tmpl / "modelstate.py.tmpl").write_text("head\n{{BROKER_IMPORT}}\ntail\n",
                                                 encoding="utf-8")
        monkeypatch.setattr(template, "TEMPLATES", tmpl)
        r = make_rail(tmp_path, "x", {"id": "x", "broker_module": "edu_media_core.broker_media"},
                      template)
        assert template.render("modelstate.py", r) == \
            "head\nfrom edu_media_core import broker_media as broker\ntail\n"

    def test_crlf_is_normalised_to_lf(self, template, tmp_path, monkeypatch):
        """'The only difference between two identity.py copies was CRLF vs LF, which no
        reader would ever see.' The normalisation IS the fix for that."""
        tmpl = tmp_path / "t"
        tmpl.mkdir()
        (tmpl / "identity.py.tmpl").write_bytes(b"a\r\nb\r\n")
        monkeypatch.setattr(template, "TEMPLATES", tmpl)
        r = make_rail(tmp_path, "x", {"id": "x"}, template)
        out = template.render("identity.py", r)
        assert out == "a\nb\n"
        assert "\r" not in out

    def test_a_template_with_no_placeholder_is_passed_through(self, template, tmp_path,
                                                              monkeypatch):
        tmpl = tmp_path / "t"
        tmpl.mkdir()
        (tmpl / "identity.py.tmpl").write_text("no placeholder here\n", encoding="utf-8")
        monkeypatch.setattr(template, "TEMPLATES", tmpl)
        r = make_rail(tmp_path, "x", {"id": "x"}, template)
        assert template.render("identity.py", r) == "no placeholder here\n"


class TestShippedTemplates:
    def test_both_tier_one_templates_exist(self, template):
        for name in ("identity.py", "modelstate.py"):
            assert (template.TEMPLATES / f"{name}.tmpl").is_file()

    def test_only_modelstate_carries_the_broker_placeholder(self, template):
        ident = (template.TEMPLATES / "identity.py.tmpl").read_text(encoding="utf-8")
        model = (template.TEMPLATES / "modelstate.py.tmpl").read_text(encoding="utf-8")
        assert "{{BROKER_IMPORT}}" not in ident
        assert "{{BROKER_IMPORT}}" in model

    @pytest.mark.parametrize("name", ["identity.py", "modelstate.py"])
    def test_the_rendered_template_is_valid_python(self, template, tmp_path, name):
        """A template that does not compile scaffolds a broken rail and, worse, `sync`
        would write it over every existing copy."""
        r = make_rail(tmp_path, "x", {"id": "x"}, template)
        compile(template.render(name, r), name, "exec")

    def test_no_unfilled_placeholder_survives_rendering(self, template, tmp_path):
        r = make_rail(tmp_path, "x", {"id": "x"}, template)
        for name in ("identity.py", "modelstate.py"):
            assert "{{" not in template.render(name, r)


# =============================================================================
# facade_gaps() - the surface modelstate.py is generated against
# =============================================================================

class TestFacade:
    FULL = ("class BrokerError(Exception):\n    pass\n\n"
            "def roles():\n    return []\n\n"
            "def models():\n    return []\n\n"
            "def status():\n    return {}\n")

    def _rail_with_facade(self, template, tmp_path, src: str | None,
                          modelstate: bool = True, **extra) -> object:
        r = make_rail(tmp_path, "x", {"id": "x", "package": "xapp", "layout": "src", **extra},
                      template)
        r.package_dir.mkdir(parents=True, exist_ok=True)
        if modelstate:
            (r.package_dir / "modelstate.py").write_text("", encoding="utf-8")
        if src is not None:
            (r.package_dir / "broker.py").write_text(src, encoding="utf-8")
        return r

    def test_a_complete_facade_has_no_gaps(self, template, tmp_path):
        r = self._rail_with_facade(template, tmp_path, self.FULL)
        assert template.facade_gaps(r) == []

    def test_a_rail_with_no_modelstate_is_exempt(self, template, tmp_path):
        """'meeting-atlas and workstation are the real cases: no modelstate, nothing to
        resolve.' Exempt, not silently passing something it never looked at."""
        r = self._rail_with_facade(template, tmp_path, None, modelstate=False)
        assert template.facade_gaps(r) == []

    def test_a_missing_facade_module_is_reported(self, template, tmp_path):
        r = self._rail_with_facade(template, tmp_path, None)
        assert template.facade_gaps(r) == ["<no broker facade found>"]

    @pytest.mark.parametrize("drop", ["roles", "models", "status"])
    def test_each_missing_function_is_named(self, template, tmp_path, drop):
        src = "\n".join(b for b in self.FULL.split("\n\n") if f"def {drop}(" not in b)
        r = self._rail_with_facade(template, tmp_path, src)
        assert template.facade_gaps(r) == [drop]

    def test_a_missing_error_class_is_named(self, template, tmp_path):
        src = "\n".join(b for b in self.FULL.split("\n\n") if "BrokerError" not in b)
        r = self._rail_with_facade(template, tmp_path, src)
        assert template.facade_gaps(r) == ["BrokerError"]

    def test_an_imported_name_counts(self, template, tmp_path):
        """'iep re-exports BrokerError from platform_core rather than defining its own,
        which is correct - the surface is what matters, not its origin.'"""
        src = ("from platform_core.broker import BrokerError\n"
               "def roles():\n    return []\n"
               "def models():\n    return []\n"
               "def status():\n    return {}\n")
        r = self._rail_with_facade(template, tmp_path, src)
        assert template.facade_gaps(r) == []

    def test_an_aliased_import_counts_under_its_alias(self, template, tmp_path):
        src = ("from x import Boom as BrokerError\n"
               "from y import roles, models, status\n")
        r = self._rail_with_facade(template, tmp_path, src)
        assert template.facade_gaps(r) == []

    def test_an_async_function_counts(self, template, tmp_path):
        src = ("class BrokerError(Exception): pass\n"
               "async def roles(): return []\n"
               "async def models(): return []\n"
               "async def status(): return {}\n")
        r = self._rail_with_facade(template, tmp_path, src)
        assert template.facade_gaps(r) == []

    def test_a_module_level_assignment_counts(self, template, tmp_path):
        src = ("BrokerError = RuntimeError\n"
               "roles = lambda: []\n"
               "models = lambda: []\n"
               "status = lambda: {}\n")
        r = self._rail_with_facade(template, tmp_path, src)
        assert template.facade_gaps(r) == []

    def test_a_nested_definition_does_not_count(self, template, tmp_path):
        """Only module-level names are the surface; something defined inside a function is
        not importable by modelstate.py."""
        src = ("class BrokerError(Exception): pass\n"
               "def roles(): return []\n"
               "def models(): return []\n"
               "def _wrap():\n    def status(): return {}\n")
        r = self._rail_with_facade(template, tmp_path, src)
        assert template.facade_gaps(r) == ["status"]

    def test_gaps_are_reported_in_canonical_order(self, template, tmp_path):
        r = self._rail_with_facade(template, tmp_path, "x = 1\n")
        assert template.facade_gaps(r) == ["roles", "models", "status", "BrokerError"]

    def test_broker_module_redirects_to_a_local_alias(self, template, tmp_path):
        r = make_rail(tmp_path, "x", {"id": "x", "package": "xapp", "layout": "src",
                                      "broker_module": "broker_media"}, template)
        r.package_dir.mkdir(parents=True, exist_ok=True)
        (r.package_dir / "modelstate.py").write_text("", encoding="utf-8")
        (r.package_dir / "broker.py").write_text("x = 1\n", encoding="utf-8")
        assert template.facade_gaps(r) == ["<no broker facade found>"], \
            "broker_module must redirect away from the default broker.py"
        (r.package_dir / "broker_media.py").write_text(self.FULL, encoding="utf-8")
        assert template.facade_gaps(r) == []

    def test_a_dotted_broker_module_is_found_under_packages(self, template, tmp_path,
                                                            monkeypatch):
        pkg = tmp_path / "packages" / "edu-media-core" / "src" / "edu_media_core"
        pkg.mkdir(parents=True)
        (pkg / "broker_media.py").write_text(self.FULL, encoding="utf-8")
        monkeypatch.setattr(template, "REPO", tmp_path)
        r = make_rail(tmp_path, "edu-suite",
                      {"id": "edu-suite", "package": "dashboard", "layout": "src",
                       "broker_module": "edu_media_core.broker_media"}, template)
        r.package_dir.mkdir(parents=True, exist_ok=True)
        (r.package_dir / "modelstate.py").write_text("", encoding="utf-8")
        assert template.facade_module(r) == pkg / "broker_media.py"
        assert template.facade_gaps(r) == []

    def test_a_dotted_module_that_is_nowhere_is_reported(self, template, tmp_path,
                                                         monkeypatch):
        (tmp_path / "packages").mkdir()
        monkeypatch.setattr(template, "REPO", tmp_path)
        r = make_rail(tmp_path, "x", {"id": "x", "package": "xapp", "layout": "src",
                                      "broker_module": "nope.missing"}, template)
        r.package_dir.mkdir(parents=True, exist_ok=True)
        (r.package_dir / "modelstate.py").write_text("", encoding="utf-8")
        assert template.facade_module(r) is None
        assert template.facade_gaps(r) == ["<no broker facade found>"]


def test_the_canonical_surface_is_what_the_docs_say(template):
    """RC022 checks this same surface. If FACADE changes, the contract doc and the
    conformance rule have to move with it."""
    assert template.FACADE == ("roles", "models", "status")
    assert template.FACADE_ERROR == "BrokerError"


def test_rails_reads_manifests_in_sorted_order(template, tmp_path, monkeypatch):
    for name in ("c", "a", "b"):
        d = tmp_path / name
        d.mkdir(parents=True)
        (d / "rail.json").write_text(json.dumps({"id": name}), encoding="utf-8")
    monkeypatch.setattr(template, "RAILS", tmp_path)
    assert [r.id for r in template.rails()] == ["a", "b", "c"]


def test_rails_ignores_directories_without_a_manifest(template, tmp_path, monkeypatch):
    (tmp_path / "scaffold").mkdir()
    (tmp_path / "real").mkdir()
    (tmp_path / "real" / "rail.json").write_text(json.dumps({"id": "real"}), encoding="utf-8")
    monkeypatch.setattr(template, "RAILS", tmp_path)
    assert [r.id for r in template.rails()] == ["real"]
