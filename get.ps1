<#
  AI-Platform one-line bootstrap.  Run from any PowerShell:

      irm https://raw.githubusercontent.com/bigfnj/ai-platform-public/main/get.ps1 | iex

  It runs from memory (no clone needed to start): ensures git, enables Windows long-paths, clones
  (or updates) the repo to a local folder, and opens an interactive menu that drives the lean GUI
  installer (deploy/installer/install.ps1). It runs NON-elevated; the installer self-elevates only
  for its provisioning step.

  Because `| iex` can't take parameters, override defaults with env vars set BEFORE the one-liner:
      $env:AIPLATFORM_DIR = 'C:\src\ai-platform'   # where to clone (default: %USERPROFILE%\ai-platform-public)
      $env:AIPLATFORM_REF = 'v1.2.3'               # branch or tag to check out (default: main)

  Security note: piping a remote script to iex executes whatever is at that URL right now. Read it
  first (open the raw URL), and for a pinned install point AIPLATFORM_REF at a tag, not a branch.
#>
$ErrorActionPreference = 'Stop'
$RepoUrl = 'https://github.com/bigfnj/ai-platform-public.git'
$Ref = if ($env:AIPLATFORM_REF) { $env:AIPLATFORM_REF } else { 'main' }
$Dir = if ($env:AIPLATFORM_DIR) { $env:AIPLATFORM_DIR } else { Join-Path $env:USERPROFILE 'ai-platform-public' }

function Write-Head($t) { Write-Host ''; Write-Host "  $t" -ForegroundColor Cyan }
function Write-Ok($t) { Write-Host "  [ok]  $t" -ForegroundColor Green }
function Write-Warn2($t) { Write-Host "  [!]   $t" -ForegroundColor Yellow }

# --- git -------------------------------------------------------------------
function Find-Git {
  $c = Get-Command git -ErrorAction SilentlyContinue
  if ($c) { return $c.Source }
  foreach ($p in @("$env:ProgramFiles\Git\cmd\git.exe", "${env:ProgramFiles(x86)}\Git\cmd\git.exe")) {
    if (Test-Path $p) { return $p }
  }
  return $null
}
function Install-Git {
  $git = Find-Git
  if ($git) { Write-Ok "git present"; return $git }
  Write-Warn2 'git not found - installing via winget (Git.Git)...'
  if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { throw 'winget is unavailable; install Git for Windows manually and re-run.' }
  Start-Process winget -Wait -ArgumentList @('install', '--id', 'Git.Git', '-e', '--accept-source-agreements', '--accept-package-agreements')
  $git = Find-Git
  if (-not $git) { throw 'git still not found after install. Open a NEW terminal and re-run the one-liner.' }
  Write-Ok 'git installed'
  return $git
}

# --- banner ----------------------------------------------------------------
try { Clear-Host } catch {}
Write-Host ''
Write-Host '  ============================================================' -ForegroundColor DarkCyan
Write-Host '   AI-Platform  -  lean self-hosted install (bootstrap)' -ForegroundColor White
Write-Host '  ============================================================' -ForegroundColor DarkCyan

$git = Install-Git
# A long rail recipe filename needs core.longpaths on Windows (one-time, global).
try { & $git config --global core.longpaths true | Out-Null } catch {}

# --- target dir + OneDrive guard -------------------------------------------
Write-Head "Install folder: $Dir"
$inOneDrive = ($Dir -match 'OneDrive') -or ($env:OneDrive -and $Dir -like "$env:OneDrive*")
if ($inOneDrive) {
  Write-Warn2 'That path is under OneDrive. A cloned repo + Python venv + a LocalSystem service do'
  Write-Warn2 'not play well with OneDrive sync - prefer a local path (e.g. C:\src\ai-platform-public).'
}
$ans = Read-Host '  Press Enter to use it, or type another path'
if ($ans) { $Dir = $ans }

# --- clone or update -------------------------------------------------------
if (Test-Path (Join-Path $Dir '.git')) {
  Write-Head "Updating existing clone ($Ref)..."
  & $git -C $Dir fetch --depth 1 origin $Ref
  & $git -C $Dir checkout $Ref
  & $git -C $Dir pull --ff-only origin $Ref
}
elseif ((Test-Path $Dir) -and (Get-ChildItem -Force $Dir -ErrorAction SilentlyContinue | Select-Object -First 1)) {
  throw "$Dir exists and is not an ai-platform clone. Pick an empty/new path via `$env:AIPLATFORM_DIR and re-run."
}
else {
  Write-Head "Cloning $RepoUrl ($Ref)..."
  $parent = Split-Path $Dir -Parent
  if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
  & $git clone --branch $Ref --depth 1 $RepoUrl $Dir
}
if ($LASTEXITCODE -ne 0) { throw "git clone/update failed (exit $LASTEXITCODE)." }

$Installer = Join-Path $Dir 'deploy\installer\install.ps1'
if (-not (Test-Path $Installer)) { throw "installer not found at $Installer (unexpected repo layout)." }
Write-Ok "repo ready at $Dir"

# Show the prereq doctor once up front (MAS-style: you see status immediately).
Write-Head 'Prerequisites:'
& powershell -NoProfile -ExecutionPolicy Bypass -File $Installer -Check

# --- interactive menu ------------------------------------------------------
function Show-Menu {
  Write-Host ''
  Write-Host '  ------------------------------------------------------------' -ForegroundColor DarkGray
  Write-Host '   1  Re-check prerequisites (doctor)'
  Write-Host '   2  Install in this terminal   (recommended)'
  Write-Host '   3  Install with the desktop window (GUI)'
  Write-Host '   4  Open the install folder'
  Write-Host '   Q  Quit'
  Write-Host '  ------------------------------------------------------------' -ForegroundColor DarkGray
}
$run = $true
while ($run) {
  Show-Menu
  switch ((Read-Host '  Select').Trim().ToUpperInvariant()) {
    '1' { & powershell -NoProfile -ExecutionPolicy Bypass -File $Installer -Check }
    '2' { & $Installer -Console }
    '3' { Write-Head 'Launching the installer window (close it to return here)...'; & $Installer }
    '4' { Start-Process explorer.exe $Dir }
    'Q' { $run = $false }
    default { Write-Warn2 'Unrecognized choice - enter 1, 2, 3, 4, or Q.' }
  }
}
Write-Host ''
Write-Ok 'Done. Re-run the one-liner any time to update and reopen this menu.'
