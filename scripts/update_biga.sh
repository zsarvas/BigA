#!/usr/bin/env bash
# Fetch latest changes from origin/main and restart the biga service if updated.
# Intended to run as root via cron at 4 AM:
#   0 4 * * * /home/pi/BigA/scripts/update_biga.sh

set -euo pipefail

REPO_DIR="/home/pi/BigA"
LOG_FILE="/var/log/biga_update.log"
SERVICE="biga"
DEPLOY_KEY="/etc/biga/deploy_key"

# Use the deploy key for SSH if present (required for private repos).
if [ -f "$DEPLOY_KEY" ]; then
    export GIT_SSH_COMMAND="ssh -i $DEPLOY_KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
fi

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

log "--- update check start ---"

if [ ! -d "$REPO_DIR/.git" ]; then
    log "ERROR: $REPO_DIR is not a git repo. Aborting."
    exit 1
fi

cd "$REPO_DIR"

if ! git fetch origin main >> "$LOG_FILE" 2>&1; then
    log "ERROR: git fetch failed (network issue?). Aborting."
    exit 1
fi

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)

if [ "$LOCAL" = "$REMOTE" ]; then
    log "Already up to date ($LOCAL). No action taken."
    exit 0
fi

log "Update found: $LOCAL -> $REMOTE"

if ! git pull origin main >> "$LOG_FILE" 2>&1; then
    log "ERROR: git pull failed. Service not restarted."
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
