#!/usr/bin/env bash
# Create (or recreate) the BigA AP network profile via NetworkManager.
# Derives the SSID from this Pi's wlan0 MAC address so each device is unique.
# Safe to re-run — deletes and recreates the profile each time.
#
# Called by:
#   setup.py (direct install)
#   biga-firstboot.service (first boot from golden image on a new Pi)

set -euo pipefail

INTERFACE="wlan0"
CON_NAME="biga-ap"
AP_PASSWORD="${BIGA_AP_PASSWORD:-bigasetup}"
# Shared default PSK for every device (print on housing / setup screen).
# SSID is unique per Pi (BigA-<last4 MAC>). Override at image build time if needed.
AP_IP="192.168.4.1"

# Derive last-4 of MAC for a unique-per-device SSID
if [ -r "/sys/class/net/${INTERFACE}/address" ]; then
    MAC=$(cat "/sys/class/net/${INTERFACE}/address" | tr -d ':' | tr '[:lower:]' '[:upper:]')
    SUFFIX="${MAC: -4}"
else
    SUFFIX="0000"
fi
AP_SSID="BigA-${SUFFIX}"

echo "  → SSID    : $AP_SSID"
echo "  → Password: $AP_PASSWORD"
echo "  → Gateway : $AP_IP"

# Remove old profile if present
nmcli con delete "$CON_NAME" 2>/dev/null && echo "  → removed old profile" || true

# Create AP profile
nmcli con add \
    type wifi \
    ifname "$INTERFACE" \
    con-name "$CON_NAME" \
    autoconnect no \
    ssid "$AP_SSID"

nmcli con modify "$CON_NAME" \
    802-11-wireless.mode ap \
    802-11-wireless.band bg \
    ipv4.method shared \
    ipv4.addresses "${AP_IP}/24" \
    wifi-sec.key-mgmt wpa-psk \
    wifi-sec.psk "$AP_PASSWORD"

echo "  → AP profile '$CON_NAME' created (activate with: nmcli con up $CON_NAME)"

# Guarantee SSH is enabled on every golden image boot — without this, SSH
# is only present if Pi Imager enabled it for that specific flash, which
# doesn't carry over to cards flashed from the golden image.
systemctl enable ssh 2>/dev/null || true
systemctl start  ssh 2>/dev/null || true
echo "  → SSH enabled and started"
