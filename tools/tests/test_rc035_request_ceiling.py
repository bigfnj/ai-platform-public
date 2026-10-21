"""RC035: a rail's request-body ceiling is a whole number of bytes in range, mirrored exactly.

The gateway streams every proxied body and refuses one past the rail's ceiling with a 413, so
`max_request_bytes` is a door every legitimate upload must fit through. The gateway container
cannot see rails/, so what it applies is its own copy, config.MAX_REQUEST_BYTES; a figure raised
in the manifest and not there still refuses the upload it was raised for. These pin each
finding on synthetic manifests with a stand-in mirror, then the real tree.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

MIB = 1024 * 1024


def _manifest(conformance, tmp_path: Path, data: dict):
    d = tmp_path / "rails" / data["id"]
    d.mkdir(parents=True, exist_ok=True)
    p = d / "rail.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return conformance.Manifest(path=p, data=data)


@pytest.fixture()
def mirror(conformance, monkeypatch):
    """set(mapping) makes `mapping` the gateway's MAX_REQUEST_BYTES for one test; None means the
    literal could not be read."""
    def set_(value):
        monkeypatch.setattr(conformance, "gateway_max_request_bytes", lambda: value)
    return set_


def _levels(out) -> list[str]:
    return [f.level for f in out]


def test_a_figure_the_mirror_agrees_with_passes(conformance, tmp_path, mirror):
    mirror({"cc": 12 * MIB})
    m = _manifest(conformance, tmp_path, {"id": "cc", "max_request_bytes": 12 * MIB})
    assert conformance.rc035(m, [m]) == []


def test_no_figure_and_no_mirror_entry_is_a_skip(conformance, tmp_path, mirror):
    mirror({"other": 12 * MIB})
    m = _manifest(conformance, tmp_path, {"id": "cc"})
    out = conformance.rc035(m, [m])
    assert _levels(out) == ["skip"] and "platform default" in out[0].message


@pytest.mark.parametrize("bad", ["25MB", 25.5, True, [25]], ids=["str", "float", "bool", "list"])
def test_a_figure_that_is_not_a_whole_number_fails(conformance, tmp_path, mirror, bad):
    mirror({"cc": bad})
    m = _manifest(conformance, tmp_path, {"id": "cc", "max_request_bytes": bad})
    out = conformance.rc035(m, [m])
    assert _levels(out) == ["fail"] and "whole number" in out[0].message, out


@pytest.mark.parametrize("figure", [25, MIB - 1, 1024 * MIB + 1], ids=["mb-unit", "low", "high"])
def test_a_figure_out_of_range_fails(conformance, tmp_path, mirror, figure):
    mirror({"cc": figure})
    m = _manifest(conformance, tmp_path, {"id": "cc", "max_request_bytes": figure})
    out = conformance.rc035(m, [m])
    assert _levels(out) == ["fail"] and "outside" in out[0].message, out


@pytest.mark.parametrize("figure", [MIB, 1024 * MIB], ids=["floor", "ceiling"])
def test_the_range_includes_both_ends(conformance, tmp_path, mirror, figure):
    mirror({"cc": figure})
    m = _manifest(conformance, tmp_path, {"id": "cc", "max_request_bytes": figure})
    assert conformance.rc035(m, [m]) == []


def test_a_mirror_that_disagrees_fails_naming_both_figures(conformance, tmp_path, mirror):
    mirror({"cc": 26 * MIB})
    m = _manifest(conformance, tmp_path, {"id": "cc", "max_request_bytes": 256 * MIB})
    out = conformance.rc035(m, [m])
    assert _levels(out) == ["fail"], out
    assert str(26 * MIB) in out[0].message and str(256 * MIB) in out[0].message
    assert out[0].where.endswith("platform_gateway_app/config.py")


def test_a_declared_figure_with_no_mirror_entry_fails(conformance, tmp_path, mirror):
    mirror({})
    m = _manifest(conformance, tmp_path, {"id": "cc", "max_request_bytes": 12 * MIB})
    out = conformance.rc035(m, [m])
    assert _levels(out) == ["fail"] and "no entry" in out[0].message, out


def test_a_stale_mirror_entry_for_a_rail_that_declares_none_fails(conformance, tmp_path, mirror):
    mirror({"cc": 12 * MIB})
    m = _manifest(conformance, tmp_path, {"id": "cc"})
    out = conformance.rc035(m, [m])
    assert _levels(out) == ["fail"] and "declares none" in out[0].message, out


def test_every_also_serves_tile_is_held_to_the_same_figure(conformance, tmp_path, mirror):
    """One image backing two tiles gets one figure: the relay looks the ceiling up by the tile
    the request names, so a tile the mirror misses falls to the default unseen."""
    mirror({"cc": 256 * MIB})
    m = _manifest(conformance, tmp_path,
                  {"id": "cc", "also_serves": ["cc-two"], "max_request_bytes": 256 * MIB})
    out = conformance.rc035(m, [m])
    assert _levels(out) == ["fail"] and "'cc-two'" in out[0].message, out
    mirror({"cc": 256 * MIB, "cc-two": 256 * MIB})
    assert conformance.rc035(m, [m]) == []


def test_a_tile_held_to_the_default_has_no_mirror_entry(conformance, tmp_path, mirror):
    """also_serves_default_ceiling: the tile takes the platform default, not the rail's figure,
    as edu-suite's iep tile does (one 25 MiB PDF, where edu-suite takes a 250 MiB folder). A
    mirror entry for it would hold it to the figure the manifest says it does not take."""
    m = _manifest(conformance, tmp_path,
                  {"id": "cc", "also_serves": ["cc-two"], "max_request_bytes": 256 * MIB,
                   "also_serves_default_ceiling": ["cc-two"]})
    mirror({"cc": 256 * MIB, "cc-two": 256 * MIB})
    out = conformance.rc035(m, [m])
    assert _levels(out) == ["fail"], out
    assert "'cc-two'" in out[0].message and "platform default" in out[0].message, out
    mirror({"cc": 256 * MIB})
    assert conformance.rc035(m, [m]) == []


@pytest.mark.parametrize("named", [["cc"], ["cc-three"], "cc-two"],
                         ids=["own-id", "not-served", "not-a-list"])
def test_a_default_ceiling_that_names_no_served_tile_fails(conformance, tmp_path, mirror,
                                                           named):
    """The rail's own id, a tile it does not serve, or a bare string holds nothing: the rail's
    figure still applies to every tile, which is not what whoever wrote the key meant."""
    m = _manifest(conformance, tmp_path,
                  {"id": "cc", "also_serves": ["cc-two"], "max_request_bytes": 256 * MIB,
                   "also_serves_default_ceiling": named})
    mirror({"cc": 256 * MIB, "cc-two": 256 * MIB})
    out = conformance.rc035(m, [m])
    assert _levels(out) == ["fail"], out
    assert "also_serves_default_ceiling" in out[0].message, out


def test_an_unreadable_mirror_warns_never_passes(conformance, tmp_path, mirror):
    mirror(None)
    m = _manifest(conformance, tmp_path, {"id": "cc", "max_request_bytes": 12 * MIB})
    out = conformance.rc035(m, [m])
    assert _levels(out) == ["warn"] and "could not read" in out[0].message, out


def test_the_real_mirror_is_read(conformance):
    """The reader finds the real literal: a None here would turn every rail's check into a
    warning, and a {} into a stream of 'no entry' failures."""
    got = conformance.gateway_max_request_bytes()
    assert isinstance(got, dict) and got, got
    assert all(isinstance(v, int) and not isinstance(v, bool) for v in got.values()), got


def test_the_real_tree_has_no_rc035_finding_and_some_rail_declares(conformance):
    manifests = [conformance.Manifest(path=p, data=json.loads(p.read_text(encoding="utf-8")))
                 for p in sorted(conformance.RAILS.glob("*/rail.json"))]
    out = [f for m in manifests for f in conformance.rc035(m, manifests)]
    assert [f.line() for f in out if f.level != "skip"] == []
    assert any("max_request_bytes" in m.data for m in manifests), "RC035 checked no rail"
