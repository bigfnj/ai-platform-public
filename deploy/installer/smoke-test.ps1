<#
  AI-Platform smoke test - the phase gate for a container-runtime migration and a general
  post-install health check. The repo had no automated coverage of the container stack at all
  (the pytest suites are all in-process), so this is it.

  Stages (run one, or -Stage all):
    runtime      the container runtime itself: CLI, machine/daemon, host reachability,
                 published ports, and Windows-path bind mounts through compose
    data         the persisted volumes: presence, contents vs the backup manifest, SQLite
                 integrity, and file ownership under rootless user-namespace remapping.
                 The manifest is a migration SNAPSHOT, not an invariant: a lost FILE fails,
                 byte drift on a live volume does not. Re-record it with -Rebaseline.
                 With NO manifest the comparison is a [warn], not a [SKIP], deliberately: most
                 boxes never had a migration backup, and counting it would hold every one of
                 them at exit 2 for ever. Changing that is the owner's call.
    build        every image in the installed stack builds / is present
    e2e          the platform as the user sees it: login, rail list, rail APIs, broker round trip
    persistence  restart survival, per runtime. podman: the logon shortcut, the machine, the
                 podman-restart service, no stray WSL. Docker Desktop: it starts at sign-in and
                 every platform container restarts with the engine. wsl: the keep-alive. All of
                 them: the engine answers and the platform containers are running.

  Usage:
    powershell -ExecutionPolicy Bypass -File deploy\installer\smoke-test.ps1 -Stage runtime
    powershell -ExecutionPolicy Bypass -File deploy\installer\smoke-test.ps1 -Stage all
    ... -Stage data -BackupDir C:\Users\me\ai-platform-migration
    ... -Rebaseline                     # rewrite manifest.json from the live volumes, then exit
    ... -Stage e2e -FullStack           # -BaseUrl defaults to http://localhost:1111; keep it

  -BaseUrl is where the e2e stage reaches the gateway, and the default is the right target.
  Caddy publishes only 1111:80 in both compose files, so nothing listens on 443 and an https://
  URL here reaches nothing (.NET cannot resolve *.localhost names either). Over plain HTTP to a
  loopback host the gateway issues a non-Secure session cookie even when
  PLATFORM_COOKIE_SECURE=true, so the authenticated checks run there in full.

  WHICH RUNTIME: -RuntimeMode if given; else PLATFORM_RUNTIME in deploy\.env (the installer writes
  it); else the runtime whose CLI lists platform-gateway-1, falling back to the first CLI installed.
  A declaration is followed even when another runtime lists the gateway running: that is printed
  as a [warn] naming both, so a wrong line is visible without the run guessing past it.

  [n/a] marks a check that cannot apply to this runtime at all (the podman machine's checks on
  Docker Desktop or WSL). It is printed once and does not move the exit code: a desktop box can
  never run them, so counting them would make every desktop run exit 2.

  Exit codes:
    0  every check in the selected stage(s) ran and passed
    1  at least one check FAILED
    2  nothing failed, but at least one check did NOT RUN (each is printed as [SKIP] with the
       reason). A stage that returns early used to print a yellow note and still exit 0, so a run
       with three of e2e's four checks unrun was indistinguishable from a full pass by exit code.
#>
[CmdletBinding()]
param(
  [ValidateSet('runtime', 'data', 'build', 'e2e', 'persistence', 'all')]
  [string]$Stage = 'all',
  # Normally detected from the runtime whose CLI lists the platform's containers; this overrides.
  [ValidateSet('', 'podman', 'desktop', 'wsl')]
  [Alias('Runtime')]
  [string]$RuntimeMode,
  # Phase-0 backup dir (holds manifest.json + volumes\*.tar) for the `data` stage.
  [string]$BackupDir = (Join-Path $env:USERPROFILE 'ai-platform-migration'),
  [string]$BaseUrl = 'http://localhost:1111',
  # Target the FULL stack (deploy/docker-compose.yml) instead of the lean installer compose.
  # This box runs the full stack, so without this the build stage would check a compose file that
  # is not what is actually deployed and report a green that means nothing.
  [switch]$FullStack,
  # Rewrite the data stage's manifest.json from what is on the volumes RIGHT NOW, then exit.
  # The baseline is a migration snapshot; a platform in use moves away from it legitimately, and
  # without a way to re-record it the only options are a permanent red or deleting the file.
  [switch]$Rebaseline
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
# deploy\logs' rotated copies, the services' too, pruned to the newest ten of each (what the
# installers' no-watchdog warning says this script does; Step-ScriptLog prunes only its own log's).
$null = Remove-RotatedServiceLogs -LogDir $LogDir
# smoke-test.log grows by every run: rotated at a size, rotated copies pruned (Step-ScriptLog).
$null = Step-ScriptLog -Path $LogFile

# Detected from the runtime that actually LISTS the platform's containers, not from which CLI is
# installed: "podman on PATH" picked podman on a Docker Desktop box that merely had podman beside
# it, and every stage then tested an empty, stopped machine. -RuntimeMode stays the override.
$RuntimeWhy = 'from -RuntimeMode'; $RuntimeWarning = ''
if (-not $RuntimeMode) {
  $pick = Resolve-PlatformRuntime -EnvFile (Join-Path $Root 'deploy\.env')
  $RuntimeMode = $pick.Mode
  $RuntimeWhy = $pick.Reason
  $RuntimeWarning = $pick.Warning
}

$script:pass = 0; $script:fail = 0; $script:warn = 0; $script:skip = 0; $script:na = 0
function Hdr($t)  { Write-Host ''; Write-Host "== $t ==" -ForegroundColor Cyan; Write-Log "== $t ==" }
function Ok($m)   { Write-Host "  [OK]   $m" -ForegroundColor Green;  $script:pass++; Write-Log "OK   $m" }
function Bad($m)  { Write-Host "  [FAIL] $m" -ForegroundColor Red;    $script:fail++; Write-Log "FAIL $m" }
function Warn($m) { Write-Host "  [warn] $m" -ForegroundColor Yellow; $script:warn++; Write-Log "warn $m" }
# A check that did not run. Not a Warn: a warning is advice about a check that DID run, and it
# leaves the exit code alone; a skipped check moves the exit code to 2.
function Skip($m) { Write-Host "  [SKIP] $m" -ForegroundColor Yellow; $script:skip++; Write-Log "SKIP $m" }
# A check that cannot apply to this runtime. Said, so the run does not look like it covered it,
# and not counted as a skip, because no run on this runtime could ever do it.
function NA($m)   { Write-Host "  [n/a]  $m" -ForegroundColor DarkGray; $script:na++; Write-Log "n/a  $m" }

# Before any stage runs a container. A ~/.docker/config.json that is a DIRECTORY fails every
# `podman run` with exit 125, which read here as 26 failed data checks; this run reads an empty
# config of ours instead (Repair-DockerConfigEnv has already logged it, so only shown and counted).
$dockerConfig = Repair-DockerConfigEnv
if ($dockerConfig) { Write-Host "  [warn] $($dockerConfig.Warning)" -ForegroundColor Yellow; $script:warn++ }

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

# Does a port published from a container reach the host? The stack publishes only 1111, so this is
# a direct check of the one port that matters. Rootless podman can only publish >1024, which is
# why it was written for podman; but `run -p` is the same question on Docker Desktop and in WSL,
# and it used to be asked on podman only, so a desktop runtime stage passed without it, silently.
function Test-PublishedPort {
  $probePort = 51111
  $name = "smoke-port-$PID"
  # `caddy respond` is a real HTTP server in one flag-set, and the caddy image is already local
  # (the stack runs it), so this costs no extra pull. Two earlier forms of this probe were broken:
  #
  #   1. `--entrypoint ''` is dropped by PowerShell when it calls a native exe -- INCLUDING
  #      when splatted from an array, which an earlier fix here believed was safe. The runtime
  #      then reads the image name as --entrypoint's value and treats `sh` as the image ->
  #      "sh:latest access denied". Every --entrypoint '' in this file has been removed: the
  #      probe image (alpine) declares no entrypoint, so there was never anything to clear,
  #      and the whole `data` stage failed on all 23 volumes because of it.
  #   2. The replacement used busybox `httpd`, which Alpine's busybox does not build in
  #      ("sh: httpd: not found", exit 127) -- and `nc -q` is likewise unsupported.
  #
  # Both failed as "published host port not reachable", blaming the runtime for a probe bug.
  $null = Invoke-VolumeCli -Mode $RuntimeMode -CliArgs @('run', '-d', '--rm', '--name', $name,
            '-p', "${probePort}:8000", 'docker.io/library/caddy:2', 'caddy', 'respond',
            '--listen', ':8000', '--body', 'port-ok!')
  Start-Sleep 3
  try {
    $resp = Invoke-WebRequest "http://localhost:$probePort" -TimeoutSec 5 -UseBasicParsing
    if ($resp.Content -match 'port-ok') { Ok "published host port :$probePort reaches the container" } else { Bad 'published port answered unexpectedly' }
  } catch { Bad "published host port :$probePort not reachable: $($_.Exception.Message)" }
  $null = Invoke-VolumeCli -Mode $RuntimeMode -CliArgs @('rm', '-f', $name)
}

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
          } elseif (-not $m.DynamicMemoryEnabled -and $startupMb -gt [int]($totalMb / 4)) {
            # The lowering command below needs Dynamic Memory; on a static VM it can only fail.
            Warn ("machine is a STATIC ${startupMb} MB VM on a ${totalMb} MB box - it may fail to boot " +
                  "when RAM is busy (startup then unloads Ollama models and retries once). " +
                  (Get-StaticMemoryFix -VmName $PodmanMachine -StartupMb $startupMb))
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

    $rows = (& podman machine list 2>$null) -join ' '
    if ($rows -match 'hyperv') { Ok 'machine provider is hyperv (WSL is out of the runtime path)' }
    elseif ($rows -match 'wsl') { Warn 'machine provider is WSL - the WSL2 layer is still in the runtime path' }

    $cv = & docker-compose version 2>&1
    if ($LASTEXITCODE -eq 0) { Ok "compose provider: $(($cv -join ' ').Trim())" } else { Bad 'docker-compose.exe not runnable' }
  }
  else {
    $cli = Get-RuntimeCli
    Ok "runtime CLI: $cli (mode $RuntimeMode)"
    NA ("podman machine state, its Hyper-V memory reservation, machine provider and docker-compose.exe: " +
        "podman-only, nothing to check on $RuntimeMode")
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
    $r = Invoke-InContainer @($Probe, 'sh', '-c', "wget -qO- --timeout=5 $u 2>/dev/null || echo UNREACHABLE")
    if ($r -match 'UNREACHABLE' -or -not $r) { Bad "container -> native $($p.n) on :$($p.port) UNREACHABLE" }
    else { Ok "container -> native $($p.n) on :$($p.port) reachable" }
  }

  # --- published ports ------------------------------------------------------
  Test-PublishedPort

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

# Does this box run a rail? Read as the gateway is handed the lists: PLATFORM_ENABLED_APPS minus
# PLATFORM_DISABLED_APPS (Get-DeclaredAppList, the gateway's parser). A blank ENABLED list is the
# stack's own default: every rail on the full stack, and the lean compose's `:-` default for its
# gateway (terminal-fun). A list the gateway would refuse is judged strictly: the rail runs.
function Test-BoxRunsRail([string]$Rail) {
  $envFile = Join-Path $Root 'deploy\.env'
  try {
    $on  = @(Get-DeclaredAppList -Key 'PLATFORM_ENABLED_APPS' -EnvFile $envFile)
    $off = @(Get-DeclaredAppList -Key 'PLATFORM_DISABLED_APPS' -EnvFile $envFile)
  } catch { return $true }
  if ($off -contains $Rail) { return $false }
  if ($on.Count) { return ($on -contains $Rail) }
  if ($FullStack) { return $true }
  $lean = Join-Path $Installer 'docker-compose.installer.yml'
  if (-not (Test-Path -LiteralPath $lean)) { return $true }
  $m = [regex]::Match([IO.File]::ReadAllText($lean), 'PLATFORM_ENABLED_APPS:\s*\$\{PLATFORM_ENABLED_APPS:-([^}]*)\}')
  if (-not $m.Success) { return $true }
  return (@($m.Groups[1].Value -split ',' | ForEach-Object { $_.Trim() }) -contains $Rail)
}

# ---------------------------------------------------------------------------
function Test-StageData {
  Hdr 'data (persisted volumes)'
  $manifestPath = Join-Path $BackupDir 'manifest.json'
  $expected = $null
  $ageDays = 0
  if (Test-Path $manifestPath) {
    $expected = (Get-Content $manifestPath -Raw | ConvertFrom-Json).volumes
    $ageDays = [int]((Get-Date) - (Get-Item $manifestPath).LastWriteTime).TotalDays
    if ($ageDays -ge 7) {
      Warn "manifest is $ageDays days old - byte totals below are compared loosely (see the note in this stage)"
    }
  }
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
  # Recipe Book's is required only where that rail runs: a lean terminal-fun box never has it, and
  # requiring it failed every data stage there.
  $required = @('platform_gateway_data')
  if (Test-BoxRunsRail 'recipe-book') { $required += 'platform_recipe_book_data' }
  else { NA 'platform_recipe_book_data: recipe-book does not run on this box (PLATFORM_ENABLED_APPS / PLATFORM_DISABLED_APPS)' }
  foreach ($must in $required) {
    if ($vols -contains $must) { Ok "required volume present: $must" } else { Bad "MISSING required volume: $must" }
  }

  foreach ($v in $vols) {
    # No embedded double quotes: PowerShell mangles them on the way to a native exe, so the
    # printf form this used to carry arrived at sh in pieces and produced nothing at all --
    # which read as "could not stat contents" for every volume, on every runtime.
    $stat = Invoke-InContainer @('-v', "${v}:/vol:ro", $Probe, 'sh', '-c', 'echo $(find /vol -type f | wc -l) $(du -sb /vol | cut -f1)')
    if ($stat -match '^\s*(\d+)\s+(\d+)') {
      $files = [int]$Matches[1]; $bytes = [long]$Matches[2]
      if ($expected -and $expected.PSObject.Properties.Name -contains $v) {
        # The manifest is a migration SNAPSHOT, not an invariant. A platform in use moves away
        # from it legitimately: SQLite grows and vacuums, caddy rotates its own state. Asserting
        # exact bytes against an aging baseline fails forever and teaches everyone to ignore the
        # stage - it reported 4 red on a box where every file count matched exactly, including
        # 1758/1758 for the recipe corpus, purely from 13 days of normal drift.
        #
        # So the FILE COUNT is the load-bearing signal and only a DROP is data loss. Byte totals
        # move on their own and are compared with a tolerance; a large shrink with no files lost
        # is odd enough to mention and not enough to fail on.
        $e = $expected.$v
        $slack = [Math]::Max([long]($e.bytes * 0.25), 4MB)
        if ($files -lt $e.files) {
          Bad "${v}: $files files, manifest recorded $($e.files) - $($e.files - $files) FILES ARE GONE"
        }
        elseif ($files -gt $e.files) {
          Ok "${v}: $files files / $bytes bytes (+$($files - $e.files) since the manifest; the platform has been used)"
        }
        elseif ($bytes -lt ($e.bytes - $slack)) {
          Warn "${v}: $files files (all present) but $bytes bytes vs the manifest's $($e.bytes) - contents shrank without losing a file"
        }
        else { Ok "${v}: $files files / $bytes bytes (matches manifest)" }
      }
      else { Ok "${v}: $files files / $bytes bytes" }
    }
    else { Bad "${v}: could not stat contents" }
  }

  # SQLite integrity, checked inside a container so no host python/sqlite is needed.
  $dbs = @(@{ vol = 'platform_gateway_data'; file = '/vol/platform.db' }, @{ vol = 'platform_recipe_book_data'; file = '/vol/recipe_book.db' })
  foreach ($d in $dbs) {
    if ($vols -notcontains $d.vol) { continue }
    # immutable=1, not mode=ro: a WAL-mode database still wants to create its -shm/-wal
    # sidecars even when opened read-only, which a :ro mount refuses -- so recipe_book.db
    # reported "unable to open database file" while being perfectly intact.
    $py = "import sqlite3;c=sqlite3.connect('file:$($d.file)?immutable=1',uri=True);print(c.execute('PRAGMA integrity_check').fetchone()[0])"
    $r = Invoke-InContainer @('-v', "$($d.vol):/vol:ro", 'docker.io/library/python:3.11-slim', 'python', '-c', $py)
    if ($r -match '\bok\b') { Ok "$($d.vol): $(Split-Path $d.file -Leaf) integrity_check ok" }
    else { Bad "$($d.vol): integrity_check did NOT return ok: $($r -join ' ')" }
  }

  # Ownership: the co-worker / terminal-fun images run as uid 10001 and need to WRITE their volume.
  # Rootless user-namespace remapping is exactly where an imported volume goes wrong.
  foreach ($v in @('platform_co_worker_inbox', 'platform_terminal_fun_data')) {
    if ($vols -notcontains $v) { continue }
    $w = Invoke-InContainer @('-v', "${v}:/vol", '--user', '10001', $Probe, 'sh', '-c', 'touch /vol/.smoke-write && rm /vol/.smoke-write && echo WRITABLE')
    if ($w -match 'WRITABLE') { Ok "$v is writable as uid 10001" } else { Bad "$v NOT writable as uid 10001: $($w -join ' ')" }
  }
}

# ---------------------------------------------------------------------------
function Test-StageBuild {
  Hdr 'build (stack images)'
  $envFile = Join-Path $Root 'deploy\.env'
  if (-not (Test-Path $envFile)) { Bad 'no deploy\.env'; return }
  $apps = ([string](Get-DotEnvValue -Path $envFile -Key 'PLATFORM_ENABLED_APPS')).Trim()
  # Absent is legitimate: the full stack sets no override and takes GatewaySettings' default (every
  # rail). Printing an empty string there reads like a fault, so name what is actually happening.
  if ($apps) { Ok "enabled apps: $apps" }
  else { Ok 'enabled apps: (no PLATFORM_ENABLED_APPS override; gateway default = every rail)' }

  $paths = Get-ComposePaths -Mode $RuntimeMode
  $composeFile = if ($FullStack) { Join-Path $Root 'deploy\docker-compose.yml' } else { $paths.Compose }
  Ok ("compose file: " + (Split-Path $composeFile -Leaf) + $(if ($FullStack) { ' (full stack)' } else { ' (lean installer)' }))
  # The box-local override is written against the lean file's services, so it rides only with that.
  # Checking the lean stack WITHOUT it would assert a stack startup never actually brings up.
  $fileArgs = if ($FullStack) { @('-f', $composeFile) } else { $paths.FileArgs }
  if (-not $FullStack -and $paths.Files.Count -gt 1) { Ok ("compose override: " + $paths.Files[-1]) }
  $cargs = @('--env-file', $paths.Env) + $fileArgs
  # The full compose declares no profiles; passing one there is harmless but pointless.
  if (-not $FullStack) { $cargs += Get-ComposeProfiles -Apps $apps }
  $cargs += @('config', '--services')
  $services = Invoke-Compose -Arguments $cargs -Mode $RuntimeMode
  $services = @($services | Where-Object { $_ -match '^[a-z0-9-]+$' })
  if ($services.Count -gt 0) { Ok "compose resolves $($services.Count) services: $($services -join ', ')" }
  else { Bad 'compose could not resolve the service list (config error?)'; return }

  # Ask compose which IMAGE each service uses rather than hardcoding a name list: a hardcoded list
  # silently stops covering every rail added after it was written, which is the failure mode this
  # whole script exists to catch. caddy and other pulled images are skipped -- only locally built
  # images (the ones a broken build would leave missing) are asserted.
  $cfgArgs = @('--env-file', $paths.Env) + $fileArgs
  if (-not $FullStack) { $cfgArgs += Get-ComposeProfiles -Apps $apps }
  $cfgArgs += @('config')
  # -NoLog: this is the full resolved render, so it holds every secret from --env-file.
  # Only the `image:` lines are wanted; nothing below needs it in the log.
  $rendered = (Invoke-Compose -Arguments $cfgArgs -Mode $RuntimeMode -NoLog) -join "`n"
  $wanted = @([regex]::Matches($rendered, '(?m)^\s*image:\s*([^\s]+)') |
              ForEach-Object { $_.Groups[1].Value } |
              Where-Object { $_ -notmatch '^(docker\.io/)?(library/)?caddy' } | Sort-Object -Unique)
  # This is a SECOND compose invocation, so the services guard above does not cover it. Without
  # this, a render that failed or came back empty ran the loop below zero times and the stage
  # passed on the strength of the three Oks above, having asserted no image at all.
  if ($wanted.Count -eq 0) {
    Bad "compose config rendered no built image to check ($($services.Count) services resolved, 0 image: lines) - the image assertions did not run"
    return
  }

  $images = switch ($RuntimeMode) {
    'podman' { @(& podman images --format '{{.Repository}}:{{.Tag}}' 2>$null) }
    'wsl'    { @(& wsl.exe docker images --format '{{.Repository}}' 2>$null) }
    default  { @(& docker images --format '{{.Repository}}:{{.Tag}}' 2>$null) }
  }
  $haystack = $images -join ' '
  foreach ($want in $wanted) {
    $bare = ($want -replace ':latest$', '')
    if ($haystack -match [regex]::Escape($bare)) { Ok "image built: $want" } else { Bad "image MISSING: $want" }
  }
}

# ---------------------------------------------------------------------------
# The e2e stage, and the logout every exit path after a login owes. Each run used to log in as the
# admin and leave the session row behind, live for the session TTL (7 days), one per run. The
# finally covers every return below, the Secure-cookie early return included.
function Test-StageE2E {
  $script:e2eLogin = $null
  try { Invoke-E2EChecks }
  finally { if ($script:e2eLogin) { Close-E2ESession $script:e2eLogin } }
}

# Log out the session a successful login opened, then prove it is gone.
#
# The cookie is replayed from the login's Set-Cookie through a fresh web session holding it as a
# NON-Secure cookie for this host: the login's own session will not send a Secure cookie over
# plain HTTP, and that path logs in too. Not a `Cookie:` header, which Windows PowerShell 5.1
# drops in favour of the (empty) cookie container. And /me is asked BEFORE logout as well:
# without that positive control, a replay that never carried the cookie would get a 401 after
# logout too, and "revoked" would be proved by a request that was never authenticated.
function Close-E2ESession($login) {
  $pair = ''
  foreach ($c in @($login.Headers['Set-Cookie'])) {
    if ([string]$c -match '^\s*([^=;\s]+)=([^;]*)') { $pair = @($Matches[1], $Matches[2]); break }
  }
  if (-not $pair) { Warn 'logged in, but no session cookie came back to log out with; it lives until it expires'; return }
  $ws = New-Object Microsoft.PowerShell.Commands.WebRequestSession
  $ws.Cookies.Add((New-Object System.Net.Cookie($pair[0], $pair[1], '/', ([Uri]$BaseUrl).Host)))
  $meCode = {
    try { [int](Invoke-WebRequest "$BaseUrl/api/platform/me" -WebSession $ws -TimeoutSec 10 -UseBasicParsing).StatusCode }
    catch { $c = 0; try { $c = [int]$_.Exception.Response.StatusCode } catch {}; $c }
  }
  $before = & $meCode
  try {
    $null = Invoke-WebRequest "$BaseUrl/api/platform/logout" -Method POST -WebSession $ws -TimeoutSec 10 -UseBasicParsing
  } catch { Warn "could not log out ($($_.Exception.Message)); the session lives until it expires"; return }
  $after = & $meCode
  if ($before -ne 200) { Warn "logged out, but revocation is unproven: the replayed cookie got $before from /me even before logout" }
  elseif ($after -eq 401) { Ok 'logged out: the session is revoked (/api/platform/me 200 before, 401 after)' }
  else { Bad "logged out, but the session still answers /api/platform/me ($after): logout did not revoke it" }
}

function Invoke-E2EChecks {
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

  # The checks that need a session, named once so every early exit below can count exactly
  # which ones did not run instead of returning as if they had passed.
  $authChecks = @('rail list (/api/platform/apps)', 'rail bundles (remoteEntry.js per rail)',
                  'broker round trip (/api/platform/status, /api/platform/models)')

  # Login with the CURRENT credentials from .env. Success proves the imported auth DB (users,
  # argon2 hashes, sessions, entitlements) survived the migration - the single most user-visible bit.
  # Read-DotEnvFile, so these are the values compose hands the gateway: a quoted password loses its
  # quotes, an unquoted one its trailing comment, and the last line wins. Read raw, a quoted
  # password went to the login WITH its quotes, got a 401, and was reported as an auth DB that had
  # not migrated. Read as ANSI under 5.1, a non-ASCII one failed the same way.
  $envFile = Join-Path $Root 'deploy\.env'
  $u = ''; $p = ''
  try {
    $u = [string](Get-DotEnvValue -Path $envFile -Key 'PLATFORM_ADMIN_USER')
    $p = [string](Get-DotEnvValue -Path $envFile -Key 'PLATFORM_ADMIN_PASSWORD')
  } catch { Bad "deploy\.env cannot be read as compose reads it: $_"; return }
  if (-not $u -or -not $p) {
    Skip 'login (auth DB intact): no PLATFORM_ADMIN_USER / PLATFORM_ADMIN_PASSWORD in deploy\.env'
    foreach ($c in $authChecks) { Skip "${c}: needs a login" }
    return
  }

  $session = $null
  try {
    $body = @{ username = $u; password = $p } | ConvertTo-Json
    $r = Invoke-WebRequest "$BaseUrl/api/platform/login" -Method POST -Body $body -ContentType 'application/json' `
           -TimeoutSec 15 -UseBasicParsing -SessionVariable session
    if ($r.StatusCode -eq 200) { Ok "login as '$u' succeeded (auth DB intact)" } else { Bad "login returned $($r.StatusCode)" }
    $script:e2eLogin = $r          # Test-StageE2E logs it out, whichever way this function returns

    # A Secure cookie is not returned over plain HTTP, so every authenticated check below would
    # 401 — on a box that is behaving exactly as configured (PLATFORM_COOKIE_SECURE=true is right
    # when HTTPS is terminated upstream, e.g. a Cloudflare tunnel). Failing here would red-flag a
    # working security setting, so say what is happening and stop rather than cry wolf.
    # The gateway drops Secure for plain HTTP to a LOOPBACK host (_cookie_secure_for), so on the
    # default http://localhost:1111 this never fires; it fires for a non-loopback HTTP host such
    # as a LAN address. Do not send the operator to HTTPS: Caddy publishes only 1111:80 here.
    $setCookie = [string]$r.Headers['Set-Cookie']
    if ($setCookie -match 'Secure' -and $BaseUrl -like 'http://*') {
      Warn 'session cookie is Secure but this URL is plain HTTP, so it will not be sent back.'
      Warn '  Not a fault: PLATFORM_COOKIE_SECURE=true, and the gateway relaxes it only for a'
      Warn '  loopback host. Re-run with -BaseUrl http://localhost:1111 for the authenticated checks.'
      foreach ($c in $authChecks) { Skip "${c}: the Secure session cookie is not sent over plain HTTP" }
      return
    }
  } catch { Bad "login FAILED as '$u' - the auth DB may not have migrated: $($_.Exception.Message)"; return }

  try {
    $body = (Invoke-WebRequest "$BaseUrl/api/platform/apps" -WebSession $session -TimeoutSec 10 -UseBasicParsing).Content | ConvertFrom-Json
    # The endpoint returns {apps: [...], user: {...}} — iterating the WRAPPER yields no ids and
    # reports a healthy platform as "no rails", which is how this check shipped upstream.
    $apps = if ($null -ne $body.apps) { $body.apps } else { $body }
    # 'soon' entries are catalog placeholders with no backend or bundle; only assert real ones.
    $ids = @($apps | Where-Object { $_.status -ne 'soon' } | ForEach-Object { $_.id })
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
# Docker Desktop's "start when you sign in" setting: $true, $false, or $null when it cannot be read.
# Newer releases keep it in settings-store.json as AutoStart, older ones in settings.json as
# autoStart; property names compare case-insensitively here, so one lookup covers both.
function Get-DockerDesktopAutoStart {
  foreach ($f in @('settings-store.json', 'settings.json')) {
    $p = Join-Path $env:APPDATA "Docker\$f"
    if (-not (Test-Path -LiteralPath $p)) { continue }
    try { $s = Get-Content -LiteralPath $p -Raw | ConvertFrom-Json } catch { continue }
    if ($s.PSObject.Properties.Name -contains 'AutoStart') { return [bool]$s.AutoStart }
  }
  return $null
}

# Will the stack come back after a reboot? The question is the same on every runtime; the parts
# that answer it are not. This stage used to ask only podman's parts and say nothing on Docker
# Desktop or WSL, where it then failed for a missing podman shortcut and warned about the WSL
# processes Docker Desktop itself runs on. Each runtime now gets its own answer, and what can
# never apply to it is named once as [n/a].
function Test-StagePersistence {
  Hdr 'persistence (restart survival)'
  $cli = Get-RuntimeCli

  # 1. what brings the runtime back at sign-in
  $startup = [Environment]::GetFolderPath('Startup')
  $lnks = @(Get-ChildItem $startup -Filter '*.lnk' -ErrorAction SilentlyContinue)
  $ai = @($lnks | Where-Object { $_.Name -match 'AI-Platform' })
  $wslLnk = @($ai | Where-Object { $_.Name -match 'WSL' })
  switch ($RuntimeMode) {
    'podman' {
      $item = @($ai | Where-Object { $_.Name -notmatch 'WSL' })
      if ($item.Count -gt 0) { Ok "logon startup item present: $($item.Name -join ', ')" } else { Bad 'no AI-Platform startup item - the stack will not come back after a reboot' }
      if ($wslLnk.Count -gt 0) { Bad "a WSL keep-alive shortcut is still installed: $($wslLnk.Name -join ', ')" }
      else { Ok 'no WSL keep-alive shortcut' }
    }
    'wsl' {
      # WSL2 idles the VM down when nothing holds it, and the stack with it.
      if ($wslLnk.Count -gt 0) { Ok "WSL keep-alive present: $($wslLnk.Name -join ', ')" }
      else { Bad 'no AI-Platform WSL keep-alive - WSL2 idles the VM down, and the stack with it' }
    }
    default {
      $auto = Get-DockerDesktopAutoStart
      if ($auto -eq $true) { Ok 'Docker Desktop starts at sign-in, which brings the engine and the stack back' }
      elseif ($auto -eq $false) { Bad 'Docker Desktop does not start at sign-in - the stack will not come back after a reboot' }
      else { Skip 'Docker Desktop starts at sign-in: no settings-store.json or settings.json under %APPDATA%\Docker to read' }
    }
  }

  # 2. the engine answers
  if ($RuntimeMode -eq 'podman') {
    if ((Get-PodmanMachineState) -eq 'running') { Ok 'podman machine is running' } else { Bad 'podman machine is not running' }
  }
  else {
    $null = Invoke-VolumeCli -Mode $RuntimeMode -CliArgs @('info')
    if ($LASTEXITCODE -eq 0) { Ok "the $cli engine answers" } else { Bad "the $cli engine does not answer (exit $LASTEXITCODE)" }
  }

  # 3. the platform containers are running, on every runtime
  $up = @(Invoke-VolumeCli -Mode $RuntimeMode -CliArgs @('ps', '--format', '{{.Names}}') |
          ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ -match '^platform-' })
  if ($up.Count -gt 0) { Ok "$($up.Count) platform containers running: $($up -join ', ')" } else { Bad 'no platform-* containers running' }

  # 4. they come back when the engine does
  if ($RuntimeMode -eq 'podman') {
    $r = & podman machine ssh 'systemctl is-enabled podman-restart' 2>&1
    if ($r -match 'enabled') { Ok 'podman-restart.service is enabled (containers return when the VM boots)' }
    else { Bad "podman-restart.service is not enabled: $($r -join ' ')" }
  }
  elseif ($up.Count -gt 0) {
    # The engine restarts a container only for restart: always / unless-stopped.
    $pol = @(Invoke-VolumeCli -Mode $RuntimeMode -CliArgs (@('inspect', '--format', '{{.Name}} {{.HostConfig.RestartPolicy.Name}}') + $up) |
             ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ -match '^/?platform-' })
    $stays = @($pol | Where-Object { (-split $_)[-1] -notin @('always', 'unless-stopped') } | ForEach-Object { ((-split $_)[0]).TrimStart('/') })
    if ($pol.Count -eq 0) { Bad "could not read the platform containers' restart policies" }
    elseif ($stays.Count -gt 0) { Bad "these do not restart with the engine (restart policy not always/unless-stopped): $($stays -join ', ')" }
    else { Ok "every platform container restarts with the engine ($($pol.Count) checked)" }
  }

  # 5. WSL out of the runtime path: the point of the podman migration, and nothing else's
  if ($RuntimeMode -eq 'podman') {
    $vm = @(Get-Process -Name 'vmmem*', 'wsl' -ErrorAction SilentlyContinue)
    if ($vm.Count -eq 0) { Ok 'no WSL VM processes running' } else { Warn "WSL processes present: $(($vm | ForEach-Object { $_.Name }) -join ', ')" }
  }
  else {
    NA ("podman-restart.service, the podman logon shortcut and 'no WSL in the runtime path': " +
        "podman-only, nothing to check on $RuntimeMode")
  }
}

# ---------------------------------------------------------------------------
Write-Host ''
Write-Host "AI-Platform smoke test - stage: $Stage, runtime: $RuntimeMode" -ForegroundColor White
Write-Host "  runtime chosen: $RuntimeWhy" -ForegroundColor DarkGray
Write-Log "=== smoke test start (stage=$Stage runtime=$RuntimeMode) ==="
Write-Log "runtime chosen: $RuntimeWhy"
if ($RuntimeWarning) { Warn "runtime: $RuntimeWarning" }

if ($Rebaseline) {
  Hdr 'rebaseline (rewrite the data manifest from the live volumes)'
  $vols = switch ($RuntimeMode) {
    'podman' { @(& podman volume ls --format '{{.Name}}' 2>$null) }
    'wsl'    { @(& wsl.exe docker volume ls -q 2>$null) }
    default  { @(& docker volume ls --format '{{.Name}}' 2>$null) }
  }
  $vols = @($vols | Where-Object { $_ -match '\S' })
  if ($vols.Count -eq 0) { Bad 'no volumes found - nothing to record'; exit 1 }
  $record = [ordered]@{}
  foreach ($v in $vols) {
    $stat = Invoke-InContainer @('-v', "${v}:/vol:ro", $Probe, 'sh', '-c', 'echo $(find /vol -type f | wc -l) $(du -sb /vol | cut -f1)')
    if ($stat -match '^\s*(\d+)\s+(\d+)') {
      $record[$v] = [ordered]@{ files = [int]$Matches[1]; bytes = [long]$Matches[2] }
      Ok "${v}: $($Matches[1]) files / $($Matches[2]) bytes"
    }
    else { Bad "${v}: could not stat contents - refusing to write a partial manifest"; exit 1 }
  }
  New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
  $out = Join-Path $BackupDir 'manifest.json'
  # -Encoding utf8: Set-Content defaults to the ANSI codepage here, and ConvertFrom-Json on the
  # next run would choke on any non-ASCII volume name that produced.
  @{ volumes = $record } | ConvertTo-Json -Depth 5 | Out-File -FilePath $out -Encoding utf8
  Write-Host ''
  Write-Host "wrote $out - this is now the baseline the data stage compares against." -ForegroundColor White
  exit 0
}

switch ($Stage) {
  'runtime'     { Test-StageRuntime }
  'data'        { Test-StageData }
  'build'       { Test-StageBuild }
  'e2e'         { Test-StageE2E }
  'persistence' { Test-StagePersistence }
  'all'         { Test-StageRuntime; Test-StageData; Test-StageBuild; Test-StageE2E; Test-StagePersistence }
}

Write-Host ''
$summary = "$script:pass passed, $script:warn warnings, $script:skip skipped, $script:fail failed"
if ($script:na -gt 0) { $summary += ", $script:na n/a" }
Write-Log $summary
if ($script:fail -gt 0) { Write-Host "SMOKE TEST FAILED - $summary" -ForegroundColor Red; exit 1 }
if ($script:skip -gt 0) {
  Write-Host "SMOKE TEST INCOMPLETE - $summary" -ForegroundColor Yellow
  Write-Host "  $script:skip check(s) did not run (each [SKIP] above says why). Exit 2: not a full pass." -ForegroundColor Yellow
  exit 2
}
Write-Host "SMOKE TEST PASSED - $summary" -ForegroundColor Green
exit 0
