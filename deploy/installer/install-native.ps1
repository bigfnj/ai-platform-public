# Register the native services for a LEAN install: create the torch-free broker venv, then register
# `platform-broker` (media OFF) as a LocalSystem NSSM service and start it. With -InstallOllama
# (default $true) it ALSO registers an `ollama serve` service; the lean installer passes
# -InstallOllama:$false so Ollama runs via its own app/autostart on :11434 (one server, no port
# conflict). Idempotent. MUST run elevated (install.ps1 launches it elevated).
#
#   powershell -ExecutionPolicy Bypass -File install-native.ps1 -PlatformRoot <repo-root>
#
# Parameterized (unlike deploy/install-services.ps1, which is pinned to the full-stack box).
#Requires -RunAsAdministrator
param(
  [Parameter(Mandatory = $true)][string]$PlatformRoot,
  [bool]$InstallOllama = $true
)
$ErrorActionPreference = 'Stop'

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
  $zip = "$env:TEMP\nssm-2.24.zip"; $ex = "$env:TEMP\nssm-2.24-extract"
  Invoke-WebRequest 'https://nssm.cc/release/nssm-2.24.zip' -OutFile $zip
  Expand-Archive $zip -DestinationPath $ex -Force
  Copy-Item "$ex\nssm-2.24\win64\nssm.exe" $Nssm -Force
}

# --- 2. broker venv (torch-free; media is disabled so no CUDA/diffusers/XTTS) ----
if (-not (Test-Path $BrokerPy)) {
  Write-Host 'Creating broker venv (fastapi/uvicorn/httpx/pydantic + platform_core)...'
  & python -m venv (Join-Path $PlatformRoot '.venv')
  if ($LASTEXITCODE -ne 0) { throw 'python -m venv failed (is Python 3.11 on PATH?)' }
  & $BrokerPy -m pip install --upgrade pip --quiet
  & $BrokerPy -m pip install --quiet "fastapi>=0.111" "uvicorn[standard]>=0.30" "httpx>=0.27" "pydantic>=2.7" "pydantic-settings>=2.2"
  & $BrokerPy -m pip install --quiet -e (Join-Path $PlatformRoot 'packages\platform_core')
}

# --- 3. nssm helpers (relax EAP so a benign stderr line doesn't abort) --------
function Invoke-Nssm {
  $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  try { & $Nssm @args 2>&1 | Out-Null } finally { $ErrorActionPreference = $old }
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
  Invoke-Nssm set $Name AppStdout "$LogDir\$Name.out.log" | Out-Null
  Invoke-Nssm set $Name AppStderr "$LogDir\$Name.err.log" | Out-Null
  Invoke-Nssm set $Name AppRotateFiles 1 | Out-Null
  Invoke-Nssm set $Name AppExit Default Restart | Out-Null
  Write-Host "installed service: $Name (LocalSystem)"
}

# --- 4. services -------------------------------------------------------------
# Broker LEAN: media pipeline OFF (no image/TTS), so no HF_HOME/TTS_HOME needed.
Install-Svc 'platform-broker' $BrokerPy `
  '-m uvicorn app.main:app --app-dir services\broker --host 0.0.0.0 --port 11500' `
  $PlatformRoot @('BROKER_MEDIA_ENABLED=false')

if ($InstallOllama) {
  Install-Svc 'ollama' $OllamaExe 'serve' (Split-Path $OllamaExe) `
    @("OLLAMA_MODELS=$UserProfile\.ollama\models")
}

# --- 5. start + report ------------------------------------------------------
if ($InstallOllama) { Invoke-Nssm start ollama | Out-Null; Start-Sleep 4 }
else { Write-Host 'skipping the Ollama service (Ollama runs via its own app/autostart on :11434).' }
Invoke-Nssm start platform-broker | Out-Null
Start-Sleep 4
$names = @('platform-broker'); if ($InstallOllama) { $names += 'ollama' }
Get-Service -Name $names -ErrorAction SilentlyContinue | Select-Object Name, Status | Format-Table -AutoSize
Write-Host 'native services installed. Verify: curl http://127.0.0.1:11500/healthz'