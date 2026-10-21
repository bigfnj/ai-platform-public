# Register the native services for a LEAN install: create the torch-free broker venv, then register
# `platform-broker` (media OFF) as a LocalSystem NSSM service and start it. By default it ALSO
# registers an `ollama serve` service; pass -SkipOllama (the lean installer does) so Ollama runs via
# its own app/autostart on :11434 (one server, no port conflict). Idempotent. MUST run elevated
# (install.ps1 launches it elevated), except with -PlanOnly. NB: the switches are [switch], not
# [bool], so they survive being passed through `powershell -File`.
#
#   powershell -ExecutionPolicy Bypass -File install-native.ps1 -PlatformRoot <repo-root> -SkipOllama
#
# The broker is AUTHENTICATED and, outside wsl mode, bound to loopback (lib-runtime's
# Get-BrokerBindHost says why per runtime): the token is BROKER_AUTH_TOKEN from deploy\.env,
# generated there when absent, and set in the service's environment, so the rails and the gateway
# (which compose hands the same deploy\.env value) are the callers it accepts. A re-register keeps
# the installed service's BROKER_CLI_* and map-file settings (lib-runtime Get-LeanBrokerServiceEnv),
# so the Claude CLI backend that install-services.ps1 -BrokerCliEnvFile set up survives it.
#
# Parameterized (unlike deploy/install-services.ps1, whose media paths are the full-stack box's; on a
# lean box it registers this script's spec: lib-runtime Get-InstallServicesBrokerPlan).
param(
  [Parameter(Mandatory = $true)][string]$PlatformRoot,
  [switch]$SkipOllama,           # skip the Ollama NSSM service (lean install: Ollama runs as its own app)
  [switch]$OpenWslFirewall,      # wsl mode: admit WSL2 containers to broker/ollama, block the rest
  # The broker's bind address. Default: Get-BrokerBindHost for the runtime deploy\.env declares
  # (PLATFORM_RUNTIME), so a re-run on its own keeps what the installer chose.
  [ValidatePattern('^(\d{1,3}\.){3}\d{1,3}$')][string]$BrokerHost,
  # Print the plan (bind address, environment names, the token's sha256, firewall rules) as JSON and
  # exit, registering nothing and needing no admin. It still makes sure deploy\.env holds a token,
  # as a real run does, because the plan names it.
  [switch]$PlanOnly
)
$ErrorActionPreference = 'Stop'
# Elevation is checked here rather than by #Requires, which would refuse -PlanOnly too.
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
  [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $PlanOnly -and -not $isAdmin) { throw 'install-native.ps1 registers services: run it elevated (install.ps1 launches it that way).' }

# lib-runtime: the deploy\.env reader and writer, the broker service spec, the log settings.
$LibRuntime = Join-Path $PSScriptRoot 'lib-runtime.ps1'
. $LibRuntime
$EnvFile = Join-Path $PlatformRoot 'deploy\.env'
$declared = Get-DeclaredRuntime -EnvFile $EnvFile
if (-not $BrokerHost) { $BrokerHost = Get-BrokerBindHost -Mode $declared }
$token = Initialize-BrokerToken -EnvFile $EnvFile
$spec = Get-BrokerServiceSpec -BindHost $BrokerHost -Token $token.Token -Root $PlatformRoot
# The box's own role map and disabled list (deploy\local, git-ignored), which the spec names.
$BrokerFiles = Get-LeanBrokerFiles -Root $PlatformRoot
# The service's environment: the installed broker's BROKER_CLI_* and map-file settings carried
# forward, the spec's entries over them (lib-runtime Get-LeanBrokerServiceEnv says why).
$Carried = @(Select-CarriedBrokerEnv -Existing (Get-InstalledBrokerEnv))
$ServiceEnv = @(Get-LeanBrokerServiceEnv -Spec $spec -Existing $Carried)
$fwRules = @(); if ($OpenWslFirewall) { $fwRules = @(Get-BrokerFirewallRules) }
# The broker venv's installs, from services\broker\pyproject.toml (Get-BrokerPipInstalls says why).
$BrokerPipInstalls = @(Get-BrokerPipInstalls -Root $PlatformRoot)
if ($BrokerHost -ne '127.0.0.1' -and -not $OpenWslFirewall) {
  Write-Warning ("the broker will listen on $BrokerHost with no firewall rule scoping it; the token is " +
                 'its only guard there. wsl mode adds the rules with -OpenWslFirewall.')
}
if ($PlanOnly) {
  $sha = [System.Security.Cryptography.SHA256]::Create()
  $hash = -join ($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($token.Token)) | ForEach-Object { $_.ToString('x2') })
  [ordered]@{
    DeclaredRuntime = $declared; BindHost = $BrokerHost; AppParameters = $spec.AppParameters
    # Names only: a carried BROKER_CLI_* value can be a token.
    EnvironmentNames = @($ServiceEnv | ForEach-Object { ($_ -split '=', 2)[0] })
    CarriedNames = @($Carried | ForEach-Object { ($_ -split '=', 2)[0] })
    TokenSha256 = $hash; TokenGenerated = $token.Generated
    NssmLogSettings = @(Get-NssmLogSettings -Name 'platform-broker' -Stdout (Join-Path $PlatformRoot 'deploy\logs\platform-broker.out.log') `
                          -Stderr (Join-Path $PlatformRoot 'deploy\logs\platform-broker.err.log') -Lib $LibRuntime | ForEach-Object { , @($_) })
    Firewall = @($fwRules | ForEach-Object { [ordered]@{ DisplayName = $_.DisplayName; Action = $_.Action
                                                      LocalPort = $_.LocalPort; RemoteAddress = $_.RemoteAddress } })
    # What a real run installs into the broker venv (each entry follows `python -m pip`), and what
    # the broker's pyproject declares, which is what the second of them brings.
    BrokerPipInstalls = @($BrokerPipInstalls | ForEach-Object { , @($_) })
    BrokerDependencies = @(Get-BrokerDeclaredDependencies -Root $PlatformRoot)
    # The files BROKER_ROLES_FILE / BROKER_DISABLED_FILE name, and whether each exists yet (a real
    # run seeds a missing one: Initialize-LeanBrokerFiles).
    BrokerFiles = [ordered]@{ Roles = $BrokerFiles.Roles; Disabled = $BrokerFiles.Disabled
                              RolesExists = (Test-Path -LiteralPath $BrokerFiles.Roles)
                              DisabledExists = (Test-Path -LiteralPath $BrokerFiles.Disabled) }
  } | ConvertTo-Json -Depth 4 -Compress
  exit 0
}
Write-Host ("broker token: " + $(if ($token.Generated) { 'generated into deploy\.env' } else { 'taken from deploy\.env' }))

$UserProfile = $env:USERPROFILE
$BrokerPy    = Join-Path $PlatformRoot '.venv\Scripts\python.exe'
$OllamaExe   = Join-Path $UserProfile 'AppData\Local\Programs\Ollama\ollama.exe'
$LogDir      = Join-Path $PlatformRoot 'deploy\logs'
$BinDir      = Join-Path $PlatformRoot 'deploy\bin'
$Nssm        = Join-Path $BinDir 'nssm.exe'
New-Item -ItemType Directory -Force -Path $LogDir, $BinDir | Out-Null

# --- 1. nssm ----------------------------------------------------------------
if (-not (Test-Path $Nssm)) {
  Write-Host 'Downloading NSSM...'
  $null = Install-NssmExe -Dest $Nssm     # lib-runtime: nothing left in TEMP, the pin checked when set
}

# --- 2. broker venv (torch-free; media is disabled so no CUDA/diffusers/XTTS) ----
# Installed from the broker's own pyproject ($BrokerPipInstalls), when the venv is new and whenever
# the broker does not import with it (Test-BrokerImports): a venv built from the hand-written list
# this step used to carry, which had no python-multipart, is repaired by a re-run instead of being
# skipped because it exists. Each pip run's exit code is checked; EAP is relaxed around them, as
# around nssm, so a pip notice on stderr does not abort a run whose output is redirected.
$fresh = -not (Test-Path $BrokerPy)
if ($fresh) {
  Write-Host 'Creating broker venv...'
  & python -m venv (Join-Path $PlatformRoot '.venv')
  if ($LASTEXITCODE -ne 0) { throw 'python -m venv failed (is Python 3.11 on PATH?)' }
}
if ($fresh -or -not (Test-BrokerImports -Python $BrokerPy -Root $PlatformRoot)) {
  Write-Host "Installing the broker's dependencies from services\broker\pyproject.toml (+ platform_core)..."
  $eap = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  try {
    & $BrokerPy -m pip install --upgrade pip --quiet
    foreach ($a in $BrokerPipInstalls) {
      & $BrokerPy -m pip @a
      if ($LASTEXITCODE -ne 0) { throw "pip $($a -join ' ') failed (exit $LASTEXITCODE)" }
    }
  }
  finally { $ErrorActionPreference = $eap }
  if (-not (Test-BrokerImports -Python $BrokerPy -Root $PlatformRoot)) {
    throw "the broker still does not import with $BrokerPy, so its service would crash-loop; see the pip output above"
  }
}

# --- 3. nssm helpers (relax EAP so a benign stderr line doesn't abort) --------
# Invoke-Nssm RETURNS nssm's exit code: `install` failing is fatal, the prune hook's is kept and
# said (Install-Svc), the rest are not judged.
function Invoke-Nssm {
  $eap = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  try { & $Nssm @args 2>&1 | Out-Null } finally { $ErrorActionPreference = $eap }
  return $LASTEXITCODE
}
function Install-Svc([string]$Name, [string]$Exe, [string]$AppArgs, [string]$Cwd, [string[]]$EnvExtra) {
  if (Get-Service -Name $Name -ErrorAction SilentlyContinue) {
    Invoke-Nssm stop $Name | Out-Null
    Invoke-Nssm remove $Name confirm | Out-Null
    Start-Sleep -Milliseconds 500
  }
  if ((Invoke-Nssm install $Name $Exe) -ne 0) { throw "nssm install $Name failed" }
  Invoke-Nssm set $Name AppParameters $AppArgs | Out-Null
  Invoke-Nssm set $Name AppDirectory $Cwd | Out-Null
  Invoke-Nssm set $Name Start SERVICE_AUTO_START | Out-Null
  if ($EnvExtra) { Invoke-Nssm set $Name AppEnvironmentExtra @EnvExtra | Out-Null }
  # Logs rotated at start and online at a size, rotated copies pruned (lib-runtime Get-NssmLogSettings).
  # The prune hook's exit code is kept: NSSM 2.24 has no event hooks and refuses it. Not fatal (the
  # watchdog prunes daily, or where there is none, rebuild-stale.ps1 and the logon startup when they
  # run), but said once the service is registered, below, naming which.
  $hookExit = 0
  foreach ($a in @(Get-NssmLogSettings -Name $Name -Stdout "$LogDir\$Name.out.log" -Stderr "$LogDir\$Name.err.log" -Lib $LibRuntime)) {
    $code = Invoke-Nssm @a
    if (Test-NssmHookSetting $a) { $hookExit = $code }
  }
  Invoke-Nssm set $Name AppExit Default Restart | Out-Null
  Write-Host "installed service: $Name (LocalSystem)"
  # Registered: did NSSM keep the hook? Read back from the registry (lib-runtime Get-NssmHookWarning).
  $watchdog = [bool](Get-Service -Name 'platform-watchdog' -ErrorAction SilentlyContinue)
  $hookWarning = Get-NssmHookWarning -Name $Name -HookExit $hookExit -WatchdogRegistered $watchdog
  if ($hookWarning) { Write-Warning $hookWarning }
}

# --- 4. services -------------------------------------------------------------
# Broker LEAN: media pipeline OFF (no image/TTS), so no HF_HOME/TTS_HOME needed. Bound and
# authenticated per $spec (see the header). Its role map and disabled list are deploy\local's
# (lib-runtime Get-LeanBrokerFiles), seeded here when missing from what the broker reads now: on a
# box installed before they existed, the tracked files hold its lean map and its admin edits.
foreach ($s in @(Initialize-LeanBrokerFiles -Root $PlatformRoot)) { Write-Host "box-local broker file: $s" }
if ($Carried.Count) {
  Write-Host ("keeping from the installed service: " + (($Carried | ForEach-Object { ($_ -split '=', 2)[0] }) -join ', '))
}
Install-Svc 'platform-broker' $BrokerPy $spec.AppParameters $PlatformRoot $ServiceEnv
Write-Host "broker: listening on ${BrokerHost}:11500, token required on /v1/*"

if (-not $SkipOllama) {
  Install-Svc 'ollama' $OllamaExe 'serve' (Split-Path $OllamaExe) `
    @("OLLAMA_MODELS=$UserProfile\.ollama\models")
}

# --- 4b. WSL firewall: let WSL2 containers reach the native broker/ollama ----
# WSL Docker mode only (Get-BrokerFirewallRules says why): the WSL2 range is admitted to
# :11500/:11434, else the rail containers (and the gateway) can't reach the native broker, and every
# other remote address is blocked from both, which is what scopes the 0.0.0.0 binds (the broker's
# here; Ollama's is the user's OLLAMA_HOST, which install.ps1 sets: Set-WslOllamaHost). Idempotent
# by name, so a box installed before gains the block rule under its new name.
foreach ($r in $fwRules) {
  if (Get-NetFirewallRule -DisplayName $r.DisplayName -ErrorAction SilentlyContinue) {
    Write-Host "firewall rule already present: $($r.DisplayName)"
  }
  else {
    New-NetFirewallRule -DisplayName $r.DisplayName -Direction Inbound -Action $r.Action -Protocol TCP `
      -LocalPort $r.LocalPort -RemoteAddress $r.RemoteAddress -Profile Any | Out-Null
    Write-Host "firewall rule added: $($r.DisplayName) ($($r.Action) TCP $($r.LocalPort -join ',') from $($r.RemoteAddress -join ', '))"
  }
}

# --- 5. start + report ------------------------------------------------------
if (-not $SkipOllama) { Invoke-Nssm start ollama | Out-Null; Start-Sleep 4 }
else { Write-Host 'skipping the Ollama service (Ollama runs via its own app/autostart on :11434).' }
Invoke-Nssm start platform-broker | Out-Null
Start-Sleep 4
$names = @('platform-broker'); if (-not $SkipOllama) { $names += 'ollama' }
Get-Service -Name $names -ErrorAction SilentlyContinue | Select-Object Name, Status | Format-Table -AutoSize
Write-Host 'native services installed. Verify: curl http://127.0.0.1:11500/healthz'