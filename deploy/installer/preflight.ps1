<#
.SYNOPSIS
  Installer preflight: detect prerequisites, and optionally remediate one.

.DESCRIPTION
  The CLI entry point over lib-preflight.ps1. This is the seam every front end sits on: the TUI
  prints the rows and calls this with -Fix, a GUI draws buttons that call -Fix, the broker tray
  calls -Json and renders its own. The detection and remediation live in the library so all of
  them share one definition of "is X ready", and the post-install smoke test can call the same
  checks so preflight and verify never drift.

.EXAMPLE
  preflight.ps1                 # detect and print the board
  preflight.ps1 -Json           # the same board as JSON, for a non-PowerShell front end
  preflight.ps1 -Fix ollama     # remediate one row, then re-check just that row
  preflight.ps1 -Fix runtime -DryRun
  preflight.ps1 -SelfTest       # assert the checks and fixes are wired to each other
#>
param(
    [switch]$Json,
    [string]$Fix,
    [switch]$DryRun,
    [switch]$SelfTest,
    [string]$InstallRoot = $env:SystemDrive
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib-preflight.ps1')


function Invoke-SelfTest {
    # The wiring invariant, in the spirit of the rail contract's RC022/RC023: a row that
    # advertises an auto fix must HAVE one, a fix must map to a real check, and every row must be
    # re-checkable by id. Catches the "button installs X then trusts a fix that isn't there" bug
    # before a user ever meets it.
    $fail = 0
    $results = Get-Preflight -InstallRoot $InstallRoot

    foreach ($r in $results) {
        $checkFn = "Test-$((Get-Culture).TextInfo.ToTitleCase($r.Id))Check"
        if (-not (Get-Command $checkFn -ErrorAction SilentlyContinue)) {
            Write-Host "  FAIL  row '$($r.Id)' has no $checkFn, so Invoke-PreflightFix can't re-check it"
            $fail++
        }
        if ($r.FixKind -eq 'auto' -and -not $PreflightFixes.ContainsKey($r.Id)) {
            Write-Host "  FAIL  row '$($r.Id)' advertises an auto fix but $PreflightFixes has no entry"
            $fail++
        }
        if ($r.FixLabel -and -not $r.FixKind) {
            Write-Host "  FAIL  row '$($r.Id)' has a fix label but no FixKind"
            $fail++
        }
    }
    foreach ($id in $PreflightFixes.Keys) {
        $checkFn = "Test-$((Get-Culture).TextInfo.ToTitleCase($id))Check"
        if (-not (Get-Command $checkFn -ErrorAction SilentlyContinue)) {
            Write-Host "  FAIL  fix '$id' has no matching $checkFn"
            $fail++
        }
    }
    # Every result must survive a JSON round-trip: a front end in another language is the point.
    try { $null = $results | ConvertTo-Json -Depth 5 } catch { Write-Host "  FAIL  results are not JSON-serialisable: $_"; $fail++ }

    if ($fail -eq 0) { Write-Host "  self-test ok ($($results.Count) checks, $($PreflightFixes.Count) fixes wired)" }
    return $fail
}


if ($SelfTest) { exit (Invoke-SelfTest) }

if ($Fix) {
    if ($NeedsAdmin = ((Get-Preflight -InstallRoot $InstallRoot |
                        Where-Object Id -eq $Fix | Select-Object -First 1).NeedsAdmin)) {
        if (-not (Test-Elevated) -and -not $DryRun) {
            Write-Host "  '$Fix' needs Administrator. Re-run elevated, or the flow should elevate once before its fixes."
        }
    }
    $result = Invoke-PreflightFix -Id $Fix -DryRun:$DryRun
    if ($Json) { $result | ConvertTo-Json -Depth 5 } else { Show-Preflight -Results @($result) }
    exit 0
}

$results = Get-Preflight -InstallRoot $InstallRoot
if ($Json) {
    $results | ConvertTo-Json -Depth 5
} else {
    Write-Host "AI-Platform preflight`n"
    Show-Preflight -Results $results
}
$blocking = @($results | Where-Object { $_.Blocking -and $_.Status -ne 'ok' })
exit ([math]::Min($blocking.Count, 1))
