"""tools/smoke-app-image.py drives whichever container CLI the box has.

It spelled `docker` at every call, so on a box with only podman it died on its first command, and
the runbook's step 4 (smoke the candidate before it replaces the running image) could not be done
there at all. The choice is now $CONTAINER_CLI when set, else docker when it is on PATH, else
podman, and every command the tool runs goes through it.

Two podman facts met on the live upgrade of 2026-10-14 are pinned here too. Podman gives a named
volume its owner once, at its first mount, so the volume preparation docker gets (the live
volume's root:root state) is not run under podman, where it spent that mount and left /app/data
root:root whatever the image held; the data-dir check reads the image's own /app/data owner beside
the volume's, so the v1.0.3 control still fails it. And a registry-less name is found under either
spelling a build gives it: docker.io/library/<name> (compose, through podman's Docker-compatible
API) or localhost/<name> (`podman build`).

Two more from the delta audit of 2026-10-15. A bare name is never looked up bare: podman resolves a
short name to localhost/ first, so a stale localhost build was smoked in compose's image's place;
compose's spelling is tried first. And podman is decided by the engine's own `version` output, so a
`docker` CLI on podman's API meets the volume as podman hands it out.

Nothing here runs a container or a browser: `sh` is replaced by a fake CLI that answers `image
inspect` and `version` the way docker or podman does and records every other command, and the app
is made to answer the way an image built without NEXT_BASE_PATH does, which is the path through
main() that ends before Playwright is needed.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1] / "tools" / "smoke-app-image.py"


def _load():
    spec = importlib.util.spec_from_file_location("smoke_app_image", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


smoke = _load()


def _path_has(monkeypatch, *names: str) -> None:
    """PATH holds exactly `names`, as far as the tool (or anything else) can tell."""
    monkeypatch.setattr(shutil, "which",
                        lambda name, *a, **k: f"/usr/bin/{name}" if name in names else None)


@pytest.mark.parametrize("env,on_path,expect", [
    ("podman", ("docker", "podman"), "podman"),       # the variable wins, even over docker
    ("  nerdctl ", ("docker",), "nerdctl"),           # any CLI, trimmed, need not be docker/podman
    ("", ("docker", "podman"), "docker"),             # unset: docker when it is there
    (None, ("podman",), "podman"),                    # a podman-only box, this one
    (None, (), "podman"),                             # nothing at all: podman, named in the error
], ids=["env-over-docker", "env-any-cli", "docker-on-path", "podman-only", "neither"])
def test_the_cli_is_the_variable_then_docker_then_podman(monkeypatch, env, on_path, expect):
    if env is None:
        monkeypatch.delenv("CONTAINER_CLI", raising=False)
    else:
        monkeypatch.setenv("CONTAINER_CLI", env)
    _path_has(monkeypatch, *on_path)
    assert smoke.container_cli() == expect


# What `version` prints: Docker's own engine, and a docker CLI on podman's Docker-compatible API
# (the server component podman's API reports is "Podman Engine").
DOCKER_VERSION = ("Client: Docker Engine - Community\n Version:           27.3.1\n\n"
                  "Server: Docker Engine - Community\n Engine:\n  Version:          27.3.1\n")
PODMAN_API_VERSION = ("Client: Docker Engine - Community\n Version:           27.3.1\n\n"
                      "Server: linux/amd64/fedora-41\n Podman Engine:\n  Version:          5.4.0\n")


@pytest.fixture
def recorded(monkeypatch):
    """Run main() against a fake CLI, every command recorded, the app answering like an image
    with no basePath. `have` is the qualified names the engine holds. An inspect of a short
    (registry-less) name resolves as the CLI does: podman's to localhost/ FIRST, then
    docker.io/library/; docker's to docker.io/library/ only. `version` prints `version` (by
    default nothing, so the CLI's name decides). Returns (exit code, every argv `sh` was given)."""
    calls: list[list[str]] = []
    held: set[str] = {"docker.io/library/openmaic-app:candidate"}
    said = {"version": ""}

    def resolve(cli: str, name: str) -> str | None:
        if "/" in name:
            return name if name in held else None
        podman = Path(cli).name.lower().startswith("podman")
        return next((f"{p}{name}" for p in (("localhost/", "docker.io/library/") if podman
                                             else ("docker.io/library/",))
                     if f"{p}{name}" in held), None)

    def fake_sh(*args, check=True, timeout=120):
        calls.append(list(args))
        if args[1:3] == ("image", "inspect"):
            if resolve(args[0], args[3]) is None:
                return subprocess.CompletedProcess(list(args), 125, stdout="[]",
                                                   stderr=f"Error: {args[3]}: image not known")
            out = json.dumps([{"Id": "sha256:feed", "Size": 2e8, "Created": "2026-10-13T00:00:00Z"}])
            return subprocess.CompletedProcess(list(args), 0, stdout=out, stderr="")
        if args[1:2] == ("version",):
            return subprocess.CompletedProcess(list(args), 0, stdout=said["version"], stderr="")
        return subprocess.CompletedProcess(list(args), 0, stdout="", stderr="")

    def fake_get(url, method="GET", body=None):
        # 404 under the basePath while / answers 200: start() returns at once with its problem.
        return (404, "") if smoke.BASE in url else (200, "")

    monkeypatch.setattr(smoke, "sh", fake_sh)
    monkeypatch.setattr(smoke, "get", fake_get)
    # main() imports Playwright before anything else; this path never launches it.
    pw = types.ModuleType("playwright")
    api = types.ModuleType("playwright.sync_api")
    api.sync_playwright = lambda: pytest.fail("the browser was launched on a serve failure")
    monkeypatch.setitem(sys.modules, "playwright", pw)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", api)

    def run(image: str = "openmaic-app:candidate", have: set[str] | None = None,
            version: str = ""):
        if have is not None:
            held.clear()
            held.update(have)
        said["version"] = version
        code = smoke.main([image])
        return code, calls
    return run


@pytest.mark.parametrize("on_path,expect,steps", [
    (("podman",), "podman", ["image inspect", "version", "network create", "volume create",
                             "run -d", "logs", "rm -f", "network rm", "volume rm"]),
    (("docker", "podman"), "docker", ["image inspect", "version", "network create",
                                      "volume create", "run --rm", "run -d", "logs", "rm -f",
                                      "network rm", "volume rm"]),
], ids=["podman-only", "docker-present"])
def test_every_container_command_goes_through_the_chosen_cli(
        monkeypatch, recorded, on_path, expect, steps):
    monkeypatch.delenv("CONTAINER_CLI", raising=False)
    _path_has(monkeypatch, *on_path)
    code, calls = recorded()
    assert code == 1                      # the serve check failed, as the fake app arranged
    # Every container command this path reaches, in order: the inspect, the engine's version,
    # start()'s, the log read, and stop()'s three removals.
    assert [c[1] if c[1] in ("logs", "version") else " ".join(c[1:3]) for c in calls] == steps
    assert {c[0] for c in calls} == {expect}, calls


#: How a podman CLI is named on the platform the tool runs on. A Windows path only where it is
#: one: on Linux a backslash is part of a file name, so `C:\...\podman.exe` names no file, and
#: main() refuses that CLI before it asks it anything (shutil.which finds nothing; the test
#: below stands in a `which` that finds every name). Asked anyway, the Windows path read as
#: docker there, because Path(cli).name splits on "/" alone (CI, 2026-10-17).
PODMAN_CLIS = ["podman", "/usr/bin/podman"] + (
    [r"C:\Program Files\RedHat\Podman\podman.exe"] if sys.platform == "win32" else [])


@pytest.mark.parametrize("cli", PODMAN_CLIS)
def test_podman_gets_a_fresh_volume_so_the_apps_mount_is_its_first(monkeypatch, recorded, cli):
    """Podman gives a volume its owner once, at its FIRST mount. The root:root preparation
    (docker's model of the live volume, which docker re-owns at every mount of an empty volume)
    spent that mount, so on podman /app/data stayed root:root whatever the image held: the
    data-dir failure of 2026-10-14. No container touches the volume before the app's."""
    monkeypatch.setenv("CONTAINER_CLI", cli)
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: name)
    _, calls = recorded()
    vol = next(c[3] for c in calls if c[1:3] == ["volume", "create"])
    mounts = [c for c in calls if c[1] == "run" and any(a.startswith(f"{vol}:") for a in c)]
    assert len(mounts) == 1 and mounts[0][2] == "-d" and f"{vol}:/app/data" in mounts[0], mounts


def test_docker_still_meets_the_volume_as_the_live_one_is(monkeypatch, recorded):
    monkeypatch.delenv("CONTAINER_CLI", raising=False)
    _path_has(monkeypatch, "docker")
    _, calls = recorded()
    prep = [c for c in calls if c[1:3] == ["run", "--rm"]]
    assert len(prep) == 1 and prep[0][-1] == "chown root:root /mnt && chmod 755 /mnt", prep


@pytest.mark.parametrize("asked,have,used", [
    ("openmaic-app:candidate", {"docker.io/library/openmaic-app:candidate"},
     "docker.io/library/openmaic-app:candidate"),           # built through compose's API
    ("openmaic-app:candidate", {"localhost/openmaic-app:candidate"},
     "localhost/openmaic-app:candidate"),                   # `podman build` given the bare name
    ("localhost/openmaic-app:candidate", {"docker.io/library/openmaic-app:candidate"},
     "docker.io/library/openmaic-app:candidate"),
    ("docker.io/library/openmaic-app:candidate", {"localhost/openmaic-app:candidate"},
     "localhost/openmaic-app:candidate"),
    ("localhost/openmaic-app:candidate", {"docker.io/library/openmaic-app:candidate",
                                          "localhost/openmaic-app:candidate"},
     "localhost/openmaic-app:candidate"),                   # a qualified name as given wins
], ids=["bare-finds-docker.io", "bare-finds-localhost", "localhost-finds-docker.io",
        "docker.io-finds-localhost", "qualified-as-given-first"])
def test_a_registry_less_name_is_found_under_either_spelling(monkeypatch, recorded, capsys,
                                                             asked, have, used):
    monkeypatch.delenv("CONTAINER_CLI", raising=False)
    _path_has(monkeypatch, "podman")
    code, calls = recorded(asked, have)
    assert code == 1                      # it got as far as the serve check
    runs = [c for c in calls if c[1] == "run"]
    assert runs and all(used in c for c in runs), runs
    assert f"image {used}  id sha256:feed" in capsys.readouterr().out


def test_an_image_under_neither_spelling_is_a_clear_refusal(monkeypatch, recorded, capsys):
    monkeypatch.delenv("CONTAINER_CLI", raising=False)
    _path_has(monkeypatch, "podman")
    code, calls = recorded("openmaic-app:candidate", set())
    assert code == 2 and all(c[1:3] == ["image", "inspect"] for c in calls)
    out = capsys.readouterr().out
    assert ("tried docker.io/library/openmaic-app:candidate, "
            "localhost/openmaic-app:candidate") in out


def test_a_bare_name_smokes_composes_image_not_a_stale_localhost_build(monkeypatch, recorded,
                                                                         capsys):
    """A podman box holding compose's docker.io/library/openmaic-app:candidate and a stale
    localhost/openmaic-app:candidate from an older bare-name build. Podman resolves the short
    name to localhost/ first, so looking it up as given smoked the stale image, under the name
    asked for and with no note that it was another."""
    monkeypatch.delenv("CONTAINER_CLI", raising=False)
    _path_has(monkeypatch, "podman")
    code, calls = recorded("openmaic-app:candidate", {"docker.io/library/openmaic-app:candidate",
                                                      "localhost/openmaic-app:candidate"})
    assert code == 1
    assert ["podman", "image", "inspect", "openmaic-app:candidate"] not in calls, \
        "the short name was looked up as given"
    runs = [c for c in calls if c[1] == "run"]
    assert runs and all("docker.io/library/openmaic-app:candidate" in c for c in runs), runs
    out = capsys.readouterr().out
    assert "image docker.io/library/openmaic-app:candidate  id sha256:feed" in out
    assert "[asked for openmaic-app:candidate]" in out


@pytest.mark.parametrize("version,podman", [
    (PODMAN_API_VERSION, True),           # a docker CLI on podman's Docker-compatible API
    (DOCKER_VERSION, False),              # Docker's own engine
], ids=["docker-cli-on-podman", "docker-engine"])
def test_the_engine_not_the_cli_name_decides_podman(monkeypatch, recorded, capsys, version,
                                                    podman):
    """A `docker` CLI talking to podman took Docker's volume preparation, which spends a podman
    volume's one-time ownership: the false data-dir failure of 2026-10-14, back by another road."""
    monkeypatch.delenv("CONTAINER_CLI", raising=False)
    _path_has(monkeypatch, "docker")
    code, calls = recorded(version=version)
    assert code == 1 and {c[0] for c in calls} == {"docker"}
    prep = [c for c in calls if c[1:3] == ["run", "--rm"]]
    assert (prep == []) is podman, prep
    assert f"(via docker, a {'podman' if podman else 'docker'} engine)" in capsys.readouterr().out


def test_a_name_with_another_registry_or_a_namespace_is_taken_as_given():
    assert smoke.image_candidates("ghcr.io/someone/openmaic-app:1") == ["ghcr.io/someone/openmaic-app:1"]
    assert smoke.image_candidates("someone/openmaic-app:1") == ["someone/openmaic-app:1"]
    # An image id is no name to qualify.
    assert smoke.image_candidates("0123456789ab") == ["0123456789ab"]
    assert smoke.image_candidates("sha256:" + "f" * 64) == ["sha256:" + "f" * 64]


def _data_dir(monkeypatch, cli: str, image_owner: str | None, volume_out: str,
              volume_rc: int = 0):
    """check_data_dir against a fake CLI: `image_owner` is what `stat` reads on the image's own
    /app/data (None: it has none), `volume_out` what the exec in the app's container prints."""
    calls: list[list[str]] = []

    def fake_sh(*args, check=True, timeout=120):
        calls.append(list(args))
        if args[1:3] == ("run", "--rm"):
            if image_owner is None:
                return subprocess.CompletedProcess(
                    list(args), 1, stdout="",
                    stderr="stat: can't stat '/app/data': No such file or directory")
            return subprocess.CompletedProcess(list(args), 0, stdout=image_owner + "\n", stderr="")
        return subprocess.CompletedProcess(list(args), volume_rc, stdout=volume_out, stderr="")

    monkeypatch.setattr(smoke, "sh", fake_sh)
    s = smoke.Smoke("docker.io/library/openmaic-app:candidate", False, cli, cli == "podman")
    s.check_data_dir()
    return s.results, calls


def test_the_data_dir_check_goes_through_the_chosen_cli(monkeypatch):
    """check_data_dir is the one command main() reaches only on a serving app."""
    results, calls = _data_dir(monkeypatch, "podman", "nextjs",
                               "nextjs\nnextjs:nodejs 755\nwritable\n")
    assert [c[:2] for c in calls] == [["podman", "run"], ["podman", "exec"]]
    # The image's own /app/data, read with no volume over it.
    assert calls[0][2:] == ["--rm", "--entrypoint", "stat",
                            "docker.io/library/openmaic-app:candidate", "-c", "%U", "/app/data"]
    assert results == [("data-dir", True, "image /app/data owner nextjs; fresh volume (podman "
                        "keeps an existing one's owner): nextjs nextjs:nodejs 755 writable")]


def test_an_image_without_its_own_data_dir_fails_even_on_a_writable_volume(monkeypatch):
    """The v1.0.3 control, on podman: a fresh volume may be writable whatever the image holds,
    so the volume alone cannot tell this image from a good one. The image's own directory can."""
    results, _ = _data_dir(monkeypatch, "podman", None, "nextjs\nnextjs:nogroup 755\nwritable\n")
    assert results[0][1] is False
    assert "owner none: stat: can't stat '/app/data'" in results[0][2]


def test_a_volume_the_app_cannot_write_fails_whatever_the_image_holds(monkeypatch):
    """The failure of 2026-10-14 as the check now reads it: the image's /app/data is the app's,
    the volume is root's (a podman volume an earlier mount spent, or the live one)."""
    results, _ = _data_dir(monkeypatch, "docker", "nextjs", "nextjs\nroot:root 755\n",
                           volume_rc=1)
    assert results[0][1] is False
    assert results[0][2].startswith("image /app/data owner nextjs; empty root-owned volume")


def test_no_cli_on_path_is_a_clear_refusal_not_a_traceback(monkeypatch, recorded, capsys):
    monkeypatch.delenv("CONTAINER_CLI", raising=False)
    _path_has(monkeypatch)
    code, calls = recorded()
    assert code == 2 and calls == []
    out = capsys.readouterr().out
    assert "podman" in out and "CONTAINER_CLI" in out
