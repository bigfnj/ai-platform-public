# The installer preflight engine: detect every prerequisite, classify each result, and know how
# to remediate the ones that are safely ours to fix.
#
# ONE DETECTOR, MANY CALLERS. This file is pure detection + remediation logic that returns
# STRUCTURED objects and renders nothing by itself. A TUI prints the rows, a GUI draws buttons,
# the broker tray calls the same functions, and the post-install smoke test reuses the same
# checks so preflight and verify can never disagree about what "Ollama is up" means. That shared
# code path is the whole point; a second detector that drifts from this one is the bug this
# design exists to prevent.
#
# THREE LANES, because "what if it's missing" is not one answer (see the flow notes in
# docs/installer-design.md):
#   auto    small, deterministic, and OURS to install: Ollama, the podman machine, the models,
#           the conditional venvs. These carry a real Fix.
#   guided  big, licensed, or reboot-prone: Docker Desktop, the GPU driver. We detect and hand
#           over a command or link, then re-check. Never silently triggered.
#   info    shapes later choices but never blocks: existing models, the VRAM tier, CPU-only.
#
# Every result is serialisable (Status/Lane/Detail/FixKind, no scriptblocks on the object), so
# the whole board round-trips through -Json for a non-PowerShell front end. The Fix CODE lives in
# $PreflightFixes, keyed by id, and Invoke-PreflightFix runs it then RE-CHECKS that one row --
# detect, do, re-detect -- because a button that installs Ollama and then trusts it is how you
# ship "it said it worked and nothing runs".

Set-StrictMode -Version Latest

# lib-runtime carries the podman machine + control-plane helpers this engine reuses rather than
# reinventing (Get-PodmanMachineState, Test-ControlPlane, Initialize-PodmanMachine, ...).
$__here = Split-Path -Parent $MyInvocation.MyCommand.Path
$__libRuntime = Join-Path $__here 'lib-runtime.ps1'
if (Test-Path $__libRuntime) { . $__libRuntime }

# Below this many free GB on the install drive, warn: the bundled image plus a couple of models
# is comfortably several GB, and a half-pulled model on a full disk is the worst failure mode.
$PreflightMinFreeGb = 25


function New-PreflightResult {
    param(
        [Parameter(Mandatory)][string]$Id,
        [Parameter(Mandatory)][string]$Label,
        [Parameter(Mandatory)][ValidateSet('ok', 'warn', 'missing', 'info')][string]$Status,
        [string]$Detail = '',
        [ValidateSet('auto', 'guided', 'info')][string]$Lane = 'info',
        [bool]$Blocking = $false,
        [string]$FixLabel = '',
        [ValidateSet('', 'auto', 'link', 'command')][string]$FixKind = '',
        [string]$FixTarget = '',
        [bool]$NeedsAdmin = $false,
        [hashtable]$Data = @{}
    )
    [PSCustomObject]@{
        Id = $Id; Label = $Label; Status = $Status; Detail = $Detail
        Lane = $Lane; Blocking = $Blocking
        FixLabel = $FixLabel; FixKind = $FixKind; FixTarget = $FixTarget
        NeedsAdmin = $NeedsAdmin; Data = $Data
    }
}


function Test-Elevated {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}


# --- individual checks ------------------------------------------------------------------------
# Each returns exactly one result object and MUTATES NOTHING. Detection only.

function Test-RuntimeCheck {
    # The one hard blocker: no container runtime, no platform. Either engine satisfies it.
    $podman = Get-Command podman -ErrorAction SilentlyContinue
    $docker = Get-Command docker -ErrorAction SilentlyContinue

    if ($podman) {
        $state = if (Get-Command Get-PodmanMachineState -ErrorAction SilentlyContinue) {
            Get-PodmanMachineState
        } else { 'unknown' }
        if ($state -eq 'running') {
            return New-PreflightResult -Id 'runtime' -Label 'Container runtime' -Status 'ok' `
                -Detail "podman, machine running" -Data @{ mode = 'podman' }
        }
        # Installed but the machine is down or absent: ours to start, so it is an auto fix.
        return New-PreflightResult -Id 'runtime' -Label 'Container runtime' -Status 'warn' `
            -Detail "podman present, machine '$state'" -Lane 'auto' -Blocking $true `
            -FixLabel 'Start the podman machine' -FixKind 'auto' -NeedsAdmin $true `
            -Data @{ mode = 'podman' }
    }

    if ($docker) {
        $up = $false
        try { & docker info *>$null; $up = ($LASTEXITCODE -eq 0) } catch { $up = $false }
        if ($up) {
            return New-PreflightResult -Id 'runtime' -Label 'Container runtime' -Status 'ok' `
                -Detail "Docker Desktop, engine up" -Data @{ mode = 'desktop' }
        }
        return New-PreflightResult -Id 'runtime' -Label 'Container runtime' -Status 'warn' `
            -Detail "docker present but the engine is not responding (is Docker Desktop started?)" `
            -Lane 'guided' -Blocking $true -FixLabel 'Start Docker Desktop' -FixKind 'command' `
            -FixTarget 'Start Docker Desktop and re-check' -Data @{ mode = 'desktop' }
    }

    # Neither. Offer podman as the auto path; name Docker Desktop as the manual alternative.
    return New-PreflightResult -Id 'runtime' -Label 'Container runtime' -Status 'missing' `
        -Detail 'no docker or podman. Podman can be installed here; Docker Desktop is a manual, licensed install.' `
        -Lane 'auto' -Blocking $true -FixLabel 'Install Podman' -FixKind 'auto' -NeedsAdmin $true `
        -FixTarget 'https://www.docker.com/products/docker-desktop/' -Data @{ mode = 'none' }
}


function Test-OllamaCheck {
    $cli = Get-Command ollama -ErrorAction SilentlyContinue
    $version = $null
    try { $version = (Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/version' -TimeoutSec 5).version }
    catch { $version = $null }

    if ($cli -and $version) {
        return New-PreflightResult -Id 'ollama' -Label 'Ollama runtime' -Status 'ok' `
            -Detail "reachable, v$version"
    }
    if ($cli -and -not $version) {
        # Installed but not serving: the NSSM service or `ollama serve` is not up. Ours to start.
        return New-PreflightResult -Id 'ollama' -Label 'Ollama runtime' -Status 'warn' `
            -Detail 'installed but not responding on :11434 (service not started)' -Lane 'auto' `
            -Blocking $true -FixLabel 'Start Ollama' -FixKind 'auto'
    }
    return New-PreflightResult -Id 'ollama' -Label 'Ollama runtime' -Status 'missing' `
        -Detail 'not installed. Small, silent install.' -Lane 'auto' -Blocking $true `
        -FixLabel 'Install Ollama' -FixKind 'auto'
}


function Test-GpuCheck {
    # Detection only. A driver is never ours to install; this shapes the model recommendations.
    $smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
    if (-not $smi) {
        return New-PreflightResult -Id 'gpu' -Label 'GPU' -Status 'warn' `
            -Detail 'no NVIDIA GPU detected. CPU-only: embeddings work, chat is slow, image and tts are off.' `
            -Lane 'info' -Data @{ vram_mib = 0; cpu_only = $true }
    }
    $lines = @(& nvidia-smi --query-gpu=memory.total,name --format=csv,noheader,nounits 2>$null)
    $maxVram = 0; $names = @()
    foreach ($l in $lines) {
        $parts = $l -split ',', 2
        if ($parts.Count -ge 1) {
            $mib = 0; [void][int]::TryParse(($parts[0].Trim()), [ref]$mib)
            if ($mib -gt $maxVram) { $maxVram = $mib }
        }
        if ($parts.Count -ge 2) { $names += $parts[1].Trim() }
    }
    $gb = [math]::Round($maxVram / 1024, 1)
    # Size against the LARGEST SINGLE card, not the sum: the broker runs one heavy model at a
    # time and evicts before the next, so what matters is the biggest model fitting alone.
    return New-PreflightResult -Id 'gpu' -Label 'GPU' -Status 'ok' `
        -Detail "$($names -join ', ') ($gb GB usable for one resident model)" -Lane 'info' `
        -Data @{ vram_mib = $maxVram; cpu_only = $false; gpus = $names.Count }
}


function Test-ModelsCheck {
    # Pure inventory. Anything already pulled is free and should pre-select in the model step.
    $tags = $null
    try { $tags = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 5 } catch { $tags = $null }
    if (-not $tags) {
        return New-PreflightResult -Id 'models' -Label 'Installed models' -Status 'info' `
            -Detail 'none visible (Ollama not reachable yet)' -Lane 'info' -Data @{ models = @() }
    }
    $names = @($tags.models | ForEach-Object { $_.name })
    $detail = if ($names.Count) { "$($names.Count) already installed: $($names -join ', ')" }
              else { 'none installed yet' }
    New-PreflightResult -Id 'models' -Label 'Installed models' -Status 'info' -Detail $detail `
        -Lane 'info' -Data @{ models = $names }
}


function Test-DiskCheck {
    param([string]$InstallRoot = $env:SystemDrive)
    $root = if ($InstallRoot) { $InstallRoot } else { 'C:' }
    $free = -1.0
    try {
        $di = New-Object System.IO.DriveInfo ([System.IO.Path]::GetPathRoot((Resolve-Path $root).Path))
        $free = [math]::Round($di.AvailableFreeSpace / 1GB, 1)
    } catch { $free = -1.0 }
    if ($free -lt 0) {
        return New-PreflightResult -Id 'disk' -Label 'Disk space' -Status 'warn' `
            -Detail "could not read free space on $root" -Lane 'info'
    }
    $status = if ($free -ge $PreflightMinFreeGb) { 'ok' } else { 'warn' }
    New-PreflightResult -Id 'disk' -Label 'Disk space' -Status $status `
        -Detail "$free GB free on $root (image + models want $PreflightMinFreeGb GB+)" -Lane 'info' `
        -Data @{ free_gb = $free }
}


# --- the detector ---------------------------------------------------------------------------

function Get-Preflight {
    param([string]$InstallRoot = $env:SystemDrive)
    @(
        Test-RuntimeCheck
        Test-OllamaCheck
        Test-GpuCheck
        Test-ModelsCheck
        (Test-DiskCheck -InstallRoot $InstallRoot)
    )
}


# --- remediation ----------------------------------------------------------------------------
# The Fix CODE, keyed by id. Kept off the result objects so the board stays serialisable. Each
# returns $true on a clean run; Invoke-PreflightFix re-checks regardless, because the truth is
# the re-check, not the exit code.

$PreflightFixes = @{
    'ollama' = {
        # Per-user, silent. winget first (present on modern Windows); the caller falls back to
        # the official installer link if winget is absent.
        & winget install --id Ollama.Ollama --silent --accept-package-agreements `
            --accept-source-agreements 2>&1 | Out-Host
        Start-Sleep -Seconds 3
        ($LASTEXITCODE -eq 0)
    }
    'runtime' = {
        $r = Test-RuntimeCheck
        if ($r.Data.mode -eq 'podman') {
            # Installed already: just bring the machine up (Hyper-V, needs admin).
            if (Get-Command Initialize-PodmanMachine -ErrorAction SilentlyContinue) {
                Initialize-PodmanMachine | Out-Host; return $true
            }
            & podman machine start 2>&1 | Out-Host; return ($LASTEXITCODE -eq 0)
        }
        # Not installed: podman is the auto path (Docker Desktop stays manual, see FixTarget).
        & winget install --id RedHat.Podman --silent --accept-package-agreements `
            --accept-source-agreements 2>&1 | Out-Host
        if ($LASTEXITCODE -ne 0) { return $false }
        if (Get-Command Initialize-PodmanMachine -ErrorAction SilentlyContinue) {
            Initialize-PodmanMachine | Out-Host
        } else { & podman machine init 2>&1 | Out-Host; & podman machine start 2>&1 | Out-Host }
        $true
    }
}


function Invoke-PreflightFix {
    # Run the remediation for one check, then RE-CHECK that check and return the fresh result.
    # -DryRun reports what would run and re-checks without changing anything, so the loop can be
    # exercised without mutating the box.
    param(
        [Parameter(Mandatory)][string]$Id,
        [switch]$DryRun
    )
    $checkFn = "Test-$((Get-Culture).TextInfo.ToTitleCase($Id))Check"
    if (-not (Get-Command $checkFn -ErrorAction SilentlyContinue)) {
        throw "no check named $checkFn for id '$Id'"
    }
    if (-not $PreflightFixes.ContainsKey($Id)) {
        Write-Host "  '$Id' has no auto fix (it is guided or informational); re-checking only."
        return (& $checkFn)
    }
    if ($DryRun) {
        Write-Host "  [dry-run] would run the '$Id' fix, then re-check."
        return (& $checkFn)
    }
    $null = & $PreflightFixes[$Id]
    & $checkFn
}


# --- default renderer -----------------------------------------------------------------------
# The plain-text skin, so the engine is runnable and demonstrable today. A TUI/GUI is another
# renderer over the same Get-Preflight objects.

function Show-Preflight {
    param([object[]]$Results)
    $glyph = @{ ok = '[ ok ]'; warn = '[warn]'; missing = '[MISS]'; info = '[info]' }
    foreach ($r in $Results) {
        $g = $glyph[$r.Status]; if (-not $g) { $g = '[????]' }
        Write-Host ("  {0}  {1,-20} {2}" -f $g, $r.Label, $r.Detail)
        if ($r.FixLabel) {
            $adm = if ($r.NeedsAdmin) { ' (needs admin)' } else { '' }
            Write-Host ("         -> {0} [{1}]{2}" -f $r.FixLabel, $r.FixKind, $adm)
        }
    }
    $blockers = @($Results | Where-Object { $_.Blocking -and $_.Status -ne 'ok' })
    Write-Host ''
    if ($blockers.Count) {
        Write-Host ("  {0} blocker(s): {1}" -f $blockers.Count, (($blockers | ForEach-Object Id) -join ', '))
    } else {
        Write-Host '  no blockers; ready to choose rails.'
    }
}
