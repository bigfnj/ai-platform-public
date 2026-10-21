# Install the NATIVE GPU-layer processes as Windows services (via NSSM) so hosting
# survives a reboot with no login at all. The containers (caddy/gateway/dashboard)
# already auto-restart via Docker Desktop; this covers the native side.
#
#   platform-broker : GPU/Model Broker on 0.0.0.0:11500 (+ its subprocess media worker); on a
#                     lean box, the lean installer's broker instead (its bind, media off: -Stack)
#   ollama          : `ollama serve` on 127.0.0.1:11434
#
# The Admin account on this box has NO password (PIN/Hello login), and Windows
# refuses to run a service as a blank-password local account. So the services run as
# LocalSystem (no account/password needed) and we point them at your profile's
# models/caches via env vars, since LocalSystem otherwise uses the wrong profile:
#   OLLAMA_MODELS -> your Ollama models   |   HF_HOME -> your HuggingFace cache
#   TTS_HOME      -> parent of your Coqui/XTTS model dir (%LOCALAPPDATA%). REQUIRED:
#     Coqui resolves its model dir from the HKCU "Local AppData" shell-folder registry
#     value, which does NOT exist for the LocalSystem account (winreg raises
#     FileNotFoundError), so without TTS_HOME the XTTS worker dies before it loads.
#     Pointing it at your profile also REUSES the ~2GB model already downloaded there
#     (no re-download — contrary to the earlier "no cache env override" belief).
#
# RUN THIS IN AN ELEVATED (Administrator) PowerShell:
#   powershell -ExecutionPolicy Bypass -File <repo>\deploy\install-services.ps1
#
# Skip the Ollama service (if it already auto-starts) with:  -SkipOllama
# Skip the Course Builder rail service with:                 -SkipCourseBuilder
# Skip the broker with:                                      -SkipBroker
#
# Media root (holds venvs\, models\, cache\) if it is not the checkout's grandparent:
#                                                            -AiWorkRoot <dir>
# This box's OWN broker maps, kept outside the checkout (each optional; each must exist):
#                                                            -RolesFile <path>
#                                                            -UpstreamsFile <path>
#                                                            -DisabledFile <path>
# e.g. from C:\Users\<you>\ai-platform on a box with an 8 GB card:
#   ... install-services.ps1 -AiWorkRoot "C:\Users\<you>\<work root>" -RolesFile <dir outside the checkout>\roles.json
#
# The -Skip* flags are [switch] parameters with SKIP names on purpose, and both halves of that matter.
#   * [switch], because -File is how this script is documented and run: under -File every
#     argument reaches PowerShell as a string, and a [bool] parameter refuses to bind one.
#     `-InstallOllama:$false`, `-InstallOllama $false`, `... False` and `... 0` ALL fail with
#     "Cannot convert value System.String to type System.Boolean" (measured 2026-09-29), so
#     the previous [bool] form documented an invocation that could not work at all.
#   * SKIP names, because a [switch] defaults to $false and these three installs default to
#     ON. Renaming [bool]$InstallOllama = $true to [switch]$InstallOllama would have inverted
#     that silently: a plain run would then install NOTHING while still reporting success,
#     which is a far worse failure than a parameter that refuses to bind. Inverting the name
#     with the type keeps "absence means do it".
# [CmdletBinding()] is what makes the change safe to land: without it a script `param()` block
# quietly drops unmatched named arguments into $args, so an old `-InstallOllama:$false` caller
# would be IGNORED and would install the service it asked to skip. As an advanced script, an
# unknown parameter is a hard binding error instead.
#
# Each service is its own switch so one can be (re)installed without disturbing the others.
# That matters more than it looks: Install-Svc reinstalls by teardown, so a run that only
# meant to add a rail would otherwise stop the broker, repoint it at THIS checkout, and hand
# it whatever services/broker/roles.json this tree happens to carry. On a box whose role map
# is tuned for its own GPU, that silently repoints every rail's model.

# -UserProfile is the profile the SERVICES should read models/caches/indices from. It defaults
# to the caller's, which is right when this is run from an elevated USER shell. It is wrong when
# the script itself runs as SYSTEM (e.g. via a privilege helper): $env:USERPROFILE is then
# systemprofile, and every path built from it points at a directory the real user never writes.
# That fails silently — the services install, start, and read empty directories — so the guard
# below refuses rather than letting it through.
#
# -AiWorkRoot is the directory holding venvs\{media,tts,kokoro}-venv, models\kokoro and
# cache\hf-cache; every media path in the broker's env is built from it. It defaults to the
# checkout's GRANDPARENT, which was right for exactly one layout (a checkout two levels under
# the work root, <work root>\<folder>\ai-platform) and is wrong for any other: from
# C:\Users\<you>\ai-platform it lands on C:\Users, and every media path becomes
# C:\Users\venvs\... Nothing fails at install
# time; the first image or speech job does. So a value that is a drive root or the users
# directory itself is refused, and the fix is to pass -AiWorkRoot explicitly. Missing venvs
# underneath are only WARNED about: a box with no media stack is a legitimate install (media
# ops report unavailable), and refusing it would also refuse the text broker it still needs.
#
# -RolesFile / -UpstreamsFile / -DisabledFile become BROKER_ROLES_FILE / BROKER_UPSTREAMS_FILE /
# BROKER_DISABLED_FILE. Omitted, the broker reads services\broker\{roles,upstreams,disabled}.json
# in THIS checkout, i.e. the tracked files, and the tracked roles.json is the map for the 24 GB
# card this repo is developed on. A smaller card needs its own map, and it belongs OUTSIDE the
# tree: the broker writes admin edits (/v1/roles, disabled models) back into that file, so a map
# inside the deployment checkout dirties it and turns every `git pull` into a conflict or a
# silent revert of the box's tuning. Each path given must exist. A typo is refused rather than
# passed through, because the broker reads a missing file as an empty overlay and quietly falls
# back to the 24 GB DEFAULT_ROLES, which is exactly the failure the flag exists to prevent.
#
# -BrokerCliEnvFile <path> sets the Claude CLI backend's settings (BROKER_CLI_ENABLED, _OWNERS,
# _EXE, _WORKDIR, _OAUTH_TOKEN_FILE, ...) from a KEY=value file kept OUTSIDE the checkout: they
# are this box's (the owner's usernames, the path to their claude.exe, the token file), so this
# tracked script does not hold them. Without the flag they are carried forward from the installed
# service as before; with it, the file's keys are merged over the carried-forward ones and `KEY=`
# removes one. Read by deploy\.env's rules (lib-runtime Read-ServiceEnvFile), only BROKER_CLI_*
# keys are accepted, and the paths are checked (Test-BrokerCliEnv: a real claude.exe, a token file
# that exists, a workdir with no CLAUDE.md at or above it) BEFORE anything is torn down. Every
# service's environment goes through Merge-ServiceEnv, so no blank element is ever written (NSSM
# ignores everything after one).
[CmdletBinding()]
param([switch]$SkipOllama,
      [switch]$SkipCourseBuilder,
      [switch]$SkipBroker,
      [string]$UserProfile = $env:USERPROFILE,
      # No default expressions for these four: under Windows PowerShell 5.1 $PSScriptRoot is
      # EMPTY inside param(), so a default built from it resolves against the caller's cwd.
      # $AiWorkRoot's default is computed in the body instead.
      [string]$AiWorkRoot,
      [string]$RolesFile,
      [string]$UpstreamsFile,
      [string]$DisabledFile,
      [string]$BrokerCliEnvFile,
      # The stack whose broker this is: full (0.0.0.0, media on) or lean (the lean installer's
      # spec). Default: lean when the installed broker runs with BROKER_MEDIA_ENABLED=false.
      [ValidateSet('', 'full', 'lean')][string]$Stack)

#Requires -RunAsAdministrator
$ErrorActionPreference = 'Stop'

# The rest of the script reads these three positively; the switches are the negation at the
# boundary, so nothing below has to be read inside-out.
$InstallOllama        = -not $SkipOllama
$InstallCourseBuilder = -not $SkipCourseBuilder
$InstallBroker        = -not $SkipBroker

$PlatformRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if ($UserProfile -like '*\config\systemprofile*' -or -not $UserProfile) {
    throw ("UserProfile resolved to '$UserProfile' - this script is running as SYSTEM, so " +
           "USERPROFILE is the service account's own profile rather than yours, and every " +
           "models/cache/index path built from it would point somewhere the real user never " +
           "writes. Re-run from an elevated USER shell, or pass it explicitly: " +
           "-UserProfile C:\Users\<you>")
}

# The historical derivation stays the default, so existing callers get the same value. Normalised
# without Resolve-Path, which would throw on a missing directory before the guard below could say
# what to do; a relative -AiWorkRoot resolves against the caller's location, not the service's.
$AiWorkRootGiven = [bool]$AiWorkRoot
if (-not $AiWorkRoot) { $AiWorkRoot = Join-Path $PlatformRoot '..\..' }
$AiWorkRoot = [IO.Path]::GetFullPath(
    $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($AiWorkRoot))
if ($AiWorkRoot -ne [IO.Path]::GetPathRoot($AiWorkRoot)) { $AiWorkRoot = $AiWorkRoot.TrimEnd('\') }
# Only the broker's env reads $AiWorkRoot, so only a broker install is refused over it: a rail-only
# run from a checkout whose grandparent is C:\Users should not fail on a value it never uses.
if ($InstallBroker) {
    $UsersDir = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList' `
                    -ErrorAction SilentlyContinue).ProfilesDirectory
    if (-not $UsersDir) { $UsersDir = "$env:SystemDrive\Users" }
    $BadRoot = $AiWorkRoot -eq [IO.Path]::GetPathRoot($AiWorkRoot) -or $AiWorkRoot -eq $UsersDir.TrimEnd('\')
    if ($BadRoot -and -not $AiWorkRootGiven) {
        # The DEFAULT lands here from the standard clone location (get.ps1 clones to
        # %USERPROFILE%\ai-platform), and refusing it made every fresh install fail, including the
        # apply engine's, which calls this script with no arguments. So the default falls back to
        # the profile: media paths then name a directory the user owns, the missing venvs are
        # warned about below, and the text broker still installs.
        Write-Warning ("This checkout's grandparent is '$AiWorkRoot', which cannot hold media venvs; " +
                       "using '$UserProfile' as the media root instead. Pass -AiWorkRoot to choose.")
        $AiWorkRoot = $UserProfile.TrimEnd('\')
    }
    elseif ($BadRoot) {
        throw ("-AiWorkRoot '$AiWorkRoot' is a drive root or the users directory itself. Every " +
               "media path would become '$AiWorkRoot\venvs\...', which nothing ever writes. Pass " +
               "the directory that holds venvs\ and models\: -AiWorkRoot 'C:\Users\<you>\<work root>'")
    }
    # A LocalSystem service reading interpreters and models out of a cloud-synced folder works only
    # while every file is fully downloaded: an online-only placeholder is a hydration request that a
    # service in session 0 cannot reliably satisfy. Allowed, because this box's media paths have
    # always been there, but said, so a media fix does not move gigabytes into it unawares.
    if ($AiWorkRoot -match '\\OneDrive( - [^\\]+)?(\\|$)') {
        Write-Warning ("-AiWorkRoot '$AiWorkRoot' is inside OneDrive. Keep media venvs and models " +
                       "out of a synced folder, or pin them 'Always keep on this device'.")
    }
    $MissingVenvs = @('media-venv', 'tts-venv', 'kokoro-venv') |
        Where-Object { -not (Test-Path -LiteralPath "$AiWorkRoot\venvs\$_\Scripts\python.exe") }
    if ($MissingVenvs) {
        Write-Warning ("No interpreter under '$AiWorkRoot\venvs' for: " + ($MissingVenvs -join ', ') +
                       ". The broker installs and serves text, but media ops (image, speech, " +
                       "transcription) will be unavailable until those venvs exist there, or " +
                       "-AiWorkRoot points at the directory where they do.")
    }
}

# lib-runtime: the env reader, the service-environment helpers and the NSSM log settings. Here,
# before the checks below, which use it.
. (Join-Path $PSScriptRoot 'installer\lib-runtime.ps1')

# Two rails this script reaches into are not in every checkout: the public snapshot withholds
# course-builder and ai-voice. It used to install course-builder from a directory that was not
# there (pip failed, its exit code unread, and NSSM crash-looped a service with no package) and
# point the broker's voice engines at ai-voice's. A rail that is not here is said and skipped.
$CourseBuilderDir = Join-Path $PlatformRoot 'rails\course-builder\backend'
if ($InstallCourseBuilder -and -not (Test-Path -LiteralPath $CourseBuilderDir -PathType Container)) {
    Write-Host '  course-builder: not in this checkout (no rails\course-builder), so its service is skipped' -ForegroundColor Yellow
    $InstallCourseBuilder = $false
}
$VoiceEnginesDir = Join-Path $PlatformRoot 'rails\ai-voice\native'
# Failures that do not stop the run (the other services still install and start), said at the end
# and in the exit code.
$Unfinished = @()

# -BrokerCliEnvFile: read and checked before NSSM is fetched or anything is torn down, so a bad
# file costs nothing. Its values are never printed: it may hold a token.
$CliFileEnv = @()
if ($BrokerCliEnvFile) {
    if (-not $InstallBroker) {
        throw "-BrokerCliEnvFile sets the broker service's environment, which -SkipBroker leaves alone. Drop one of the two."
    }
    $CliFileEnv = @(Read-ServiceEnvFile -Path $BrokerCliEnvFile -Prefix 'BROKER_CLI_')
    $CliProblems = @(Test-BrokerCliEnv -Entries $CliFileEnv -RepoRoot $PlatformRoot)
    if ($CliProblems.Count) {
        throw ("-BrokerCliEnvFile '$BrokerCliEnvFile' is refused; nothing was changed:`n  " + ($CliProblems -join "`n  "))
    }
}

# -RolesFile / -UpstreamsFile / -DisabledFile -> BROKER_*_FILE entries for the broker's env.
# Checked HERE, before NSSM is fetched or anything is torn down, so a typo costs nothing. Made
# absolute because the broker resolves a relative path against ITS working directory (this
# checkout's root), not yours. Emits nothing for an omitted flag, which keeps today's behaviour.
function Resolve-BrokerFile([string]$Param, [string]$Path, [string]$Var) {
    if (-not $Path) { return }
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw ("-$Param '$Path' does not exist. Refusing rather than passing it through as " +
               "$Var`: the broker reads a missing file as empty and would quietly fall back " +
               "to its defaults (for -RolesFile, the 24 GB DEFAULT_ROLES this flag exists to " +
               "replace). Fix the path, or omit the flag to use the tracked " +
               "services\broker\*.json in this checkout.")
    }
    "$Var=" + (Resolve-Path -LiteralPath $Path).ProviderPath
}
# Broker control-plane token (shared secret) from deploy/.env — injected into the broker's env so
# it enforces `Authorization: Bearer` on every /v1/* route. The rail containers get the SAME token
# via compose (which also reads deploy/.env). Read script-relative so it's robust to $PlatformRoot.
# Read-DotEnvFile (lib-runtime), so it is the value compose hands the rails: the first line used to
# win here and the last in compose, and a trailing comment or quotes became part of the token, so
# the broker and every rail could hold different secrets and every call 401'd. (lib-runtime was
# dot-sourced above.)
$EnvFile = Join-Path $PSScriptRoot '.env'
$BrokerToken = ([string](Get-DotEnvValue -Path $EnvFile -Key 'BROKER_AUTH_TOKEN')).Trim()

# Which stack's broker this box runs, decided before NSSM is fetched or anything is torn down, and
# before the installed service's map files are carried (below), which it decides. On a lean box
# (the installed broker has media off) it is registered as the lean installer registers it: the
# declared runtime's bind (loopback outside wsl), media off, deploy\local's maps; on the full
# stack, as it always was. lib-runtime Get-InstallServicesBrokerPlan says why. The installed
# service's environment is a plain registry read (lib-runtime Get-InstalledBrokerEnv).
if ($InstallBroker) {
    $ExistingBrokerEnv = @(Get-InstalledBrokerEnv)
    $fileParams = @()
    foreach ($n in @('RolesFile', 'DisabledFile')) { if ($PSBoundParameters.ContainsKey($n)) { $fileParams += $n } }
    $BrokerPlan = Get-InstallServicesBrokerPlan -Existing $ExistingBrokerEnv -Stack $Stack -EnvFile $EnvFile `
                      -Root $PlatformRoot -Token $BrokerToken -FileParams $fileParams
    if ($BrokerPlan.Problem) { throw "Refusing; nothing was changed: $($BrokerPlan.Problem)" }
    Write-Host "  broker: the $($BrokerPlan.Stack) stack's, on $($BrokerPlan.BindHost):11500 ($($BrokerPlan.Why))"
}

# An OMITTED flag keeps what the installed broker already uses. Re-running this script for some
# other reason (a code update, a course-builder reinstall without -SkipBroker) used to drop the
# box's own maps silently and put an 8 GB card back on the tracked 24 GB role map. Pass the flag
# with an empty value (-RolesFile '') to really go back to the tracked file.
# Not on a lean box for the role map and the disabled list: those are deploy\local's files, which
# the lean plan's overlay names anyway. Carried, a lean box whose deploy\local was wiped
# (`git clean -fdx`) was refused below with "-RolesFile ... does not exist" before the seed further
# down could put the file back, so the one way to repair it was to edit the registry by hand.
if ($InstallBroker) {
    $carry = @(@('RolesFile', 'BROKER_ROLES_FILE'), @('UpstreamsFile', 'BROKER_UPSTREAMS_FILE'),
               @('DisabledFile', 'BROKER_DISABLED_FILE'))
    if ($BrokerPlan.Stack -eq 'lean') { $carry = @(, @('UpstreamsFile', 'BROKER_UPSTREAMS_FILE')) }
    foreach ($pair in $carry) {
        if ($PSBoundParameters.ContainsKey($pair[0])) { continue }
        $prev = $ExistingBrokerEnv | Where-Object { $_ -like "$($pair[1])=*" } | Select-Object -First 1
        if ($prev) {
            $prev = $prev.Substring($pair[1].Length + 1)
            Set-Variable -Name $pair[0] -Value $prev
            Write-Host "  keeping $($pair[1])=$prev from the installed service (-$($pair[0]) '' drops it)"
        }
    }
    # A lean box's two files are made to exist now, before anything checks or reads them. A missing
    # one is seeded from the TRACKED file (lib-runtime Initialize-LeanBrokerFiles), which is the
    # repo's map, not this box's: any Admin > Rails edit it held is gone unless a backup has it.
    if ($BrokerPlan.Stack -eq 'lean') {
        foreach ($s in @(Initialize-LeanBrokerFiles -Root $PlatformRoot)) {
            Write-Host "  box-local broker file was missing, seeded from the tracked map (this box's own edits are not in it; a backup's deploy\local copy has them): $s" -ForegroundColor Yellow
        }
    }
}
$BrokerFileEnv = @(Resolve-BrokerFile 'RolesFile'     $RolesFile     'BROKER_ROLES_FILE'
                   Resolve-BrokerFile 'UpstreamsFile' $UpstreamsFile 'BROKER_UPSTREAMS_FILE'
                   Resolve-BrokerFile 'DisabledFile'  $DisabledFile  'BROKER_DISABLED_FILE')

$BrokerPy     = "$PlatformRoot\.venv\Scripts\python.exe"

# Import the broker the way the service will, BEFORE anything is torn down. This script never
# installs the broker's dependencies (it assumes the venv has them), so a venv that is missing
# one - python-multipart was undeclared until 2026-10-01 and present only by hand-install - gets
# a service that starts, dies at import and is restarted by NSSM forever, with every rail behind
# it. Refusing here costs nothing; finding it after the old service is gone costs the platform.
# EAP is relaxed for the probe only: on 5.1, `2>&1` under 'Stop' throws on the first stderr line.
if ($InstallBroker) {
    if (-not (Test-Path -LiteralPath $BrokerPy)) {
        throw "No platform venv at $BrokerPy. Create it, then: & '$BrokerPy' -m pip install -e '$PlatformRoot\services\broker'"
    }
    $eap = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    # A blank stderr line arrives on 5.1 as an empty RemoteException, whose text is its type name.
    # uvicorn too: it is what the service runs (-m uvicorn), and nothing under app/ imports it. The
    # path goes in as an argument, not inside the Python literal, so a quote in it cannot break it.
    $probe = @(& $BrokerPy -c "import sys; sys.path.insert(0, sys.argv[1]); import app.main, uvicorn" "$PlatformRoot\services\broker" 2>&1 |
               ForEach-Object { [string]$_ } |
               Where-Object { $_.Trim() -and $_ -ne 'System.Management.Automation.RemoteException' })
    $probeExit = $LASTEXITCODE
    $ErrorActionPreference = $eap
    if ($probeExit -ne 0) {
        throw ("The broker does not import with $BrokerPy, so the service would crash-loop and take " +
               "every rail down with it. Last lines:`n  " + (($probe | Select-Object -Last 3) -join "`n  ") +
               "`nInstall its declared dependencies into that venv as yourself (not SYSTEM), then re-run:" +
               "`n  & '$BrokerPy' -m pip install -e '$PlatformRoot\services\broker'")
    }
}
$OllamaExe    = "$UserProfile\AppData\Local\Programs\Ollama\ollama.exe"
$LogDir       = "$PlatformRoot\deploy\logs"
$BinDir       = "$PlatformRoot\deploy\bin"
$Nssm         = "$BinDir\nssm.exe"

foreach ($d in $LogDir, $BinDir) { New-Item -ItemType Directory -Force -Path $d | Out-Null }

# --- 1. fetch NSSM if we don't have it --------------------------------------
if (-not (Test-Path $Nssm)) {
    Write-Host 'Downloading NSSM...' -ForegroundColor Cyan
    $null = Install-NssmExe -Dest $Nssm   # lib-runtime: nothing left in TEMP, the pin checked when set
}
Write-Host "nssm: $Nssm"

# --- 2. install helper (runs as LocalSystem; no ObjectName/password) ---------
# NSSM prints status to STDERR, and under $ErrorActionPreference='Stop' PowerShell
# 5.1 promotes any native stderr to a *terminating* error. On a fresh box the very
# first cleanup call ("nssm stop <name>") prints "Can't open service!" and would
# abort the whole install. So run every nssm call with EAP relaxed and judge it by
# the process exit code, and only clean up a service that actually exists. It RETURNS that code;
# `nssm install` failing is fatal, the prune hook's is kept and said (Install-Svc), the rest are not
# judged.
function Invoke-Nssm {
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $Nssm @args 2>&1 | Out-Null } finally { $ErrorActionPreference = $old }
    return $LASTEXITCODE
}

# NOTE: the parameter is $AppArgs, NOT $Args — $Args collides with PowerShell's
# automatic $args variable and silently binds to nothing (AppParameters ends up
# empty, so the service launches a bare Python REPL instead of uvicorn).
function Install-Svc([string]$Name, [string]$Exe, [string]$AppArgs, [string]$Cwd, [string[]]$EnvExtra) {
    if (Get-Service -Name $Name -ErrorAction SilentlyContinue) {   # clear a prior install
        Invoke-Nssm stop $Name | Out-Null
        Invoke-Nssm remove $Name confirm | Out-Null
        Start-Sleep -Milliseconds 500
    }
    if ((Invoke-Nssm install $Name $Exe) -ne 0) { throw "nssm install $Name failed" }
    # Set AppParameters explicitly: passing args as a trailing string to `nssm
    # install` does NOT populate them (the child launches with no args — a bare
    # Python REPL, in the broker's case), so set them as their own value here.
    Invoke-Nssm set $Name AppParameters $AppArgs | Out-Null
    Invoke-Nssm set $Name AppDirectory $Cwd | Out-Null
    Invoke-Nssm set $Name Start SERVICE_AUTO_START | Out-Null
    # Through Merge-ServiceEnv: a blank element ends a REG_MULTI_SZ, and NSSM ignores every entry
    # after it. A list carried forward from the registry ends in one on the podman box.
    $EnvExtra = @(Merge-ServiceEnv -Base $EnvExtra)
    if ($EnvExtra) { Invoke-Nssm set $Name AppEnvironmentExtra @EnvExtra | Out-Null }
    # Logs rotated at start and online at a size, rotated copies pruned (lib-runtime Get-NssmLogSettings).
    # The prune hook's exit code is kept: NSSM 2.24 has no event hooks and refuses it. Not fatal (the
    # watchdog prunes daily, or on a box with none, the full stack's, rebuild-stale.ps1 when it runs),
    # but said once the service is registered, below, naming which.
    $hookExit = 0
    foreach ($a in @(Get-NssmLogSettings -Name $Name -Stdout "$LogDir\$Name.out.log" -Stderr "$LogDir\$Name.err.log" `
                         -Lib (Join-Path $PSScriptRoot 'installer\lib-runtime.ps1'))) {
        $code = Invoke-Nssm @a
        if (Test-NssmHookSetting $a) { $hookExit = $code }
    }
    Invoke-Nssm set $Name AppExit Default Restart | Out-Null       # restart on crash
    Write-Host "installed service: $Name (LocalSystem)" -ForegroundColor Green
    # Registered: did NSSM keep the hook? Read back from the registry (lib-runtime Get-NssmHookWarning).
    $watchdog = [bool](Get-Service -Name 'platform-watchdog' -ErrorAction SilentlyContinue)
    $hookWarning = Get-NssmHookWarning -Name $Name -HookExit $hookExit -WatchdogRegistered $watchdog
    if ($hookWarning) { Write-Warning $hookWarning }
}

# --- 3. broker (point torch/HF at your profile cache) -----------------------
if ($InstallBroker) {
# Kokoro ships in several precisions (kokoro-v1.0.onnx, .fp16, .fp16-gpu, .int8), and a box has
# whichever was downloaded. Hard-coding the plain name pointed an 8 GB box that holds only the
# fp16-gpu file at nothing, and every speech job failed. The plain file still wins if present.
$KokoroModel = "$AiWorkRoot\models\kokoro\kokoro-v1.0.onnx"
if (-not (Test-Path -LiteralPath $KokoroModel)) {
    $alt = Get-ChildItem -LiteralPath "$AiWorkRoot\models\kokoro" -Filter 'kokoro-v1.0*.onnx' -File `
               -ErrorAction SilentlyContinue | Sort-Object Name | Select-Object -First 1
    if ($alt) { $KokoroModel = $alt.FullName; Write-Host "  Kokoro model: $($alt.Name)" }
}
$BrokerEnv = @("HF_HOME=$AiWorkRoot\cache\hf-cache",
               "HUGGINGFACE_HUB_CACHE=$AiWorkRoot\cache\hf-cache\hub",
               "TTS_HOME=$UserProfile\AppData\Local",
               # The media worker's THREE interpreters. These were previously set ONLY in
               # the service registry, by hand — so re-running this script silently reverted
               # the broker to the (nonexistent) defaults in config.py and every image and
               # speech job failed. Declare them here so the install is reproducible.
               #   image  = torch + diffusers  (transformers 5.5.4, numpy 1.26.4)
               #   speech = torch + coqui-tts  (needs transformers <5 — do not merge these)
               #   kokoro = kokoro-onnx        (needs numpy >=2, torch-free)
               # The kokoro split is forced, not stylistic: kokoro-onnx requires numpy>=2.0.2
               # and simple-lama-inpainting in the media venv requires numpy<2.0.0, so a
               # merge silently upgrades numpy under every image path.
               "BROKER_MEDIA_PYTHON=$AiWorkRoot\venvs\media-venv\Scripts\python.exe",
               "BROKER_TTS_PYTHON=$AiWorkRoot\venvs\tts-venv\Scripts\python.exe",
               "BROKER_KOKORO_PYTHON=$AiWorkRoot\venvs\kokoro-venv\Scripts\python.exe",
               "BROKER_KOKORO_MODEL_PATH=$KokoroModel",
               "BROKER_KOKORO_VOICES_PATH=$AiWorkRoot\models\kokoro\voices-v1.0.bin",
               # faster-whisper shares the kokoro venv (torch-free, verified no numpy
               # conflict), so BROKER_WHISPER_PYTHON is left unset and falls back to it.
               # MULTILINGUAL 'small', never 'small.en' — the .en models are English-only,
               # ignore the language parameter, and render Spanish as nonsense.
               "BROKER_WHISPER_MODEL=small",
               "BROKER_WHISPER_DEVICE=cpu",
               "BROKER_WHISPER_COMPUTE_TYPE=int8",
               # edu_media_core moved out of the edu-suite rail into packages/ so the broker no
               # longer depends on a rail. This value is baked into the NSSM service env, so
               # changing the code alone is NOT enough -- re-run this script elevated or the
               # service keeps the old path and every media job dies with ModuleNotFoundError
               # inside a subprocess whose stderr is only tail-captured.
               "BROKER_MEDIA_CORE_SRC=$PlatformRoot\packages\edu-media-core\src",
               "BROKER_MEDIA_VOICES_DIR=$PlatformRoot\packages\edu-media-core\voices")
# Host-native voice engines for /v1/voice/*. The broker no longer hardcodes a path into the
# ai-voice rail: dispatch stays in the broker (synthesis takes the GPU gate and evicts every
# resident heavy model, arbitration only the broker can do) but the LOCATION is deployment
# configuration. Unset => voice reports unavailable, which is correct for a platform without the
# ai-voice rail, so it is set only where that rail's engines are.
if (Test-Path -LiteralPath $VoiceEnginesDir -PathType Container) {
    $BrokerEnv += "BROKER_VOICE_ENGINES_DIR=$VoiceEnginesDir"
    # The RVC engine runs ffmpeg in this service's environment, which has the MACHINE PATH only; a
    # box whose ffmpeg is on the user's PATH alone (the DevToolbox's) gives it the full path this
    # shell finds, a shim resolved to the exe it runs (lib-runtime Resolve-FfmpegExe). Found at
    # every run, so a later run without ffmpeg on PATH drops it again.
    $Ffmpeg = Resolve-FfmpegExe
    if ($Ffmpeg.Path) {
        $BrokerEnv += "AI_VOICE_FFMPEG=$($Ffmpeg.Path)"
        Write-Host "  voice engines' ffmpeg: AI_VOICE_FFMPEG=$($Ffmpeg.Path) ($($Ffmpeg.Note))"
    }
    else {
        Write-Host ("  voice engines' ffmpeg: $($Ffmpeg.Note), so AI_VOICE_FFMPEG is left unset and RVC voices " +
                    'need ffmpeg on the MACHINE PATH') -ForegroundColor Yellow
    }
}
else { Write-Host '  voice engines: no rails\ai-voice\native in this checkout, so BROKER_VOICE_ENGINES_DIR is left unset (voice reports unavailable)' }
if ($BrokerToken) { $BrokerEnv += "BROKER_AUTH_TOKEN=$BrokerToken" }  # enable control-plane auth
# This box's own role/upstream/disabled maps, validated at the top. Empty when none was given,
# and the broker then reads the tracked services\broker\*.json in this checkout.
$BrokerEnv += $BrokerFileEnv
# The Claude CLI backend's settings (BROKER_CLI_*) are box-specific -- the owner's platform
# usernames, the path to the owner's claude.exe, the token file -- so this tracked script does
# not write them. They are CARRIED FORWARD from the installed service instead, like the map files
# above. A reinstall that dropped them would switch the backend off with nothing to notice: every
# call would simply be answered locally, exactly as before. The media venv paths failed the same
# silent way back when they lived only in the service registry.
if ($ExistingBrokerEnv) {
    $CliEnv = @($ExistingBrokerEnv | Where-Object { $_ -like 'BROKER_CLI_*=*' })
    if ($CliEnv.Count -gt 0) {
        $BrokerEnv += $CliEnv
        Write-Host "  keeping $($CliEnv.Count) BROKER_CLI_* setting(s) from the installed service"
    }
}
# -BrokerCliEnvFile, checked at the top: merged OVER the carried-forward settings (the file wins,
# `KEY=` removes one). Keys only in the output; a value can be a token.
if ($CliFileEnv.Count -gt 0) {
    $BrokerEnv = @(Merge-ServiceEnv -Base $BrokerEnv -Overlay $CliFileEnv)
    Write-Host ("  applying BROKER_CLI_* from $BrokerCliEnvFile`: " +
                (($CliFileEnv | ForEach-Object { $_.Substring(0, $_.IndexOf('=')) }) -join ', '))
}
# A lean box's broker: the lean spec's entries (bind, media off, the token, deploy\local's maps,
# made to exist near the top) merged LAST, so the apply engine's Test-BrokerServiceCurrent agrees
# with what was registered here.
if ($BrokerPlan.Overlay.Count) {
    $BrokerEnv = @(Merge-ServiceEnv -Base $BrokerEnv -Overlay $BrokerPlan.Overlay)
}
Install-Svc 'platform-broker' $BrokerPy $BrokerPlan.AppParameters $PlatformRoot $BrokerEnv
}

# --- 4. ollama (point at your models) ---------------------------------------
# NOTE: if Ollama already starts on boot (its app adds itself to startup), disable
# that (Task Manager -> Startup apps -> Ollama -> Disable) or run with
# -SkipOllama, or two servers fight over port 11434.
if ($InstallOllama) {
    Install-Svc 'ollama' $OllamaExe 'serve' (Split-Path $OllamaExe) `
        @("OLLAMA_MODELS=$UserProfile\.ollama\models")
}

# --- 5. course-builder (native rail — reads arbitrary host paths, no container) ---------
# Installed into the platform venv and registered as a service so it comes up with the box.
# The gateway proxies /course-builder/api/* to 8901. Only where the rail is in this checkout (see
# $CourseBuilderDir at the top).
if ($InstallCourseBuilder) {
    Write-Host 'Installing course-builder package into platform venv...' -ForegroundColor Cyan
    & $BrokerPy -m pip install -q -e $CourseBuilderDir
    # Judged by its exit code: a failed install registered a service that died at import and
    # that NSSM restarted for ever. Then the service is not (re)installed, an existing one is
    # left as it was, and the run still starts the other services before it exits 1.
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "pip could not install course-builder from $CourseBuilderDir (exit $LASTEXITCODE); its service was NOT (re)installed."
        $Unfinished += "course-builder: pip install exit $LASTEXITCODE, service not (re)installed"
        $InstallCourseBuilder = $false
    }
}
if ($InstallCourseBuilder) {
    # CB_BLUEPRINT_CSV has no sane default — it points at an exam-blueprint CSV that lives
    # outside the repo — so take it from deploy/.env when present.
    $CbBlueprintCsv = ([string](Get-DotEnvValue -Path $EnvFile -Key 'CB_BLUEPRINT_CSV')).Trim()
    if (-not $CbBlueprintCsv) {
        Write-Host '  note: no CB_BLUEPRINT_CSV in deploy/.env - builds will fail until one is set' -ForegroundColor Yellow
    }

    # CB_INDEX_DIR is NOT optional here, for the same reason OLLAMA_MODELS and HF_HOME are not:
    # the app's default goes through Path.home(), and LocalSystem's home is
    # C:\Windows\System32\config\systemprofile. Left unset the rail comes up healthy and reports
    # zero corpora, while the indices sit in the installing user's profile where it never looks.
    $CbEnv = @(
        "CB_BROKER_URL=http://127.0.0.1:11500",
        "CB_INDEX_DIR=$UserProfile\.course-builder-indices",
        "CB_BLUEPRINT_CSV=$CbBlueprintCsv",
        "CB_EMBED_ROLE=@embed",
        "CB_CONDENSE_ROLE=@openmaic"
    )
    if ($BrokerToken) { $CbEnv += "BROKER_AUTH_TOKEN=$BrokerToken" }

    Install-Svc 'platform-course-builder' $BrokerPy `
        '-m uvicorn course_builder_app.api.app:app --host 127.0.0.1 --port 8901' `
        $CourseBuilderDir `
        $CbEnv
}

# --- 6. start + report ------------------------------------------------------
if ($InstallOllama) { Invoke-Nssm start ollama | Out-Null; Start-Sleep 4 }
if ($InstallBroker) { Invoke-Nssm start platform-broker | Out-Null }
if ($InstallCourseBuilder) { Invoke-Nssm start platform-course-builder | Out-Null }
Start-Sleep 4
$svcNames = @(); if ($InstallBroker) { $svcNames += 'platform-broker' }
if ($InstallOllama) { $svcNames += 'ollama' }
if ($InstallCourseBuilder) { $svcNames += 'platform-course-builder' }
Get-Service -Name $svcNames -ErrorAction SilentlyContinue |
    Select-Object Name, Status, StartType | Format-Table -AutoSize
Write-Host ''
Write-Host 'Verify:  curl http://127.0.0.1:11500/healthz   (expect ollama_reachable:true)'
Write-Host "Logs:    $LogDir"
Write-Host 'Manage:  nssm restart platform-broker | nssm status platform-broker | nssm stop platform-broker'
if ($Unfinished.Count) {
    Write-Host ''
    Write-Host ("NOT DONE: " + ($Unfinished -join '; ')) -ForegroundColor Red
    exit 1
}