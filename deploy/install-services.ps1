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
#   powershell -ExecutionPolicy Bypass -File <repo>\deploy\install-services.ps1
#
# Skip the Ollama service (if it already auto-starts) with:  -InstallOllama:$false

param([bool]$InstallOllama = $true)

#Requires -RunAsAdministrator
$ErrorActionPreference = 'Stop'

$PlatformRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$AiWorkRoot   = (Resolve-Path (Join-Path $PlatformRoot '..\..')).Path
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
$BrokerEnv = @("HF_HOME=$AiWorkRoot\cache\hf-cache",
               "HUGGINGFACE_HUB_CACHE=$AiWorkRoot\cache\hf-cache\hub",
               "TTS_HOME=$UserProfile\AppData\Local",
               # The media worker's THREE interpreters. These were previously set ONLY in
               # the service registry, by hand — so re-running this script silently reverted
               # the broker to the (nonexistent) defaults in config.py and every image and
               # speech job failed. Declare them here so the install is reproducible.
               #   image  = torch + diffusers  (transformers 5.5.4, numpy 1.26.4)
               #   speech = torch + coqui-tts  (needs transformers <5 — do not merge these)
               #   kokoro = kokoro-onnx        (needs numpy >=2, torch-free)
               # The kokoro split is forced, not stylistic: kokoro-onnx requires numpy>=2.0.2
               # and simple-lama-inpainting in the media venv requires numpy<2.0.0, so a
               # merge silently upgrades numpy under every image path.
               "BROKER_MEDIA_PYTHON=$AiWorkRoot\venvs\media-venv\Scripts\python.exe",
               "BROKER_TTS_PYTHON=$AiWorkRoot\venvs\tts-venv\Scripts\python.exe",
               "BROKER_KOKORO_PYTHON=$AiWorkRoot\venvs\kokoro-venv\Scripts\python.exe",
               "BROKER_KOKORO_MODEL_PATH=$AiWorkRoot\models\kokoro\kokoro-v1.0.onnx",
               "BROKER_KOKORO_VOICES_PATH=$AiWorkRoot\models\kokoro\voices-v1.0.bin",
               # faster-whisper shares the kokoro venv (torch-free, verified no numpy
               # conflict), so BROKER_WHISPER_PYTHON is left unset and falls back to it.
               # MULTILINGUAL 'small', never 'small.en' — the .en models are English-only,
               # ignore the language parameter, and render Spanish as nonsense.
               "BROKER_WHISPER_MODEL=small",
               "BROKER_WHISPER_DEVICE=cpu",
               "BROKER_WHISPER_COMPUTE_TYPE=int8",
               # edu_media_core moved out of rails/edu-suite into packages/ so the broker no
               # longer depends on a rail. This value is baked into the NSSM service env, so
               # changing the code alone is NOT enough -- re-run this script elevated or the
               # service keeps the old path and every media job dies with ModuleNotFoundError
               # inside a subprocess whose stderr is only tail-captured.
               "BROKER_MEDIA_CORE_SRC=$PlatformRoot\packages\edu-media-core\src",
               "BROKER_MEDIA_VOICES_DIR=$PlatformRoot\packages\edu-media-core\voices",
               # Host-native voice engines for /v1/voice/*. The broker no longer hardcodes a
               # path into rails/ai-voice: dispatch stays in the broker (synthesis takes the GPU
               # gate and evicts every resident heavy model, arbitration only the broker can do)
               # but the LOCATION is deployment configuration. Unset => voice reports
               # unavailable, which is correct for a platform without the ai-voice rail.
               "BROKER_VOICE_ENGINES_DIR=$PlatformRoot\rails\ai-voice\native")
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