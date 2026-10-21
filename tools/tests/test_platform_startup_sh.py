"""platform-startup.sh (a WSL box's logon path) reads deploy/.env as compose does and honours
PLATFORM_DISABLED_APPS.

It concatenated two PLATFORM_ENABLED_APPS lines with no separator (grep | cut | tr), kept a trailing
comment as part of the list, and ignored PLATFORM_DISABLED_APPS altogether, so a rail disabled on a
WSL box came back at every logon. The REAL script runs here under bash with stand-ins for docker,
ip, sleep, wslpath and powershell.exe on a PATH that holds nothing from Windows, and its dotenv
reader is held to the measured compose cases it implements.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "deploy" / "installer" / "platform-startup.sh"
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import mutation_runner as mr  # noqa: E402  (the shared runner: a timeout kills the whole tree)


def _bash() -> str | None:
    if sys.platform != "win32":
        return shutil.which("bash")
    # Git's bash, never System32\bash.exe (that one is WSL's, and would run a real distro).
    git = shutil.which("git")
    if not git:
        return None
    # git.exe sits in <git>\cmd or <git>\mingw64\bin depending on the install.
    for anc in list(Path(git).resolve().parents)[:3]:
        cand = anc / "usr" / "bin" / "bash.exe"
        if cand.is_file():
            return str(cand)
    return None


BASH = _bash()
pytestmark = pytest.mark.skipif(BASH is None, reason="needs bash (Git for Windows' on Windows)")

# Every child here runs through the shared runner: a timeout kills the whole process TREE, and the
# read after the kill is bounded (mr.REAP_TIMEOUT). Plain subprocess.run killed only bash on a
# timeout and then, on Windows, read the pipes with NO limit while the orphaned `sleep infinity`
# still held them: one run of this file sat 45 minutes and three sleep.exe were left behind.
# Calling cygpath by its path (below) removed that trigger; this removes the shape.
TIMEOUT = 60


def _run(cmd, **kw) -> subprocess.CompletedProcess:
    return mr.run_tree(cmd, timeout=kw.pop("timeout", TIMEOUT), **kw)


def _env_without_git_tools() -> dict:
    """The environment a PowerShell-launched run has: no Git usr\\bin or mingw64\\bin on PATH. Every
    bash here runs under it, so a regression to finding cygpath through PATH fails from Git Bash
    too, where that PATH used to hide it."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("PLATFORM_")}
    keep = [d for d in env.get("PATH", "").split(os.pathsep)
            if d and not (Path(d) / "cygpath.exe").is_file()
            and not d.rstrip("/\\").replace("\\", "/").lower().endswith("/mingw64/bin")]
    env["PATH"] = os.pathsep.join(keep)
    return env


def stub(path: Path, body: str) -> None:
    path.write_bytes(("#!/usr/bin/env bash\n" + body + "\n").encode("utf-8"))
    path.chmod(0o755)


# `ip` on a NAT-mode WSL box: a default route via the Windows end of the vNIC, and no loopback0
# interface (only mirrored networking creates one; `ip link show` of a missing device exits 1).
IP_NAT = ('case "$1" in\n'
          '  link) echo "Device \\"$4\\" does not exist." >&2; exit 1 ;;\n'
          '  *) echo "default via 172.17.208.1 dev eth0 proto kernel" ;;\n'
          'esac')
# ... and under mirrored networking: the LAN router as the default route, and a loopback0.
IP_MIRRORED = ('case "$1" in\n'
               '  link) echo "5: loopback0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500"; exit 0 ;;\n'
               '  *) echo "default via 192.168.1.1 dev eth0 proto kernel metric 25" ;;\n'
               'esac')


def run_startup(base: Path, env_text: str, interop: bool, stubs: dict | None = None) -> dict:
    root = base / "repo"
    (root / "deploy" / "installer").mkdir(parents=True)
    shutil.copy2(SCRIPT, root / "deploy" / "installer" / "platform-startup.sh")
    (root / "deploy" / "installer" / "docker-compose.installer.yml").write_text("services: {}\n", encoding="utf-8")
    (root / "deploy" / ".env").write_bytes(env_text.encode("utf-8"))
    bin_ = base / "bin"
    bin_.mkdir()
    calls = base / "calls.txt"
    rec = f'printf "%s\\n" "$(basename "$0") $*" >> "$CALLS"'
    stub(bin_ / "docker", rec)
    stub(bin_ / "sleep", rec)
    stub(bin_ / "ip", IP_NAT)
    stub(bin_ / "wslpath", 'echo "C:\\\\repo\\\\deploy\\\\installer\\\\sync-disabled-rails.ps1"')
    if interop:
        stub(bin_ / "powershell.exe", rec)
    for name, body in (stubs or {}).items():
        stub(bin_ / name, body)
    driver = base / "driver.sh"
    driver.write_bytes((
        'set -u\n'
        'S="$1"; B="$2"\n'
        # By absolute path: a bash started from PowerShell (run-tests.ps1) has no /usr/bin on
        # PATH, so `command -v cygpath` failed there, the stub dir stayed a C:\ path PATH cannot
        # hold, the REAL `exec sleep infinity` ran, and its open pipe hung pytest past the timeout.
        'if [ -x /usr/bin/cygpath ]; then S=$(/usr/bin/cygpath -u "$S"); B=$(/usr/bin/cygpath -u "$B"); CALLS=$(/usr/bin/cygpath -u "$CALLS"); fi\n'
        'export CALLS\n'
        'PATH="$B:/usr/bin:/bin" bash "$S"\n').encode("utf-8"))
    env = _env_without_git_tools()
    # The driver is handed C:/... paths, not C:\... ones. Unconverted, a backslash stub dir still
    # worked: bash splits PATH at the drive's colon, and MSYS resolves the `\Users\...` half against
    # the CURRENT drive, so the stubs were found whenever the run's cwd shared tmp's drive, and the
    # harness case that removes the driver's own cygpath lookup could not fire on such a host. The
    # `/Users/...` half of a C:/ path resolves under MSYS's own root instead, so only the driver's
    # conversion reaches the stubs. cygpath -u converts either form.
    env["CALLS"] = calls.as_posix()
    r = _run([BASH, str(driver), (root / "deploy" / "installer" / "platform-startup.sh").as_posix(),
              bin_.as_posix()],
             capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    log = root / "deploy" / "logs" / "startup.log"
    return {"code": r.returncode, "out": r.stdout + r.stderr,
            "calls": calls.read_text(encoding="utf-8").splitlines() if calls.exists() else [],
            "log": log.read_text(encoding="utf-8") if log.exists() else "",
            "env": (root / "deploy" / ".env").read_text(encoding="utf-8")}


ENV = ("PLATFORM_ENABLED_APPS=terminal-fun,recipe-book\n"
       "PLATFORM_ENABLED_APPS=terminal-fun,co-worker,gemini-cx,openmaic  # this box\n"
       'PLATFORM_DISABLED_APPS="openmaic"\n'
       "WINDOWS_HOST=1.2.3.4\n")


@pytest.fixture(scope="module")
def startup(tmp_path_factory):
    return {"interop": run_startup(tmp_path_factory.mktemp("sh"), ENV, True),
            "no-interop": run_startup(tmp_path_factory.mktemp("sh"), ENV, False)}


def _up(run) -> str:
    ups = [c for c in run["calls"] if c.startswith("docker compose") and " up " in c + " "]
    assert len(ups) == 1, run
    return ups[0]


def test_the_last_enabled_line_wins_and_a_disabled_rail_gets_no_profile(startup):
    up = _up(startup["interop"])
    assert "--profile co-worker" in up and "--profile gemini-cx" in up, up
    assert "recipe-book" not in up, up            # the first line lost, as compose loses it
    assert "openmaic" not in up, up               # disabled
    assert "disabled: openmaic" in startup["interop"]["log"], startup["interop"]["log"]


def test_disabled_rails_are_parked_through_the_shared_powershell_step(startup):
    run = startup["interop"]
    ps = [c for c in run["calls"] if c.startswith("powershell.exe")]
    assert len(ps) == 1, run
    assert "-File C:\\repo\\deploy\\installer\\sync-disabled-rails.ps1 -RuntimeMode wsl" in ps[0], ps
    # after `up`, as platform-startup.ps1 parks after its own: `up` would start a parked container
    assert run["calls"].index(ps[0]) > run["calls"].index(_up(run)), run["calls"]


def test_without_interop_the_unparked_rails_are_named_not_skipped_in_silence(startup):
    run = startup["no-interop"]
    assert not [c for c in run["calls"] if c.startswith("powershell.exe")]
    assert "WARNING: no Windows interop" in run["log"] and "NOT parked: openmaic" in run["log"], run["log"]


def test_the_gateway_address_is_still_refreshed(startup):
    assert "WINDOWS_HOST=172.17.208.1" in startup["interop"]["env"].splitlines()


# --- WSL's mirrored networking: the default route is the LAN router, not Windows -------------------

# name -> (extra stubs, whether the mode reads as mirrored)
NET_MODES = {
    # wslinfo's answer alone decides (the `ip` here has no loopback0 and a NAT-looking route).
    "wslinfo-mirrored": ({"wslinfo": 'echo mirrored'}, True),
    "loopback0-only": ({"ip": IP_MIRRORED}, True),                      # an older WSL with no wslinfo
    "wslinfo-nat": ({"wslinfo": 'echo nat'}, False),
}


@pytest.fixture(scope="module")
def net_modes(tmp_path_factory):
    return {name: run_startup(tmp_path_factory.mktemp("sh"), ENV, True, stubs)
            for name, (stubs, _) in NET_MODES.items()}


@pytest.mark.parametrize("name", [n for n, (_, m) in NET_MODES.items() if m])
def test_mirrored_networking_leaves_windows_host_alone_and_says_why(net_modes, name):
    run = net_modes[name]
    assert "WINDOWS_HOST=1.2.3.4" in run["env"].splitlines(), run["env"]     # not the router's address
    assert "192.168.1.1" not in run["env"], run["env"]
    assert "WARNING: WSL runs in mirrored networking mode" in run["log"], run["log"]
    assert "leaving WINDOWS_HOST unchanged" in run["log"] and "networkingMode=nat" in run["log"], run["log"]
    _up(run)                                                                 # and the stack still comes up


def test_nat_networking_named_by_wslinfo_refreshes_the_address(net_modes):
    run = net_modes["wslinfo-nat"]
    assert "WINDOWS_HOST=172.17.208.1" in run["env"].splitlines(), run["env"]
    assert "mirrored" not in run["log"], run["log"]


# --- dotenv_get against the measured compose cases it implements ---------------------------------

def _subset():
    """The cases dotenv_get claims: no $-expansion, no double-quote escapes, no value spanning
    lines, no file compose refuses. Everything else is the PowerShell reader's alone."""
    out = []
    for f in ("dotenv_compose_cases.json", "dotenv_compose_cases_extra.json"):
        for c in json.loads((HERE / f).read_text(encoding="utf-8"))["cases"]:
            env = c["env"]
            if c.get("error") or "$" in env or ('"' in env and "\\" in env):
                continue
            if c["value"] is not None and ("\n" in c["value"] or "\r" in c["value"]):
                continue
            out.append(c)
    return out


SUBSET = _subset()


def test_the_subset_is_most_of_the_cases():
    # 44 of the 84 measured cases on 2026-10-08. A filter that grew greedy would hollow this out.
    assert len(SUBSET) >= 44, len(SUBSET)


def test_dotenv_get_gives_composes_answer(tmp_path):
    text = SCRIPT.read_text(encoding="utf-8")
    func = re.search(r"^dotenv_get\(\) \{\n.*?^\}\n", text, re.S | re.M).group(0)
    files = []
    for i, c in enumerate(SUBSET):
        p = tmp_path / f"{i}.env"
        p.write_bytes(c["env"].encode("utf-8"))
        files.append(p)
    (tmp_path / "func.sh").write_bytes(func.encode("utf-8"))
    body = ('. "$1"; shift\n'
            'for f in "$@"; do if v=$(dotenv_get K "$f"); then printf "SET:%s\\0" "$v"; else printf "UNSET\\0"; fi; done\n')
    (tmp_path / "run.sh").write_bytes(body.encode("utf-8"))
    env = _env_without_git_tools()
    conv = (lambda p: _run([BASH, "-c", '/usr/bin/cygpath -u "$1"', "_", str(p)], capture_output=True,
                           text=True, env=env, timeout=30).stdout.strip()) if sys.platform == "win32" else str
    r = _run([BASH, conv(tmp_path / "run.sh"), conv(tmp_path / "func.sh")] + [conv(p) for p in files],
             capture_output=True, env=env)
    assert r.returncode == 0, r.stderr
    got = r.stdout.decode("utf-8").split("\0")[:-1]
    assert len(got) == len(SUBSET)
    bad = []
    for c, g in zip(SUBSET, got):
        want = "UNSET" if c["value"] is None else "SET:" + c["value"]
        if g != want:
            bad.append((c["name"], g, want))
    assert bad == [], bad


# --- a child that outlives the kill cannot hold a run for ever --------------------------------

# DEADLINE is the run's timeout, when run_tree issues its kill. B (below) must be gone by then, or
# the tree kill takes C too and nothing holds the pipe: under a CPU hog plus a process-spawn storm
# B exited 3.9-4.6 s in, after the old 3 s deadline, and C escaped only because taskkill was slow.
DEADLINE = 10
# After its kill run_tree waits at most REAP_TIMEOUT for the tree kill and REAP_TIMEOUT for the
# read that follows. The margin is for a host slow to start or run taskkill: most runs take ~16 s
# from the kill, one here took 48.5 s.
AFTER_KILL = 2 * mr.REAP_TIMEOUT + 30
# Past every bound above, so a run that waits for the orphan can only come back after it let go.
ORPHAN_LIFE = DEADLINE + AFTER_KILL + 30

# A starts B and waits for it; B starts C and exits at once, so C's parent is gone and a tree kill
# of A cannot find it. C keeps the inherited stdout pipe open, as the orphaned sleep.exe did, then
# writes `<pidfile>.released` and exits. On POSIX the tree kill is a process-group kill, which
# would reach C, so there C leaves the group first, as a daemonising grandchild does.
ORPHAN_C =("import os, sys, time\n"
            "if hasattr(os, 'setsid'):\n    os.setsid()\n"
            "open(sys.argv[1], 'w').write(str(os.getpid()))\n"
            f"time.sleep({ORPHAN_LIFE:g})\nopen(sys.argv[1] + '.released', 'w').close()\n")
ORPHAN_B = "import subprocess, sys\nsubprocess.Popen([sys.executable, sys.argv[2], sys.argv[1]])\n"
ORPHAN_A = ("import subprocess, sys, time\n"
            "subprocess.Popen([sys.executable, sys.argv[2], sys.argv[1], sys.argv[3]]).wait()\n"
            f"time.sleep({ORPHAN_LIFE:g})\n")


def test_a_child_that_outlives_the_kill_cannot_hold_the_run_for_ever(tmp_path, monkeypatch):
    """The run comes back while an orphan outside the killed tree still holds its pipe, within the
    runner's own bounds of the moment it issued the kill. Plain subprocess.run waited here until
    the orphan exited (ORPHAN_LIFE in this test; for the script's `sleep infinity`, never).

    The verdict is the order of two events, not a stopwatch from the test's start: that one
    (timeout + REAP_TIMEOUT + 5 s) sat 3.5 s above an idle run, and a loaded host failed it."""
    for name, body in (("a.py", ORPHAN_A), ("b.py", ORPHAN_B), ("c.py", ORPHAN_C)):
        (tmp_path / name).write_text(body, encoding="utf-8")
    pidfile = tmp_path / "orphan.pid"
    released = tmp_path / "orphan.pid.released"
    killed_at: list[float] = []
    real_kill = mr._kill_tree

    def timed_kill(proc):
        killed_at.append(time.monotonic())
        real_kill(proc)

    monkeypatch.setattr(mr, "_kill_tree", timed_kill)
    try:
        with pytest.raises(subprocess.TimeoutExpired) as timed_out:
            _run([sys.executable, str(tmp_path / "a.py"), str(pidfile), str(tmp_path / "b.py"),
                  str(tmp_path / "c.py")], capture_output=True, timeout=DEADLINE)
        back = time.monotonic()
        assert pidfile.is_file(), "the orphan never started, so this proved nothing"
        assert not released.exists(), "the run came back only after the orphan let go of the pipe"
        # run_tree hands back no output when the read after its kill was cut off: something
        # outside the tree still held the pipe.
        assert timed_out.value.output is None, (
            "the read after the kill finished, so the tree kill took the orphan too (was B still "
            "running at the deadline?) and this proved nothing")
        assert killed_at, "the run timed out without run_tree's tree kill"
        assert back - killed_at[0] < AFTER_KILL, (
            f"{back - killed_at[0]:.1f} s from the kill to the run's return (bound {AFTER_KILL:g} s)")
    finally:
        pid = int(pidfile.read_text(encoding="utf-8").strip() or 0) if pidfile.is_file() else 0
        if pid and sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=30)
        elif pid:
            try:
                os.kill(pid, 9)
            except OSError:
                pass
