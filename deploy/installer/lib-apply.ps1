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

# Get-ComposeFiles (the -f list, box-local override included) lives in lib-runtime, so startup, the
# installer and this engine cannot disagree about which files make up the stack. So do
# Invoke-Compose and Invoke-VolumeCli, which pick the CLI per runtime: this engine used to call
# `docker` directly, and a Podman box has docker-compose.exe and podman.exe but no docker.exe, so
# its image and compose steps could not run there at all. Loaded the way lib-preflight loads it:
# definitions only, nothing runs at dot-source time.
$__ap_runtime = Join-Path $__ap_here 'lib-runtime.ps1'
if (-not (Get-Command Get-ComposeFiles -ErrorAction SilentlyContinue) -and (Test-Path $__ap_runtime)) { . $__ap_runtime }


# --- writers (pure) -------------------------------------------------------------------------

function ConvertTo-RolesJson {
    # Deterministic serialiser for the broker's roles.json: stable key order so a re-run produces
    # byte-identical output and the "already done?" check is a simple text compare.
    #
    # The map is FLAT at the top level, because that is the only shape the consumer reads.
    # services/broker/app/config.py's overlay_roles() json.loads() the file and keeps every
    # TOP-LEVEL key matching _ROLE_NAME ([a-z0-9][a-z0-9-]*) as role -> str(value); it never
    # descends into a wrapper. This writer used to nest the map under a "roles" key, so the broker
    # read back exactly ONE role, literally named "roles", whose model pattern was a stringified
    # dict. Every VRAM-sized choice the plan made was discarded: @recipe fell through to the 24 GB
    # gemma4*:26b default on the card the plan had just downshifted, and GET /v1/roles, the admin
    # Rails picker and the startup audit all advertised the phantom role. services/broker/roles.json
    # and roles.lean.json are both flat; matching them also means a later admin repoint through
    # set_role() (read-modify-write of the same flat dict) merges instead of colliding.
    #
    # Hand-rolled StringBuilder rather than ConvertTo-Json on purpose: ConvertTo-Json pins neither
    # key order nor escaping, and both are load-bearing for the byte-identical re-run.
    param([Parameter(Mandatory)][hashtable]$Roles)
    # An annotation, not a role: overlay_roles()'s leading-[a-z0-9] filter drops any "_"-prefixed
    # key, which is the same licence roles.lean.json's _comment / _per_rail keys use. Static prose
    # deliberately -- a timestamp, host name or VRAM figure here would break the byte-identical
    # re-run that the apply action's whole-text Test depends on.
    $noteKey = '_generated_by'
    $note = 'ai-platform installer (deploy/installer/apply.ps1), generated from the VRAM-sized model plan. Flat top-level role -> model-pattern map, which is the shape app/config.py overlay_roles() reads; keys prefixed with _ are annotations and the broker filters them out. Rewritten on every apply, so prefer repointing a role through Admin > Rails (set_role) over hand-editing.'
    $keys = @($Roles.Keys | Where-Object { $_ -ne $noteKey } | Sort-Object)
    $noteComma = if ($keys.Count) { ',' } else { '' }
    $sb = [System.Text.StringBuilder]::new()
    [void]$sb.AppendLine('{')
    [void]$sb.AppendLine(('  "{0}": "{1}"{2}' -f $noteKey, $note, $noteComma))
    for ($i = 0; $i -lt $keys.Count; $i++) {
        $k = $keys[$i]
        $comma = if ($i -lt $keys.Count - 1) { ',' } else { '' }
        [void]$sb.AppendLine(('  "{0}": "{1}"{2}' -f $k, $Roles[$k], $comma))
    }
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

# The roles.json to write: the box's CURRENT map (Read-RoleMap: the roles the broker reads now)
# with the plan's choices laid over it. The plan holds only the per-rail slots it sized, and
# writing it ALONE dropped every generic role the lean map pins to gemma3:4b (chat, chat-fast,
# chat-large, reasoning, code, vision and three RAG roles): each fell back to its 24 GB
# DEFAULT_ROLES entry on the card the plan had just downshifted.
function Get-RolesToWrite {
    param([Parameter(Mandatory)][object]$Plan, [Parameter(Mandatory)][string]$Path)
    $out = @{}
    $cur = Read-RoleMap -Path $Path
    foreach ($k in @($cur.Keys)) { $out[$k] = $cur[$k] }
    $pr = Get-PlanRoles -Plan $Plan
    foreach ($k in @($pr.Keys)) { $out[$k] = $pr[$k] }
    return $out
}

function Write-RolesJson {
    param(
        [Parameter(Mandatory)][object]$Plan,
        [Parameter(Mandatory)][string]$Path
    )
    $text = ConvertTo-RolesJson -Roles (Get-RolesToWrite -Plan $Plan -Path $Path)
    $dir = Split-Path -Parent $Path
    if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    # UTF-8 with NO BOM, via .NET rather than Set-Content, because `Set-Content -Encoding UTF8`
    # means two different things: Windows PowerShell 5.1 writes a BOM, PowerShell 7 does not. A BOM
    # is the other shape this consumer cannot read -- config.py does Path.read_text(encoding="utf-8"),
    # which KEEPS a leading U+FEFF, so json.loads raises and overlay_roles() swallows the
    # JSONDecodeError and returns {}. Under 5.1 the installer therefore delivered NO roles at all,
    # in silence. `-Encoding utf8NoBOM` would be the one-word fix but is PS7-only, and these engines
    # have to run under both. WriteAllText also appends nothing, matching the old -NoNewline.
    [System.IO.File]::WriteAllText($Path, $text, [System.Text.UTF8Encoding]::new($false))
    $Path
}

function Get-InstallerEnvText {
    # The .env contents from env.lean.example with the wizard's selections filled in. Pure: returns
    # the string, so the apply action can capture the RESULT and compare/write it without calling
    # back into this function from inside a closure (GetNewClosure captures variables, not
    # functions, so a closure that called a helper would fail to resolve it at run time).
    # A blank -AdminPassword is apply.ps1's default and a designed value: deploy\.env then holds
    # PLATFORM_ADMIN_PASSWORD="", and the gateway generates a password and prints it once. So the
    # three password parameters here take [AllowEmptyString()]; Mandatory alone refused it.
    param(
        [Parameter(Mandatory)][string[]]$Rails,
        [Parameter(Mandatory)][string]$AdminUser,
        [Parameter(Mandatory)][AllowEmptyString()][string]$AdminPassword,
        [string]$Template
    )
    if (-not $Template) { $Template = Join-Path $__ap_here 'env.lean.example' }
    # -Encoding UTF8: the template is UTF-8 without a BOM (it carries em dashes and arrows), which
    # 5.1 otherwise decodes as ANSI. That broke the env step's re-check once the file was written
    # without a BOM: the Test re-read it as ANSI too, and the two never compared equal.
    $text = Get-Content -Raw -Encoding UTF8 -Path $Template
    # lib-runtime's filler, the one install.ps1 uses: the name and password as quoted values, so
    # compose reads back what was typed (a bare `P@$$w0rd` reached the gateway as `P@$w0rd`).
    Expand-LeanEnvTemplate -Text $text -AdminUser $AdminUser -AdminPassword $AdminPassword -EnabledApps ($Rails -join ',')
}

function Write-InstallerEnv {
    param(
        [Parameter(Mandatory)][string[]]$Rails,
        [Parameter(Mandatory)][string]$AdminUser,
        [Parameter(Mandatory)][AllowEmptyString()][string]$AdminPassword,   # blank: the gateway generates one
        [Parameter(Mandatory)][string]$Path,
        [string]$Template
    )
    $text = Get-InstallerEnvText -Rails $Rails -AdminUser $AdminUser -AdminPassword $AdminPassword -Template $Template
    # The same no-BOM writer the apply step uses, so the self-test exercises the real encoding.
    [System.IO.File]::WriteAllText($Path, $text, [System.Text.UTF8Encoding]::new($false))
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
        [Parameter(Mandatory)][AllowEmptyString()][string]$AdminPassword,   # blank: the gateway generates one
        # The container runtime: podman | desktop | wsl. Required rather than defaulted, because a
        # silent default is exactly how this engine came to assume `docker` on every box.
        # apply.ps1 resolves it with Resolve-PlatformRuntime.
        [Parameter(Mandatory)][ValidateSet('podman', 'desktop', 'wsl')][string]$Mode,
        [string]$Root = $__ap_repo,
        [string]$Image = 'platform-gateway-bundled:latest'
    )
    # The box's OWN role map, deploy\local\roles.json (git-ignored), which the 'services' step
    # registers the broker to read (lib-runtime Get-LeanBrokerFiles says why). Never the tracked
    # services\broker\roles.json: this step used to write that, and the next update reset it to the
    # 24 GB map. The map the plan is laid over is the one the broker reads now: the box-local file
    # once it exists, else the tracked one, which on a box installed before it holds the lean map.
    $brokerFiles = Get-LeanBrokerFiles -Root $Root
    $rolesPath = $brokerFiles.Roles
    $rolesBase = if (Test-Path -LiteralPath $rolesPath) { $rolesPath } else { $brokerFiles.TrackedRoles }
    $envPath = Join-Path $Root 'deploy\.env'
    $actions = @()

    # Precompute the file contents HERE, where the helper functions are in scope, and let the
    # closures capture the resulting STRINGS. GetNewClosure snapshots variables, not functions,
    # so a Test/Do that called ConvertTo-RolesJson itself would fail to resolve it at run time.
    $rolesText = ConvertTo-RolesJson -Roles (Get-RolesToWrite -Plan $Plan -Path $rolesBase)
    # Carried over: keys this box added that the template does not define (the compose override,
    # a broker token). Rewriting .env without them dropped the override, and the compose step
    # below then brought the stack up without it.
    $envText = Add-EnvCarryOver -ExistingPath $envPath `
        -NewText (Get-InstallerEnvText -Rails $Rails -AdminUser $AdminUser -AdminPassword $AdminPassword)
    # Declare the runtime this apply drives, so startup, the smoke test and the next apply read it
    # rather than infer it. After the carry-over, on purpose: the template only COMMENTS the key,
    # so a box's own line is carried over and then replaced by the runtime actually used now.
    $envText = Set-EnvTextValue -Text $envText -Key 'PLATFORM_RUNTIME' -Value $Mode
    # The broker's shared token: the box's own (carried over above), or a new one. The 'services'
    # step registers the broker with this value and compose hands it to the rails and the gateway,
    # so the two halves of the handshake come from the one line this step writes.
    $brokerToken = ([string](ConvertFrom-DotEnvText -Text $envText)['BROKER_AUTH_TOKEN']).Trim()
    if (-not $brokerToken) {
        $brokerToken = New-BrokerToken
        $envText = Set-EnvTextValue -Text $envText -Key 'BROKER_AUTH_TOKEN' -Value $brokerToken
    }
    $brokerSpec = Get-BrokerServiceSpec -BindHost (Get-BrokerBindHost -Mode $Mode) -Token $brokerToken -Root $Root

    $actions += New-ApplyAction -Id 'roles' -Label 'Write the box''s broker role map (deploy\local\roles.json) from the model plan' -Test {
        # -ceq: -eq ignores case, the broker's re.fullmatch does not, so a hand-edited "Recipe" key
        # (which the broker drops) read as "already done".
        (Test-Path $rolesPath) -and ((Get-Content -Raw -Encoding UTF8 -Path $rolesPath).TrimEnd() -ceq $rolesText.TrimEnd())
    }.GetNewClosure() -Do {
        $dir = Split-Path -Parent $rolesPath
        if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
        # UTF-8 no BOM, same reason as Write-RolesJson: 5.1's Set-Content -Encoding UTF8 adds a BOM
        # that makes the broker's json.loads raise, and overlay_roles() then returns {} in silence.
        # A .NET static call is safe inside a closure where a helper FUNCTION would not be.
        [System.IO.File]::WriteAllText($rolesPath, $rolesText, [System.Text.UTF8Encoding]::new($false))
    }.GetNewClosure()

    $actions += New-ApplyAction -Id 'env' -Label 'Write deploy\.env from selections' -Test {
        (Test-Path $envPath) -and ((Get-Content -Raw -Encoding UTF8 -Path $envPath).TrimEnd() -ceq $envText.TrimEnd())
    }.GetNewClosure() -Do {
        $dir = Split-Path -Parent $envPath
        if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
        # Not Set-Content -Encoding UTF8: on 5.1 that writes a BOM, which glues itself to the first
        # variable name for every consumer (compose, the gateway, startup's own .env reads).
        [System.IO.File]::WriteAllText($envPath, $envText, [System.Text.UTF8Encoding]::new($false))
    }.GetNewClosure()

    # The runtime helpers, held in variables for the same reason as $composeFiles below: a
    # GetNewClosure block sees variables, not functions. Invoke-VolumeCli takes any runtime
    # subcommand (`image inspect`, `pull`, `ps`), not only `volume`; only the entry point differs.
    $runtimeCli = ${function:Invoke-VolumeCli}
    $composeCli = ${function:Invoke-Compose}
    $toWslPath = ${function:ConvertTo-WslPath}

    # The --env-file and -f arguments every compose call here needs, computed when a step RUNS (the
    # 'env' step above may just have rewritten deploy\.env, override included). Get-ComposeFiles is
    # held in a variable because the closures can see variables only.
    $composeFiles = ${function:Get-ComposeFiles}
    $compose = Join-Path $Root 'deploy\installer\docker-compose.installer.yml'
    $composeBase = {
        $files = & $composeFiles -EnvFile $envPath -Compose $compose
        if ($files.Missing) {
            Write-Host "WARNING: PLATFORM_COMPOSE_OVERRIDE names '$($files.Missing)', which does not exist; composing without it."
        }
        # wsl's docker reads Linux paths; the other two take the Windows ones as they are.
        $envArg = $envPath; $fileList = @($files.Files)
        if ($Mode -eq 'wsl') { $envArg = & $toWslPath $envPath; $fileList = @($fileList | ForEach-Object { & $toWslPath $_ }) }
        $fileArgs = @(); foreach ($f in $fileList) { $fileArgs += @('-f', $f) }
        # --env-file is required. Without it compose looks for .env in its project directory,
        # deploy\installer\, where there is none, and every ${VAR} in the file resolves empty.
        , (@('--env-file', $envArg) + $fileArgs)
    }.GetNewClosure()

    # BUILT, not pulled: platform-gateway-bundled:latest is a local build that is in no registry, so
    # the `pull` this step used to run failed on every fresh box, and apply -Execute exited 1 on that
    # spurious failure while the compose step built the image anyway.
    $actions += New-ApplyAction -Id 'image' -Label "Build the gateway image ($Image)" -Test {
        $null = & $runtimeCli -CliArgs @('image', 'inspect', $Image) -Mode $Mode
        $LASTEXITCODE -eq 0
    }.GetNewClosure() -Do {
        & $composeCli -Arguments ((& $composeBase) + @('build', 'gateway')) -Mode $Mode -NoLog | Out-Host
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

    # No media-venv step. One used to be added whenever the plan picked an image model, and it ran
    # deploy\install-native.ps1, which does not exist (it lives in deploy\installer\, takes a mandatory
    # -PlatformRoot, and builds only the torch-free broker venv), so it failed on every plan with
    # recipe-book. Nothing in the lean path provisions a media venv, and nothing there would use one:
    # the lean broker runs with BROKER_MEDIA_ENABLED=false. Show-ModelPlan says so whenever the plan
    # picks an image model, rather than this list carrying a step that cannot succeed.

    # The LEAN broker service, from the same installer\install-native.ps1 install.ps1 runs. This step
    # used to run deploy\install-services.ps1, the FULL stack's script, with no flags: a second Ollama
    # service on :11434 beside the Ollama app, a course-builder service, and a throw when .venv was
    # absent. "Done" is now the registered service matching this plan's bind address and token
    # (Test-BrokerServiceCurrent), not just a service of that name existing: a broker registered
    # before the token existed is re-registered, not skipped.
    $serviceCurrent = ${function:Test-BrokerServiceCurrent}
    $bindHost = Get-BrokerBindHost -Mode $Mode
    $actions += New-ApplyAction -Id 'services' -Label "Install the native broker service (NSSM; ${bindHost}:11500, token required)" -Test {
        & $serviceCurrent -Spec $brokerSpec
    }.GetNewClosure() -Do {
        $script = Join-Path $Root 'deploy\installer\install-native.ps1'
        if (-not (Test-Path $script)) { throw "install-native.ps1 not found at $script" }
        $nativeArgs = @{ PlatformRoot = $Root; SkipOllama = $true; BrokerHost = $bindHost }
        if ($Mode -eq 'wsl') { $nativeArgs['OpenWslFirewall'] = $true }
        & $script @nativeArgs 2>&1 | ForEach-Object { [string]$_ } | Out-Host
    }.GetNewClosure() -NeedsAdmin $true

    $actions += New-ApplyAction -Id 'compose' -Label "Bring the stack up (profiles: $($Rails -join ', '))" -Test {
        # Only names count, and only from a listing that succeeded: the helper returns stderr too,
        # and an error line counted as a container would report a stack that is not there as up.
        $lines = @(& $runtimeCli -CliArgs @('ps', '--filter', 'name=platform-', '--format', '{{.Names}}') -Mode $Mode |
                   ForEach-Object { [string]$_ })
        $ok = ($LASTEXITCODE -eq 0)
        $ok -and [bool](@($lines | Where-Object { $_ -match '^platform-' }).Count)
    }.GetNewClosure() -Do {
        $profileArgs = @(); foreach ($r in $Rails) { $profileArgs += @('--profile', $r) }
        # -NoLog: this engine has no log file; the output goes to the console below.
        & $composeCli -Arguments ((& $composeBase) + $profileArgs + @('up', '-d')) -Mode $Mode -NoLog | Out-Host
    }.GetNewClosure()

    $actions
}


function Test-ApplyElevated {
    (New-Object Security.Principal.WindowsPrincipal(
        [Security.Principal.WindowsIdentity]::GetCurrent())).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

# A wsl box's OLLAMA_HOST, after the steps. wsl only: any other -Mode returns nothing and reads
# nothing. The containers reach the Ollama app only once it binds beyond loopback, which takes
# OLLAMA_HOST in the USER's environment (lib-runtime Set-WslOllamaHost, which install.ps1 calls from
# its own unelevated process). This flow elevates once, up front, and an elevation on a managed box
# can be another account (over-the-shoulder UAC), whose environment is not the user's. So an
# unelevated -Execute sets it, and an elevated run sets nothing and says what to do. Not an apply
# action: its "done?" cannot be read from an elevated process either (that HKCU may be another
# account's), and a step that can only say what to do is no failure. Returns the lines to print.
function Invoke-ApplyOllamaHost {
    param([Parameter(Mandatory)][string]$Mode, [switch]$Execute, [bool]$Elevated,
          [hashtable]$Store = $OllamaHostStore)
    $want = Get-OllamaHostForMode -Mode $Mode
    if (-not $want) { return @() }
    if ($Elevated) {
        return @("  note  OLLAMA_HOST=$want is not set by an elevated run: it belongs in your own account's " +
                 "environment, and this one may be another account's. Set it for your account (Settings > " +
                 "`"Edit environment variables for your account`") and restart the Ollama app, or run " +
                 'apply.ps1 -Execute again from an unelevated shell, which sets it.')
    }
    if (-not $Execute) {
        if ([string](& $Store.Get) -eq $want) { return @("  skip  OLLAMA_HOST=$want for your account  (already done)") }
        return @("  would set OLLAMA_HOST=$want for your account, and restart the Ollama app if it listens on loopback")
    }
    $lines = @("  run   set OLLAMA_HOST=$want for your account ...")
    foreach ($l in @(Set-WslOllamaHost -Mode $Mode -Store $Store)) { $lines += "        $l" }
    return $lines
}

function Invoke-Apply {
    # Dry by default. Each step: if already done, skip; else (dry) announce, or (execute) do it
    # then RE-TEST and report the re-test, not the exit code. Then, on a wsl box, the user's
    # OLLAMA_HOST (Invoke-ApplyOllamaHost).
    param(
        [Parameter(Mandatory)][object[]]$Actions,
        [switch]$Execute,
        # The runtime the actions were built for (Get-ApplyActions' -Mode); required for the same
        # reason it is there.
        [Parameter(Mandatory)][ValidateSet('podman', 'desktop', 'wsl')][string]$Mode
    )
    $elevated = Test-ApplyElevated
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
    try {
        foreach ($l in @(Invoke-ApplyOllamaHost -Mode $Mode -Execute:$Execute -Elevated $elevated)) { Write-Host $l }
    }
    catch { Write-Host "        error: setting OLLAMA_HOST: $_"; $failed++ }
    $failed
}
