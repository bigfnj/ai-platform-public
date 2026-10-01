# The installer model-plan engine: selected rails + detected VRAM -> the models to install.
#
# Same shape as lib-preflight: pure logic, structured serialisable results, renders nothing, so
# a TUI/GUI/tray all drive it and the seam it writes (roles.json) is computed here rather than in
# a UI. It answers the user-facing question "for the rails I picked, which local models do I need,
# and which fit my card" without inventing footprints: sizes come from `ollama list` where a model
# is installed, and from model-catalog.json (flagged estimated) where it is not.
#
# THREE THINGS THAT MAKE THIS MORE THAN A LOOKUP:
#  1. The default per role is roles.json, ALWAYS honoured even when it is not in the catalog for
#     that kind. recipe's CHAT role defaults to gemma4:26b, a multimodal model, precisely so one
#     model serves both its chat and vision roles and loads once. So the default is a synthesised
#     option and the catalog only supplies ALTERNATIVES.
#  2. Dedupe by MODEL, not by role. recipe's @recipe and @recipe-vision are the same gemma4:26b,
#     so it is one download and one VRAM budget. finance is the opposite lesson: its two chat
#     roles are deliberately different models, which is why planning is per-role, not per-kind.
#  3. VRAM sizing is "does the single largest model fit as sole resident", never a sum, because
#     the broker runs one heavy model at a time and evicts before the next.

Set-StrictMode -Version Latest

$__mp_here = Split-Path -Parent $MyInvocation.MyCommand.Path
$__mp_repo = (Resolve-Path (Join-Path $__mp_here '..\..')).Path

$ModelCatalogPath = Join-Path $__mp_here 'model-catalog.json'
$RolesJsonPath = Join-Path $__mp_repo 'services\broker\roles.json'
$RailsDir = Join-Path $__mp_repo 'rails'

# Quality order, best-that-fits first. 'any' is the embedders: they fit everything.
$TierRank = @{ tiny = 1; small = 2; mid = 3; large = 4; any = 5 }


function Get-RoleDefaults {
    $r = Get-Content -Raw -Path $RolesJsonPath | ConvertFrom-Json
    $roles = if ($r.PSObject.Properties.Name -contains 'roles') { $r.roles } else { $r }
    $out = @{}
    foreach ($p in $roles.PSObject.Properties) { $out[$p.Name] = [string]$p.Value }
    $out
}

function Get-ModelCatalog {
    $c = Get-Content -Raw -Path $ModelCatalogPath | ConvertFrom-Json
    $out = @{}
    foreach ($p in $c.PSObject.Properties) {
        if ($p.Name -eq '_note') { continue }
        $out[$p.Name] = @($p.Value)
    }
    $out
}

function Get-InstalledModels {
    # name -> size in GB, from the live Ollama. Empty when unreachable; a caller mid-install can
    # pass a fixture instead (the -Installed param on Get-ModelPlan).
    $map = @{}
    try {
        $tags = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 5
        foreach ($m in $tags.models) { $map[[string]$m.name] = [math]::Round($m.size / 1e9, 1) }
    } catch { }
    $map
}

function Test-ModelInstalled {
    # A roles/catalog entry may be a size-scoped glob (gemma4*:26b, bge-m3*). Match it against
    # concrete installed names, returning the matched name (and its size) or $null.
    param([string]$Pattern, [hashtable]$Installed)
    foreach ($name in $Installed.Keys) {
        if ($name -like $Pattern) { return $name }
    }
    $null
}

function Get-RailSlots {
    param([string[]]$Rails)
    $out = @()
    foreach ($id in $Rails) {
        $man = Join-Path $RailsDir "$id\rail.json"
        if (-not (Test-Path $man)) { Write-Warning "no manifest for rail '$id'"; continue }
        $d = Get-Content -Raw -Path $man | ConvertFrom-Json
        foreach ($s in @($d.model_slots)) {
            if ($null -eq $s) { continue }
            # admin_panel absent defaults to REQUIRED (true): omission should be strict, not lax.
            # ai-voice's normalize slot sets it false on purpose (optional, off the critical path,
            # deliberately absent from roles.json), and that is the one case we skip quietly.
            $required = -not (($s.PSObject.Properties.Name -contains 'admin_panel') -and ($s.admin_panel -eq $false))
            $out += [PSCustomObject]@{ rail = $id; role = [string]$s.role; kind = [string]$s.kind
                                       required = $required }
        }
    }
    $out
}


function Get-ModelPlan {
    <#
      rails + vram -> a plan. Per-role options (default from roles.json, alternatives from the
      catalog, each annotated fits/installed/recommended), plus the deduped install list, the
      total download, and any role whose default does not fit and has no fitting substitute.
    #>
    param(
        [Parameter(Mandatory)][string[]]$Rails,
        [int]$VramMib = 0,
        [hashtable]$Installed
    )
    if (-not $PSBoundParameters.ContainsKey('Installed')) { $Installed = Get-InstalledModels }
    $vramGb = [math]::Round($VramMib / 1024, 1)
    $defaults = Get-RoleDefaults
    $catalog = Get-ModelCatalog

    # A flat model -> {approx_gb, min_vram_gb, backend, estimated} index across every catalog kind,
    # so a default that lives under another kind (gemma4:26b under vision, used as recipe's chat)
    # still resolves a size and a VRAM floor.
    $meta = @{}
    foreach ($kind in $catalog.Keys) {
        foreach ($e in $catalog[$kind]) {
            if (-not $meta.ContainsKey($e.model)) {
                $backend = if ($e.PSObject.Properties.Name -contains 'backend') { $e.backend } else { 'ollama' }
                $est = ($e.PSObject.Properties.Name -contains 'estimated') -and $e.estimated
                $meta[$e.model] = @{ approx_gb = [double]$e.approx_gb; min_vram_gb = [double]$e.min_vram_gb
                                     backend = $backend; estimated = $est }
            }
        }
    }

    # CPU-only doesn't mean "nothing runs": embedders and the tiny chat/vision tier run in RAM,
    # slowly but usably. So on a card-less box only the small tier (<=5 GB VRAM floor) is viable,
    # which keeps embed + nemotron + moondream on the menu and drops the heavy models honestly.
    $cpuViableFloorGb = 5
    function _fits([string]$model) {
        if ($meta.ContainsKey($model)) {
            $minv = $meta[$model].min_vram_gb
            if ($VramMib -le 0) { return $minv -le $cpuViableFloorGb }
            return $vramGb -ge $minv
        }
        return ($VramMib -gt 0)                        # unknown model: allow on a GPU, not on CPU
    }
    function _sizeGb([string]$model) {
        $inst = Test-ModelInstalled -Pattern $model -Installed $Installed
        if ($inst) { return $Installed[$inst] }
        if ($meta.ContainsKey($model)) { return $meta[$model].approx_gb }
        return $null
    }
    function _backend([string]$model) {
        if ($meta.ContainsKey($model)) { return $meta[$model].backend }
        'ollama'
    }
    function _optionFor([string]$model, [string]$kind, [bool]$isDefault) {
        $inst = Test-ModelInstalled -Pattern $model -Installed $Installed
        [PSCustomObject]@{
            model = $model; kind = $kind
            approx_gb = _sizeGb $model
            fits = (_fits $model)
            installed = [bool]$inst
            installed_as = $inst
            backend = _backend $model
            is_default = $isDefault
            recommended = $false
        }
    }

    $roleplans = @()
    $warnings = @()
    foreach ($slot in (Get-RailSlots -Rails $Rails)) {
        $role = $slot.role; $kind = $slot.kind
        $default = if ($defaults.ContainsKey($role)) { $defaults[$role] } else { $null }
        if (-not $default) {
            # A required slot with no default is a real gap; an optional one (admin_panel:false)
            # is intentional and skipped without noise.
            if ($slot.required) { $warnings += "required role @$role ($($slot.rail)) has no entry in roles.json" }
            continue
        }

        # Options: the default first, then the catalog alternatives for this kind (minus the
        # default if it is already among them).
        $opts = @()
        $opts += (_optionFor $default $kind $true)
        foreach ($e in @($catalog[$kind])) {
            if ($e.model -eq $default) { continue }
            $opts += (_optionFor $e.model $kind $false)
        }

        # Recommend the default if it fits; else the best-quality catalog option that fits.
        $rec = $null
        if ($opts[0].fits) { $rec = $opts[0] }
        else {
            $fitting = @($opts | Where-Object { $_.fits })
            if ($fitting.Count) {
                $rec = $fitting | Sort-Object @{ Expression = {
                    $t = 'mid'
                    foreach ($e in @($catalog[$kind])) { if ($e.model -eq $_.model) { $t = $e.tier } }
                    $TierRank[$t]
                } } -Descending | Select-Object -First 1
            }
        }
        if ($rec) { ($opts | Where-Object { $_.model -eq $rec.model } | Select-Object -First 1).recommended = $true }
        else { $warnings += "role @$role needs a $kind model but none fits $vramGb GB (default $default)" }

        $roleplans += [PSCustomObject]@{
            rail = $slot.rail; role = $role; kind = $kind
            default = $default
            chosen = if ($rec) { $rec.model } else { $null }
            options = $opts
        }
    }

    # The embedder is a BASELINE, always installed, even when no selected rail declares an embed
    # slot. recipe-book is the reason: it embeds for semantic search via a literal bge-m3 with no
    # slot, so a purely slot-driven plan would skip it and search would silently degrade. It is
    # also tiny (~1 GB) and shared platform-wide, so shipping it unconditionally is free insurance.
    $embedDefault = $null
    foreach ($e in @($catalog['embed'])) { if (($e.PSObject.Properties.Name -contains 'default') -and $e.default) { $embedDefault = $e.model } }

    # Dedupe downloads by chosen model, then add the baseline embedder if a role did not already.
    $chosen = @($roleplans | Where-Object { $_.chosen } | ForEach-Object { $_.chosen } | Select-Object -Unique)
    if ($embedDefault -and ($chosen -notcontains $embedDefault)) { $chosen = @($chosen) + $embedDefault }
    $installs = @()
    foreach ($model in $chosen) {
        $inst = Test-ModelInstalled -Pattern $model -Installed $Installed
        $installs += [PSCustomObject]@{
            model = $model
            approx_gb = _sizeGb $model
            backend = _backend $model
            installed = [bool]$inst
        }
    }
    $toPull = @($installs | Where-Object { -not $_.installed })
    $totalGb = ($toPull | ForEach-Object { if ($_.approx_gb) { $_.approx_gb } else { 0 } } | Measure-Object -Sum).Sum
    $needsVenv = [bool](@($installs | Where-Object { $_.backend -eq 'hf-media-venv' }).Count)

    [PSCustomObject]@{
        rails = $Rails
        vram_gb = $vramGb
        roles = $roleplans
        installs = $installs
        to_pull = $toPull
        total_download_gb = [math]::Round(($totalGb + 0.0), 1)
        needs_media_venv = $needsVenv
        warnings = $warnings
    }
}


function Show-ModelPlan {
    param([object]$Plan)
    Write-Host ("  card: {0} GB    rails: {1}`n" -f $Plan.vram_gb, ($Plan.rails -join ', '))
    foreach ($rp in $Plan.roles) {
        $rec = @($rp.options | Where-Object recommended | ForEach-Object model)
        Write-Host ("  @{0,-22} {1,-7} -> {2}" -f $rp.role, $rp.kind, ($(if ($rp.chosen) { $rp.chosen } else { 'NONE FITS' })))
        foreach ($o in $rp.options) {
            $flag = if ($o.recommended) { '*' } elseif ($o.fits) { ' ' } else { 'x' }
            $tags = @()
            if ($o.installed) { $tags += 'installed' }
            if ($o.backend -ne 'ollama') { $tags += $o.backend }
            $sz = if ($o.approx_gb) { "$($o.approx_gb)GB" } else { '?GB' }
            Write-Host ("      {0} {1,-24} {2,-7} {3}" -f $flag, $o.model, $sz, ($tags -join ' '))
        }
    }
    Write-Host ''
    Write-Host ("  download: {0} GB across {1} model(s) to pull ({2} already installed)" -f `
        $Plan.total_download_gb, $Plan.to_pull.Count, ($Plan.installs.Count - $Plan.to_pull.Count))
    if ($Plan.needs_media_venv) { Write-Host '  note: selection needs the GPU media venv (torch + diffusers, one-time, several GB)' }
    foreach ($w in $Plan.warnings) { Write-Host "  WARN: $w" }
}
