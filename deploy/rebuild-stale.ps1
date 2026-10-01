#Requires -Version 5.1
<#
    Rebuild the services whose images predate the code in git, one at a time.

    WHY A SCRIPT. The standing rule for this stack is "rebuild ONLY the changed backend service"
    (`docker compose up -d --build --no-deps <service>`) and NEVER a bare full-stack
    `docker compose up`, which recreates caddy, rebinds host ports and churns the Windows
    Docker/WSL NAT, briefly dropping the whole machine's internet. Putting the loop in a file
    means that rule is expressed once, in a form that cannot be fat-fingered into the dangerous
    version at 2am, and it takes one approval instead of fifteen.

    Ordering, both deliberate:
      - `dashboard` FIRST because `iep` runs the same image (edu-suite-dashboard:latest) with
        IEP_ONLY=1. It is recreated (no --build) once that image exists, so the two never
        disagree about which build they are running.
      - `gateway` LAST because it is the front door; rebuilding it is the only step here a
        logged-in user would notice.

    `caddy` is a pulled image and `terminal-fun` was already rebuilt after the commit the others
    are missing, so neither is listed.

    Failures do not abort the run. A partial rebuild that reports honestly is more useful than
    one that stops at the first red and leaves you guessing which services moved.
#>
param(
    [string]$LogDir = (Join-Path $PSScriptRoot 'logs'),
    [switch]$WhatIfOnly,     # print the plan, touch nothing
    # Rebuild only these services. Order is preserved from the full list below rather than from
    # the order given, because `dashboard` must precede the `iep` recreate that shares its image.
    # Default (empty) = the whole stale set, which is what the 2026-09-11 drift pass needed.
    [string[]]$Service = @()
)

$ErrorActionPreference = 'Continue'
$deploy = $PSScriptRoot

# EVERY buildable compose service, in a safe build order (`dashboard` first because `iep` runs the
# image it builds; `gateway` last because it is the only step a logged-in user notices). `caddy` is
# absent because it is a pulled image with nothing to build.
#
# Two lists, deliberately. This started as a one-shot "rebuild what drifted" script and $staleSet
# was the whole of it; once -Service existed, validating against that subset meant a legitimate
# service name was rejected as unknown (terminal-fun, which happened to be current that day). The
# known set is what the stack CONTAINS; the stale set is one day's plan.
$allBuild = @(
    'dashboard',
    'iep-goals', 'job-aid', 'finance', 'recipe-book', 'bouquet',
    'workstation', 'terminal-fun', 'ai-playground', 'ai-voice', 'co-worker',
    'smb-partner-enablement', 'gemini-cx', 'meeting-atlas',
    'gateway'
)
# Recreate only: shares an image built above, so building it again would be a second identical build.
$allRecreate = @('iep')

# The default plan: what was behind git on 2026-09-11. terminal-fun and ai-playground were already
# current then, which is why they are not here but ARE selectable by name.
$staleSet = @($allBuild | Where-Object { $_ -ne 'terminal-fun' }) + $allRecreate

if ($Service.Count -gt 0) {
    $unknown = @($Service | Where-Object { $_ -notin ($allBuild + $allRecreate) })
    if ($unknown.Count -gt 0) {
        # Fail rather than silently rebuild nothing: a typo'd service name that produces an empty
        # plan and exits 0 reads exactly like a successful no-op.
        Write-Host "[FAIL] not compose services in this stack: $($unknown -join ', ')" -ForegroundColor Red
        Write-Host "       known: $(($allBuild + $allRecreate) -join ', ')"
        exit 2
    }
    $build = @($allBuild | Where-Object { $_ -in $Service })
    $recreate = @($allRecreate | Where-Object { $_ -in $Service })
    # `iep` runs the image `dashboard` builds. Asking for one without the other is almost always
    # a mistake, so say so instead of shipping a half-updated pair.
    if (('iep' -in $recreate) -and ('dashboard' -notin $build)) {
        Write-Host "[WARN] recreating iep without rebuilding dashboard: it will start on the " -ForegroundColor Yellow -NoNewline
        Write-Host "existing edu-suite-dashboard image." -ForegroundColor Yellow
    }
    if (('dashboard' -in $build) -and ('iep' -notin $recreate)) {
        Write-Host "[WARN] rebuilding dashboard without recreating iep: iep keeps running the " -ForegroundColor Yellow -NoNewline
        Write-Host "OLD image until it is recreated." -ForegroundColor Yellow
    }
} else {
    $build = @($allBuild | Where-Object { $_ -in $staleSet })
    $recreate = @($allRecreate | Where-Object { $_ -in $staleSet })
}

if (-not (Test-Path $LogDir)) { $null = New-Item -ItemType Directory -Path $LogDir -Force }
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$log = Join-Path $LogDir "rebuild-stale-$stamp.log"

Write-Host "rebuild plan ($($build.Count) build, $($recreate.Count) recreate)"
Write-Host "  build   : $($build -join ', ')"
Write-Host "  recreate: $($recreate -join ', ')"
Write-Host "  log     : $log"
if ($WhatIfOnly) { Write-Host 'WhatIfOnly: nothing was changed.'; exit 0 }

Push-Location $deploy
$results = New-Object System.Collections.Generic.List[object]
try {
    foreach ($svc in $build) {
        $t0 = Get-Date
        Write-Host "`n=== build $svc ..." -NoNewline
        "=== build $svc ($(Get-Date -Format o))" | Add-Content $log
        # One command per service. --no-deps so a rail rebuild cannot drag caddy or the whole
        # stack in behind it.
        & docker compose up -d --build --no-deps $svc *>&1 | Add-Content $log
        $ok = ($LASTEXITCODE -eq 0)
        $secs = [int]((Get-Date) - $t0).TotalSeconds
        Write-Host $(if ($ok) { " ok ($secs s)" } else { " FAILED ($secs s)" })
        $results.Add([pscustomobject]@{ Service = $svc; Action = 'build'; Ok = $ok; Seconds = $secs })
    }
    foreach ($svc in $recreate) {
        $t0 = Get-Date
        Write-Host "`n=== recreate $svc ..." -NoNewline
        "=== recreate $svc ($(Get-Date -Format o))" | Add-Content $log
        & docker compose up -d --no-deps --force-recreate $svc *>&1 | Add-Content $log
        $ok = ($LASTEXITCODE -eq 0)
        $secs = [int]((Get-Date) - $t0).TotalSeconds
        Write-Host $(if ($ok) { " ok ($secs s)" } else { " FAILED ($secs s)" })
        $results.Add([pscustomobject]@{ Service = $svc; Action = 'recreate'; Ok = $ok; Seconds = $secs })
    }
} finally {
    Pop-Location
}

Write-Host "`n================ summary ================"
$results | ForEach-Object {
    Write-Host ("{0,-24} {1,-9} {2,-6} {3,4}s" -f $_.Service, $_.Action, $(if ($_.Ok) { 'PASS' } else { 'FAIL' }), $_.Seconds)
}
$failed = @($results | Where-Object { -not $_.Ok })
Write-Host "========================================="
Write-Host ("{0} ok, {1} failed; log: {2}" -f ($results.Count - $failed.Count), $failed.Count, $log)
if ($failed.Count -gt 0) { exit 1 }
exit 0
