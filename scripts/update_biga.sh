#!/usr/bin/env bash
# Fetch latest changes from origin/main and restart the biga service if updated.
#
# Invoked from:
#   - biga.service ExecStartPre (quick attempt, BIGA_UPDATE_CONTEXT=boot-pre)
#   - biga-update-boot.timer (~3 min after boot, full retry)
#   - root cron at 4 AM
#
#   0 4 * * * /home/pi/BigA/scripts/update_biga.sh

set -uo pipefail

REPO_DIR="/home/pi/BigA"
LOG_FILE="/var/log/biga_update.log"
SERVICE="biga"
DEPLOY_KEY="/etc/biga/deploy_key"
CONTEXT="${BIGA_UPDATE_CONTEXT:-manual}"
MAX_WAIT_SEC="${BIGA_UPDATE_MAX_WAIT_SEC:-90}"
FETCH_RETRIES="${BIGA_UPDATE_FETCH_RETRIES:-3}"
SLEEP_SEC="${BIGA_UPDATE_RETRY_SLEEP_SEC:-10}"
GIT=(git -c "safe.directory=$REPO_DIR")

# ExecStartPre: don't block boot for long — timer retries later.
if [ "$CONTEXT" = "boot-pre" ] && [ -z "${BIGA_UPDATE_MAX_WAIT_SEC+x}" ]; then
    MAX_WAIT_SEC=25
    FETCH_RETRIES=2
fi

# Use the deploy key for SSH if present (required for private repos).
if [ -f "$DEPLOY_KEY" ]; then
    export GIT_SSH_COMMAND="ssh -i $DEPLOY_KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
fi

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [$CONTEXT] $*" | tee -a "$LOG_FILE"
}

log_tail() {
    tail -n 8 "$LOG_FILE" 2>/dev/null | while IFS= read -r line; do
        log "  | $line"
    done
}

wait_for_network() {
    local deadline=$((SECONDS + MAX_WAIT_SEC))
    local attempt=0
    while [ "$SECONDS" -lt "$deadline" ]; do
        attempt=$((attempt + 1))
        if getent hosts github.com > /dev/null 2>&1 \
            && ping -c 1 -W 3 github.com > /dev/null 2>&1; then
            log "network ready (attempt $attempt, ${SECONDS}s elapsed)"
            return 0
        fi
        log "waiting for network (attempt $attempt, max ${MAX_WAIT_SEC}s)..."
        sleep 5
    done
    log "ERROR: network not ready after ${MAX_WAIT_SEC}s"
    return 1
}

git_fetch_with_retries() {
    local try=1
    while [ "$try" -le "$FETCH_RETRIES" ]; do
        log "git fetch origin main (try $try/$FETCH_RETRIES)..."
        if "${GIT[@]}" fetch origin main >> "$LOG_FILE" 2>&1; then
            return 0
        fi
        log "git fetch failed on try $try/$FETCH_RETRIES"
        log_tail
        if [ "$try" -lt "$FETCH_RETRIES" ]; then
            sleep "$SLEEP_SEC"
        fi
        try=$((try + 1))
    done
    return 1
}

log "--- update check start (context=$CONTEXT max_wait=${MAX_WAIT_SEC}s) ---"

if ! wait_for_network; then
    if [ "$CONTEXT" = "boot-pre" ]; then
        log "boot-pre: skipping update; biga-update-boot.timer will retry"
        exit 0
    fi
    exit 1
fi

if [ ! -d "$REPO_DIR/.git" ]; then
    log "ERROR: $REPO_DIR is not a git repo. Aborting."
    exit 1
fi

cd "$REPO_DIR"

if ! git_fetch_with_retries; then
    log "ERROR: git fetch failed after $FETCH_RETRIES tries (network/SSH?). Aborting."
    if [ -f "$DEPLOY_KEY" ]; then
        log "deploy key present at $DEPLOY_KEY — check GitHub deploy key access"
    else
        log "no deploy key at $DEPLOY_KEY — private repo fetch will fail"
    fi
    exit 1
fi

LOCAL=$("${GIT[@]}" rev-parse HEAD)
REMOTE=$("${GIT[@]}" rev-parse origin/main)

if [ "$LOCAL" = "$REMOTE" ]; then
    log "Already up to date ($LOCAL). No action taken."
    exit 0
fi

log "Update found: $LOCAL -> $REMOTE"

# Hard reset to origin — the Pi is a deployment target, never a dev machine.
if ! "${GIT[@]}" reset --hard origin/main >> "$LOG_FILE" 2>&1; then
    log "ERROR: git reset --hard failed. Service not restarted."
    exit 1
fi

log "Pull successful. Restarting $SERVICE service..."

if systemctl restart "$SERVICE"; then
    log "Service restarted successfully."
else
    log "ERROR: systemctl restart $SERVICE failed (exit $?)."
    exit 1
fi

log "--- update complete ---"
