<#
.SYNOPSIS
  Installer apply: selections -> a deployed platform. Dry by default.

.DESCRIPTION
  The CLI seam over lib-apply.ps1, and the point where the three engines compose: it reads VRAM
  from the preflight engine, builds the plan with the model-plan engine, then runs the apply
  actions. It prints what it WOULD do and changes nothing until -Execute, and -Execute stops at
  any admin-needing step unless already elevated (the flow elevates once, up front).

  The container runtime: -RuntimeMode if given; else PLATFORM_RUNTIME in deploy\.env; else the
  one whose CLI lists the platform's containers (falling back to the first CLI installed), the
  same rule smoke-test.ps1 uses. A declaration contradicted by a running gateway elsewhere is
  followed, with a WARNING naming both. The env step records the runtime it was run for as
  PLATFORM_RUNTIME, so the next run of anything here is declared rather than inferred.

  With no -AdminPassword (the default is blank) deploy\.env gets PLATFORM_ADMIN_PASSWORD="", and
  the gateway, seeding the admin on its first run, generates a strong password and prints it once
  to its log.

  On a wsl box an unelevated -Execute also sets OLLAMA_HOST=0.0.0.0:11434 for the user, as
  install.ps1 does, so the containers reach the Ollama app; an elevated run, which may be another
  account, sets nothing and prints what to do (lib-apply Invoke-ApplyOllamaHost).

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
    [ValidateSet('', 'podman', 'desktop', 'wsl')]
    [string]$RuntimeMode = '',
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
    # The writers produce artifacts the CONSUMER can actually read, the action list is well-formed,
    # and each runtime's image and compose steps reach that runtime's own CLI. Safe: it writes only
    # to a temp dir, calls a real action's Test (a safe read) and never its Do; the routing block
    # runs Test and Do against stub CLIs only, and refuses to run if any stub is not in place.
    #
    # The roles.json block below asserts the broker's contract, not the writer's own output shape.
    # The version of this test that shipped asserted `$parsed.roles.recipe`, which passed only
    # BECAUSE the writer nested the map under a "roles" key that the broker cannot see -- it
    # printed "self-test ok" for the entire life of the bug. Every assertion here is stated in
    # terms of what services/broker/app/config.py overlay_roles() does with the file.
    $fail = 0
    # The broker's own _ROLE_NAME (app/config.py). A top-level key that fails this is DROPPED at
    # read time, in silence, so it has to fail here instead.
    #
    # Compared with the CASE-SENSITIVE operators (-cnotmatch, -cnotcontains) throughout. PowerShell's
    # -match and -contains ignore case; Python's re.fullmatch does not. With the case-insensitive
    # defaults this whole block waves through a key like "Recipe", which the broker drops on the
    # floor -- found by mutation-testing this very test, which passed clean until the operators were
    # made case-sensitive.
    $rolePattern = '^[a-z0-9][a-z0-9-]*$'
    # The LONG form of TEMP. $env:TEMP is the 8.3 short form on some boxes
    # (C:\Users\YOURNA~1.ME\AppData\Local\Temp), and Remove-Item cannot remove anything reached
    # through a short user-profile component: under both engines it throws "An object at the
    # specified path C:\Users\YOURNA~1.ME does not exist", a TERMINATING error that -ErrorAction
    # SilentlyContinue does not silence. So a self-test that had passed exited 1 from its own
    # cleanup and left the directory behind. New-Item, Set-Content and Test-Path take either form,
    # which is why only the cleanup broke. GetFullPath expands the short components on both engines.
    $tmp = Join-Path ([IO.Path]::GetFullPath($env:TEMP)) ("ai-apply-selftest-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null
    try {
        $plan = Get-ModelPlan -Rails @('recipe-book', 'terminal-fun') -VramMib 24564 -Installed @{}
        $planRoles = Get-PlanRoles -Plan $plan

        $rp = Join-Path $tmp 'roles.json'
        Write-RolesJson -Plan $plan -Path $rp | Out-Null
        $rolesText = Get-Content -Raw $rp
        $roleKeys = @()
        $notes = @()
        $parsed = $null
        try { $parsed = $rolesText | ConvertFrom-Json }
        catch { Write-Host "  FAIL  roles.json is not valid JSON: $_"; $fail++ }

        if ($parsed) {
            # Property NAMES, not property access. Under StrictMode a reference to a missing
            # property THROWS, so an absence check written as `-not $parsed.foo` inside a try/catch
            # gets swallowed and misreported as a JSON syntax error -- which is the second reason
            # the old assertion never spoke up.
            $top = @($parsed.PSObject.Properties.Name)
            $roleKeys = @($top | Where-Object { -not $_.StartsWith('_') })
            $notes = @($top | Where-Object { $_.StartsWith('_') })

            # REGRESSION GUARD -- the assertion whose absence let the nested shape ship.
            # overlay_roles() keeps TOP-LEVEL keys only and never descends into a wrapper, so
            # {"roles": {...}} does not read as no roles, it reads as one role NAMED "roles" whose
            # model pattern is a stringified dict. Nothing downstream rejects that: the rails get
            # the 24 GB defaults, /v1/roles lists a phantom, and the installer reports success.
            # Case-INsensitive on purpose, the one exception here: a "Roles" wrapper hides the map
            # just as thoroughly, and it is never a legal role name, so there is nothing to lose.
            if ($top -contains 'roles') {
                Write-Host "  FAIL  roles.json has a top-level 'roles' key: overlay_roles() reads only top-level keys, so the map is nested out of sight and reads back as one phantom role named 'roles'"
                $fail++
            }
            # Canary, hardcoded, so an empty plan cannot vacuously satisfy the set compare below.
            if ($roleKeys -cnotcontains 'recipe') {
                Write-Host "  FAIL  roles.json has no top-level 'recipe' role, so recipe-book's @recipe falls through to the 24 GB gemma4*:26b default"
                $fail++
            }
            foreach ($r in @($planRoles.Keys)) {
                if ($roleKeys -cnotcontains $r) {
                    Write-Host "  FAIL  plan role @$r -> '$($planRoles[$r])' is not a top-level key of roles.json, so the broker never sees that choice"
                    $fail++
                    continue
                }
                # And the VALUE. Key presence alone is the weaker half: the bug this engine had
                # was that the plan's CHOICES were discarded, and an audit proved the point by
                # emitting every role with the first role's model -- an image slot pointed at a
                # chat model -- with the self-test still reporting ok. -cne because a model
                # pattern is case-sensitive downstream (fnmatch against Ollama tags).
                if ($parsed.$r -cne $planRoles[$r]) {
                    Write-Host "  FAIL  roles.json maps @$r to '$($parsed.$r)' but the plan chose '$($planRoles[$r])', so the VRAM-sized choice was discarded on the way to disk"
                    $fail++
                }
            }
            foreach ($k in $roleKeys) {
                if ($k -cnotmatch $rolePattern) {
                    Write-Host "  FAIL  roles.json key '$k' does not match the broker's _ROLE_NAME $rolePattern, so overlay_roles() silently drops it"
                    $fail++
                }
                $v = $parsed.$k
                $vType = if ($null -eq $v) { 'null' } else { $v.GetType().Name }
                if ($v -isnot [string] -or -not $v) {
                    Write-Host "  FAIL  roles.json role @$k maps to a $vType, not a model-pattern string; overlay_roles() str()s the value, so this reaches the rail as garbage"
                    $fail++
                }
            }
        }

        # Encoding is part of the shape. config.py does Path.read_text(encoding="utf-8"), which
        # KEEPS a leading U+FEFF, so a BOM'd file makes json.loads raise and overlay_roles() returns
        # {} -- every role discarded, no error anywhere. `Set-Content -Encoding UTF8` writes a BOM
        # on Windows PowerShell 5.1 and none on PowerShell 7, so a self-test that only ever ran
        # under 7 would certify this the same way the old one certified the nesting. Asserted on the
        # BYTES: ConvertFrom-Json strips a BOM, so every check above passes straight through it.
        $bytes = [System.IO.File]::ReadAllBytes($rp)
        if ($bytes.Count -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
            Write-Host "  FAIL  roles.json begins with a UTF-8 BOM, which makes the broker's json.loads raise and overlay_roles() hand back an empty map"
            $fail++
        }

        # The write that lands on the LIVE roles.json is INLINED in Get-ApplyActions rather than
        # routed through Write-RolesJson (the action closures must capture strings, not call
        # functions). Two producers of one shape is how a shape fix lands on only half the paths,
        # so pin that the bytes on disk are the bytes the action would write.
        $inlineText = ConvertTo-RolesJson -Roles $planRoles
        if ($inlineText.TrimEnd() -ne $rolesText.TrimEnd()) {
            Write-Host "  FAIL  the inlined apply-action text differs from what Write-RolesJson put on disk, so one of the two write paths ships an unvalidated shape"
            $fail++
        }
        # Determinism is load-bearing: the 'roles' action's Test is a whole-text compare, so a
        # serialiser that followed hashtable insertion order would report "not done" on every
        # re-run and rewrite the file forever. Same map, opposite insertion order, same bytes.
        # [ordered], not @{}: a plain hashtable's enumeration order for a fixed key set is
        # insertion-INdependent on .NET Framework, so under Windows PowerShell 5.1 this guard
        # could not see a serialiser that had lost its sort -- proven by deleting `| Sort-Object`,
        # which fails under 7 and passes under 5.1. An ordered dictionary preserves insertion
        # order on both engines, which is the property being tested.
        $shuffled = [ordered]@{}
        foreach ($k in @($planRoles.Keys | Sort-Object -Descending)) { $shuffled[$k] = $planRoles[$k] }
        if ((ConvertTo-RolesJson -Roles $shuffled) -ne $inlineText) {
            Write-Host "  FAIL  ConvertTo-RolesJson output depends on hashtable insertion order, so the 'roles' action's text compare would never settle"
            $fail++
        }

        $ep = Join-Path $tmp '.env'
        Write-InstallerEnv -Rails @('recipe-book', 'terminal-fun') -AdminUser 'u' -AdminPassword 'p' -Path $ep | Out-Null
        $env = Get-Content -Raw $ep
        if ($env -match '\{\{') { Write-Host "  FAIL  .env still has unfilled {{placeholders}}"; $fail++ }
        if ($env -notmatch 'PLATFORM_ENABLED_APPS=recipe-book,terminal-fun') { Write-Host "  FAIL  .env ENABLED_APPS not filled"; $fail++ }

        $actions = Get-ApplyActions -Plan $plan -Rails @('recipe-book', 'terminal-fun') -AdminUser 'u' -AdminPassword 'p' -Mode desktop
        if (@($actions).Count -lt 4) { Write-Host "  FAIL  too few apply actions"; $fail++ }
        foreach ($a in $actions) {
            foreach ($f in 'Id', 'Label', 'Test', 'Do') {
                if (-not $a.PSObject.Properties.Name.Contains($f)) { Write-Host "  FAIL  action missing $f"; $fail++ }
            }
            # Skipped for the two runtime steps: their Tests ask a real CLI, and the routing block
            # below runs them against stubs instead.
            if ($a.Id -in 'image', 'compose') { continue }
            try { $null = & $a.Test } catch { Write-Host "  FAIL  action '$($a.Id)' Test threw: $_"; $fail++ }
        }

        # Runtime routing. The image and compose steps must reach the runtime's OWN CLI: a Podman
        # box has podman.exe and docker-compose.exe and no docker.exe, and this engine used to call
        # `docker` for all four commands. Each step's Test AND Do run here, against global stub
        # functions (a function wins command resolution over an exe), with -Root a temp dir so not
        # even a path points at the real tree. Nothing runs unless every CLI resolves to its stub.
        $global:__applyCalls = New-Object System.Collections.Generic.List[string]
        $global:__applyImage = $false
        $stubNames = @('podman', 'docker', 'docker-compose', 'wsl.exe')
        foreach ($n in $stubNames) {
            Set-Item -Path "function:global:$n" -Value {
                $argv = @(foreach ($x in $args) { foreach ($y in @($x)) { [string]$y } })
                $global:__applyCalls.Add($MyInvocation.MyCommand.Name + ' ' + ($argv -join ' '))
                $global:LASTEXITCODE = 0
                if ($argv -contains 'inspect' -and -not $global:__applyImage) { $global:LASTEXITCODE = 1 }
                if ($argv -contains 'ps') { 'platform-gateway-1' }
            }
        }
        try {
            # -CommandType Function, not .CommandType on the result: under StrictMode a CLI that is
            # not there at all yields $null, and reading a property of it threw instead of reporting.
            $unstubbed = @($stubNames | Where-Object { -not (Get-Command $_ -CommandType Function -ErrorAction SilentlyContinue) })
            if ($unstubbed.Count) {
                Write-Host "  FAIL  runtime routing not tested: $($unstubbed -join ', ') did not resolve to a stub"
                $fail++
            }
            else {
                $routeRoot = Join-Path $tmp 'route'
                New-Item -ItemType Directory -Force -Path (Join-Path $routeRoot 'deploy') | Out-Null
                $expect = @{
                    # Build, not Pull: the gateway image is a local build in no registry.
                    podman  = @{ Image = 'podman image inspect'; Build = 'docker-compose --env-file'; Ps = 'podman ps --filter name=platform-'
                                 Up = 'docker-compose --env-file'; Forbidden = '^(docker|wsl\.exe) ' }
                    desktop = @{ Image = 'docker image inspect'; Build = 'docker compose --env-file'; Ps = 'docker ps --filter name=platform-'
                                 Up = 'docker compose --env-file'; Forbidden = '^(podman|docker-compose|wsl\.exe) ' }
                    wsl     = @{ Image = 'wsl.exe docker image inspect'; Build = 'wsl.exe docker compose --env-file /mnt/'
                                 Ps = 'wsl.exe docker ps --filter name=platform-'
                                 Up = 'wsl.exe docker compose --env-file /mnt/'; Forbidden = '^(podman|docker|docker-compose) ' }
                }
                foreach ($mode in 'podman', 'desktop', 'wsl') {
                    $global:__applyCalls.Clear()
                    $acts = Get-ApplyActions -Plan $plan -Rails @('terminal-fun') -AdminUser 'u' -AdminPassword 'p' -Mode $mode -Root $routeRoot
                    $img = $acts | Where-Object Id -eq 'image'
                    $up = $acts | Where-Object Id -eq 'compose'
                    $global:__applyImage = $false
                    $absent = & $img.Test
                    $global:__applyImage = $true
                    $present = & $img.Test
                    & $img.Do
                    $running = & $up.Test
                    & $up.Do
                    $seen = @($global:__applyCalls)
                    $e = $expect[$mode]
                    foreach ($k in 'Image', 'Ps', 'Up') {
                        if (-not @($seen | Where-Object { $_.StartsWith($e[$k]) }).Count) {
                            Write-Host "  FAIL  [$mode] no '$($e[$k]) ...' call; saw: $($seen -join ' | ')"; $fail++
                        }
                    }
                    if (-not @($seen | Where-Object { $_.StartsWith($e.Build) -and $_.EndsWith(' build gateway') }).Count) {
                        Write-Host "  FAIL  [$mode] the image step did not build the gateway ('$($e.Build) ... build gateway'); saw: $($seen -join ' | ')"; $fail++
                    }
                    if (@($seen | Where-Object { $_ -match ' pull ' -or $_ -match ' pull$' }).Count) {
                        Write-Host "  FAIL  [$mode] something pulled the gateway image, which is in no registry: $($seen -join ' | ')"; $fail++
                    }
                    $wrong = @($seen | Where-Object { $_ -match $e.Forbidden })
                    if ($wrong.Count) { Write-Host "  FAIL  [$mode] called another runtime's CLI: $($wrong -join ' | ')"; $fail++ }
                    if ($absent -or -not $present) {
                        Write-Host "  FAIL  [$mode] the image Test does not follow the CLI's exit code (absent=$absent present=$present)"; $fail++
                    }
                    if (-not $running) { Write-Host "  FAIL  [$mode] the compose Test did not see the listed platform container"; $fail++ }
                    # The compose step's call, not the image step's `build gateway` on the same prefix.
                    $upCall = @($seen | Where-Object { $_.StartsWith($e.Up) -and -not $_.EndsWith(' build gateway') })[0]
                    if ($upCall -and $upCall -notmatch '--profile terminal-fun up -d$') {
                        Write-Host "  FAIL  [$mode] compose up lost its profiles or verb: $upCall"; $fail++
                    }
                }
            }
        }
        finally {
            foreach ($n in $stubNames) { Remove-Item -Path "function:global:$n" -ErrorAction SilentlyContinue }
        }
        if ($fail -eq 0) {
            Write-Host "  runtime routing ok (podman, desktop, wsl: image + compose steps reach their own CLI)"
        }
        if ($fail -eq 0) {
            Write-Host ("  self-test ok ({0} actions, roles.json flat with {1} top-level roles + {2} annotation(s), writers valid)" -f @($actions).Count, $roleKeys.Count, $notes.Count)
        }
    } finally {
        Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
    }
    return $fail
}


if ($SelfTest) { exit (Invoke-ApplySelfTest) }

$vram = Resolve-Vram -VramGb $VramGb
$plan = Get-ModelPlan -Rails $Rails -VramMib $vram
$runtimeWhy = 'from -RuntimeMode'
$runtimeWarning = ''
if (-not $RuntimeMode) {
  $pick = Resolve-PlatformRuntime; $RuntimeMode = $pick.Mode; $runtimeWhy = $pick.Reason; $runtimeWarning = $pick.Warning
}
$actions = Get-ApplyActions -Plan $plan -Rails $Rails -AdminUser $AdminUser -AdminPassword $AdminPassword -Mode $RuntimeMode

$mode = if ($Execute) { 'EXECUTE' } else { 'dry run (nothing will change; add -Execute to apply)' }
Write-Host "AI-Platform apply -- $mode`n  rails: $($Rails -join ', ')   card: $([math]::Round($vram/1024,1)) GB"
Write-Host "  runtime: $RuntimeMode ($runtimeWhy)"
if ($runtimeWarning) { Write-Host "  WARNING: $runtimeWarning" -ForegroundColor Yellow }
Write-Host ''
$rc = Invoke-Apply -Actions $actions -Execute:$Execute -Mode $RuntimeMode
exit ([math]::Min($rc, 1))
