<#
  AI-Platform logon startup (Podman / Docker Desktop on Windows).

  Installed by install.ps1 as a Startup-folder shortcut:
    %APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\AI-Platform startup.lnk
  (Task Scheduler is Access-denied for non-elevated users on managed boxes.)

  What it does, in order:
    1. starts the podman machine if it isn't running
    2. re-detects the container -> Windows host address and rewrites WINDOWS_HOST in deploy/.env
    3. brings the compose stack up (idempotent; recreates only what changed), without the rails
       PLATFORM_DISABLED_APPS names, then parks their containers (restart=no, stopped) so the
       machine's podman-restart does not bring them back either (sync-disabled-rails.ps1)

  This is the Podman counterpart of platform-startup.sh, and it is deliberately SHORTER: a Hyper-V
  podman machine does not idle-shut-down the way WSL2 does, so there is no `sleep infinity`
  keep-alive to hold a VM up. Containers with `restart: always` are brought back inside the machine
  by podman-restart.service (enabled at install time).

  Run by hand to bring the platform up:
    powershell -ExecutionPolicy Bypass -File deploy\installer\platform-startup.ps1

  Check the machine-start memory fallbacks without touching anything (safe with the platform up):
    powershell -ExecutionPolicy Bypass -File deploy\installer\platform-startup.ps1 -SelfTest

  Say which runtime a run would drive, and why, then exit without touching anything:
    powershell -ExecutionPolicy Bypass -File deploy\installer\platform-startup.ps1 -ShowRuntime

  WHICH RUNTIME: -RuntimeMode if given; else PLATFORM_RUNTIME in deploy\.env (the installer writes
  it); else the runtime whose CLI lists platform-gateway-1. A declaration is followed even when
  another runtime lists the gateway running, and the log says so as a WARNING naming both.
#>
[CmdletBinding()]
param(
  # Override the auto-detected runtime; normally inferred from what is installed.
  [ValidateSet('', 'podman', 'desktop')]
  [string]$RuntimeMode,
  # Run the offline self-test of the machine-start memory paths and exit (0 = all passed).
  [switch]$SelfTest,
  # Print the runtime this run would drive and why, then exit before taking the lock.
  [switch]$ShowRuntime
)

$ErrorActionPreference = 'Continue'   # native tools report status on stderr; judge by exit codes

$Installer = $PSScriptRoot
$Root      = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$TempBase  = Join-Path $env:USERPROFILE 'AppData\Local\Temp'
$LogDir    = Join-Path $Root 'deploy\logs'
try { New-Item -ItemType Directory -Force -Path $LogDir -ErrorAction Stop | Out-Null } catch {}
$LogFile   = Join-Path $LogDir 'startup.log'

# Same PATH problem as install.ps1: podman/docker-compose land on the USER PATH, which a session
# started before their install cannot see.
foreach ($dir in @((Join-Path $env:LOCALAPPDATA 'Programs\Podman'), (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links'))) {
  if ((Test-Path $dir) -and ($env:Path -notlike "*$dir*")) { $env:Path = "$dir;$env:Path" }
}

function Write-Log($m) {
  $line = "{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m
  Add-Content -Path $LogFile -Value $line -Encoding utf8
  Write-Host $line
}

. (Join-Path $PSScriptRoot 'lib-runtime.ps1')

# ---------------------------------------------------------------------------
# -SelfTest: the machine-start memory paths, offline, under any account.
#
# Drives the REAL Initialize-PodmanMachine through Hyper-V's memory refusal with every command
# that could touch the box replaced by a function defined in this scope (PowerShell resolves a
# function before a cmdlet or an exe, and a called function sees its caller's). Before any
# scenario runs it checks that each one resolves to a stub, so it cannot start a VM, stop a
# process or unload a real model even if a stub goes missing. Safe on the live box with the
# platform up: it reads nothing from it and writes only a temp file.
function Invoke-StartupSelfTest {
  $fail = 0
  $LogFile = Join-Path ([IO.Path]::GetTempPath()) "ai-startup-selftest-$PID.log"  # Write-NativeLine's
  $st = $null

  function Write-Log($m) { $st.Log.Add([string]$m) }
  function podman {
    $a = (@($args) | ForEach-Object { [string]$_ }) -join ' '
    $st.Calls.Add("podman $a")
    $global:LASTEXITCODE = 0
    if ($a -like 'machine list*') { 'podman-machine-default  hyperv  never  4  12GiB  60GiB'; return }
    if ($a -like 'info*') { if (-not $st.Running) { $global:LASTEXITCODE = 125 }; return }
    if ($a -like 'machine start*') {
      $r = if ($st.Starts -lt $st.Script.Count) { $st.Script[$st.Starts] } else { 'other' }
      $st.Starts++
      if ($r -eq 'ok') { $st.Running = $true; 'Machine "podman-machine-default" started successfully'; return }
      $global:LASTEXITCODE = 125
      if ($r -eq 'nomem') {
        'Error: unable to start host networking: ... Not enough memory in the system to start the virtual machine podman-machine-default with ram size 12288 megabytes. (0x8007000E)'
      } else { 'Error: some unrelated failure' }
      return
    }
  }
  function Get-VMMemory { [CmdletBinding()] param([string]$VMName)
    [pscustomobject]@{ DynamicMemoryEnabled = ($st.Mem -eq 'dynamic'); Startup = 12GB; Maximum = 12GB } }
  function Set-VMMemory { [CmdletBinding()] param([string]$VMName, $StartupBytes, $MinimumBytes, $MaximumBytes)
    $st.Calls.Add('Set-VMMemory')
    if ($st.Mem -eq 'static') { throw 'changing the minimum needs Dynamic Memory' } }
  function Get-VM { [CmdletBinding()] param([string]$Name) [pscustomobject]@{ State = 'Off' } }
  function Stop-VM { [CmdletBinding()] param([string]$Name, [switch]$Force) $st.Calls.Add('Stop-VM') }
  function Get-CimInstance { [CmdletBinding()] param([Parameter(Position = 0)][string]$ClassName)
    [pscustomobject]@{ FreePhysicalMemory = $st.FreeKb } }
  function Get-Process { [CmdletBinding()] param([Parameter(Position = 0)][string[]]$Name) }
  function Stop-Process { [CmdletBinding()] param([int]$Id, [switch]$Force) $st.Calls.Add('Stop-Process') }
  function Start-Sleep { [CmdletBinding()] param([Parameter(Position = 0)][int]$Seconds) }
  function Invoke-RestMethod { [CmdletBinding()]
    param([string]$Uri, [string]$Method = 'Get', [string]$Body, [string]$ContentType, [int]$TimeoutSec)
    $st.Calls.Add("HTTP $Method $Uri $Body")
    if ($st.OllamaDown) { throw 'No connection could be made because the target machine actively refused it' }
    if ($Uri -like '*/api/ps') { return [pscustomobject]@{ models = @($st.Resident | ForEach-Object { [pscustomobject]@{ name = $_ } }) } }
    if ($Uri -like '*/api/generate') {
      $model = ($Body | ConvertFrom-Json).model
      $st.Resident = @($st.Resident | Where-Object { $_ -ne $model })
      $st.FreeKb += 1600MB / 1KB
      return [pscustomobject]@{ model = $model; done = $true; done_reason = 'unload' }
    }
  }

  foreach ($cmd in 'podman', 'Get-VMMemory', 'Set-VMMemory', 'Get-VM', 'Stop-VM', 'Get-CimInstance',
                   'Get-Process', 'Stop-Process', 'Start-Sleep', 'Invoke-RestMethod', 'Write-Log') {
    $c = Get-Command $cmd -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $c -or $c.CommandType -ne 'Function') {
      Write-Host "  FAIL  '$cmd' does not resolve to a self-test stub; refusing to run anything"
      return 1
    }
  }

  function Run($name, $mem, $script, $resident, [switch]$OllamaDown) {
    $st = [pscustomobject]@{
      Mem = $mem; Script = @($script); Starts = 0; Running = $false; Resident = @($resident)
      FreeKb = [long](12.4GB / 1KB); OllamaDown = [bool]$OllamaDown
      Calls = New-Object System.Collections.Generic.List[string]
      Log = New-Object System.Collections.Generic.List[string]
    }
    $ok = Initialize-PodmanMachine -MemoryMb 12288
    [pscustomobject]@{ Name = $name; Ok = [bool]$ok; Starts = $st.Starts; Calls = @($st.Calls)
                       Log = ($st.Log -join "`n"); State = $st }
  }
  function Check($r, $cond, $what) {
    if (-not $cond) {
      Write-Host "  FAIL  [$($r.Name)] $what"
      Write-Host ('        log: ' + ($r.Log -replace "`n", "`n             "))
      Write-Host ('        calls: ' + ($r.Calls -join ' | '))
      $script:selfTestFail++
    }
  }
  $script:selfTestFail = 0
  $unloads = { param($r) @($r.Calls | Where-Object { $_ -like 'HTTP Post */api/generate*' }) }

  # The incident: a static 12 GiB VM, 12.4 GB free, two models resident. Expected: no attempt to
  # lower a reservation that cannot change, both models unloaded with keep_alive 0 and NO prompt,
  # one retry, machine up.
  $r = Run 'static, no memory, two models resident' 'static' @('nomem', 'ok') @('gemma3:4b', 'qwen2.5:3b')
  Check $r $r.Ok 'the machine did not end up running'
  Check $r ($r.Starts -eq 2) "expected 2 start attempts, saw $($r.Starts)"
  Check $r (-not ($r.Calls -contains 'Set-VMMemory')) 'tried to lower the reservation of a STATIC VM'
  Check $r ($r.Log -notmatch 'retrying with a smaller startup reservation') 'announced a reservation retry that cannot work'
  Check $r ($r.Log -match 'STATIC memory') 'did not say the VM has static memory'
  Check $r ($r.Log -match 'DynamicMemoryEnabled') 'did not print the one-time Dynamic Memory fix'
  Check $r ($r.Log -match '-MaximumBytes 12288MB') "the fix does not keep the VM's own 12 GiB ceiling"
  $u = & $unloads $r
  Check $r ($u.Count -eq 2) "expected 2 unload calls, saw $($u.Count)"
  Check $r (@($u | Where-Object { $_ -match '"keep_alive":0' -and $_ -notmatch 'prompt' }).Count -eq 2) 'an unload was not {model, keep_alive: 0} without a prompt'
  Check $r ($r.Log -match 'unloaded 2 Ollama model') 'did not log what it unloaded'

  # Nothing resident to give back: say so, and do not retry a start nothing has changed.
  $r = Run 'static, no memory, nothing resident' 'static' @('nomem', 'ok') @()
  Check $r (-not $r.Ok) 'reported success with nothing freed'
  Check $r ($r.Starts -eq 1) "expected 1 start attempt, saw $($r.Starts)"
  Check $r ($r.Log -match 'no model resident') 'did not say there was nothing to unload'

  # A dynamic VM: the reservation fallback is the cheaper fix and it works; Ollama is left alone.
  $r = Run 'dynamic, no memory, reservation fixes it' 'dynamic' @('nomem', 'ok') @('gemma3:4b')
  Check $r $r.Ok 'the machine did not end up running'
  Check $r ($r.Calls -contains 'Set-VMMemory') 'did not lower the reservation of a dynamic VM'
  Check $r (@($r.Calls | Where-Object { $_ -like 'HTTP*' }).Count -eq 0) 'touched Ollama when the reservation fallback had worked'

  # A dynamic VM still refused after the reservation change: then unload, once.
  $r = Run 'dynamic, still no memory, then unload' 'dynamic' @('nomem', 'nomem', 'ok') @('gemma3:4b')
  Check $r $r.Ok 'the machine did not end up running'
  Check $r ($r.Starts -eq 3) "expected 3 start attempts, saw $($r.Starts)"
  Check $r ((& $unloads $r).Count -eq 1) 'expected exactly one unload'

  # Any other failure: unloading would cost the user a reload and fix nothing.
  $r = Run 'static, unrelated failure' 'static' @('other', 'ok') @('gemma3:4b')
  Check $r (-not $r.Ok) 'reported success on an unrelated failure'
  Check $r (@($r.Calls | Where-Object { $_ -like 'HTTP*' }).Count -eq 0) 'touched Ollama for a failure that was not about memory'
  Check $r ($r.Starts -eq 1) "expected 1 start attempt, saw $($r.Starts)"

  # Ollama down: logged, no crash, no retry.
  $r = Run 'static, no memory, Ollama down' 'static' @('nomem', 'ok') @() -OllamaDown
  Check $r (-not $r.Ok) 'reported success with Ollama unreachable'
  Check $r ($r.Log -match 'could not ask Ollama') 'did not say Ollama was unreachable'

  Remove-Item -LiteralPath $LogFile -Force -ErrorAction SilentlyContinue
  if ($script:selfTestFail -eq 0) { Write-Host '  self-test ok (6 machine-start scenarios: static/dynamic memory, Ollama unload)' }
  return $script:selfTestFail
}

if ($SelfTest) { exit (Invoke-StartupSelfTest) }

# The runtime whose CLI lists the platform's containers, not the first one installed: "podman is on
# PATH" picks podman on a Docker Desktop box that merely has podman beside it, and this script then
# CREATES and starts a podman machine there. -RuntimeMode stays the override.
$RuntimeWhy = 'from -RuntimeMode'; $RuntimeWarning = ''
if (-not $RuntimeMode) {
  $pick = Resolve-PlatformRuntime -EnvFile (Join-Path $Root 'deploy\.env')
  # This script drives podman or Docker Desktop; a WSL box starts with platform-startup.sh.
  $RuntimeMode = if ($pick.Mode -eq 'wsl') { 'desktop' } else { $pick.Mode }
  $RuntimeWhy = $pick.Reason
  $RuntimeWarning = $pick.Warning
}
if ($ShowRuntime) {
  Write-Host "runtime: $RuntimeMode ($RuntimeWhy)"
  if ($RuntimeWarning) { Write-Host "WARNING: $RuntimeWarning" }
  exit 0
}

# Two things can start the platform: the logon Startup shortcut, and the platform-watchdog service
# (which also triggers this script in the user's session). Two concurrent `podman machine start`
# runs fight over \\.\pipe\<machine> and can leave the machine unstartable by ANY account, so
# serialise them. A lock FILE rather than a Global\ mutex: creating a Global object needs a
# privilege a standard user does not have, and these two callers run under different accounts.
$LockFile = Join-Path $LogDir 'startup.lock'

function Enter-StartupLock {
  # 24 x 5 s = 2 min. If the other run has not finished by then it is either done enough that the
  # platform is up, or it is in trouble - and a second concurrent machine start would not help either.
  for ($i = 0; $i -lt 24; $i++) {
    if (Test-Path $LockFile) {
      # Honour a live lock, but drop one whose owner died or wedged - a run that exits early (or is
      # killed) would otherwise deadlock every future startup behind a file nobody owns.
      $stale = $false
      try {
        $info = Get-Content $LockFile -Raw -ErrorAction Stop | ConvertFrom-Json
        if (-not (Get-Process -Id $info.pid -ErrorAction SilentlyContinue)) { $stale = $true }
        elseif (((Get-Date) - [datetime]$info.at).TotalMinutes -gt 10) { $stale = $true }
      } catch { $stale = $true }

      if ($stale) { Remove-Item $LockFile -Force -ErrorAction SilentlyContinue }
      else { Start-Sleep -Seconds 5; continue }
    }
    try {
      # CreateNew + no sharing: whoever wins the race owns the lock and the loser retries.
      $fs = [System.IO.File]::Open($LockFile, 'CreateNew', 'Write', 'None')
      $bytes = [Text.Encoding]::UTF8.GetBytes((@{ pid = $PID; at = (Get-Date).ToString('o') } | ConvertTo-Json -Compress))
      $fs.Write($bytes, 0, $bytes.Length)
      $fs.Close()
      return $true
    } catch { Start-Sleep -Seconds 2 }
  }
  return $false
}

function Exit-StartupLock {
  try {
    $info = Get-Content $LockFile -Raw -ErrorAction Stop | ConvertFrom-Json
    if ($info.pid -eq $PID) { Remove-Item $LockFile -Force -ErrorAction SilentlyContinue }
  } catch {}
}

if (-not (Enter-StartupLock)) {
  Write-Log 'another startup run holds the lock and has not finished; leaving the work to it.'
  exit 0
}
# Every way out releases the lock. The early `exit 1`s used to leave it behind, and so did a
# terminating error (a deploy\.env compose would refuse now throws in Read-DotEnvFile): the next
# logon then honoured it as long as its PID was alive, and Windows reuses PIDs, so a startup could
# wait out the two-minute timeout and leave the platform down. The trap covers the throw; the
# exits go through Stop-Startup.
function Stop-Startup([int]$Code) { Exit-StartupLock; exit $Code }
trap { Write-Log "startup failed: $_"; Exit-StartupLock; break }

# deploy\logs' rotated copies, the services' too, pruned to the newest ten of each (what the
# installers' no-watchdog warning says this script does; Step-ScriptLog prunes only its own log's).
$null = Remove-RotatedServiceLogs -LogDir $LogDir
# startup.log is appended to at every logon and every watchdog trigger: rotated at a size, its
# rotated copies pruned (lib-runtime Step-ScriptLog). Under the lock, so one run rotates at a time.
$null = Step-ScriptLog -Path $LogFile
Write-Log "=== AI-Platform startup ($RuntimeMode) ==="
Write-Log "runtime chosen: $RuntimeWhy"
if ($RuntimeWarning) { Write-Log "WARNING: $RuntimeWarning" }
# Before anything runs a container (Get-ContainerHostIp's probe is a `podman run`): a
# ~/.docker/config.json that is a DIRECTORY fails every one with exit 125 (lib-runtime.ps1).
$null = Repair-DockerConfigEnv

$envFile = Join-Path $Root 'deploy\.env'
if (-not (Test-Path $envFile)) { Write-Log "no deploy\.env - is the platform installed? aborting."; Stop-Startup 1 }

# 1. runtime up
if ($RuntimeMode -eq 'podman') {
  if (-not (Initialize-PodmanMachine)) {
    # This runs unattended at logon, so the log is the ONLY place a failure can be seen. Say why.
    $advice = Get-PodmanMachineMemoryAdvice
    if ($advice) { Write-Log "  $advice" }
    Write-Log 'the podman machine did not start; aborting.'
    Stop-Startup 1
  }
  Write-Log 'podman machine is running.'
}

# 2. re-sync the host address. It is stable for gvproxy but dynamic for WSL, and re-detecting costs
# nothing - a stale value here is the classic "everything is up but every rail 502s" failure.
$winHost = Get-ContainerHostIp -Mode $RuntimeMode
if ($winHost) {
  Set-EnvValue -Path $envFile -Key 'WINDOWS_HOST' -Value $winHost
  Write-Log "WINDOWS_HOST=$winHost"
}

# 3. compose up, with the profiles that match the installed rails
# Read-DotEnvFile: the value compose gives the gateway (quotes and a trailing comment handled).
$apps = ([string](Get-DotEnvValue -Path $envFile -Key 'PLATFORM_ENABLED_APPS')).Trim()
Write-Log "enabled apps: $apps"

# Rails disabled on this box are not brought up (their profiles are left out) and are parked
# after `up`: a service with no profile, or a container created before it was disabled, would
# otherwise run, and come back at every machine boot through podman-restart. One declaration,
# read as the gateway and tools/rail_smoke.py read it. A list that does not parse is logged and
# parks nothing: the gateway refuses it anyway, and startup must still bring the platform up.
$disabled = @()
try { $disabled = @(Get-DeclaredAppList -Key 'PLATFORM_DISABLED_APPS' -EnvFile $envFile) }
catch { Write-Log "WARNING: PLATFORM_DISABLED_APPS does not parse ($($_.Exception.Message)); parking nothing." }
if ($disabled.Count) { Write-Log "disabled on this box: $($disabled -join ', ')" }
$upApps = (@($apps -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ -and $_ -notin $disabled })) -join ','

# This script brings up the LEAN stack (deploy\installer\docker-compose.installer.yml). On a box
# whose gateway was created from the FULL stack's file, `up` with the lean one would recreate the
# full stack's containers as lean ones under the same project and start the lean rails beside them.
# Refused when the gateway's own compose label says full (Resolve-PlatformStack, Proven); a box with
# no gateway or no label yet is composed as before. The full stack's containers come back by
# themselves (restart: always under Docker Desktop).
$stackPick = Resolve-PlatformStack -Mode $RuntimeMode
if ($stackPick.Stack -eq 'full' -and $stackPick.Proven) {
  Write-Log ("this box runs the FULL stack ($($stackPick.Reason)), and this script brings up only the " +
             'lean one; doing nothing. Its containers restart by themselves; rebuild-stale.ps1 drives that stack.')
  Stop-Startup 2
}

$paths = Get-ComposePaths -Mode $RuntimeMode
$cargs = @('--env-file', $paths.Env) + $paths.FileArgs   # FileArgs carries the box-local override
if ($paths.Files.Count -gt 1) { Write-Log "compose override: $($paths.Files[-1])" }
$cargs += Get-ComposeProfiles -Apps $upApps
Invoke-Compose -Arguments ($cargs + @('up', '-d')) -Mode $RuntimeMode
if ($LASTEXITCODE -ne 0) { Write-Log "compose up failed (exit $LASTEXITCODE)"; Stop-Startup 1 }

# Park what PLATFORM_DISABLED_APPS names, and bring back what it no longer names. After `up`, so
# nothing `up` started is left running. Rendered with EVERY enabled rail's profile, disabled ones
# included: a profiled-out service is absent from the render, so its container (created before the
# rail was disabled) could not be found to park. -NoLog: the render carries every secret in .env.
$mapArgs = @('--env-file', $paths.Env) + $paths.FileArgs + (Get-ComposeProfiles -Apps $apps)
# Stdout only (Invoke-ComposeConfigJson): a compose warning on stderr used to land in the JSON.
$render = Invoke-ComposeConfigJson -Arguments $mapArgs -Mode $RuntimeMode
try {
  if ($render.Code -ne 0) { throw "compose config exit $($render.Code): $(@($render.Err)[-1])" }
  $park = Sync-DisabledRails -Disabled $disabled -Services @(Get-ComposeServices -ConfigJson $render.Json) `
                             -RailsDir (Join-Path $Root 'rails') -Mode $RuntimeMode
  foreach ($l in $park.Lines) { Write-Log "  disabled rails: $l" }
} catch { Write-Log "WARNING: could not read the compose services to park disabled rails: $($_.Exception.Message)" }

# 4. wait for the front door so the log records a usable end state
for ($i = 0; $i -lt 30; $i++) {
  try { Invoke-WebRequest 'http://localhost:1111/api/platform/healthz' -TimeoutSec 3 -UseBasicParsing | Out-Null; break } catch { Start-Sleep 2 }
}
try {
  Invoke-WebRequest 'http://localhost:1111/api/platform/healthz' -TimeoutSec 3 -UseBasicParsing | Out-Null
  Write-Log 'platform is up at http://localhost:1111'
} catch {
  Write-Log 'WARNING: the gateway did not answer on :1111 - check `podman ps` and the container logs.'
}
Write-Log '=== startup done ==='
Exit-StartupLock
