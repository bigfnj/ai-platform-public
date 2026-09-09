<#
.SYNOPSIS
  Installer apply: selections -> a deployed platform. Dry by default.

.DESCRIPTION
  The CLI seam over lib-apply.ps1, and the point where the three engines compose: it reads VRAM
  from the preflight engine, builds the plan with the model-plan engine, then runs the apply
  actions. It prints what it WOULD do and changes nothing until -Execute, and -Execute stops at
  any admin-needing step unless already elevated (the flow elevates once, up front).

.EXAMPLE
  apply.ps1 -Rails recipe-book,terminal-fun -AdminUser me -AdminPassword pw          # dry run
  apply.ps1 -Rails recipe-book,terminal-fun -AdminUser me -AdminPassword pw -Execute # for real
  apply.ps1 -SelfTest
#>
param(
    [string[]]$Rails = @('recipe-book', 'terminal-fun'),
    [double]$VramGb = -1,
    [string]$AdminUser = 'admin',
    [string]$AdminPassword = '',
    [switch]$Execute,
    [switch]$SelfTest
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib-modelplan.ps1')
. (Join-Path $PSScriptRoot 'lib-apply.ps1')


function Resolve-Vram {
    param([double]$VramGb)
    if ($VramGb -ge 0) { return [int]($VramGb * 1024) }
    $pf = Join-Path $PSScriptRoot 'lib-preflight.ps1'
    if (Test-Path $pf) {
        . $pf
        $gpu = Get-Preflight | Where-Object Id -eq 'gpu' | Select-Object -First 1
        if ($gpu -and $gpu.Data.vram_mib) { return [int]$gpu.Data.vram_mib }
    }
    0
}


function Invoke-ApplySelfTest {
    # The writers produce valid artifacts and the action list is well-formed. Pure and safe: it
    # writes only to a temp dir and calls each action's Test (a safe read), never a Do.
    $fail = 0
    $tmp = Join-Path $env:TEMP ("ai-apply-selftest-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null
    try {
        $plan = Get-ModelPlan -Rails @('recipe-book', 'terminal-fun') -VramMib 24564 -Installed @{}

        $rp = Join-Path $tmp 'roles.json'
        Write-RolesJson -Plan $plan -Path $rp | Out-Null
        try {
            $parsed = Get-Content -Raw $rp | ConvertFrom-Json
            if (-not $parsed.roles.recipe) { Write-Host "  FAIL  roles.json missing the recipe role"; $fail++ }
        } catch { Write-Host "  FAIL  roles.json is not valid JSON: $_"; $fail++ }

        $ep = Join-Path $tmp '.env'
        Write-InstallerEnv -Rails @('recipe-book', 'terminal-fun') -AdminUser 'u' -AdminPassword 'p' -Path $ep | Out-Null
        $env = Get-Content -Raw $ep
        if ($env -match '\{\{') { Write-Host "  FAIL  .env still has unfilled {{placeholders}}"; $fail++ }
        if ($env -notmatch 'PLATFORM_ENABLED_APPS=recipe-book,terminal-fun') { Write-Host "  FAIL  .env ENABLED_APPS not filled"; $fail++ }

        $actions = Get-ApplyActions -Plan $plan -Rails @('recipe-book', 'terminal-fun') -AdminUser 'u' -AdminPassword 'p'
        if (@($actions).Count -lt 4) { Write-Host "  FAIL  too few apply actions"; $fail++ }
        foreach ($a in $actions) {
            foreach ($f in 'Id', 'Label', 'Test', 'Do') {
                if (-not $a.PSObject.Properties.Name.Contains($f)) { Write-Host "  FAIL  action missing $f"; $fail++ }
            }
            try { $null = & $a.Test } catch { Write-Host "  FAIL  action '$($a.Id)' Test threw: $_"; $fail++ }
        }
        if ($fail -eq 0) { Write-Host "  self-test ok ($(@($actions).Count) actions, writers valid)" }
    } finally {
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    }
    return $fail
}


if ($SelfTest) { exit (Invoke-ApplySelfTest) }

$vram = Resolve-Vram -VramGb $VramGb
$plan = Get-ModelPlan -Rails $Rails -VramMib $vram
$actions = Get-ApplyActions -Plan $plan -Rails $Rails -AdminUser $AdminUser -AdminPassword $AdminPassword

$mode = if ($Execute) { 'EXECUTE' } else { 'dry run (nothing will change; add -Execute to apply)' }
Write-Host "AI-Platform apply -- $mode`n  rails: $($Rails -join ', ')   card: $([math]::Round($vram/1024,1)) GB`n"
$rc = Invoke-Apply -Actions $actions -Execute:$Execute
exit ([math]::Min($rc, 1))
