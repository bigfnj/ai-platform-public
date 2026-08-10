"""Seed-icon hydration: a fresh/empty volume unpacks the bundled icon archive, so a clean
install ships recipes WITH icons and needs no image GPU. Offline (uses a tiny fake archive)."""
from __future__ import annotations

import io
import tarfile

from recipe_book import config, seed


def _make_archive(path, names):
    with tarfile.open(path, "w:gz") as tar:
        for nm in names:
            data = b"\x89PNG\r\n\x1a\n" + b"x" * 16
            ti = tarfile.TarInfo(name=nm)
            ti.size = len(data)
            tar.addfile(ti, io.BytesIO(data))


def test_unpacks_when_icons_dir_empty(tmp_path, monkeypatch):
    icons = tmp_path / "icons"
    arc = tmp_path / "icons.tgz"
    _make_archive(arc, ["aaa.png", "bbb.png", "sub/ccc.png", "notes.txt"])
    monkeypatch.setattr(config, "ICONS_DIR", icons)
    monkeypatch.setattr(config, "SEED_ICONS_ARCHIVE", arc)

    assert seed.hydrate_icons() == 3                 # 3 pngs; the .txt is skipped
    assert (icons / "aaa.png").exists()
    assert (icons / "ccc.png").exists()              # nested member flattened to basename (no traversal)
    assert not (icons / "notes.txt").exists()


def test_skips_when_icons_already_present(tmp_path, monkeypatch):
    icons = tmp_path / "icons"
    icons.mkdir()
    (icons / "existing.png").write_bytes(b"x")
    arc = tmp_path / "icons.tgz"
    _make_archive(arc, ["aaa.png"])
    monkeypatch.setattr(config, "ICONS_DIR", icons)
    monkeypatch.setattr(config, "SEED_ICONS_ARCHIVE", arc)

    assert seed.hydrate_icons() == 0                 # real install: untouched
    assert not (icons / "aaa.png").exists()


def test_noop_without_archive(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ICONS_DIR", tmp_path / "icons")
    monkeypatch.setattr(config, "SEED_ICONS_ARCHIVE", tmp_path / "nope.tgz")
    assert seed.hydrate_icons() == 0
