# The installer apply engine: turn selections + a model plan into a deployed platform.
#
# The connective tissue between the two detection engines (lib-preflight, lib-modelplan) and a
# running stack. Two halves:
#
#   WRITERS   pure file generation, no side effects on anything but the target path:
#             Write-RolesJson (the adaptive replacement for the static roles.lean.json --
#             it computes role->model from the VRAM-sized plan) and Write-InstallerEnv.
#   ACTIONS   the orchestration, as a list of idempotent steps. Each carries a Test (is this
#             already done?) and a Do, and Invoke-Apply runs them DRY BY DEFAULT: it prints what
#             it would do and changes nothing until -Execute. Every executed step re-runs its
#             Test afterward, because a step that ran is not the same as a step that worked.
#
# Unlike the preflight board, apply actions are executed host-side and never round-trip through
# JSON, so they legitimately carry scriptblocks. install.ps1 (today a 746-line monolith with its
# own prereq doctor and a static roles file) should adopt these so there is one apply path, the
# same way smoke-test should adopt the preflight checks. Noted in docs/installer-design.md.

Set-StrictMode -Version Latest

$__ap_here = Split-Path -Parent $MyInvocation.MyCommand.Path
$__ap_repo = (Resolve-Path (Join-Path $__ap_here '..\..')).Path


# --- writers (pure) -------------------------------------------------------------------------

function ConvertTo-RolesJson {
    # Deterministic serialiser: stable key order so a re-run produces byte-identical output and
    # the "already done?" check is a simple text compare.
    param([Parameter(Mandatory)][hashtable]$Roles)
    $sb = [System.Text.StringBuilder]::new()
    [void]$sb.AppendLine('{')
    [void]$sb.AppendLine('  "roles": {')
    $keys = @($Roles.Keys | Sort-Object)
    for ($i = 0; $i -lt $keys.Count; $i++) {
        $k = $keys[$i]
        $comma = if ($i -lt $keys.Count - 1) { ',' } else { '' }
        [void]$sb.AppendLine(('    "{0}": "{1}"{2}' -f $k, $Roles[$k], $comma))
    }
    [void]$sb.AppendLine('  }')
    [void]$sb.AppendLine('}')
    $sb.ToString()
}

function Get-PlanRoles {
    # role -> model from a model plan. chosen where a fitting model was found, else the roles.json
    # default: a role that maps to a too-big model shows a red chip (honest), which beats an
    # unmapped role 500ing the rail. The plan's own warnings already flagged the no-fit case.
    param([Parameter(Mandatory)][object]$Plan)
    $out = @{}
    foreach ($rp in $Plan.roles) {
        $model = if ($rp.chosen) { $rp.chosen } else { $rp.default }
        if ($model) { $out[$rp.role] = $model }
    }
    $out
}

function Write-RolesJson {
    param(
        [Parameter(Mandatory)][object]$Plan,
        [Parameter(Mandatory)][string]$Path
    )
    $text = ConvertTo-RolesJson -Roles (Get-PlanRoles -Plan $Plan)
    $dir = Split-Path -Parent $Path
    if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    Set-Content -Path $Path -Value $text -Encoding UTF8 -NoNewline
    $Path
}

function Get-InstallerEnvText {
    # The .env contents from env.lean.example with the wizard's selections filled in. Pure: returns
    # the string, so the apply action can capture the RESULT and compare/write it without calling
    # back into this function from inside a closure (GetNewClosure captures variables, not
    # functions, so a closure that called a helper would fail to resolve it at run time).
    param(
        [Parameter(Mandatory)][string[]]$Rails,
        [Parameter(Mandatory)][string]$AdminUser,
        [Parameter(Mandatory)][string]$AdminPassword,
        [string]$Template
    )
    if (-not $Template) { $Template = Join-Path $__ap_here 'env.lean.example' }
    $text = Get-Content -Raw -Path $Template
    $text = $text.Replace('{{ADMIN_USER}}', $AdminUser)
    $text = $text.Replace('{{ADMIN_PASSWORD}}', $AdminPassword)
    $text = $text.Replace('{{ENABLED_APPS}}', ($Rails -join ','))
    $text
}

function Write-InstallerEnv {
    param(
        [Parameter(Mandatory)][string[]]$Rails,
        [Parameter(Mandatory)][string]$AdminUser,
        [Parameter(Mandatory)][string]$AdminPassword,
        [Parameter(Mandatory)][string]$Path,
        [string]$Template
    )
    $text = Get-InstallerEnvText -Rails $Rails -AdminUser $AdminUser -AdminPassword $AdminPassword -Template $Template
    Set-Content -Path $Path -Value $text -Encoding UTF8
    $Path
}


# --- actions --------------------------------------------------------------------------------

function New-ApplyAction {
    param(
        [Parameter(Mandatory)][string]$Id,
        [Parameter(Mandatory)][string]$Label,
        [Parameter(Mandatory)][scriptblock]$Test,   # $true when already done
        [Parameter(Mandatory)][scriptblock]$Do,
        [bool]$NeedsAdmin = $false
    )
    [PSCustomObject]@{ Id = $Id; Label = $Label; Test = $Test; Do = $Do; NeedsAdmin = $NeedsAdmin }
}

function Get-ApplyActions {
    <#
      The ordered, idempotent step list for one deployment. Config first (cheap, local), then the
      slow network/host work, then bring the stack up. Each Test is a safe read so the runner can
      report "already done" without touching anything.
    #>
    param(
        [Parameter(Mandatory)][object]$Plan,
        [Parameter(Mandatory)][string[]]$Rails,
        [Parameter(Mandatory)][string]$AdminUser,
        [Parameter(Mandatory)][string]$AdminPassword,
        [string]$Root = $__ap_repo,
        [string]$Image = 'platform-gateway-bundled:latest'
    )
    $rolesPath = Join-Path $Root 'services\broker\roles.json'
    $envPath = Join-Path $Root 'deploy\.env'
    $actions = @()

    # Precompute the file contents HERE, where the helper functions are in scope, and let the
    # closures capture the resulting STRINGS. GetNewClosure snapshots variables, not functions,
    # so a Test/Do that called ConvertTo-RolesJson itself would fail to resolve it at run time.
    $rolesText = ConvertTo-RolesJson -Roles (Get-PlanRoles -Plan $Plan)
    $envText = Get-InstallerEnvText -Rails $Rails -AdminUser $AdminUser -AdminPassword $AdminPassword

    $actions += New-ApplyAction -Id 'roles' -Label 'Write broker roles.json from the model plan' -Test {
        (Test-Path $rolesPath) -and ((Get-Content -Raw -Path $rolesPath).TrimEnd() -eq $rolesText.TrimEnd())
    }.GetNewClosure() -Do {
        $dir = Split-Path -Parent $rolesPath
        if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
        Set-Content -Path $rolesPath -Value $rolesText -Encoding UTF8 -NoNewline
    }.GetNewClosure()

    $actions += New-ApplyAction -Id 'env' -Label 'Write deploy\.env from selections' -Test {
        (Test-Path $envPath) -and ((Get-Content -Raw -Path $envPath).TrimEnd() -eq $envText.TrimEnd())
    }.GetNewClosure() -Do {
        $dir = Split-Path -Parent $envPath
        if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
        Set-Content -Path $envPath -Value $envText -Encoding UTF8
    }.GetNewClosure()

    $actions += New-ApplyAction -Id 'image' -Label "Pull/build the gateway image ($Image)" -Test {
        $null = & docker image inspect $Image 2>$null; $LASTEXITCODE -eq 0
    }.GetNewClosure() -Do {
        & docker pull $Image 2>&1 | Out-Host
    }.GetNewClosure()

    foreach ($m in @($Plan.to_pull | Where-Object { $_.backend -eq 'ollama' })) {
        $model = $m.model
        $actions += New-ApplyAction -Id "model:$model" -Label "Pull model $model ($($m.approx_gb) GB)" -Test {
            $names = @()
            try { $names = (Invoke-RestMethod 'http://127.0.0.1:11434/api/tags' -TimeoutSec 5).models.name } catch { }
            [bool](@($names | Where-Object { $_ -like $model }).Count)
        }.GetNewClosure() -Do {
            & ollama pull $model 2>&1 | Out-Host
        }.GetNewClosure()
    }

    if ($Plan.needs_media_venv) {
        # venvs live at <ai-work>/venvs, which is the repo's parent's parent (the broker resolves
        # the same _AI_WORK = _REPO_ROOT.parent.parent). One Split-Path too few pointed at
        # projects/venvs, which never exists, so the check always said "not provisioned".
        $venvPy = Join-Path (Split-Path (Split-Path $Root -Parent) -Parent) 'venvs\media-venv\Scripts\python.exe'
        $actions += New-ApplyAction -Id 'media-venv' -Label 'Provision the GPU media venv (torch + diffusers)' -Test {
            Test-Path $venvPy
        }.GetNewClosure() -Do {
            $script = Join-Path $Root 'deploy\install-native.ps1'
            if (Test-Path $script) { & $script 2>&1 | Out-Host } else { throw "install-native.ps1 not found" }
        }.GetNewClosure() -NeedsAdmin $true
    }

    $actions += New-ApplyAction -Id 'services' -Label 'Install the native broker + Ollama services (NSSM)' -Test {
        [bool](Get-Service platform-broker -ErrorAction SilentlyContinue)
    }.GetNewClosure() -Do {
        $script = Join-Path $Root 'deploy\install-services.ps1'
        if (Test-Path $script) { & $script 2>&1 | Out-Host } else { throw "install-services.ps1 not found" }
    }.GetNewClosure() -NeedsAdmin $true

    $actions += New-ApplyAction -Id 'compose' -Label "Bring the stack up (profiles: $($Rails -join ', '))" -Test {
        $running = @()
        try { $running = & docker ps --filter 'name=platform-' --format '{{.Names}}' 2>$null } catch { }
        [bool](@($running).Count)
    }.GetNewClosure() -Do {
        $compose = Join-Path $Root 'deploy\installer\docker-compose.installer.yml'
        $profileArgs = @(); foreach ($r in $Rails) { $profileArgs += @('--profile', $r) }
        & docker compose -f $compose @profileArgs up -d 2>&1 | Out-Host
    }.GetNewClosure()

    $actions
}


function Invoke-Apply {
    # Dry by default. Each step: if already done, skip; else (dry) announce, or (execute) do it
    # then RE-TEST and report the re-test, not the exit code.
    param(
        [Parameter(Mandatory)][object[]]$Actions,
        [switch]$Execute
    )
    $elevated = (New-Object Security.Principal.WindowsPrincipal(
        [Security.Principal.WindowsIdentity]::GetCurrent())).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
    $failed = 0
    foreach ($a in $Actions) {
        $done = $false
        try { $done = [bool](& $a.Test) } catch { $done = $false }
        if ($done) { Write-Host ("  skip  {0}  (already done)" -f $a.Label); continue }
        if (-not $Execute) {
            $adm = if ($a.NeedsAdmin) { ' (needs admin)' } else { '' }
            Write-Host ("  would {0}{1}" -f $a.Label, $adm)
            continue
        }
        if ($a.NeedsAdmin -and -not $elevated) {
            Write-Host ("  BLOCK {0}  (needs admin; elevate once before -Execute)" -f $a.Label); $failed++; continue
        }
        Write-Host ("  run   {0} ..." -f $a.Label)
        try { & $a.Do } catch { Write-Host "        error: $_" }
        $ok = $false
        try { $ok = [bool](& $a.Test) } catch { $ok = $false }
        if ($ok) { Write-Host "        ok" } else { Write-Host "        FAILED (re-check false)"; $failed++ }
    }
    $failed
}
