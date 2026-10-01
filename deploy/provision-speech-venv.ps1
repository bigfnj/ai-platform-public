<#
.SYNOPSIS
  Build (or repair) the broker's speech venv -- the interpreter BROKER_TTS_PYTHON points at.

.DESCRIPTION
  This venv runs Coqui XTTS for BOTH consumers:
    * the broker's generic speech ops  /v1/tts and /v1/tts_batch
      (edu-suite CVC audio, Just Translate, TeachTown, and the IEP rail's narrate stage)
    * the ai-voice rail's `xtts` engine (voice_id slj-xtts) via /v1/voice/synthesize

  It exists because the venv was previously hand-built and undocumented in code. It then
  silently lost its `TTS` package, which broke audio on every rail at once and stayed
  broken until someone happened to try it. Nothing recreated it, so nothing detected it.

  Four constraints are load-bearing and none are discoverable from a plain
  `pip install coqui-tts`. They are encoded below so they survive:

    1. coqui-tts declares NO torch dependency  -> the CUDA build must be chosen here.
    2. coqui-tts needs transformers < 5        -> pip resolves 5.x, which then fails at
                                                  import (`isin_mps_friendly` was removed
                                                  in 5.x). Must be installed AFTER coqui-tts.
    3. torch >= 2.9 requires torchcodec         -> the `[codec]` extra.
    4. torchcodec requires FFmpeg *SHARED*      -> the box's winget ffmpeg is a static
       libraries                                  "essentials" build with no DLLs. The DLLs
                                                  are copied INTO the torchcodec package
                                                  directory (Windows resolves a DLL's
                                                  dependencies from the loading module's
                                                  folder) rather than onto PATH, where they
                                                  would collide with the DevToolbox ffmpeg.

  Idempotent: safe to re-run. Re-run it after ANY torchcodec upgrade -- a
  `pip install --force-reinstall torchcodec` wipes the FFmpeg DLLs and audio dies again.

.PARAMETER Recreate
  Delete and rebuild the venv from scratch.

.PARAMETER VerifyOnly
  Run the verification checks against the existing venv and exit. Good for a health check.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File deploy\provision-speech-venv.ps1
  powershell -ExecutionPolicy Bypass -File deploy\provision-speech-venv.ps1 -VerifyOnly
#>
[CmdletBinding()]
param(
    [string]$VenvPath   = (Join-Path (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path 'venvs	ts-venv'),
    [string]$BasePython = 'C:\Users\Admin\AppData\Local\Programs\Python\Python311\python.exe',
    [string]$TorchIndex = 'https://download.pytorch.org/whl/cu130',
    [string]$FfmpegPkg  = 'BtbN.FFmpeg.GPL.Shared.7.1',
    [switch]$Recreate,
    [switch]$VerifyOnly
)

$ErrorActionPreference = 'Stop'
$Py = Join-Path $VenvPath 'Scripts\python.exe'

function Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Warn($msg) { Write-Host "  ! $msg" -ForegroundColor Yellow }

# --- 1. venv ----------------------------------------------------------------
if ($Recreate -and (Test-Path $VenvPath)) {
    Step "removing existing venv ($VenvPath)"
    Remove-Item -Recurse -Force $VenvPath
}

if (-not $VerifyOnly) {
    if (-not (Test-Path $Py)) {
        Step "creating venv at $VenvPath"
        if (-not (Test-Path $BasePython)) { throw "base python not found: $BasePython" }
        & $BasePython -m venv $VenvPath
        & $Py -m pip install --quiet --upgrade pip
    } else {
        Step "venv present, updating in place"
    }

    # --- 2. the stack, in an order that matters ------------------------------
    # torch FIRST so coqui-tts does not drag in a CPU wheel from PyPI.
    Step "installing torch + torchaudio ($TorchIndex)"
    & $Py -m pip install --quiet torch torchaudio --index-url $TorchIndex

    Step "installing coqui-tts[codec] + soundfile"
    & $Py -m pip install --quiet 'coqui-tts[codec]' soundfile

    # AFTER coqui-tts: it pulls transformers 5.x, which its own import path cannot use.
    Step "pinning transformers < 5 (coqui-tts import needs isin_mps_friendly)"
    & $Py -m pip install --quiet 'transformers<5'

    # --- 3. FFmpeg shared DLLs into the torchcodec package -------------------
    Step "ensuring FFmpeg shared libraries for torchcodec"
    $pkgRoot = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages'
    $avcodec = Get-ChildItem -Path $pkgRoot -Filter 'avcodec-*.dll' -Recurse -File -ErrorAction SilentlyContinue |
               Where-Object { $_.FullName -like '*Shared*' } | Select-Object -First 1
    if (-not $avcodec) {
        Step "  installing $FfmpegPkg (shared build; the essentials build has no DLLs)"
        winget install --id $FfmpegPkg --accept-package-agreements --accept-source-agreements --disable-interactivity | Out-Null
        $avcodec = Get-ChildItem -Path $pkgRoot -Filter 'avcodec-*.dll' -Recurse -File -ErrorAction SilentlyContinue |
                   Where-Object { $_.FullName -like '*Shared*' } | Select-Object -First 1
    }
    if (-not $avcodec) { throw "could not locate FFmpeg shared DLLs after installing $FfmpegPkg" }

    $tcDir = & $Py -c "import torchcodec, os; print(os.path.dirname(torchcodec.__file__))"
    if (-not (Test-Path $tcDir)) { throw "torchcodec package directory not found: $tcDir" }
    Copy-Item (Join-Path $avcodec.DirectoryName '*.dll') -Destination $tcDir -Force
    $n = (Get-ChildItem $tcDir -Filter 'av*.dll').Count + (Get-ChildItem $tcDir -Filter 'sw*.dll').Count
    Write-Host "  copied $n FFmpeg DLL(s) into $tcDir"
}

# --- 4. verify --------------------------------------------------------------
Step 'verifying'
if (-not (Test-Path $Py)) { throw "no venv at $VenvPath -- run without -VerifyOnly first" }

$refWav = Join-Path (Resolve-Path (Join-Path $PSScriptRoot '..')).Path 'packages\edu-media-core\voices\english_reference.wav'
$check = @"
import sys
ok = True

import torch
print(f'  torch          {torch.__version__}  cuda={torch.cuda.is_available()}')
if not torch.cuda.is_available():
    print('  ! CUDA not available - XTTS will run on CPU and be very slow'); ok = False

import transformers
print(f'  transformers   {transformers.__version__}')
if int(transformers.__version__.split(".")[0]) >= 5:
    print('  ! transformers >= 5 breaks the coqui-tts import'); ok = False

import TTS
print(f'  coqui-tts      import OK')

import soundfile
print(f'  soundfile      {soundfile.__version__}')

import os
ref = r'$refWav'
if os.path.exists(ref):
    from torchcodec.decoders import AudioDecoder
    d = AudioDecoder(ref)
    print(f'  torchcodec     decoded reference wav @ {d.metadata.sample_rate} Hz')
else:
    print('  ! reference clip missing: ' + ref); ok = False

sys.exit(0 if ok else 1)
"@

$tmp = Join-Path $env:TEMP 'speech_venv_verify.py'
Set-Content -Path $tmp -Value $check -Encoding utf8
& $Py $tmp
$rc = $LASTEXITCODE
Remove-Item $tmp -Force -ErrorAction SilentlyContinue

if ($rc -ne 0) {
    Warn 'verification FAILED - speech will 502 through the broker'
    exit 1
}

Write-Host ''
Write-Host 'speech venv OK.' -ForegroundColor Green
Write-Host "  BROKER_TTS_PYTHON=$Py"
Write-Host '  If you changed the path, update deploy\install-services.ps1 and re-run it.'
Write-Host '  Hand-editing the NSSM AppEnvironmentExtra instead? Strip EMPTY elements first:'
Write-Host '  REG_MULTI_SZ ends at the first empty string, so anything appended past a stray'
Write-Host '  blank is invisible to the service with no error anywhere.'
Write-Host '  Confirm either way with:  GET /v1/status -> media.speech_python / speech_isolated'
