"""deploy/installer/smoke-test.ps1: the checks that decide its exit code.

The script is the only out-of-process coverage of the container stack, and its exit code is the
whole of its contract ("0 only when every check in the selected stage(s) passed"). Every test
here drives the REAL script, copied into a scratch repo so it reads a scratch deploy/.env, with
the container CLIs shadowed by global functions. A function wins command resolution over an
executable, so nothing here can reach a real runtime; each harness also records which CLI was
called, so a test can tell the runtime the script CHOSE from the one it was meant to.
"""
from __future__ import annotations

import json
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
INSTALLER = REPO / "deploy" / "installer"


def q(p) -> str:
    return "'" + str(p).replace("'", "''") + "'"


def make_root(base: Path, env: str) -> Path:
    root = base / "repo"
    inst = root / "deploy" / "installer"
    inst.mkdir(parents=True, exist_ok=True)
    for name in ("smoke-test.ps1", "lib-runtime.ps1"):
        shutil.copy2(INSTALLER / name, inst / name)
    (root / "deploy" / ".env").write_text(env, encoding="utf-8")
    return root


# Global stubs for every CLI the script can reach. $global:scenario picks what they answer.
STUBS = r"""
$ErrorActionPreference = 'Continue'
$global:calls = New-Object System.Collections.Generic.List[string]
# Flattened: the script calls `& docker @(@('compose') + $Arguments)`, which hands a FUNCTION one
# array argument where a native exe would have received the elements as separate argv entries.
function global:Get-Argv($a) { @(foreach ($x in $a) { foreach ($y in @($x)) { [string]$y } }) }
function global:docker {
  $a = Get-Argv $args
  $global:calls.Add('docker ' + ($a -join ' '))
  if ($a[0] -eq 'compose' -and ($a -contains '--services')) { 'gateway'; 'terminal-fun'; 'caddy'; return }
  if ($a[0] -eq 'compose' -and $a[-1] -eq 'config') {
    if ($global:scenario -eq 'empty-render') { return }
    'services:'
    '  caddy:'
    '    image: docker.io/library/caddy:2'
    '  gateway:'
    '    image: platform-gateway:latest'
    '  terminal-fun:'
    '    image: terminal-fun-backend:latest'
    return
  }
  if ($a[0] -eq 'images') {
    'platform-gateway:latest'
    if ($global:scenario -ne 'missing-image') { 'terminal-fun-backend:latest' }
    return
  }
}
function global:podman { $global:calls.Add('podman ' + ((Get-Argv $args) -join ' ')) }
function global:docker-compose { $global:calls.Add('docker-compose ' + ((Get-Argv $args) -join ' ')) }
function global:wsl.exe { $global:calls.Add('wsl.exe ' + ((Get-Argv $args) -join ' ')) }
"""


def run_scenarios(run_ps, shell, script: Path, scenarios: list[tuple[str, str]], stubs: str = STUBS,
                  *, offline: bool = True) -> dict:
    """One engine process, several invocations of the real script. Returns name -> (exit, out).
    `offline` goes to run_ps (conftest's PS_OFFLINE)."""
    body = stubs
    for name, argline in scenarios:
        body += f"""
$global:scenario = '{name}'
$global:calls.Clear()
Write-Output '<<<{name}>>>'
& {q(script)} {argline} *>&1 | ForEach-Object {{ [string]$_ }}
Write-Output "EXIT=$LASTEXITCODE"
Write-Output ("CALLS=" + ($global:calls -join ' | '))
"""
    r = run_ps(shell, body, offline=offline)
    assert r.returncode == 0, r.stdout + r.stderr
    out: dict = {}
    for chunk in r.stdout.split("<<<")[1:]:
        name, _, text = chunk.partition(">>>")
        code = int(text.rsplit("EXIT=", 1)[1].split()[0])
        out[name] = (code, text)
    assert set(out) == {n for n, _ in scenarios}, r.stdout + r.stderr
    return out


# --- build stage -----------------------------------------------------------------------------

@pytest.fixture(scope="module")
def build_stage(run_ps, shell, tmp_path_factory):
    root = make_root(tmp_path_factory.mktemp("build"), "PLATFORM_ENABLED_APPS=terminal-fun\n")
    script = root / "deploy" / "installer" / "smoke-test.ps1"
    args = "-Stage build -RuntimeMode desktop"
    return run_scenarios(run_ps, shell, script,
                         [("all-present", args), ("empty-render", args), ("missing-image", args)])


def test_build_stage_passes_when_every_built_image_is_present(build_stage):
    """The positive control: without it, the two tests below could pass on a stage that always
    fails, and 'exit 1' would prove nothing about the guard."""
    code, out = build_stage["all-present"]
    assert code == 0, out
    assert "image built: platform-gateway:latest" in out
    assert "image built: terminal-fun-backend:latest" in out
    assert "caddy:2" not in out   # a pulled image is not asserted as built


def test_an_empty_image_render_fails_the_build_stage(build_stage):
    """The second `compose config` render is a separate call from the services list. When it came
    back empty the image loop ran zero times and the stage exited 0 on the three earlier Oks."""
    code, out = build_stage["empty-render"]
    assert code == 1, out
    assert "rendered no built image" in out
    assert "SMOKE TEST FAILED" in out


def test_a_missing_built_image_fails_the_build_stage(build_stage):
    code, out = build_stage["missing-image"]
    assert code == 1, out
    assert "image MISSING: terminal-fun-backend:latest" in out


# --- e2e stage -------------------------------------------------------------------------------

class _Gateway(BaseHTTPRequestHandler):
    """Just enough of the gateway for the e2e stage: healthz, the shell, login, the three
    authenticated reads, logout and /me. `secure` decides whether the session cookie carries the
    Secure flag; `revoke` whether logout really ends the session. Each server records the Cookie
    header of every logout it receives in `logouts`."""
    secure = False
    revoke = True
    me = True                       # False: /me refuses every session, as a broken replay would see
    password = None                 # set: login refuses any other password, as the real gateway does
    logouts: list = []
    revoked: set = set()

    def log_message(self, *a):          # keep pytest output clean
        pass

    def _send(self, code: int, body: bytes = b"ok", ctype: str = "text/plain", extra=()):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _authed(self) -> bool:
        return "session=s1" in (self.headers.get("Cookie") or "") and "s1" not in self.revoked

    def do_GET(self):
        if self.path == "/api/platform/healthz":
            return self._send(200)
        if self.path == "/":
            return self._send(200, b'<div id="root"></div>', "text/html")
        if not self._authed():
            return self._send(401, b"no session")
        if self.path == "/api/platform/me":
            return self._send(200, b"{}", "application/json") if self.me else self._send(401, b"no")
        if self.path == "/api/platform/apps":
            return self._send(200, json.dumps({"apps": [{"id": "alpha", "status": "live"}]}).encode(),
                              "application/json")
        if self.path in ("/alpha/assets/remoteEntry.js", "/api/platform/status", "/api/platform/models"):
            return self._send(200)
        return self._send(404, b"nope")

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.path == "/api/platform/logout":
            self.logouts.append(self.headers.get("Cookie") or "")
            if self.revoke and "session=s1" in (self.headers.get("Cookie") or ""):
                self.revoked.add("s1")
            return self._send(200, b'{"ok": true}', "application/json")
        if self.path != "/api/platform/login":
            return self._send(404, b"nope")
        if self.password is not None and json.loads(raw or b"{}").get("password") != self.password:
            return self._send(401, b'{"detail": "bad credentials"}', "application/json")
        cookie = "session=s1; Path=/; HttpOnly" + ("; Secure" if self.secure else "")
        return self._send(200, b"{}", "application/json", [("Set-Cookie", cookie)])


def _serve(secure: bool, revoke: bool = True, me: bool = True, password: str | None = None):
    handler = type("H", (_Gateway,), {"secure": secure, "revoke": revoke, "me": me, "logouts": [],
                                      "revoked": set(), "password": password})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture(scope="module")
def e2e_stage(run_ps, shell, tmp_path_factory):
    plain, secure, broken = _serve(False), _serve(True), _serve(False, revoke=False)
    blind = _serve(False, me=False)
    strict = _serve(False, password=QUOTED_PASSWORD)
    try:
        creds = make_root(tmp_path_factory.mktemp("e2e"),
                          "PLATFORM_ADMIN_USER=owner-one\nPLATFORM_ADMIN_PASSWORD=pw\n")
        nocreds = make_root(tmp_path_factory.mktemp("e2e"), "PLATFORM_ENABLED_APPS=alpha\n")
        url = lambda s: f"http://127.0.0.1:{s.server_address[1]}"   # noqa: E731
        with_creds = creds / "deploy" / "installer" / "smoke-test.ps1"
        without = nocreds / "deploy" / "installer" / "smoke-test.ps1"
        base = "-Stage e2e -RuntimeMode desktop -BaseUrl"
        # offline=False: the e2e stage's Invoke-WebRequest is the point, and every call goes to
        # -BaseUrl, one of the servers above, which this test started on 127.0.0.1.
        out = run_scenarios(run_ps, shell, with_creds, [
            ("full", f"{base} {url(plain)}"), ("secure-cookie", f"{base} {url(secure)}"),
            ("logout-broken", f"{base} {url(broken)}"), ("me-blind", f"{base} {url(blind)}")],
            offline=False)
        logouts_after_full = list(plain.RequestHandlerClass.logouts)
        out.update(run_scenarios(run_ps, shell, without, [("no-credentials", f"{base} {url(plain)}")],
                                 offline=False))
        quoted = make_root(tmp_path_factory.mktemp("e2e"), QUOTED_ENV)
        out.update(run_scenarios(run_ps, shell, quoted / "deploy" / "installer" / "smoke-test.ps1",
                                 [("quoted-password", f"{base} {url(strict)}")], offline=False))
        out["logouts"] = {"full": logouts_after_full,
                          "secure-cookie": list(secure.RequestHandlerClass.logouts),
                          "no-credentials": plain.RequestHandlerClass.logouts[len(logouts_after_full):]}
        return out
    finally:
        plain.shutdown()
        secure.shutdown()
        broken.shutdown()
        blind.shutdown()
        strict.shutdown()


# A quoted password with a hash and a space in it, then a trailing comment, and an earlier line
# that the last one overrides: what compose hands the gateway is `p w#1`. The smoke test used to log
# in with `"p w#1"  # set at install` (first match, quotes and comment kept), got a 401, and blamed
# an auth DB that had not migrated.
QUOTED_PASSWORD = "p w#1"
QUOTED_ENV = ('PLATFORM_ADMIN_USER=owner-one\nPLATFORM_ADMIN_PASSWORD=old\n'
              'PLATFORM_ADMIN_PASSWORD="p w#1"  # set at install\n')


def test_a_quoted_password_logs_in_with_the_value_compose_hands_the_gateway(e2e_stage):
    code, out = e2e_stage["quoted-password"]
    assert "login as 'owner-one' succeeded" in out, out
    assert code == 0, out


def test_e2e_with_every_check_run_passes_with_exit_0(e2e_stage):
    """Positive control: a real login, every authenticated check run and passing."""
    code, out = e2e_stage["full"]
    assert code == 0, out
    assert "rails served: alpha" in out and "alpha : remoteEntry.js served" in out
    assert "0 skipped" in out and "SMOKE TEST PASSED" in out


def test_a_secure_cookie_over_http_reports_the_unrun_checks_and_exits_2(e2e_stage):
    """The Secure-cookie early return was loud on screen and silent in the exit code: the run
    ended SMOKE TEST PASSED with three of the four advertised e2e checks never run."""
    code, out = e2e_stage["secure-cookie"]
    assert code == 2, out
    assert out.count("  [SKIP] ") == 3, out
    assert "3 skipped, 0 failed" in out and "SMOKE TEST INCOMPLETE" in out
    assert "SMOKE TEST PASSED" not in out


def test_absent_credentials_report_all_four_unrun_checks_and_exit_2(e2e_stage):
    code, out = e2e_stage["no-credentials"]
    assert code == 2, out
    assert out.count("  [SKIP] ") == 4, out
    assert "SMOKE TEST INCOMPLETE" in out



# --- runtime stage -----------------------------------------------------------------------------

# Every CLI the runtime stage reaches, plus the two cmdlets that would touch the box for real:
# Invoke-WebRequest (the published-port probe) and Start-Sleep (3 s of waiting per run).
RUNTIME_STUBS = r"""
$ErrorActionPreference = 'Continue'
$global:calls = New-Object System.Collections.Generic.List[string]
$global:portUp = $false
function global:Get-Argv($a) { @(foreach ($x in $a) { foreach ($y in @($x)) { [string]$y } }) }
function global:Fake-Cli($cli, $a) {
  $line = ($a -join ' ')
  $global:calls.Add("$cli $line")
  $global:LASTEXITCODE = 0
  if ($line -match 'echo container-ok') { 'container-ok'; return }
  if ($line -match 'getent hosts') { '192.168.65.254  host.docker.internal'; return }
  if ($line -match 'wget') { '{"ok":true}'; return }
  if ($a[0] -eq 'run' -and $a -contains '-d') { $global:portUp = $true; 'c0ffee'; return }
  if ($a[0] -eq 'rm') { $global:portUp = $false; return }
  if ($line -match '(^| )run --rm probe$') { 'bind-mount-ok'; return }
  if ($a[0] -eq '--version') { "$cli version 5.6.0"; return }
  if ($a[0] -eq 'version') { 'Docker Compose version v2.40.0'; return }
  if ($a[0] -eq 'machine' -and $a[1] -eq 'list') { 'podman-machine-default  hyperv  now  4  8GiB'; return }
}
function global:docker { Fake-Cli 'docker' (Get-Argv $args) }
function global:podman { Fake-Cli 'podman' (Get-Argv $args) }
function global:docker-compose { Fake-Cli 'docker-compose' (Get-Argv $args) }
function global:wsl.exe { Fake-Cli 'wsl.exe' (Get-Argv $args) }
function global:Invoke-WebRequest { [CmdletBinding()] param([Parameter(Position = 0)]$Uri, $TimeoutSec, [switch]$UseBasicParsing)
  if ($global:portUp -and "$Uri" -like '*:51111*') { return [pscustomobject]@{ Content = 'port-ok!'; StatusCode = 200 } }
  throw "connection refused: $Uri" }
function global:Start-Sleep { [CmdletBinding()] param([Parameter(Position = 0)]$Seconds) }
function global:Get-VMMemory { [CmdletBinding()] param([string]$VMName) throw 'no Hyper-V VM here' }
"""


@pytest.fixture(scope="module")
def runtime_stage(run_ps, shell, tmp_path_factory):
    root = make_root(tmp_path_factory.mktemp("rt"), "PLATFORM_ENABLED_APPS=terminal-fun\n")
    script = root / "deploy" / "installer" / "smoke-test.ps1"
    return run_scenarios(run_ps, shell, script,
                         [("desktop", "-Stage runtime -RuntimeMode desktop"),
                          ("podman", "-Stage runtime -RuntimeMode podman")], stubs=RUNTIME_STUBS)


def test_the_published_port_is_checked_on_docker_desktop_too(runtime_stage):
    """`run -p` is the same question on every runtime; asked on podman only, a desktop runtime
    stage passed without it and said nothing."""
    code, out = runtime_stage["desktop"]
    assert code == 0, out
    assert "published host port :51111 reaches the container" in out
    calls = out.split("CALLS=", 1)[1]
    assert "docker run -d --rm --name smoke-port-" in calls and "docker rm -f smoke-port-" in calls, calls


def test_podman_only_checks_are_named_once_as_na_on_desktop_without_failing_the_run(runtime_stage):
    code, out = runtime_stage["desktop"]
    assert code == 0, out
    assert out.count("  [n/a]  ") == 1 and "podman-only, nothing to check on desktop" in out, out
    assert "0 skipped, 0 failed, 1 n/a" in out and "SMOKE TEST PASSED" in out, out


def test_the_podman_runtime_stage_runs_its_own_checks_and_no_na(runtime_stage):
    code, out = runtime_stage["podman"]
    assert code == 0, out
    assert "[n/a]" not in out and "machine provider is hyperv" in out, out
    assert "podman run -d --rm --name smoke-port-" in out.split("CALLS=", 1)[1]


def test_e2e_logs_out_the_session_it_opened_and_proves_it_is_revoked(e2e_stage):
    """Every run used to leave a live admin session row behind (7 days each)."""
    code, out = e2e_stage["full"]
    assert e2e_stage["logouts"]["full"] == ["session=s1"], e2e_stage["logouts"]
    assert "logged out: the session is revoked (/api/platform/me 200 before, 401 after)" in out, out


def test_the_secure_cookie_early_return_logs_out_too(e2e_stage):
    """The web session will not send a Secure cookie over plain HTTP, so the cookie goes by hand."""
    assert e2e_stage["logouts"]["secure-cookie"] == ["session=s1"], e2e_stage["logouts"]
    assert "logged out: the session is revoked" in e2e_stage["secure-cookie"][1]


def test_no_login_means_no_logout(e2e_stage):
    assert e2e_stage["logouts"]["no-credentials"] == []


def test_a_logout_that_does_not_revoke_the_session_fails_the_run(e2e_stage):
    code, out = e2e_stage["logout-broken"]
    assert code == 1, out
    assert "logout did not revoke it" in out


def test_a_401_after_logout_proves_nothing_unless_the_cookie_worked_before(e2e_stage):
    """A replay that never carried the cookie gets 401 after logout too. Without asking /me before
    logout, that read as "revoked" (it did, under 5.1, until the replay was fixed)."""
    code, out = e2e_stage["me-blind"]
    assert "revocation is unproven" in out and "logged out: the session is revoked" not in out, out


# --- persistence stage -------------------------------------------------------------------------

PERSIST_STUBS = r"""
$ErrorActionPreference = 'Continue'
$global:calls = New-Object System.Collections.Generic.List[string]
function global:Get-Argv($a) { @(foreach ($x in $a) { foreach ($y in @($x)) { [string]$y } }) }
function global:Fake-Engine($cli, $a) {
  if ($cli -eq 'wsl.exe' -and $a[0] -eq 'docker') { $a = @($a | Select-Object -Skip 1) }
  $global:calls.Add("$cli " + ($a -join ' '))
  $global:LASTEXITCODE = 0
  if ($a[0] -eq 'ps') { 'platform-gateway-1'; 'platform-caddy-1'; return }
  if ($a[0] -eq 'inspect') {
    '/platform-gateway-1 always'
    if ($global:scenario -eq 'desktop-no-restart') { '/platform-caddy-1 no' } else { '/platform-caddy-1 unless-stopped' }
    return
  }
  if ($a[0] -eq 'machine' -and $a[1] -eq 'list') { 'podman-machine-default  hyperv  now'; return }
  if ($a[0] -eq 'machine' -and $a[1] -eq 'ssh') { 'enabled'; return }
}
function global:docker { Fake-Engine 'docker' (Get-Argv $args) }
function global:podman { Fake-Engine 'podman' (Get-Argv $args) }
function global:wsl.exe { Fake-Engine 'wsl.exe' (Get-Argv $args) }
function global:Get-ChildItem { [CmdletBinding()] param([Parameter(Position = 0)]$Path, $Filter)
  foreach ($n in @($global:startupItems)) { if ($n) { [pscustomobject]@{ Name = $n } } } }
function global:Get-Process { [CmdletBinding()] param($Name) }
"""


@pytest.fixture(scope="module")
def persistence_stage(run_ps, shell, tmp_path_factory):
    base = tmp_path_factory.mktemp("persist")
    appdata = {}
    for name, auto in (("on", True), ("off", False)):
        d = base / f"appdata-{name}" / "Docker"
        d.mkdir(parents=True)
        (d / "settings-store.json").write_text(json.dumps({"AutoStart": auto, "Other": 1}), encoding="utf-8")
        appdata[name] = d.parent
    root = make_root(base, "PLATFORM_ENABLED_APPS=terminal-fun\n")
    script = root / "deploy" / "installer" / "smoke-test.ps1"
    scenarios = [  # name, runtime, Docker Desktop settings, logon items
        ("desktop-ok", "desktop", "on", []),
        ("desktop-no-restart", "desktop", "on", []),
        ("desktop-no-autostart", "desktop", "off", []),
        ("podman-ok", "podman", "on", ["AI-Platform startup.lnk"]),
        ("wsl-ok", "wsl", "on", ["AI-Platform WSL keep-alive.lnk"]),
    ]
    body = PERSIST_STUBS
    for name, mode, settings, items in scenarios:
        items_ps = ", ".join(q(i) for i in items) or "$null"
        body += f"""
$global:scenario = '{name}'
$global:startupItems = @({items_ps})
$env:APPDATA = {q(appdata[settings])}
$global:calls.Clear()
Write-Output '<<<{name}>>>'
& {q(script)} -Stage persistence -RuntimeMode {mode} *>&1 | ForEach-Object {{ [string]$_ }}
Write-Output "EXIT=$LASTEXITCODE"
Write-Output ("CALLS=" + ($global:calls -join ' | '))
"""
    r = run_ps(shell, body)
    assert r.returncode == 0, r.stdout + r.stderr
    out = {}
    for chunk in r.stdout.split("<<<")[1:]:
        name, _, text = chunk.partition(">>>")
        out[name] = (int(text.rsplit("EXIT=", 1)[1].split()[0]), text)
    return out


def test_desktop_persistence_checks_what_a_desktop_box_can_answer(persistence_stage):
    """It used to check podman's shortcut and service only, then fail on a desktop box for the
    missing podman shortcut and warn about the WSL processes Docker Desktop itself runs on."""
    code, out = persistence_stage["desktop-ok"]
    assert code == 0, out
    assert "Docker Desktop starts at sign-in" in out and "the docker engine answers" in out
    assert "2 platform containers running" in out
    assert "every platform container restarts with the engine (2 checked)" in out
    calls = out.split("CALLS=", 1)[1]
    assert "docker ps --format {{.Names}}" in calls and "docker inspect --format" in calls, calls
    assert out.count("  [n/a]  ") == 1 and "1 n/a" in out and "SMOKE TEST PASSED" in out, out


def test_a_container_that_will_not_restart_with_the_engine_fails_on_desktop(persistence_stage):
    code, out = persistence_stage["desktop-no-restart"]
    assert code == 1, out
    assert "do not restart with the engine" in out and "platform-caddy-1" in out


def test_docker_desktop_not_starting_at_sign_in_fails(persistence_stage):
    code, out = persistence_stage["desktop-no-autostart"]
    assert code == 1, out
    assert "Docker Desktop does not start at sign-in" in out


def test_podman_persistence_keeps_its_own_checks_and_no_na(persistence_stage):
    code, out = persistence_stage["podman-ok"]
    assert code == 0, out
    assert "[n/a]" not in out and "podman-restart.service is enabled" in out
    assert "logon startup item present: AI-Platform startup.lnk" in out
    assert "podman ps --format {{.Names}}" in out.split("CALLS=", 1)[1]


def test_wsl_persistence_wants_its_keep_alive_not_the_absence_of_one(persistence_stage):
    code, out = persistence_stage["wsl-ok"]
    assert code == 0, out
    assert "WSL keep-alive present" in out and "still installed" not in out
    assert out.count("  [n/a]  ") == 1


# --- data stage: the volumes a box must have are the ones its rails use ----------------------

# A box with the gateway's volume and terminal-fun's, as a lean terminal-fun-only install has.
DATA_STUBS = r"""
$ErrorActionPreference = 'Continue'
$global:calls = New-Object System.Collections.Generic.List[string]
function global:Get-Argv($a) { @(foreach ($x in $a) { foreach ($y in @($x)) { [string]$y } }) }
function global:docker {
  $a = Get-Argv $args
  $global:calls.Add('docker ' + ($a -join ' '))
  $global:LASTEXITCODE = 0
  if ($a[0] -eq 'volume' -and $a[1] -eq 'ls') { 'platform_gateway_data'; 'platform_terminal_fun_data'; return }
  if ($a[0] -eq 'run' -and $a -contains 'python') { 'ok'; return }
  if ($a[0] -eq 'run' -and $a -contains '10001') { 'WRITABLE'; return }
  if ($a[0] -eq 'run') { '3 1024'; return }
}
function global:podman { $global:calls.Add('podman ' + ((Get-Argv $args) -join ' ')) }
function global:docker-compose { $global:calls.Add('docker-compose ' + ((Get-Argv $args) -join ' ')) }
function global:wsl.exe { $global:calls.Add('wsl.exe ' + ((Get-Argv $args) -join ' ')) }
"""

# name -> (deploy\.env, extra arguments, expected exit)
DATA_SCENARIOS = {
    "lean-terminal-fun-only": ("PLATFORM_ENABLED_APPS=terminal-fun\n", "", 0),
    "recipe-book-enabled": ("PLATFORM_ENABLED_APPS=terminal-fun,recipe-book\n", "", 1),
    "recipe-book-disabled": ("PLATFORM_DISABLED_APPS=recipe-book\n", "-FullStack", 0),
    "full-stack-every-rail": ("", "-FullStack", 1),
}


@pytest.fixture(scope="module")
def data_stage(run_ps, shell, tmp_path_factory):
    base = tmp_path_factory.mktemp("data")
    backup = base / "no-backup"            # no manifest: the comparison is a [warn], not a fail
    backup.mkdir()
    body = DATA_STUBS
    for name, (env, extra, _) in DATA_SCENARIOS.items():
        root = make_root(base / name, env)
        body += f"""
$global:calls.Clear()
Write-Output '<<<{name}>>>'
& {q(root / 'deploy' / 'installer' / 'smoke-test.ps1')} -Stage data -RuntimeMode desktop -BackupDir {q(backup)} {extra} *>&1 |
  ForEach-Object {{ [string]$_ }}
Write-Output "EXIT=$LASTEXITCODE"
"""
    r = run_ps(shell, body)
    assert r.returncode == 0, r.stdout + r.stderr
    out = {}
    for chunk in r.stdout.split("<<<")[1:]:
        name, _, text = chunk.partition(">>>")
        out[name] = (int(text.rsplit("EXIT=", 1)[1].split()[0]), text)
    assert set(out) == set(DATA_SCENARIOS), r.stdout + r.stderr
    return out


def test_a_lean_terminal_fun_box_passes_the_data_stage_without_recipe_books_volume(data_stage):
    code, out = data_stage["lean-terminal-fun-only"]
    assert code == 0, out
    assert "required volume present: platform_gateway_data" in out, out
    assert "MISSING required volume" not in out, out
    assert "[n/a]  platform_recipe_book_data: recipe-book does not run on this box" in out, out


@pytest.mark.parametrize("name", ["recipe-book-enabled", "full-stack-every-rail"])
def test_a_box_that_runs_recipe_book_still_requires_its_volume(data_stage, name):
    """The positive control: the requirement is scoped, not dropped."""
    code, out = data_stage[name]
    assert code == 1, out
    assert "MISSING required volume: platform_recipe_book_data" in out, out


def test_a_disabled_recipe_book_needs_no_volume(data_stage):
    code, out = data_stage["recipe-book-disabled"]
    assert code == 0 and "MISSING required volume" not in out, out
