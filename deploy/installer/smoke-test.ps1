<#
  AI-Platform smoke test - the phase gate for a container-runtime migration and a general
  post-install health check. The repo had no automated coverage of the container stack at all
  (the pytest suites are all in-process), so this is it.

  Stages (run one, or -Stage all):
    runtime      the container runtime itself: CLI, machine/daemon, host reachability,
                 published ports, and Windows-path bind mounts through compose
    data         the persisted volumes: presence, contents vs the backup manifest, SQLite
                 integrity, and file ownership under rootless user-namespace remapping
    build        every image in the installed stack builds / is present
    e2e          the platform as the user sees it: login, rail list, rail APIs, broker round trip
    persistence  restart survival: startup shortcut, machine + containers back up, no stray WSL

  Usage:
    powershell -ExecutionPolicy Bypass -File deploy\installer\smoke-test.ps1 -Stage runtime
    powershell -ExecutionPolicy Bypass -File deploy\installer\smoke-test.ps1 -Stage all
    ... -Stage data -BackupDir C:\Users\me\ai-platform-migration

  Exit code is 0 only when every check in the selected stage(s) passed.
#>
[CmdletBinding()]
param(
  [ValidateSet('runtime', 'data', 'build', 'e2e', 'persistence', 'all')]
  [string]$Stage = 'all',
  [ValidateSet('', 'podman', 'desktop', 'wsl')]
  [string]$RuntimeMode,
  # Phase-0 backup dir (holds manifest.json + volumes\*.tar) for the `data` stage.
  [string]$BackupDir = (Join-Path $env:USERPROFILE 'ai-platform-migration'),
  [string]$BaseUrl = 'http://localhost:1111'
)

$ErrorActionPreference = 'Continue'

$Installer = $PSScriptRoot
$Root      = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$TempBase  = Join-Path $env:USERPROFILE 'AppData\Local\Temp'
$LogDir    = Join-Path $Root 'deploy\logs'
try { New-Item -ItemType Directory -Force -Path $LogDir -ErrorAction Stop | Out-Null } catch {}
$LogFile   = Join-Path $LogDir 'smoke-test.log'

foreach ($dir in @((Join-Path $env:LOCALAPPDATA 'Programs\Podman'), (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links'))) {
  if ((Test-Path $dir) -and ($env:Path -notlike "*$dir*")) { $env:Path = "$dir;$env:Path" }
}

function Write-Log($m) { Add-Content -Path $LogFile -Value ("{0}  {1}" -f (Get-Date -Format 'HH:mm:ss'), $m) -Encoding utf8 }

. (Join-Path $PSScriptRoot 'lib-runtime.ps1')

if (-not $RuntimeMode) {
  $RuntimeMode = if (Get-Command podman -ErrorAction SilentlyContinue) { 'podman' }
                 elseif (Get-Command docker -ErrorAction SilentlyContinue) { 'desktop' }
                 else { 'wsl' }
}

$script:pass = 0; $script:fail = 0; $script:warn = 0
function Hdr($t)  { Write-Host ''; Write-Host "== $t ==" -ForegroundColor Cyan; Write-Log "== $t ==" }
function Ok($m)   { Write-Host "  [OK]   $m" -ForegroundColor Green;  $script:pass++; Write-Log "OK   $m" }
function Bad($m)  { Write-Host "  [FAIL] $m" -ForegroundColor Red;    $script:fail++; Write-Log "FAIL $m" }
function Warn($m) { Write-Host "  [warn] $m" -ForegroundColor Yellow; $script:warn++; Write-Log "warn $m" }

# Run a container with the selected runtime and return its stdout.
function Invoke-InContainer {
  param([string[]]$Arguments)
  switch ($RuntimeMode) {
    'podman'  { return (& podman @(@('run', '--rm') + $Arguments) 2>&1) }
    'wsl'     { return (& wsl.exe @(@('docker', 'run', '--rm') + $Arguments) 2>&1) }
    default   { return (& docker @(@('run', '--rm') + $Arguments) 2>&1) }
  }
}
function Get-RuntimeCli { switch ($RuntimeMode) { 'podman' { 'podman' } 'wsl' { 'wsl' } default { 'docker' } } }

$Probe = $PodmanProbeImage   # docker.io/library/alpine:latest

# ---------------------------------------------------------------------------
function Test-StageRuntime {
  Hdr "runtime ($RuntimeMode)"

  if ($RuntimeMode -eq 'podman') {
    $v = & podman --version 2>&1
    if ($LASTEXITCODE -eq 0) { Ok "podman CLI: $($v -join ' ')" } else { Bad 'podman CLI not runnable'; return }

    $state = Get-PodmanMachineState
    if ($state -eq 'running') {
      Ok 'podman machine is running'
      # Reboot-survival check: the machine can be up NOW and still be unable to boot at next logon,
      # because Hyper-V must reserve the whole startup allocation before the VM starts. A startup
      # reservation larger than typical free RAM is exactly how the platform goes missing after a
      # restart with nothing but a Hyper-V error code to show for it.
      if (Get-Command Get-VMMemory -ErrorAction SilentlyContinue) {
        try {
          $m = Get-VMMemory -VMName $PodmanMachine -ErrorAction Stop
          $startupMb = [int]($m.Startup / 1MB); $maxMb = [int]($m.Maximum / 1MB)
          $totalMb = [int]((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1MB)
          if ($m.DynamicMemoryEnabled -and $startupMb -le 2048) {
            Ok "machine memory: ${startupMb} MB startup / ${maxMb} MB max (boots under memory pressure)"
          } elseif ($startupMb -gt [int]($totalMb / 4)) {
            Warn ("machine reserves ${startupMb} MB at startup on a ${totalMb} MB box - it may fail to " +
                  "boot at logon. Lower it: Set-VMMemory -VMName $PodmanMachine -StartupBytes 2GB " +
                  "-MinimumBytes 512MB -MaximumBytes ${maxMb}MB")
          } else {
            Ok "machine memory: ${startupMb} MB startup / ${maxMb} MB max"
          }
        } catch {}
      }
    }
    else {
      $advice = Get-PodmanMachineMemoryAdvice
      Bad "podman machine state = $state (run: podman machine start)"
      if ($advice) { Warn $advice }
      return
    }

    $prov = (& podman machine inspect --format '{{.ConfigDir.Path}}' 2>$null)
    $rows = (& podman machine list 2>$null) -join ' '
    if ($rows -match 'hyperv') { Ok 'machine provider is hyperv (WSL is out of the runtime path)' }
    elseif ($rows -match 'wsl') { Warn 'machine provider is WSL - the WSL2 layer is still in the runtime path' }

    $cv = & docker-compose version 2>&1
    if ($LASTEXITCODE -eq 0) { Ok "compose provider: $(($cv -join ' ').Trim())" } else { Bad 'docker-compose.exe not runnable' }
  }
  else {
    $cli = Get-RuntimeCli
    Ok "runtime CLI: $cli (mode $RuntimeMode)"
  }

  # containers actually run
  $out = Invoke-InContainer @($Probe, 'echo', 'container-ok')
  if ($out -match 'container-ok') { Ok 'a container runs and returns stdout' } else { Bad "container run failed: $($out -join ' ')" }

  # --- host reachability: the native broker + Ollama ------------------------
  # Podman machine injects host.docker.internal / host.containers.internal itself; gvproxy runs ON
  # Windows and dials the target over the host's own loopback, so this must work even for a service
  # bound to 127.0.0.1 only and needs no inbound firewall rule.
  $hosts = Invoke-InContainer @($Probe, 'getent', 'hosts', 'host.docker.internal')
  if ($hosts -match '^\s*(\d+\.\d+\.\d+\.\d+)') {
    $ip = $Matches[1]
    Ok "host.docker.internal resolves inside a container -> $ip"
  } else { Bad "host.docker.internal does not resolve inside a container: $($hosts -join ' ')" }

  foreach ($p in @(@{n = 'broker'; port = 11500; path = '/healthz' }, @{n = 'ollama'; port = 11434; path = '/api/version' })) {
    $u = "http://host.docker.internal:$($p.port)$($p.path)"
    $r = Invoke-InContainer @('--entrypoint', '', $Probe, 'sh', '-c', "wget -qO- --timeout=5 $u 2>/dev/null || echo UNREACHABLE")
    if ($r -match 'UNREACHABLE' -or -not $r) { Bad "container -> native $($p.n) on :$($p.port) UNREACHABLE" }
    else { Ok "container -> native $($p.n) on :$($p.port) reachable" }
  }

  # --- published ports ------------------------------------------------------
  # Rootless podman can only publish >1024 on the host; the stack only publishes 1111, so this is a
  # direct check of the one port that matters.
  $probePort = 51111
  $cli = Get-RuntimeCli
  $name = "smoke-port-$PID"
  if ($RuntimeMode -eq 'podman') {
    # `caddy respond` is a real HTTP server in one flag-set, and the caddy image is already local
    # (the stack runs it), so this costs no extra pull. Two earlier forms of this probe were broken:
    #
    #   1. `--entrypoint ''` written INLINE is dropped by PowerShell (splatted in an array, as the
    #      Invoke-InContainer calls above do, it survives). podman then read the image name as
    #      --entrypoint's value and treated `sh` as the image -> "docker://sh:latest access denied".
    #   2. The replacement used busybox `httpd`, which Alpine's busybox does not build in
    #      ("sh: httpd: not found", exit 127) -- and `nc -q` is likewise unsupported.
    #
    # Both failed as "published host port not reachable", blaming the runtime for a probe bug.
    & podman run -d --rm --name $name -p "${probePort}:8000" `
        docker.io/library/caddy:2 caddy respond --listen :8000 --body 'port-ok!' 2>&1 | Out-Null
    Start-Sleep 3
    try {
      $resp = Invoke-WebRequest "http://localhost:$probePort" -TimeoutSec 5 -UseBasicParsing
      if ($resp.Content -match 'port-ok') { Ok "published host port :$probePort reaches the container" } else { Bad 'published port answered unexpectedly' }
    } catch { Bad "published host port :$probePort not reachable: $($_.Exception.Message)" }
    & podman rm -f $name 2>&1 | Out-Null
  }

  # --- THE big one: Windows-path bind mounts through the compose API --------
  # docker-compose.exe resolves relative binds to absolute WINDOWS paths and sends them over the
  # Docker-compat pipe. If podman does not translate them into the machine, every bind mount in the
  # stack (the Caddyfile, the co-worker inbox) silently breaks.
  $bindDir = Join-Path $TempBase "ai-platform-smoke-bind-$PID"
  New-Item -ItemType Directory -Force -Path $bindDir | Out-Null
  $sentinel = 'bind-mount-ok'
  [IO.File]::WriteAllText((Join-Path $bindDir 'probe.txt'), $sentinel)
  $composeText = @"
name: aiplatform-smoke
services:
  probe:
    image: $Probe
    entrypoint: ["sh","-c","cat /probe/probe.txt"]
    volumes:
      - "$($bindDir -replace '\\','\\'):/probe:ro"
"@
  $composeFile = Join-Path $bindDir 'compose.yml'
  [IO.File]::WriteAllText($composeFile, $composeText, (New-Object System.Text.UTF8Encoding($false)))
  $cargs = @('-f', $composeFile, 'run', '--rm', 'probe')
  $bindOut = switch ($RuntimeMode) {
    'podman' { & docker-compose @cargs 2>&1 }
    'wsl'    { & wsl.exe @(@('docker', 'compose') + @('-f', (ConvertTo-WslPath $composeFile), 'run', '--rm', 'probe')) 2>&1 }
    default  { & docker @(@('compose') + $cargs) 2>&1 }
  }
  if ($bindOut -match $sentinel) { Ok 'Windows-path bind mount works through compose (no in-VM path rewrite needed)' }
  else { Bad "Windows-path bind mount through compose FAILED - use in-VM paths for binds. Output: $(($bindOut -join ' ').Trim())" }
  switch ($RuntimeMode) {
    'podman' { & docker-compose -f $composeFile down -v 2>&1 | Out-Null }
    default  {}
  }
  Remove-Item $bindDir -Recurse -Force -ErrorAction SilentlyContinue
}

# ---------------------------------------------------------------------------
function Test-StageData {
  Hdr 'data (persisted volumes)'
  $manifestPath = Join-Path $BackupDir 'manifest.json'
  $expected = $null
  if (Test-Path $manifestPath) { $expected = (Get-Content $manifestPath -Raw | ConvertFrom-Json).volumes }
  else { Warn "no manifest at $manifestPath - contents cannot be compared to the pre-migration state" }

  $vols = switch ($RuntimeMode) {
    'podman' { @(& podman volume ls --format '{{.Name}}' 2>$null) }
    'wsl'    { @(& wsl.exe docker volume ls -q 2>$null) }
    default  { @(& docker volume ls --format '{{.Name}}' 2>$null) }
  }
  $vols = @($vols | Where-Object { $_ -match '\S' })
  if ($vols.Count -eq 0) { Bad 'no volumes found'; return }
  Ok "volumes present: $($vols -join ', ')"

  # The auth DB is the one whose loss the user would notice immediately (re-login, lost entitlements).
  foreach ($must in @('platform_gateway_data', 'platform_recipe_book_data')) {
    if ($vols -contains $must) { Ok "required volume present: $must" } else { Bad "MISSING required volume: $must" }
  }

  foreach ($v in $vols) {
    $stat = Invoke-InContainer @('-v', "${v}:/vol:ro", '--entrypoint', '', $Probe, 'sh', '-c', 'printf "%s %s" "$(find /vol -type f | wc -l)" "$(du -sb /vol | cut -f1)"')
    if ($stat -match '^\s*(\d+)\s+(\d+)') {
      $files = [int]$Matches[1]; $bytes = [long]$Matches[2]
      if ($expected -and $expected.PSObject.Properties.Name -contains $v) {
        $e = $expected.$v
        if ($files -eq $e.files -and $bytes -eq $e.bytes) { Ok "${v}: $files files / $bytes bytes (matches manifest)" }
        else { Bad "${v}: $files files / $bytes bytes but manifest says $($e.files) / $($e.bytes)" }
      }
      else { Ok "${v}: $files files / $bytes bytes" }
    }
    else { Bad "${v}: could not stat contents" }
  }

  # SQLite integrity, checked inside a container so no host python/sqlite is needed.
  $dbs = @(@{ vol = 'platform_gateway_data'; file = '/vol/platform.db' }, @{ vol = 'platform_recipe_book_data'; file = '/vol/recipe_book.db' })
  foreach ($d in $dbs) {
    if ($vols -notcontains $d.vol) { continue }
    $py = "import sqlite3;c=sqlite3.connect('file:$($d.file)?mode=ro',uri=True);print(c.execute('PRAGMA integrity_check').fetchone()[0])"
    $r = Invoke-InContainer @('-v', "$($d.vol):/vol:ro", 'docker.io/library/python:3.11-slim', 'python', '-c', $py)
    if ($r -match '\bok\b') { Ok "$($d.vol): $(Split-Path $d.file -Leaf) integrity_check ok" }
    else { Bad "$($d.vol): integrity_check did NOT return ok: $($r -join ' ')" }
  }

  # Ownership: the co-worker / terminal-fun images run as uid 10001 and need to WRITE their volume.
  # Rootless user-namespace remapping is exactly where an imported volume goes wrong.
  foreach ($v in @('platform_co_worker_inbox', 'platform_terminal_fun_data')) {
    if ($vols -notcontains $v) { continue }
    $w = Invoke-InContainer @('-v', "${v}:/vol", '--user', '10001', '--entrypoint', '', $Probe, 'sh', '-c', 'touch /vol/.smoke-write && rm /vol/.smoke-write && echo WRITABLE')
    if ($w -match 'WRITABLE') { Ok "$v is writable as uid 10001" } else { Bad "$v NOT writable as uid 10001: $($w -join ' ')" }
  }
}

# ---------------------------------------------------------------------------
function Test-StageBuild {
  Hdr 'build (stack images)'
  $envFile = Join-Path $Root 'deploy\.env'
  if (-not (Test-Path $envFile)) { Bad 'no deploy\.env'; return }
  $apps = ''
  foreach ($l in Get-Content $envFile) { if ($l -match '^\s*PLATFORM_ENABLED_APPS\s*=\s*(.*)$') { $apps = $Matches[1].Trim() } }
  Ok "enabled apps: $apps"

  $paths = Get-ComposePaths -Mode $RuntimeMode
  $cargs = @('--env-file', $paths.Env, '-f', $paths.Compose)
  $cargs += Get-ComposeProfiles -Apps $apps
  $cargs += @('config', '--services')
  $services = Invoke-Compose -Arguments $cargs -Mode $RuntimeMode
  $services = @($services | Where-Object { $_ -match '^[a-z0-9-]+$' })
  if ($services.Count -gt 0) { Ok "compose resolves $($services.Count) services: $($services -join ', ')" }
  else { Bad 'compose could not resolve the service list (config error?)'; return }

  $images = switch ($RuntimeMode) {
    'podman' { @(& podman images --format '{{.Repository}}:{{.Tag}}' 2>$null) }
    'wsl'    { @(& wsl.exe docker images --format '{{.Repository}}' 2>$null) }
    default  { @(& docker images --format '{{.Repository}}:{{.Tag}}' 2>$null) }
  }
  foreach ($want in @('platform-gateway-bundled', 'terminal-fun-backend')) {
    if (($images -join ' ') -match [regex]::Escape($want)) { Ok "image built: $want" } else { Bad "image MISSING: $want" }
  }
}

# ---------------------------------------------------------------------------
function Test-StageE2E {
  Hdr 'e2e (the platform as the user sees it)'

  # Deliberately localhost, not platform.localhost: a managed browser routes *.localhost through the
  # corporate proxy and refuses the connection.
  try {
    $h = Invoke-WebRequest "$BaseUrl/api/platform/healthz" -TimeoutSec 10 -UseBasicParsing
    if ($h.StatusCode -eq 200) { Ok "gateway healthz 200 at $BaseUrl" } else { Bad "healthz returned $($h.StatusCode)" }
  } catch { Bad "gateway not answering at $BaseUrl : $($_.Exception.Message)"; return }

  try {
    $shell = Invoke-WebRequest $BaseUrl -TimeoutSec 10 -UseBasicParsing
    if ($shell.Content -match '<div id="root"|<script') { Ok 'shell SPA is served' } else { Warn 'shell HTML looks unexpected' }
  } catch { Bad "shell not served: $($_.Exception.Message)" }

  # Login with the CURRENT credentials from .env. Success proves the imported auth DB (users,
  # argon2 hashes, sessions, entitlements) survived the migration - the single most user-visible bit.
  $envFile = Join-Path $Root 'deploy\.env'
  $u = ''; $p = ''
  foreach ($l in Get-Content $envFile -ErrorAction SilentlyContinue) {
    if ($l -match '^\s*PLATFORM_ADMIN_USER\s*=\s*(.*)$')     { $u = $Matches[1].Trim() }
    if ($l -match '^\s*PLATFORM_ADMIN_PASSWORD\s*=\s*(.*)$') { $p = $Matches[1].Trim() }
  }
  if (-not $u -or -not $p) { Warn 'no admin credentials in deploy\.env - skipping the login check'; return }

  $session = $null
  try {
    $body = @{ username = $u; password = $p } | ConvertTo-Json
    $r = Invoke-WebRequest "$BaseUrl/api/platform/login" -Method POST -Body $body -ContentType 'application/json' `
           -TimeoutSec 15 -UseBasicParsing -SessionVariable session
    if ($r.StatusCode -eq 200) { Ok "login as '$u' succeeded (auth DB intact)" } else { Bad "login returned $($r.StatusCode)" }
  } catch { Bad "login FAILED as '$u' - the auth DB may not have migrated: $($_.Exception.Message)"; return }

  try {
    $apps = (Invoke-WebRequest "$BaseUrl/api/platform/apps" -WebSession $session -TimeoutSec 10 -UseBasicParsing).Content | ConvertFrom-Json
    $ids = @($apps | ForEach-Object { $_.id })
    if ($ids.Count -gt 0) { Ok "rails served: $($ids -join ', ')" } else { Bad 'no rails returned by /api/platform/apps' }
    # Each rail's static bundle must be present, or the rail renders blank with no error at all.
    foreach ($id in $ids) {
      try {
        $re = Invoke-WebRequest "$BaseUrl/$id/assets/remoteEntry.js" -WebSession $session -TimeoutSec 10 -UseBasicParsing
        if ($re.StatusCode -eq 200) { Ok "$id : remoteEntry.js served" } else { Bad "$id : remoteEntry.js $($re.StatusCode)" }
      } catch { Bad "$id : remoteEntry.js not served - the rail will render blank ($($_.Exception.Message))" }
    }
  } catch { Bad "/api/platform/apps failed: $($_.Exception.Message)" }

  # Broker reachability THROUGH the gateway proves the container -> native-host path end to end.
  try {
    $st = Invoke-WebRequest "$BaseUrl/api/platform/status" -WebSession $session -TimeoutSec 20 -UseBasicParsing
    if ($st.StatusCode -eq 200) { Ok 'gateway -> native broker /status 200 (host.docker.internal works)' }
  } catch { Bad "gateway -> broker failed: $($_.Exception.Message)" }
  try {
    $m = Invoke-WebRequest "$BaseUrl/api/platform/models" -WebSession $session -TimeoutSec 30 -UseBasicParsing
    if ($m.StatusCode -eq 200) { Ok 'model pool listed through the broker' }
  } catch { Warn "model list failed (broker up but Ollama may be down): $($_.Exception.Message)" }
}

# ---------------------------------------------------------------------------
function Test-StagePersistence {
  Hdr 'persistence (restart survival)'

  $startup = [Environment]::GetFolderPath('Startup')
  $lnks = @(Get-ChildItem $startup -Filter '*.lnk' -ErrorAction SilentlyContinue)
  $ai = @($lnks | Where-Object { $_.Name -match 'AI-Platform' })
  if ($ai.Count -gt 0) { Ok "logon startup item present: $($ai.Name -join ', ')" } else { Bad 'no AI-Platform startup item - the stack will not come back after a reboot' }
  $wslLnk = @($ai | Where-Object { $_.Name -match 'WSL' })
  if ($wslLnk.Count -gt 0) { Bad "a WSL keep-alive shortcut is still installed: $($wslLnk.Name -join ', ')" }
  else { Ok 'no WSL keep-alive shortcut' }

  if ($RuntimeMode -eq 'podman') {
    if ((Get-PodmanMachineState) -eq 'running') { Ok 'podman machine is running' } else { Bad 'podman machine is not running' }
    $r = & podman machine ssh 'systemctl is-enabled podman-restart' 2>&1
    if ($r -match 'enabled') { Ok 'podman-restart.service is enabled (containers return when the VM boots)' }
    else { Bad "podman-restart.service is not enabled: $($r -join ' ')" }
    $up = @(& podman ps --format '{{.Names}}' 2>$null | Where-Object { $_ -match 'platform-' })
    if ($up.Count -gt 0) { Ok "$($up.Count) platform containers running: $($up -join ', ')" } else { Bad 'no platform-* containers running' }
  }

  # WSL should be neither running nor required.
  $vm = @(Get-Process -Name 'vmmem*', 'wsl' -ErrorAction SilentlyContinue)
  if ($vm.Count -eq 0) { Ok 'no WSL VM processes running' } else { Warn "WSL processes present: $(($vm | ForEach-Object { $_.Name }) -join ', ')" }
}

# ---------------------------------------------------------------------------
Write-Host ''
Write-Host "AI-Platform smoke test - stage: $Stage, runtime: $RuntimeMode" -ForegroundColor White
Write-Log "=== smoke test start (stage=$Stage runtime=$RuntimeMode) ==="

switch ($Stage) {
  'runtime'     { Test-StageRuntime }
  'data'        { Test-StageData }
  'build'       { Test-StageBuild }
  'e2e'         { Test-StageE2E }
  'persistence' { Test-StagePersistence }
  'all'         { Test-StageRuntime; Test-StageData; Test-StageBuild; Test-StageE2E; Test-StagePersistence }
}

Write-Host ''
$summary = "$script:pass passed, $script:warn warnings, $script:fail failed"
Write-Log $summary
if ($script:fail -gt 0) { Write-Host "SMOKE TEST FAILED - $summary" -ForegroundColor Red; exit 1 }
Write-Host "SMOKE TEST PASSED - $summary" -ForegroundColor Green
exit 0
