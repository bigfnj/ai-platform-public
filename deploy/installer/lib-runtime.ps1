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
# The Windows host IP as seen from inside WSL. Under WSL's default NAT networking that is the
# default-route gateway, the Windows end of the WSL vNIC, dynamic across restarts. Under MIRRORED
# networking (networkingMode=mirrored in %USERPROFILE%\.wslconfig) Linux shares Windows' own
# interfaces and its default route is the LAN router, and Microsoft's answer for reaching Windows
# from there is 127.0.0.1 (learn.microsoft.com/windows/wsl/networking, "Mirrored mode
# networking"), which reaches Windows from the distro itself but is a container's OWN loopback on
# a Docker bridge network. No source documents an address that takes a bridge container to a
# Windows listener in that mode (docs/engineering-record.md has what was read), so mirrored mode
# gets no address and a warning, not a guess. The probe reads the mode the way Docker's own engine
# does (moby PR 48514): `wslinfo --networking-mode`, else a `loopback0` interface, which only
# mirrored mode creates; .wslconfig is the fallback when the probe says nothing.
# Run wsl in a BACKGROUND JOB: wsl.exe can wedge when invoked directly in an interactive console, and
# the job gives a hard timeout so host detection can never hang the whole install. The command has
# no double quote and no $( ), so neither engine's native quoting nor the distro's login shell can
# change it on the way.
$WslNetworkProbe = 'echo wslinfo:; wslinfo --networking-mode 2>/dev/null; echo route:; ip route show default; ip link show loopback0 >/dev/null 2>&1 && echo loopback0:yes'
function Get-WslNetworkProbe {
  $job = Start-Job -ScriptBlock { param($c) (& wsl.exe sh -c $c 2>$null) -join "`n" } -ArgumentList $WslNetworkProbe
  $o = ''
  if (Wait-Job $job -Timeout 25) { $o = (@(Receive-Job $job) -join "`n") }
  else { Stop-Job $job -ErrorAction SilentlyContinue }
  Remove-Job $job -Force -ErrorAction SilentlyContinue
  return $o
}

# The probe's text (and .wslconfig's) read into @{ Mode; Host; Warning }. Mode is what wslinfo said,
# else 'mirrored' for a loopback0 interface, else .wslconfig's networkingMode (under [wsl2], or the
# [experimental] section older builds read it from), else 'nat'. Host is the default route's
# gateway, except in mirrored mode: $null there, and Warning says why and what to do.
function ConvertFrom-WslNetworkProbe {
  param([AllowEmptyString()][string]$Text, [AllowEmptyString()][string]$WslConfigText)
  $section = ''; $mode = ''; $gateway = $null; $loopback0 = $false
  foreach ($l in @(([string]$Text) -split "`r?`n")) {
    $t = $l.Trim()
    if ($t -in @('wslinfo:', 'route:')) { $section = $t; continue }
    if ($t -eq 'loopback0:yes') { $loopback0 = $true; continue }
    if ($section -ne 'route:' -and -not $mode -and $t -match '^(nat|mirrored|virtioproxy|consomme|bridged|none)$') { $mode = $t.ToLowerInvariant() }
    if ($section -eq 'route:' -and -not $gateway -and $t -match '^default\s+via\s+(\d+\.\d+\.\d+\.\d+)') { $gateway = $Matches[1] }
  }
  $from = 'wslinfo'
  if (-not $mode -and $loopback0) { $mode = 'mirrored'; $from = 'a loopback0 interface' }
  if (-not $mode) {
    $sect = ''
    foreach ($l in @(([string]$WslConfigText) -split "`r?`n")) {
      $t = ($l -replace '[#;].*$', '').Trim()
      if ($t -match '^\[(.+)\]$') { $sect = $Matches[1].Trim().ToLowerInvariant(); continue }
      if ($sect -in @('wsl2', 'experimental') -and $t -match '^networkingMode\s*=\s*(\S+)$') {
        $mode = $Matches[1].Trim('"', "'").ToLowerInvariant(); $from = '.wslconfig'
      }
    }
  }
  if (-not $mode) { $mode = 'nat'; $from = 'the default' }
  if ($mode -ne 'mirrored') { return @{ Mode = $mode; Host = $gateway; Warning = ''; From = $from } }
  $warn = ("WSL runs in mirrored networking mode (from $from), where the default route is the LAN " +
           'router' + $(if ($gateway) { " ($gateway)" } else { '' }) + ', not Windows, so WINDOWS_HOST ' +
           'is not set from it. No documented address lets a container on a Docker bridge network reach ' +
           'the broker or Ollama on Windows in that mode (127.0.0.1 is the container''s own), so the ' +
           'rails may not reach them. Use NAT networking (networkingMode=nat under [wsl2] in ' +
           '%USERPROFILE%\.wslconfig, then wsl --shutdown and run this again), or set WINDOWS_HOST in ' +
           'deploy\.env to an address you have checked from inside a container (docs/INSTALL.md).')
  return @{ Mode = 'mirrored'; Host = $null; Warning = $warn; From = $from }
}

# One probe of this box's WSL networking: @{ Mode; Host; Warning } (ConvertFrom-WslNetworkProbe).
# -Probe and -WslConfigPath are for tests; the probe runs wsl.exe.
function Get-WslNetworkInfo {
  param([scriptblock]$Probe = { Get-WslNetworkProbe }, [string]$WslConfigPath)
  if (-not $WslConfigPath) { $WslConfigPath = Join-Path $env:USERPROFILE '.wslconfig' }
  $text = [string](& $Probe)
  $cfg = ''
  try { if (Test-Path -LiteralPath $WslConfigPath -PathType Leaf) { $cfg = [IO.File]::ReadAllText($WslConfigPath) } } catch {}
  return ConvertFrom-WslNetworkProbe -Text $text -WslConfigText $cfg
}

# The address alone, as Get-ContainerHostIp hands it out ($null in mirrored mode, with the warning).
function Get-WslWindowsHost {
  $n = Get-WslNetworkInfo
  if ($n.Warning) { Write-Warning $n.Warning }
  return $n.Host
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

# --- which runtime holds the platform? ---------------------------------------
# "podman is on PATH" is not "podman runs the platform". A box can carry both CLIs: the 24 GB box
# runs the stack on Docker Desktop and has podman installed beside it, and a detector that took
# the first CLI it found pointed every smoke-test stage at an empty, stopped podman machine. The
# platform's containers are the evidence, so ask each CLI whether it lists them.

# One `<cli> ps -a` with a hard timeout. Returns @{ Present; Ok; Rows; Detail }, Rows being
# "name state" strings. Through System.Diagnostics.Process rather than `& $cli`: a podman whose
# SSH transport is dead hangs indefinitely (see Test-ControlPlane), and the argument string goes
# to the process verbatim, so 5.1 cannot mangle the quoted --format template on the way.
function Get-RuntimeContainerList {
  param([Parameter(Mandatory)][string]$Cli, [int]$TimeoutMs = 15000)
  $cmd = Get-Command $Cli -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
  if (-not $cmd) { return @{ Present = $false; Ok = $false; Rows = @(); Detail = "$Cli not on PATH" } }
  try {
    $psi = [System.Diagnostics.ProcessStartInfo]::new($cmd.Source, 'ps -a --format "{{.Names}} {{.State}}"')
    $psi.UseShellExecute        = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError  = $true
    $psi.CreateNoWindow         = $true
    $p = [System.Diagnostics.Process]::Start($psi)
    # Both streams drained asynchronously: a child that fills one pipe while we wait on the
    # other would otherwise deadlock until the timeout and be reported as hung.
    $out = $p.StandardOutput.ReadToEndAsync()
    $err = $p.StandardError.ReadToEndAsync()
    if (-not $p.WaitForExit($TimeoutMs)) {
      Stop-ProcessTree -Process $p
      return @{ Present = $true; Ok = $false; Rows = @(); Detail = "$Cli ps timed out after $([int]($TimeoutMs / 1000)) s" }
    }
    if ($p.ExitCode -ne 0) {
      $first = @(($err.Result + "`n" + $out.Result) -split "`r?`n" | Where-Object { $_ -match '\S' })[0]
      return @{ Present = $true; Ok = $false; Rows = @(); Detail = "$Cli ps exit $($p.ExitCode): $first" }
    }
    $rows = @($out.Result -split "`r?`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    return @{ Present = $true; Ok = $true; Rows = $rows; Detail = "$($rows.Count) container(s)" }
  } catch {
    return @{ Present = $true; Ok = $false; Rows = @(); Detail = "$Cli ps could not run: $($_.Exception.Message)" }
  }
}

# One Windows command line from an argument list, for Start-Process -ArgumentList: each argument
# quoted so the C runtime's rules (and CommandLineToArgvW's, which powershell.exe -File follows)
# parse it back to exactly that string. Start-Process joins an -ArgumentList ARRAY with spaces and
# quotes nothing, so a value holding a space reached the child as several arguments. An argument
# with no blank and no double quote is passed as it is, so those command lines are unchanged.
#   inside quotes: a run of backslashes before a `"` is doubled and the `"` escaped (\"), a run
#   before the closing quote is doubled, and any other backslash is literal; never a doubled `""`,
#   which the two parsers read differently.
function Join-CommandLine {
  param([Parameter(Mandatory)][AllowEmptyCollection()][AllowEmptyString()][string[]]$Arguments)
  $parts = New-Object System.Collections.Generic.List[string]
  foreach ($a in $Arguments) {
    if ($a -ne '' -and $a -notmatch '[\s"]') { $parts.Add($a); continue }
    $sb = New-Object System.Text.StringBuilder
    [void]$sb.Append('"')
    $bs = 0
    foreach ($ch in $a.ToCharArray()) {
      if ($ch -eq [char]92) { $bs++; continue }
      if ($ch -eq [char]34) { [void]$sb.Append([char]92, 2 * $bs + 1).Append([char]34); $bs = 0; continue }
      if ($bs) { [void]$sb.Append([char]92, $bs); $bs = 0 }
      [void]$sb.Append($ch)
    }
    if ($bs) { [void]$sb.Append([char]92, 2 * $bs) }
    [void]$sb.Append('"')
    $parts.Add($sb.ToString())
  }
  return ($parts -join ' ')
}

# Kill a process AND everything it started. Process.Kill() ends only the one process, and a CLI's
# children inherit its handles, this script's own output pipe among them: a hung `podman ps`
# killed that way left its child holding the pipe, so anything reading this script's output waited
# as long as the orphan lived. taskkill /T, because Kill($true) is .NET Core only and the logon
# startup runs under Windows PowerShell 5.1. Run as a process, not `& taskkill`, so its stderr
# ("not found" for a tree already gone) never meets a caller's EAP=Stop.
function Stop-ProcessTree {
  param([Parameter(Mandatory)][System.Diagnostics.Process]$Process)
  try {
    $psi = [System.Diagnostics.ProcessStartInfo]::new(
      (Join-Path $env:SystemRoot 'System32\taskkill.exe'), "/PID $($Process.Id) /T /F")
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $tk = [System.Diagnostics.Process]::Start($psi)
    $null = $tk.WaitForExit(10000)
  } catch {}
  try { if (-not $Process.HasExited) { $Process.Kill() } } catch {}
}

# The runtime this box DECLARES: PLATFORM_RUNTIME in deploy\.env, or '' when it declares none.
# Read by Read-DotEnvFile, compose's own rules (last assignment wins, quotes, a trailing comment).
function Get-DeclaredRuntime {
  param([string]$EnvFile)
  if (-not $EnvFile) { return '' }
  return [string](Get-DotEnvValue -Path $EnvFile -Key 'PLATFORM_RUNTIME')
}

# Set (or replace) one KEY=value in env-file TEXT. The pure twin of Set-EnvValue, for callers
# that build the text first and write it once (lib-apply's env step compares whole texts).
function Set-EnvTextValue {
  param([Parameter(Mandatory)][AllowEmptyString()][string]$Text, [Parameter(Mandatory)][string]$Key,
        [Parameter(Mandatory)][string]$Value)
  $nl = if ($Text -match "`r`n") { "`r`n" } else { "`n" }
  $out = New-Object System.Collections.Generic.List[string]
  $seen = $false
  foreach ($l in ($Text -split "`r?`n")) {
    if ($l -match "^\s*$([regex]::Escape($Key))\s*=") { if (-not $seen) { $out.Add("$Key=$Value"); $seen = $true } }
    else { $out.Add($l) }
  }
  if (-not $seen) {
    while ($out.Count -gt 0 -and $out[$out.Count - 1] -eq '') { $out.RemoveAt($out.Count - 1) }
    $out.Add("$Key=$Value"); $out.Add('')
  }
  return ($out -join $nl)
}

# Pick the runtime mode. Returns @{ Mode; Reason; Warning; Basis }: Reason says what decided it,
# so a caller can print WHY and a wrong pick is diagnosable from one line; Warning is non-empty
# when the declaration and the evidence disagree, and callers print it loudly. Basis is 'declared',
# 'container' (a CLI lists $Container), 'only-cli' (docker is the only engine CLI on PATH) or
# 'guess' (none of those: the first of two CLIs, podman alone, or wsl), so a script that would
# build or read volumes on the wrong engine can refuse a guess (rebuild-stale, backup-platform)
# while one that only reports can follow it.
#
# DECLARED BEATS INFERRED. A box that says which runtime it runs (PLATFORM_RUNTIME in deploy\.env,
# written by the installer and by apply.ps1 for the runtime they set up) is taken at its word,
# and an explicit -RuntimeMode on the calling script still beats the declaration. The OTHER
# runtime's CLI is still asked: if it lists the gateway running, that is said loudly and the
# declaration is still followed, because a wrong declaration is the owner's to fix and a guess
# that silently overrules it is how a box ends up driven two ways. The declared runtime's own CLI
# is not asked (its answer could change nothing), and the other is asked for at most
# $DeclaredProbeMs, since only that warning rides on it: a declared box paid both CLIs' full 15 s
# timeouts on every run. A value that is not podman, desktop or wsl is ignored, and the reason
# says so.
#
# Undeclared, the order of evidence is: a CLI listing $Container RUNNING, then one listing it at
# all (a stopped stack still belongs to its runtime), then docker as the only engine CLI on PATH,
# then the first CLI present - the old rule, kept as the fallback for a box with no platform yet.
# wsl is never probed: wsl.exe can wedge an interactive console (see Get-WslWindowsHost), so it is
# inferred only when neither CLI exists.
# Docker alone is Docker Desktop, a known basis (delta audit 2026-10-15): only the lean writers
# declare PLATFORM_RUNTIME, so a full-stack box (Docker Desktop's) declares nothing, and with its
# stack down no CLI lists the gateway; as a guess, its backup and its rebuild (and the daily log
# prune that runs after the rebuild's refusal) were refused. With podman beside docker there are
# two engines to choose between, and that is still a guess.
#
# -EnvFile defaults to deploy\.env beside this file's folder; pass '' to declare nothing (tests).
function Resolve-PlatformRuntime {
  param([string]$Container = 'platform-gateway-1', [int]$TimeoutMs = 15000, [string]$EnvFile,
        [int]$DeclaredProbeMs = 5000)
  if (-not $PSBoundParameters.ContainsKey('EnvFile')) { $EnvFile = Join-Path (Split-Path $PSScriptRoot -Parent) '.env' }
  $declared = (Get-DeclaredRuntime -EnvFile $EnvFile).ToLowerInvariant()
  $ignored = ''
  if ($declared -and $declared -notin @('podman', 'desktop', 'wsl')) {
    $ignored = "deploy\.env declares PLATFORM_RUNTIME='$declared', which is not podman, desktop or wsl, so it is ignored; "
    $declared = ''
  }
  $seen = @()
  foreach ($c in @(@{ Mode = 'podman'; Cli = 'podman' }, @{ Mode = 'desktop'; Cli = 'docker' })) {
    if ($declared -eq $c.Mode) {
      $seen += [pscustomobject]@{ Mode = $c.Mode; Cli = $c.Cli; Present = $true; Ok = $false; State = $null
                                  Detail = 'not asked: declared' }
      continue
    }
    $r = Get-RuntimeContainerList -Cli $c.Cli -TimeoutMs $(if ($declared) { [Math]::Min($TimeoutMs, $DeclaredProbeMs) } else { $TimeoutMs })
    $state = $null
    foreach ($row in @($r.Rows)) {
      $f = -split [string]$row
      if ($f.Count -ge 1 -and $f[0] -eq $Container) { $state = if ($f.Count -ge 2) { $f[1] } else { 'listed' } }
    }
    $seen += [pscustomobject]@{ Mode = $c.Mode; Cli = $c.Cli; Present = [bool]$r.Present; Ok = [bool]$r.Ok
                                State = $state; Detail = $r.Detail }
  }
  $what = ($seen | ForEach-Object {
      if ($_.State) { "$($_.Cli): $Container $($_.State)" } else { "$($_.Cli): $($_.Detail)" } }) -join '; '

  $running = @($seen | Where-Object { $_.State -eq 'running' })
  $listed  = @($seen | Where-Object { $_.State })
  if ($declared) {
    $warning = ''
    $others = @($running | Where-Object { $_.Mode -ne $declared })
    if ($others.Count -gt 0) {
      $warning = ("deploy\.env declares PLATFORM_RUNTIME=$declared, but '$($others[0].Cli) ps' lists $Container " +
                  "running; following the declaration. Correct the line, or pass -RuntimeMode.")
    }
    return @{ Mode = $declared; Reason = "declared in deploy\.env (PLATFORM_RUNTIME=$declared) ($what)"; Warning = $warning; Basis = 'declared' }
  }
  if ($running.Count -eq 1) {
    return @{ Mode = $running[0].Mode; Reason = "$ignored'$($running[0].Cli) ps' lists $Container running ($what)"; Warning = ''; Basis = 'container' }
  }
  if ($running.Count -gt 1) {
    return @{ Mode = $running[0].Mode; Reason = "${ignored}AMBIGUOUS, both CLIs list $Container running; took $($running[0].Cli), pass -RuntimeMode to choose ($what)"; Warning = ''; Basis = 'container' }
  }
  $doubt = ''
  if ($listed.Count -ge 1) {
    # A STOPPED copy is evidence only when every other installed CLI answered. A migrated box can
    # keep an exited platform in the runtime it left, and before logon has started the podman
    # machine the runtime it moved to cannot answer at all; that leftover must not win then.
    $silent = @($seen | Where-Object { $_.Present -and -not $_.Ok -and $_.Mode -ne $listed[0].Mode })
    if ($silent.Count -eq 0) {
      return @{ Mode = $listed[0].Mode; Reason = "$ignored'$($listed[0].Cli) ps' lists $Container, not running ($what)"; Warning = ''; Basis = 'container' }
    }
    $doubt = "AMBIGUOUS, '$($listed[0].Cli) ps' lists $Container stopped but $($silent[0].Cli) could not be asked; "
  }
  $first = @($seen | Where-Object { $_.Present })
  $clis = @($first | ForEach-Object { $_.Cli })
  if ($clis.Count -eq 1 -and $clis[0] -eq 'docker') {
    return @{ Mode = 'desktop'; Reason = "${ignored}no CLI lists $Container running, and docker is the only engine CLI on PATH, so Docker Desktop ($what)"; Warning = ''; Basis = 'only-cli' }
  }
  if ($first.Count -gt 0) {
    return @{ Mode = $first[0].Mode; Reason = "$ignored${doubt}no CLI lists $Container running, so the first CLI on PATH ($what)"; Warning = ''; Basis = 'guess' }
  }
  return @{ Mode = 'wsl'; Reason = "${ignored}neither podman nor docker is on PATH, so Docker inside WSL ($what)"; Warning = ''; Basis = 'guess' }
}

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
    $freeMb = Get-FreeMemoryMb
    $startupMb = 0
    $static = $false
    if (Get-Command Get-VMMemory -ErrorAction SilentlyContinue) {
      try {
        $m = Get-VMMemory -VMName $VmName -ErrorAction Stop
        $startupMb = [int]($m.Startup / 1MB)
        $static = -not $m.DynamicMemoryEnabled
      } catch {}
    }
    if ($startupMb -gt 0 -and $freeMb -lt $startupMb) {
      # The usual fix lowers the startup reservation, and Set-VMMemory refuses -MinimumBytes on a
      # static VM, so on one of those that advice was a command that could only fail.
      if ($static) {
        return ("only ${freeMb} MB RAM free but the machine is a STATIC ${startupMb} MB VM - Hyper-V " +
                "will refuse to boot it. Free memory, or " + (Get-StaticMemoryFix -VmName $VmName -StartupMb $startupMb))
      }
      return ("only ${freeMb} MB RAM free but the machine reserves ${startupMb} MB at startup - " +
              "Hyper-V will refuse to boot it. Close some memory hogs, or lower the reservation with:" +
              "`n    Set-VMMemory -VMName $VmName -StartupBytes 2GB -MinimumBytes 512MB -MaximumBytes 8GB")
    }
    return ''
  } catch { return '' }
}

# Free physical memory in MB, or 0 when it cannot be read.
function Get-FreeMemoryMb {
  try { return [int]((Get-CimInstance Win32_OperatingSystem -ErrorAction Stop).FreePhysicalMemory / 1KB) }
  catch { return 0 }
}

# 'static' | 'dynamic' | 'unknown' (no Hyper-V module, or no rights to read the VM).
#
# It decides whether lowering the startup reservation can work at all: Hyper-V only lets a VM's
# minimum, maximum and buffer change while Dynamic Memory is on. The 8 GB box's machine is a STATIC
# 12 GiB VM, and its "retrying with a smaller startup reservation" fallback failed every time.
function Get-PodmanMachineMemoryMode {
  param([string]$VmName = $PodmanMachine)
  if (-not (Get-Command Get-VMMemory -ErrorAction SilentlyContinue)) { return 'unknown' }
  try { $m = Get-VMMemory -VMName $VmName -ErrorAction Stop } catch { return 'unknown' }
  if ($m.DynamicMemoryEnabled) { return 'dynamic' }
  return 'static'
}

# The one-time change that makes a static VM bootable under memory pressure. Printed, never run:
# it is elevated and persistent, so it is the owner's call (and needs the machine stopped). The
# ceiling is the VM's CURRENT size, read from Hyper-V when not given: the 8 GB box's machine is
# 12 GiB, and a fixed 8 GB here would have quietly shrunk it.
function Get-StaticMemoryFix {
  param([string]$VmName = $PodmanMachine, [int]$StartupMb = 0)
  if ($StartupMb -le 0) {
    $StartupMb = 8192
    try { $StartupMb = [int]((Get-VMMemory -VMName $VmName -ErrorAction Stop).Startup / 1MB) } catch {}
  }
  return ("switch the VM to Dynamic Memory once (elevated, machine stopped):" +
          "`n    Set-VMMemory -VMName $VmName -DynamicMemoryEnabled `$true -StartupBytes 2GB " +
          "-MinimumBytes 512MB -MaximumBytes ${StartupMb}MB")
}

# Did this `podman machine start` fail because Hyper-V could not reserve the VM's memory? Its
# text is the only evidence: podman exits 125 for that and for everything else.
function Test-HyperVMemoryStartFailure {
  param([string[]]$Output)
  return [bool](($Output -join "`n") -match 'Not enough memory in the system to start the virtual machine|0x8007000E')
}

# Unload every model Ollama holds resident and return the names unloaded.
#
# `/api/generate` with `keep_alive: 0` and no prompt is Ollama's own unload: nothing is generated.
# Used only when Hyper-V has refused a machine start for memory, because resident models are the
# largest thing an unattended start can give back. On 2026-10-01, unloading two by hand freed
# 3.1 GB and the same start came up clean. The broker reloads on the next request.
function Clear-OllamaResidentModels {
  param([string]$BaseUrl = 'http://127.0.0.1:11434', [int]$SettleSeconds = 10)
  $names = @()
  try {
    $ps = Invoke-RestMethod -Uri "$BaseUrl/api/ps" -TimeoutSec 5 -ErrorAction Stop
    $names = @(@($ps.models) | ForEach-Object { if ($_.name) { $_.name } else { $_.model } } | Where-Object { $_ })
  } catch {
    Write-Log "  could not ask Ollama what is resident ($BaseUrl): $($_.Exception.Message)"
    return @()
  }
  $done = New-Object System.Collections.Generic.List[string]
  foreach ($n in $names) {
    try {
      $body = @{ model = $n; keep_alive = 0 } | ConvertTo-Json -Compress
      Invoke-RestMethod -Uri "$BaseUrl/api/generate" -Method Post -Body $body -ContentType 'application/json' `
                        -TimeoutSec 60 -ErrorAction Stop | Out-Null
      $done.Add($n)
    } catch { Write-Log "  could not unload $n : $($_.Exception.Message)" }
  }
  # The unload answers before the runner has handed its memory back; wait (bounded) for /api/ps to
  # empty, so the retry is not racing the free.
  for ($i = 0; $i -lt $SettleSeconds -and $done.Count -gt 0; $i++) {
    try { if (@((Invoke-RestMethod -Uri "$BaseUrl/api/ps" -TimeoutSec 5 -ErrorAction Stop).models).Count -eq 0) { break } }
    catch { break }
    Start-Sleep -Seconds 1
  }
  return $done.ToArray()
}

# One `podman machine start`, logged line by line. Returns its exit code AND its output, because
# the output is the only place the reason lives (see Test-HyperVMemoryStartFailure).
function Start-PodmanMachineOnce {
  $out = @(& podman machine start 2>&1 | Write-NativeLine)
  return @{ Rc = $LASTEXITCODE; Output = $out }
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
# Is this podman.exe the orphaned api-proxy parent rather than a command in flight? A long-lived
# podman.exe is the parent; a transient CLI call against a stopped machine exits in about a second,
# so anything started before -Before (30 s ago by default) is the orphan. An unreadable StartTime
# means the process belongs to another account (the LocalSystem watchdog's), which is the case
# this reap exists for, so it counts as stale. This script's own process never does.
#
# A function, not an inline `(try { ... } catch { ... })`: `try` is a statement, not an expression,
# so inside parentheses PowerShell parses it as a COMMAND named try, and the watchdog threw "The
# term 'try' is not recognized" on every reap (three times on the podman box, once straight after
# a Stop-VM), so the stale proxy that blocks every machine start was never reaped.
function Test-StalePodmanProcess {
  param([Parameter(Mandatory, Position = 0)]$Process, [datetime]$Before = (Get-Date).AddSeconds(-30))
  if ($Process.Id -eq $PID) { return $false }
  $start = $null
  try { $start = $Process.StartTime } catch {}
  return ($null -eq $start -or $start -lt $Before)
}

# Only safe with the VM down - a running machine legitimately needs its proxy.
function Clear-StalePodmanProxy {
  if ((Get-PodmanMachineState) -eq 'running') { return $false }

  # gvproxy serves nothing but a podman machine, so a survivor with the VM down is always stale.
  # podman.exe is stale only when Test-StalePodmanProcess says so (old, or another account's).
  $stale = @(Get-Process gvproxy -ErrorAction SilentlyContinue) + @(
    Get-Process podman -ErrorAction SilentlyContinue | Where-Object { Test-StalePodmanProcess $_ }
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

# Probe the podman SSH transport: one `podman volume ls` with a hard timeout (10 s by default).
# Returns @{ Alive; Detail; Seconds }: Alive only for exit 0 inside the timeout, Detail what was
# seen (the answer's time, the timeout, the exit code and the first stderr line), for the log.
#
# `podman volume ls` must go through the SSH tunnel to the VM; if that path is broken the command
# hangs indefinitely. Start-Job is avoided here because it inherits the caller's WMI session
# (a problem inside the NSSM service context). System.Diagnostics.Process gives a clean timeout
# without any of that baggage.
#
# Both redirected pipes are drained from the start, as Get-RuntimeContainerList drains them. They
# used to be redirected and never read, and a child writing to a pipe nobody reads blocks once the
# pipe's buffer is full (measured under 5.1: ~5 KB never exits), so once a box's volume names
# passed about 4 KB a HEALTHY podman timed out here and read as a dead transport. A timeout kills
# the probe's whole tree (Stop-ProcessTree: Kill() ended podman alone and left its children), and
# the process is disposed either way. The watchdog force-stops the VM on this probe's word, after a
# slower confirming one (platform-watchdog.ps1): 2026-10-14's outage began with two of these.
function Get-ControlPlaneProbe {
  param([int]$TimeoutMs = 10000)
  $cmd = Get-Command podman -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
  if (-not $cmd) { return @{ Alive = $false; Detail = 'podman is not on PATH'; Seconds = 0 } }
  $proc = $null
  $clock = [System.Diagnostics.Stopwatch]::StartNew()
  try {
    $psi = [System.Diagnostics.ProcessStartInfo]::new($cmd.Source, 'volume ls --noheading --format {{.Name}}')
    $psi.UseShellExecute        = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError  = $true
    $psi.CreateNoWindow         = $true
    $proc = [System.Diagnostics.Process]::Start($psi)
    $drainOut = $proc.StandardOutput.ReadToEndAsync()
    $drainErr = $proc.StandardError.ReadToEndAsync()
    if (-not $proc.WaitForExit($TimeoutMs)) {
      Stop-ProcessTree -Process $proc
      return @{ Alive = $false; Seconds = $clock.Elapsed.TotalSeconds
                Detail = "podman volume ls gave no answer in $([int]($TimeoutMs / 1000)) s" }
    }
    $took = '{0:0.0}' -f $clock.Elapsed.TotalSeconds
    if ($proc.ExitCode -eq 0) { return @{ Alive = $true; Seconds = $clock.Elapsed.TotalSeconds; Detail = "answered in $took s" } }
    # The first stderr line says why; bounded, in case a child of podman still holds the pipe.
    $why = ''
    if ($drainErr.Wait(2000)) { $why = [string](@($drainErr.Result -split "`r?`n" | Where-Object { $_ -match '\S' })[0]) }
    return @{ Alive = $false; Seconds = $clock.Elapsed.TotalSeconds
              Detail = "podman volume ls exit $($proc.ExitCode) in $took s: $why" }
  }
  catch {
    return @{ Alive = $false; Seconds = $clock.Elapsed.TotalSeconds
              Detail = "podman volume ls could not run: $($_.Exception.Message)" }
  }
  finally {
    if ($proc) { $proc.Dispose() }
  }
}

# The same probe as a yes/no: is the transport alive?
function Test-ControlPlane {
  param([int]$TimeoutMs = 10000)
  return [bool](Get-ControlPlaneProbe -TimeoutMs $TimeoutMs).Alive
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
    # Rc captured with the output: every helper below runs podman/Hyper-V calls that overwrite
    # $LASTEXITCODE.
    $start = Start-PodmanMachineOnce
    $rc = $start.Rc

    # A stale api proxy blocks every start attempt, and its error text says nothing about memory -
    # so clear that first, before blaming the startup reservation.
    if ($rc -ne 0 -and (Clear-StalePodmanProxy)) {
      Write-Log 'retrying the machine start after clearing a stale api proxy...'
      $start = Start-PodmanMachineOnce
      $rc = $start.Rc
    }

    if ($rc -ne 0) { Write-Log "podman machine start failed (exit $rc)." }
    if ($rc -ne 0 -and $Provider -eq 'hyperv') {
      $memMode = Get-PodmanMachineMemoryMode
      $advice = Get-PodmanMachineMemoryAdvice
      if ($advice) { Write-Log "  $advice" }
      if ($memMode -eq 'static') {
        # Lowering the startup reservation needs Dynamic Memory. On a static VM that call can only
        # fail, and it used to be announced as "retrying with a smaller startup reservation" and
        # followed by a retry that nothing had changed (2026-10-01, static 12 GiB VM).
        Write-Log ('  the VM has STATIC memory (Dynamic Memory off), so a smaller startup reservation ' +
                   'cannot be set; skipping that fallback. To stop this recurring, ' + (Get-StaticMemoryFix))
      }
      else {
        # Most common cause on a memory-tight box: not enough free RAM for the startup reservation.
        Write-Log '  retrying with a smaller startup reservation...'
        Set-PodmanMachineStartupRam -MaxMb $MemoryMb | Out-Null
        $start = Start-PodmanMachineOnce
        $rc = $start.Rc
        if ($rc -ne 0) { Write-Log "podman machine start still failing (exit $rc)." }
      }

      # Hyper-V said, in so many words, that there is not enough memory. Give back the largest
      # thing an unattended start can: Ollama's resident models. Once, and only for this error -
      # the user pays a model reload for it, and no other failure would be helped. This is the
      # watchdog's case: it restarts the machine mid-session, when models and a browser are resident.
      if ($rc -ne 0 -and (Test-HyperVMemoryStartFailure -Output $start.Output)) {
        $before = Get-FreeMemoryMb
        $unloaded = @(Clear-OllamaResidentModels)
        if ($unloaded.Count -gt 0) {
          Write-Log ("Hyper-V refused the start for memory; unloaded {0} Ollama model(s) ({1}), free RAM {2} -> {3} MB. Retrying once..." -f
                     $unloaded.Count, ($unloaded -join ', '), $before, (Get-FreeMemoryMb))
          $start = Start-PodmanMachineOnce
          $rc = $start.Rc
          if ($rc -ne 0) { Write-Log "podman machine start still failing after unloading Ollama (exit $rc)." }
        }
        else {
          Write-Log ('Hyper-V refused the start for memory and Ollama had no model resident to unload; ' +
                     'free memory by hand and run platform-startup.ps1 again.')
        }
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
#
# READ AS UTF-8 TOO. The reader used to be a bare Get-Content, which Windows PowerShell 5.1 decodes
# as ANSI: every non-ASCII byte of the lean template's comments came back as two or three
# characters and was then written out as UTF-8, so each logon (platform-startup sets WINDOWS_HOST)
# re-encoded the file once more. Measured: 5,614 -> 46,425 bytes in 11 calls, and a value holding
# an accented character mangled after one. Now both sides are UTF-8, a BOM is dropped (never
# written), the file's own line ending is kept, and an unchanged file is not rewritten at all.
function Set-EnvValue {
  param([string]$Path, [string]$Key, [string]$Value)
  $f = Read-EnvFileLines -Path $Path
  $out = New-Object System.Collections.Generic.List[string]
  $seen = $false
  foreach ($l in $f.Lines) {
    if ($l -match "^\s*$([regex]::Escape($Key))\s*=") { if (-not $seen) { $out.Add("$Key=$Value"); $seen = $true } }
    else { $out.Add($l) }
  }
  if (-not $seen) { $out.Add("$Key=$Value") }
  Write-EnvFileLines -Path $Path -Lines $out.ToArray() -Newline $f.Newline -Previous $f.Raw
}

# Replace a whole block of KEY=value lines, under one header comment, at the end of the file:
# every listed key's old line and the old header are dropped first, so a re-run leaves the file
# byte-identical. activate-model-roles.ps1 used to keep the previous header and add a new one on
# every run, and read the file as ANSI under 5.1 like Set-EnvValue did.
function Set-EnvBlock {
  param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][System.Collections.IDictionary]$Values,
        [Parameter(Mandatory)][string]$Header)
  $f = Read-EnvFileLines -Path $Path
  $keys = @($Values.Keys | ForEach-Object { [string]$_ })
  $out = New-Object System.Collections.Generic.List[string]
  foreach ($l in $f.Lines) {
    if ($l -eq $Header) { continue }
    $hit = $false
    foreach ($k in $keys) { if ($l -match "^\s*$([regex]::Escape($k))\s*=") { $hit = $true; break } }
    if (-not $hit) { $out.Add($l) }
  }
  $out.Add($Header)
  foreach ($k in $keys) { $out.Add("$k=$($Values[$k])") }
  Write-EnvFileLines -Path $Path -Lines $out.ToArray() -Newline $f.Newline -Previous $f.Raw
}

# An env file's lines, decoded as UTF-8 on both engines (a BOM is detected and dropped), and the
# line ending it uses (CRLF when it has none yet). Raw is the text as read, for Write-EnvFileLines'
# unchanged check; null when the file carries a BOM, so that file is always rewritten without one.
function Read-EnvFileLines {
  param([Parameter(Mandatory)][string]$Path)
  if (-not (Test-Path -LiteralPath $Path)) { return @{ Lines = @(); Newline = "`r`n"; Raw = $null; Bom = $false } }
  $bytes = [System.IO.File]::ReadAllBytes($Path)
  $bom = $bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF
  $text = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
  $nl = if ($text.Contains("`r`n")) { "`r`n" } elseif ($text.Contains("`n")) { "`n" } else { "`r`n" }
  $lines = New-Object System.Collections.Generic.List[string]
  foreach ($l in ($text -split "`r?`n")) { $lines.Add($l) }
  if ($lines.Count -gt 0 -and $lines[$lines.Count - 1] -eq '') { $lines.RemoveAt($lines.Count - 1) }
  return @{ Lines = $lines.ToArray(); Newline = $nl; Raw = $(if ($bom) { $null } else { $text }); Bom = $bom }
}

# Write env-file lines ATOMICALLY (temp file, then a move over the old one) as UTF-8 with NO BOM,
# ending in one newline. Skipped when the text is what the file already holds.
function Write-EnvFileLines {
  param([Parameter(Mandatory)][string]$Path, [AllowEmptyCollection()][string[]]$Lines,
        [string]$Newline = "`r`n", [AllowNull()][string]$Previous)
  $text = ($Lines -join $Newline) + $Newline
  if ($null -ne $Previous -and $text -ceq $Previous) { return }
  $tmp = "$Path.tmp$PID"
  [System.IO.File]::WriteAllText($tmp, $text, (New-Object System.Text.UTF8Encoding($false)))
  Move-Item -LiteralPath $tmp -Destination $Path -Force
}

# --- deploy\.env read the way docker compose reads it -----------------------------------------
# ONE reader for every value a script takes from deploy\.env, so a script sees what the containers
# see. Each script used to carry its own: some took the FIRST line where compose takes the last,
# some kept a trailing "  # comment" or the quotes as part of the value, one read the file as ANSI
# under 5.1. So `PLATFORM_DISABLED_APPS=servicenow  # note` parked nothing, and a quoted admin
# password failed the smoke test's login. The rules are compose's own dotenv parser, measured
# against Docker Compose v5.5.1 with `compose config`; the cases are in tools/tests/
# dotenv_compose_cases.json (which the Python readers are held to as well) and
# dotenv_compose_cases_extra.json:
#   * UTF-8 on both engines, a BOM dropped, LF or CRLF. A line that is not `KEY=...` (blank, a
#     `# comment`, junk) is skipped; `export ` before the key and blanks around the `=` are allowed.
#   * The LAST assignment of a key wins.
#   * unquoted: ends at " #" (a SPACE then a hash: `a#b` and `a<TAB># b` are kept whole), loses
#     its trailing whitespace, and a backslash is literal (C:\Users\x stays as written).
#   * 'single': literal up to the next ', across lines; the rest of that line is ignored.
#   * "double": \n \r \t \a \b \f \v \\ \" \$ are escapes and any other backslash is kept; across
#     lines; the rest of that line is ignored.
#   * unquoted and "double" values expand $NAME and ${NAME}, also with :- - :+ + :? ?, and $$ is
#     one $. A name is looked up in the process environment first, then among the keys ABOVE it in
#     the file; unset is empty. So an unquoted `pa$sword` reaches the containers as `pa`, and this
#     reader says `pa` too rather than handing a script a password nothing accepts.
#   * an unterminated quote, an unclosed or empty ${}, and ${NAME?msg} with NAME unset throw,
#     naming the line. Compose refuses the same file, so nothing would run on it either.
# Keys are case-sensitive, as in compose (an OrderedDictionary, not PowerShell's [ordered]@{}).
function Read-DotEnvFile {
  param([Parameter(Mandatory)][string]$Path)
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    return (New-Object System.Collections.Specialized.OrderedDictionary)
  }
  $text = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
  return (ConvertFrom-DotEnvText -Text $text -Source $Path)
}

# One key's value as compose interpolates it from the file, or $null when the file does not
# assign it (or does not exist). The process environment's own value of KEY is not consulted
# here: what a caller does when both are set is that caller's rule (see Get-DeclaredAppList).
function Get-DotEnvValue {
  param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$Key)
  $vars = Read-DotEnvFile -Path $Path
  if ($vars.Contains($Key)) { return [string]$vars[$Key] }
  return $null
}

function ConvertFrom-DotEnvText {
  param([Parameter(Mandatory)][AllowEmptyString()][string]$Text, [string]$Source = 'env file')
  if ($Text.Length -gt 0 -and $Text[0] -eq [char]0xFEFF) { $Text = $Text.Substring(1) }
  $vars = New-Object System.Collections.Specialized.OrderedDictionary
  $keyRx = [regex]'^[ \t]*(?:export[ \t]+)?([A-Za-z_][A-Za-z0-9_.-]*)[ \t]*=[ \t]*'
  $n = $Text.Length; $i = 0; $line = 1
  while ($i -lt $n) {
    $eol = $Text.IndexOf([char]10, $i); if ($eol -lt 0) { $eol = $n }
    $m = $keyRx.Match($Text.Substring($i, $eol - $i))
    if (-not $m.Success) { $i = $eol + 1; $line++; continue }
    $key = $m.Groups[1].Value
    $p = $i + $m.Length
    $q = if ($p -lt $eol) { $Text[$p] } else { [char]0 }
    if ($q -eq [char]39 -or $q -eq [char]34) {
      $close = -1
      for ($j = $p + 1; $j -lt $n; $j++) {
        if ($q -eq [char]34 -and $Text[$j] -eq [char]92) { $j++; continue }
        if ($Text[$j] -eq $q) { $close = $j; break }
      }
      if ($close -lt 0) { throw "${Source}: line ${line}: unterminated quoted value for $key" }
      $inner = $Text.Substring($p + 1, $close - $p - 1)
      if ($q -eq [char]34) { $value = Expand-DotEnvValue -Raw $inner -Vars $vars -DoubleQuoted -Line $line -Source $Source }
      else { $value = $inner }
      $line += $inner.Split([char]10).Count - 1
      $eol = $Text.IndexOf([char]10, $close); if ($eol -lt 0) { $eol = $n }
    }
    else {
      $raw = $Text.Substring($p, $eol - $p)
      $h = $raw.IndexOf(' #', [System.StringComparison]::Ordinal); if ($h -ge 0) { $raw = $raw.Substring(0, $h) }
      $value = Expand-DotEnvValue -Raw $raw.TrimEnd() -Vars $vars -Line $line -Source $Source
    }
    $vars[$key] = $value
    $i = $eol + 1; $line++
  }
  return $vars
}

# The $-expansion and the double-quote escapes of one value (see Read-DotEnvFile for the rules).
function Expand-DotEnvValue {
  param([AllowEmptyString()][string]$Raw, [System.Collections.IDictionary]$Vars, [switch]$DoubleQuoted,
        [int]$Line, [string]$Source)
  if ($Raw.IndexOf([char]36) -lt 0 -and -not ($DoubleQuoted -and $Raw.IndexOf([char]92) -ge 0)) { return $Raw }
  $sb = New-Object System.Text.StringBuilder
  $n = $Raw.Length; $i = 0
  while ($i -lt $n) {
    $c = $Raw[$i]
    if ($DoubleQuoted -and $c -eq [char]92 -and $i + 1 -lt $n) {
      $e = [string]$Raw[$i + 1]
      $mapped = switch -CaseSensitive ($e) {
        'n' { "`n" } 'r' { "`r" } 't' { "`t" } 'a' { [string][char]7 } 'b' { [string][char]8 }
        'f' { [string][char]12 } 'v' { [string][char]11 } '\' { '\' } '"' { '"' } '$' { '$' }
        default { $null }
      }
      if ($null -ne $mapped) { [void]$sb.Append($mapped) } else { [void]$sb.Append($c).Append($e) }
      $i += 2; continue
    }
    if ($c -eq [char]36 -and $i + 1 -lt $n) {
      $d = $Raw[$i + 1]
      if ($d -eq [char]36) { [void]$sb.Append('$'); $i += 2; continue }
      if ($d -eq [char]123) {
        $depth = 1; $j = $i + 2
        while ($j -lt $n) {
          if ($Raw[$j] -eq [char]36 -and $j + 1 -lt $n -and $Raw[$j + 1] -eq [char]123) { $depth++; $j += 2; continue }
          if ($Raw[$j] -eq [char]125) { $depth--; if ($depth -eq 0) { break } }
          $j++
        }
        if ($depth -ne 0) { throw "${Source}: line ${Line}: invalid template `"$Raw`" (unclosed `${)" }
        $body = $Raw.Substring($i + 2, $j - $i - 2)
        $bm = [regex]::Match($body, '^([A-Za-z_][A-Za-z0-9_]*)(?:(:?[-+?])([\s\S]*))?$')
        if (-not $bm.Success) { throw "${Source}: line ${Line}: invalid template `"$Raw`"" }
        $name = $bm.Groups[1].Value; $op = $bm.Groups[2].Value; $word = $bm.Groups[3].Value
        $look = Get-DotEnvLookup -Name $name -Vars $Vars
        $empty = -not $look.Set -or $look.Value -eq ''
        $useWord = switch ($op) {
          ':-' { $empty } '-' { -not $look.Set } ':+' { -not $empty } '+' { $look.Set } default { $false }
        }
        if (($op -eq ':?' -and $empty) -or ($op -eq '?' -and -not $look.Set)) {
          throw "${Source}: line ${Line}: required variable $name is missing a value: $word"
        }
        if ($useWord) {
          [void]$sb.Append((Expand-DotEnvValue -Raw $word -Vars $Vars -DoubleQuoted:$DoubleQuoted -Line $Line -Source $Source))
        }
        elseif ($op -ne ':+' -and $op -ne '+') { [void]$sb.Append($look.Value) }
        $i = $j + 1; continue
      }
      $nm = [regex]::Match($Raw.Substring($i + 1), '^[A-Za-z_][A-Za-z0-9_]*')
      if ($nm.Success) {
        [void]$sb.Append((Get-DotEnvLookup -Name $nm.Value -Vars $Vars).Value)
        $i += 1 + $nm.Length; continue
      }
    }
    [void]$sb.Append($c); $i++
  }
  return $sb.ToString()
}

# A name as compose's expansion sees it: the process environment, else a key above in the file.
function Get-DotEnvLookup {
  param([string]$Name, [System.Collections.IDictionary]$Vars)
  $v = [Environment]::GetEnvironmentVariable($Name)
  if ($null -ne $v) { return @{ Set = $true; Value = $v } }
  if ($Vars.Contains($Name)) { return @{ Set = $true; Value = [string]$Vars[$Name] } }
  return @{ Set = $false; Value = '' }
}

# A value as a deploy\.env line must hold it for compose to read back exactly what was given: a
# double-quoted string with \, " and $ escaped (the double-quote escapes Read-DotEnvFile lists),
# and a CR or LF as \r or \n, so the assignment stays on one line for the line-based writers.
# Written bare, compose's rules changed a typed admin password before the gateway saw it (measured
# with deploy_snapshot.read_env, which is held to `docker compose config`): `P@$$w0rd` became
# `P@$w0rd`, `x${Y}z` became `xz`, `abc #1` became `abc`, blanks at either end went, and a leading
# quote made compose refuse the file. In double quotes nothing is expanded and no comment begins.
function ConvertTo-DotEnvQuoted {
  param([AllowNull()][AllowEmptyString()][string]$Value)
  return ('"' + $Value.Replace('\', '\\').Replace('"', '\"').Replace('$', '\$').Replace("`r", '\r').Replace("`n", '\n') + '"')
}

# deploy\.env's text from env.lean.example (the text as read): the admin's name and password as
# quoted values (ConvertTo-DotEnvQuoted), the rail list as it is (rail ids hold nothing compose
# treats specially). One filler for install.ps1 and lib-apply's env step, so the two installer
# paths cannot write the same selections differently.
function Expand-LeanEnvTemplate {
  param([Parameter(Mandatory)][AllowEmptyString()][string]$Text, [AllowNull()][AllowEmptyString()][string]$AdminUser,
        [AllowNull()][AllowEmptyString()][string]$AdminPassword, [AllowNull()][AllowEmptyString()][string]$EnabledApps)
  $out = $Text.Replace('{{ADMIN_USER}}', (ConvertTo-DotEnvQuoted -Value $AdminUser))
  $out = $out.Replace('{{ADMIN_PASSWORD}}', (ConvertTo-DotEnvQuoted -Value $AdminPassword))
  return $out.Replace('{{ENABLED_APPS}}', $EnabledApps)
}

# --- the lean broker service: its token, its bind address, its firewall rules ---------------------
# A lean install used to register the broker on 0.0.0.0:11500 with no token at all, while every rail
# and the gateway sent ${BROKER_AUTH_TOKEN:-} (empty): an open GPU and role control plane for any
# container on the box (terminal-fun's games included) and, wherever a python allow rule existed, the
# LAN. Now the install writes a token into deploy\.env and the service's environment, and the broker
# binds loopback wherever the containers reach it that way.

# A new shared broker token: 32 random bytes as hex, so compose's dotenv has nothing in it to treat
# specially ($, #, quotes, blanks) and the rails, the gateway and the service read the same string.
function New-BrokerToken {
  $bytes = New-Object byte[] 32
  $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
  try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
  return (-join ($bytes | ForEach-Object { $_.ToString('x2') }))
}

# This box's broker token: BROKER_AUTH_TOKEN as compose reads it from deploy\.env, generated into the
# file first when it has none or an empty one. Returns @{ Token; Generated }. Never logs the value.
function Initialize-BrokerToken {
  param([Parameter(Mandatory)][string]$EnvFile)
  $t = ([string](Get-DotEnvValue -Path $EnvFile -Key 'BROKER_AUTH_TOKEN')).Trim()
  if ($t) { return @{ Token = $t; Generated = $false } }
  $t = New-BrokerToken
  Set-EnvValue -Path $EnvFile -Key 'BROKER_AUTH_TOKEN' -Value $t
  return @{ Token = $t; Generated = $true }
}

# Where the lean broker listens, per runtime. The containers reach it as host.docker.internal:
#   podman  - gvproxy NATs its host address to the host's own 127.0.0.1 (see Get-PodmanHostIp), so a
#             loopback bind is reachable from every container and from nothing else.
#   desktop - Docker Desktop delivers host.docker.internal traffic from 127.0.0.1 as well. Measured on
#             the 24 GB box (Docker Desktop) on 2026-10-08: every request in its broker's access log
#             came from 127.0.0.1 or one LAN host, the rails' own /v1/chat calls included.
#   wsl     - the engine runs inside the WSL2 VM, so its containers arrive over the WSL vNIC at the
#             Windows end of that adapter, whose address changes across reboots and does not exist
#             yet when the service starts at boot. Only 0.0.0.0 covers it, so a wsl install also gets
#             Get-BrokerFirewallRules: that range admitted, every other remote address blocked.
# An undeclared runtime gets loopback: the safe answer, and install.ps1 declares wsl when it is wsl.
function Get-BrokerBindHost {
  param([string]$Mode)
  if ($Mode -eq 'wsl') { return '0.0.0.0' }
  return '127.0.0.1'
}

# The inbound rules a wsl-mode broker needs. The allow rule (its name unchanged, so a reinstall finds
# the old one) admits WSL's NAT range to the broker and Ollama. The block rule is what scopes the
# 0.0.0.0 binds: Windows Firewall applies a block before any allow, so a python.exe or ollama.exe
# allow rule left by an earlier "Allow access" prompt does not open either to the LAN. Loopback
# (127/8) and the WSL range are left out of the block. It covers :11434 as well as :11500 since a
# wsl install binds Ollama to 0.0.0.0 too (Set-WslOllamaHost), and Ollama has no authentication.
# A NEW name for that reason: install-native keeps a rule it finds by name, so the old
# 'AI-Platform: broker, block all but WSL and loopback' (:11500 only) would have stood unchanged on
# a box installed before. Where it exists it stays, a subset of this one.
function Get-BrokerFirewallRules {
  return @(
    @{ DisplayName = 'AI-Platform: WSL -> broker/ollama'; Action = 'Allow'; LocalPort = @(11500, 11434)
       RemoteAddress = @('172.16.0.0/12') },
    @{ DisplayName = 'AI-Platform: broker/ollama, block all but WSL and loopback'; Action = 'Block'; LocalPort = @(11500, 11434)
       RemoteAddress = @('0.0.0.0-126.255.255.255', '128.0.0.0-172.15.255.255', '172.32.0.0-255.255.255.255') }
  )
}

# --- a wsl box's Ollama app, bound where its containers reach it ----------------------------------
# In wsl mode the containers reach the host at WINDOWS_HOST, the Windows end of the WSL vNIC, and the
# Ollama app binds 127.0.0.1:11434 unless OLLAMA_HOST says otherwise, so the two calls a container
# makes to Ollama itself (Admin > Models > Delete, AI Playground's Embedding Lab pull and weekly
# re-pull) were refused there. A wsl install sets the user's OLLAMA_HOST to 0.0.0.0:11434, as
# Ollama's FAQ says to on Windows ("Ollama inherits your user and system environment variables":
# quit the app, set the variable for your account, start the app), and restarts a running app so it
# binds it; Get-BrokerFirewallRules blocks :11434 to all but loopback and the WSL range. The ollama
# CLI still reaches the server with that value: Go dials a wildcard address as loopback on Windows
# (net/ipsock_posix.go, internetSocket). Podman (gvproxy) and Docker Desktop reach the loopback bind,
# so every other runtime gets '' here and nothing below runs for it.
$WslOllamaHost = '0.0.0.0:11434'
function Get-OllamaHostForMode {
  param([string]$Mode)
  if ($Mode -eq 'wsl') { return $WslOllamaHost }
  return ''
}

# Where the user's OLLAMA_HOST lives: the User environment (HKCU\Environment; the .NET call also
# tells Explorer, so the next logon's app gets it). A store of the same shape is how a test keeps
# the write inside itself.
$OllamaHostStore = @{ Get = { [Environment]::GetEnvironmentVariable('OLLAMA_HOST', 'User') }; Set = { param($v) [Environment]::SetEnvironmentVariable('OLLAMA_HOST', $v, 'User') } }

# wsl mode only (any other -Mode returns nothing and touches nothing): OLLAMA_HOST in the user's
# environment and this process's (so the app this run starts, here or in the caller, inherits it:
# a child gets its parent's environment, not the registry's), then the app restarted when the
# :11434 listener is not already on 0.0.0.0. Only the app is restarted: a server it did not start
# (a service, a terminal's `ollama serve`) is named with what to do, never stopped. Returns the
# lines to log.
function Set-WslOllamaHost {
  param([string]$Mode, [string]$AppPath, [hashtable]$Store = $OllamaHostStore,
        [int]$Port = 11434, [int]$WaitSeconds = 20)
  $want = Get-OllamaHostForMode -Mode $Mode
  if (-not $want) { return @() }
  if (-not $AppPath) { $AppPath = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama app.exe' }
  $lines = New-Object System.Collections.Generic.List[string]
  $had = [string](& $Store.Get)
  if ($had -ne $want) {
    & $Store.Set $want
    $lines.Add("OLLAMA_HOST=$want set in your user environment" + $(if ($had) { " (it was $had)" } else { '' }) +
               ', so the containers reach Ollama across the WSL adapter')
  }
  else { $lines.Add("OLLAMA_HOST=$want is already set in your user environment") }
  $env:OLLAMA_HOST = $want
  $listening = { @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
                   ForEach-Object { [string]$_.LocalAddress } | Sort-Object -Unique) }
  $now = @(& $listening)
  if ($now.Count -eq 0) { $lines.Add("nothing listens on :$Port yet; the Ollama app started next inherits it"); return $lines.ToArray() }
  if ($now -contains '0.0.0.0') { $lines.Add("Ollama already listens on 0.0.0.0:$Port"); return $lines.ToArray() }
  $apps = @(Get-Process -Name 'ollama app' -ErrorAction SilentlyContinue)
  if ($apps.Count -eq 0) {
    $lines.Add("WARNING: Ollama listens on $($now -join ', ') port $Port, but not from the Ollama app (a service, or a " +
               "terminal's 'ollama serve'), so it was not restarted. Restart it with OLLAMA_HOST=$want in its " +
               'environment, or the containers cannot reach it.')
    return $lines.ToArray()
  }
  # The app runs its server as `ollama.exe serve`; one left running keeps the loopback bind, so it
  # goes too. Any other ollama.exe (an `ollama run` in a terminal) is left alone.
  $servers = @(Get-CimInstance -ClassName Win32_Process -Filter "Name = 'ollama.exe'" -ErrorAction SilentlyContinue |
               Where-Object { [string]$_.CommandLine -match '\sserve(\s|$)' })
  $ids = @(@($apps | ForEach-Object { [int]$_.Id }) + @($servers | ForEach-Object { [int]$_.ProcessId }))
  foreach ($id in $ids) { Stop-Process -Id $id -Force -ErrorAction SilentlyContinue }
  Wait-Process -Id $ids -Timeout 10 -ErrorAction SilentlyContinue
  if (-not (Test-Path -LiteralPath $AppPath)) {
    $lines.Add("WARNING: stopped the Ollama app to rebind it, but $AppPath is not there to start again: start Ollama from the Start menu.")
    return $lines.ToArray()
  }
  Start-Process -FilePath $AppPath | Out-Null
  for ($i = 0; $i -lt $WaitSeconds; $i++) {
    if (@(& $listening) -contains '0.0.0.0') { $lines.Add("restarted the Ollama app; it listens on 0.0.0.0:$Port"); return $lines.ToArray() }
    Start-Sleep -Seconds 1
  }
  $lines.Add("WARNING: restarted the Ollama app, but :$Port is still not on 0.0.0.0 after $WaitSeconds s " +
             "($((@(& $listening)) -join ', ')). Quit Ollama from its tray icon and start it again.")
  return $lines.ToArray()
}

# The uvicorn arguments and the service environment of the lean broker, from one place, so the
# installer registers and the apply engine's check expects the same thing. -Root is the checkout the
# service runs from: its role map and disabled list are that box's own files (Get-LeanBrokerFiles).
function Get-BrokerServiceSpec {
  param([Parameter(Mandatory)][string]$BindHost, [Parameter(Mandatory)][string]$Token,
        [Parameter(Mandatory)][string]$Root)
  $files = Get-LeanBrokerFiles -Root $Root
  return @{
    AppParameters = "-m uvicorn app.main:app --app-dir services\broker --host $BindHost --port 11500"
    Environment   = @('BROKER_MEDIA_ENABLED=false', "BROKER_AUTH_TOKEN=$Token",
                      "BROKER_ROLES_FILE=$($files.Roles)", "BROKER_DISABLED_FILE=$($files.Disabled)")
  }
}

# --- a lean box's role map and disabled list: its own files, never the tracked ones ----------------
# The broker writes Admin > Rails repoints and disabled models back into the files it reads, and
# with no BROKER_ROLES_FILE / BROKER_DISABLED_FILE those are services\broker\roles.json and
# disabled.json, TRACKED files. The lean installer used to copy roles.lean.json over the tracked
# roles.json, so the update get.ps1 tells its users to run (a `git reset --hard`) put the 24 GB map
# back and dropped every admin edit, and the broker hot-read the result at once. A lean box keeps
# both under deploy\local\ instead, which deploy\.gitignore ignores, so neither a reset nor a pull
# touches them; its service is registered to read them there (Get-BrokerServiceSpec). The full
# stack's own maps are install-services.ps1's -RolesFile / -DisabledFile, outside the checkout.
# GetFullPath, so the installer's spec and the apply engine's check spell the paths the same way.
function Get-LeanBrokerFiles {
  param([Parameter(Mandatory)][string]$Root)
  $r = [IO.Path]::GetFullPath($Root)
  $dir = Join-Path $r 'deploy\local'
  return @{ Dir = $dir; Roles = (Join-Path $dir 'roles.json'); Disabled = (Join-Path $dir 'disabled.json')
            TrackedRoles = (Join-Path $r 'services\broker\roles.json')
            TrackedDisabled = (Join-Path $r 'services\broker\disabled.json') }
}

# Make sure both box-local files exist. A missing one is seeded from what the broker reads NOW, the
# tracked file as it stands: on a box installed before these files existed, that is its lean map
# and its admin edits, which registering the broker on the new files would otherwise drop. Never
# overwrites a file that exists. Returns one "<file> <- <source>" line per file it seeded.
function Initialize-LeanBrokerFiles {
  param([Parameter(Mandatory)][string]$Root)
  $f = Get-LeanBrokerFiles -Root $Root
  $seeded = @()
  foreach ($pair in @(@($f.Roles, $f.TrackedRoles, "{}`n"), @($f.Disabled, $f.TrackedDisabled, "[]`n"))) {
    if (Test-Path -LiteralPath $pair[0]) { continue }
    New-Item -ItemType Directory -Force -Path $f.Dir | Out-Null
    if (Test-Path -LiteralPath $pair[1] -PathType Leaf) {
      Copy-Item -LiteralPath $pair[1] -Destination $pair[0]
      $seeded += "$($pair[0]) <- $($pair[1])"
    }
    else {
      [System.IO.File]::WriteAllText($pair[0], $pair[2], (New-Object System.Text.UTF8Encoding($false)))
      $seeded += "$($pair[0]) <- (empty)"
    }
  }
  return $seeded
}

# The header a host-side script sends the broker: Bearer <BROKER_AUTH_TOKEN as compose reads it from
# deploy\.env>, the value the service was given. Empty when the box has no token (an open broker).
function Get-BrokerAuthHeaders {
  param([Parameter(Mandatory)][string]$EnvFile)
  $t = ([string](Get-DotEnvValue -Path $EnvFile -Key 'BROKER_AUTH_TOKEN')).Trim()
  if ($t) { return @{ Authorization = "Bearer $t" } }
  return @{}
}

# Whether the registered platform-broker service already runs that spec: its NSSM parameters, read
# from the registry (a plain read, no admin). A service registered before the token existed, or bound
# to another address, is NOT current, so the apply engine re-registers it instead of calling it done.
function Test-BrokerServiceCurrent {
  param([Parameter(Mandatory)][hashtable]$Spec,
        [string]$Key = 'HKLM:\SYSTEM\CurrentControlSet\Services\platform-broker\Parameters')
  try { $p = Get-ItemProperty -Path $Key -ErrorAction Stop } catch { return $false }
  if ([string]$p.AppParameters -ne $Spec.AppParameters) { return $false }
  $have = @($p.AppEnvironmentExtra | ForEach-Object { [string]$_ })
  foreach ($e in $Spec.Environment) { if ($have -notcontains $e) { return $false } }
  return $true
}

# The platform-broker service's environment as registered (AppEnvironmentExtra, a plain registry
# read, no admin), blanks dropped; nothing when no service is registered.
function Get-InstalledBrokerEnv {
  param([string]$Key = 'HKLM:\SYSTEM\CurrentControlSet\Services\platform-broker\Parameters')
  try { $p = Get-ItemProperty -Path $Key -ErrorAction Stop } catch { return @() }
  if ($p.PSObject.Properties.Name -notcontains 'AppEnvironmentExtra') { return @() }
  return @($p.AppEnvironmentExtra | ForEach-Object { [string]$_ } | Where-Object { $_ })
}

# The ffmpeg.exe to hand the broker service as AI_VOICE_FFMPEG. ai-voice's RVC engine runs ffmpeg in
# the broker's environment, AI_VOICE_FFMPEG when it is set, else the one on PATH
# (rails/ai-voice/native/engines/rvc_synth_cli.py), and the broker is a LocalSystem service, which
# sees the MACHINE PATH only, while the DevToolbox puts ffmpeg on the user's, so RVC synthesis failed
# under the service on such a box. So install-services gives the service the full path of the ffmpeg
# its own (the installing user's) shell finds: Get-Command's first application, and for a .cmd or
# .bat shim, which is what the DevToolbox's native\bin\ffmpeg.cmd is (`"<...>\ffmpeg.exe" %*`), the
# ffmpeg.exe it names (quoted, or bare from a drive or %~dp0 or a %VARIABLE%) once that file exists,
# since the engine runs the path it is given. Returns @{ Path; Note }: Path '' when there is none to
# give, Note saying why either way.
function Resolve-FfmpegExe {
  $cmd = @(Get-Command ffmpeg -CommandType Application -ErrorAction SilentlyContinue)[0]
  if (-not $cmd) { return @{ Path = ''; Note = 'no ffmpeg on PATH in this shell' } }
  $src = [string]$cmd.Source
  if ($src -notmatch '\.(cmd|bat)$') { return @{ Path = $src; Note = "the ffmpeg on PATH, $src" } }
  $text = ''
  try { $text = [IO.File]::ReadAllText($src) } catch {}
  $dir = Split-Path -Parent $src
  $rx = [regex]::new('"([^"\r\n]*ffmpeg\.exe)"|((?:%~dp0|%[A-Za-z_][A-Za-z0-9_]*%|[A-Za-z]:\\)[^\s"]*ffmpeg\.exe)',
                     [Text.RegularExpressions.RegexOptions]::IgnoreCase)
  foreach ($m in $rx.Matches($text)) {
    $p = if ($m.Groups[1].Success) { $m.Groups[1].Value } else { $m.Groups[2].Value }
    $p = [Environment]::ExpandEnvironmentVariables($p.Replace('%~dp0', $dir + '\'))
    if (Test-Path -LiteralPath $p -PathType Leaf) {
      return @{ Path = [IO.Path]::GetFullPath($p); Note = "the ffmpeg.exe the shim $src runs" }
    }
  }
  return @{ Path = ''; Note = "the ffmpeg on PATH is the shim $src, which names no ffmpeg.exe that exists" }
}

# The entries of an installed broker's environment that a re-register carries forward, the set
# install-services.ps1 carries: this box's Claude CLI backend settings (BROKER_CLI_*: the owner's
# usernames, their claude.exe, the token file, which no tracked script holds) and its own map files
# (BROKER_ROLES_FILE, BROKER_UPSTREAMS_FILE, BROKER_DISABLED_FILE).
function Select-CarriedBrokerEnv {
  param([AllowNull()][AllowEmptyCollection()][string[]]$Existing)
  $files = @('BROKER_ROLES_FILE', 'BROKER_UPSTREAMS_FILE', 'BROKER_DISABLED_FILE')
  return @(@($Existing) | ForEach-Object { [string]$_ } | Where-Object {
      $_.IndexOf('=') -gt 0 -and (($_ -like 'BROKER_CLI_*=*') -or ($files -contains $_.Substring(0, $_.IndexOf('=')))) })
}

# The environment install-native.ps1 registers the lean broker with: what the installed service
# carries forward (Select-CarriedBrokerEnv), then the spec's own entries over it, so the spec's
# bind, token, media switch and deploy\local maps win and Test-BrokerServiceCurrent agrees. It used
# to register the spec ALONE, so every apply that re-registered the broker (whenever that check said
# no, which install-services' old 0.0.0.0 bind made every time) turned the Claude CLI backend off and
# dropped the box's upstreams, with nothing to notice: each call was simply answered locally.
function Get-LeanBrokerServiceEnv {
  param([Parameter(Mandatory)][hashtable]$Spec, [AllowNull()][AllowEmptyCollection()][string[]]$Existing)
  return @(Merge-ServiceEnv -Base @(Select-CarriedBrokerEnv -Existing $Existing) -Overlay $Spec.Environment)
}

# What install-services.ps1 registers the broker as, by the stack this box runs, so the two
# installers agree on one box: @{ Stack; Why; BindHost; AppParameters; Overlay; Problem }.
#   full - the full stack's documented broker: 0.0.0.0 (its LAN upstream reaches it), media on, the
#          command line it always registered; no Overlay.
#   lean - the lean spec (Get-BrokerServiceSpec): the bind Get-BrokerBindHost gives the runtime
#          deploy\.env declares, BROKER_MEDIA_ENABLED=false, the token, deploy\local's maps. Overlay
#          is that spec's environment, for the caller to merge LAST, so Test-BrokerServiceCurrent
#          (the apply engine's check) agrees after either installer. install-services registered
#          `--host 0.0.0.0` with media on there, so the check was false on every lean box after it,
#          the next `apply -Execute` re-ran install-native (which then dropped BROKER_CLI_*), and in
#          between a loopback broker listened on every interface.
# The stack is -Stack when given; else lean when the INSTALLED broker runs with
# BROKER_MEDIA_ENABLED=false, which only the lean installers write; else full. On lean, -RolesFile
# and -DisabledFile (named in -FileParams when the caller passed them) are refused: the box's maps
# are its deploy\local files, and the next apply would put those back anyway. A lean broker without
# a token is refused too: the lean spec requires one, and install-native puts one in deploy\.env.
function Get-InstallServicesBrokerPlan {
  param([AllowNull()][AllowEmptyCollection()][string[]]$Existing, [AllowEmptyString()][string]$Stack,
        [Parameter(Mandatory)][string]$EnvFile, [Parameter(Mandatory)][string]$Root,
        [AllowEmptyString()][string]$Token, [AllowEmptyCollection()][string[]]$FileParams = @())
  $why = 'from -Stack'
  if (-not $Stack) {
    if (@($Existing) -contains 'BROKER_MEDIA_ENABLED=false') {
      $Stack = 'lean'; $why = 'the installed broker runs with BROKER_MEDIA_ENABLED=false, which only the lean installers set'
    }
    else { $Stack = 'full'; $why = 'the installed broker is not a lean one, or there is none' }
  }
  if ($Stack -ne 'lean') {
    return @{ Stack = 'full'; Why = $why; BindHost = '0.0.0.0'; Overlay = @(); Problem = ''
              AppParameters = '-m uvicorn app.main:app --app-dir services\broker --host 0.0.0.0 --port 11500' }
  }
  $declared = Get-DeclaredRuntime -EnvFile $EnvFile
  $bind = Get-BrokerBindHost -Mode $declared
  $problem = ''
  if (@($FileParams).Count) {
    $problem = ("this box runs the lean stack ($why), whose broker reads its role map and disabled list from " +
                "$(Join-Path (Get-LeanBrokerFiles -Root $Root).Dir '{roles,disabled}.json'): edit those instead of passing " +
                "-$(@($FileParams) -join ' / -'), or pass -Stack full if this box runs the full stack.")
  }
  elseif (-not $Token) {
    $problem = ("this box runs the lean stack ($why), and deploy\.env holds no BROKER_AUTH_TOKEN, which its broker " +
                'requires: run the lean installer''s broker step (deploy\installer\install-native.ps1), which generates one.')
  }
  if ($problem) { return @{ Stack = 'lean'; Why = $why; BindHost = $bind; AppParameters = ''; Overlay = @(); Problem = $problem } }
  $spec = Get-BrokerServiceSpec -BindHost $bind -Token $Token -Root $Root
  return @{ Stack = 'lean'; Why = $why; BindHost = $bind; AppParameters = $spec.AppParameters
            Overlay = @($spec.Environment); Problem = '' }
}

# --- NSSM service environments: merged without a blank, and a file of box settings -----------------
# AppEnvironmentExtra is a REG_MULTI_SZ, which an empty string terminates: NSSM stops reading at the
# first blank element and silently ignores everything after it (CLAUDE.md records a BROKER_TTS_PYTHON
# appended past one, invisible to the service while every earlier entry worked). The lists read back
# from the registry on the podman box end in exactly such a blank.

# One environment list from a base and an overlay, in that order: blank and malformed elements
# (no `=`, or nothing before it) dropped; a key set twice keeps its LAST value at its FIRST position
# (case-insensitively, as Windows treats variable names); and `KEY=` with nothing after it DELETES
# the key. Never returns a blank element.
function Merge-ServiceEnv {
  param([AllowNull()][AllowEmptyCollection()][string[]]$Base, [AllowNull()][AllowEmptyCollection()][string[]]$Overlay)
  $order = New-Object System.Collections.Generic.List[string]
  $value = New-Object 'System.Collections.Generic.Dictionary[string,string]' ([System.StringComparer]::OrdinalIgnoreCase)
  $spelled = New-Object 'System.Collections.Generic.Dictionary[string,string]' ([System.StringComparer]::OrdinalIgnoreCase)
  foreach ($e in @(@($Base) + @($Overlay))) {
    $s = [string]$e
    if (-not $s.Trim()) { continue }
    $i = $s.IndexOf('=')
    if ($i -lt 1 -or -not $s.Substring(0, $i).Trim()) { continue }
    $k = $s.Substring(0, $i).Trim(); $v = $s.Substring($i + 1)
    if ($v -eq '') {
      if ($value.Remove($k)) { [void]$order.Remove(@($order | Where-Object { $_ -ieq $k })[0]) }
      continue
    }
    if (-not $value.ContainsKey($k)) { $order.Add($k); $spelled[$k] = $k }
    $value[$k] = $v
  }
  return @($order | ForEach-Object { "$($spelled[$_])=$($value[$_])" })
}

# The KEY=value lines of a service-environment file, read by deploy\.env's rules (ConvertFrom-
# DotEnvText: compose's quoting, comments, last assignment wins), as a list Merge-ServiceEnv takes:
# `KEY=` (empty) stays in the list and means "remove KEY". Every key must start with -Prefix: the
# file configures ONE service's own settings, and a stray PATH= or BROKER_AUTH_TOKEN= in it would
# reach the service unreviewed. A refusal names the key and line, never a value: the file can hold
# a token. A value with a line break or NUL is refused, as no environment entry can hold one.
function Read-ServiceEnvFile {
  param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$Prefix)
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "the service environment file $Path does not exist" }
  $text = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
  try { $vars = ConvertFrom-DotEnvText -Text $text -Source $Path }
  catch {
    # The parser's own message can quote the value it choked on; say where, not what.
    $at = if ($_.Exception.Message -match 'line (\d+)') { " at line $($Matches[1])" } else { '' }
    throw "$Path does not parse as an env file$at (an unterminated quote, or an unclosed `${ in a value)"
  }
  $bad = @($vars.Keys | Where-Object { -not ([string]$_).StartsWith($Prefix, [StringComparison]::Ordinal) })
  if ($bad.Count) {
    throw ("$Path may only set $Prefix* settings; refusing it for: " + ($bad -join ', ') +
           ". Put any other setting where it belongs (deploy\.env, or this script's own parameters).")
  }
  $broken = @($vars.Keys | Where-Object { ([string]$vars[$_]).IndexOfAny([char[]]@("`r", "`n", [char]0)) -ge 0 })
  if ($broken.Count) { throw "$Path gives $($broken -join ', ') a value with a line break, which no service environment can hold" }
  return @($vars.Keys | ForEach-Object { "$_=$($vars[$_])" })
}

# Problems with the Claude CLI backend's settings in a KEY=value list, one line each, naming the key
# and the path (never a token). Only the keys present are checked; `KEY=` (a removal) is not.
#   BROKER_CLI_EXE              absolute, exists, a real .exe: the broker launches it without a
#                               shell, so a .cmd/.ps1 shim cannot run (README, "Under LocalSystem").
#   BROKER_CLI_OAUTH_TOKEN_FILE absolute, exists: LocalSystem resolves a relative one elsewhere.
#   BROKER_CLI_WORKDIR          absolute, exists, outside the repo and any OneDrive folder, and with
#                               no CLAUDE.md in it or ANY directory above it: Claude Code reads every
#                               CLAUDE.md above its working directory into a rail's prompt.
#   BROKER_CLI_ROLES_FILE       absolute, outside the repo: the Rails tab writes it, and a file inside
#                               the deployment checkout dirties it (it need not exist yet).
#   BROKER_CLI_TEMP_ROOT        absolute, not a file, outside the repo and any OneDrive folder, not
#                               BROKER_CLI_WORKDIR or inside it (when the list sets that too: the
#                               workdir must stay empty), and with no CLAUDE.md in it or any
#                               directory above it, the workdir's rule. The broker makes it and each
#                               call's own TEMP under it, so it need not exist yet.
#   BROKER_CLI_MODEL            one of BROKER_CLI_MODELS, when the list sets that too: the broker
#                               passes it as `--model` on every call the switch file gives no
#                               model of its own, and holds only the switch file to that list.
#   any key                     not a value starting with `#`: under deploy\.env's rules a comment
#                               after `KEY=` IS the value (the key's `=[ \t]*` takes the spaces, so
#                               no ` #` is left to cut at). The broker README's own example once set
#                               BROKER_CLI_MODEL to its comment, and every call passed it as
#                               `--model`. Named, never quoted: the key could hold a token.
function Test-BrokerCliEnv {
  param([AllowEmptyCollection()][string[]]$Entries, [Parameter(Mandatory)][string]$RepoRoot)
  $abs = { param($p) [bool]($p -match '^[A-Za-z]:\\' -or $p -match '^\\\\[^\\]+\\') }
  # A character no Windows path holds. Said as a problem, never reached by GetExtension or
  # GetFullPath, which THROW on one under Windows PowerShell 5.1 (.NET Framework): the README's own
  # `C:\Users\<owner>\...` placeholder, copied as it stands, ended the check with "Illegal
  # characters in path" instead of naming the setting. pwsh accepts the same string.
  $badChars = [char[]](('<>|"' + -join [char[]](0..31)).ToCharArray())
  $unpathable = { param($p) ([string]$p).IndexOfAny($badChars) -ge 0 }
  $pathKeys = @('BROKER_CLI_EXE', 'BROKER_CLI_OAUTH_TOKEN_FILE', 'BROKER_CLI_WORKDIR', 'BROKER_CLI_ROLES_FILE', 'BROKER_CLI_TEMP_ROOT')
  $norm = { param($p) [IO.Path]::GetFullPath($p).TrimEnd('\') }
  $repo = & $norm $RepoRoot
  $inside = { param($p, $root) $f = & $norm $p; ($f -ieq $root -or $f.StartsWith("$root\", [StringComparison]::OrdinalIgnoreCase)) }
  $oneDrives = @($env:OneDrive, $env:OneDriveCommercial, $env:OneDriveConsumer | Where-Object { $_ } | ForEach-Object { & $norm $_ })
  $inOneDrive = { param($full) [bool]($full -match '\\OneDrive( - [^\\]+)?(\\|$)' -or @($oneDrives | Where-Object { & $inside $full $_ }).Count) }
  # The nearest CLAUDE.md in a folder or any folder above it (the folder need not exist), or $null.
  $claudeMdAbove = {
    param($full)
    $d = $full
    while ($d) {
      if (Test-Path -LiteralPath (Join-Path $d 'CLAUDE.md') -PathType Leaf) { return (Join-Path $d 'CLAUDE.md') }
      $up = Split-Path $d -Parent
      if (-not $up -or $up -eq $d) { break }
      $d = $up
    }
    return $null
  }
  # BROKER_CLI_WORKDIR as the same list sets it (the last one wins), for BROKER_CLI_TEMP_ROOT's rule.
  $workdir = $null
  foreach ($e in @($Entries)) {
    $s = [string]$e
    if ($s -like 'BROKER_CLI_WORKDIR=*') {
      $w = $s.Substring('BROKER_CLI_WORKDIR='.Length)
      $workdir = if ($w -and (& $abs $w) -and -not (& $unpathable $w)) { & $norm $w } else { $null }
    }
  }
  # BROKER_CLI_MODELS as the same list sets it (the last one wins, `KEY=` removes it), for
  # BROKER_CLI_MODEL's rule. Unset here, the model is not held to a list: the one the service ends
  # up with may be the broker's default or one carried forward from the registry.
  $models = $null
  foreach ($e in @($Entries)) {
    $s = [string]$e
    if ($s -like 'BROKER_CLI_MODELS=*') {
      $l = $s.Substring('BROKER_CLI_MODELS='.Length)
      $models = if ($l.Trim()) { @($l -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ }) } else { $null }
    }
  }
  $problems = @()
  foreach ($e in @($Entries)) {
    $i = ([string]$e).IndexOf('=')
    if ($i -lt 1) { continue }
    $k = $e.Substring(0, $i); $v = $e.Substring($i + 1)
    if ($v -eq '') { continue }
    if ($v.TrimStart().StartsWith('#')) {
      $problems += "$k is set to a comment (its value starts with '#'): a comment written after KEY= is read as the value, so put it on a line of its own"
      continue
    }
    if ($pathKeys -contains $k -and (& $unpathable $v)) {
      $problems += "$k '$v' holds a character no Windows path can (<, >, |, a double quote or a control character): a placeholder such as <owner> left in?"
      continue
    }
    switch ($k) {
      'BROKER_CLI_MODEL' {
        if ($null -ne $models -and $models -cnotcontains $v.Trim()) {
          $problems += ("BROKER_CLI_MODEL '$($v.Trim())' is not one of the models BROKER_CLI_MODELS allows " +
                        "($($models -join ', ')); every call with no model of its own would pass it as --model")
        }
      }
      'BROKER_CLI_EXE' {
        if (-not (& $abs $v)) { $problems += "BROKER_CLI_EXE '$v' is not an absolute path"; break }
        if ([IO.Path]::GetExtension($v) -ine '.exe') { $problems += "BROKER_CLI_EXE '$v' is not an .exe (the broker runs it without a shell, so a .cmd or .ps1 shim cannot start)"; break }
        if (-not (Test-Path -LiteralPath $v -PathType Leaf)) { $problems += "BROKER_CLI_EXE '$v' does not exist" }
      }
      'BROKER_CLI_OAUTH_TOKEN_FILE' {
        if (-not (& $abs $v)) { $problems += "BROKER_CLI_OAUTH_TOKEN_FILE '$v' is not an absolute path"; break }
        if (-not (Test-Path -LiteralPath $v -PathType Leaf)) { $problems += "BROKER_CLI_OAUTH_TOKEN_FILE '$v' does not exist" }
      }
      'BROKER_CLI_WORKDIR' {
        if (-not (& $abs $v)) { $problems += "BROKER_CLI_WORKDIR '$v' is not an absolute path"; break }
        if (-not (Test-Path -LiteralPath $v -PathType Container)) { $problems += "BROKER_CLI_WORKDIR '$v' does not exist"; break }
        if (& $inside $v $repo) { $problems += "BROKER_CLI_WORKDIR '$v' is inside the repo, whose agent guide would reach every rail's prompt"; break }
        $full = & $norm $v
        if (& $inOneDrive $full) {
          $problems += "BROKER_CLI_WORKDIR '$v' is inside a OneDrive folder"; break
        }
        $found = & $claudeMdAbove $full
        if ($found) {
          $problems += "BROKER_CLI_WORKDIR '$v' has $found at or above it, which Claude Code reads into every rail's prompt; use an empty directory with none above it"
        }
      }
      'BROKER_CLI_ROLES_FILE' {
        if (-not (& $abs $v)) { $problems += "BROKER_CLI_ROLES_FILE '$v' is not an absolute path"; break }
        if (& $inside $v $repo) { $problems += "BROKER_CLI_ROLES_FILE '$v' is inside the repo, and the Rails tab writes it" }
      }
      'BROKER_CLI_TEMP_ROOT' {
        if (-not (& $abs $v)) { $problems += "BROKER_CLI_TEMP_ROOT '$v' is not an absolute path"; break }
        if (Test-Path -LiteralPath $v -PathType Leaf) { $problems += "BROKER_CLI_TEMP_ROOT '$v' is a file; the broker makes each CLI call's temp directory under it"; break }
        if (& $inside $v $repo) { $problems += "BROKER_CLI_TEMP_ROOT '$v' is inside the repo, which every CLI call would write into"; break }
        $full = & $norm $v
        if (& $inOneDrive $full) {
          $problems += "BROKER_CLI_TEMP_ROOT '$v' is inside a OneDrive folder, which would sync what every CLI call writes"; break
        }
        if ($workdir -and (& $inside $full $workdir)) {
          $problems += "BROKER_CLI_TEMP_ROOT '$v' is BROKER_CLI_WORKDIR or inside it, which must stay empty; put it beside the workdir"; break
        }
        $found = & $claudeMdAbove $full
        if ($found) {
          $problems += "BROKER_CLI_TEMP_ROOT '$v' has $found at or above it; use a folder with none above it, as for BROKER_CLI_WORKDIR"
        }
      }
    }
  }
  return $problems
}

# --- NSSM service logs: rotated while running, and pruned ----------------------------------------
# Every NSSM service here set AppRotateFiles 1 and nothing more: a log rotated only when its service
# started, grew without bound while it ran, and nothing ever removed a rotated file. Measured on the
# 24 GB box by the audit: deploy\logs held 69 files, 123 MB, after 19 days (~2.4 GB a year). Now each
# service also rotates ONLINE at a size, and an NSSM Rotate/Post hook prunes that stream's rotated
# files to the newest $NssmLogKeep, so the bound holds on a box where no script of ours runs
# periodically (the full stack has no watchdog and no logon script). Only an NSSM with event hooks
# (2.24-101 or later) runs that hook; NSSM 2.24 does not, and there the watchdog prunes once a day
# (Invoke-LogPruneIfDue, below), or on a box with no watchdog rebuild-stale.ps1 does when it runs
# (Invoke-LogPruneFromStamp).
$NssmLogKeep = 10
$NssmRotateBytes = 10485760   # 10 MB

# Delete all but the newest $Keep rotated copies of each log in $LogDir. NSSM names a rotated file
# <name>-<yyyyMMddTHHmmss.fff>.log, so the name sorts by age. A live log and every file of any other
# shape (rebuild-stale-*.log, smoke-test.log) is never touched. With -Base, only the copies of that
# one log (<Base>-<stamp>.log) are, for a folder that is not ours alone. Returns how many were removed.
function Remove-RotatedServiceLogs {
  param([Parameter(Mandatory)][string]$LogDir, [int]$Keep = $NssmLogKeep, [string]$Base)
  if (-not (Test-Path -LiteralPath $LogDir)) { return 0 }
  $rx = '^(?<base>.+)-\d{8}T\d{6}\.\d{3}\.log$'
  $removed = 0
  $rotated = @(Get-ChildItem -LiteralPath $LogDir -File | Where-Object { $_.Name -match $rx })
  if ($Base) { $rotated = @($rotated | Where-Object { [regex]::Match($_.Name, $rx).Groups['base'].Value -eq $Base }) }
  foreach ($g in ($rotated | Group-Object { [regex]::Match($_.Name, $rx).Groups['base'].Value })) {
    foreach ($f in @($g.Group | Sort-Object Name -Descending | Select-Object -Skip $Keep)) {
      try { Remove-Item -LiteralPath $f.FullName -Force -ErrorAction Stop; $removed++ } catch {}
    }
  }
  return $removed
}

# The scripts' OWN logs, bounded the same way. startup.log (every logon and every watchdog
# trigger), smoke-test.log and install.log are appended to by each run and were never rotated, and
# rebuild-stale writes a new rebuild-stale-<stamp>.log per run and never removed one.
# A log at or past $MaxBytes is renamed to NSSM's rotated shape, <name>-<yyyyMMddTHHmmss.fff>.log,
# before the run appends, and Remove-RotatedServiceLogs then keeps that log's newest $Keep copies.
# Only that log's: install.ps1 keeps its log in the user's TEMP when deploy\logs cannot be made, and
# a prune of the whole folder there cut another program's <name>-<stamp>.log files to ten (delta
# audit 2026-10-15). A script that should prune deploy\logs as a whole calls Remove-RotatedServiceLogs
# itself (smoke-test.ps1, platform-startup.ps1).
# A rename that fails (another run is writing) is left for the next run. Returns the new name or ''.
function Step-ScriptLog {
  param([Parameter(Mandatory)][string]$Path, [long]$MaxBytes = $NssmRotateBytes, [int]$Keep = $NssmLogKeep)
  $to = ''
  try {
    $f = Get-Item -LiteralPath $Path -ErrorAction Stop
    if ($f.Length -ge $MaxBytes) {
      $to = Join-Path $f.DirectoryName ('{0}-{1}{2}' -f $f.BaseName, (Get-Date).ToString('yyyyMMddTHHmmss.fff'), $f.Extension)
      Move-Item -LiteralPath $Path -Destination $to -ErrorAction Stop
    }
  } catch { $to = '' }
  $null = Remove-RotatedServiceLogs -LogDir (Split-Path -Parent $Path) -Keep $Keep -Base ([IO.Path]::GetFileNameWithoutExtension($Path))
  return $to
}

# A per-run log family (rebuild-stale-<yyyyMMdd-HHmmss>.log): all but the newest $Keep removed, by
# name, which sorts by age. Called before a run writes its own, so the bound includes it.
function Remove-OldRunLogs {
  param([Parameter(Mandatory)][string]$LogDir, [Parameter(Mandatory)][string]$Filter, [int]$Keep = $NssmLogKeep)
  if (-not (Test-Path -LiteralPath $LogDir)) { return 0 }
  $removed = 0
  foreach ($f in @(Get-ChildItem -LiteralPath $LogDir -File -Filter $Filter | Sort-Object Name -Descending | Select-Object -Skip $Keep)) {
    try { Remove-Item -LiteralPath $f.FullName -Force -ErrorAction Stop; $removed++ } catch {}
  }
  return $removed
}

# The command line NSSM runs after each rotation (AppEvents Rotate/Post): Windows PowerShell running
# Remove-RotatedServiceLogs as an -EncodedCommand, so no quote has to survive nssm's argument
# handling or 5.1's native-argument quoting.
function Get-NssmPruneHook {
  param([Parameter(Mandatory)][string]$LogDir, [Parameter(Mandatory)][string]$Lib, [int]$Keep = $NssmLogKeep)
  $lit = { param($s) "'" + ($s -replace "'", "''") + "'" }
  $cmd = ". $(& $lit $Lib); `$null = Remove-RotatedServiceLogs -LogDir $(& $lit $LogDir) -Keep $Keep"
  $b64 = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($cmd))
  $ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
  return "$ps -NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand $b64"
}

# The `nssm set` argument lists for one service's logs: both streams, rotation at start AND online
# at $RotateBytes, and the prune hook. One place, so the three installers cannot drift apart again.
function Get-NssmLogSettings {
  param([Parameter(Mandatory)][string]$Name, [Parameter(Mandatory)][string]$Stdout,
        [Parameter(Mandatory)][string]$Stderr, [Parameter(Mandatory)][string]$Lib,
        [long]$RotateBytes = $NssmRotateBytes, [int]$Keep = $NssmLogKeep)
  $dir = Split-Path -Parent $Stdout
  return @(
    , @('set', $Name, 'AppStdout', $Stdout)
    , @('set', $Name, 'AppStderr', $Stderr)
    , @('set', $Name, 'AppRotateFiles', '1')
    , @('set', $Name, 'AppRotateOnline', '1')
    , @('set', $Name, 'AppRotateBytes', [string]$RotateBytes)
    , @('set', $Name, 'AppEvents', 'Rotate/Post', (Get-NssmPruneHook -LogDir $dir -Lib $Lib -Keep $Keep))
  )
}

# --- when NSSM has no hooks: the check after registration, and the watchdog's daily prune ---------
# NSSM 2.24, the build Install-NssmExe fetches, predates event hooks (they arrived in the 2.24-101
# pre-release): its `nssm set <svc> AppEvents Rotate/Post ...` fails and writes nothing, so on such a
# box the hook above never runs and rotated logs pile up again (2026-10-14: 339 rotated files, 286
# of them past the keep count). The hook stays for a build that supports it; the installers check
# for it after registration and say when it is missing, and the watchdog prunes once a day.

# Whether an argument list from Get-NssmLogSettings is the hook's, so an installer can keep that
# call's exit code without spelling the setting's name itself.
function Test-NssmHookSetting {
  param([Parameter(Mandatory)][AllowEmptyCollection()][string[]]$Arguments)
  return ($Arguments.Count -ge 3 -and $Arguments[2] -eq 'AppEvents')
}

# Whether NSSM kept $Name's Rotate/Post hook: the registry key NSSM keeps hooks in,
# Parameters\AppEvents\<event>, with the action (Post) as its value.
function Test-NssmRotateHook {
  param([Parameter(Mandatory)][string]$Name)
  $p = Get-ItemProperty -LiteralPath "HKLM:\SYSTEM\CurrentControlSet\Services\$Name\Parameters\AppEvents\Rotate" `
                        -ErrorAction SilentlyContinue
  return [bool]($p -and $p.PSObject.Properties.Name -contains 'Post' -and $p.Post)
}

# The installers' warning when $Name has no Rotate/Post hook after registration ('' when it has
# one). $HookExit is the `nssm set ... AppEvents` call's exit code. $WatchdogRegistered: whether a
# platform-watchdog service prunes deploy\logs. Without one (the full stack registers none) the line
# names what prunes there instead: rebuild-stale.ps1, at most once a day (Invoke-LogPruneFromStamp),
# and smoke-test.ps1 and platform-startup.ps1 on every run (each calls Remove-RotatedServiceLogs on
# deploy\logs before it rotates its own log).
function Get-NssmHookWarning {
  param([Parameter(Mandatory)][string]$Name, [int]$HookExit = 0, [bool]$WatchdogRegistered = $true)
  if (Test-NssmRotateHook -Name $Name) { return '' }
  $how = $(if ($HookExit -ne 0) { "nssm set $Name AppEvents exited $HookExit" } else { "nssm set $Name AppEvents reported success" })
  $msg = ("${Name}: this NSSM has no event hooks (NSSM 2.24 predates them; $how and there is no " +
          "AppEvents\Rotate key after registration), so its rotated logs are not pruned at rotation.")
  if ($WatchdogRegistered) {
    $msg += (" The platform-watchdog service prunes deploy\logs once a day instead, keeping the newest " +
             "$NssmLogKeep of each log.")
  }
  else {
    $msg += (' No platform-watchdog service is registered on this box (the full stack runs none), so ' +
             "deploy\logs is pruned, keeping the newest $NssmLogKeep of each log, only when one of these " +
             'runs: deploy\rebuild-stale.ps1 (at most once a day), deploy\installer\smoke-test.ps1, or the ' +
             'podman logon startup, platform-startup.ps1. A box that runs none of them keeps every rotated log.')
  }
  return $msg
}

# The watchdog's prune of deploy\logs, run from its monitor loop: due on the first call (a $Last of
# MinValue), then once $Interval has passed since the last run. A clock that went backwards counts
# as due, so a reset clock cannot hold the prune off until it catches up. Returns
# @{ Ran; Removed; Last }; pass Last back in as -Last next time.
function Invoke-LogPruneIfDue {
  param([Parameter(Mandatory)][string]$LogDir, [datetime]$Last = [datetime]::MinValue,
        [Parameter(Mandatory)][datetime]$Now, [int]$Keep = $NssmLogKeep,
        [timespan]$Interval = [timespan]::FromDays(1))
  if ($Now -ge $Last -and ($Now - $Last) -lt $Interval) { return @{ Ran = $false; Removed = 0; Last = $Last } }
  $n = Remove-RotatedServiceLogs -LogDir $LogDir -Keep $Keep
  return @{ Ran = $true; Removed = $n; Last = $Now }
}

# The same daily prune for a script that runs and exits, which has no loop to keep Last in: Last is
# read from, and written back to, a stamp file in $LogDir (an ISO 8601 UTC time; a missing or
# unreadable one is due). This is how a FULL-STACK box prunes: it runs no watchdog and no logon
# script, and NSSM 2.24 runs no rotate hook, so nothing of ours runs there on a clock, and the owner
# adds no scheduled task or service for a side job. rebuild-stale.ps1, the full stack's build and
# update path, calls this on every real run. A stamp that cannot be written leaves the next run due.
$LogPruneStampName = 'log-prune.last'
function Invoke-LogPruneFromStamp {
  param([Parameter(Mandatory)][string]$LogDir, [Parameter(Mandatory)][datetime]$Now, [string]$StampFile,
        [int]$Keep = $NssmLogKeep)
  if (-not $StampFile) { $StampFile = Join-Path $LogDir $LogPruneStampName }
  $last = [datetime]::MinValue
  try {
    $last = [datetime]::Parse(([IO.File]::ReadAllText($StampFile)).Trim(), [Globalization.CultureInfo]::InvariantCulture,
                              [Globalization.DateTimeStyles]::RoundtripKind).ToUniversalTime()
  } catch { $last = [datetime]::MinValue }
  $r = Invoke-LogPruneIfDue -LogDir $LogDir -Last $last -Now $Now.ToUniversalTime() -Keep $Keep
  if ($r.Ran) {
    try { [IO.File]::WriteAllText($StampFile, $r.Last.ToString('o'), (New-Object System.Text.UTF8Encoding($false))) } catch {}
  }
  return $r
}

# --- is Windows shutting down? The watchdog asks before it starts anything -----------------------
# On 2026-10-15 the owner powered the podman box off: Explorer asked for it at 01:27:30 (System event
# 1074), the logoff stopped the podman machine, and at 01:29:01 the watchdog found the platform down
# with nobody logged on and cold-started it as LocalSystem, 13 s before the event log stopped. What a
# LocalSystem PowerShell 5.1 process under NSSM can read, and why these:
#   User32 1074      a process asked for a power off or restart (Start > Shut down, shutdown.exe,
#                    Windows Update). Written when it is asked, minutes before the services stop:
#                    NSSM's own stop reaches the script only once the logoff is over, mid-start.
#   User32 1075      the last such request was aborted (shutdown /a).
#   Kernel-Boot 27   "The boot type was 0x..", at every boot, a fast-startup one (0x1) too, which
#                    leaves Win32_OperatingSystem.LastBootUpTime where it was. So the last boot is
#                    the later of the two, and a 1074 before it is the shutdown this boot followed.
# In progress: the newest of the three since the last boot, inside $ShutdownWindowMinutes, is a
# 1074. The window bounds a shutdown abandoned with no 1075 (a "this app is preventing shutdown"
# screen cancelled), after which the watchdog starts the platform as it always did. A System log that
# cannot be read proves no shutdown either, so that starts too, and the Detail says why. Asked only
# on a path about to start something: one CIM read and one log query over at most the window.
# Returns @{ InProgress; Detail }.
$ShutdownWindowMinutes = 30
$ShutdownEventReader = {
  param([datetime]$Since)
  Get-WinEvent -FilterHashtable @{ LogName = 'System'; ProviderName = @('User32', 'Microsoft-Windows-Kernel-Boot')
                                   Id = @(1074, 1075, 27); StartTime = $Since } -ErrorAction Stop
}
function Get-ShutdownState {
  param([datetime]$Now = (Get-Date), [AllowNull()][object]$LastBoot, [scriptblock]$ReadEvents = $ShutdownEventReader,
        [int]$WindowMinutes = $ShutdownWindowMinutes)
  if (-not $PSBoundParameters.ContainsKey('LastBoot')) {
    try {
      $os = Get-CimInstance -ClassName Win32_OperatingSystem -ErrorAction Stop
      if ($os) { $LastBoot = $os.LastBootUpTime }
    } catch { $LastBoot = $null }
  }
  $since = $Now.AddMinutes(-$WindowMinutes)
  if ($LastBoot -is [datetime] -and $LastBoot -gt $since) { $since = $LastBoot }
  $events = @()
  try { $events = @(& $ReadEvents $since) }
  catch {
    # Get-WinEvent says "none" by throwing NoMatchingEventsFound; anything else is a log it could not read.
    if ([string]$_.FullyQualifiedErrorId -notlike 'NoMatchingEventsFound*') {
      return @{ InProgress = $false; Detail = "the System log could not be read ($($_.Exception.Message))" }
    }
  }
  # Filtered here as well as in the query, so a reader that returns more cannot widen what counts.
  $newest = $events | Where-Object {
      $_ -and $_.TimeCreated -ge $since -and (
        ($_.ProviderName -eq 'User32' -and $_.Id -in @(1074, 1075)) -or
        ($_.ProviderName -eq 'Microsoft-Windows-Kernel-Boot' -and $_.Id -eq 27)) } |
    Sort-Object -Property TimeCreated, RecordId -Descending | Select-Object -First 1
  if (-not $newest -or $newest.Id -ne 1074) { return @{ InProgress = $false; Detail = '' } }
  # The 1074's own words: [0] the process that asked, [4] power off or restart.
  $props = if ($newest.PSObject.Properties['Properties']) { @($newest.Properties) } else { @() }
  $what = if ($props.Count -gt 4) { @([string]$props[0].Value, [string]$props[4].Value) | Where-Object { $_ } } else { @() }
  $who = if (@($what).Count) { ': ' + (@($what) -join ', ') } else { '' }
  return @{ InProgress = $true
            Detail = ('Windows is shutting down or restarting (System event 1074 at {0:yyyy-MM-dd HH:mm:ss}{1}; ' +
                      'no cancel or boot since)') -f $newest.TimeCreated, $who }
}

# --- the profile the watchdog service is given ----------------------------------------------
# register-watchdog.ps1 bakes a user profile into the watchdog service (USERPROFILE, APPDATA,
# LOCALAPPDATA, HOME) so podman, run by LocalSystem, finds that user's machine config and SSH key.
# Run through the RunAS Helper gate, as WATCHDOG.md says to, the registrar itself runs as SYSTEM,
# whose $env:USERPROFILE is C:\Windows\System32\config\systemprofile, and that was baked in: the
# service then drove a podman that could not find its machine. In order: WATCHDOG_USER (that name
# under the profiles folder); the caller's USERPROFILE unless it is a service account's, refused
# when the install root's parent is ANOTHER user's profile folder (below); the install root's parent
# when that is a profile folder (get.ps1 clones to %USERPROFILE%\ai-platform); else nothing, and
# the registrar refuses, naming WATCHDOG_USER. Returns @{ Profile; Why; Problem }.
function Resolve-WatchdogProfile {
  param([AllowEmptyString()][string]$EnvProfile, [Parameter(Mandatory)][string]$Root,
        [AllowEmptyString()][string]$WatchdogUser, [string]$ProfilesDir)
  if (-not $ProfilesDir) {
    $ProfilesDir = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList' `
                       -ErrorAction SilentlyContinue).ProfilesDirectory
    if (-not $ProfilesDir) { $ProfilesDir = Join-Path $env:SystemDrive 'Users' }
  }
  $isService = { param($p) (-not $p) -or $p -match '\\config\\systemprofile(\\|$)' -or $p -match '\\ServiceProfiles\\' }
  if ($WatchdogUser) { return @{ Profile = (Join-Path $ProfilesDir $WatchdogUser); Why = 'WATCHDOG_USER'; Problem = '' } }
  $parent = Split-Path ([IO.Path]::GetFullPath($Root)) -Parent
  $parentIsProfile = [bool]($parent -and ((Split-Path $parent -Parent) -ieq $ProfilesDir.TrimEnd('\')) -and -not (& $isService $parent))
  if (-not (& $isService $EnvProfile)) {
    # An elevated shell can be another account's (an administrator's credentials typed at the
    # elevation prompt), and this is then that administrator's USERPROFILE. Over a checkout in a
    # DIFFERENT user's profile folder there are two candidate users and nothing that says whose
    # podman machine runs the stack, and a wrong profile baked in fails only later, in the service's
    # log. So neither is taken: refused, naming both folders and what to pass. Not the checkout's
    # owner by preference, which would put a guess from a path over the one account the OS says
    # is running this, and would take a folder such as Public for a user (ops-e follow-up).
    $own = [IO.Path]::GetFullPath($EnvProfile).TrimEnd('\')
    if ($parentIsProfile -and $parent -ine $own) {
      return @{ Profile = ''; Why = ''
                Problem = ("USERPROFILE is '$EnvProfile', but the install root $Root is in another account's profile " +
                           "folder, $parent. An elevated shell can be another account's, so whose podman machine the " +
                           "watchdog should drive is not known. Pass -WatchdogUser $(Split-Path $parent -Leaf) (or set " +
                           "WATCHDOG_USER) for the checkout's owner, or name this account to keep its own, and run it again.") }
    }
    return @{ Profile = $EnvProfile; Why = 'USERPROFILE'; Problem = '' }
  }
  if ($parentIsProfile) {
    return @{ Profile = $parent; Why = "the install root's parent ($EnvProfile is a service account's profile)"; Problem = '' }
  }
  return @{ Profile = ''; Why = ''
            Problem = ("USERPROFILE is '$EnvProfile', a service account's profile (this runs as SYSTEM), and the install " +
                       "root $Root is not in a user's profile folder, so there is no user to give the watchdog. " +
                       'Set WATCHDOG_USER to the account whose podman machine it should drive, and run it again.') }
}

# --- NSSM, fetched once ----------------------------------------------------------------------
# Both installers download NSSM 2.24 when deploy\bin\nssm.exe is not there yet and run it as
# LocalSystem. They used to leave %TEMP%\nssm-2.24.zip and its extract folder behind for good, at
# fixed names a second run would trample; now the download is this run's own folder under the long
# form of TEMP, removed in a finally, and only nssm.exe is taken out of the zip (no extract folder).
# $NssmExeSha256 pins the nssm.exe a fetch may install (the zip's nssm-2.24/win64/nssm.exe, which is
# what Install-NssmExe hashes, not the zip): a mismatch is refused before the file is copied.
# nssm.cc publishes no checksum, so the pin is the SHA-256 of the deploy\bin\nssm.exe the owner's
# box runs its services with, hashed on 2026-10-14: FileVersion 2.24, 331,264 bytes, linker
# timestamp 2014-08-31 (the official 2.24 release date), and unsigned, as every NSSM 2.24 build is.
# A caller that passes -Sha256 '' gets the unpinned path, which prints the fetched file's SHA-256.
$NssmExeSha256 = 'f689ee9af94b00e9e3f0bb072b34caaf207f32dcb4f5782fc9ca351df9a06c97'
function Install-NssmExe {
  param([Parameter(Mandatory)][string]$Dest, [string]$Url = 'https://nssm.cc/release/nssm-2.24.zip',
        [AllowEmptyString()][string]$Sha256 = $NssmExeSha256)
  $work = Join-Path ([IO.Path]::GetFullPath($env:TEMP)) ('nssm-fetch-{0}-{1}' -f $PID, [guid]::NewGuid().ToString('N').Substring(0, 12))
  New-Item -ItemType Directory -Force -Path $work | Out-Null
  try {
    $zip = Join-Path $work 'nssm-2.24.zip'
    Invoke-WebRequest $Url -OutFile $zip -UseBasicParsing
    $exe = Join-Path $work 'nssm.exe'
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $z = [System.IO.Compression.ZipFile]::OpenRead($zip)
    try {
      $entry = $z.GetEntry('nssm-2.24/win64/nssm.exe')
      if (-not $entry) { throw "$Url holds no nssm-2.24/win64/nssm.exe" }
      [System.IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $exe, $true)
    }
    finally { $z.Dispose() }
    $got = Get-Sha256Hex -Path $exe
    if ($Sha256 -and $got -ne $Sha256.ToLowerInvariant()) {
      throw "nssm.exe from $Url has SHA-256 $got, not the pinned $Sha256; refusing to install it"
    }
    if (-not $Sha256) { Write-Host "nssm.exe SHA-256 $got (not verified: no pinned hash)" }
    Copy-Item -LiteralPath $exe -Destination $Dest -Force
    return $got
  }
  finally { Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue }
}

# --- the Python the native broker venv needs -----------------------------------------------------
# services/broker/pyproject.toml's requires-python (">=3.11" today), READ rather than restated, so the
# installer's doctor cannot pass a Python that pip then refuses. It accepted 3.10, and
# `pip install -e services\broker` failed on it after the doctor had said OK. 3.11 if the file is
# not there to read (or names no lower bound), which is the bound every core package declares.
function Get-RequiredPython {
  param([Parameter(Mandatory)][string]$Root)
  $f = Join-Path $Root 'services\broker\pyproject.toml'
  if (Test-Path -LiteralPath $f) {
    $t = [System.IO.File]::ReadAllText($f, [System.Text.Encoding]::UTF8)
    if ($t -match 'requires-python\s*=\s*"\s*>=\s*(\d+)\.(\d+)') { return [version]"$($Matches[1]).$($Matches[2])" }
  }
  return [version]'3.11'
}

# Whether `python --version` output names a Python at or above $Minimum (major.minor).
function Test-PythonVersionText {
  param([AllowEmptyString()][string]$Text, [Parameter(Mandatory)][version]$Minimum)
  if ($Text -notmatch 'Python\s+(\d+)\.(\d+)') { return $false }
  return ([version]"$($Matches[1]).$($Matches[2])" -ge $Minimum)
}

# --- what the native broker venv installs, and whether the broker imports with it ---------------
# The broker's dependencies are services\broker\pyproject.toml's, installed FROM it, the way the
# full stack's install-services.ps1 tells its operator to: packages\platform_core first, editable,
# because the broker declares it bare and it is not on PyPI (pip refuses the broker without it),
# then the broker itself, editable, which brings everything else its pyproject declares. The lean
# installer used to restate that list by hand, and the copy fell behind the pyproject: no
# python-multipart, without which FastAPI raises while the broker is imported (so NSSM restarts the
# service forever), and no anyio. Each entry is one pip run's arguments, after `python -m pip`.
function Get-BrokerPipInstalls {
  param([Parameter(Mandatory)][string]$Root)
  return @(
    , @('install', '--quiet', '-e', (Join-Path $Root 'packages\platform_core'))
    , @('install', '--quiet', '-e', (Join-Path $Root 'services\broker'))
  )
}

# The requirements services\broker\pyproject.toml's `dependencies` array declares, as written,
# read rather than restated (as Get-RequiredPython reads requires-python), so a dry run can say what
# the broker's install brings. Comments are skipped; nothing if the file is not there to read.
function Get-BrokerDeclaredDependencies {
  param([Parameter(Mandatory)][string]$Root)
  $f = Join-Path $Root 'services\broker\pyproject.toml'
  if (-not (Test-Path -LiteralPath $f)) { return @() }
  $t = [System.IO.File]::ReadAllText($f, [System.Text.Encoding]::UTF8)
  if ($t -notmatch '(?ms)^dependencies\s*=\s*\[(.*?)^\]') { return @() }
  $deps = @()
  foreach ($line in ($Matches[1] -split "`r?`n")) {
    $code = ($line -split '#', 2)[0]
    foreach ($m in [regex]::Matches($code, '"([^"]+)"')) { $deps += $m.Groups[1].Value }
  }
  return $deps
}

# Whether the broker imports with $Python the way its service runs it (`-m uvicorn app.main:app
# --app-dir <root>\services\broker`): app.main, and uvicorn, which nothing under app/ imports. The
# full stack's install-services.ps1 refuses to register a broker that fails this; the lean installer
# reinstalls from the pyproject when it fails, so a venv built from the short list the installer
# used to carry is repaired by a re-run rather than skipped because it exists. The root goes in as
# an argument, not inside the Python literal, so a quote in it cannot break it. EAP is relaxed for
# the probe only: on 5.1, `2>&1` under 'Stop' throws on the first stderr line (a traceback here).
function Test-BrokerImports {
  param([Parameter(Mandatory)][string]$Python, [Parameter(Mandatory)][string]$Root)
  if (-not (Test-Path -LiteralPath $Python)) { return $false }
  $eap = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  try {
    & $Python -c 'import sys; sys.path.insert(0, sys.argv[1]); import app.main, uvicorn' (Join-Path $Root 'services\broker') 2>&1 | Out-Null
    return ($LASTEXITCODE -eq 0)
  }
  finally { $ErrorActionPreference = $eap }
}

# --- SHA256SUMS.txt that GNU `sha256sum -c` reads on any machine -------------------------------
# One "<lowercase hex>  <path>" line per file, the path relative with FORWARD slashes, LF line ends,
# UTF-8 with NO BOM, sorted. The backups wrote theirs with Set-Content: CRLF everywhere and a BOM on
# 5.1, and GNU sha256sum then failed EVERY entry (the CR becomes part of each file name, the BOM part
# of the first hash; measured by the audit: exit 1, while the same lines with LF pass).
# backup-voice also wrote backslash paths, which sha256sum reads as an escaped name, and uppercase
# hex. RESTORE.md promises `sha256sum -c SHA256SUMS.txt` works anywhere; this is what makes it so.
# A file's SHA-256 as lowercase hex, through .NET rather than Get-FileHash. Windows PowerShell 5.1
# started from PowerShell 7 inherits 7's PSModulePath and then cannot load the Utility module that
# Get-FileHash lives in ("not recognized as the name of a cmdlet"): measured, it failed this
# writer's own tests when run-tests ran under pwsh.
function Get-Sha256Hex {
  param([Parameter(Mandatory)][string]$Path)
  $sha = [System.Security.Cryptography.SHA256]::Create()
  $fs = [System.IO.File]::OpenRead($Path)
  try { return (-join ($sha.ComputeHash($fs) | ForEach-Object { $_.ToString('x2') })) }
  finally { $fs.Dispose(); $sha.Dispose() }
}

function Write-Sha256Sums {
  param([Parameter(Mandatory)][string]$Dir, [string[]]$Exclude = @('SHA256SUMS.txt'))
  $root = (Resolve-Path -LiteralPath $Dir).Path.TrimEnd('\')
  $lines = @(Get-ChildItem -LiteralPath $root -Recurse -File | Where-Object { $_.Name -notin $Exclude } |
    Sort-Object FullName | ForEach-Object {
      '{0}  {1}' -f (Get-Sha256Hex -Path $_.FullName),
                    ($_.FullName.Substring($root.Length + 1) -replace '\\', '/')
    })
  $text = ($lines -join "`n") + "`n"
  [System.IO.File]::WriteAllText((Join-Path $root 'SHA256SUMS.txt'), $text, (New-Object System.Text.UTF8Encoding($false)))
  return $lines.Count
}

# The text of a freshly templated deploy/.env, plus every KEY=value line from the existing file
# whose key the new text does not define. Both installer paths rebuild .env from env.lean.example,
# and a box's own wiring is not in that template: PLATFORM_COMPOSE_OVERRIDE, BROKER_AUTH_TOKEN,
# CB_BLUEPRINT_CSV. Rewriting without carrying them over dropped the override silently, and the
# very same run then composed the stack without it. A key the template does define keeps the
# template's (freshly chosen) value; a key that is only commented out in the template counts as
# undefined, so the box's value survives. Pure, so lib-apply's closures can use the result.
function Add-EnvCarryOver {
  param([Parameter(Mandatory)][string]$NewText, [string]$ExistingPath)
  if (-not $ExistingPath -or -not (Test-Path -LiteralPath $ExistingPath)) { return $NewText }
  $defined = @{}
  foreach ($l in ($NewText -split "`r?`n")) {
    if ($l -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=') { $defined[$Matches[1]] = $true }
  }
  $carry = New-Object System.Collections.Generic.List[string]
  foreach ($l in (Get-Content -LiteralPath $ExistingPath -Encoding UTF8)) {
    if ($l -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=' -and -not $defined.ContainsKey($Matches[1])) {
      $carry.Add($l); $defined[$Matches[1]] = $true
    }
  }
  if ($carry.Count -eq 0) { return $NewText }
  $nl = if ($NewText -match "`r`n") { "`r`n" } else { "`n" }
  return ($NewText.TrimEnd() + $nl + $nl +
          '# --- carried over from the previous deploy\.env: keys the template does not define ---' +
          $nl + ($carry -join $nl) + $nl)
}

# Map the enabled-app list onto the compose profiles that carry those services.
#
# This list must track the `profiles:` keys actually declared in
# docker-compose.installer.yml — naming a profile that file doesn't declare is silently ignored by
# compose, so a typo here fails as "the rail just never came up". terminal-fun has no profile
# (it is always installed) and so is deliberately absent.
function Get-ComposeProfiles {
  param([string]$Apps)
  $p = @()
  # It must also track the chooser lists in install.ps1: a profile absent from those never reaches
  # PLATFORM_ENABLED_APPS, so there is nothing here to match and the service is skipped just as
  # silently. RC025 enforces the three-way agreement.
  foreach ($a in @('recipe-book', 'co-worker', 'smb-partner-enablement', 'gemini-cx', 'meeting-atlas', 'ai-playground', 'openmaic')) {
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
  #
  # -NoLog forwards without logging. Needed because `compose config` renders the FULL resolved
  # file, and with --env-file that means every secret interpolated (API keys, the broker token,
  # the admin password) would land in deploy/logs/smoke-test.log. A leak like that is invisible
  # at the call site because the logging lives HERE, in the shared helper, not there.
  param([Parameter(ValueFromPipeline = $true)]$InputObject, [switch]$NoLog)
  process {
    $line = [string]$InputObject
    if (-not $NoLog) {
      try { Add-Content -Path $LogFile -Value $line -Encoding utf8 -ErrorAction Stop } catch {}
    }
    $line
  }
}

# How compose is run for a runtime: @{ Exe; Prefix; DockerHost }, run as `& $Exe @Prefix <args>`.
#   podman  - the standalone docker-compose.exe over podman's Docker-compatible API pipe. No
#             DOCKER_HOST is needed when Docker isn't installed (podman claims
#             //./pipe/docker_engine), but callers set DockerHost (when DOCKER_HOST is unset) so the
#             target is never ambiguous.
#   wsl     - `docker compose` inside WSL.
#   desktop - `docker compose`.
# The one place that says so: Invoke-Compose runs on it, and so does rebuild-stale.ps1, which
# needs compose's stderr apart from its stdout and so cannot use Invoke-Compose for its render.
function Get-ComposeCommand {
  param([string]$Mode = $RuntimeMode)
  switch ($Mode) {
    'podman' { return @{ Exe = 'docker-compose'; Prefix = @(); DockerHost = "npipe:////./pipe/$PodmanMachine" } }
    'wsl'    { return @{ Exe = 'wsl.exe'; Prefix = @('docker', 'compose'); DockerHost = '' } }
    default  { return @{ Exe = 'docker'; Prefix = @('compose'); DockerHost = '' } }
  }
}

function Invoke-Compose {
  # -NoLog for `config` (a bare one, not --services/--volumes): that render carries every
  # interpolated secret, and the log is world-readable on disk and swept up by support bundles.
  param([string[]]$Arguments, [string]$Mode = $RuntimeMode, [switch]$NoLog)
  # Judged by $LASTEXITCODE, never by exceptions. Under a caller's EAP=Stop (apply.ps1) Windows
  # PowerShell 5.1 turns compose's first progress line on stderr into a terminating
  # NativeCommandError, so `up` would die mid-output. Function-scoped: the caller keeps its own.
  $ErrorActionPreference = 'Continue'
  $c = Get-ComposeCommand -Mode $Mode
  if ($c.DockerHost -and -not $env:DOCKER_HOST) { $env:DOCKER_HOST = $c.DockerHost }
  $argv = @($c.Prefix)
  if ($Arguments) { $argv += $Arguments }
  & $c.Exe @argv 2>&1 | Write-NativeLine -NoLog:$NoLog
}

# `compose <args> config --format json` with stdout kept apart from stderr: @{ Code; Json; Err }.
# Every render whose output is PARSED goes through here, never through Invoke-Compose, which merges
# stderr into its output for the log: one compose warning on stderr (a ~/.docker/config.json that
# is a directory, an unset variable) became the first line of the "JSON", ConvertFrom-Json threw,
# and sync-disabled-rails.ps1 exited 1 having done nothing (reproduced on the podman box,
# 2026-10-14); platform-startup parked nothing. rebuild-stale.ps1 and backup-platform.ps1 split
# their renders the same way. Never logged: with --env-file the render carries every secret in
# deploy\.env. Judged by Code; Err is compose's stderr, warnings included, for the caller to quote.
function Invoke-ComposeConfigJson {
  param([string[]]$Arguments, [string]$Mode = $RuntimeMode)
  $ErrorActionPreference = 'Continue'      # Invoke-Compose's reason: judged by the exit code
  $c = Get-ComposeCommand -Mode $Mode
  if ($c.DockerHost -and -not $env:DOCKER_HOST) { $env:DOCKER_HOST = $c.DockerHost }
  $argv = @($c.Prefix)
  if ($Arguments) { $argv += $Arguments }
  $argv += @('config', '--format', 'json')
  $all = @(& $c.Exe @argv 2>&1)
  $code = $LASTEXITCODE
  return @{
    Code = $code
    Json = (@($all | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] } | ForEach-Object { [string]$_ }) -join "`n")
    Err  = @($all | Where-Object { $_ -is [System.Management.Automation.ErrorRecord] } | ForEach-Object { [string]$_ })
  }
}

# Which stack the platform was brought up from: 'lean' (deploy\installer\docker-compose.installer.yml,
# the installer's) or 'full' (deploy\docker-compose.yml), read from the label compose itself puts
# on every container it creates, com.docker.compose.project.config_files (its -f list, comma-joined;
# the first is the base file, the rest overrides). Returns @{ Stack; Reason; Proven }, Proven $true
# only when the label names one of the two stacks' files.
#
# A box running one stack must not be driven with the other: the full file names services, volumes
# and build contexts the lean box never had. When the label does not decide (no container yet or
# after `compose down`, the runtime cannot be asked, no label, a file that is neither stack's),
# podman's answer is 'lean': podman runs only the lean stack (the full stack is Docker Desktop's),
# and 'full' there had rebuild-stale build the whole full stack into a lean box's podman machine.
# wsl's is 'lean' too, for the same reason: Docker inside WSL is the lean installer's third mode,
# and nothing sets the full stack up there (delta audit 2026-10-15). Docker Desktop still answers
# 'full', what every caller did before, so the full stack's Docker path is unchanged.
# `{{json .Config.Labels}}` rather than an `index` template: that one needs a double quote inside
# a native argument, which Windows PowerShell 5.1 does not escape.
function Resolve-PlatformStack {
  param([string]$Mode = $RuntimeMode, [string]$Container = 'platform-gateway-1')
  $fallback = if ($Mode -eq 'podman' -or $Mode -eq 'wsl') { 'lean' } else { 'full' }
  $so = switch ($Mode) {
    'podman' { 'so the lean stack, the only one podman runs' }
    'wsl'    { 'so the lean stack, the only one a WSL engine runs' }
    default  { 'so the full stack' }
  }
  # A CLI that is not there at all is "cannot be asked" too, never a throw into the caller (a
  # terminating CommandNotFoundException ended platform-startup through its trap).
  try {
    $out = @(Invoke-VolumeCli -Mode $Mode -CliArgs @('inspect', '--format', '{{json .Config.Labels}}', $Container) |
             ForEach-Object { [string]$_ })
    $code = $LASTEXITCODE
  }
  catch {
    return @{ Stack = $fallback; Reason = "no $Container to read ($($_.Exception.Message)), $so"; Proven = $false }
  }
  if ($code -ne 0) {
    return @{ Stack = $fallback; Reason = "no $Container to read (inspect exit $code), $so"; Proven = $false }
  }
  $files = ''
  try {
    $labels = ($out -join "`n") | ConvertFrom-Json
    if ($labels -and $labels.PSObject.Properties.Name -contains 'com.docker.compose.project.config_files') {
      $files = [string]$labels.'com.docker.compose.project.config_files'
    }
  } catch {}
  $first = @($files -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })[0]
  if (-not $first) {
    return @{ Stack = $fallback; Reason = "$Container carries no compose config_files label, $so"; Proven = $false }
  }
  $leaf = ($first -split '[\\/]')[-1]
  if ($leaf -eq 'docker-compose.installer.yml') { return @{ Stack = 'lean'; Reason = "$Container was created from $first"; Proven = $true } }
  if ($leaf -eq 'docker-compose.yml') { return @{ Stack = 'full'; Reason = "$Container was created from $first"; Proven = $true } }
  return @{ Stack = $fallback; Reason = "$Container was created from $first, which is neither stack's file, $so"; Proven = $false }
}

# podman, docker and wsl-docker all take the same `volume` subcommands; only the entry point differs.
function Invoke-VolumeCli {
  param([string[]]$CliArgs, [string]$Mode = $RuntimeMode)
  $ErrorActionPreference = 'Continue'   # same reason as Invoke-Compose: judged by exit code
  switch ($Mode) {
    'podman' { & podman @CliArgs 2>&1 }
    'wsl'    { & wsl.exe @(@('docker') + $CliArgs) 2>&1 }
    default  { & docker @CliArgs 2>&1 }
  }
}

# --- rails THIS box does not run: PLATFORM_DISABLED_APPS ------------------------
# The gateway stops routing a rail named in PLATFORM_DISABLED_APPS, but its container kept running:
# every service is `restart: always`, so the engine brings it back at every boot (Docker Desktop
# does it by itself; podman-restart does it in the machine), and rebuild-stale's derived plan
# rebuilt and STARTED it. "Disabled on this box" now means "not running on this box" for the ops
# scripts too, from the same one declaration.
#
# Why not compose profiles instead: the full stack declares none, its gateway depends_on every
# rail service (a profiled-out dependency is a compose error), profiles do nothing to a container
# that already exists with `restart: always`, and a per-box COMPOSE_PROFILES list would be a
# SECOND declaration of what runs here, beside PLATFORM_DISABLED_APPS, free to disagree with it.

# The value of an app-list key, read as tools/rail_smoke.py reads it (declared() then parse_apps(),
# themselves a mirror of the gateway's GatewaySettings parser): the environment if non-blank, else
# the value compose gives the gateway from deploy\.env (Read-DotEnvFile: the LAST assignment, a
# trailing "  # comment" dropped, quotes removed), trimmed; then a JSON array, or a comma/newline-
# separated list. Blank is no list. A value that is not valid JSON after a `[` throws, as the
# gateway refuses to start on it. This used to take the FIRST line and keep a trailing comment, so
# `PLATFORM_DISABLED_APPS=servicenow  # note` parked nothing while the gateway disabled servicenow.
# tools/tests/test_disabled_rails.py runs this and rail_smoke over the same inputs.
function Get-DeclaredAppList {
  param([Parameter(Mandatory)][string]$Key, [string]$EnvFile)
  $raw = [string][Environment]::GetEnvironmentVariable($Key)
  $raw = $raw.Trim()
  if (-not $raw -and $EnvFile) { $raw = ([string](Get-DotEnvValue -Path $EnvFile -Key $Key)).Trim() }
  if (-not $raw) { return @() }
  # Assigned first: Windows PowerShell 5.1's ConvertFrom-Json emits a JSON array as ONE pipeline
  # object, which a straight pipe joined into a single space-separated name.
  if ($raw.StartsWith('[')) { $arr = $raw | ConvertFrom-Json; return @(@($arr) | ForEach-Object { [string]$_ }) }
  return @($raw -replace "`n", ',' -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
}

# The services in a `compose config --format json` render: Name, Rail (its `ai-platform.rail`
# label: the rail a service exists only for, e.g. openmaic-app) and Restart (its declared policy).
function Get-ComposeServices {
  param([Parameter(Mandatory)][string]$ConfigJson)
  $cfg = $ConfigJson | ConvertFrom-Json
  foreach ($p in $cfg.services.PSObject.Properties) {
    $v = $p.Value
    $rail = ''
    if ($v.PSObject.Properties.Name -contains 'labels' -and $v.labels -and
        $v.labels.PSObject.Properties.Name -contains 'ai-platform.rail') { $rail = [string]$v.labels.'ai-platform.rail' }
    $restart = if ($v.PSObject.Properties.Name -contains 'restart') { [string]$v.restart } else { '' }
    [pscustomobject]@{ Name = $p.Name; Rail = $rail; Restart = $restart }
  }
}

# The compose services of the disabled apps: an app's own service (its rail.json compose_service,
# else its id, the rule rail_conformance and publish use; edu-suite runs as `dashboard`), a tile a
# rail also serves (iep) as the service of that name, and every service labelled
# ai-platform.rail=<app>. Returns @{ Services = ordered service -> app; Unknown = app ids that
# matched nothing, which therefore stop nothing and must be said }.
function Resolve-DisabledServices {
  param([string[]]$Disabled, [object[]]$Services, [string]$RailsDir)
  $names = @($Services | ForEach-Object { $_.Name })
  $own = @{}
  if ($RailsDir -and (Test-Path -LiteralPath $RailsDir)) {
    foreach ($dir in Get-ChildItem -LiteralPath $RailsDir -Directory) {
      $m = Join-Path $dir.FullName 'rail.json'
      if (-not (Test-Path -LiteralPath $m)) { continue }
      $d = Get-Content -Raw -Encoding UTF8 -LiteralPath $m | ConvertFrom-Json
      $props = @($d.PSObject.Properties.Name)
      if (-not ($props -contains 'id')) { continue }
      $own[[string]$d.id] = if ($props -contains 'compose_service' -and $d.compose_service) { [string]$d.compose_service } else { [string]$d.id }
      if ($props -contains 'also_serves') { foreach ($t in @($d.also_serves)) { $own[[string]$t] = [string]$t } }
    }
  }
  $out = [ordered]@{}
  $unknown = @()
  foreach ($app in @($Disabled)) {
    $svc = if ($own.ContainsKey($app)) { $own[$app] } else { $app }
    $hit = $false
    if ($names -contains $svc) { $out[$svc] = $app; $hit = $true }
    foreach ($s in @($Services)) { if ($s.Rail -and $s.Rail -eq $app) { $out[$s.Name] = $app; $hit = $true } }
    if (-not $hit) { $unknown += $app }
  }
  return @{ Services = $out; Unknown = $unknown }
}

# Make the runtime agree with PLATFORM_DISABLED_APPS, idempotently, and say what it did.
#   - a disabled app's container: restart policy set to `no` (so no engine restart brings it
#     back, at boot or ever), then stopped if running;
#   - a container this step had parked (policy `no`) whose service is NOT disabled any more, and
#     whose compose service declares a policy: that policy restored, and the container started.
#     That is the way back: take the rail out of the list and run the step again.
# -DryRun changes nothing and prints what it would do. Returns @{ Lines; Failed }.
function Sync-DisabledRails {
  param([string[]]$Disabled, [object[]]$Services, [string]$RailsDir, [string]$Mode = $RuntimeMode,
        [switch]$DryRun, [string]$Project)
  $ErrorActionPreference = 'Continue'      # judged by exit codes, like the other CLI helpers
  if (-not $Project) { $Project = if ($env:COMPOSE_PROJECT_NAME) { $env:COMPOSE_PROJECT_NAME } else { 'platform' } }
  $lines = New-Object System.Collections.Generic.List[string]
  $failed = 0
  $r = Resolve-DisabledServices -Disabled $Disabled -Services $Services -RailsDir $RailsDir
  foreach ($u in $r.Unknown) { $lines.Add("PLATFORM_DISABLED_APPS names '$u', which no rail or service here serves; it stops nothing") }

  $ps = @(Invoke-VolumeCli -Mode $Mode -CliArgs @('ps', '-a', '--format', '{{.Names}} {{.State}}') | ForEach-Object { [string]$_ })
  if ($LASTEXITCODE -ne 0) {
    $lines.Add("could not list containers (exit $LASTEXITCODE): $(($ps | Select-Object -First 1))")
    return @{ Lines = $lines.ToArray(); Failed = 1 }
  }
  $byService = @{}
  foreach ($row in $ps) {
    $f = -split $row
    if ($f.Count -ge 1 -and $f[0] -match "^$([regex]::Escape($Project))-(.+)-\d+$") {
      $svc = $Matches[1]
      if (-not $byService.ContainsKey($svc)) { $byService[$svc] = @() }
      $byService[$svc] += [pscustomobject]@{ Name = $f[0]; State = $(if ($f.Count -ge 2) { $f[1] } else { '' }) }
    }
  }
  $policy = @{}
  $all = @($byService.Values | ForEach-Object { $_ } | ForEach-Object { $_.Name })
  if ($all.Count -gt 0) {
    foreach ($row in @(Invoke-VolumeCli -Mode $Mode -CliArgs (@('inspect', '--format', '{{.Name}} {{.HostConfig.RestartPolicy.Name}}') + $all))) {
      # An EMPTY policy is "no": podman can report a container with no restart policy as "" where
      # docker says "no". Read as missing, a parked container looked unparked, so every run
      # re-issued `update --restart=no`, and a re-enabled rail was never brought back.
      if ([string]$row -match '^\s*(\S+)\s*(\S*)\s*$') {
        $policy[$Matches[1].TrimStart('/')] = $(if ($Matches[2]) { $Matches[2] } else { 'no' })
      }
    }
  }
  $act = {
    param([string[]]$CliArgs, [string]$Said)
    if ($DryRun) { $lines.Add("would $Said"); return }
    $o = @(Invoke-VolumeCli -Mode $Mode -CliArgs $CliArgs | ForEach-Object { [string]$_ })
    if ($LASTEXITCODE -eq 0) { $lines.Add($Said) }
    else { $lines.Add("FAILED to $Said (exit $LASTEXITCODE): $(($o | Select-Object -First 1))"); $count.Failed++ }
  }
  $count = @{ Failed = 0 }     # a hashtable, so the scriptblock above can count into it

  foreach ($svc in @($r.Services.Keys)) {
    $app = $r.Services[$svc]
    if (-not $byService.ContainsKey($svc)) { $lines.Add("$svc ($app is disabled on this box): no container, nothing to do"); continue }
    foreach ($c in $byService[$svc]) {
      $did = $false
      if ($policy[$c.Name] -ne 'no') {
        & $act @('update', '--restart=no', $c.Name) "set restart=no on $($c.Name) ($app is disabled on this box, so no engine restart brings it back)"
        $did = $true
      }
      if ($c.State -eq 'running') { & $act @('stop', $c.Name) "stop $($c.Name)"; $did = $true }
      if (-not $did) { $lines.Add("$($c.Name) already down and kept down ($app is disabled on this box)") }
    }
  }
  foreach ($s in @($Services)) {
    if ($r.Services.Contains($s.Name) -or -not $s.Restart -or $s.Restart -eq 'no' -or -not $byService.ContainsKey($s.Name)) { continue }
    foreach ($c in $byService[$s.Name]) {
      if ($policy[$c.Name] -ne 'no') { continue }
      & $act @('update', "--restart=$($s.Restart)", $c.Name) "restore restart=$($s.Restart) on $($c.Name) (its rail is not disabled any more)"
      if ($c.State -ne 'running') { & $act @('start', $c.Name) "start $($c.Name)" }
    }
  }
  return @{ Lines = $lines.ToArray(); Failed = $count.Failed }
}

# (No volume pre-create. Initialize-ComposeVolumes stood here for volumes declared `external: true`,
# which neither compose file has any more: compose creates its own named volumes, labelled. The
# pre-create made them WITHOUT compose's labels, so every following `up` warned "volume already
# exists but was not created by Docker Compose", the warning Write-NativeLine exists to flatten.)

# The compose files to pass as -f: the lean file, then this box's own override when the env file
# names one in PLATFORM_COMPOSE_OVERRIDE.
#
# The override is how one machine adds what the tracked file must not know about (a bind mount of a
# dist built outside any image, a rail that is withheld from the public mirror) without editing a
# tracked file, so the deployment checkout stays clean and `git pull` stays a fast-forward. The path
# is read from deploy\.env and never from the user profile: startup can run as SYSTEM, whose profile
# is systemprofile, and an override that only resolves in the user's session silently vanishes
# after a reboot. Relative paths INSIDE the override resolve against deploy\installer\ (compose's
# project directory is the first file's), so write absolute ones there.
#
# Pure, with no logging, so lib-apply's closures can call it. A value naming a missing file comes
# back in Missing rather than failing: the front door coming up without the extras beats it not
# coming up at all, but the caller must say so.
function Get-ComposeFiles {
  param([Parameter(Mandatory)][string]$EnvFile, [Parameter(Mandatory)][string]$Compose)
  # Read-DotEnvFile: UTF-8 on both engines (a non-ASCII path once came back garbled under 5.1 and
  # was skipped as Missing), and compose's own rules for quotes, a trailing comment and the last
  # assignment winning, so the file used here is the one compose would be told about.
  $override = [string](Get-DotEnvValue -Path $EnvFile -Key 'PLATFORM_COMPOSE_OVERRIDE')
  # A value holding a character no path may hold names no file: Missing, said by the caller, not an
  # exception from IsPathRooted under 5.1. A DOUBLE-quoted C:\dir\new.yml is one, read the way
  # compose reads it (\n is a newline there); single quotes or none keep a Windows path as written.
  if ($override.IndexOfAny([IO.Path]::GetInvalidPathChars()) -ge 0) { return @{ Files = @($Compose); Missing = $override } }
  if ($override -and -not [IO.Path]::IsPathRooted($override)) {
    $override = Join-Path (Split-Path (Split-Path $EnvFile -Parent) -Parent) $override
  }
  if (-not $override) { return @{ Files = @($Compose); Missing = '' } }
  if (Test-Path -LiteralPath $override -PathType Leaf) { return @{ Files = @($Compose, $override); Missing = '' } }
  return @{ Files = @($Compose); Missing = $override }
}

# Absolute paths as the selected runtime's compose needs to see them. Pass FileArgs, not
# '-f', Compose: FileArgs carries the box-local override (Get-ComposeFiles), and a caller that
# hand-builds a single -f brings the stack up without it.
function Get-ComposePaths {
  param([string]$Mode = $RuntimeMode)
  $envFile = Join-Path $Root 'deploy\.env'
  $compose = Join-Path $Installer 'docker-compose.installer.yml'
  $r = Get-ComposeFiles -EnvFile $envFile -Compose $compose
  if ($r.Missing) {
    Write-Log "WARNING: PLATFORM_COMPOSE_OVERRIDE names '$($r.Missing)', which does not exist; composing without it."
  }
  $files = @($r.Files)
  if ($Mode -eq 'wsl') {
    $envFile = ConvertTo-WslPath $envFile
    $compose = ConvertTo-WslPath $compose
    $files = @($files | ForEach-Object { ConvertTo-WslPath $_ })
  }
  $fileArgs = @(); foreach ($f in $files) { $fileArgs += @('-f', $f) }
  return @{ Env = $envFile; Compose = $compose; Files = $files; FileArgs = $fileArgs }
}

# --- a ~/.docker/config.json that is a DIRECTORY ------------------------------------------------
# Measured on a podman box whose %USERPROFILE%\.docker\config.json was a directory: docker-compose
# downgrades that to a warning, but `podman run`, `podman pull` and `podman build` read the file for
# registry auth and fail before doing anything (reading JSON file ...\.docker\config.json: ...
# Incorrect function, exit 125). smoke-test.ps1 -Stage all reported 26 FAILs there, every data check
# that runs a container; with DOCKER_CONFIG pointed at a directory holding a `{}` config.json it
# passed 72/0. The stray directory is the user's, so it is named and never touched: this process
# and its children read an empty config from a directory of ours instead. Nothing happens when
# DOCKER_CONFIG is already set or the file is a file. Returns $null, or what it did (Config, Stray,
# Warning) after logging the warning once; a second call finds DOCKER_CONFIG set and says nothing.
function Repair-DockerConfigEnv {
  param([string]$UserProfile = $env:USERPROFILE, [string]$Base = $TempBase)
  if ($env:DOCKER_CONFIG -or -not $UserProfile) { return $null }
  $stray = Join-Path $UserProfile '.docker\config.json'
  if (-not (Test-Path -LiteralPath $stray -PathType Container)) { return $null }
  if (-not $Base) { $Base = $env:TEMP }
  $dir = Join-Path $Base 'platform-docker-config'
  $cfg = Join-Path $dir 'config.json'
  if (-not (Test-Path -LiteralPath $cfg -PathType Leaf)) {
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    [System.IO.File]::WriteAllText($cfg, '{}', (New-Object System.Text.UTF8Encoding($false)))
  }
  $env:DOCKER_CONFIG = $dir
  $warning = "$stray is a DIRECTORY, so podman run/pull/build cannot read their registry config " +
             "(exit 125). Using an empty config ($cfg) for this run; delete that directory to fix it."
  Write-Log "WARNING: $warning"
  return [pscustomobject]@{ Config = $dir; Stray = $stray; Warning = $warning }
}

