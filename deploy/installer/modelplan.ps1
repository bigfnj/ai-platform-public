<#
.SYNOPSIS
  Installer model plan: selected rails + VRAM -> the models to install, sized to the card.

.DESCRIPTION
  The CLI seam over lib-modelplan.ps1, the companion to preflight.ps1. It composes with
  preflight: if -VramGb is not given it reads the GPU from the preflight engine, so the two
  stages share one VRAM number rather than detecting it twice.

.EXAMPLE
  modelplan.ps1 -Rails recipe-book,terminal-fun
  modelplan.ps1 -Rails recipe-book,terminal-fun -VramGb 8
  modelplan.ps1 -Rails recipe-book -Json
  modelplan.ps1 -SelfTest
#>
param(
    [string[]]$Rails = @('recipe-book', 'terminal-fun'),
    [double]$VramGb = -1,
    [switch]$Json,
    [switch]$SelfTest
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib-modelplan.ps1')


function Resolve-Vram {
    param([double]$VramGb)
    if ($VramGb -ge 0) { return [int]($VramGb * 1024) }
    # Not given: ask the preflight engine, so both stages agree on the card.
    $pf = Join-Path $PSScriptRoot 'lib-preflight.ps1'
    if (Test-Path $pf) {
        . $pf
        $gpu = Get-Preflight | Where-Object Id -eq 'gpu' | Select-Object -First 1
        if ($gpu -and $gpu.Data.vram_mib) { return [int]$gpu.Data.vram_mib }
    }
    0
}


function Invoke-ModelPlanSelfTest {
    # Wiring invariants, RC022/RC023 in spirit: every kind a rail actually declares must have a
    # catalog list, every roles.json default a rail uses must resolve to a size or be flagged
    # estimated, and the whole-fleet plan must be JSON-serialisable. Catches "a rail declares a
    # tts slot the catalog can't offer" before a user meets it.
    $fail = 0
    $catalog = Get-ModelCatalog
    $defaults = Get-RoleDefaults

    $railsDir = Join-Path (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path 'rails'
    $allRails = @(Get-ChildItem $railsDir -Directory | Where-Object { Test-Path (Join-Path $_.FullName 'rail.json') } | ForEach-Object Name)

    foreach ($slot in (Get-RailSlots -Rails $allRails)) {
        if (-not $catalog.ContainsKey($slot.kind)) {
            Write-Host "  FAIL  rail $($slot.rail) declares kind '$($slot.kind)' with no catalog list"
            $fail++
        }
        if (-not $defaults.ContainsKey($slot.role)) {
            if ($slot.required) {
                Write-Host "  FAIL  required role @$($slot.role) ($($slot.rail)) has no roles.json default"
                $fail++
            } else {
                Write-Host "  warn  optional role @$($slot.role) ($($slot.rail)) has no default (intentional)"
            }
        }
    }
    try { $null = (Get-ModelPlan -Rails $allRails -VramMib 24564 -Installed @{}) | ConvertTo-Json -Depth 8 }
    catch { Write-Host "  FAIL  whole-fleet plan is not JSON-serialisable: $_"; $fail++ }

    if ($fail -eq 0) {
        $kinds = @($catalog.Keys | Sort-Object)
        Write-Host "  self-test ok ($($allRails.Count) rails, kinds: $($kinds -join ', '))"
    }
    return $fail
}


if ($SelfTest) { exit (Invoke-ModelPlanSelfTest) }

$vram = Resolve-Vram -VramGb $VramGb
$plan = Get-ModelPlan -Rails $Rails -VramMib $vram
if ($Json) {
    $plan | ConvertTo-Json -Depth 8
} else {
    Write-Host "AI-Platform model plan`n"
    Show-ModelPlan -Plan $plan
}
exit ([math]::Min(@($plan.warnings).Count, 1))