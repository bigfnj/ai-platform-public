<#
  Shared container-runtime helpers for the AI-Platform installer and the logon startup script.

  Dot-sourced by:
    deploy/installer/install.ps1          (install + doctor + provisioning)
    deploy/installer/platform-startup.ps1 (logon: start the machine + the stack)

  CONTRACT - the caller must define these BEFORE dot-sourcing:
    $Root       repo root (the folder containing deploy/)
    $Installer  deploy/installer directory
    $LogFile    path the helpers append to
    $TempBase   a writable temp dir
    Write-Log   function($message)
    $RuntimeMode  'podman' | 'desktop' | 'wsl'   (Invoke-Compose / Get-ComposePaths default to it)

  These live in one file on purpose: Set-EnvValue is the atomic .env writer, and the bug it exists
  to prevent (a crash mid-rewrite leaving deploy/.env NUL-padded, silently wiping
  PLATFORM_ENABLED_APPS) is exactly the kind that a second, drifting copy would reintroduce.
#>
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

# Lower the Hyper-V startup memory reservation while keeping the dynamic ceiling at $MaxMb.
#
# `podman machine init --memory N` yields DynamicMemoryEnabled=True with Startup == Maximum == N.
# Hyper-V refuses to boot unless the whole STARTUP amount can be reserved up front, so an 8 GB
# machine will not start on a 32 GB box that is already fully committed -- and because that happens
# at logon, the platform is simply missing with no visible error. A small startup value boots
# reliably and the balloon still grows to $MaxMb under load.
#
# Requires the Hyper-V PowerShell module + "Hyper-V Administrators" membership; best-effort by design
# (a machine that already boots does not need this, so a failure here is logged, never fatal).
function Set-PodmanMachineStartupRam {
  param([int]$MaxMb = 8192, [int]$StartupMb = 2048, [string]$VmName = $PodmanMachine)
  if ($StartupMb -gt $MaxMb) { $StartupMb = $MaxMb }
  try {
    if (-not (Get-Command Set-VMMemory -ErrorAction SilentlyContinue)) {
      Write-Log 'Hyper-V PowerShell module not available; leaving the machine memory config alone.'
      return $false
    }
    Set-VMMemory -VMName $VmName -StartupBytes ($StartupMb * 1MB) `
                 -MinimumBytes 512MB -MaximumBytes ($MaxMb * 1MB) -ErrorAction Stop
    Write-Log "podman machine memory: startup ${StartupMb} MB, max ${MaxMb} MB (dynamic)."
    return $true
  } catch {
    Write-Log "could not adjust the machine startup memory: $($_.Exception.Message)"
    return $false
  }
}

# Why won't the Hyper-V machine boot? Returns a human-readable reason, or '' when memory looks fine.
#
# Exists because the failure it explains is otherwise invisible: at logon the VM just doesn't appear,
# and the only clue is a Hyper-V error code buried in a log. Called on the failure paths so the
# message names the actual cause instead of "the container runtime did not come up".
function Get-PodmanMachineMemoryAdvice {
  param([string]$VmName = $PodmanMachine)
  try {
    $os = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
    $freeMb = [int]($os.FreePhysicalMemory / 1KB)
    $startupMb = 0
    if (Get-Command Get-VMMemory -ErrorAction SilentlyContinue) {
      try { $startupMb = [int]((Get-VMMemory -VMName $VmName -ErrorAction Stop).Startup / 1MB) } catch {}
    }
    if ($startupMb -gt 0 -and $freeMb -lt $startupMb) {
      return ("only ${freeMb} MB RAM free but the machine reserves ${startupMb} MB at startup - " +
              "Hyper-V will refuse to boot it. Close some memory hogs, or lower the reservation with:" +
              "`n    Set-VMMemory -VMName $VmName -StartupBytes 2GB -MinimumBytes 512MB -MaximumBytes 8GB")
    }
    return ''
  } catch { return '' }
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
    # Tee-Object passes its input DOWN the pipeline; without Out-Null those lines become part of this
    # function's return value, and `-not <non-empty array>` is $false — a failure that reads as success.
    & podman @iargs 2>&1 | Tee-Object -FilePath $LogFile -Append | Out-Null
    if ($LASTEXITCODE -ne 0) {
      Write-Log 'podman machine init failed. The first Hyper-V machine needs an ADMIN shell (and the'
      Write-Log 'Hyper-V feature + "Hyper-V Administrators" membership); see docs/INSTALL.md.'
      return $false
    }
    # Podman sets Hyper-V dynamic memory with STARTUP = max. Hyper-V must reserve the full startup
    # amount before the VM boots, so an 8 GB startup fails outright on a box whose RAM is already
    # committed -- the platform then silently never comes up at logon. Cap startup low and let the
    # balloon grow to $MemoryMb on demand.
    if ($Provider -eq 'hyperv') { Set-PodmanMachineStartupRam -MaxMb $MemoryMb | Out-Null }
    $state = 'stopped'
  }
  if ($state -ne 'running') {
    Write-Log 'starting the podman machine...'
    & podman machine start 2>&1 | Tee-Object -FilePath $LogFile -Append | Out-Null
    if ($LASTEXITCODE -ne 0) {
      # Most common cause on a memory-tight box: not enough free RAM for the startup reservation.
      Write-Log "podman machine start failed (exit $LASTEXITCODE); retrying with a smaller startup reservation..."
      if ($Provider -eq 'hyperv') {
        $advice = Get-PodmanMachineMemoryAdvice
        if ($advice) { Write-Log "  $advice" }
        Set-PodmanMachineStartupRam -MaxMb $MemoryMb | Out-Null
        & podman machine start 2>&1 | Tee-Object -FilePath $LogFile -Append | Out-Null
        if ($LASTEXITCODE -ne 0) { Write-Log "podman machine start still failing (exit $LASTEXITCODE)." }
      }
    }
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

