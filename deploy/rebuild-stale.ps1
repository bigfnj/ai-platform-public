#Requires -Version 5.1
<#
    Rebuild every service the stack builds, one at a time, and recreate the services that run one
    of those images. It does NOT detect staleness: nothing here compares an image with the code in
    git, so every planned service is rebuilt whether its code changed or not (the name is older
    than that fact). To rebuild only what you changed, name it with -Service.

    WHY A SCRIPT. The standing rule for this stack is "rebuild ONLY the changed backend service"
    (`docker compose up -d --build --no-deps <service>`) and NEVER a bare full-stack
    `docker compose up`, which recreates caddy, rebinds host ports and churns the Windows
    Docker/WSL NAT, briefly dropping the whole machine's internet. Putting the loop in a file
    means that rule is expressed once, in a form that cannot be fat-fingered into the dangerous
    version at 2am, and it takes one approval instead of one per service.

    THE SERVICE LIST COMES FROM COMPOSE, not from this file. `docker compose config` names every
    service with a `build:` stanza; those are rebuilt. A service with no `build:` whose image one
    of those builds (`iep` runs `dashboard`'s edu-suite-dashboard:latest with IEP_ONLY=1) is
    recreated instead. A pulled image (caddy, openmaic-app) is neither. The hardcoded list this
    replaced had already fallen four services behind the stack (openmaic, course-builder,
    servicenow, watch-builder), and a rail missing from it was simply never rebuilt.

    Ordering, both deliberate:
      - a service whose image another service also runs builds FIRST, and the sharer is
        recreated (no --build) after it, so the two never disagree about which build they run.
      - `gateway` LAST because it is the front door; rebuilding it is the only step here a
        logged-in user would notice.

    Failures do not abort the run. A partial rebuild that reports honestly is more useful than
    one that stops at the first red and leaves you guessing which services moved.

    RAILS DISABLED ON THIS BOX (PLATFORM_DISABLED_APPS in deploy\.env, the gateway's own list)
    are left out of the plan and named as skipped, because `up -d --build` would START them; asking
    for one by -Service is refused. After the run (and as a dry run with -WhatIfOnly) the same
    step as sync-disabled-rails.ps1 parks their containers: restart=no, stopped.

    THE RUNTIME AND THE STACK ARE THE BOX'S. This script ran `docker compose` in deploy\, which is
    the full stack on Docker Desktop. A podman box has no docker CLI at all and runs the LEAN stack
    (deploy\installer\docker-compose.installer.yml, the box's PLATFORM_COMPOSE_OVERRIDE, and one
    compose profile per enabled rail), so there it could rebuild nothing. -RuntimeMode (podman |
    desktop | wsl) defaults to Resolve-PlatformRuntime's pick, which is refused when it is only a
    guess (nothing declared, no CLI lists the gateway, and docker is not the only engine CLI; docker
    alone is Docker Desktop), and decides the compose command (lib-runtime Get-ComposeCommand).
    -Stack (full | lean) defaults to the stack the gateway container was created from
    (Resolve-PlatformStack), and when there is none to lean on podman and wsl (the lean
    installer's runtimes) and full on Docker Desktop. The full
    stack runs exactly the commands it always ran; the lean one adds --env-file deploy\.env, every
    -f the box composes with (Get-ComposePaths, the override included) and the enabled rails'
    profiles, as platform-startup.ps1 does.

    A SERVICE BUILT FROM OUTSIDE THIS CHECKOUT is skipped and named ("external build context")
    unless it is asked for with -Service. A box override can build openmaic-app from another
    checkout, and a blanket rebuild would ship whatever that checkout holds at the moment.

    A REAL RUN ALSO PRUNES THE NATIVE SERVICES' ROTATED LOGS in deploy\logs, at most once a day
    (deploy\logs\log-prune.last keeps the time), to the newest ten of each. A full-stack box has no
    watchdog to do it, and NSSM 2.24 has no rotate hook. -WhatIfOnly prunes nothing.
#>
[CmdletBinding()]
param(
    # No default expressions built from $PSScriptRoot in here: under Windows PowerShell 5.1 an
    # advanced script run with -File sees it EMPTY inside param() (measured), so such a default
    # threw before the script began. Both defaults are set in the body.
    [string]$LogDir,         # default: deploy\logs
    [switch]$WhatIfOnly,     # print the plan, touch nothing
    # Rebuild only these services. Order is preserved from the derived plan rather than from the
    # order given, because a shared image's builder must precede the recreate that runs it.
    # Default (empty) = every service the stack builds, plus the recreates that share an image.
    [string[]]$Service = @(),
    # The deploy folder to read (compose file, .env) and run compose in. Another checkout's, for a
    # read-only -WhatIfOnly from a worktree. Default: this script's folder.
    [string]$DeployDir,
    # The container runtime. Default: Resolve-PlatformRuntime (deploy\.env's PLATFORM_RUNTIME,
    # else the CLI that lists the gateway, else Docker Desktop when docker is the only engine CLI;
    # with none of those, the script refuses and asks for this).
    [ValidateSet('podman', 'desktop', 'wsl')][string]$RuntimeMode,
    # The compose stack. Default: the one the gateway container was created from, else lean on
    # podman and wsl and full on Docker Desktop.
    [ValidateSet('full', 'lean')][string]$Stack
)

$ErrorActionPreference = 'Continue'
if (-not $DeployDir) { $DeployDir = $PSScriptRoot }
if (-not $LogDir) { $LogDir = Join-Path $PSScriptRoot 'logs' }
$deploy = $DeployDir
$envFile = Join-Path $deploy '.env'

# The front door. Named because it is the one ordering rule compose cannot express.
$frontDoor = 'gateway'

# lib-runtime: the runtime and stack pickers, the compose command and files, the disabled-rail step.
$Root = Split-Path $deploy -Parent; $Installer = Join-Path $deploy 'installer'
$TempBase = Join-Path $env:USERPROFILE 'AppData\Local\Temp'; $LogFile = Join-Path $TempBase 'ai-platform-rebuild-stale.log'
function Write-Log($m) { Write-Host "  $m" -ForegroundColor Yellow }
. (Join-Path $PSScriptRoot 'installer\lib-runtime.ps1')

if (-not $RuntimeMode) {
    $pick = Resolve-PlatformRuntime -EnvFile $envFile
    $RuntimeMode = $pick.Mode
    Write-Host "runtime : $RuntimeMode ($($pick.Reason))"
    if ($pick.Warning) { Write-Host "[WARN] $($pick.Warning)" -ForegroundColor Yellow }
    # A guess is refused: with nothing declared and no CLI listing the gateway (after `compose
    # down`, or while Docker Desktop is still starting), the first CLI on PATH is podman on a box
    # that carries both, and this script then built the full stack into podman's machine. docker as
    # the only engine CLI is no guess (Basis 'only-cli'): that is Docker Desktop.
    if ($pick.Basis -eq 'guess') {
        Write-Host ("[FAIL] the runtime is a guess: deploy\.env declares no PLATFORM_RUNTIME, no CLI lists " +
                    "platform-gateway-1, and docker is not the only engine CLI here. Pass -RuntimeMode podman|desktop|wsl, " +
                    "or declare PLATFORM_RUNTIME in $envFile.") -ForegroundColor Red
        exit 2
    }
}
if (-not $Stack) {
    $picked = Resolve-PlatformStack -Mode $RuntimeMode
    $Stack = $picked.Stack
    Write-Host "stack   : $Stack ($($picked.Reason))"
}
$compose = Get-ComposeCommand -Mode $RuntimeMode
if ($compose.DockerHost -and -not $env:DOCKER_HOST) { $env:DOCKER_HOST = $compose.DockerHost }
$composeName = (@($compose.Exe) + @($compose.Prefix)) -join ' '

# What goes before every compose subcommand. The full stack: nothing, run in deploy\ so compose
# finds docker-compose.yml and .env by itself (the commands this script always ran). The lean
# stack: the env file, every -f the box composes with (FileArgs carries the override), and EVERY
# enabled rail's profile, disabled ones included: a profiled-out service is absent from the render,
# so its container could not be found to park (sync-disabled-rails.ps1 renders the same way).
# $projectDir is where a relative path in a compose file resolves: the first file's folder.
$stackArgs = @()
$projectDir = $deploy
if ($Stack -eq 'lean') {
    try { $enabled = @(Get-DeclaredAppList -Key 'PLATFORM_ENABLED_APPS' -EnvFile $envFile) }
    catch {
        Write-Host "[FAIL] PLATFORM_ENABLED_APPS does not parse ($($_.Exception.Message)); the gateway refuses it too." -ForegroundColor Red
        exit 2
    }
    $paths = Get-ComposePaths -Mode $RuntimeMode
    $stackArgs = @('--env-file', $paths.Env) + $paths.FileArgs + @(Get-ComposeProfiles -Apps ($enabled -join ','))
    $projectDir = $Installer
}

# Read the stack from compose, from deploy\ like the build commands below. Not Invoke-Compose,
# which merges stderr into the output: compose's stderr is kept apart here so a warning cannot
# corrupt the JSON and a failure can be quoted. The render is held in memory only and never logged:
# with env_file inlined it carries every secret the stack has.
$renderArgs = @($compose.Prefix) + $stackArgs + @('config', '--format', 'json')
Push-Location $deploy
try { $render = @(& $compose.Exe @renderArgs 2>&1) } finally { Pop-Location }
$renderErr = @($render | Where-Object { $_ -is [System.Management.Automation.ErrorRecord] })
$renderJson = (@($render | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] }) -join "`n")
$cfg = $null
if ($LASTEXITCODE -eq 0) {
    try { $cfg = $renderJson | ConvertFrom-Json }
    catch { $cfg = $null }
}
$svcProps = if ($cfg -and $cfg.services) { @($cfg.services.PSObject.Properties) } else { @() }
if ($svcProps.Count -eq 0) {
    # Fail loudly: an empty plan that exits 0 reads exactly like "nothing was stale".
    $why = if ($renderErr.Count) { [string]$renderErr[0] } else { "exit $LASTEXITCODE, no services in the render" }
    Write-Host "[FAIL] could not read the stack from '$composeName config' in $deploy : $why" -ForegroundColor Red
    exit 2
}

# `-Service a,b` arrives as ONE string through `powershell -File`, which does not parse arrays.
$Service = @($Service | ForEach-Object { $_ -split ',' } | ForEach-Object { $_.Trim() } | Where-Object { $_ })

# Is this build context inside the checkout? compose renders contexts absolute (inside WSL, as
# /mnt/<drive>/...); a relative one resolves against the first compose file's folder. A remote
# context (a git URL) is outside by definition.
function Test-RepoContext([string]$Context) {
    if (-not $Context) { return $true }
    if ($Context -match '^[A-Za-z][A-Za-z0-9+.-]+://|^git@') { return $false }
    if ($RuntimeMode -eq 'wsl' -and $Context.StartsWith('/')) {
        $r = (ConvertTo-WslPath $Root).TrimEnd('/')
        $c = $Context.TrimEnd('/')
        return ($c -eq $r -or $c.StartsWith("$r/", [StringComparison]::Ordinal))
    }
    $full = if ([IO.Path]::IsPathRooted($Context)) { $Context } else { Join-Path $projectDir $Context }
    $full = [IO.Path]::GetFullPath($full).TrimEnd('\', '/')
    $r = [IO.Path]::GetFullPath($Root).TrimEnd('\', '/')
    return ($full -eq $r -or $full.StartsWith("$r\", [StringComparison]::OrdinalIgnoreCase))
}

# Every service with a build stanza is built; a build-less service whose image one of them builds
# is recreated after it. Anything else (a pulled image) has nothing to do here.
$builtBy = @{}      # image -> the service that builds it
$allBuild = @(); $allRecreate = @()
foreach ($p in $svcProps) {
    if ($p.Value.PSObject.Properties.Name -contains 'build') {
        $allBuild += $p.Name
        if ($p.Value.PSObject.Properties.Name -contains 'image' -and $p.Value.image) { $builtBy[[string]$p.Value.image] = $p.Name }
    }
}
$sharer = @{}       # recreate-only service -> the service whose build it runs
foreach ($p in $svcProps) {
    if ($p.Value.PSObject.Properties.Name -contains 'build') { continue }
    $img = if ($p.Value.PSObject.Properties.Name -contains 'image') { [string]$p.Value.image } else { '' }
    if ($img -and $builtBy.ContainsKey($img)) { $allRecreate += $p.Name; $sharer[$p.Name] = $builtBy[$img] }
}
$first = @($allBuild | Where-Object { $_ -in $sharer.Values })
$allBuild = @($first) + @($allBuild | Where-Object { $_ -notin $first -and $_ -ne $frontDoor }) +
            @($allBuild | Where-Object { $_ -eq $frontDoor })

# Built from outside this checkout: left out and named, unless asked for by -Service.
$external = [ordered]@{}   # service -> its build context
foreach ($p in $svcProps) {
    if ($p.Value.PSObject.Properties.Name -notcontains 'build') { continue }
    $b = $p.Value.build
    $ctx = if ($b -is [string]) { $b } elseif ($b.PSObject.Properties.Name -contains 'context') { [string]$b.context } else { '' }
    if (-not (Test-RepoContext $ctx)) { $external[$p.Name] = $ctx }
}
$externalSkipped = @($external.Keys | Where-Object { $_ -notin $Service })
$allBuild = @($allBuild | Where-Object { $_ -notin $externalSkipped })

# Rails disabled on this box: one declaration, read as the gateway and tools/rail_smoke.py read it.
try { $disabled = @(Get-DeclaredAppList -Key 'PLATFORM_DISABLED_APPS' -EnvFile $envFile) }
catch {
    Write-Host "[FAIL] PLATFORM_DISABLED_APPS does not parse ($($_.Exception.Message)); the gateway refuses it too." -ForegroundColor Red
    exit 2
}
$composeServices = @(Get-ComposeServices -ConfigJson $renderJson)
$off = Resolve-DisabledServices -Disabled $disabled -Services $composeServices -RailsDir (Join-Path $Root 'rails')
$offServices = @($off.Services.Keys)
foreach ($u in $off.Unknown) { Write-Host "[WARN] PLATFORM_DISABLED_APPS names '$u', which no rail or service here serves; it skips nothing." -ForegroundColor Yellow }
$allBuild = @($allBuild | Where-Object { $_ -notin $offServices })
$allRecreate = @($allRecreate | Where-Object { $_ -notin $offServices })

if ($Service.Count -gt 0) {
    $asked = @($Service | Where-Object { $_ -in $offServices })
    if ($asked.Count -gt 0) {
        # `up -d --build` would START it on a box that declared it does not run it.
        Write-Host ("[FAIL] disabled on this box by PLATFORM_DISABLED_APPS: " +
                    (($asked | ForEach-Object { "$_ ($($off.Services[$_]))" }) -join ', ') +
                    ". Take the rail out of that list in deploy\.env first.") -ForegroundColor Red
        exit 2
    }
    $unknown = @($Service | Where-Object { $_ -notin ($allBuild + $allRecreate) })
    if ($unknown.Count -gt 0) {
        # Fail rather than silently rebuild nothing: a typo'd service name that produces an empty
        # plan and exits 0 reads exactly like a successful no-op.
        Write-Host "[FAIL] not services this script builds or recreates: $($unknown -join ', ')" -ForegroundColor Red
        Write-Host "       known: $(($allBuild + $allRecreate) -join ', ')"
        exit 2
    }
    $build = @($allBuild | Where-Object { $_ -in $Service })
    $recreate = @($allRecreate | Where-Object { $_ -in $Service })
    # A recreate runs another service's image. Asking for one without the other is almost always
    # a mistake, so say so instead of shipping a half-updated pair.
    foreach ($r in $allRecreate) {
        $b = $sharer[$r]
        if (($r -in $recreate) -and ($b -notin $build)) {
            Write-Host "[WARN] recreating $r without rebuilding ${b}: it will start on the existing image." -ForegroundColor Yellow
        }
        if (($b -in $build) -and ($r -notin $recreate)) {
            Write-Host "[WARN] rebuilding $b without recreating ${r}: $r keeps running the OLD image until it is recreated." -ForegroundColor Yellow
        }
    }
} else {
    $build = $allBuild
    $recreate = $allRecreate
}

if (-not (Test-Path $LogDir)) { $null = New-Item -ItemType Directory -Path $LogDir -Force }
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$log = Join-Path $LogDir "rebuild-stale-$stamp.log"
# One log per run, and none was ever removed: the newest $NssmLogKeep are kept, this run's included.
if (-not $WhatIfOnly) { $null = Remove-OldRunLogs -LogDir $LogDir -Filter 'rebuild-stale-*.log' -Keep ($NssmLogKeep - 1) }
# The services' rotated logs (deploy\logs of the checkout this runs, where install-services points
# NSSM), pruned at most once a day. The full stack runs no watchdog and no logon script, and NSSM
# 2.24 runs no rotate hook, so this, its build and update path, is where they are pruned there
# (lib-runtime Invoke-LogPruneFromStamp).
if (-not $WhatIfOnly) {
    $prune = Invoke-LogPruneFromStamp -LogDir (Join-Path $deploy 'logs') -Now ([datetime]::UtcNow)
    if ($prune.Ran) { Write-Host "log prune: removed $($prune.Removed) rotated service log file(s); the newest $NssmLogKeep of each are kept" }
}

Write-Host "rebuild plan ($($build.Count) build, $($recreate.Count) recreate)"
Write-Host "  build   : $($build -join ', ')"
Write-Host "  recreate: $($recreate -join ', ')"
$skipped = @($offServices | ForEach-Object { "$_ ($($off.Services[$_]) is disabled on this box)" }) +
           @($externalSkipped | Where-Object { $_ -notin $offServices } |
             ForEach-Object { "$_ (external build context $($external[$_]); name it with -Service to rebuild it)" })
if ($skipped.Count -gt 0) {
    Write-Host ("  skipped : " + ($skipped -join ', '))
}
Write-Host "  log     : $log"

# Park the disabled rails' containers (restart=no, stopped); bring back any whose rail is no
# longer disabled. A dry run under -WhatIfOnly. Through this box's runtime CLI: it was always
# docker, which a podman box does not have.
function Invoke-DisabledStep([switch]$DryRun) {
    $res = Sync-DisabledRails -Disabled $disabled -Services $composeServices -RailsDir (Join-Path $Root 'rails') `
                              -Mode $RuntimeMode -DryRun:$DryRun
    if ($res.Lines.Count) { Write-Host 'disabled rails:'; foreach ($l in $res.Lines) { Write-Host "  $l" } }
    return $res.Failed
}
if ($WhatIfOnly) {
    $null = Invoke-DisabledStep -DryRun
    Write-Host 'WhatIfOnly: nothing was changed.'
    exit 0
}

Push-Location $deploy
$results = New-Object System.Collections.Generic.List[object]
try {
    foreach ($svc in $build) {
        $t0 = Get-Date
        Write-Host "`n=== build $svc ..." -NoNewline
        "=== build $svc ($(Get-Date -Format o))" | Add-Content $log
        # One command per service. --no-deps so a rail rebuild cannot drag caddy or the whole
        # stack in behind it.
        $upArgs = @($compose.Prefix) + $stackArgs + @('up', '-d', '--build', '--no-deps', $svc)
        & $compose.Exe @upArgs *>&1 | Add-Content $log
        $ok = ($LASTEXITCODE -eq 0)
        $secs = [int]((Get-Date) - $t0).TotalSeconds
        Write-Host $(if ($ok) { " ok ($secs s)" } else { " FAILED ($secs s)" })
        $results.Add([pscustomobject]@{ Service = $svc; Action = 'build'; Ok = $ok; Seconds = $secs })
    }
    foreach ($svc in $recreate) {
        $t0 = Get-Date
        Write-Host "`n=== recreate $svc ..." -NoNewline
        "=== recreate $svc ($(Get-Date -Format o))" | Add-Content $log
        $upArgs = @($compose.Prefix) + $stackArgs + @('up', '-d', '--no-deps', '--force-recreate', $svc)
        & $compose.Exe @upArgs *>&1 | Add-Content $log
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
$parkFailed = Invoke-DisabledStep
if ($failed.Count -gt 0 -or $parkFailed -gt 0) { exit 1 }
exit 0
