<#
  Run the platform's Python test suites — core + every rail — from one command.

    .\run-tests.ps1                  # core + all rails (live/network tests deselected)
    .\run-tests.ps1 -Rail edu-suite # one rail (or -Rail core for just the core suites)
    .\run-tests.ps1 -IncludeLive     # also run tests that hit real external services
    .\run-tests.ps1 -IncludeEdu      # no-op since 2026-09-14; edu-suite always runs now
    .\run-tests.ps1 -List            # show what would run, run nothing

  WHY THIS EXISTS. Every suite here is in-process pytest, but each rail is its own package with
  its own layout (`src/` for most, `backend/` for terminal-fun + workstation), so there was no
  single command that ran them all — and consequently no baseline. That gap hid a real fault: the
  repo .venv's editable installs still pointed at the pre-migration `D:\.claude\projects\platform`
  paths, so `import platform_core` failed and NOTHING was runnable for three days without anyone
  noticing. If this script won't import, re-run the editable installs (see -Doctor).

  Rails are put on sys.path via PYTHONPATH rather than pip-installed, deliberately: installing 10
  rail packages into one venv invites the dependency collisions this repo isolates against.

  edu-suite RUNS by default since 2026-09-14. It was excluded for months on the stated
  grounds that its dashboard tests need "torch + torchaudio + coqui TTS + transformers==4.44.2
  + a capped diffusers". That was measured and is false: those tests STUB the GPU stack into
  sys.modules, and with torch, torchaudio, TTS, transformers and diffusers all absent from the
  repo venv, all 95 of them pass in 3.3s. The single missing dependency was `pytesseract`, a
  thin wrapper that edu_media_core/pdf.py imports at module level — no GPU, no model, ~40 KB.
  Add it (`pip install pytesseract`) and the rail is covered offline; -Doctor checks for it.

  What the old note got RIGHT is worth keeping: do NOT install the media stack into this venv.
  That is the move that once silently upgraded numpy 1.26 -> 2.4.6 and broke every image path.
  torch lives in D:\.ai-work\venvs\media-venv (image) and tts-venv (XTTS), the deployed
  dashboard container is torch-free for the same reason, and all GPU work goes to the broker.
  -IncludeEdu is now a no-op, kept so existing invocations and docs do not break.

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
  [switch]$IncludeEdu,    # no-op; edu-suite always runs. Kept so old invocations still work.
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
     Paths = @('services\broker\tests', 'apps\platform\backend\tests', 'packages\platform_core\tests', 'tools\tests',
               'packages\edu-media-core\tests') }
)

# Rail source root: `src/` if present, else `backend/`, else the rail dir. (Matches every rail's
# actual layout; asserted by the discovery below rather than hardcoded per rail.)
foreach ($dir in (Get-ChildItem (Join-Path $Root 'rails') -Directory | Sort-Object Name)) {
  $id = $dir.Name
  # edu-suite is no longer skipped: its tests stub the GPU stack and pass on a bare repo venv
  # with pytesseract present. See the header note for what was measured.

  # A `tests` dir with no test_*.py in it is not a pytest target. Without that last filter,
  # rails\edu-suite\apps\teachtown\tests — which holds only the node test — was claimed by BOTH
  # the pytest target and the node one. pytest collected nothing there and still exited 0 on
  # the strength of a sibling directory in the same invocation, so it read as deliberate.
  $testDirs = @(Get-ChildItem $dir.FullName -Directory -Recurse -Filter 'tests' -ErrorAction SilentlyContinue |
                Where-Object { $_.FullName -notmatch '\\(native|node_modules|\.venv)\\' } |
                Where-Object { Get-ChildItem $_.FullName -File -Filter 'test_*.py' -ErrorAction SilentlyContinue } |
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

# edu-suite's store-only tests. These three DO also run inside the `edu-suite` target now that
# the rail is no longer excluded, so the sweep executes them twice and the summary counts them
# as two targets. That duplication is deliberate and costs ~1.5s: these files import nothing but
# `dashboard.store` / `dashboard.access` (stdlib sqlite3), so this target still reports on the E2
# per-user ownership boundary on a venv where `edu_media_core` cannot import at all — which is
# exactly the state the whole rail was stuck in until 2026-09-14. A security boundary that only
# gets tested when the rest of the import graph is healthy is one that stops getting tested.
# Keep it file-scoped: point it at the directory and it drags the heavier imports back in.
$eduStore = @('rails\edu-suite\apps\dashboard\tests\test_ownership.py',
              'rails\edu-suite\apps\dashboard\tests\test_levels.py',
              'rails\edu-suite\apps\dashboard\tests\test_shared_workspaces.py') |
            Where-Object { Test-Path (Join-Path $Root $_) }
if ($eduStore) {
  $targets += @{ Name = 'edu-suite-store'; Paths = @($eduStore)
                 PyPath = @((Join-Path $Root 'rails\edu-suite\apps\dashboard\src')) }
}

# The interactive TeachTown site is plain JS a teacher opens off a USB stick, so its test is
# plain node — no framework, no npm install. It is listed here rather than left as a README
# command because a test nothing runs is not a test: the builder copies index.html into every
# generated bundle without parsing it, so a syntax error there yields a green build and a
# broken site. Skipped with NO NODE rather than failing the sweep on a box without node.
$siteTest = 'rails\edu-suite\apps\teachtown\tests\vocab-pictures.test.js'
if (Test-Path (Join-Path $Root $siteTest)) {
  $targets += @{ Name = 'edu-suite-site'; Paths = @($siteTest); PyPath = @(); Node = $true }
}

# The dashboard review screen's restore transition, pulled out of edit.tsx so it can be tested
# at all (there is no React harness here). Its OWN target rather than a second path on
# edu-suite-site: the runner does `node @($t.Paths)`, and node runs the first file and hands
# the rest to argv — so a second path there would read as wired up and never execute.
$uiTest = 'rails\edu-suite\apps\dashboard\frontend\tests\vocab-restore.test.js'
if (Test-Path (Join-Path $Root $uiTest)) {
  $targets += @{ Name = 'edu-suite-ui'; Paths = @($uiTest); PyPath = @(); Node = $true }
}

# Which row actions the library offers. Its own target for the same reason as the line above:
# `node @($t.Paths)` runs the first file and hands the rest to argv, so a second path on
# edu-suite-ui would read as wired up and never execute.
$actionsTest = 'rails\edu-suite\apps\dashboard\frontend\tests\job-actions.test.js'
if (Test-Path (Join-Path $Root $actionsTest)) {
  $targets += @{ Name = 'edu-suite-actions'; Paths = @($actionsTest); PyPath = @(); Node = $true }
}

# web-core, the SHARED frontend package every rail federates. Its read-aloud language
# resolver had no test target at all, which is how /v1/tts_light's American-English default
# reached Spanish text on every rail at once. Own target, same reason as the two above.
$webTest = 'web\tests\speech-lang.test.js'
if (Test-Path (Join-Path $Root $webTest)) {
  $targets += @{ Name = 'web-core-speech'; Paths = @($webTest); PyPath = @(); Node = $true }
}

# Its OWN target, deliberately not a second path on web-core-speech: the node runner below does
# `node @($t.Paths)`, which runs the FIRST path and hands the rest to the script as argv. A
# second file appended there reads as wired up in -List and never executes.
$dictateTest = 'web\tests\dictate-policy.test.js'
if (Test-Path (Join-Path $Root $dictateTest)) {
  $targets += @{ Name = 'web-core-dictate'; Paths = @($dictateTest); PyPath = @(); Node = $true }
}

if ($Rail) {
  # Exact name OR "<name>-*", so -Rail edu-suite runs all four of its targets rather than
  # only the pytest one. It used to be -eq, which silently skipped edu-suite-store,
  # edu-suite-site and edu-suite-ui: the run said PASS and three of the rail's four
  # targets, including both node suites, had not executed. A filter that quietly narrows
  # what it checks is worse than one that errors.
  #
  # No other target collides today: the suffixed names are edu-suite-{store,site,ui}, and
  # -Rail iep matches only iep (the iep-goals CONTAINER has no test target of its own,
  # because targets are derived from directories under rails/). Check -List before adding
  # a target whose name is a prefix of another.
  $targets = @($targets | Where-Object { $_.Name -eq $Rail -or $_.Name -like "$Rail-*" })
  if ($targets.Count -eq 0) { throw "no target '$Rail'. Use -List to see the available targets." }
}

if ($Doctor) {
  '--- venv doctor ---'
  "python: $Py"
  # pytesseract is here because edu_media_core/pdf.py imports it at module level, so its
  # absence fails 95 edu-suite tests at COLLECTION with an error that names pdf.py and not
  # the missing package. It is a thin wrapper: no GPU, no model, nothing to conflict with.
  # jsonschema backs tools/tests/test_rail_manifest_schema.py, the only check that the manifest
  # schema still describes the shipped manifests. That test skips without it (so one missing
  # package cannot stop the whole core target collecting), which makes this the place it shows.
  foreach ($m in @('platform_core', 'platform_gateway_app', 'pytest', 'pytesseract', 'jsonschema')) {
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
  "`n{0} target(s). live tests: {1}. edu-suite runs by default." -f $targets.Count,
    $(if ($IncludeLive) { 'included' } else { 'deselected' })
  return
}

# --- run --------------------------------------------------------------------
# Live tests hit real external services (a rail that queries a real external API). They are already guarded by a
# reachability check, but a reachable-yet-empty board still fails, so they are out by default:
# a suite that goes red because someone else's website changed teaches you to ignore red.
$kArgs = if ($IncludeLive) { @() } else { @('-k', 'not LiveTests') }

$results = @()
$startedAll = Get-Date

# Every target's output is kept on disk, because the summary table alone cannot tell you WHICH
# test failed, and a flaky target that passes on re-run takes the only copy of its failure with
# it. Recorded twice in docs/BACKLOG.md: a failure seen once and gone by the diagnosing run.
#   .test-logs/runs/<stamp>-<pid>/<target>.log   - this run; the last few runs are kept
#   .test-logs/failures/<stamp>-<pid>-<target>.log - kept across runs, so a flake survives
#
# ONE DIRECTORY PER RUN, not a shared "latest". The first version wrote latest/<target>.log, and
# two sweeps in the same checkout — which is ordinary: two terminals, an agent and a human —
# then fought over one file. The second StreamWriter threw, the run aborted with exit 1, and it
# read exactly like a test failure. Found by running two sweeps at once on purpose. The PID in
# the name makes a collision impossible rather than unlikely: the stamp only has 1 s resolution.
$LogDir      = Join-Path $Root '.test-logs'
$RunStamp    = Get-Date -Format 'yyyyMMdd-HHmmss'
$RunId       = '{0}-{1}' -f $RunStamp, $PID
$RunsDir     = Join-Path $LogDir 'runs'
$RunDir      = Join-Path $RunsDir $RunId
$FailuresDir = Join-Path $LogDir 'failures'
foreach ($d in $RunDir, $FailuresDir) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
$KeepRuns     = 10
$KeepFailures = 30

# Streams a native command's output to the console AND a UTF-8 log, returning its exit code.
# Two Windows PowerShell 5.1 traps shape this, and the gate runs scripts under 5.1:
#  - Tee-Object has no -Encoding there and writes UTF-16, so a plain grep for FAILED finds
#    nothing in the log it just wrote. Hence the explicit UTF-8 (no BOM) writer.
#  - `2>&1` on a native command under $ErrorActionPreference='Stop' promotes the FIRST stderr
#    line to a terminating error. A pytest DeprecationWarning would abort the whole sweep. So
#    EAP is relaxed for the call only and the command is judged by its exit code, which is how
#    it was judged before.
function Invoke-Logged([string]$LogPath, [scriptblock]$Command) {
  # LOGGING MUST NEVER BE WHAT FAILS A RUN. The per-run directory removes the known collision,
  # but a log can still fail to open for reasons nobody controls (an AV scanner holding the
  # handle, a full disk). Then this degrades to console-only with a warning and the command still
  # runs and is still judged by its exit code — the same result the sweep gave before logging.
  $writer = $null
  try {
    $writer = New-Object IO.StreamWriter($LogPath, $false, (New-Object Text.UTF8Encoding($false)))
  } catch {
    Write-Host ("  (could not open {0} - this target is not logged: {1})" -f $LogPath, $_.Exception.Message) -ForegroundColor Yellow
  }
  $oldEap = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try {
    & $Command 2>&1 | ForEach-Object {
      $line = "$_"
      Write-Host $line
      if ($writer) { $writer.WriteLine($line) }
    }
    return $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $oldEap
    if ($writer) { $writer.Dispose() }
  }
}
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
    $log = Join-Path $RunDir ("{0}.log" -f $t.Name)
    if ($t.Node) {
      $node = (Get-Command node -ErrorAction SilentlyContinue)
      if ($node) { $code = Invoke-Logged $log { & $node.Source @($t.Paths) } }
      else {
        Write-Host '  node not on PATH - skipped'
        Set-Content -Path $log -Value 'node not on PATH - skipped' -Encoding UTF8
        $code = 5
      }
    } else {
      $code = Invoke-Logged $log { & $Py -m pytest @($t.Paths) -q -p no:cacheprovider @kArgs }
    }
    $result = if ($code -eq 0) { 'PASS' } elseif ($code -eq 5) { 'NO TESTS' } else { "FAIL ($code)" }
    $kept = $null
    if ($result -like 'FAIL*' -and (Test-Path -LiteralPath $log)) {
      $kept = Join-Path $FailuresDir ("{0}-{1}.log" -f $RunId, $t.Name)
      # Best effort for the same reason as the log itself: an archive copy that cannot be made
      # must not turn a reported failure into a crash that hides it.
      try { Copy-Item -LiteralPath $log -Destination $kept -Force -ErrorAction Stop }
      catch { $kept = $log }
    }
    $results += [pscustomobject]@{
      Target  = $t.Name
      Result  = $result
      Seconds = [math]::Round(((Get-Date) - $started).TotalSeconds, 1)
      Log     = $log
      Kept    = $kept
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

# Name the failing tests HERE, under the table. They are also printed inside each target's own
# block, but that is scrolled away by the time the sweep ends — and anything reading only the
# tail of this output (an agent, a CI log excerpt) never saw it at all.
foreach ($f in $failed) {
  Write-Host ("`n--- {0} ---" -f $f.Target) -ForegroundColor Red
  $lines = @(Get-Content -LiteralPath $f.Log -Encoding UTF8 -ErrorAction SilentlyContinue)
  $named = @($lines | Where-Object { $_ -match '^(FAILED|ERROR) ' })
  # A collection error, an import failure or a node target prints no FAILED line, so fall back
  # to the tail rather than printing nothing for exactly the failures that are hardest to read.
  $show = if ($named.Count) { $named } else { $lines | Select-Object -Last 15 }
  $show | ForEach-Object { Write-Host "  $_" }
  Write-Host ("  full output: {0}" -f $f.Kept) -ForegroundColor DarkGray
}

# Bound both archives. Newest first by name, which is the timestamp prefix. This run is always
# the newest and is never pruned; a CONCURRENT run is the second newest and survives too unless
# ten more sweeps complete while it is still going.
Get-ChildItem -LiteralPath $FailuresDir -Filter '*.log' -ErrorAction SilentlyContinue |
  Sort-Object Name -Descending | Select-Object -Skip $KeepFailures |
  Remove-Item -Force -ErrorAction SilentlyContinue
Get-ChildItem -LiteralPath $RunsDir -Directory -ErrorAction SilentlyContinue |
  Sort-Object Name -Descending | Select-Object -Skip $KeepRuns |
  Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Write-Host ("logs: {0}" -f $RunDir) -ForegroundColor DarkGray

Write-Host ("{0} target(s) in {1}s; {2} failed." -f $results.Count,
  [math]::Round(((Get-Date) - $startedAll).TotalSeconds, 1), $failed.Count)
if ($failed.Count -gt 0) { exit 1 }
