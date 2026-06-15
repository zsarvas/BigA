#!/usr/bin/env bash
# Generate a read-only SSH deploy key for the BigA private repo.
# Run once on the Pi (as root), then add the printed public key to GitHub.
#
# Usage:
#   sudo bash /home/pi/BigA/scripts/setup_deploy_key.sh

set -euo pipefail

KEY_DIR="/etc/biga"
KEY_PATH="$KEY_DIR/deploy_key"
REPO_DIR="/home/pi/BigA"
SSH_URL="git@github.com:zsarvas/BigA.git"
KNOWN_HOSTS="/root/.ssh/known_hosts"

if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root: sudo bash $0"
    exit 1
fi

echo "========================================"
echo "  BigA — Deploy Key Setup"
echo "========================================"

# --- Generate key if not already present ---
mkdir -p "$KEY_DIR"
chmod 700 "$KEY_DIR"

if [ -f "$KEY_PATH" ]; then
    echo ""
    echo "Deploy key already exists at $KEY_PATH"
else
    echo ""
    echo "Generating ED25519 deploy key..."
    ssh-keygen -t ed25519 -C "biga-pi-deploy" -f "$KEY_PATH" -N ""
    chmod 600 "$KEY_PATH"
    chmod 644 "${KEY_PATH}.pub"
    echo "Key generated."
fi

# --- Pre-accept GitHub's host key so git operations don't prompt ---
mkdir -p /root/.ssh
chmod 700 /root/.ssh
if ! grep -q "github.com" "$KNOWN_HOSTS" 2>/dev/null; then
    echo ""
    echo "Adding GitHub to known_hosts..."
    ssh-keyscan -t ed25519 github.com >> "$KNOWN_HOSTS" 2>/dev/null
    chmod 644 "$KNOWN_HOSTS"
fi

# --- Configure the repo remote to use SSH ---
if [ -d "$REPO_DIR/.git" ]; then
    CURRENT_URL="$(git -C "$REPO_DIR" remote get-url origin 2>/dev/null || true)"
    if [ "$CURRENT_URL" != "$SSH_URL" ]; then
        echo ""
        echo "Switching remote from HTTPS → SSH..."
        git -C "$REPO_DIR" remote set-url origin "$SSH_URL"
        echo "Remote updated: $SSH_URL"
    else
        echo "Remote already set to SSH."
    fi
fi

# --- Print the public key and next steps ---
echo ""
echo "========================================"
echo "  PUBLIC KEY — add this to GitHub"
echo "========================================"
echo ""
cat "${KEY_PATH}.pub"
echo ""
echo "========================================"
echo "  Next steps:"
echo ""
echo "  1. Open: https://github.com/zsarvas/BigA/settings/keys"
echo "  2. Click 'Add deploy key'"
echo "  3. Title: BigA Pi Deploy Key"
echo "  4. Paste the key above"
echo "  5. Leave 'Allow write access' UNCHECKED"
echo "  6. Click 'Add key'"
echo ""
echo "  Then test with:"
echo "    sudo GIT_SSH_COMMAND=\"ssh -i $KEY_PATH -o IdentitiesOnly=yes\" \\"
echo "      git -C $REPO_DIR fetch origin main"
echo "========================================"
