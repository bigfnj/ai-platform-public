"""tools/deploy_snapshot.py - the offline parts: env parsing, body reduction, and the diff.

The diff is what gates a deployment phase, so it has to report every change and nothing else:
a missed key means a regression waved through, a spurious one means a gate people learn to skip.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("deploy_snapshot", TOOLS / "deploy_snapshot.py")
ds = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ds)


def test_read_env_skips_comments_and_strips_quotes(tmp_path):
    f = tmp_path / ".env"
    f.write_text('﻿# c\nA=1\n\nB="two words"\nC = x=y\n', encoding="utf-8")
    assert ds.read_env(f) == {"A": "1", "B": "two words", "C": "x=y"}


def test_scalars_keeps_facts_and_drops_bulk():
    body = {"chunks": 5836, "models": [1, 2], "path": "x" * 200, "deep": {"a": {"b": {"c": 1}}}}
    assert ds.scalars(body) == {"chunks": 5836, "models": "[2 items]", "path": "<str 200>",
                                "deep": {"a": {"b": "{...}"}}}


def test_diff_reports_added_removed_and_changed():
    a = {"roles": {"chat": {"resolved": None}, "old": {}}, "gateway": 200}
    b = {"roles": {"chat": {"resolved": "gemma3:4b"}, "new": {}}, "gateway": 200}
    out = ds.diff(a, b)
    assert '~ roles.chat.resolved: null -> "gemma3:4b"' in out
    assert any(c.startswith("- roles.old") for c in out)
    assert any(c.startswith("+ roles.new") for c in out)
    assert len(out) == 3


def test_identical_snapshots_diff_empty():
    s = {"rails": {"x": {"status": 200}}, "containers": {"c": {"id": "abc"}}}
    assert ds.diff(s, s) == []


def test_read_env_reads_like_compose(tmp_path):
    f = tmp_path / ".env"
    f.write_text("export A=1\nPW=hunter2  # rotate me\nQ=\"has # hash\" # c\nS='x'\n", encoding="utf-8")
    assert ds.read_env(f) == {"A": "1", "PW": "hunter2", "Q": "has # hash", "S": "x"}


def test_scalars_redacts_credential_keys_whatever_their_length():
    body = {"broker_token": "sk-live-abc", "nested": {"api_key": "k", "Password": "p", "ok": 1}}
    assert ds.scalars(body) == {"broker_token": "<redacted>",
                                "nested": {"Password": "<redacted>", "api_key": "<redacted>", "ok": 1}}


def test_main_refuses_one_file_for_out_and_diff(tmp_path, monkeypatch):
    base = tmp_path / "s.json"
    base.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(ds, "snapshot", lambda *a: {"login": 200})
    monkeypatch.setattr("sys.argv", ["x", "--env", str(tmp_path / ".env"),
                                     "--out", str(base), "--diff", str(base)])
    with pytest.raises(SystemExit) as e:
        ds.main()
    assert e.value.code == 2
    assert base.read_text(encoding="utf-8") == "{}"          # the baseline was not overwritten


def test_main_diffs_against_the_baseline_not_its_own_output(tmp_path, monkeypatch, capsys):
    """A regression this pins: with the baseline read AFTER writing, the diff compared the new
    snapshot with itself and reported 0 changes, a gate that always passed."""
    base, out = tmp_path / "before.json", tmp_path / "after.json"
    base.write_text(json.dumps({"login": 200, "rails": {}, "roles": {}, "containers": {},
                                "gateway_healthz": {"status": 200}}), encoding="utf-8")
    monkeypatch.setattr(ds, "snapshot", lambda *a: {"login": 401, "rails": {}, "roles": {},
                                                    "containers": {}, "gateway_healthz": {"status": 200}})
    monkeypatch.setattr("sys.argv", ["x", "--env", str(tmp_path / ".env"),
                                     "--out", str(out), "--diff", str(base)])
    assert ds.main() == 1
    assert "~ login: 200 -> 401" in capsys.readouterr().out


# --- status routes for also_serves tiles --------------------------------------------------
#
# A tile one image serves twice (edu-suite's second instance) has no manifest of its own. Read
# from manifest ids alone it was recorded as "no status_route", so its status was never compared.
# Synthetic rail names throughout: this file ships in the public snapshot.

def _rail(root: Path, name: str, data: dict) -> None:
    d = root / "rails" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "rail.json").write_text(json.dumps(data), encoding="utf-8")


def test_an_also_served_tile_gets_its_owners_status_route(tmp_path, monkeypatch):
    _rail(tmp_path, "alpha", {"id": "alpha", "status_route": "/api/capabilities",
                              "also_serves": ["alpha-two"]})
    _rail(tmp_path, "beta", {"id": "beta", "status_route": "/api/health"})
    monkeypatch.setattr(ds, "REPO", tmp_path)
    assert ds.status_routes() == {"alpha": "/api/capabilities",
                                  "alpha-two": "/api/capabilities",
                                  "beta": "/api/health"}


def test_a_tile_with_its_own_manifest_keeps_its_own_route(tmp_path, monkeypatch):
    """Whichever manifest is read first, a tile's own declaration beats a sibling's claim."""
    _rail(tmp_path, "a-owner", {"id": "a-owner", "status_route": "/api/one",
                                "also_serves": ["z-tile"]})
    _rail(tmp_path, "z-tile", {"id": "z-tile", "status_route": "/api/two"})
    monkeypatch.setattr(ds, "REPO", tmp_path)
    assert ds.status_routes()["z-tile"] == "/api/two"


def test_an_owner_with_no_status_route_lends_none(tmp_path, monkeypatch):
    _rail(tmp_path, "alpha", {"id": "alpha", "status_route": None, "also_serves": ["alpha-two"]})
    monkeypatch.setattr(ds, "REPO", tmp_path)
    assert ds.status_routes() == {}


def test_snapshot_probes_an_also_served_tile_and_records_its_status(tmp_path, monkeypatch):
    """End to end through snapshot(), with the network and the container runtime stubbed: the
    tile is fetched at /<tile><route> and recorded like any rail, not as a note."""
    _rail(tmp_path, "alpha", {"id": "alpha", "status_route": "/api/capabilities",
                              "also_serves": ["alpha-two"]})
    monkeypatch.setattr(ds, "REPO", tmp_path)
    env = tmp_path / ".env"
    env.write_text("PLATFORM_ENABLED_APPS=alpha,alpha-two\n", encoding="utf-8")
    asked: list[str] = []

    class _Resp:
        def __init__(self, body: bytes):
            self.status, self._body = 200, body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return self._body

    class _Opener:
        def open(self, req, timeout=None):
            asked.append(req.full_url)
            if req.full_url.endswith("/api/capabilities"):
                return _Resp(json.dumps({"ok": True, "role": "x"}).encode())
            return _Resp(b"{}")

    class _Ps:
        returncode, stdout, stderr = 0, "", ""

    monkeypatch.setattr(ds.urllib.request, "build_opener", lambda *a: _Opener())
    monkeypatch.setattr(ds.subprocess, "run", lambda *a, **k: _Ps())
    snap = ds.snapshot(env, "http://gw", "http://broker")
    tile = snap["rails"]["alpha-two"]
    assert tile["route"] == "/api/capabilities"
    assert tile["status"] == 200 and tile["body"] == {"ok": True, "role": "x"}
    assert "note" not in tile
    assert "http://gw/alpha-two/api/capabilities" in asked


# --- which apps a deployment serves ----------------------------------------------------------
#
# Read from PLATFORM_ENABLED_APPS alone, a box that leaves it unset (which means EVERY rail) was
# snapshotted with zero rails, and the run still exited 0.

def _two_rails(tmp_path, monkeypatch):
    _rail(tmp_path, "alpha", {"id": "alpha", "status_route": "/api/a", "also_serves": ["alpha-two"]})
    _rail(tmp_path, "beta", {"id": "beta", "status_route": "/api/b"})
    monkeypatch.setattr(ds, "REPO", tmp_path)


class TestServedApps:
    def test_unset_enabled_means_every_app_not_none(self, tmp_path, monkeypatch):
        _two_rails(tmp_path, monkeypatch)
        assert ds.served_apps({}) == (["alpha", "alpha-two", "beta"], {})

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_blank_enabled_is_unset(self, tmp_path, monkeypatch, blank):
        _two_rails(tmp_path, monkeypatch)
        assert ds.served_apps({"PLATFORM_ENABLED_APPS": blank})[0] == ["alpha", "alpha-two", "beta"]

    def test_disabled_is_subtracted_and_reported(self, tmp_path, monkeypatch):
        _two_rails(tmp_path, monkeypatch)
        served, skipped = ds.served_apps({"PLATFORM_DISABLED_APPS": "beta"})
        assert served == ["alpha", "alpha-two"]
        assert skipped == {"beta": "disabled on this box"}

    def test_an_explicit_enabled_list_is_the_whole_list(self, tmp_path, monkeypatch):
        _two_rails(tmp_path, monkeypatch)
        served, skipped = ds.served_apps({"PLATFORM_ENABLED_APPS": '["beta"]'})
        assert served == ["beta"]
        assert skipped == {"alpha": "not enabled on this box",
                           "alpha-two": "not enabled on this box"}

    @pytest.mark.parametrize("raw", ["beta", "alpha,beta", " alpha , beta ", '["alpha","beta"]',
                                     "alpha\nbeta", "alpha,,beta,", "[]"])
    def test_parse_parity_with_the_gateway(self, raw):
        """A stdlib COPY of the gateway's parser: if the two disagree, the snapshot probes a
        different set of apps from the one the gateway serves."""
        config = pytest.importorskip("platform_gateway_app.config")
        gw = config.GatewaySettings(enabled_apps=raw, disabled_apps=()).enabled_apps
        assert ds.parse_apps(raw) == tuple(gw)

    def test_every_app_is_the_gateways_default_list(self):
        """Unset ENABLED has to mean the same set on both sides, against the real tree."""
        config = pytest.importorskip("platform_gateway_app.config")
        assert set(ds.every_app()) == set(
            config.GatewaySettings.model_fields["enabled_apps"].default)


# --- which container CLI ----------------------------------------------------------------------
#
# Always `podman`: on the Docker box that recorded an exception string on both sides of every
# diff. Chosen now as lib-runtime.ps1's Resolve-PlatformRuntime does.

class _Run:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def _clis(monkeypatch, listing: dict[str, str | None | Exception]):
    """listing: cli -> its `ps -a` output, None when not installed, or an exception to raise."""
    monkeypatch.setattr(ds.shutil, "which", lambda c: None if listing.get(c, None) is None
                        else f"/bin/{c}")

    def run(argv, **_kw):
        got = listing[argv[0]]
        if isinstance(got, Exception):
            raise got
        return _Run(stdout=got)

    monkeypatch.setattr(ds.subprocess, "run", run)


class TestContainerCli:
    def test_the_cli_running_the_gateway_wins(self, monkeypatch):
        _clis(monkeypatch, {"podman": "", "docker": "platform-gateway-1 running\nx exited\n"})
        assert ds.container_cli()[0] == "docker"

    def test_running_beats_merely_listed(self, monkeypatch):
        _clis(monkeypatch, {"podman": "platform-gateway-1 exited\n",
                            "docker": "platform-gateway-1 running\n"})
        cli, why = ds.container_cli()
        assert cli == "docker" and "running" in why

    def test_listed_beats_merely_installed(self, monkeypatch):
        _clis(monkeypatch, {"podman": "", "docker": "platform-gateway-1 exited\n"})
        assert ds.container_cli()[0] == "docker"

    def test_with_nothing_listed_the_first_installed_cli(self, monkeypatch):
        _clis(monkeypatch, {"podman": "", "docker": ""})
        assert ds.container_cli()[0] == "podman"

    def test_a_cli_that_hangs_counts_as_listing_nothing(self, monkeypatch):
        _clis(monkeypatch, {"podman": ds.subprocess.TimeoutExpired("podman", 15),
                            "docker": "platform-gateway-1 running\n"})
        assert ds.container_cli()[0] == "docker"

    def test_none_installed(self, monkeypatch):
        _clis(monkeypatch, {})
        assert ds.container_cli() == (None, "neither podman nor docker is on PATH")


class TestContainers:
    def _stub(self, monkeypatch, ps, inspect):
        def run(argv, **_kw):
            return ps if argv[1] == "ps" else inspect
        monkeypatch.setattr(ds.subprocess, "run", run)

    def test_docker_inspect_shape(self, monkeypatch):
        self._stub(monkeypatch, _Run(stdout="platform-gateway-1\n"), _Run(stdout=json.dumps([{
            "Name": "/platform-gateway-1", "Image": "sha256:abc",
            "Config": {"Image": "platform-gateway:latest"}, "State": {"Status": "running"}}])))
        assert ds.containers("docker") == {"platform-gateway-1": {
            "image": "platform-gateway:latest", "id": "sha256:abc", "state": "running"}}

    def test_podman_inspect_shape(self, monkeypatch):
        self._stub(monkeypatch, _Run(stdout="platform-gateway-1\n"), _Run(stdout=json.dumps([{
            "Name": "platform-gateway-1", "Image": "abc", "ImageName": "localhost/gw:latest",
            "Config": {}, "State": {"Status": "running"}}])))
        assert ds.containers("podman")["platform-gateway-1"]["image"] == "localhost/gw:latest"

    def test_a_dead_transport_is_a_string_not_zero_containers(self, monkeypatch):
        self._stub(monkeypatch, _Run(returncode=125, stderr="cannot connect"), _Run())
        got = ds.containers("podman")
        assert isinstance(got, str) and "cannot connect" in got

    def test_no_cli_is_a_string(self):
        assert isinstance(ds.containers(None), str)


# --- the admin session is logged out, and the logout is proved ---------------------------------

class _Gateway:
    """A fake gateway behind a fake plain opener: /me answers 200 while the session lives."""

    def __init__(self, logout_works=True, me_ok_before=True):
        self.alive, self.logout_works, self.me_ok_before = True, logout_works, me_ok_before
        self.cookies: list[str] = []

    def open(self, req, timeout=None):
        self.cookies.append(req.get_header("Cookie"))
        if req.full_url.endswith("/logout"):
            if self.logout_works:
                self.alive = False
            return _Resp(200)
        ok = self.alive and self.me_ok_before
        if not ok:
            raise ds.urllib.error.HTTPError(req.full_url, 401, "no", {}, None)
        return _Resp(200)


class _Resp:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Cookie:
    def __init__(self, name, value):
        self.name, self.value = name, value


class TestEndSession:
    def test_a_real_logout_is_proved_revoked(self, monkeypatch):
        gw = _Gateway()
        monkeypatch.setattr(ds.urllib.request, "build_opener", lambda *a: gw)
        got = ds.end_session([_Cookie("platform_session", "t0k")], "http://gw")
        assert got == {"status": 200, "revoked": True}
        assert set(gw.cookies) == {"platform_session=t0k"}, "the session cookie was not replayed"

    def test_a_logout_that_deletes_nothing_is_not_revoked(self, monkeypatch):
        """logout answers 200 either way, so its status alone cannot fail."""
        monkeypatch.setattr(ds.urllib.request, "build_opener",
                            lambda *a: _Gateway(logout_works=False))
        got = ds.end_session([_Cookie("platform_session", "t0k")], "http://gw")
        assert got["status"] == 200 and got["revoked"] is False

    def test_a_401_before_logout_proves_nothing(self, monkeypatch):
        """The positive control: a malformed replay is ALSO a 401 afterwards."""
        monkeypatch.setattr(ds.urllib.request, "build_opener",
                            lambda *a: _Gateway(me_ok_before=False))
        assert ds.end_session([_Cookie("s", "x")], "http://gw")["revoked"] is False

    def test_no_session_no_logout(self, monkeypatch):
        monkeypatch.setattr(ds.urllib.request, "build_opener",
                            lambda *a: pytest.fail("nothing to log out"))
        assert ds.end_session([], "http://gw")["revoked"] is None


def test_snapshot_with_enabled_unset_checks_every_app_and_ends_its_session(tmp_path,
                                                                           monkeypatch):
    """The box this tool was inert on, end to end: no PLATFORM_ENABLED_APPS line, one rail
    disabled. Every other app is probed, the disabled one is recorded as skipped, the container
    CLI is the one chosen, and the session is ended."""
    _two_rails(tmp_path, monkeypatch)
    env = tmp_path / ".env"
    env.write_text("PLATFORM_DISABLED_APPS=beta\n", encoding="utf-8")
    asked: list[str] = []

    class _Opener:
        def open(self, req, timeout=None):
            asked.append(req.full_url)

            class _R(_Resp):
                def read(self):
                    return b"{}"
            return _R(200)

    ended = []
    monkeypatch.setattr(ds.urllib.request, "build_opener", lambda *a: _Opener())
    monkeypatch.setattr(ds, "container_cli", lambda: ("docker", "stub"))
    monkeypatch.setattr(ds, "containers", lambda cli: {"c": {"cli": cli}})
    monkeypatch.setattr(ds, "end_session",
                        lambda jar, gw: ended.append(gw) or {"status": 200, "revoked": True})
    snap = ds.snapshot(env, "http://gw", "http://broker")
    assert sorted(snap["rails"]) == ["alpha", "alpha-two"]
    assert snap["rails_skipped"] == {"beta": "disabled on this box"}
    assert "http://gw/alpha/api/a" in asked and "http://gw/alpha-two/api/a" in asked
    assert not any("/beta/" in u for u in asked)
    assert snap["container_cli"] == "docker" and snap["containers"] == {"c": {"cli": "docker"}}
    assert ended == ["http://gw"] and snap["logout"]["revoked"] is True
