<#
  Run the platform's Python test suites — core + every rail — from one command.

    .\run-tests.ps1                  # core + all rails (live/network tests deselected)
    .\run-tests.ps1 -Rail edu-suite # one rail (or -Rail core for just the core suites)
    .\run-tests.ps1 -IncludeLive     # also run tests that hit real external services
    .\run-tests.ps1 -IncludeEdu      # also attempt edu-suite (see the note below)
    .\run-tests.ps1 -List            # show what would run, run nothing

  WHY THIS EXISTS. Every suite here is in-process pytest, but each rail is its own package with
  its own layout (`src/` for most, `backend/` for terminal-fun + workstation), so there was no
  single command that ran them all — and consequently no baseline. That gap hid a real fault: the
  repo .venv's editable installs still pointed at the pre-migration `D:\.claude\projects\platform`
  paths, so `import platform_core` failed and NOTHING was runnable for three days without anyone
  noticing. If this script won't import, re-run the editable installs (see -Doctor).

  Rails are put on sys.path via PYTHONPATH rather than pip-installed, deliberately: installing 10
  rail packages into one venv invites the dependency collisions this repo isolates against.

  ⚠ edu-suite is EXCLUDED by default and that is not laziness. Its dashboard tests import
  `edu_media_core`, whose dependency set is torch + torchaudio + coqui TTS + transformers==4.44.2
  + a capped diffusers — the heavy media stack that deliberately lives in its own venv
  (D:\.ai-work\venvs\media-venv / tts-venv). Installing it into the repo venv is exactly the move
  that once silently upgraded numpy 1.26 -> 2.4.6 and broke every image path. Run edu-suite's
  tests in its container or its own venv. -IncludeEdu attempts it anyway and will fail on a bare
  repo venv; the failure is expected and is not a code regression.

  ONE FILE of edu-suite's is carved out and always runs: `edu-suite-store`. test_ownership.py
  imports nothing but `dashboard.store` (stdlib sqlite3), and it is the only standing guard on
  the E2 per-user job-ownership fix from the 2026-08-06 security audit. Blanket-excluding the
  rail left that guard running nowhere, so it gets its own target rather than waiting on somebody
  to remember the container command. Keep this target file-scoped: point it at a directory and it
  will drag the heavy imports back in.
#>
[CmdletBinding()]
param(
  [string]$Rail,          # run a single target: a rail id, or 'core'
  [switch]$IncludeLive,   # include tests that hit real external services (flaky by nature)
  [switch]$IncludeEdu,    # attempt edu-suite despite the heavy-stack requirement above
  [switch]$List,          # print the plan and exit
  [switch]$Doctor         # check the venv can import the core packages, then exit
)

$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Py   = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Py)) { throw "no repo venv at $Py - create it and pip install -e the core packages." }

# --- the plan ---------------------------------------------------------------
# Each target: Name, Paths (test dirs), PyPath (extra sys.path entries, ';'-joined).
$targets = @(
  @{ Name = 'core'; PyPath = @();
     Paths = @('services\broker\tests', 'apps\platform\backend\tests', 'packages\platform_core\tests') }
)

# Rail source root: `src/` if present, else `backend/`, else the rail dir. (Matches every rail's
# actual layout; asserted by the discovery below rather than hardcoded per rail.)
foreach ($dir in (Get-ChildItem (Join-Path $Root 'rails') -Directory | Sort-Object Name)) {
  $id = $dir.Name
  if ($id -eq 'edu-suite' -and -not $IncludeEdu) { continue }

  $testDirs = @(Get-ChildItem $dir.FullName -Directory -Recurse -Filter 'tests' -ErrorAction SilentlyContinue |
                Where-Object { $_.FullName -notmatch '\\(native|node_modules|\.venv)\\' } |
                ForEach-Object { $_.FullName.Substring($Root.Length + 1) })
  if ($testDirs.Count -eq 0) { continue }

  $src = if     (Test-Path (Join-Path $dir.FullName 'src'))     { Join-Path $dir.FullName 'src' }
         elseif (Test-Path (Join-Path $dir.FullName 'backend')) { Join-Path $dir.FullName 'backend' }
         else                                                   { $dir.FullName }
  $pyPath = @($src)
  # edu-suite is a multi-package tree: the app under test plus the shared media package.
  if ($id -eq 'edu-suite') {
    $pyPath = @((Join-Path $dir.FullName 'apps\dashboard'),
                (Join-Path $PSScriptRoot 'packages\edu-media-core\src'))
  }
  $targets += @{ Name = $id; Paths = $testDirs; PyPath = $pyPath }
}

# edu-suite's store-only tests (see the header). File-scoped, not directory-scoped: the other two
# files in that directory import edu_media_core and would fail collection on the repo venv.
$eduStore = @('rails\edu-suite\apps\dashboard\tests\test_ownership.py',
              'rails\edu-suite\apps\dashboard\tests\test_levels.py') |
            Where-Object { Test-Path (Join-Path $Root $_) }
if ($eduStore) {
  $targets += @{ Name = 'edu-suite-store'; Paths = @($eduStore)
                 PyPath = @((Join-Path $Root 'rails\edu-suite\apps\dashboard\src')) }
}

if ($Rail) {
  $targets = @($targets | Where-Object { $_.Name -eq $Rail })
  if ($targets.Count -eq 0) { throw "no target '$Rail'. Use -List to see the available targets." }
}

if ($Doctor) {
  '--- venv doctor ---'
  "python: $Py"
  foreach ($m in @('platform_core', 'platform_gateway_app', 'pytest')) {
    & $Py -c "import $m, sys; print('  OK   $m ->', getattr($m, '__file__', 'builtin'))" 2>&1 | Out-String | Write-Host -NoNewline
    if ($LASTEXITCODE -ne 0) {
      Write-Host "  FAIL $m is not importable."
      Write-Host '       fix: .venv\Scripts\python.exe -m pip install -e packages\platform_core -e services\broker -e apps\platform\backend'
    }
  }
  return
}

if ($List) {
  '--- test plan ---'
  foreach ($t in $targets) { "{0,-24} {1}" -f $t.Name, ($t.Paths -join ', ') }
  "`n{0} target(s). live tests: {1}. edu-suite: {2} (its store-only tests always run)." -f $targets.Count,
    $(if ($IncludeLive) { 'included' } else { 'deselected' }),
    $(if ($IncludeEdu) { 'attempted' } else { 'excluded (heavy media stack)' })
  return
}

# --- run --------------------------------------------------------------------
# Live tests hit real external services (a rail that queries a real external API). They are already guarded by a
# reachability check, but a reachable-yet-empty board still fails, so they are out by default:
# a suite that goes red because someone else's website changed teaches you to ignore red.
$kArgs = if ($IncludeLive) { @() } else { @('-k', 'not LiveTests') }

$results = @()
$startedAll = Get-Date
# Every $t.Paths entry is repo-RELATIVE, so the run has to happen from the repo root. Without
# this, invoking the script by absolute path from anywhere else failed every target with
# "file or directory not found: rails\<x>\tests" — a red suite that says nothing about the code.
# finally, not a trailing Pop-Location: the summary below exits 1 on failure.
Push-Location $Root
try {
  foreach ($t in $targets) {
    Write-Host ("`n=== {0} ===" -f $t.Name) -ForegroundColor Cyan
    $env:PYTHONPATH = ($t.PyPath -join ';')
    $started = Get-Date
    & $Py -m pytest @($t.Paths) -q -p no:cacheprovider @kArgs
    $code = $LASTEXITCODE
    $results += [pscustomobject]@{
      Target = $t.Name
      Result = if ($code -eq 0) { 'PASS' } elseif ($code -eq 5) { 'NO TESTS' } else { "FAIL ($code)" }
      Seconds = [math]::Round(((Get-Date) - $started).TotalSeconds, 1)
    }
  }
} finally {
  $env:PYTHONPATH = ''
  Pop-Location
}

Write-Host "`n================ summary ================" -ForegroundColor Cyan
$results | ForEach-Object {
  $c = if ($_.Result -eq 'PASS') { 'Green' } elseif ($_.Result -eq 'NO TESTS') { 'Yellow' } else { 'Red' }
  Write-Host ("{0,-24} {1,-12} {2,6}s" -f $_.Target, $_.Result, $_.Seconds) -ForegroundColor $c
}
$failed = @($results | Where-Object { $_.Result -like 'FAIL*' })
Write-Host ("{0} target(s) in {1}s; {2} failed." -f $results.Count,
  [math]::Round(((Get-Date) - $startedAll).TotalSeconds, 1), $failed.Count)
if ($failed.Count -gt 0) { exit 1 }
