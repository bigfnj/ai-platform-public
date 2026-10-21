<#
  Run the platform's Python test suites — core + every rail — from one command.

    .\run-tests.ps1                  # core + all rails (live/network tests deselected)
    .\run-tests.ps1 -Rail edu-suite # one rail (or -Rail core for just the core suites)
    .\run-tests.ps1 -IncludeLive     # also run tests that hit real external services
    .\run-tests.ps1 -IncludeEdu      # no-op since 2026-09-14; edu-suite always runs now
    .\run-tests.ps1 -List            # show what would run, run nothing
    .\run-tests.ps1 -List -Json      # the same plan as JSON, each target's PYTHONPATH included

  The pytest targets' directories, PYTHONPATH and arguments are data, in tools\test-targets.json,
  which the public CI driver (.github\scripts\run_public_suite.py) reads too.

  Beside the pytest targets: node:test files (each its own target), and a <name>-types target per
  frontend that runs its own TypeScript as `tsc --noEmit -p tsconfig.json` (the shell, every rail
  frontend, edu-suite's dashboard; web-core is checked inside each of them).

  EXIT CODES. 0 = every target ran and passed. 1 = a target failed. 2 = nothing failed, but at
  least one target checked NOTHING: pytest collected or selected no test (exit 5, e.g. a rail
  whose only tests are LiveTests), a node target found no node, or a frontend had no
  node_modules to type-check with (NO MODULES; `npm ci` in that frontend). Those used to be a
  yellow row in the table and exit 0, so a clean checkout without node passed with five suites unrun.

  WHY THIS EXISTS. Every suite here is in-process pytest, but each rail is its own package with
  its own layout (`src/` for most, `backend/` for terminal-fun + workstation), so there was no
  single command that ran them all — and consequently no baseline. That gap hid a real fault: the
  repo .venv's editable installs still pointed at where the checkout lived before it moved,
  so `import platform_core` failed and NOTHING was runnable for three days without anyone
  noticing. If this script won't import, re-run the editable installs (see -Doctor).

  -Doctor runs tools\venv_doctor.py: every dependency each rail's pyproject.toml (and core's)
  declares, checked installed, at the declared version, and importable, reported by rail with
  one `pip install` line for what is missing. Exit 1 when anything is. -Rail narrows it.

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
  torch lives in <ai-work>\venvs\media-venv (image) and tts-venv (XTTS), the deployed
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
  [switch]$Json,          # with -List: print the plan as JSON, each target's PYTHONPATH included
  [switch]$Doctor         # check the venv against every manifest's declared deps, then exit
)

$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
# The interpreter: the checkout's TEST venv, .venv-test, when it has one, else .venv. On a deployed
# box .venv is the broker SERVICE's interpreter (install-native.ps1 and install-services.ps1 both run
# platform-broker from it), so a test recipe that pip-installs pytest and editable packages into
# .venv changes the running service's packages underneath it; .venv-test keeps the two apart. A
# checkout with only .venv (every dev box so far) runs exactly as it did.
$VenvNames = @('.venv-test', '.venv')
function Find-RepoPython([string]$Dir) {
  foreach ($n in $VenvNames) { $c = Join-Path $Dir "$n\Scripts\python.exe"; if (Test-Path $c) { return $c } }
  return $null
}
$Py = Find-RepoPython $Root
# A git WORKTREE has no venv of its own; it lives beside the main checkout. Without this fallback
# the suite simply refused to run from a worktree, which is where parallel work happens.
if (-not $Py) {
  try {
    $common = (& git -C $Root rev-parse --path-format=absolute --git-common-dir 2>$null)
    if ($common) { $Py = Find-RepoPython (Split-Path ($common.Trim()) -Parent) }
  } catch { }
}
if (-not $Py) { throw "no repo venv (.venv-test or .venv) under $Root - create one and pip install -e the core packages." }

# --- the plan ---------------------------------------------------------------
# Each target: Name, Paths (test dirs), PyPath (extra sys.path entries, ';'-joined).
# The pytest targets' Paths, PyPath and PytestArgs come from tools\test-targets.json, the table the
# public CI driver reads as well (.github\scripts\run_public_suite.py, which cannot run this script
# on its runner). The driver restated them by hand until 2026-10-19 and had drifted: its rails had
# their source root alone on PYTHONPATH and edu-suite its rail dir, so a rail test that imported a
# shared library passed here and failed, or imported another copy, in the public snapshot's CI.
$TargetTable = Get-Content -Raw -LiteralPath (Join-Path $Root 'tools\test-targets.json') | ConvertFrom-Json
# A table path (repo-relative, '/') as this tree's own path.
function Get-TreePath([string]$Rel) { Join-Path $Root ($Rel -replace '/', '\') }
# core's PyPath is THIS tree's own source roots, first on sys.path. It used to be empty and lean on
# the venv's editable installs, which record one absolute checkout path: run from any other tree
# (a worktree, a moved checkout) and core imported the broker and gateway from somewhere else,
# testing code the run never touched while reporting green. Found 2026-10-07 merging origin/main.
# core's PytestArgs pin asyncio_mode. The root has no pytest config, so pytest takes the first path's
# (services\broker's sets auto); with another path first it took packages\platform_core's, strict,
# and 304 tests in the 13 files with async tests failed. Pinned, the order of Paths no longer matters.
$targets = @(
  @{ Name = 'core'; PyPath = @($TargetTable.core.pythonpath | ForEach-Object { Get-TreePath $_ });
     Paths = @($TargetTable.core.paths | ForEach-Object { $_ -replace '/', '\' });
     PytestArgs = @($TargetTable.core.pytest_args) }
)

# Rail source root, by the table's `rail` rule: the rail's own entry in source_root_of if it has one,
# else the first of source_roots (`src/`, then `backend/`) present, else the rail dir. (Matches every
# rail's actual layout; asserted by the discovery below rather than hardcoded per rail.)
# edu-suite is the one entry in source_root_of. It is a multi-package tree: the app under test plus
# the shared media package. The app's package is apps\dashboard\src\dashboard, so the root is src:
# apps\dashboard put nothing importable on the path, and test_iep / test_levels / test_ownership /
# test_shared_workspaces passed only because a file collected before them had pushed src onto
# sys.path itself.
function Get-RailRoot([string]$RailDir) {
  $own = $TargetTable.rail.source_root_of.PSObject.Properties[(Split-Path $RailDir -Leaf)]
  if ($own) { return (Join-Path $RailDir ($own.Value -replace '/', '\')) }
  foreach ($s in @($TargetTable.rail.source_roots)) {
    $c = Join-Path $RailDir ($s -replace '/', '\')
    if (Test-Path $c) { return $c }
  }
  return $RailDir
}
# The shared libraries come from THIS tree as well, for the reason core's PyPath does: the venv's
# editable installs record the main checkout, so a rail target run from a worktree imported
# platform_core and edu-media-core from there and tested the rail against code the run never
# touched. Found when a branch had deleted BrokerClient.embed(): the main checkout still had
# it, and every rail suite run from the branch's worktree imported the main checkout's copy.
$shared = @($TargetTable.rail.shared | ForEach-Object { Get-TreePath $_ })
# The rails\ directories in ORDINAL name order, the order Python's sorted() gives the public CI
# driver. Both loops over the rails take them from here. They used Sort-Object Name, which compares
# by the engine's culture, and the two engines' cultures differ: Windows PowerShell 5.1 (NLS) passes
# over a hyphen, so co-worker came after course-builder, and pwsh (ICU) puts an underscore before a
# digit. The plan, and -List, came out in a different order under each engine.
# Both arrays are TYPED: handed an untyped one, PowerShell passes [Array]::Sort a converted copy,
# sorts that, and leaves $dirs as it was (both engines; the order seen was Get-ChildItem's own).
function Get-RailDirs {
  [IO.DirectoryInfo[]]$dirs = @(Get-ChildItem (Join-Path $Root 'rails') -Directory)
  [string[]]$names = @($dirs | ForEach-Object { $_.Name })
  [Array]::Sort($names, $dirs, [StringComparer]::Ordinal)
  return $dirs
}
foreach ($dir in (Get-RailDirs)) {
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

  $pyPath = @(Get-RailRoot $dir.FullName) + $shared
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

# The IEP Present Levels intro's privacy sentence, which follows the writer chip: "nothing
# leaves the local machine" only when the local model answers this viewer. Own target for the
# same reason as the two above.
$copyTest = 'rails\edu-suite\apps\dashboard\frontend\tests\iep-copy.test.js'
if (Test-Path (Join-Path $Root $copyTest)) {
  $targets += @{ Name = 'edu-suite-copy'; Paths = @($copyTest); PyPath = @(); Node = $true }
}

# When the dashboard's job polls stop (a terminal status, a deleted job), whether Start may queue
# a job (every workflow needs a file but cvc's sample set), and the debounced search. Own target.
$jobsTest = 'rails\edu-suite\apps\dashboard\frontend\tests\job-poll.test.js'
if (Test-Path (Join-Path $Root $jobsTest)) {
  $targets += @{ Name = 'edu-suite-jobs'; Paths = @($jobsTest); PyPath = @(); Node = $true }
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

# What a rail's model chip and the widget's Claude section say. Own target for the reason
# above: appended to web-core-dictate it would list as wired up and never run. The second
# claim it guards, that a chip from an older broker renders exactly as before, has no visible
# failure at all on the box where the new broker is running.
$chipsTest = 'web\tests\model-chips.test.js'
if (Test-Path (Join-Path $Root $chipsTest)) {
  $targets += @{ Name = 'web-core-chips'; Paths = @($chipsTest); PyPath = @(); Node = $true }
}

# Every frontend sink that turns text into HTML goes through the rails' markdown sanitiser.
# Own target for the reason above. The sanitiser's behaviour in a real DOM is the browser
# half, web\tests\browser\railcheck.py, which needs built bundles and so is not a target here.
$sinksTest = 'web\tests\markdown-sinks.test.js'
if (Test-Path (Join-Path $Root $sinksTest)) {
  $targets += @{ Name = 'web-core-sinks'; Paths = @($sinksTest); PyPath = @(); Node = $true }
}

# The shared poller (hidden-tab skip, no overlap, stop on a verdict or unmount). Own target.
$pollTest = 'web\tests\poll.test.js'
if (Test-Path (Join-Path $Root $pollTest)) {
  $targets += @{ Name = 'web-core-poll'; Paths = @($pollTest); PyPath = @(); Node = $true }
}

# No raw setInterval left in the frontends that poll through usePoll. Own target.
$pollAdoptTest = 'web\tests\poll-adoption.test.js'
if (Test-Path (Join-Path $Root $pollAdoptTest)) {
  $targets += @{ Name = 'web-core-poll-adoption'; Paths = @($pollAdoptTest); PyPath = @(); Node = $true }
}

# Devices read at call time (web-core is not a federation singleton), and a read-aloud chip
# stopping only its own playback. Own target.
$voiceDevTest = 'web\tests\voice-devices.test.js'
if (Test-Path (Join-Path $Root $voiceDevTest)) {
  $targets += @{ Name = 'web-core-voice'; Paths = @($voiceDevTest); PyPath = @(); Node = $true }
}

# Every value web-core exports has an importer, or a stated reason to stay. Own target.
$exportsTest = 'web\tests\webcore-exports.test.js'
if (Test-Path (Join-Path $Root $exportsTest)) {
  $targets += @{ Name = 'web-core-exports'; Paths = @($exportsTest); PyPath = @(); Node = $true }
}

# The shell's status-poll decision (a 401 is a lost session) and its canonical-URL redirect.
$shellStatusTest = 'apps\platform\frontend\tests\shell-status.test.js'
if (Test-Path (Join-Path $Root $shellStatusTest)) {
  $targets += @{ Name = 'shell-status'; Paths = @($shellStatusTest); PyPath = @(); Node = $true }
}

# The admin Rails tab's shared-role note (a repoint moves every rail on the role). Own target.
$adminRailsTest = 'apps\platform\frontend\tests\admin-rails.test.js'
if (Test-Path (Join-Path $Root $adminRailsTest)) {
  $targets += @{ Name = 'shell-admin-rails'; Paths = @($adminRailsTest); PyPath = @(); Node = $true }
}

# course-builder's progress-stream reader: an event split across chunks, and a stream that
# ends without done/error. A rail's FRONTEND test, so its own target: the rail's pytest target
# collects only test_*.py.
$cbSseTest = 'rails\course-builder\frontend\tests\sse.test.js'
if (Test-Path (Join-Path $Root $cbSseTest)) {
  $targets += @{ Name = 'course-builder-sse'; Paths = @($cbSseTest); PyPath = @(); Node = $true }
}

# servicenow's two WebSocket streams: every ending that is not `done` reaches onError, once.
$snStreamsTest = 'rails\servicenow\frontend\tests\streams.test.js'
if (Test-Path (Join-Path $Root $snStreamsTest)) {
  $targets += @{ Name = 'servicenow-streams'; Paths = @($snStreamsTest); PyPath = @(); Node = $true }
}

# job-aid's Runs view: when it stops polling a run (a worker that exited mid-run is over).
$jaRunsTest = 'rails\job-aid\frontend\tests\run-poll.test.js'
if (Test-Path (Join-Path $Root $jaRunsTest)) {
  $targets += @{ Name = 'job-aid-runs'; Paths = @($jaRunsTest); PyPath = @(); Node = $true }
}

# finance's shared reads: one request per thing in flight and for its TTL, failures not kept.
$finSharedTest = 'rails\finance\frontend\tests\shared-reads.test.js'
if (Test-Path (Join-Path $Root $finSharedTest)) {
  $targets += @{ Name = 'finance-reads'; Paths = @($finSharedTest); PyPath = @(); Node = $true }
}

# recipe-book's note on a stale or failed AI search index.
$rbNoticeTest = 'rails\recipe-book\frontend\tests\search-notice.test.js'
if (Test-Path (Join-Path $Root $rbNoticeTest)) {
  $targets += @{ Name = 'recipe-book-search'; Paths = @($rbNoticeTest); PyPath = @(); Node = $true }
}

# IEP Goals (rails\iep): a refusal shows the backend's sentence, not raw JSON. Named iep-*, so
# -Rail iep runs it with the rail's own pytest target.
$iepUiTest = 'rails\iep\frontend\tests\iep-goals.test.js'
if (Test-Path (Join-Path $Root $iepUiTest)) {
  $targets += @{ Name = 'iep-goals-ui'; Paths = @($iepUiTest); PyPath = @(); Node = $true }
}

# bouquet's and ai-voice's job polls: an abort ends them, a blip does not, a lost job does.
$bqPollTest = 'rails\bouquet\frontend\tests\poll-job.test.js'
if (Test-Path (Join-Path $Root $bqPollTest)) {
  $targets += @{ Name = 'bouquet-jobs'; Paths = @($bqPollTest); PyPath = @(); Node = $true }
}
$avPollTest = 'rails\ai-voice\frontend\tests\poll-job.test.js'
if (Test-Path (Join-Path $Root $avPollTest)) {
  $targets += @{ Name = 'ai-voice-jobs'; Paths = @($avPollTest); PyPath = @(); Node = $true }
}

# The shell's desk pet engine (apps\platform\frontend\src\deskpet\esheep.js, a vendored fork):
# its expression evaluator, which a malformed pet XML must not get past, and the spawn filter
# that keeps the bathtub gag on screen. Own target for the reason above.
$petTest = 'apps\platform\frontend\tests\deskpet-engine.test.js'
if (Test-Path (Join-Path $Root $petTest)) {
  $targets += @{ Name = 'shell-deskpet'; Paths = @($petTest); PyPath = @(); Node = $true }
}
# Where the pet's speech bubble goes (src\deskpet\bubble.ts): inside the window on every edge.
$bubbleTest = 'apps\platform\frontend\tests\deskpet-bubble.test.js'
if (Test-Path (Join-Path $Root $bubbleTest)) {
  $targets += @{ Name = 'shell-deskpet-bubble'; Paths = @($bubbleTest); PyPath = @(); Node = $true }
}

# Four rails' frontend state machines: the logic their components render (the Brief tab's
# synthesis poll, the answer pane's stream and Stop, Search's fan-out, the voice session), pulled
# out of the components so it could be tested at all with no React harness. One target per file
# for the reason above, each named <rail>-ui so -Rail <rail> runs it beside the rail's pytest.
foreach ($ui in @(
    @{ Name = 'co-worker-ui'; Path = 'rails\co-worker\frontend\tests\brief-sync.test.js' },
    @{ Name = 'gemini-cx-ui'; Path = 'rails\gemini-cx\frontend\tests\ask-flow.test.js' },
    @{ Name = 'meeting-atlas-ui'; Path = 'rails\meeting-atlas\frontend\tests\limit.test.js' },
    @{ Name = 'smb-partner-enablement-ui'; Path = 'rails\smb-partner-enablement\frontend\tests\voice-flows.test.js' })) {
  if (Test-Path (Join-Path $Root $ui.Path)) {
    $targets += @{ Name = $ui.Name; Paths = @($ui.Path); PyPath = @(); Node = $true }
  }
}

# Every frontend, TYPE-CHECKED: tsc --noEmit against its own tsconfig.json with its own TypeScript
# (node_modules\typescript), which is the `tsc` step of each `tsc && vite build`. Nothing here
# built or checked a frontend before, so a type error in a rail (or the shell, or web-core) was
# found only by whoever next built that rail; the node targets above test logic, not types.
# Covered: the shell, every rails\<rail>\frontend with a tsconfig.json, and edu-suite's dashboard.
# web\ has no tsconfig of its own: every consumer's tsconfig INCLUDES web-core's src (through the
# node_modules\@platform\web-core junction to this tree's web\), so it is checked in each of them.
# smb-partner-enablement's mobile bundle is in that rail's tsconfig include, so its target covers it.
# A frontend without node_modules is NO MODULES: did not run, named in the table, exit 2, never a
# pass. Named <rail>-types so -Rail <rail> type-checks the rail too.
$tscDirs = @(@{ Name = 'shell-types'; Dir = 'apps\platform\frontend' })
foreach ($fe in @(Get-RailDirs)) {
  if (Test-Path (Join-Path $fe.FullName 'frontend\tsconfig.json')) {
    $tscDirs += @{ Name = "$($fe.Name)-types"; Dir = "rails\$($fe.Name)\frontend" }
  }
}
$tscDirs += @{ Name = 'edu-suite-types'; Dir = 'rails\edu-suite\apps\dashboard\frontend' }
foreach ($fe in $tscDirs) {
  if (Test-Path (Join-Path $Root (Join-Path $fe.Dir 'tsconfig.json'))) {
    $targets += @{ Name = $fe.Name; Paths = @($fe.Dir); PyPath = @(); Tsc = $true }
  }
}

if ($Rail) {
  # Exact name OR "<name>-*", so -Rail edu-suite runs all four of its targets rather than
  # only the pytest one. It used to be -eq, which silently skipped edu-suite-store,
  # edu-suite-site and edu-suite-ui: the run said PASS and three of the rail's four
  # targets, including both node suites, had not executed. A filter that quietly narrows
  # what it checks is worse than one that errors.
  #
  # The suffixed names are each a rail's own node suites: edu-suite-{store,site,ui,actions,
  # copy,jobs}, iep-goals-ui (rails\iep is the IEP Goals rail, so -Rail iep runs it too, on
  # purpose), and the <rail>-<suite> frontend targets above. Check -List before adding a
  # target whose name is a prefix of a DIFFERENT rail's.
  $targets = @($targets | Where-Object { $_.Name -eq $Rail -or $_.Name -like "$Rail-*" })
  if ($targets.Count -eq 0) { throw "no target '$Rail'. Use -List to see the available targets." }
}

if ($Doctor) {
  '--- venv doctor ---'
  "python: $Py"
  # The manifests are the source of truth. This used to import five packages named here by hand,
  # while rails declared python-multipart, cryptography, numpy and openai that a venv built per
  # docs/INSTALL.md could lack; those rails then failed at COLLECTION, reported as ordinary test
  # failures. The deliberate absences (the GPU media stack, lazily imported extras) are listed,
  # with reasons, in the doctor itself.
  $dargs = @((Join-Path $Root 'tools\venv_doctor.py'), '--repo', $Root)
  if ($Rail) { $dargs += @('--rail', $Rail) }
  & $Py @dargs
  exit $LASTEXITCODE
}

if ($Json -and -not $List) { throw "-Json goes with -List: .\run-tests.ps1 -List -Json" }
if ($List -and $Json) {
  # Every target with what it runs under, PYTHONPATH included, for a reader that has to agree with
  # this script: tools\tests\test_public_suite_plan.py holds the public CI driver to it.
  ConvertTo-Json -Depth 4 -InputObject @($targets | ForEach-Object {
    [ordered]@{ Name = $_.Name; Paths = @($_.Paths); PyPath = @($_.PyPath)
                PytestArgs = @($_.PytestArgs | Where-Object { $_ }) } })
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
# The opt-ins the live tests check THEMSELVES, so a bare pytest can never spend quota: CLI_LIVE
# for the Claude CLI (services/broker), JOB_AID_LIVE_TESTS for job-aid's keyed boards. Set only
# for -IncludeLive and cleared otherwise, so a value left in the caller's shell cannot leak in;
# the caller's own values are put back in the finally below.
# Remove-Item/Set-Item rather than [Environment]::SetEnvironmentVariable($v, $null): pwsh binds
# that $null as "", which leaves the variable SET to empty instead of removing it.
$liveVars = @('CLI_LIVE', 'JOB_AID_LIVE_TESTS')
$liveSaved = @{}
# PYTHONPATH is set per target below; the caller's own value comes back afterwards. The finally
# used to set it to '', which deleted whatever the calling shell had.
$callerPythonPath = [Environment]::GetEnvironmentVariable('PYTHONPATH', 'Process')
foreach ($v in $liveVars) {
  $liveSaved[$v] = [Environment]::GetEnvironmentVariable($v, 'Process')
  if ($IncludeLive) { Set-Item -Path "Env:$v" -Value '1' }
  else { Remove-Item -Path "Env:$v" -ErrorAction SilentlyContinue }
}

$results = @()
$startedAll = Get-Date

# Every target's output is kept on disk, because the summary table alone cannot tell you WHICH
# test failed, and a flaky target that passes on re-run takes the only copy of its failure with
# it. That has happened more than once: a failure seen once and gone by the diagnosing run.
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
    $result = $null
    if ($t.Node) {
      $node = (Get-Command node -ErrorAction SilentlyContinue)
      if ($node) { $code = Invoke-Logged $log { & $node.Source @($t.Paths) } }
      else {
        Write-Host '  node not on PATH - skipped'
        Set-Content -Path $log -Value 'node not on PATH - skipped' -Encoding UTF8
        $result = 'NO NODE'
      }
    } elseif ($t.Tsc) {
      $node = (Get-Command node -ErrorAction SilentlyContinue)
      $feDir = $t.Paths[0]
      $tscJs = Join-Path $Root (Join-Path $feDir 'node_modules\typescript\bin\tsc')
      if (-not $node) {
        Write-Host '  node not on PATH - skipped'
        Set-Content -Path $log -Value 'node not on PATH - skipped' -Encoding UTF8
        $result = 'NO NODE'
      } elseif (-not (Test-Path -LiteralPath $tscJs)) {
        $why = "no node_modules\typescript in $feDir - not type-checked (npm ci there)"
        Write-Host "  $why" -ForegroundColor Yellow
        Set-Content -Path $log -Value $why -Encoding UTF8
        $result = 'NO MODULES'
      } else {
        # --pretty false: one line per error, "<file>(line,col): error TSnnnn: ...", so the log
        # and the table name the file. Paths print relative to the repo root (the cwd here).
        $tsconfig = Join-Path $feDir 'tsconfig.json'
        $code = Invoke-Logged $log { & $node.Source $tscJs --noEmit -p $tsconfig --pretty false }
      }
    } else {
      $pyArgs = if ($t.PytestArgs) { @($t.PytestArgs) } else { @() }
      $code = Invoke-Logged $log { & $Py -m pytest @($t.Paths) -q -p no:cacheprovider @kArgs @pyArgs }
    }
    # NO TESTS and NO NODE are "did not run", counted and reflected in the exit code (2) below.
    # pytest's 5 means it collected or selected nothing, which a deselected-only target also is.
    if (-not $result) {
      $result = if ($code -eq 0) { 'PASS' } elseif ($code -eq 5 -and -not $t.Node) { 'NO TESTS' } else { "FAIL ($code)" }
    }
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
  if ($null -eq $callerPythonPath) { Remove-Item -Path Env:PYTHONPATH -ErrorAction SilentlyContinue }
  else { $env:PYTHONPATH = $callerPythonPath }
  foreach ($v in $liveVars) {
    if ($null -eq $liveSaved[$v]) { Remove-Item -Path "Env:$v" -ErrorAction SilentlyContinue }
    else { Set-Item -Path "Env:$v" -Value $liveSaved[$v] }
  }
  Pop-Location
}

Write-Host "`n================ summary ================" -ForegroundColor Cyan
$results | ForEach-Object {
  $c = if ($_.Result -eq 'PASS') { 'Green' } elseif ($_.Result -like 'NO *') { 'Yellow' } else { 'Red' }
  Write-Host ("{0,-24} {1,-12} {2,6}s" -f $_.Target, $_.Result, $_.Seconds) -ForegroundColor $c
}
$failed = @($results | Where-Object { $_.Result -like 'FAIL*' })
$notRun = @($results | Where-Object { $_.Result -like 'NO *' })

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

# And the targets that checked nothing, with pytest's own last words (e.g. "5 deselected"), so
# the reason is under the table rather than inferred from a yellow row.
foreach ($n in $notRun) {
  $why = if ($n.Result -eq 'NO NODE') { 'node is not on PATH, so this node suite never ran' }
         elseif ($n.Result -eq 'NO MODULES') { 'this frontend has no node_modules, so it was not type-checked' }
         else { 'pytest collected or selected no test: every test deselected (-k), or none found' }
  Write-Host ("`n--- {0}: {1} ---" -f $n.Target, $why) -ForegroundColor Yellow
  @(Get-Content -LiteralPath $n.Log -Encoding UTF8 -ErrorAction SilentlyContinue) |
    Where-Object { $_ -match '\S' } | Select-Object -Last 3 | ForEach-Object { Write-Host "  $_" }
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

Write-Host ("{0} target(s) in {1}s; {2} failed; {3} did not run." -f $results.Count,
  [math]::Round(((Get-Date) - $startedAll).TotalSeconds, 1), $failed.Count, $notRun.Count)
if ($failed.Count -gt 0) { exit 1 }
if ($notRun.Count -gt 0) {
  Write-Host ("INCOMPLETE: {0} target(s) checked nothing ({1}). Exit 2, not a pass." -f $notRun.Count,
    (($notRun | ForEach-Object { $_.Target }) -join ', ')) -ForegroundColor Yellow
  exit 2
}
