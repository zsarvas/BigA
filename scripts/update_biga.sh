#!/usr/bin/env bash
# Fetch latest changes from origin/main and restart the biga service if updated.
#
# Invoked from:
#   - biga.service ExecStartPre (quick attempt, BIGA_UPDATE_CONTEXT=boot-pre)
#   - biga-update-boot.timer (~3 min after boot, full retry)
#   - root cron at 4 AM
#
#   0 4 * * * /home/pi/BigA/scripts/update_biga.sh
#
# Never prompts for GitHub credentials (public HTTPS or deploy-key SSH).

set -uo pipefail

REPO_DIR="/home/pi/BigA"
LOG_FILE="/var/log/biga_update.log"
SERVICE="biga"
DEPLOY_KEY="/etc/biga/deploy_key"
HTTPS_ORIGIN="https://github.com/zsarvas/BigA.git"
SSH_ORIGIN="git@github.com:zsarvas/BigA.git"
CONTEXT="${BIGA_UPDATE_CONTEXT:-manual}"
MAX_WAIT_SEC="${BIGA_UPDATE_MAX_WAIT_SEC:-90}"
FETCH_RETRIES="${BIGA_UPDATE_FETCH_RETRIES:-3}"
SLEEP_SEC="${BIGA_UPDATE_RETRY_SLEEP_SEC:-10}"

# Unattended: never ask for username/password on the console.
export GIT_TERMINAL_PROMPT=0
export GIT_ASKPASS=/bin/true
# Prefer empty askpass over a missing binary on odd images.
if [ ! -x /bin/true ]; then
    export GIT_ASKPASS=true
fi

GIT=(
    git
    -c "safe.directory=$REPO_DIR"
    -c "credential.helper="
    -c "core.askPass=/bin/true"
)

# ExecStartPre: don't block boot for long — timer retries later.
if [ "$CONTEXT" = "boot-pre" ]; then
    # Only apply short defaults when the caller did not override.
    : "${BIGA_UPDATE_MAX_WAIT_SEC:=25}"
    : "${BIGA_UPDATE_FETCH_RETRIES:=2}"
    MAX_WAIT_SEC="${BIGA_UPDATE_MAX_WAIT_SEC}"
    FETCH_RETRIES="${BIGA_UPDATE_FETCH_RETRIES}"
fi

# Use the deploy key for SSH if present (required for private repos).
if [ -f "$DEPLOY_KEY" ]; then
    export GIT_SSH_COMMAND="ssh -i $DEPLOY_KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
fi

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [$CONTEXT] $*" | tee -a "$LOG_FILE"
}

log_tail() {
    # Show only the last git error lines from this run (avoid dumping the whole log).
    tail -n 12 "$LOG_FILE" 2>/dev/null | grep -E 'fatal:|error:|Username|Authentication|Permission|denied|Could not|unable' \
        | tail -n 6 | while IFS= read -r line; do
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

ensure_remote() {
    local url
    url=$("${GIT[@]}" remote get-url origin 2>/dev/null || true)
    if [ -f "$DEPLOY_KEY" ]; then
        if [ "$url" != "$SSH_ORIGIN" ]; then
            log "setting origin → $SSH_ORIGIN (deploy key present)"
            "${GIT[@]}" remote set-url origin "$SSH_ORIGIN" >> "$LOG_FILE" 2>&1 || \
                "${GIT[@]}" remote add origin "$SSH_ORIGIN" >> "$LOG_FILE" 2>&1 || true
        fi
        return 0
    fi
    # Public HTTPS — normalize to .git URL (avoids some credential-helper quirks).
    case "$url" in
        "$HTTPS_ORIGIN"|"https://github.com/zsarvas/BigA"|"https://github.com/zsarvas/BigA.git")
            if [ "$url" != "$HTTPS_ORIGIN" ]; then
                log "normalizing origin → $HTTPS_ORIGIN"
                "${GIT[@]}" remote set-url origin "$HTTPS_ORIGIN" >> "$LOG_FILE" 2>&1 || true
            fi
            ;;
        "")
            log "no origin remote — adding $HTTPS_ORIGIN"
            "${GIT[@]}" remote add origin "$HTTPS_ORIGIN" >> "$LOG_FILE" 2>&1 || true
            ;;
        *)
            log "keeping existing origin: $url"
            ;;
    esac
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

fix_pi_tree() {
    # Root OTA can leave root-owned files; keep the tree writable for the pi user.
    if [ "$(id -u)" -eq 0 ] && id pi >/dev/null 2>&1; then
        chown -R pi:pi "$REPO_DIR" 2>/dev/null || true
    fi
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
    log "ERROR: $REPO_DIR is not a git repo (.git missing). Aborting."
    log "HINT: golden images keep .git; do not rsync a GitHub tarball over /home/pi/BigA"
    exit 1
fi

cd "$REPO_DIR"
ensure_remote

if ! git_fetch_with_retries; then
    log "ERROR: git fetch failed after $FETCH_RETRIES tries. Aborting."
    if [ -f "$DEPLOY_KEY" ]; then
        log "deploy key present at $DEPLOY_KEY — check GitHub deploy key access"
    else
        log "using HTTPS (public). If this keeps failing, check DNS/firewall to github.com"
    fi
    exit 1
fi

LOCAL=$("${GIT[@]}" rev-parse HEAD)
REMOTE=$("${GIT[@]}" rev-parse origin/main)

if [ "$LOCAL" = "$REMOTE" ]; then
    log "Already up to date ($LOCAL). No action taken."
    fix_pi_tree
    exit 0
fi

log "Update found: $LOCAL -> $REMOTE"

# Hard reset to origin — the Pi is a deployment target, never a dev machine.
if ! "${GIT[@]}" reset --hard origin/main >> "$LOG_FILE" 2>&1; then
    log "ERROR: git reset --hard failed. Service not restarted."
    exit 1
fi

fix_pi_tree

log "Pull successful ($REMOTE). Restarting $SERVICE service..."

# Avoid restart loop when we are already running as ExecStartPre of biga.
if [ "$CONTEXT" = "boot-pre" ]; then
    log "boot-pre: code updated; continuing into ExecStart with new tree"
    log "--- update complete ---"
    exit 0
fi

if systemctl restart "$SERVICE"; then
    log "Service restarted successfully."
else
    log "ERROR: systemctl restart $SERVICE failed (exit $?)."
    exit 1
fi

log "--- update complete ---"
