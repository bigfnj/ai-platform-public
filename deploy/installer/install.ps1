<#
  AI-Platform lean installer (GUI). Targets an 8 GB-VRAM Windows box with no HuggingFace token
  and no media/image pipeline. Installs: the shell (admin) + Terminal Fun + optional Recipe Book,
  on gemma3:4b + bge-m3, with the native broker/ollama NSSM services and BrokerTray.

  Run:   powershell -ExecutionPolicy Bypass -File install.ps1
  Doctor only (no window): powershell -ExecutionPolicy Bypass -File install.ps1 -Check

  Design: the window (this process, non-elevated) collects inputs and tails a log; the actual
  provisioning re-launches this script with -Provision, elevated, which writes progress to the
  shared log + a DONE/FAIL marker. (Start-Process -Verb RunAs can't redirect stdout, hence the
  file-based log.)
#>
[CmdletBinding()]
param(
  [switch]$Check,                 # run the prereq doctor to the console and exit
  [switch]$Provision,             # internal: run the elevated provisioning steps
  [switch]$Force,                 # bypass the existing-install guard
  [string]$AdminUser,
  [string]$AdminPass,
  [string]$EnabledApps,           # comma list, e.g. "terminal-fun,recipe-book"
  [switch]$WithRecipeBook
)

$ErrorActionPreference = 'Stop'
$Root      = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent   # deploy/installer -> repo root
$Installer = $PSScriptRoot
$LogFile   = Join-Path $env:TEMP 'ai-platform-install.log'
$DoneFile  = Join-Path $env:TEMP 'ai-platform-install.done'
$FailFile  = Join-Path $env:TEMP 'ai-platform-install.fail'
$DockerBin = Join-Path $env:ProgramFiles 'Docker\Docker\resources\bin'
if (Test-Path $DockerBin) { $env:Path = "$DockerBin;$env:Path" }

# ---------------------------------------------------------------------------
# Prereq doctor
# ---------------------------------------------------------------------------
function Test-Prereqs {
  $r = @()
  # Docker (State: missing | installed | running). Resolve the CLI by its known install path too,
  # because a winget install done in THIS session isn't on our already-loaded PATH yet.
  $dockerExe = Join-Path $env:ProgramFiles 'Docker\Docker\resources\bin\docker.exe'
  $dockerCmd = if (Test-Path $dockerExe) { $dockerExe } elseif (Get-Command docker -ErrorAction SilentlyContinue) { 'docker' } else { $null }
  $dockerState = 'missing'; $dockerDetail = 'not found'
  if ($dockerCmd) {
    $dockerState = 'installed'; $dockerDetail = 'installed, daemon not running'
    try { & $dockerCmd version --format '{{.Server.Version}}' 2>$null | Out-Null; if ($LASTEXITCODE -eq 0) { $dockerState = 'running'; $dockerDetail = 'engine running' } } catch {}
  }
  $r += [pscustomobject]@{ Key = 'docker'; Name = 'Docker Desktop'; Ok = ($dockerState -eq 'running'); Detail = $dockerDetail; Fix = 'Docker.DockerDesktop'; State = $dockerState }
  # NVIDIA GPU >= 8 GB
  $gpuOk = $false; $gpuDetail = 'no NVIDIA GPU detected'
  try {
    $mem = (& nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>$null | Select-Object -First 1)
    if ($mem) { $mib = [int]($mem.Trim()); $gpuDetail = "$([math]::Round($mib/1024,1)) GB VRAM"; $gpuOk = $mib -ge 7500 }
  } catch {}
  $r += [pscustomobject]@{ Key = 'gpu'; Name = 'NVIDIA GPU (>= 8 GB)'; Ok = $gpuOk; Detail = $gpuDetail; Fix = ''; State = '' }
  # Ollama
  $ollamaExe = Join-Path $env:USERPROFILE 'AppData\Local\Programs\Ollama\ollama.exe'
  $ollamaOk = (Test-Path $ollamaExe) -or [bool](Get-Command ollama -ErrorAction SilentlyContinue)
  $r += [pscustomobject]@{ Key = 'ollama'; Name = 'Ollama'; Ok = $ollamaOk; Detail = $(if ($ollamaOk) { 'installed' } else { 'not found' }); Fix = 'Ollama.Ollama'; State = '' }
  # Python 3.11+
  $pyOk = $false; $pyDetail = 'not found'
  try { $v = (& python --version 2>&1); if ($v -match '(\d+)\.(\d+)') { $pyDetail = $v.ToString().Trim(); $pyOk = ([int]$Matches[1] -eq 3 -and [int]$Matches[2] -ge 10) } } catch {}
  $r += [pscustomobject]@{ Key = 'python'; Name = 'Python 3.11'; Ok = $pyOk; Detail = $pyDetail; Fix = 'Python.Python.3.11'; State = '' }
  # Disk (system drive) >= 20 GB. DriveInfo (not Get-PSDrive, which hangs on dead network mounts).
  $free = [math]::Round((New-Object System.IO.DriveInfo($env:SystemDrive)).AvailableFreeSpace / 1GB, 1)
  $r += [pscustomobject]@{ Key = 'disk'; Name = 'Disk (>= 20 GB free)'; Ok = ($free -ge 20); Detail = "$free GB free on $env:SystemDrive"; Fix = ''; State = '' }
  return $r
}

function Test-ExistingInstall {
  if (Get-Service platform-broker -ErrorAction SilentlyContinue) { return 'platform-broker service exists' }
  try { $ps = & docker ps --format '{{.Names}}' 2>$null; if ($ps -match 'platform-') { return 'platform-* containers are running' } } catch {}
  if (Test-Path (Join-Path $Root 'deploy\.env')) { return 'deploy\.env already exists' }
  return $null
}

# Locate the Docker Desktop launcher (to start the engine after a fresh install; the winget
# package installs it but the daemon only comes up after a manual first launch + license accept).
function Get-DockerDesktopExe {
  foreach ($base in @($env:ProgramFiles, ${env:ProgramFiles(x86)})) {
    if ($base) { $p = Join-Path $base 'Docker\Docker\Docker Desktop.exe'; if (Test-Path $p) { return $p } }
  }
  return $null
}

# ---------------------------------------------------------------------------
# Provisioning (runs elevated via -Provision; logs to $LogFile + a marker)
# ---------------------------------------------------------------------------
function Write-Log($m) {
  $line = "{0}  {1}" -f (Get-Date -Format 'HH:mm:ss'), $m
  Add-Content -Path $LogFile -Value $line -Encoding utf8
}

function Invoke-Provision {
  Remove-Item $DoneFile, $FailFile -ErrorAction SilentlyContinue
  try {
    Write-Log "=== AI-Platform lean install ==="
    Write-Log "repo root: $Root"

    # 1. config: deploy/.env from the lean template + roles.lean.json -> broker roles.json
    Write-Log 'writing deploy\.env (lean) ...'
    $tmpl = Get-Content (Join-Path $Installer 'env.lean.example') -Raw
    $tmpl = $tmpl.Replace('{{ADMIN_USER}}', $AdminUser).Replace('{{ADMIN_PASSWORD}}', $AdminPass).Replace('{{ENABLED_APPS}}', $EnabledApps)
    Set-Content -Path (Join-Path $Root 'deploy\.env') -Value $tmpl -Encoding utf8
    Copy-Item (Join-Path $Installer 'roles.lean.json') (Join-Path $Root 'services\broker\roles.json') -Force
    Write-Log 'config written.'

    # 2. native services (broker venv + ollama/broker NSSM, media off)
    Write-Log 'installing native services (broker venv + NSSM)...'
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Installer 'install-native.ps1') -PlatformRoot $Root *>> $LogFile
    if ($LASTEXITCODE -ne 0) { throw 'install-native.ps1 failed' }

    # 3. pull the lean models
    foreach ($m in @('gemma3:4b', 'bge-m3')) {
      Write-Log "ollama pull $m ..."
      & ollama pull $m *>> $LogFile
    }

    # 4. bundled compose: build + up (recipe-book only when chosen)
    $composeArgs = @('--env-file', (Join-Path $Root 'deploy\.env'), '-f', (Join-Path $Installer 'docker-compose.installer.yml'))
    if ($WithRecipeBook) { $composeArgs += @('--profile', 'recipe-book') }
    Write-Log 'building + starting containers (first build takes several minutes)...'
    & docker compose @composeArgs up -d --build *>> $LogFile
    if ($LASTEXITCODE -ne 0) { throw 'docker compose up failed' }

    # 5. install the BrokerTray (native tray control), best-effort
    try {
      $tray = Join-Path $Root 'tools\broker-tray\BrokerTray.exe'
      if (Test-Path $tray) { Start-Process $tray | Out-Null; Write-Log 'launched BrokerTray.' }
    } catch { Write-Log "tray: $($_.Exception.Message)" }

    # 6. wait for the gateway
    Write-Log 'waiting for the gateway...'
    for ($i = 0; $i -lt 60; $i++) {
      try { Invoke-WebRequest 'http://localhost:1111/api/platform/healthz' -TimeoutSec 3 -UseBasicParsing | Out-Null; break } catch { Start-Sleep 2 }
    }
    Write-Log 'DONE. Platform is up at http://platform.localhost:1111'
    New-Item -ItemType File -Path $DoneFile -Force | Out-Null
  } catch {
    Write-Log "ERROR: $($_.Exception.Message)"
    Set-Content -Path $FailFile -Value $_.Exception.Message -Encoding utf8
  }
}

# ---------------------------------------------------------------------------
# entrypoints
# ---------------------------------------------------------------------------
if ($Check) {
  "AI-Platform prereq check:`n"
  Test-Prereqs | ForEach-Object { "{0} {1,-24} {2}" -f $(if ($_.Ok) { '[ OK ]' } else { '[FAIL]' }), $_.Name, $_.Detail }
  $ex = Test-ExistingInstall
  if ($ex) { "`n[WARN] existing install detected: $ex (installer would refuse without -Force)" }
  return
}
if ($Provision) { Invoke-Provision; return }

# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$form = New-Object Windows.Forms.Form
$form.Text = 'AI-Platform Installer (lean)'
$form.Size = New-Object Drawing.Size(680, 640)
$form.StartPosition = 'CenterScreen'
$form.Font = New-Object Drawing.Font('Segoe UI', 9)

function New-Label($text, $x, $y, $w, $bold) {
  $l = New-Object Windows.Forms.Label
  $l.Text = $text; $l.Location = New-Object Drawing.Point($x, $y); $l.AutoSize = $true
  if ($bold) { $l.Font = New-Object Drawing.Font('Segoe UI', 10, [Drawing.FontStyle]::Bold) }
  $form.Controls.Add($l); return $l
}

New-Label '1. Prerequisites' 16 12 0 $true | Out-Null
$prereqBox = New-Object Windows.Forms.TextBox
$prereqBox.Multiline = $true; $prereqBox.ReadOnly = $true; $prereqBox.ScrollBars = 'Vertical'
$prereqBox.Location = New-Object Drawing.Point(16, 36); $prereqBox.Size = New-Object Drawing.Size(500, 96)
$prereqBox.Font = New-Object Drawing.Font('Consolas', 9)
$form.Controls.Add($prereqBox)

$btnCheck = New-Object Windows.Forms.Button
$btnCheck.Text = 'Re-check'; $btnCheck.Location = New-Object Drawing.Point(528, 36); $btnCheck.Size = New-Object Drawing.Size(120, 28)
$form.Controls.Add($btnCheck)
$btnFix = New-Object Windows.Forms.Button
$btnFix.Text = 'Install missing'; $btnFix.Location = New-Object Drawing.Point(528, 70); $btnFix.Size = New-Object Drawing.Size(120, 28)
$form.Controls.Add($btnFix)

New-Label '2. Super-admin account' 16 142 0 $true | Out-Null
New-Label 'Username' 16 170 0 $false | Out-Null
$txtUser = New-Object Windows.Forms.TextBox; $txtUser.Location = New-Object Drawing.Point(110, 167); $txtUser.Size = New-Object Drawing.Size(180, 24); $txtUser.Text = 'admin'; $form.Controls.Add($txtUser)
New-Label 'Password' 310 170 0 $false | Out-Null
$txtPass = New-Object Windows.Forms.TextBox; $txtPass.Location = New-Object Drawing.Point(380, 167); $txtPass.Size = New-Object Drawing.Size(180, 24); $txtPass.UseSystemPasswordChar = $true; $form.Controls.Add($txtPass)

New-Label '3. Rails to install' 16 206 0 $true | Out-Null
$chkAdmin = New-Object Windows.Forms.CheckBox; $chkAdmin.Text = 'Admin shell (required)'; $chkAdmin.Checked = $true; $chkAdmin.Enabled = $false; $chkAdmin.Location = New-Object Drawing.Point(16, 232); $chkAdmin.AutoSize = $true; $form.Controls.Add($chkAdmin)
$chkTerm = New-Object Windows.Forms.CheckBox; $chkTerm.Text = 'Terminal Fun'; $chkTerm.Checked = $true; $chkTerm.Location = New-Object Drawing.Point(210, 232); $chkTerm.AutoSize = $true; $form.Controls.Add($chkTerm)
$chkRecipe = New-Object Windows.Forms.CheckBox; $chkRecipe.Text = 'Recipe Book (ships with seed)'; $chkRecipe.Checked = $false; $chkRecipe.Location = New-Object Drawing.Point(340, 232); $chkRecipe.AutoSize = $true; $form.Controls.Add($chkRecipe)

$btnInstall = New-Object Windows.Forms.Button
$btnInstall.Text = 'Install'; $btnInstall.Location = New-Object Drawing.Point(16, 268); $btnInstall.Size = New-Object Drawing.Size(140, 34)
$btnInstall.Font = New-Object Drawing.Font('Segoe UI', 10, [Drawing.FontStyle]::Bold)
$form.Controls.Add($btnInstall)
$btnLaunch = New-Object Windows.Forms.Button
$btnLaunch.Text = 'Open :1111'; $btnLaunch.Location = New-Object Drawing.Point(168, 268); $btnLaunch.Size = New-Object Drawing.Size(140, 34); $btnLaunch.Enabled = $false
$form.Controls.Add($btnLaunch)

$log = New-Object Windows.Forms.TextBox
$log.Multiline = $true; $log.ReadOnly = $true; $log.ScrollBars = 'Vertical'
$log.Location = New-Object Drawing.Point(16, 314); $log.Size = New-Object Drawing.Size(632, 268)
$log.Font = New-Object Drawing.Font('Consolas', 9)
$form.Controls.Add($log)

$script:prereqs = @()
$script:pendingInstall = $false     # user asked to install; waiting on the Docker engine to come up
$script:dockerAnnounced = $false    # printed "Docker detected" once
$script:ollamaAnnounced = $false    # printed "Ollama detected" once

function Get-Prereq($key) { $script:prereqs | Where-Object { $_.Key -eq $key } }

# The two prerequisites the build genuinely can't proceed without: a *running* container engine
# and an *installed* Ollama (the elevated provisioner starts Ollama's service and pulls models).
# GPU / Python / disk stay soft (a skippable warning).
function Test-HardReady {
  $d = Get-Prereq 'docker'; $o = Get-Prereq 'ollama'
  return ($d -and $d.State -eq 'running' -and $o -and $o.Ok)
}

function Refresh-Prereqs {
  $script:prereqs = Test-Prereqs
  $prereqBox.Text = ($script:prereqs | ForEach-Object { "{0} {1,-22} {2}" -f $(if ($_.Ok) { '[OK]  ' } else { '[MISS]' }), $_.Name, $_.Detail }) -join "`r`n"
}

# Launch the elevated provisioning and tail its log. Called directly when Docker is already up,
# or by the watcher once the engine comes online.
function Start-Provisioning {
  $ex = Test-ExistingInstall
  if ($ex -and -not $Force) { [Windows.Forms.MessageBox]::Show("An existing platform install was detected ($ex).`nThis installer refuses to touch it. Run on a clean machine/VM.", 'Installer'); return }
  $enabled = @('terminal-fun'); if ($chkRecipe.Checked) { $enabled += 'recipe-book' }
  $btnInstall.Enabled = $false
  $log.AppendText("starting provisioning (elevated)...`r`n")
  Set-Content -Path $LogFile -Value '' -Encoding utf8
  $script:pos = 0
  $pargs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"", '-Provision',
    '-AdminUser', $txtUser.Text, '-AdminPass', $txtPass.Text, '-EnabledApps', ($enabled -join ','))
  if ($chkRecipe.Checked) { $pargs += '-WithRecipeBook' }
  Start-Process powershell -Verb RunAs -WindowStyle Hidden -ArgumentList $pargs
  $timer = New-Object Windows.Forms.Timer; $timer.Interval = 800
  $timer.Add_Tick({
      if (Test-Path $LogFile) {
        $all = Get-Content $LogFile -Raw -ErrorAction SilentlyContinue
        if ($all -and $all.Length -gt $script:pos) { $log.AppendText($all.Substring($script:pos)); $script:pos = $all.Length }
      }
      if (Test-Path $DoneFile) { $timer.Stop(); $btnLaunch.Enabled = $true; $btnInstall.Enabled = $true; [Windows.Forms.MessageBox]::Show('Install complete. Click "Open :1111".', 'Installer') }
      elseif (Test-Path $FailFile) { $timer.Stop(); $btnInstall.Enabled = $true; [Windows.Forms.MessageBox]::Show("Install failed:`n" + (Get-Content $FailFile -Raw), 'Installer') }
    }.GetNewClosure())
  $timer.Start()
}

# Poll every 3s for Docker + Ollama to come up after an install/launch, then auto-continue.
$script:watchTimer = New-Object Windows.Forms.Timer
$script:watchTimer.Interval = 3000
$script:watchTimer.Add_Tick({
    Refresh-Prereqs
    $d = Get-Prereq 'docker'; $ol = Get-Prereq 'ollama'
    if ($d -and $d.State -eq 'running' -and -not $script:dockerAnnounced) { $script:dockerAnnounced = $true; $log.AppendText("Docker detected.`r`n") }
    if ($ol -and $ol.Ok -and -not $script:ollamaAnnounced) { $script:ollamaAnnounced = $true; $log.AppendText("Ollama detected.`r`n") }
    if (Test-HardReady) {
      if ($script:pendingInstall) {
        $script:pendingInstall = $false
        $script:watchTimer.Stop()
        $log.AppendText("prerequisites ready, continuing.`r`n")
        Start-Provisioning
      }
      else { $script:watchTimer.Stop() }   # ready, nothing queued
    }
  }.GetNewClosure())

Refresh-Prereqs

# Launch Docker Desktop if present (its winget package installs it but the daemon only comes up
# after a manual first launch + license accept). Emits the guidance note either way.
function Start-DockerDesktop {
  $dd = Get-DockerDesktopExe
  if ($dd) { $log.AppendText("launching Docker Desktop - accept its license / service agreement, then wait for the engine to start.`r`n"); try { Start-Process $dd | Out-Null } catch {} }
  else { $log.AppendText("Docker installed. Launch Docker Desktop and accept its license to start the engine.`r`n") }
}

# Re-check: manual refresh; if both hard prereqs are ready and an install was queued, continue now.
$btnCheck.Add_Click({
    Refresh-Prereqs
    if ((Test-HardReady) -and $script:pendingInstall) { $script:pendingInstall = $false; $script:watchTimer.Stop(); Start-Provisioning }
  })

# Install missing: winget the fixable gaps, then (if a hard prereq still isn't ready) launch
# Docker Desktop and start watching so a later "Install" continues automatically.
$btnFix.Add_Click({
    $missing = $script:prereqs | Where-Object { -not $_.Ok -and $_.Fix }
    if (-not $missing) { $log.AppendText("nothing to install - all fixable prerequisites are present.`r`n"); return }
    foreach ($p in $missing) {
      $log.AppendText("winget install $($p.Fix)...`r`n")
      Start-Process winget -ArgumentList @('install', '--id', $p.Fix, '-e', '--accept-source-agreements', '--accept-package-agreements') -Wait
    }
    Refresh-Prereqs
    if (-not (Test-HardReady)) {
      $d = Get-Prereq 'docker'
      if ($d -and $d.State -ne 'running') { Start-DockerDesktop }
      $log.AppendText("watching for Docker + Ollama to come up (auto-continues when detected)...`r`n")
      $script:watchTimer.Start()
    }
  })

# Install: proceed when Docker is running AND Ollama is installed; otherwise install/launch the
# missing hard prereq(s) and continue automatically once both are ready. GPU/Python/disk stay a
# soft, skippable warning.
$btnInstall.Add_Click({
    if (-not $txtPass.Text) { [Windows.Forms.MessageBox]::Show('Set a super-admin password.', 'Installer'); return }
    Refresh-Prereqs
    $d = Get-Prereq 'docker'; $o = Get-Prereq 'ollama'
    $soft = $script:prereqs | Where-Object { -not $_.Ok -and $_.Key -notin @('docker', 'ollama') }
    if ($soft) { if ([Windows.Forms.MessageBox]::Show("Unmet prerequisites:`n" + (($soft | ForEach-Object { $_.Name }) -join ', ') + "`n`nContinue anyway?", 'Installer', 'YesNo') -ne 'Yes') { return } }

    if (Test-HardReady) { Start-Provisioning; return }

    # A hard prereq is missing / not up: install what's missing, launch Docker, then let the
    # watcher continue once BOTH the engine is running and Ollama is installed.
    $script:pendingInstall = $true
    if ($d.State -eq 'missing') {
      $log.AppendText("winget install Docker.DockerDesktop...`r`n")
      Start-Process winget -ArgumentList @('install', '--id', 'Docker.DockerDesktop', '-e', '--accept-source-agreements', '--accept-package-agreements') -Wait
    }
    if (-not $o.Ok) {
      $log.AppendText("winget install Ollama.Ollama...`r`n")
      Start-Process winget -ArgumentList @('install', '--id', 'Ollama.Ollama', '-e', '--accept-source-agreements', '--accept-package-agreements') -Wait
    }
    Refresh-Prereqs
    $d = Get-Prereq 'docker'
    if ($d.State -ne 'running') { Start-DockerDesktop }
    $log.AppendText("waiting for Docker + Ollama; the install continues automatically once both are ready...`r`n")
    $script:watchTimer.Start()
  })

$btnLaunch.Add_Click({ Start-Process 'http://platform.localhost:1111' })

[void]$form.ShowDialog()
