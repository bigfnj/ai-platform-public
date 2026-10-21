#!/usr/bin/env bash
# AI-Platform WSL startup script.
# Runs at Windows logon (via the Startup-folder shortcut).
# 1. Re-detects the WSL→Windows gateway IP (dynamic; changes on reboot).
# 2. Updates deploy/.env with the current value.
# 3. Brings the Docker Compose stack up (recreates containers if env changed).
# 4. Sleeps forever to keep the WSL2 VM alive (idle shutdown stops containers).
#
# Self-locating: this script lives at deploy/installer/ inside the install clone,
# so the repo root is two levels up — no hardcoded paths needed.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/../.." && pwd)"
ENV_FILE="$REPO/deploy/.env"
COMPOSE_FILE="$REPO/deploy/installer/docker-compose.installer.yml"
LOG="$REPO/deploy/logs/startup.log"

mkdir -p "$(dirname "$LOG")"
log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG"; }

# A deploy/.env value read the way compose's dotenv reads it, for the plain values these keys
# hold: the LAST assignment wins (grep | cut used to CONCATENATE two PLATFORM_ENABLED_APPS lines
# with no separator), `export ` and blanks around the `=` are allowed, a 'quoted' or "quoted"
# value is taken up to its closing quote, and an unquoted one ends at " #" (a space, then a hash)
# and loses its trailing blanks. Not implemented, as these keys never need them: $-expansion, and
# the escapes and line breaks a double-quoted value may hold. Exit 1 when KEY is not assigned.
# tools/tests/test_platform_startup_sh.py holds it to the measured compose cases in its subset.
dotenv_get() {
    local key="$1" file="$2" line val found="" assigned="" first=1
    [ -f "$file" ] || return 1
    while IFS= read -r line || [ -n "$line" ]; do
        line="${line%$'\r'}"
        if [ "$first" = 1 ]; then line="${line#$'\357\273\277'}"; first=0; fi
        [[ $line =~ ^[[:space:]]*(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_.-]*)[[:space:]]*=(.*)$ ]] || continue
        [ "${BASH_REMATCH[2]}" = "$key" ] || continue
        val="${BASH_REMATCH[3]}"
        val="${val#"${val%%[![:space:]]*}"}"
        case "$val" in
            \"*) val="${val#\"}"; val="${val%%\"*}" ;;
            \'*) val="${val#\'}"; val="${val%%\'*}" ;;
            *)   val="${val%% #*}"; val="${val%"${val##*[![:space:]]}"}" ;;
        esac
        found="$val"; assigned=1
    done < "$file"
    printf '%s' "$found"
    [ -n "$assigned" ]
}

# An app list (comma or newline separated, or a JSON array) as one id per line. The environment
# wins when it holds something, as in Get-DeclaredAppList and in compose's own interpolation.
app_list() {
    local raw="${!1:-}"
    [ -n "${raw//[[:space:]]/}" ] || raw="$(dotenv_get "$1" "$ENV_FILE")" || true
    printf '%s' "$raw" | tr -d '[]"' | tr ',' '\n' | tr -d '[:blank:]\r' | grep -v '^$' || true
}

log "=== AI-Platform startup ==="

# Give WSL networking a moment to settle after logon
sleep 5

# 1. Detect current WSL→Windows gateway IP. Not under WSL's mirrored networking, where the default
# route is the LAN router, not Windows: there WINDOWS_HOST is left as it is, with a warning (the
# same detection and reasons as lib-runtime.ps1's Get-WslNetworkInfo: wslinfo, else loopback0).
NET_MODE=""
if command -v wslinfo >/dev/null 2>&1; then
    NET_MODE=$(wslinfo --networking-mode 2>/dev/null | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]') || NET_MODE=""
fi
if [ -z "$NET_MODE" ] && ip link show loopback0 >/dev/null 2>&1; then NET_MODE=mirrored; fi
if [ "$NET_MODE" = "mirrored" ]; then
    log "WARNING: WSL runs in mirrored networking mode, where the default route is the LAN router, not Windows; leaving WINDOWS_HOST unchanged. No documented address lets a bridge-network container reach the broker or Ollama there: use networkingMode=nat in .wslconfig, or set WINDOWS_HOST by hand (docs/INSTALL.md)."
else
    WINDOWS_HOST=$(ip route show default 2>/dev/null | awk '/default/ {print $3; exit}') || true
    if [ -z "$WINDOWS_HOST" ]; then
        log "WARNING: could not detect gateway IP; leaving WINDOWS_HOST unchanged"
    else
        log "gateway IP: $WINDOWS_HOST"
        if grep -q "^WINDOWS_HOST=" "$ENV_FILE" 2>/dev/null; then
            sed -i "s/^WINDOWS_HOST=.*/WINDOWS_HOST=$WINDOWS_HOST/" "$ENV_FILE"
        else
            echo "WINDOWS_HOST=$WINDOWS_HOST" >> "$ENV_FILE"
        fi
    fi
fi

# 2. Wait for Docker daemon (systemd should start it, but give it time)
TRIES=0
until docker info &>/dev/null; do
    TRIES=$((TRIES+1))
    if [ "$TRIES" -ge 15 ]; then
        log "ERROR: Docker daemon not ready after 30s; skipping compose up"
        exec sleep infinity
    fi
    sleep 2
done
log "Docker ready"

# 3. Determine which compose profiles to activate: PLATFORM_ENABLED_APPS minus
# PLATFORM_DISABLED_APPS, as platform-startup.ps1 does. A disabled rail's profile is left out, and
# step 5 parks any container it already has; this script used to ignore the disabled list, so a
# rail disabled on a WSL box came back at every logon.
PROFILES=""
ENABLED=$(app_list PLATFORM_ENABLED_APPS)
DISABLED=$(app_list PLATFORM_DISABLED_APPS)
for APP in $ENABLED; do
    if printf '%s\n' "$DISABLED" | grep -qx -- "$APP"; then continue; fi
    # MUST match Get-ComposeProfiles in lib-runtime.ps1 (a test asserts it). This list stopped
    # at recipe-book|co-worker, so a WSL box lost five rails at every logon, in silence.
    case "$APP" in
        recipe-book|co-worker|smb-partner-enablement|gemini-cx|meeting-atlas|ai-playground|openmaic)
            PROFILES="$PROFILES --profile $APP" ;;
    esac
done
DISABLED_SAID=$(printf '%s' "$DISABLED" | paste -sd, -)
log "enabled apps: $(printf '%s' "$ENABLED" | paste -sd, -); disabled: ${DISABLED_SAID:-<none>}; profiles:${PROFILES:- <none>}"

# 3b. The box-local compose override, passed as a second -f exactly as Get-ComposeFiles does for
# the PowerShell paths. Without it a WSL box came up without its override after every logon.
# Read with dotenv_get (compose's rules). A Windows path is turned into its /mnt form with wslpath.
OVERRIDE_ARGS=()
OVERRIDE=$(dotenv_get PLATFORM_COMPOSE_OVERRIDE "$ENV_FILE") || true
if [ -n "$OVERRIDE" ]; then
    # A regex, not a case glob: `[A-Za-z]:\\*` does not match a backslash path (tested), which
    # sent every Windows-style value down the relative-path branch.
    WINPATH_RE='^[A-Za-z]:[\\/]'
    if [[ $OVERRIDE =~ $WINPATH_RE ]]; then
        OVERRIDE=$(wslpath -u "$OVERRIDE" 2>/dev/null || printf '%s' "$OVERRIDE")
    elif [[ $OVERRIDE != /* ]]; then
        OVERRIDE="$REPO/$OVERRIDE"
    fi
    if [ -f "$OVERRIDE" ]; then
        OVERRIDE_ARGS=(-f "$OVERRIDE")
        log "compose override: $OVERRIDE"
    else
        log "WARNING: PLATFORM_COMPOSE_OVERRIDE names '$OVERRIDE', which does not exist; composing without it."
    fi
fi

# 4. Bring the stack up with the fresh env (idempotent; recreates if config changed)
cd "$REPO"
# shellcheck disable=SC2086  # intentional word-split for $PROFILES
if docker compose --env-file deploy/.env -f "$COMPOSE_FILE" "${OVERRIDE_ARGS[@]}" $PROFILES up -d 2>>"$LOG"; then
    log "compose up: OK"
else
    log "WARNING: compose up returned non-zero; containers may not be fully up"
fi

# 5. Rails disabled on this box are parked after `up` (restart policy no, stopped), and one that is
# enabled again comes back, by the same lib-runtime step platform-startup.ps1 runs
# (sync-disabled-rails.ps1, which drives `wsl docker` from Windows), reached through WSL's Windows
# interop: one implementation of "disabled means not running", not a second one here. Run even
# with nothing disabled, since that is how a re-enabled rail comes back. Without interop it is
# said, every time, never skipped in silence.
SYNC_PS=$(wslpath -w "$SCRIPT_DIR/sync-disabled-rails.ps1" 2>/dev/null) || SYNC_PS=""
if [ -n "$SYNC_PS" ] && command -v powershell.exe >/dev/null 2>&1; then
    if powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$SYNC_PS" -RuntimeMode wsl >>"$LOG" 2>&1; then
        log "disabled rails in step (disabled: ${DISABLED_SAID:-none})"
    else
        log "WARNING: sync-disabled-rails.ps1 reported a failure (see above); disabled rails may still run: ${DISABLED_SAID:-none}"
    fi
else
    log "WARNING: no Windows interop (powershell.exe / wslpath), so the disabled rails were NOT parked: ${DISABLED_SAID:-none}"
fi

# 6. Keep the WSL2 VM alive so containers keep running
log "entering keep-alive"
exec sleep infinity
