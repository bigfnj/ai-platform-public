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

log "=== AI-Platform startup ==="

# Give WSL networking a moment to settle after logon
sleep 5

# 1. Detect current WSL→Windows gateway IP
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

# 3. Determine which compose profiles to activate from PLATFORM_ENABLED_APPS
PROFILES=""
ENABLED_APPS=$(grep "^PLATFORM_ENABLED_APPS=" "$ENV_FILE" 2>/dev/null | cut -d= -f2 | tr -d '[:space:]') || true
if [ -n "$ENABLED_APPS" ]; then
    IFS=',' read -ra APPS <<< "$ENABLED_APPS"
    for APP in "${APPS[@]}"; do
        case "$APP" in
            recipe-book|co-worker) PROFILES="$PROFILES --profile $APP" ;;
        esac
    done
fi
log "enabled apps: ${ENABLED_APPS:-<none>}; profiles:${PROFILES:- <none>}"

# 4. Bring the stack up with the fresh env (idempotent; recreates if config changed)
cd "$REPO"
# shellcheck disable=SC2086  # intentional word-split for $PROFILES
if docker compose --env-file deploy/.env -f "$COMPOSE_FILE" $PROFILES up -d 2>>"$LOG"; then
    log "compose up: OK"
else
    log "WARNING: compose up returned non-zero; containers may not be fully up"
fi

# 5. Keep the WSL2 VM alive so containers keep running
log "entering keep-alive"
exec sleep infinity
