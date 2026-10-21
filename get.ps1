<#
  ⚠ THIS FILE IS FOR THE PUBLIC SNAPSHOT, NOT FOR THIS REPO.
  It clones github.com/bigfnj/ai-platform-public, which is the generated public subset — running
  it here would fetch that subset over the top of nothing useful. It lives in the private repo
  only because tools/publish.py can publish nothing it does not have: the file was previously
  public-only, so the first regenerate silently deleted it from the public repo. Anything the
  public artifact needs must be sourced from here.

  AI-Platform one-line bootstrap.  Run from any PowerShell:

      irm https://raw.githubusercontent.com/bigfnj/ai-platform-public/main/get.ps1 | iex

  It runs from memory (no clone needed to start): ensures git, enables Windows long-paths, clones
  (or updates) the repo to a local folder, and opens an interactive menu that drives the lean GUI
  installer (deploy/installer/install.ps1). It runs NON-elevated; the installer self-elevates only
  for its provisioning step.

  Because `| iex` can't take parameters, override defaults with env vars set BEFORE the one-liner:
      $env:AIPLATFORM_DIR = "$HOME\dev\ai-platform"   # where to clone (default: %USERPROFILE%\ai-platform)
      $env:AIPLATFORM_REF = 'v1.2.3'               # branch or tag to check out (default: main)

  Security note: piping a remote script to iex executes whatever is at that URL right now. Read it
  first (open the raw URL), and for a pinned install point AIPLATFORM_REF at a tag, not a branch.
#>
$ErrorActionPreference = 'Stop'
$RepoUrl = 'https://github.com/bigfnj/ai-platform-public.git'
$Ref = if ($env:AIPLATFORM_REF) { $env:AIPLATFORM_REF } else { 'main' }
$Dir = if ($env:AIPLATFORM_DIR) { $env:AIPLATFORM_DIR } else { Join-Path $env:USERPROFILE 'ai-platform' }

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

# --- this box's own edits to tracked broker files ------------------------------
# The update below is a `git reset --hard`, which puts every TRACKED file back to the published
# one. An install from before 2026-10-14 kept the box's role map and its disabled models in the
# tracked services\broker\roles.json and disabled.json (the broker writes Admin > Rails edits into
# the files it reads), so updating put the 24 GB role map back, hot-read by the broker at once, and
# dropped every admin edit. Installs now keep them in deploy\local\ (git-ignored). A box with edits
# to tracked files there is refused, with the way to keep them.
function Assert-NoBrokerEdits {
  param([Parameter(Mandatory)][string]$Git, [Parameter(Mandatory)][string]$Dir)
  $edited = @(& $Git -C $Dir diff --name-only HEAD -- services/broker | Where-Object { $_ })
  if ($LASTEXITCODE -ne 0) { throw "git diff failed in $Dir (exit $LASTEXITCODE); not updating." }
  if ($edited.Count -eq 0) { return }
  $local = Join-Path $Dir 'deploy\local'
  $copy = @($edited | Where-Object { (Split-Path $_ -Leaf) -in @('roles.json', 'disabled.json') -and
                                     -not (Test-Path -LiteralPath (Join-Path $local (Split-Path $_ -Leaf))) })
  $lines = @("Not updating: this box has changes to tracked files that the update would overwrite:")
  $lines += @($edited | ForEach-Object { "    $_" })
  $lines += ("An install from before 2026-10-14 keeps the box's role map and disabled models in " +
             "services\broker\roles.json and disabled.json; overwritten, the broker would serve the 24 GB role map at once.")
  $lines += 'To keep them:'
  $n = 1
  if ($copy.Count) {
    $cmds = @("New-Item -ItemType Directory -Force '$local' | Out-Null") +
            @($copy | ForEach-Object { "Copy-Item '$(Join-Path $Dir ($_ -replace '/', '\'))' '$local\'" })
    $lines += "  $n. copy them to deploy\local, which git ignores:  $($cmds -join '; ')"; $n++
  }
  $lines += "  $n. discard the tracked copies:  git -C '$Dir' checkout -- services/broker"; $n++
  $lines += "  $n. re-run the one-liner (it updates), then once, from an elevated PowerShell:"
  $lines += "       powershell -ExecutionPolicy Bypass -File '$Dir\deploy\installer\install-native.ps1' -PlatformRoot '$Dir' -SkipOllama"
  $lines += '     so the broker reads deploy\local (until then it reads the tracked 24 GB map).'
  # Printed, then a short throw: an error record's own rendering wraps a long message mid-path.
  foreach ($l in $lines) { Write-Host "  $l" -ForegroundColor Yellow }
  throw 'Not updating: tracked files under services\broker carry changes of this box (see above).'
}

# --- target dir + OneDrive guard -------------------------------------------
Write-Head "Install folder: $Dir"
$inOneDrive = ($Dir -match 'OneDrive') -or ($env:OneDrive -and $Dir -like "$env:OneDrive*")
if ($inOneDrive) {
  Write-Warn2 'That path is under OneDrive. A cloned repo + Python venv + a LocalSystem service do'
  Write-Warn2 "not play well with OneDrive sync - prefer a path under your profile, e.g. $env:USERPROFILE\ai-platform."
}
$ans = Read-Host '  Press Enter to use it, or type another path'
if ($ans) { $Dir = $ans }

# --- clone or update -------------------------------------------------------
if (Test-Path (Join-Path $Dir '.git')) {
  Write-Head "Updating existing clone ($Ref)..."
  Assert-NoBrokerEdits -Git $git -Dir $Dir
  & $git -C $Dir fetch --depth 1 origin $Ref
  if ($LASTEXITCODE -ne 0) { throw "git fetch failed (exit $LASTEXITCODE)." }
  # Force the install clone to exactly match published $Ref. It's a throwaway mirror, not a working
  # copy, so a hard reset is correct - and it tolerates the "diverged" shallow state a depth-1 fetch
  # produces (which broke pull --ff-only). reset --hard only touches tracked files, so untracked
  # install state (.venv, deploy/.env, deploy/local) is left alone; Assert-NoBrokerEdits above
  # refuses a box whose own state is still in tracked files.
  & $git -C $Dir reset --hard FETCH_HEAD
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
    '2' { & powershell -NoProfile -ExecutionPolicy Bypass -File $Installer -Console }
    '3' { Write-Head 'Launching the installer window (close it to return here)...'; & powershell -NoProfile -ExecutionPolicy Bypass -File $Installer }
    '4' { Start-Process explorer.exe $Dir }
    'Q' { $run = $false }
    default { Write-Warn2 'Unrecognized choice - enter 1, 2, 3, 4, or Q.' }
  }
}
Write-Host ''
Write-Ok 'Done. Re-run the one-liner any time to update and reopen this menu.'
