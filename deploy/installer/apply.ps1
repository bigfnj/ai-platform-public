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
    # The writers produce artifacts the CONSUMER can actually read, and the action list is
    # well-formed. Pure and safe: it writes only to a temp dir and calls each action's Test (a safe
    # read), never a Do.
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
    $tmp = Join-Path $env:TEMP ("ai-apply-selftest-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
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

        $actions = Get-ApplyActions -Plan $plan -Rails @('recipe-book', 'terminal-fun') -AdminUser 'u' -AdminPassword 'p'
        if (@($actions).Count -lt 4) { Write-Host "  FAIL  too few apply actions"; $fail++ }
        foreach ($a in $actions) {
            foreach ($f in 'Id', 'Label', 'Test', 'Do') {
                if (-not $a.PSObject.Properties.Name.Contains($f)) { Write-Host "  FAIL  action missing $f"; $fail++ }
            }
            try { $null = & $a.Test } catch { Write-Host "  FAIL  action '$($a.Id)' Test threw: $_"; $fail++ }
        }
        if ($fail -eq 0) {
            Write-Host ("  self-test ok ({0} actions, roles.json flat with {1} top-level roles + {2} annotation(s), writers valid)" -f @($actions).Count, $roleKeys.Count, $notes.Count)
        }
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
