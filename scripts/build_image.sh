#!/usr/bin/env bash
# Build a BigA golden image from an SD card and publish it as a GitHub release asset.
#
# Runs on macOS or Linux.
# Requires the GitHub CLI (gh) for release upload.
#
# Usage:
#   ./scripts/build_image.sh                      # interactive
#   ./scripts/build_image.sh disk4 v1.2           # macOS: specify disk + version tag
#   ./scripts/build_image.sh sdb  v1.2            # Linux: specify device + version tag
#
# Prerequisites:
#   macOS  — Lima installed (brew install lima), gh installed (brew install gh)
#            First time: limactl start  (takes ~2 min, one-off setup)
#   Linux  — gh installed

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
    if [ ${#missing[@]} -gt 0 ]; then
        abort "Missing dependencies: ${missing[*]}"
    fi
    if [ "$OS" = "Darwin" ] && ! command -v limactl &>/dev/null; then
        echo ""
        echo "  ⚠  Lima not found — pishrink will be skipped and the image will be full card size."
        echo "     For proper shrinking (recommended): brew install lima && limactl start"
        echo ""
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

        local sector_size sectors
        sector_size=$(diskutil info -plist "$DEVICE" | plutil -extract IOBlockSize raw - 2>/dev/null || echo 512)
        sectors=$(diskutil info -plist "$DEVICE" | plutil -extract TotalSize raw - 2>/dev/null || echo 0)
        if [ "$sectors" = "0" ] || [ -z "$sectors" ]; then
            abort "Could not read $DEVICE size — is the SD card inserted?"
        fi
        sectors=$(( sectors / sector_size ))
        info "Card size: $(( sectors * sector_size / 1000000000 )) GB ($sectors × ${sector_size}B sectors)"

        # Exact sector count avoids truncated images when macOS dd hits end-of-device quirks.
        if ! sudo dd if="$RAW_DEVICE" of="$OUT_DIR/$IMG_NAME" bs="$sector_size" count="$sectors" status=progress; then
            rm -f "$OUT_DIR/$IMG_NAME"
            abort "dd capture failed — image not written"
        fi

        local img_bytes expected_bytes
        img_bytes=$(stat -f%z "$OUT_DIR/$IMG_NAME" 2>/dev/null || echo 0)
        expected_bytes=$(( sectors * sector_size ))
        if [ "$img_bytes" -ne "$expected_bytes" ]; then
            rm -f "$OUT_DIR/$IMG_NAME"
            abort "Capture size mismatch: got ${img_bytes} bytes, expected ${expected_bytes}"
        fi
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

    local pishrink_url="https://raw.githubusercontent.com/Drewsif/PiShrink/master/pishrink.sh"
    local pishrink_local="$OUT_DIR/pishrink.sh"

    if [ "$OS" = "Darwin" ]; then
        # pishrink needs a Linux VM (Lima) to access loop devices.
        # It also needs to stage a full copy of the image inside the VM, so the Mac
        # needs ~2× image size in free disk space.  Check before attempting.
        if ! command -v limactl &>/dev/null; then
            info "Lima not found — skipping pishrink (brew install lima to enable)"
            return
        fi

        local img_bytes
        img_bytes=$(stat -f%z "$OUT_DIR/$IMG_NAME" 2>/dev/null || echo 0)
        local needed_bytes=$(( img_bytes * 2 ))
        local free_bytes
        free_bytes=$(df -k "$OUT_DIR" | awk 'NR==2{print $4 * 1024}')

        if [ "$free_bytes" -lt "$needed_bytes" ]; then
            local img_gb=$(( img_bytes / 1073741824 ))
            local free_gb=$(( free_bytes / 1073741824 ))
            info "Skipping pishrink — need ~$((img_gb * 2))GB free, only ${free_gb}GB available"
            info "Use a 16GB master card (produces ~15GB image) to shrink on this Mac"
            return
        fi

        local lima_instance="biga-builder"
        if ! limactl list 2>/dev/null | grep -q "^${lima_instance}"; then
            info "Creating Lima VM '${lima_instance}' (first time ~3 min)..."
            limactl start --name="$lima_instance" template:ubuntu \
                --set='.mounts[0].writable = true'
        elif ! limactl list 2>/dev/null | grep -q "^${lima_instance}.*Running"; then
            limactl start "$lima_instance"
        fi

        info "Downloading pishrink.sh..."
        curl -fsSL "$pishrink_url" -o "$pishrink_local"
        chmod +x "$pishrink_local"

        local lima_img="/var/tmp/biga-shrink.img"
        sudo chown "$(whoami)" "$OUT_DIR/$IMG_NAME"

        info "Copying image into Lima native disk…"
        limactl shell "$lima_instance" -- cp "$OUT_DIR/$IMG_NAME" "$lima_img"

        info "Running pishrink…"
        if ! limactl shell "$lima_instance" -- sudo bash "$pishrink_local" -s "$lima_img"; then
            limactl shell "$lima_instance" -- rm -f "$lima_img"
            rm -f "$pishrink_local"
            echo ""
            echo "  pishrink failed — the .img capture may be truncated or corrupt."
            echo "  Check: diskutil list   (confirm you picked the SD card, not an internal disk)"
            echo "  Re-run capture after deleting: rm $OUT_DIR/$IMG_NAME"
            read -rp "  Continue without shrinking (compress full-size image)? [y/N] " skip
            [[ "$skip" =~ ^[Yy]$ ]] || abort "pishrink failed — original image at $OUT_DIR/$IMG_NAME"
            return
        fi

        info "Copying shrunk image back…"
        limactl shell "$lima_instance" -- cp "$lima_img" "$OUT_DIR/$IMG_NAME"
        limactl shell "$lima_instance" -- rm -f "$lima_img"
        rm -f "$pishrink_local"
        info "Shrunk: $(du -h "$OUT_DIR/$IMG_NAME" | cut -f1)"
        return
    fi

    # Linux — loop devices work natively, run pishrink directly.
    info "Downloading pishrink.sh..."
    curl -fsSL "$pishrink_url" -o "$pishrink_local"
    chmod +x "$pishrink_local"
    sudo bash "$pishrink_local" -s "$OUT_DIR/$IMG_NAME"
    rm -f "$pishrink_local"
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
