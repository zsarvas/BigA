#!/usr/bin/env bash
# Build a BigA golden image from an SD card and publish it as a GitHub release asset.
#
# Runs on macOS or Linux. Requires Docker on macOS (for pishrink).
# Requires the GitHub CLI (gh) for release upload.
#
# Usage:
#   ./scripts/build_image.sh                      # interactive
#   ./scripts/build_image.sh disk4 v1.2           # macOS: specify disk + version tag
#   ./scripts/build_image.sh sdb  v1.2            # Linux: specify device + version tag
#
# Prerequisites:
#   macOS  — Docker Desktop running, gh installed (brew install gh)
#   Linux  — pishrink.sh in PATH or auto-downloaded, gh installed

set -euo pipefail

OS="$(uname -s)"
DISK="${1:-}"
VERSION="${2:-$(date +v%Y.%m.%d)}"
OUT_DIR="$(pwd)"
IMG_NAME="biga-${VERSION}.img"
COMPRESSED="${IMG_NAME}.xz"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

info()  { echo "  → $*"; }
step()  { echo ""; echo "[$1] $2"; }
abort() { echo ""; echo "ERROR: $*" >&2; exit 1; }

check_deps() {
    local missing=()
    for cmd in dd xz; do
        command -v "$cmd" &>/dev/null || missing+=("$cmd")
    done
    if [ "$OS" = "Darwin" ] && ! command -v docker &>/dev/null; then
        missing+=("docker  (install Docker Desktop from https://docker.com)")
    fi
    if [ ${#missing[@]} -gt 0 ]; then
        abort "Missing dependencies: ${missing[*]}"
    fi
}

# ---------------------------------------------------------------------------
# Disk selection
# ---------------------------------------------------------------------------

select_disk() {
    echo "========================================"
    echo "  BigA — Golden Image Builder"
    echo "========================================"
    step "?" "Available disks"
    if [ "$OS" = "Darwin" ]; then
        diskutil list | grep "^/dev/disk"
    else
        lsblk -d -o NAME,SIZE,MODEL,TRAN | grep -v "loop\|NAME"
    fi
    echo ""
    if [ "$OS" = "Darwin" ]; then
        read -rp "  Enter disk NUMBER only (e.g. 4 for /dev/disk4): " DISK
    else
        read -rp "  Enter device name (e.g. sdb for /dev/sdb): " DISK
    fi
}

resolve_device() {
    if [ "$OS" = "Darwin" ]; then
        # Accept "4", "disk4", or "/dev/disk4"
        DISK="${DISK#/dev/}"
        DISK="${DISK#disk}"
        DEVICE="/dev/disk${DISK}"
        RAW_DEVICE="/dev/rdisk${DISK}"
    else
        DISK="${DISK#/dev/}"
        DEVICE="/dev/${DISK}"
        RAW_DEVICE="$DEVICE"
    fi

    if [ ! -b "$DEVICE" ]; then
        abort "$DEVICE is not a block device. Check the disk identifier."
    fi
}

# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------

capture_image() {
    step "1/4" "Capturing $RAW_DEVICE → $IMG_NAME"
    if [ "$OS" = "Darwin" ]; then
        info "Unmounting $DEVICE..."
        diskutil unmountDisk "$DEVICE"
        sudo dd if="$RAW_DEVICE" of="$OUT_DIR/$IMG_NAME" bs=4m
    else
        sudo dd if="$RAW_DEVICE" of="$OUT_DIR/$IMG_NAME" bs=4M status=progress
    fi
    info "Captured: $(du -h "$OUT_DIR/$IMG_NAME" | cut -f1)"
}

# ---------------------------------------------------------------------------
# Shrink
# ---------------------------------------------------------------------------

shrink_image() {
    step "2/4" "Shrinking with pishrink"
    if [ "$OS" = "Darwin" ]; then
        info "Running pishrink via Docker..."
        docker run --privileged \
            -v "$OUT_DIR:/img" \
            mkaczanowski/pishrink \
            pishrink.sh -s "/img/$IMG_NAME"
    else
        local pishrink
        if command -v pishrink.sh &>/dev/null; then
            pishrink="pishrink.sh"
        else
            info "Downloading pishrink.sh..."
            curl -fsSL \
                https://raw.githubusercontent.com/Drewsif/PiShrink/master/pishrink.sh \
                -o /tmp/pishrink.sh
            chmod +x /tmp/pishrink.sh
            pishrink="/tmp/pishrink.sh"
        fi
        sudo "$pishrink" -s "$OUT_DIR/$IMG_NAME"
    fi
    info "Shrunk: $(du -h "$OUT_DIR/$IMG_NAME" | cut -f1)"
}

# ---------------------------------------------------------------------------
# Compress
# ---------------------------------------------------------------------------

compress_image() {
    step "3/4" "Compressing with xz (this takes a few minutes...)"
    xz -v -T 0 -9 "$OUT_DIR/$IMG_NAME"
    info "Compressed: $COMPRESSED ($(du -h "$OUT_DIR/$COMPRESSED" | cut -f1))"
}

# ---------------------------------------------------------------------------
# Publish
# ---------------------------------------------------------------------------

publish_release() {
    step "4/4" "Publishing GitHub release $VERSION"

    if ! command -v gh &>/dev/null; then
        echo ""
        echo "  gh CLI not found — upload manually:"
        echo "  https://github.com/zsarvas/BigA/releases/new"
        echo "  Asset: $OUT_DIR/$COMPRESSED"
        return
    fi

    local size
    size="$(du -h "$OUT_DIR/$COMPRESSED" | cut -f1)"

    local notes
    notes="$(cat <<EOF
## BigA Golden Image $VERSION

Flash to a microSD card with [Raspberry Pi Imager](https://www.raspberrypi.com/software/).

**In Imager → Advanced settings, configure:**
- Hostname (e.g. \`biga\`)
- Username/password (default \`pi\` / set your own)
- WiFi credentials for first SSH access
- Enable SSH

**After flashing:**
- Insert card, power on — Angels splash screen appears automatically
- The \`biga\` service starts on boot
- Factory reset: hold GPIO 26 for 5 seconds

**Image size:** $size
EOF
)"

    read -rp "  Create release and upload $COMPRESSED? [y/N] " confirm
    if [[ "$confirm" =~ ^[Yy]$ ]]; then
        gh release create "$VERSION" "$OUT_DIR/$COMPRESSED" \
            --title "BigA Golden Image $VERSION" \
            --notes "$notes"
        echo ""
        info "Release URL: $(gh release view "$VERSION" --json url -q .url)"
    else
        info "Skipped. Run later: gh release create $VERSION $COMPRESSED"
    fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

check_deps

if [ -z "$DISK" ]; then
    select_disk
fi

resolve_device

echo ""
echo "  Device : $RAW_DEVICE"
echo "  Output : $OUT_DIR/$IMG_NAME"
echo "  Version: $VERSION"
echo ""
read -rp "  Proceed? [y/N] " go
[[ "$go" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }

capture_image
shrink_image
compress_image
publish_release

echo ""
echo "========================================"
echo "  Done!  $COMPRESSED"
echo "========================================"
