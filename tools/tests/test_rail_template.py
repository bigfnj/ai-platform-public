"""tools/rail_template.py - the generator that is also the enforcement.

`check` regenerates each tier-1 file and diffs it against disk, so the derivations tested here
(where the package lives, which import line modelstate.py gets, which files count as
invariant) are the same ones that decide whether a rail is called drifted. A wrong derivation
does not fail loudly; it checks the wrong file, or no file, and reports a pass.

Every test builds a synthetic rail under tmp_path. Nothing writes into rails/.
"""
from __future__ import annotations

import argparse
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


class TestChipRailsCarryModelstate:
    """A rail whose manifest promises chips must carry the generated resolver.

    The file's EXISTENCE used to decide whether a rail had one, so a chip rail with no copy read
    as "does no model work": check counted nothing, sync wrote nothing, RC022 and RC023 skipped
    it. One rail lived in that hole with a hand-rolled resolver whose every chip said `cold`.
    The manifest decides now, and absence is a finding.
    """
    CHIPS = {"status_route": "/api/capabilities",
             "model_slots": [{"slot": "writer", "kind": "chat", "role": "writer"}]}

    def _rail(self, template, tmp_path, monkeypatch=None, **data) -> object:
        r = make_rail(tmp_path, "x", {"id": "x", "package": "xapp", **data}, template)
        r.package_dir.mkdir(parents=True, exist_ok=True)
        if monkeypatch is not None:
            monkeypatch.setattr(template, "RAILS", tmp_path / "rails")
            monkeypatch.setattr(template, "REPO", tmp_path)
        return r

    def test_a_chip_rail_with_no_copy_still_has_its_path(self, template, tmp_path):
        r = self._rail(template, tmp_path, **self.CHIPS)
        assert r.renders_chips()
        assert r.modelstate_path() == r.package_dir / "modelstate.py"
        assert ("modelstate.py", r.package_dir / "modelstate.py") in template.invariants(r)

    def test_slots_with_no_status_route_render_no_chips(self, template, tmp_path):
        r = self._rail(template, tmp_path, status_route=None,
                       model_slots=self.CHIPS["model_slots"])
        assert not r.renders_chips()
        assert r.modelstate_path() is None

    def test_an_image_only_rail_renders_no_chips(self, template, tmp_path):
        """RC007's exemption: an image backend never appears in Ollama's tag list, so a chip
        for it could only read missing, and no chip is drawn."""
        r = self._rail(template, tmp_path, status_route="/api/capabilities",
                       model_slots=[{"slot": "icon", "kind": "image", "role": "image"}])
        assert not r.renders_chips()
        assert r.modelstate_path() is None

    def test_a_status_route_with_no_slots_renders_no_chips(self, template, tmp_path):
        r = self._rail(template, tmp_path, status_route="/api/healthz", model_slots=[])
        assert not r.renders_chips()

    def test_check_names_the_missing_copy_and_fails(self, template, tmp_path, monkeypatch,
                                                     capsys):
        self._rail(template, tmp_path, monkeypatch, **self.CHIPS)
        assert template.cmd_check(argparse.Namespace(rail=None, diff=False)) == 1
        out = capsys.readouterr().out
        assert "MISSING" in out and "rails/x/backend/xapp/modelstate.py" in out

    def test_sync_creates_the_copy_and_check_then_passes(self, template, tmp_path,
                                                         monkeypatch):
        r = self._rail(template, tmp_path, monkeypatch, **self.CHIPS)
        assert template.cmd_sync(argparse.Namespace(rail=None)) == 0
        dest = r.package_dir / "modelstate.py"
        assert dest.read_text(encoding="utf-8") == template.render("modelstate.py", r)
        assert template.cmd_check(argparse.Namespace(rail=None, diff=False)) == 0

    def test_sync_does_not_invent_a_package_directory(self, template, tmp_path, monkeypatch):
        """A missing package dir means the manifest points at the wrong place; creating one
        there would make the rail look fixed while its real code sat elsewhere, unchecked."""
        r = make_rail(tmp_path, "x", {"id": "x", "package": "wrong", **self.CHIPS}, template)
        monkeypatch.setattr(template, "RAILS", tmp_path / "rails")
        monkeypatch.setattr(template, "REPO", tmp_path)
        assert template.cmd_sync(argparse.Namespace(rail=None)) == 1
        assert not r.package_dir.exists()

    def test_a_chip_rail_with_no_copy_still_owes_the_facade(self, template, tmp_path):
        """RC022 reaches the same derivation: the rail must expose the surface the resolver it
        is about to be given reads."""
        r = self._rail(template, tmp_path, **self.CHIPS)
        assert template.facade_gaps(r) == ["<no broker facade found>"]


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


# --- --rail must name something -----------------------------------------------------------

def _two_rails(template, tmp_path, monkeypatch):
    for d, rid in (("iep", "iep-goals"), ("plain", "plain")):
        (tmp_path / d).mkdir()
        (tmp_path / d / "rail.json").write_text(json.dumps({"id": rid}), encoding="utf-8")
    monkeypatch.setattr(template, "RAILS", tmp_path)


@pytest.mark.parametrize("cmd", ["check", "sync"])
def test_an_unknown_rail_is_an_error_not_0_of_0(template, tmp_path, monkeypatch, capsys, cmd):
    """It used to print `0/0 invariant file(s) match the template` and exit 0."""
    _two_rails(template, tmp_path, monkeypatch)
    ns = argparse.Namespace(rail="no-such-rail", diff=False)
    fn = template.cmd_check if cmd == "check" else template.cmd_sync
    assert fn(ns) == 2
    out = capsys.readouterr().out
    assert "no rail 'no-such-rail'" in out and "iep-goals" in out


@pytest.mark.parametrize("name", ["iep-goals", "iep"])
def test_a_rail_is_found_by_catalog_id_or_directory(template, tmp_path, monkeypatch, name):
    """iep-goals' directory is `iep`: `--rail iep` used to match nothing, silently."""
    _two_rails(template, tmp_path, monkeypatch)
    assert [r.id for r in template.selected(name)] == ["iep-goals"]


def test_no_rail_means_every_rail(template, tmp_path, monkeypatch):
    _two_rails(template, tmp_path, monkeypatch)
    assert [r.id for r in template.selected(None)] == ["iep-goals", "plain"]


def test_check_reports_coverage_per_template_and_names_uncovered_rails(template, tmp_path,
                                                                       monkeypatch, capsys):
    """Record, 2026-09-11: `check` printed 17/17 while the byte-identical guarantee covered 6
    rails and skipped 8, with no coverage number either way. A green match count over rails that
    carry no invariant file now says so."""
    _two_rails(template, tmp_path, monkeypatch)
    assert template.cmd_check(argparse.Namespace(rail=None, diff=False)) == 0
    out = capsys.readouterr().out
    assert ("coverage over 2 rail(s): identity.py 0, modelstate.py 0, logsetup.py 0; "
            "no invariant file at all: iep-goals, plain") in out
