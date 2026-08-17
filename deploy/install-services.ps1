# Install the NATIVE GPU-layer processes as Windows services (via NSSM) so hosting
# survives a reboot with no login at all. The containers (caddy/gateway/dashboard)
# already auto-restart via Docker Desktop; this covers the native side.
#
#   platform-broker : GPU/Model Broker on 0.0.0.0:11500 (+ its subprocess media worker)
#   ollama          : `ollama serve` on 127.0.0.1:11434
#
# The Admin account on this box has NO password (PIN/Hello login), and Windows
# refuses to run a service as a blank-password local account. So the services run as
# LocalSystem (no account/password needed) and we point them at your profile's
# models/caches via env vars, since LocalSystem otherwise uses the wrong profile:
#   OLLAMA_MODELS -> your Ollama models   |   HF_HOME -> your HuggingFace cache
#   TTS_HOME      -> parent of your Coqui/XTTS model dir (%LOCALAPPDATA%). REQUIRED:
#     Coqui resolves its model dir from the HKCU "Local AppData" shell-folder registry
#     value, which does NOT exist for the LocalSystem account (winreg raises
#     FileNotFoundError), so without TTS_HOME the XTTS worker dies before it loads.
#     Pointing it at your profile also REUSES the ~2GB model already downloaded there
#     (no re-download — contrary to the earlier "no cache env override" belief).
#
# RUN THIS IN AN ELEVATED (Administrator) PowerShell:
#   powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\ai-platform\deploy\install-services.ps1"
#
# Skip the Ollama service (if it already auto-starts) with:  -InstallOllama:$false

param([bool]$InstallOllama = $true)

#Requires -RunAsAdministrator
$ErrorActionPreference = 'Stop'

$PlatformRoot = "$env:USERPROFILE\ai-platform"
$UserProfile  = $env:USERPROFILE
$BrokerPy     = "$PlatformRoot\.venv\Scripts\python.exe"
$OllamaExe    = "$UserProfile\AppData\Local\Programs\Ollama\ollama.exe"
$LogDir       = "$PlatformRoot\deploy\logs"
$BinDir       = "$PlatformRoot\deploy\bin"
$Nssm         = "$BinDir\nssm.exe"

foreach ($d in $LogDir, $BinDir) { New-Item -ItemType Directory -Force -Path $d | Out-Null }

# Broker control-plane token (shared secret) from deploy/.env — injected into the broker's env so
# it enforces `Authorization: Bearer` on every /v1/* route. The rail containers get the SAME token
# via compose (which also reads deploy/.env). Read script-relative so it's robust to $PlatformRoot.
$BrokerToken = ''
$EnvFile = Join-Path $PSScriptRoot '.env'
if (Test-Path $EnvFile) {
    $m = Select-String -Path $EnvFile -Pattern '^\s*BROKER_AUTH_TOKEN\s*=\s*(.+)$' | Select-Object -First 1
    if ($m) { $BrokerToken = $m.Matches[0].Groups[1].Value.Trim() }
}

# --- 1. fetch NSSM if we don't have it --------------------------------------
if (-not (Test-Path $Nssm)) {
    Write-Host 'Downloading NSSM...' -ForegroundColor Cyan
    $zip = "$env:TEMP\nssm-2.24.zip"; $ex = "$env:TEMP\nssm-2.24-extract"
    Invoke-WebRequest 'https://nssm.cc/release/nssm-2.24.zip' -OutFile $zip
    Expand-Archive $zip -DestinationPath $ex -Force
    Copy-Item "$ex\nssm-2.24\win64\nssm.exe" $Nssm -Force
}
Write-Host "nssm: $Nssm"

# --- 2. install helper (runs as LocalSystem; no ObjectName/password) ---------
# NSSM prints status to STDERR, and under $ErrorActionPreference='Stop' PowerShell
# 5.1 promotes any native stderr to a *terminating* error. On a fresh box the very
# first cleanup call ("nssm stop <name>") prints "Can't open service!" and would
# abort the whole install. So run every nssm call with EAP relaxed and judge it by
# the process exit code, and only clean up a service that actually exists.
function Invoke-Nssm {
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $Nssm @args 2>&1 | Out-Null } finally { $ErrorActionPreference = $old }
    return $LASTEXITCODE
}

# NOTE: the parameter is $AppArgs, NOT $Args — $Args collides with PowerShell's
# automatic $args variable and silently binds to nothing (AppParameters ends up
# empty, so the service launches a bare Python REPL instead of uvicorn).
function Install-Svc([string]$Name, [string]$Exe, [string]$AppArgs, [string]$Cwd, [string[]]$EnvExtra) {
    if (Get-Service -Name $Name -ErrorAction SilentlyContinue) {   # clear a prior install
        Invoke-Nssm stop $Name | Out-Null
        Invoke-Nssm remove $Name confirm | Out-Null
        Start-Sleep -Milliseconds 500
    }
    if ((Invoke-Nssm install $Name $Exe) -ne 0) { throw "nssm install $Name failed" }
    # Set AppParameters explicitly: passing args as a trailing string to `nssm
    # install` does NOT populate them (the child launches with no args — a bare
    # Python REPL, in the broker's case), so set them as their own value here.
    Invoke-Nssm set $Name AppParameters $AppArgs | Out-Null
    Invoke-Nssm set $Name AppDirectory $Cwd | Out-Null
    Invoke-Nssm set $Name Start SERVICE_AUTO_START | Out-Null
    if ($EnvExtra) { Invoke-Nssm set $Name AppEnvironmentExtra @EnvExtra | Out-Null }
    Invoke-Nssm set $Name AppStdout "$LogDir\$Name.out.log" | Out-Null
    Invoke-Nssm set $Name AppStderr "$LogDir\$Name.err.log" | Out-Null
    Invoke-Nssm set $Name AppRotateFiles 1 | Out-Null
    Invoke-Nssm set $Name AppExit Default Restart | Out-Null       # restart on crash
    Write-Host "installed service: $Name (LocalSystem)" -ForegroundColor Green
}

# --- 3. broker (point torch/HF at your profile cache) -----------------------
$BrokerEnv = @("HF_HOME=$UserProfile\.cache\huggingface",
               "HUGGINGFACE_HUB_CACHE=$UserProfile\.cache\huggingface\hub",
               "TTS_HOME=$UserProfile\AppData\Local")
if ($BrokerToken) { $BrokerEnv += "BROKER_AUTH_TOKEN=$BrokerToken" }  # enable control-plane auth
Install-Svc 'platform-broker' $BrokerPy `
    '-m uvicorn app.main:app --app-dir services\broker --host 0.0.0.0 --port 11500' `
    $PlatformRoot `
    $BrokerEnv

# --- 4. ollama (point at your models) ---------------------------------------
# NOTE: if Ollama already starts on boot (its app adds itself to startup), disable
# that (Task Manager -> Startup apps -> Ollama -> Disable) or run with
# -InstallOllama:$false, or two servers fight over port 11434.
if ($InstallOllama) {
    Install-Svc 'ollama' $OllamaExe 'serve' (Split-Path $OllamaExe) `
        @("OLLAMA_MODELS=$UserProfile\.ollama\models")
}

# --- 5. start + report ------------------------------------------------------
if ($InstallOllama) { Invoke-Nssm start ollama | Out-Null; Start-Sleep 4 }
Invoke-Nssm start platform-broker | Out-Null
Start-Sleep 4
$svcNames = @('platform-broker'); if ($InstallOllama) { $svcNames += 'ollama' }
Get-Service -Name $svcNames -ErrorAction SilentlyContinue |
    Select-Object Name, Status, StartType | Format-Table -AutoSize
Write-Host ''
Write-Host 'Verify:  curl http://127.0.0.1:11500/healthz   (expect ollama_reachable:true)'
Write-Host "Logs:    $LogDir  (first broker media job re-downloads XTTS once, ~2GB)"
Write-Host 'Manage:  nssm restart platform-broker | nssm status platform-broker | nssm stop platform-broker'