<#
  AI-Platform lean installer. Targets an 8 GB-VRAM Windows box with no HuggingFace token and no
  media/image pipeline. Installs: the shell (admin) + Terminal Fun + optional Recipe Book, on
  gemma3:4b + bge-m3, with the native broker service (Ollama runs via its own app on :11434).

  Run (GUI window):  powershell -ExecutionPolicy Bypass -File install.ps1
  Run in-terminal:   powershell -ExecutionPolicy Bypass -File install.ps1 -Console
  Doctor only:       powershell -ExecutionPolicy Bypass -File install.ps1 -Check

  Design: the front-end (this process, non-elevated) collects inputs and tails a log; the actual
  provisioning re-launches this script with -Provision, elevated, which writes progress to the
  shared log + a DONE/FAIL marker. (Start-Process -Verb RunAs can't redirect stdout, hence the
  file-based log.)
#>
[CmdletBinding()]
param(
  [switch]$Check,                 # run the prereq doctor to the console and exit
  [switch]$Console,               # run the whole install in this terminal (no GUI window)
  [switch]$Provision,             # internal: run the elevated provisioning steps
  [switch]$Force,                 # bypass the existing-install guard
  [string]$AdminUser,
  [string]$AdminPass,
  [string]$EnabledApps,           # comma list, e.g. "terminal-fun,recipe-book"
  [string]$DockerMode,            # internal: 'desktop' or 'wsl' (which docker runtime to drive)
  [switch]$WithRecipeBook
)

$ErrorActionPreference = 'Stop'
$Root      = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent   # deploy/installer -> repo root
$Installer = $PSScriptRoot
# $env:TEMP can be the 8.3 short form (C:\Users\JUSTIN~1.LOW\...), which fails to resolve on boxes
# with 8.3 name generation disabled. USERPROFILE is the long form, so derive the temp base from it.
$TempBase  = Join-Path $env:USERPROFILE 'AppData\Local\Temp'
if (-not (Test-Path $TempBase)) { $TempBase = $env:TEMP }
# Human-readable install log lives in the install folder (deploy\logs\install.log - reachable via the
# menu's "Open the install folder"); the done/fail markers stay in temp.
$LogDir    = Join-Path $Root 'deploy\logs'
try { New-Item -ItemType Directory -Force -Path $LogDir -ErrorAction Stop | Out-Null } catch {}
$LogFile   = if (Test-Path $LogDir) { Join-Path $LogDir 'install.log' } else { Join-Path $TempBase 'ai-platform-install.log' }
$DoneFile  = Join-Path $TempBase 'ai-platform-install.done'
$FailFile  = Join-Path $TempBase 'ai-platform-install.fail'
$DockerBin = Join-Path $env:ProgramFiles 'Docker\Docker\resources\bin'
if (Test-Path $DockerBin) { $env:Path = "$DockerBin;$env:Path" }

# ---------------------------------------------------------------------------
# Prereq doctor
# ---------------------------------------------------------------------------
function Test-Prereqs {
  $r = @()
  # Docker. Dual-runtime: prefer Docker Desktop on Windows if it's installed; otherwise use the
  # Docker Engine inside WSL2 (Desktop is blocked/paid on some managed boxes). State: missing |
  # installed (CLI present, daemon down) | running. Mode: desktop | wsl | none. Fix='' - Docker isn't
  # auto-winget'd here; the installer sets it up per-mode (start Desktop, or install/start in WSL).
  $dockerMode = 'none'; $dockerState = 'missing'; $dockerDetail = 'no Docker (Desktop or WSL2)'
  $winDockerExe = Join-Path $env:ProgramFiles 'Docker\Docker\resources\bin\docker.exe'
  $hasWinDocker = (Test-Path $winDockerExe) -or [bool](Get-Command docker -ErrorAction SilentlyContinue)
  if ($hasWinDocker) {
    $dockerMode = 'desktop'; $dockerState = 'installed'; $dockerDetail = 'Docker Desktop installed, engine not running'
    & docker version 1>$null 2>$null
    if ($LASTEXITCODE -eq 0) { $dockerState = 'running'; $dockerDetail = 'Docker Desktop engine running' }
  }
  else {
    & wsl.exe -l -q 1>$null 2>$null
    if ($LASTEXITCODE -eq 0) {
      $dockerMode = 'wsl'; $dockerDetail = 'WSL2 present, Docker Engine not installed'
      & wsl.exe docker version 1>$null 2>$null
      if ($LASTEXITCODE -eq 0) {
        $dockerState = 'running'; $ver = (& wsl.exe docker --version 2>$null)
        $dockerDetail = if ($ver) { "WSL2: $($ver.ToString().Trim())" } else { 'WSL2 engine running' }
      }
      else {
        & wsl.exe docker --version 1>$null 2>$null
        if ($LASTEXITCODE -eq 0) { $dockerState = 'installed'; $dockerDetail = 'WSL2 Docker CLI present, daemon not running' }
      }
    }
  }
  $r += [pscustomobject]@{ Key = 'docker'; Name = 'Docker'; Ok = ($dockerState -eq 'running'); Detail = $dockerDetail; Fix = ''; State = $dockerState; Mode = $dockerMode }
  # NVIDIA GPU >= 8 GB
  $gpuOk = $false; $gpuDetail = 'no NVIDIA GPU detected'
  try {
    $mem = (& nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>$null | Select-Object -First 1)
    if ($mem) { $mib = [int]($mem.Trim()); $gpuDetail = "$([math]::Round($mib/1024,1)) GB VRAM"; $gpuOk = $mib -ge 7500 }
  } catch {}
  $r += [pscustomobject]@{ Key = 'gpu'; Name = 'NVIDIA GPU (>= 8 GB)'; Ok = $gpuOk; Detail = $gpuDetail; Fix = ''; State = '' }
  # Ollama
  $ollamaExe = Join-Path $env:USERPROFILE 'AppData\Local\Programs\Ollama\ollama.exe'
  $ollamaOk = (Test-Path $ollamaExe) -or [bool](Get-Command ollama -ErrorAction SilentlyContinue)
  $r += [pscustomobject]@{ Key = 'ollama'; Name = 'Ollama'; Ok = $ollamaOk; Detail = $(if ($ollamaOk) { 'installed' } else { 'not found' }); Fix = 'Ollama.Ollama'; State = '' }
  # Python 3.11+
  $pyOk = $false; $pyDetail = 'not found'
  try { $v = (& python --version 2>&1); if ($v -match '(\d+)\.(\d+)') { $pyDetail = $v.ToString().Trim(); $pyOk = ([int]$Matches[1] -eq 3 -and [int]$Matches[2] -ge 10) } } catch {}
  $r += [pscustomobject]@{ Key = 'python'; Name = 'Python 3.11'; Ok = $pyOk; Detail = $pyDetail; Fix = 'Python.Python.3.11'; State = '' }
  # Disk (system drive) >= 20 GB. DriveInfo (not Get-PSDrive, which hangs on dead network mounts).
  $free = [math]::Round((New-Object System.IO.DriveInfo($env:SystemDrive)).AvailableFreeSpace / 1GB, 1)
  $r += [pscustomobject]@{ Key = 'disk'; Name = 'Disk (>= 20 GB free)'; Ok = ($free -ge 20); Detail = "$free GB free on $env:SystemDrive"; Fix = ''; State = '' }
  return $r
}

function Test-ExistingInstall {
  if (Get-Service platform-broker -ErrorAction SilentlyContinue) { return 'platform-broker service exists' }
  try { $ps = & wsl.exe docker ps --format '{{.Names}}' 2>$null; if ($ps -match 'platform-') { return 'platform-* containers are running' } } catch {}
  if (Test-Path (Join-Path $Root 'deploy\.env')) { return 'deploy\.env already exists' }
  return $null
}

# Locate the Docker Desktop launcher (to start its engine in desktop mode).
function Get-DockerDesktopExe {
  foreach ($base in @($env:ProgramFiles, ${env:ProgramFiles(x86)})) {
    if ($base) { $p = Join-Path $base 'Docker\Docker\Docker Desktop.exe'; if (Test-Path $p) { return $p } }
  }
  return $null
}
# Convert a Windows path (C:\a\b) to a WSL /mnt path (/mnt/c/a/b) so `wsl docker` can read it.
function ConvertTo-WslPath($winPath) {
  $p = ($winPath -replace '\\', '/')
  if ($p -match '^([A-Za-z]):/(.*)$') { return "/mnt/$($Matches[1].ToLower())/$($Matches[2])" }
  return $p
}
# The Windows host IP as seen from inside WSL (the default-route gateway). Dynamic across restarts.
# Run wsl in a BACKGROUND JOB: wsl.exe can wedge when invoked directly in an interactive console, and
# the job gives a hard timeout so host detection can never hang the whole install.
function Get-WslWindowsHost {
  $job = Start-Job -ScriptBlock { (& wsl.exe sh -c 'ip route show default' 2>$null) -join "`n" }
  $ip = $null
  if (Wait-Job $job -Timeout 25) { $o = (Receive-Job $job); if ($o -match 'via\s+(\d+\.\d+\.\d+\.\d+)') { $ip = $Matches[1] } }
  else { Stop-Job $job -ErrorAction SilentlyContinue }
  Remove-Job $job -Force -ErrorAction SilentlyContinue
  return $ip
}
# Ensure the WSL2 Docker daemon is running (systemd-managed; no Windows elevation needed). Also via a
# job (same interactive-console-hang avoidance) with a timeout.
function Start-DockerEngineWsl {
  $job = Start-Job -ScriptBlock { & wsl.exe -u root systemctl start docker 2>&1 | Out-Null }
  Wait-Job $job -Timeout 30 | Out-Null; Stop-Job $job -ErrorAction SilentlyContinue; Remove-Job $job -Force -ErrorAction SilentlyContinue
}

# Install Docker Engine + compose plugin into the default WSL2 distro (run as root inside WSL; no
# Windows elevation needed). Used when WSL2 is present but Docker isn't. Returns $true on success.
function Install-DockerInWsl {
  $sh = @'
#!/usr/bin/env bash
set -e
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq ca-certificates curl
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
. /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $VERSION_CODENAME stable" > /etc/apt/sources.list.d/docker.list
apt-get update -qq
apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
U="$(getent passwd 1000 | cut -d: -f1)"; if [ -n "$U" ]; then usermod -aG docker "$U" || true; fi
if ! grep -qs systemd=true /etc/wsl.conf; then printf '[boot]\nsystemd=true\n' >> /etc/wsl.conf; fi
systemctl enable --now docker 2>/dev/null || true
'@
  $tmp = Join-Path $TempBase 'ai-platform-docker-wsl.sh'
  [System.IO.File]::WriteAllText($tmp, ($sh -replace "`r`n", "`n"))
  & wsl.exe -u root bash (ConvertTo-WslPath $tmp)
  return ($LASTEXITCODE -eq 0)
}

# ---------------------------------------------------------------------------
# Provisioning (runs elevated via -Provision; logs to $LogFile + a marker)
# ---------------------------------------------------------------------------
function Write-Log($m) {
  $line = "{0}  {1}" -f (Get-Date -Format 'HH:mm:ss'), $m
  Add-Content -Path $LogFile -Value $line -Encoding utf8
  Write-Host "  $line" -ForegroundColor DarkCyan   # echo live (console runs this in-process); harmless in the GUI's hidden subprocess
}

function Invoke-Provision {
  Remove-Item $DoneFile, $FailFile -ErrorAction SilentlyContinue
  # Native tools (ollama, docker) write progress to stderr; with `2>&1 | Tee-Object` under the
  # script's ErrorActionPreference='Stop', that benign stderr is wrapped as a terminating error
  # (PS 5.1 NativeCommandError). Relax it here and gate on explicit exit codes instead.
  $ErrorActionPreference = 'Continue'
  try {
    Write-Log "=== AI-Platform lean install ==="
    Write-Log "repo root: $Root"
    Write-Log "full log: $LogFile"

    # 1. config: deploy/.env from the lean template + roles.lean.json -> broker roles.json
    Write-Log 'writing deploy\.env (lean) ...'
    $tmpl = Get-Content (Join-Path $Installer 'env.lean.example') -Raw
    $tmpl = $tmpl.Replace('{{ADMIN_USER}}', $AdminUser).Replace('{{ADMIN_PASSWORD}}', $AdminPass).Replace('{{ENABLED_APPS}}', $EnabledApps)
    Set-Content -Path (Join-Path $Root 'deploy\.env') -Value $tmpl -Encoding utf8
    Copy-Item (Join-Path $Installer 'roles.lean.json') (Join-Path $Root 'services\broker\roles.json') -Force
    if ($DockerMode -eq 'wsl') {
      # WSL containers reach the native Windows broker via host.docker.internal = the WSL->Windows
      # gateway IP (dynamic; detected now). Desktop mode needs nothing (host-gateway maps natively).
      Write-Log 'detecting the WSL -> Windows host IP (wsl ip route, in a background job)...'
      $winHost = Get-WslWindowsHost
      if ($winHost) { Add-Content -Path (Join-Path $Root 'deploy\.env') -Value "WINDOWS_HOST=$winHost" -Encoding utf8; Write-Log "WINDOWS_HOST=$winHost (rails reach the native broker/ollama here)" }
      else { Write-Log 'WARNING: could not detect the WSL->Windows host IP; containers may not reach the broker.' }
    }
    Write-Log 'config written.'

    # 2. native broker service. This is the ONLY step that needs admin (registering the LocalSystem
    # NSSM service), so we elevate JUST this and keep the rest of provisioning non-elevated - `wsl.exe`
    # deadlocks when invoked from an elevated process, so the WSL/Docker steps below must NOT be
    # elevated. install-native has no wsl calls, so elevating it is safe. (Ollama runs via its own app
    # on :11434 - no second server; the full 24 GB stack keeps the Ollama NSSM service.)
    Write-Log 'installing the native broker service (approve the UAC prompt that appears)...'
    $nativeArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $Installer 'install-native.ps1'), '-PlatformRoot', $Root, '-SkipOllama')
    $np = Start-Process powershell -Verb RunAs -Wait -PassThru -ArgumentList $nativeArgs
    if ($np.ExitCode -ne 0) { throw "install-native.ps1 failed (exit $($np.ExitCode)); see deploy\logs\platform-broker.err.log" }

    # 3. make sure the Ollama app is serving on :11434 (it usually auto-starts right after the winget
    # install; launch it if not), then pull the lean models into the user's model store.
    Write-Log 'ensuring the Ollama app is serving on :11434 ...'
    function Test-OllamaUp { try { Invoke-WebRequest 'http://127.0.0.1:11434/api/version' -TimeoutSec 3 -UseBasicParsing | Out-Null; return $true } catch { return $false } }
    if (-not (Test-OllamaUp)) {
      $app = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama app.exe'
      if (Test-Path $app) { Write-Log 'starting the Ollama app...'; Start-Process $app | Out-Null }
      for ($i = 0; $i -lt 15 -and -not (Test-OllamaUp); $i++) { Start-Sleep 2 }
    }
    if (-not (Test-OllamaUp)) { throw 'Ollama is not serving on :11434 - start the Ollama app and re-run.' }
    Write-Log 'Ollama is up; pulling the lean models (~4.5 GB total)...'
    foreach ($m in @('gemma3:4b', 'bge-m3')) {
      Write-Log "ollama pull $m (progress below)..."
      & ollama pull $m   # direct to the console: ollama renders its own progress bar; piping it
      # through Tee mangles the bar (mojibake + red + repeated lines) since it's no longer a TTY.
      if ($LASTEXITCODE -ne 0) { throw "ollama pull $m failed (exit $LASTEXITCODE)" }
      Write-Log "  $m pulled."
    }

    # 4. bundled compose via the detected Docker runtime. WSL mode: build from /mnt/c and reach the
    # native broker via the injected WINDOWS_HOST. Desktop mode: native docker + host-gateway.
    Write-Log "building + starting containers via docker ($DockerMode); first build takes several minutes..."
    if ($DockerMode -eq 'wsl') {
      Write-Log 'starting the WSL Docker daemon...'
      Start-DockerEngineWsl
      $envA = ConvertTo-WslPath (Join-Path $Root 'deploy\.env')
      $compA = ConvertTo-WslPath (Join-Path $Installer 'docker-compose.installer.yml')
      $cargs = @('docker', 'compose', '--progress', 'plain', '--env-file', $envA, '-f', $compA)
      if ($WithRecipeBook) { $cargs += @('--profile', 'recipe-book') }
      $cargs += @('up', '-d', '--build')
      & wsl.exe @cargs 2>&1 | Tee-Object -FilePath $LogFile -Append
    }
    else {
      $envA = Join-Path $Root 'deploy\.env'
      $compA = Join-Path $Installer 'docker-compose.installer.yml'
      $cargs = @('compose', '--progress', 'plain', '--env-file', $envA, '-f', $compA)
      if ($WithRecipeBook) { $cargs += @('--profile', 'recipe-book') }
      $cargs += @('up', '-d', '--build')
      & docker @cargs 2>&1 | Tee-Object -FilePath $LogFile -Append
    }
    if ($LASTEXITCODE -ne 0) { throw "docker compose up failed ($DockerMode mode; see the log)." }

    # 5. wait for the gateway
    Write-Log 'waiting for the gateway...'
    for ($i = 0; $i -lt 60; $i++) {
      try { Invoke-WebRequest 'http://localhost:1111/api/platform/healthz' -TimeoutSec 3 -UseBasicParsing | Out-Null; break } catch { Start-Sleep 2 }
    }

    # 6. WSL mode: keep the VM alive. WSL2 shuts an idle VM down (when no session is attached), which
    # stops the containers; a logon-triggered `wsl --exec sleep infinity` holds it up. Registered as a
    # current-user task (no admin) and started now so the platform stays up this session too.
    if ($DockerMode -eq 'wsl') {
      Write-Log 'registering a logon keep-alive (holds the WSL VM + containers up)...'
      try {
        $ka = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-WindowStyle Hidden -NoProfile -Command "wsl.exe --exec sleep infinity"'
        $kt = New-ScheduledTaskTrigger -AtLogOn
        Register-ScheduledTask -TaskName 'AI-Platform WSL keep-alive' -Action $ka -Trigger $kt -Force -Description 'Keeps the WSL2 VM (and AI-Platform containers) running.' | Out-Null
        Start-ScheduledTask -TaskName 'AI-Platform WSL keep-alive' -ErrorAction SilentlyContinue
        Write-Log 'keep-alive task registered + started.'
      }
      catch { Write-Log "keep-alive task could not be registered ($($_.Exception.Message)); the VM may idle-shutdown - create it manually if so." }
    }

    Write-Log 'DONE. Platform is up at http://localhost:1111  (use localhost; platform.localhost may be proxied on managed browsers).'
    New-Item -ItemType File -Path $DoneFile -Force | Out-Null
  } catch {
    Write-Log "ERROR: $($_.Exception.Message)"
    Set-Content -Path $FailFile -Value $_.Exception.Message -Encoding utf8
  }
}

# ---------------------------------------------------------------------------
# Console (no-window) installer - the same flow as the GUI, driven in the terminal.
# ---------------------------------------------------------------------------
function Invoke-ConsoleInstall {
  function CW($text, $color = 'Gray') { Write-Host $text -ForegroundColor $color }
  function Prereq($rs, $key) { $rs | Where-Object { $_.Key -eq $key } }
  function HardReady($rs) { $d = Prereq $rs 'docker'; $o = Prereq $rs 'ollama'; return ($d -and $d.State -eq 'running' -and $o -and $o.Ok) }

  function Banner {
    try { Clear-Host } catch {}
    CW ''
    CW '  ==============================================================' 'DarkCyan'
    CW '        A I - P L A T F O R M      lean install (terminal)' 'White'
    CW '  ==============================================================' 'DarkCyan'
    CW '   one GPU, one broker, a handful of rails. lets go.' 'DarkGray'
  }

  function Show-Doctor {
    $rs = Test-Prereqs
    CW ''
    CW '  Prerequisites' 'Cyan'
    foreach ($r in $rs) {
      if ($r.Ok) { Write-Host '   [OK]  ' -ForegroundColor Green -NoNewline }
      else { Write-Host '   [ - ] ' -ForegroundColor Yellow -NoNewline }
      Write-Host ("{0,-22} {1}" -f $r.Name, $r.Detail)
    }
    return $rs
  }

  Banner
  $rs = Show-Doctor

  # 1. offer to install the missing, fixable prerequisites via winget
  $missing = $rs | Where-Object { -not $_.Ok -and $_.Fix }
  if ($missing) {
    CW ''
    CW ('  Missing: ' + (($missing | ForEach-Object { $_.Name }) -join ', ')) 'Yellow'
    if ((Read-Host '  Install them now with winget? [Y/n]') -notmatch '^[Nn]') {
      foreach ($p in $missing) {
        CW "  installing $($p.Fix) (this can take several minutes)..." 'DarkCyan'
        Start-Process winget -Wait -ArgumentList @('install', '--id', $p.Fix, '-e', '--accept-source-agreements', '--accept-package-agreements')
      }
      $rs = Show-Doctor
    }
  }

  # 2. Docker must be RUNNING (Docker Desktop or the WSL2 engine). If missing: in WSL mode offer to
  # install it; otherwise guide. If installed-but-stopped: start Desktop / the WSL daemon, spin-wait.
  $d = Prereq $rs 'docker'
  if ($d.State -eq 'missing') {
    if ($d.Mode -eq 'wsl') {
      CW ''; CW '  WSL2 is present but Docker Engine is not installed there.' 'Yellow'
      if ((Read-Host '  Install Docker Engine into WSL2 now? [Y/n]') -notmatch '^[Nn]') {
        CW '  installing Docker Engine into WSL2 (a few minutes)...' 'DarkCyan'
        Install-DockerInWsl | Out-Null
        $rs = Show-Doctor; $d = Prereq $rs 'docker'
      }
    }
    else {
      CW ''; CW '  Docker is not available. Install Docker Desktop (where your org allows it) or set' 'Red'
      CW '  up Docker on WSL2, then re-run. Aborting.' 'Red'; return
    }
  }
  if ($d.State -eq 'missing') { CW '  Docker still not available - aborting.' 'Red'; return }
  if ($d.State -ne 'running') {
    CW ''
    if ($d.Mode -eq 'desktop') {
      $dd = Get-DockerDesktopExe
      if ($dd) { CW '  starting Docker Desktop - accept its license, then hang tight...' 'DarkCyan'; try { Start-Process $dd | Out-Null } catch {} }
      else { CW '  start Docker Desktop and accept its license...' 'DarkCyan' }
    }
    else { CW '  starting the WSL2 Docker daemon...' 'DarkCyan'; Start-DockerEngineWsl }
    $spin = '|', '/', '-', '\'; $i = 0; $deadline = (Get-Date).AddMinutes(15)
    while ((Get-Date) -lt $deadline) {
      $d = Prereq (Test-Prereqs) 'docker'
      if ($d.State -eq 'running') { break }
      Write-Host ("`r   {0}  waiting for the Docker engine...   " -f $spin[$i % 4]) -ForegroundColor DarkCyan -NoNewline
      $i++; Start-Sleep -Milliseconds 700
    }
    if ($d.State -eq 'running') { Write-Host "`r   [OK]  Docker engine is up.                 " -ForegroundColor Green }
    else { Write-Host "`r   [ - ] Docker engine did not come up.       " -ForegroundColor Yellow }
  }

  # 3. re-read; Ollama just needs to be installed (its own app serves :11434; provisioning verifies).
  $rs = Test-Prereqs
  if (-not (HardReady $rs)) {
    CW ''
    CW '  Not ready: need Docker running + Ollama installed. Fix those and re-run.' 'Red'
    return
  }
  $dmode = (Prereq $rs 'docker').Mode
  CW ''
  CW "  All set - Docker ($dmode) is up and Ollama is installed." 'Green'

  # 4. existing-install guard
  $ex = Test-ExistingInstall
  if ($ex -and -not $Force) {
    CW ''
    CW "  An existing platform install was detected ($ex)." 'Red'
    CW '  This installer refuses to touch it - run on a clean machine/VM (or pass -Force).' 'Red'
    return
  }

  # 5. collect inputs
  CW ''
  CW '  Super-admin account' 'Cyan'
  $u = Read-Host '   Username [admin]'; if (-not $u) { $u = 'admin' }
  function Read-Secret($prompt) {
    $sec = Read-Host $prompt -AsSecureString
    $b = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringAuto($b) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b) }
  }
  # Enter twice and require a match - a single typo here means you can't log in and must reinstall.
  $pass = ''
  while (-not $pass) {
    $p1 = Read-Secret '   Password'
    if (-not $p1) { CW '   Password cannot be empty.' 'Yellow'; continue }
    $p2 = Read-Secret '   Confirm password'
    if ($p1 -ne $p2) { CW '   Passwords do not match - re-enter both.' 'Yellow'; continue }
    $pass = $p1
  }
  CW ''
  CW '  Rails: Admin shell (always) + Terminal Fun (default).' 'Cyan'
  $withRecipe = (Read-Host '   Also install Recipe Book? [y/N]') -match '^[Yy]'
  $enabled = @('terminal-fun'); if ($withRecipe) { $enabled += 'recipe-book' }

  # 6. Provision IN THIS (non-elevated) process, so WSL/Docker steps don't hang. Invoke-Provision
  # elevates ONLY the broker-service step (its own UAC prompt); everything else runs here and streams
  # live via Write-Log / Tee-Object (no hidden window, so you can see it's not stuck).
  CW ''
  CW '  Provisioning (a UAC prompt will appear for the broker service)...' 'Cyan'
  CW "  Full log: $LogFile" 'DarkGray'
  Set-Content -Path $LogFile -Value '' -Encoding utf8
  $script:AdminUser = $u; $script:AdminPass = $pass; $script:EnabledApps = ($enabled -join ',')
  $script:DockerMode = $dmode; $script:WithRecipeBook = [bool]$withRecipe
  Invoke-Provision
  if (Test-Path $DoneFile) { CW ''; CW '  Done! Open  http://localhost:1111  and log in.' 'Green'; CW '  (use localhost - platform.localhost may be blocked by a managed-browser proxy)' 'DarkGray' }
  elseif (Test-Path $FailFile) { CW ''; CW ('  Install failed: ' + (Get-Content $FailFile -Raw)) 'Red' }
  else { CW ''; CW '  Provisioning ended without a clear result - check the log at:' 'Yellow'; CW "  $LogFile" 'Yellow' }
}

# ---------------------------------------------------------------------------
# entrypoints
# ---------------------------------------------------------------------------
if ($Check) {
  "AI-Platform prereq check:`n"
  Test-Prereqs | ForEach-Object { "{0} {1,-24} {2}" -f $(if ($_.Ok) { '[ OK ]' } else { '[FAIL]' }), $_.Name, $_.Detail }
  $ex = Test-ExistingInstall
  if ($ex) { "`n[WARN] existing install detected: $ex (installer would refuse without -Force)" }
  return
}
if ($Console) { Invoke-ConsoleInstall; return }
if ($Provision) { Invoke-Provision; return }

# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$form = New-Object Windows.Forms.Form
$form.Text = 'AI-Platform Installer (lean)'
$form.Size = New-Object Drawing.Size(680, 640)
$form.StartPosition = 'CenterScreen'
$form.Font = New-Object Drawing.Font('Segoe UI', 9)

function New-Label($text, $x, $y, $w, $bold) {
  $l = New-Object Windows.Forms.Label
  $l.Text = $text; $l.Location = New-Object Drawing.Point($x, $y); $l.AutoSize = $true
  if ($bold) { $l.Font = New-Object Drawing.Font('Segoe UI', 10, [Drawing.FontStyle]::Bold) }
  $form.Controls.Add($l); return $l
}

New-Label '1. Prerequisites' 16 12 0 $true | Out-Null
$prereqBox = New-Object Windows.Forms.TextBox
$prereqBox.Multiline = $true; $prereqBox.ReadOnly = $true; $prereqBox.ScrollBars = 'Vertical'
$prereqBox.Location = New-Object Drawing.Point(16, 36); $prereqBox.Size = New-Object Drawing.Size(500, 96)
$prereqBox.Font = New-Object Drawing.Font('Consolas', 9)
$form.Controls.Add($prereqBox)

$btnCheck = New-Object Windows.Forms.Button
$btnCheck.Text = 'Re-check'; $btnCheck.Location = New-Object Drawing.Point(528, 36); $btnCheck.Size = New-Object Drawing.Size(120, 28)
$form.Controls.Add($btnCheck)
$btnFix = New-Object Windows.Forms.Button
$btnFix.Text = 'Install missing'; $btnFix.Location = New-Object Drawing.Point(528, 70); $btnFix.Size = New-Object Drawing.Size(120, 28)
$form.Controls.Add($btnFix)

New-Label '2. Super-admin account' 16 142 0 $true | Out-Null
New-Label 'Username' 16 170 0 $false | Out-Null
$txtUser = New-Object Windows.Forms.TextBox; $txtUser.Location = New-Object Drawing.Point(110, 167); $txtUser.Size = New-Object Drawing.Size(180, 24); $txtUser.Text = 'admin'; $form.Controls.Add($txtUser)
New-Label 'Password' 310 170 0 $false | Out-Null
$txtPass = New-Object Windows.Forms.TextBox; $txtPass.Location = New-Object Drawing.Point(380, 167); $txtPass.Size = New-Object Drawing.Size(180, 24); $txtPass.UseSystemPasswordChar = $true; $form.Controls.Add($txtPass)

New-Label '3. Rails to install' 16 206 0 $true | Out-Null
$chkAdmin = New-Object Windows.Forms.CheckBox; $chkAdmin.Text = 'Admin shell (required)'; $chkAdmin.Checked = $true; $chkAdmin.Enabled = $false; $chkAdmin.Location = New-Object Drawing.Point(16, 232); $chkAdmin.AutoSize = $true; $form.Controls.Add($chkAdmin)
$chkTerm = New-Object Windows.Forms.CheckBox; $chkTerm.Text = 'Terminal Fun'; $chkTerm.Checked = $true; $chkTerm.Location = New-Object Drawing.Point(210, 232); $chkTerm.AutoSize = $true; $form.Controls.Add($chkTerm)
$chkRecipe = New-Object Windows.Forms.CheckBox; $chkRecipe.Text = 'Recipe Book (ships with seed)'; $chkRecipe.Checked = $false; $chkRecipe.Location = New-Object Drawing.Point(340, 232); $chkRecipe.AutoSize = $true; $form.Controls.Add($chkRecipe)

$btnInstall = New-Object Windows.Forms.Button
$btnInstall.Text = 'Install'; $btnInstall.Location = New-Object Drawing.Point(16, 268); $btnInstall.Size = New-Object Drawing.Size(140, 34)
$btnInstall.Font = New-Object Drawing.Font('Segoe UI', 10, [Drawing.FontStyle]::Bold)
$form.Controls.Add($btnInstall)
$btnLaunch = New-Object Windows.Forms.Button
$btnLaunch.Text = 'Open :1111'; $btnLaunch.Location = New-Object Drawing.Point(168, 268); $btnLaunch.Size = New-Object Drawing.Size(140, 34); $btnLaunch.Enabled = $false
$form.Controls.Add($btnLaunch)

$log = New-Object Windows.Forms.TextBox
$log.Multiline = $true; $log.ReadOnly = $true; $log.ScrollBars = 'Vertical'
$log.Location = New-Object Drawing.Point(16, 314); $log.Size = New-Object Drawing.Size(632, 268)
$log.Font = New-Object Drawing.Font('Consolas', 9)
$form.Controls.Add($log)

$script:prereqs = @()
$script:pendingInstall = $false     # user asked to install; waiting on the Docker engine to come up
$script:dockerAnnounced = $false    # printed "Docker detected" once
$script:ollamaAnnounced = $false    # printed "Ollama detected" once
$script:dockerLaunched = $false     # launched Docker Desktop once (after it appears installed)

function Get-Prereq($key) { $script:prereqs | Where-Object { $_.Key -eq $key } }

# The two prerequisites the build genuinely can't proceed without: a *running* container engine
# and an *installed* Ollama (the elevated provisioner starts Ollama's service and pulls models).
# GPU / Python / disk stay soft (a skippable warning).
function Test-HardReady {
  $d = Get-Prereq 'docker'; $o = Get-Prereq 'ollama'
  return ($d -and $d.State -eq 'running' -and $o -and $o.Ok)
}

function Refresh-Prereqs {
  $script:prereqs = Test-Prereqs
  $prereqBox.Text = ($script:prereqs | ForEach-Object { "{0} {1,-22} {2}" -f $(if ($_.Ok) { '[OK]  ' } else { '[MISS]' }), $_.Name, $_.Detail }) -join "`r`n"
}

# Launch the elevated provisioning and tail its log. Called directly when Docker is already up,
# or by the watcher once the engine comes online.
function Start-Provisioning {
  $ex = Test-ExistingInstall
  if ($ex -and -not $Force) { [Windows.Forms.MessageBox]::Show("An existing platform install was detected ($ex).`nThis installer refuses to touch it. Run on a clean machine/VM.", 'Installer'); return }
  $enabled = @('terminal-fun'); if ($chkRecipe.Checked) { $enabled += 'recipe-book' }
  $btnInstall.Enabled = $false
  $log.AppendText("starting provisioning (elevated)...`r`n")
  Set-Content -Path $LogFile -Value '' -Encoding utf8
  $script:pos = 0
  $pargs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"", '-Provision',
    '-AdminUser', $txtUser.Text, '-AdminPass', $txtPass.Text, '-EnabledApps', ($enabled -join ','), '-DockerMode', (Get-Prereq 'docker').Mode)
  if ($chkRecipe.Checked) { $pargs += '-WithRecipeBook' }
  # Non-elevated subprocess: Invoke-Provision elevates only the broker-service step (its own UAC),
  # keeping WSL/Docker calls out of an elevated context (where wsl.exe hangs).
  Start-Process powershell -WindowStyle Hidden -ArgumentList $pargs
  $timer = New-Object Windows.Forms.Timer; $timer.Interval = 800
  $timer.Add_Tick({
      if (Test-Path $LogFile) {
        $all = Get-Content $LogFile -Raw -ErrorAction SilentlyContinue
        if ($all -and $all.Length -gt $script:pos) { $log.AppendText($all.Substring($script:pos)); $script:pos = $all.Length }
      }
      if (Test-Path $DoneFile) { $timer.Stop(); $btnLaunch.Enabled = $true; $btnInstall.Enabled = $true; [Windows.Forms.MessageBox]::Show('Install complete. Click "Open :1111".', 'Installer') }
      elseif (Test-Path $FailFile) { $timer.Stop(); $btnInstall.Enabled = $true; [Windows.Forms.MessageBox]::Show("Install failed:`n" + (Get-Content $FailFile -Raw), 'Installer') }
    }.GetNewClosure())
  $timer.Start()
}

# Poll every 3s for Docker + Ollama to come up after an install/launch, then auto-continue.
$script:watchTimer = New-Object Windows.Forms.Timer
$script:watchTimer.Interval = 3000
$script:watchTimer.Add_Tick({
    Refresh-Prereqs
    $d = Get-Prereq 'docker'; $ol = Get-Prereq 'ollama'
    # Docker finished installing in the background but its engine isn't up yet: launch it once.
    if ($d -and $d.State -eq 'installed' -and -not $script:dockerLaunched) { $script:dockerLaunched = $true; Start-DockerDesktop }
    if ($d -and $d.State -eq 'running' -and -not $script:dockerAnnounced) { $script:dockerAnnounced = $true; $log.AppendText("Docker detected.`r`n") }
    if ($ol -and $ol.Ok -and -not $script:ollamaAnnounced) { $script:ollamaAnnounced = $true; $log.AppendText("Ollama detected.`r`n") }
    if (Test-HardReady) {
      if ($script:pendingInstall) {
        $script:pendingInstall = $false
        $script:watchTimer.Stop()
        $log.AppendText("prerequisites ready, continuing.`r`n")
        Start-Provisioning
      }
      else { $script:watchTimer.Stop() }   # ready, nothing queued
    }
  }.GetNewClosure())

Refresh-Prereqs

# Bring the detected Docker runtime up: launch Docker Desktop, or start the WSL2 daemon.
function Start-DockerDesktop {
  $d = Get-Prereq 'docker'
  if ($d.Mode -eq 'desktop') {
    $dd = Get-DockerDesktopExe
    if ($dd) { $log.AppendText("starting Docker Desktop - accept its license, then wait for the engine...`r`n"); try { Start-Process $dd | Out-Null } catch {} }
    else { $log.AppendText("start Docker Desktop and accept its license to start the engine.`r`n") }
  }
  else { $log.AppendText("starting the WSL2 Docker daemon (systemctl start docker)...`r`n"); Start-DockerEngineWsl }
}

# Kick off a winget install WITHOUT blocking the UI thread. The watcher polls for completion via
# the prereq re-check, so the window stays responsive during a multi-minute Docker download.
function Start-WingetInstall($id) {
  $log.AppendText("winget install $id (running in the background; this can take several minutes)...`r`n")
  try { Start-Process winget -ArgumentList @('install', '--id', $id, '-e', '--accept-source-agreements', '--accept-package-agreements') | Out-Null }
  catch { $log.AppendText("  could not launch winget for ${id}: $($_.Exception.Message)`r`n") }
}

# Re-check: manual refresh; if both hard prereqs are ready and an install was queued, continue now.
$btnCheck.Add_Click({
    Refresh-Prereqs
    if ((Test-HardReady) -and $script:pendingInstall) { $script:pendingInstall = $false; $script:watchTimer.Stop(); Start-Provisioning }
  })

# Install missing: winget the fixable gaps, then (if a hard prereq still isn't ready) launch
# Docker Desktop and start watching so a later "Install" continues automatically.
$btnFix.Add_Click({
    $missing = $script:prereqs | Where-Object { -not $_.Ok -and $_.Fix }
    if (-not $missing) { $log.AppendText("nothing to install - all fixable prerequisites are present.`r`n"); return }
    $script:dockerLaunched = $false
    foreach ($p in $missing) { Start-WingetInstall $p.Fix }
    Refresh-Prereqs
    if (-not (Test-HardReady)) {
      $d = Get-Prereq 'docker'
      if ($d -and $d.State -eq 'installed') { $script:dockerLaunched = $true; Start-DockerDesktop }
      $log.AppendText("watching for prerequisites (auto-continues once Docker + Ollama are ready)...`r`n")
      $script:watchTimer.Start()
    }
  })

# Install: proceed when Docker is running AND Ollama is installed; otherwise install/launch the
# missing hard prereq(s) and continue automatically once both are ready. GPU/Python/disk stay a
# soft, skippable warning.
$btnInstall.Add_Click({
    if (-not $txtPass.Text) { [Windows.Forms.MessageBox]::Show('Set a super-admin password.', 'Installer'); return }
    Refresh-Prereqs
    $d = Get-Prereq 'docker'; $o = Get-Prereq 'ollama'
    $soft = $script:prereqs | Where-Object { -not $_.Ok -and $_.Key -notin @('docker', 'ollama') }
    if ($soft) { if ([Windows.Forms.MessageBox]::Show("Unmet prerequisites:`n" + (($soft | ForEach-Object { $_.Name }) -join ', ') + "`n`nContinue anyway?", 'Installer', 'YesNo') -ne 'Yes') { return } }

    if (Test-HardReady) { Start-Provisioning; return }

    # A hard prereq is missing / not up: install what's missing, launch Docker, then let the
    # watcher continue once BOTH the engine is running and Ollama is installed.
    $script:pendingInstall = $true
    $script:dockerLaunched = $false
    if ($d.State -eq 'missing') {
      if ($d.Mode -eq 'wsl') { $log.AppendText("installing Docker Engine into WSL2 (a few minutes)...`r`n"); Install-DockerInWsl | Out-Null }
      else { [Windows.Forms.MessageBox]::Show("Docker isn't available. Install Docker Desktop (where allowed) or set up Docker on WSL2, then re-run.", 'Installer'); $script:pendingInstall = $false; return }
    }
    elseif ($d.State -eq 'installed') { $script:dockerLaunched = $true; Start-DockerDesktop }
    if (-not $o.Ok) { Start-WingetInstall 'Ollama.Ollama' }
    $log.AppendText("waiting for Docker + Ollama; the install continues automatically once both are ready...`r`n")
    $script:watchTimer.Start()
  })

$btnLaunch.Add_Click({ Start-Process 'http://platform.localhost:1111' })

[void]$form.ShowDialog()
