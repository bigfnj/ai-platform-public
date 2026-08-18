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

# Reap an api proxy left holding \\.\pipe\<machine> after the VM is gone.
#
# `podman machine start` aborts with "could not start api proxy since expected pipe is not available"
# when gvproxy (and its podman parent) from a previous run is still alive on that pipe. An unclean
# stop is the usual trigger, but the nastier one is a change of account: the watchdog service runs as
# LocalSystem, and a SYSTEM-owned gvproxy cannot be killed from the user's session - so the machine
# becomes unstartable by ANYONE until something with the right rights reaps the orphan. `podman
# machine stop` does not do it; it reports success and leaves the proxy running.
#
# Only safe with the VM down - a running machine legitimately needs its proxy.
function Clear-StalePodmanProxy {
  if ((Get-PodmanMachineState) -eq 'running') { return $false }

  # gvproxy serves nothing but a podman machine, so a survivor with the VM down is always stale.
  # A long-lived podman.exe is the api-proxy parent; a transient CLI call against a stopped machine
  # exits in about a second, so anything older than 30 s is the orphan and not a command in flight.
  # An unreadable StartTime means the process belongs to another account, which is the case we want.
  $stale = @(Get-Process gvproxy -ErrorAction SilentlyContinue) + @(
    Get-Process podman -ErrorAction SilentlyContinue | Where-Object {
      $_.Id -ne $PID -and (try { $_.StartTime -lt (Get-Date).AddSeconds(-30) } catch { $true })
    }
  )

  $reaped = 0
  foreach ($p in $stale) {
    try {
      Stop-Process -Id $p.Id -Force -ErrorAction Stop
      Write-Log "reaped stale $($p.Name) (pid $($p.Id)) holding the machine api pipe"
      $reaped++
    }
    catch {
      Write-Log "could not reap $($p.Name) pid $($p.Id): $($_.Exception.Message)"
      Write-Log '  it belongs to another account - the platform-watchdog service (LocalSystem) can.'
    }
  }
  if ($reaped -gt 0) { Start-Sleep -Seconds 2 }
  return ($reaped -gt 0)
}

# Probe the podman SSH transport with a hard 10-second timeout.
#
# `podman volume ls` must go through the SSH tunnel to the VM; if that path is broken the command
# hangs indefinitely. Start-Job is avoided here because it inherits the caller's WMI session
# (a problem inside the NSSM service context). System.Diagnostics.Process gives a clean timeout
# without any of that baggage. Returns $true when the transport is alive (exit 0), $false otherwise.
function Test-ControlPlane {
  try {
    $psi = [System.Diagnostics.ProcessStartInfo]::new(
      'podman', 'volume ls --noheading --format {{.Name}}')
    $psi.UseShellExecute         = $false
    $psi.RedirectStandardOutput  = $true
    $psi.RedirectStandardError   = $true
    $p = [System.Diagnostics.Process]::Start($psi)
    if (-not $p.WaitForExit(10000)) { $p.Kill(); return $false }
    return $p.ExitCode -eq 0
  } catch { return $false }
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
    # Write-NativeLine passes its input DOWN the pipeline; without Out-Null those lines become part of
    # this function's return value, and `-not <non-empty array>` is $false — a failure reading as success.
    & podman @iargs 2>&1 | Write-NativeLine | Out-Null
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
    & podman machine start 2>&1 | Write-NativeLine | Out-Null
    # Capture it now: every helper below runs podman/Hyper-V calls that overwrite $LASTEXITCODE.
    $rc = $LASTEXITCODE

    # A stale api proxy blocks every start attempt, and its error text says nothing about memory -
    # so clear that first, before blaming the startup reservation.
    if ($rc -ne 0 -and (Clear-StalePodmanProxy)) {
      Write-Log 'retrying the machine start after clearing a stale api proxy...'
      & podman machine start 2>&1 | Write-NativeLine | Out-Null
      $rc = $LASTEXITCODE
    }

    if ($rc -ne 0) {
      # Most common cause on a memory-tight box: not enough free RAM for the startup reservation.
      Write-Log "podman machine start failed (exit $rc); retrying with a smaller startup reservation..."
      if ($Provider -eq 'hyperv') {
        $advice = Get-PodmanMachineMemoryAdvice
        if ($advice) { Write-Log "  $advice" }
        Set-PodmanMachineStartupRam -MaxMb $MemoryMb | Out-Null
        & podman machine start 2>&1 | Write-NativeLine | Out-Null
        $rc = $LASTEXITCODE
        if ($rc -ne 0) { Write-Log "podman machine start still failing (exit $rc)." }
      }
    }

    # Branch 3: SSH transport dead but Hyper-V VM still running.
    #
    # When gvproxy's connection to the VM breaks, `podman info` (SSH) fails so Get-PodmanMachineState
    # returns 'stopped'. But the Hyper-V VM is still up, and `podman machine start` fails because
    # starting gvproxy would conflict with an existing one, or the VM rejects the start. Neither the
    # stale-proxy nor the memory-reservation branch helps here: the issue is the broken transport,
    # not a missing pipe or low RAM. The fix is to hard-stop the VM via Hyper-V (bypasses SSH),
    # clear the orphan proxy, and retry from a clean state.
    if ($rc -ne 0 -and $Provider -eq 'hyperv' -and
        (Get-Command Get-VM -ErrorAction SilentlyContinue)) {
      $vm = Get-VM -Name $PodmanMachine -ErrorAction SilentlyContinue
      if ($vm -and $vm.State -eq 'Running') {
        Write-Log ('VM is running in Hyper-V but podman cannot reach it (SSH transport dead); ' +
                   'force-stopping to recover...')
        Stop-VM -Name $PodmanMachine -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 5
        Clear-StalePodmanProxy | Out-Null
        Write-Log 'retrying machine start after force-stop...'
        & podman machine start 2>&1 | Write-NativeLine | Out-Null
        $rc = $LASTEXITCODE
        if ($rc -ne 0) { Write-Log "machine start still failing after force-stop (exit $rc)." }
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
  foreach ($a in @('recipe-book', 'co-worker', 'smb-partner-enablement', 'gemini-cx')) {
    if (($Apps -split ',' | ForEach-Object { $_.Trim() }) -contains $a) { $p += @('--profile', $a) }
  }
  return $p
}

# Run `compose <args>` against whichever runtime is selected. Podman is driven with the standalone
# docker-compose.exe over its Docker-compatible API pipe (the reference Compose implementation, so
# profiles / ${VAR:-default} / depends_on all behave exactly as they did under Docker).
function Write-NativeLine {
  # Log-and-forward one line of native-tool output.
  #
  # Compose reports progress AND warnings on stderr, so `2>&1` is needed to capture them - but in
  # PS 5.1 that wraps every stderr line in a NativeCommandError ErrorRecord, which the host renders
  # as a red multi-line block with a source-line caret. A benign "volume already exists but was not
  # created by Docker Compose" warning then reads exactly like a crash. Casting to [string] flattens
  # the record back to the text the tool actually wrote.
  #
  # It also fixes the log: Tee-Object has no -Encoding on PS 5.1 and writes UTF-16 while Write-Log
  # appends UTF-8 - which is why deploy/logs/startup.log came out as "C o n t a i n e r".
  #
  # Output still flows down the pipeline: smoke-test.ps1 reads `compose config --services` from it.
  param([Parameter(ValueFromPipeline = $true)]$InputObject)
  process {
    $line = [string]$InputObject
    try { Add-Content -Path $LogFile -Value $line -Encoding utf8 -ErrorAction Stop } catch {}
    $line
  }
}

function Invoke-Compose {
  param([string[]]$Arguments, [string]$Mode = $RuntimeMode)
  switch ($Mode) {
    'podman' {
      # No DOCKER_HOST needed when Docker isn't installed (podman claims //./pipe/docker_engine),
      # but set it explicitly so the target is never ambiguous.
      if (-not $env:DOCKER_HOST) { $env:DOCKER_HOST = "npipe:////./pipe/$PodmanMachine" }
      & docker-compose @Arguments 2>&1 | Write-NativeLine
    }
    'wsl' { & wsl.exe @(@('docker', 'compose') + $Arguments) 2>&1 | Write-NativeLine }
    default { & docker @(@('compose') + $Arguments) 2>&1 | Write-NativeLine }
  }
}

# podman, docker and wsl-docker all take the same `volume` subcommands; only the entry point differs.
function Invoke-VolumeCli {
  param([string[]]$CliArgs, [string]$Mode = $RuntimeMode)
  switch ($Mode) {
    'podman' { & podman @CliArgs 2>&1 }
    'wsl'    { & wsl.exe @(@('docker') + $CliArgs) 2>&1 }
    default  { & docker @CliArgs 2>&1 }
  }
}

# Create any named volume the compose file expects but the runtime does not have yet.
#
# The compose file declares its volumes `external: true` (see docker-compose.installer.yml for why),
# and Compose refuses to create an external volume - so on a fresh install `up` would fail with
# "external volume not found" unless something makes them first. That is this function.
#
# The compose file stays the source of truth for the list: `config --volumes` prints the keys, and
# the external name is "<project>_<key>" - the same rule as the ${COMPOSE_PROJECT_NAME:-platform}
# interpolation in the file. Idempotent, so it is safe on every startup, not just at install.
function Initialize-ComposeVolumes {
  param([string[]]$ComposeArgs, [string]$Mode = $RuntimeMode)

  $keys = @(Invoke-Compose -Arguments ($ComposeArgs + @('config', '--volumes')) -Mode $Mode |
            Where-Object { $_ -match '^[a-z0-9_-]+$' })
  if ($keys.Count -eq 0) { return }   # compose could not render; let `up` report the real error

  # A FAILED listing must never be read as "nothing exists". Podman's SSH transport can drop while
  # the containers keep serving (healthz 200, `podman volume ls` exit 125), and treating that as an
  # empty runtime would try to re-create live volumes on every startup - burying the real fault.
  $existing = @(Invoke-VolumeCli -CliArgs @('volume', 'ls', '--format', '{{.Name}}') -Mode $Mode |
                ForEach-Object { [string]$_ })
  if ($LASTEXITCODE -ne 0) {
    Write-Log "WARNING: could not list volumes (exit $LASTEXITCODE): $($existing -join ' ')"
    Write-Log '  skipping volume pre-create; `up` will report the real error if one is missing.'
    return
  }

  $project = if ($env:COMPOSE_PROJECT_NAME) { $env:COMPOSE_PROJECT_NAME } else { 'platform' }
  foreach ($k in $keys) {
    $name = "${project}_$k"
    if ($existing -contains $name) { continue }
    $out = @(Invoke-VolumeCli -CliArgs @('volume', 'create', $name) -Mode $Mode | ForEach-Object { [string]$_ })
    if ($LASTEXITCODE -eq 0) { Write-Log "created volume $name" }
    else { Write-Log "WARNING: could not create volume $name (exit $LASTEXITCODE): $($out -join ' ')" }
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

