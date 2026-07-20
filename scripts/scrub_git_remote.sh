#!/usr/bin/env bash
# Force anonymous public HTTPS for BigA OTA / manual git pulls.
#
# GitHub Actions checkout bakes a dead token into .git/config as
# http.https://github.com/.extraheader — remote set-url alone does not remove it,
# so every fetch sends "Invalid username or token" on flashed Pis.
#
# Usage:
#   sudo bash /home/pi/BigA/scripts/scrub_git_remote.sh [/home/pi/BigA]

set -euo pipefail

REPO_DIR="${1:-/home/pi/BigA}"
HTTPS_ORIGIN="https://github.com/zsarvas/BigA.git"

if [ ! -d "$REPO_DIR/.git" ]; then
    echo "scrub_git_remote: $REPO_DIR is not a git repo" >&2
    exit 1
fi

GIT=(git -C "$REPO_DIR" -c "safe.directory=$REPO_DIR")

_scrub_config() {
    local scope="$1"  # local | global
    shift
    local key
    for key in "$@"; do
        if [ "$scope" = "local" ]; then
            "${GIT[@]}" config --local --unset-all "$key" 2>/dev/null || true
        else
            git config --global --unset-all "$key" 2>/dev/null || true
        fi
    done
}

url=$("${GIT[@]}" remote get-url origin 2>/dev/null || true)
if [ -n "$url" ]; then
    "${GIT[@]}" remote set-url origin "$HTTPS_ORIGIN"
else
    "${GIT[@]}" remote add origin "$HTTPS_ORIGIN"
fi

AUTH_KEYS=(
    "http.https://github.com/.extraheader"
    "http.https://github.com/.extraHeader"
    "credential.https://github.com.helper"
    "credential.helper"
)

for key in "${AUTH_KEYS[@]}"; do
    _scrub_config local "$key"
    _scrub_config global "$key"
done

echo "scrub_git_remote: origin → $HTTPS_ORIGIN (was: ${url:-<none>})"
