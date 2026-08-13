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
  # internal: which container runtime to drive. 'podman' (daemonless, Hyper-V or WSL machine),
  # 'desktop' (Docker Desktop) or 'wsl' (Docker Engine inside WSL2). -DockerMode is kept as an
  # alias so older invocations and docs keep working.
  [Alias('DockerMode')]
  [ValidateSet('', 'podman', 'desktop', 'wsl')]
  [string]$RuntimeMode,
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
  # Container runtime. Tri-runtime, in preference order:
  #   podman  - daemonless; Linux containers run in a `podman machine` VM (Hyper-V provider keeps
  #             WSL out of the picture entirely, which matters on boxes where WSL2 is unstable).
  #             Driven with the standalone docker-compose.exe over Podman's Docker-compatible API.
  #   desktop - Docker Desktop on Windows.
  #   wsl     - Docker Engine inside WSL2 (Desktop is blocked/paid on some managed boxes).
  # State: missing | installed (CLI present, no engine/machine running) | running.
  # Mode: podman | desktop | wsl | none.
  # Fix='' - none of these are auto-winget'd here; the installer sets each up per-mode.
  $rtMode = 'none'; $rtState = 'missing'; $rtDetail = 'no container runtime (Podman or Docker)'
  $podmanExe = Join-Path $env:LOCALAPPDATA 'Programs\Podman\podman.exe'
  $hasPodman = (Test-Path $podmanExe) -or [bool](Get-Command podman -ErrorAction SilentlyContinue)
  $winDockerExe = Join-Path $env:ProgramFiles 'Docker\Docker\resources\bin\docker.exe'
  $hasWinDocker = (Test-Path $winDockerExe) -or [bool](Get-Command docker -ErrorAction SilentlyContinue)
  if ($hasPodman) {
    $rtMode = 'podman'; $rtState = 'installed'
    $machines = @(& podman machine list --noheading 2>$null | Where-Object { $_ -match '\S' })
    $rtDetail = if ($machines.Count -eq 0) { 'Podman installed, no machine created yet' } else { 'Podman machine created, not running' }
    # `podman info` talks to the machine, so a zero exit means the VM is up and serving.
    & podman info 1>$null 2>$null
    if ($LASTEXITCODE -eq 0) {
      $rtState = 'running'
      $pv = (& podman --version 2>$null)
      $rtDetail = if ($pv) { "$($pv.ToString().Trim()) (machine running)" } else { 'Podman machine running' }
      if (-not (Get-Command docker-compose -ErrorAction SilentlyContinue)) {
        $rtDetail += ' - docker-compose.exe MISSING'
        $rtState = 'installed'   # can't compose without it, so don't claim ready
      }
    }
  }
  elseif ($hasWinDocker) {
    $rtMode = 'desktop'; $rtState = 'installed'; $rtDetail = 'Docker Desktop installed, engine not running'
    & docker version 1>$null 2>$null
    if ($LASTEXITCODE -eq 0) { $rtState = 'running'; $rtDetail = 'Docker Desktop engine running' }
  }
  else {
    & wsl.exe -l -q 1>$null 2>$null
    if ($LASTEXITCODE -eq 0) {
      $rtMode = 'wsl'; $rtDetail = 'WSL2 present, Docker Engine not installed'
      & wsl.exe docker version 1>$null 2>$null
      if ($LASTEXITCODE -eq 0) {
        $rtState = 'running'; $ver = (& wsl.exe docker --version 2>$null)
        $rtDetail = if ($ver) { "WSL2: $($ver.ToString().Trim())" } else { 'WSL2 engine running' }
      }
      else {
        & wsl.exe docker --version 1>$null 2>$null
        if ($LASTEXITCODE -eq 0) { $rtState = 'installed'; $rtDetail = 'WSL2 Docker CLI present, daemon not running' }
      }
    }
  }
  $r += [pscustomobject]@{ Key = 'runtime'; Name = 'Container runtime'; Ok = ($rtState -eq 'running'); Detail = $rtDetail; Fix = ''; State = $rtState; Mode = $rtMode }
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
  # Ask whichever runtime is actually present. This used to only ever run `wsl docker ps`, so the
  # guard was blind in desktop mode (and would be blind under Podman too).
  $probes = @()
  if (Get-Command podman -ErrorAction SilentlyContinue) { $probes += , @('podman', @('ps', '--format', '{{.Names}}')) }
  if (Get-Command docker -ErrorAction SilentlyContinue) { $probes += , @('docker', @('ps', '--format', '{{.Names}}')) }
  $probes += , @('wsl.exe', @('docker', 'ps', '--format', '{{.Names}}'))
  foreach ($p in $probes) {
    try {
      $ps = & $p[0] @($p[1]) 2>$null
      if ($ps -match 'platform-') { return "platform-* containers exist ($($p[0]))" }
    } catch {}
  }
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

# --- Podman -----------------------------------------------------------------
# Podman is daemonless, but Linux containers still need a Linux VM ("podman machine"). The Hyper-V
# provider is preferred here over the default WSL provider: it keeps WSL - and WSL's drvfs/9p and
# vNIC churn - out of the runtime path entirely. Networking is user-mode via gvproxy over hvsock,
# so containers reach the native Windows broker WITHOUT an inbound firewall rule (gvproxy dials the
# broker from the host's own loopback).
$PodmanMachine = 'podman-machine-default'   # default name => the default //./pipe/docker_engine API pipe

# Is a podman machine present / running? Returns 'missing' | 'stopped' | 'running'.
function Get-PodmanMachineState {
  $rows = @(& podman machine list --noheading 2>$null | Where-Object { $_ -match '\S' })
  if ($rows.Count -eq 0) { return 'missing' }
  & podman info 1>$null 2>$null
  if ($LASTEXITCODE -eq 0) { return 'running' }
  return 'stopped'
}

# Create the machine if absent, then start it. The FIRST hyperv machine init needs admin (it writes
# machine-scope registry keys); Podman 6.0 no longer needs admin for start/stop. Returns $true when
# the machine ends up running.
function Initialize-PodmanMachine {
  param([string]$Provider = 'hyperv', [int]$Cpus = 4, [int]$MemoryMb = 8192, [int]$DiskGb = 60)
  $state = Get-PodmanMachineState
  if ($state -eq 'missing') {
    Write-Log "creating the podman machine ($Provider, ${Cpus} cpu / ${MemoryMb} MB / ${DiskGb} GB)..."
    # Share the repo into the VM at the SAME path so compose bind mounts resolve identically on both
    # sides. (The Hyper-V provider shares host dirs over 9p; the WSL provider automounts /mnt/<drive>.)
    $iargs = @('machine', 'init', '--provider', $Provider, '--cpus', $Cpus, '--memory', $MemoryMb, '--disk-size', $DiskGb)
    if ($Provider -eq 'hyperv') { $iargs += @('-v', "${Root}:${Root}") }
    & podman @iargs 2>&1 | Tee-Object -FilePath $LogFile -Append
    if ($LASTEXITCODE -ne 0) {
      Write-Log 'podman machine init failed. The first Hyper-V machine needs an ADMIN shell (and the'
      Write-Log 'Hyper-V feature + "Hyper-V Administrators" membership); see docs/INSTALL.md.'
      return $false
    }
    $state = 'stopped'
  }
  if ($state -ne 'running') {
    Write-Log 'starting the podman machine...'
    & podman machine start 2>&1 | Tee-Object -FilePath $LogFile -Append
  }
  # `restart: always` containers need something to bring them back when the VM boots. Podman has no
  # daemon, so enable podman-restart.service inside the machine (the Docker-daemon equivalent).
  & podman machine ssh 'sudo systemctl enable --now podman-restart' 2>&1 | Out-Null
  return ((Get-PodmanMachineState) -eq 'running')
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

# The address the CONTAINERS should use for the native Windows broker/Ollama, per runtime:
#   wsl     - the WSL->Windows default-route gateway (dynamic, re-detected every logon).
#   podman  - gvproxy's host address. gvproxy runs ON Windows and dials the target over the host's
#             own loopback, so this works even for services bound to 127.0.0.1 only (Ollama) and
#             needs no firewall rule. Asked of a container directly, with the documented
#             gvisor-tap-vsock host address as the fallback.
#   desktop - $null: Docker Desktop resolves the literal `host-gateway` itself.
$PodmanProbeImage = 'docker.io/library/alpine:latest'
$GvproxyHostFallback = '192.168.127.254'

function Get-PodmanHostIp {
  $ip = $null
  try {
    & podman image exists $PodmanProbeImage 2>$null
    if ($LASTEXITCODE -ne 0) { & podman pull -q $PodmanProbeImage 2>&1 | Out-Null }
    $out = & podman run --rm $PodmanProbeImage getent hosts host.containers.internal 2>$null
    if ($out -match '^\s*(\d+\.\d+\.\d+\.\d+)') { $ip = $Matches[1] }
  } catch {}
  if (-not $ip) { $ip = $GvproxyHostFallback }
  return $ip
}

function Get-ContainerHostIp {
  param([string]$Mode)
  switch ($Mode) {
    'wsl'    { return Get-WslWindowsHost }
    'podman' { return Get-PodmanHostIp }
    default  { return $null }
  }
}

# Set (or replace) one KEY=value in an env file, ATOMICALLY and as UTF-8 with NO BOM.
# Both matter: the old code appended/`sed -i`'d in place, and a crash mid-rewrite left the live
# deploy/.env padded with NUL bytes (silently wiping PLATFORM_ENABLED_APPS and everything after it).
# A BOM would corrupt the first variable name for every consumer.
function Set-EnvValue {
  param([string]$Path, [string]$Key, [string]$Value)
  $lines = if (Test-Path $Path) { @(Get-Content -LiteralPath $Path) } else { @() }
  $out = New-Object System.Collections.Generic.List[string]
  $seen = $false
  foreach ($l in $lines) {
    if ($l -match "^\s*$([regex]::Escape($Key))\s*=") { if (-not $seen) { $out.Add("$Key=$Value"); $seen = $true } }
    else { $out.Add($l) }
  }
  if (-not $seen) { $out.Add("$Key=$Value") }
  $tmp = "$Path.tmp$PID"
  [System.IO.File]::WriteAllText($tmp, (($out -join "`r`n") + "`r`n"), (New-Object System.Text.UTF8Encoding($false)))
  Move-Item -LiteralPath $tmp -Destination $Path -Force
}

# Map the enabled-app list onto the compose profiles that carry those services.
function Get-ComposeProfiles {
  param([string]$Apps)
  $p = @()
  foreach ($a in @('recipe-book', 'bouquet', 'co-worker')) {
    if (($Apps -split ',' | ForEach-Object { $_.Trim() }) -contains $a) { $p += @('--profile', $a) }
  }
  return $p
}

# Run `compose <args>` against whichever runtime is selected. Podman is driven with the standalone
# docker-compose.exe over its Docker-compatible API pipe (the reference Compose implementation, so
# profiles / ${VAR:-default} / depends_on all behave exactly as they did under Docker).
function Invoke-Compose {
  param([string[]]$Arguments, [string]$Mode = $RuntimeMode)
  switch ($Mode) {
    'podman' {
      # No DOCKER_HOST needed when Docker isn't installed (podman claims //./pipe/docker_engine),
      # but set it explicitly so the target is never ambiguous.
      if (-not $env:DOCKER_HOST) { $env:DOCKER_HOST = "npipe:////./pipe/$PodmanMachine" }
      & docker-compose @Arguments 2>&1 | Tee-Object -FilePath $LogFile -Append
    }
    'wsl' { & wsl.exe @(@('docker', 'compose') + $Arguments) 2>&1 | Tee-Object -FilePath $LogFile -Append }
    default { & docker @(@('compose') + $Arguments) 2>&1 | Tee-Object -FilePath $LogFile -Append }
  }
}

# Absolute paths as the selected runtime's compose needs to see them.
function Get-ComposePaths {
  param([string]$Mode = $RuntimeMode)
  $envFile = Join-Path $Root 'deploy\.env'
  $compose = Join-Path $Installer 'docker-compose.installer.yml'
  if ($Mode -eq 'wsl') { return @{ Env = (ConvertTo-WslPath $envFile); Compose = (ConvertTo-WslPath $compose) } }
  return @{ Env = $envFile; Compose = $compose }
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
    $envFile = Join-Path $Root 'deploy\.env'
    # UTF-8 with NO BOM, written whole. See Set-EnvValue for why this is not Set-Content -Encoding utf8.
    [System.IO.File]::WriteAllText($envFile, $tmpl, (New-Object System.Text.UTF8Encoding($false)))
    Copy-Item (Join-Path $Installer 'roles.lean.json') (Join-Path $Root 'services\broker\roles.json') -Force

    # The Co-Worker rail reads a Windows-side inbox that a host process writes into, so the container
    # needs a bind mount to it. Under WSL that path must be the /mnt/<drive> form; podman/desktop take
    # the Windows path as-is. (This conversion was documented in the compose file but never implemented.)
    $inboxWin = Join-Path $Root 'data\co-worker\inbox'
    try { New-Item -ItemType Directory -Force -Path $inboxWin -ErrorAction Stop | Out-Null } catch {}
    $inboxMount = if ($RuntimeMode -eq 'wsl') { ConvertTo-WslPath $inboxWin } else { $inboxWin }
    Set-EnvValue -Path $envFile -Key 'CO_WORKER_INBOX_WIN'   -Value $inboxWin
    Set-EnvValue -Path $envFile -Key 'CO_WORKER_INBOX_MOUNT' -Value $inboxMount
    Write-Log "co-worker inbox: $inboxMount"

    # How containers reach the NATIVE broker/Ollama on the Windows host.
    if ($RuntimeMode -eq 'wsl' -or $RuntimeMode -eq 'podman') {
      Write-Log "detecting the container -> Windows host address ($RuntimeMode)..."
      $winHost = Get-ContainerHostIp -Mode $RuntimeMode
      if ($winHost) {
        Set-EnvValue -Path $envFile -Key 'WINDOWS_HOST' -Value $winHost
        Write-Log "WINDOWS_HOST=$winHost (rails reach the native broker/ollama here)"
      }
      else { Write-Log 'WARNING: could not detect the container->host address; containers may not reach the broker.' }
    }
    Write-Log 'config written.'

    # 2. native broker service. This is the ONLY step that needs admin (registering the LocalSystem
    # NSSM service), so we elevate JUST this and keep the rest of provisioning non-elevated - `wsl.exe`
    # deadlocks when invoked from an elevated process, so the WSL/Docker steps below must NOT be
    # elevated. install-native has no wsl calls, so elevating it is safe. (Ollama runs via its own app
    # on :11434 - no second server; the full 24 GB stack keeps the Ollama NSSM service.)
    Write-Log 'installing the native broker service (approve the UAC prompt that appears)...'
    $nativeArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $Installer 'install-native.ps1'), '-PlatformRoot', $Root, '-SkipOllama')
    # Only WSL-Docker needs the inbound rule: its containers reach the broker across the WSL vNIC.
    # Podman's gvproxy dials from the host's own loopback, so no rule (and no 0.0.0.0 bind) is needed.
    if ($RuntimeMode -eq 'wsl') { $nativeArgs += '-OpenWslFirewall' }
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

    # 4. bundled compose via the detected runtime.
    #   podman  - docker-compose.exe over Podman's Docker-compat pipe; the machine must be up.
    #   wsl     - build from /mnt/c, reach the native broker via the injected WINDOWS_HOST.
    #   desktop - native docker + host-gateway.
    Write-Log "building + starting containers via $RuntimeMode; first build takes several minutes..."
    if ($RuntimeMode -eq 'podman') {
      if (-not (Initialize-PodmanMachine)) { throw 'the podman machine is not running (see the log).' }
    }
    elseif ($RuntimeMode -eq 'wsl') {
      Write-Log 'starting the WSL Docker daemon...'
      Start-DockerEngineWsl
    }
    $paths = Get-ComposePaths
    $cargs = @('--progress', 'plain', '--env-file', $paths.Env, '-f', $paths.Compose)
    $cargs += Get-ComposeProfiles -Apps $EnabledApps
    $cargs += @('up', '-d', '--build')
    Invoke-Compose -Arguments $cargs
    if ($LASTEXITCODE -ne 0) { throw "compose up failed ($RuntimeMode mode; see the log)." }

    # 5. wait for the gateway
    Write-Log 'waiting for the gateway...'
    for ($i = 0; $i -lt 60; $i++) {
      try { Invoke-WebRequest 'http://localhost:1111/api/platform/healthz' -TimeoutSec 3 -UseBasicParsing | Out-Null; break } catch { Start-Sleep 2 }
    }

    # 6a. Podman mode: start the machine + the stack at logon. A Hyper-V machine does NOT idle-shut-down
    # the way WSL2 does, so there is no keep-alive/`sleep infinity` hack here - just a normal startup
    # script. Task Scheduler is Access-denied for non-elevated users on managed boxes, so this is a
    # Startup-folder shortcut (always user-writable).
    if ($RuntimeMode -eq 'podman') {
      Write-Log 'installing a logon startup task (starts the podman machine + the stack)...'
      try {
        $startupPs1 = Join-Path $Installer 'platform-startup.ps1'
        $startup = [Environment]::GetFolderPath('Startup')
        $lnk = Join-Path $startup 'AI-Platform startup.lnk'
        $ws = New-Object -ComObject WScript.Shell
        $sc = $ws.CreateShortcut($lnk)
        $sc.TargetPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $sc.Arguments = "-WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File `"$startupPs1`""
        $sc.WindowStyle = 7
        $sc.Description = 'Starts the podman machine and the AI-Platform stack at logon.'
        $sc.Save()
        Write-Log "startup shortcut installed: $lnk"
      }
      catch { Write-Log "startup-shortcut note ($($_.Exception.Message)); add it manually to keep the stack up across logons." }
    }

    # 6b. WSL mode: keep the VM alive. WSL2 shuts an idle VM down (when no session is attached), which
    # stops the containers; a logon-triggered `wsl --exec sleep infinity` holds it up. Registered as a
    # current-user task (no admin) and started now so the platform stays up this session too.
    if ($RuntimeMode -eq 'wsl') {
      # Keep the WSL VM alive across logons. Task Scheduler is often Access-denied for a non-elevated
      # user on managed boxes, so use a Startup-folder shortcut (always user-writable) that runs
      # deploy/installer/platform-startup.sh — it re-detects the WSL gateway IP, updates .env,
      # runs docker compose up -d, then sleeps forever to hold the VM up.
      Write-Log 'installing a logon keep-alive (holds the WSL VM + containers up)...'
      try {
        $wslScript = (ConvertTo-WslPath $Root) + '/deploy/installer/platform-startup.sh'

        $startup = [Environment]::GetFolderPath('Startup')
        $lnk = Join-Path $startup 'AI-Platform WSL keep-alive.lnk'
        $ws = New-Object -ComObject WScript.Shell
        $sc = $ws.CreateShortcut($lnk)
        $sc.TargetPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $sc.Arguments  = "-WindowStyle Hidden -NoProfile -Command `"wsl bash '$wslScript'`""
        $sc.WindowStyle = 7
        $sc.Description = 'Keeps the WSL2 VM alive and re-syncs the broker IP on each logon (AI-Platform).'
        $sc.Save()
        # Also launch now so the platform stays up this session
        Start-Process wsl -ArgumentList 'bash', $wslScript -WindowStyle Hidden
        Write-Log "keep-alive installed (Startup shortcut) + started: $lnk"
      }
      catch { Write-Log "keep-alive note ($($_.Exception.Message)); if the VM idle-shuts-down, add a Startup keep-alive manually." }
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
  function HardReady($rs) { $d = Prereq $rs 'runtime'; $o = Prereq $rs 'ollama'; return ($d -and $d.State -eq 'running' -and $o -and $o.Ok) }

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

  # 2. The container runtime must be RUNNING. If missing: create the podman machine, or offer to
  # install Docker Engine into WSL2; otherwise guide. If installed-but-stopped: start it, spin-wait.
  $d = Prereq $rs 'runtime'
  if ($d.State -eq 'missing') {
    if ($d.Mode -eq 'wsl') {
      CW ''; CW '  WSL2 is present but Docker Engine is not installed there.' 'Yellow'
      if ((Read-Host '  Install Docker Engine into WSL2 now? [Y/n]') -notmatch '^[Nn]') {
        CW '  installing Docker Engine into WSL2 (a few minutes)...' 'DarkCyan'
        Install-DockerInWsl | Out-Null
        $rs = Show-Doctor; $d = Prereq $rs 'runtime'
      }
    }
    else {
      CW ''; CW '  No container runtime. Install Podman (winget install Podman.CLI - and see' 'Red'
      CW '  docs/INSTALL.md for the Hyper-V prep) or Docker Desktop, then re-run. Aborting.' 'Red'; return
    }
  }
  if ($d.State -eq 'missing') { CW '  Still no container runtime - aborting.' 'Red'; return }
  if ($d.State -ne 'running') {
    CW ''
    if ($d.Mode -eq 'podman') {
      CW '  starting the podman machine (creates it on first run)...' 'DarkCyan'
      Initialize-PodmanMachine | Out-Null
    }
    elseif ($d.Mode -eq 'desktop') {
      $dd = Get-DockerDesktopExe
      if ($dd) { CW '  starting Docker Desktop - accept its license, then hang tight...' 'DarkCyan'; try { Start-Process $dd | Out-Null } catch {} }
      else { CW '  start Docker Desktop and accept its license...' 'DarkCyan' }
    }
    else { CW '  starting the WSL2 Docker daemon...' 'DarkCyan'; Start-DockerEngineWsl }
    $spin = '|', '/', '-', '\'; $i = 0; $deadline = (Get-Date).AddMinutes(15)
    while ((Get-Date) -lt $deadline) {
      $d = Prereq (Test-Prereqs) 'runtime'
      if ($d.State -eq 'running') { break }
      Write-Host ("`r   {0}  waiting for the container runtime...   " -f $spin[$i % 4]) -ForegroundColor DarkCyan -NoNewline
      $i++; Start-Sleep -Milliseconds 700
    }
    if ($d.State -eq 'running') { Write-Host "`r   [OK]  container runtime is up.              " -ForegroundColor Green }
    else { Write-Host "`r   [ - ] container runtime did not come up.    " -ForegroundColor Yellow }
  }

  # 3. re-read; Ollama just needs to be installed (its own app serves :11434; provisioning verifies).
  $rs = Test-Prereqs
  if (-not (HardReady $rs)) {
    CW ''
    CW '  Not ready: need a running container runtime + Ollama installed. Fix those and re-run.' 'Red'
    return
  }
  $dmode = (Prereq $rs 'runtime').Mode
  CW ''
  CW "  All set - $dmode is up and Ollama is installed." 'Green'

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
  $script:RuntimeMode = $dmode; $script:WithRecipeBook = [bool]$withRecipe
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
$script:runtimeAnnounced = $false    # printed "Docker detected" once
$script:ollamaAnnounced = $false    # printed "Ollama detected" once
$script:runtimeLaunched = $false     # launched Docker Desktop once (after it appears installed)

function Get-Prereq($key) { $script:prereqs | Where-Object { $_.Key -eq $key } }

# The two prerequisites the build genuinely can't proceed without: a *running* container engine
# and an *installed* Ollama (the elevated provisioner starts Ollama's service and pulls models).
# GPU / Python / disk stay soft (a skippable warning).
function Test-HardReady {
  $d = Get-Prereq 'runtime'; $o = Get-Prereq 'ollama'
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
    '-AdminUser', $txtUser.Text, '-AdminPass', $txtPass.Text, '-EnabledApps', ($enabled -join ','), '-RuntimeMode', (Get-Prereq 'runtime').Mode)
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
    $d = Get-Prereq 'runtime'; $ol = Get-Prereq 'ollama'
    # Docker finished installing in the background but its engine isn't up yet: launch it once.
    if ($d -and $d.State -eq 'installed' -and -not $script:runtimeLaunched) { $script:runtimeLaunched = $true; Start-ContainerRuntime }
    if ($d -and $d.State -eq 'running' -and -not $script:runtimeAnnounced) { $script:runtimeAnnounced = $true; $log.AppendText("Docker detected.`r`n") }
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
function Start-ContainerRuntime {
  $d = Get-Prereq 'runtime'
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
    $script:runtimeLaunched = $false
    foreach ($p in $missing) { Start-WingetInstall $p.Fix }
    Refresh-Prereqs
    if (-not (Test-HardReady)) {
      $d = Get-Prereq 'runtime'
      if ($d -and $d.State -eq 'installed') { $script:runtimeLaunched = $true; Start-ContainerRuntime }
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
    $d = Get-Prereq 'runtime'; $o = Get-Prereq 'ollama'
    $soft = $script:prereqs | Where-Object { -not $_.Ok -and $_.Key -notin @('runtime', 'ollama') }
    if ($soft) { if ([Windows.Forms.MessageBox]::Show("Unmet prerequisites:`n" + (($soft | ForEach-Object { $_.Name }) -join ', ') + "`n`nContinue anyway?", 'Installer', 'YesNo') -ne 'Yes') { return } }

    if (Test-HardReady) { Start-Provisioning; return }

    # A hard prereq is missing / not up: install what's missing, launch Docker, then let the
    # watcher continue once BOTH the engine is running and Ollama is installed.
    $script:pendingInstall = $true
    $script:runtimeLaunched = $false
    if ($d.State -eq 'missing') {
      if ($d.Mode -eq 'wsl') { $log.AppendText("installing Docker Engine into WSL2 (a few minutes)...`r`n"); Install-DockerInWsl | Out-Null }
      else { [Windows.Forms.MessageBox]::Show("Docker isn't available. Install Docker Desktop (where allowed) or set up Docker on WSL2, then re-run.", 'Installer'); $script:pendingInstall = $false; return }
    }
    elseif ($d.State -eq 'installed') { $script:runtimeLaunched = $true; Start-ContainerRuntime }
    if (-not $o.Ok) { Start-WingetInstall 'Ollama.Ollama' }
    $log.AppendText("waiting for Docker + Ollama; the install continues automatically once both are ready...`r`n")
    $script:watchTimer.Start()
  })

$btnLaunch.Add_Click({ Start-Process 'http://localhost:1111' })

[void]$form.ShowDialog()
