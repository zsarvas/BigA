"""Captive-portal helpers shared by portal.py and setup_screen.py."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

PORTAL_IP = "192.168.4.1"
PORTAL_HTTP_URL = f"http://{PORTAL_IP}/"
PORTAL_HOSTNAME = "biga.setup"
PORTAL_SETUP_URL = f"http://{PORTAL_HOSTNAME}/"
WLAN_INTERFACE = "wlan0"
AP_CON_NAME = "biga-ap"


def wlan_mac() -> str:
    """Hardware MAC for wlan0 (uppercase colon-separated), or empty if unavailable."""
    override = os.environ.get("BIGA_WLAN_MAC", "").strip()
    if override:
        return override.upper()
    try:
        return Path(f"/sys/class/net/{WLAN_INTERFACE}/address").read_text().strip().upper()
    except OSError:
        return ""


def ap_ssid() -> str:
    """
    SSID clients should join — always read from the live NM ``biga-ap`` profile
    so the QR screen matches what the radio is actually broadcasting.
    """
    override = os.environ.get("BIGA_AP_SSID", "")
    if override:
        return override
    try:
        result = subprocess.run(
            ["nmcli", "-g", "802-11-wireless.ssid", "connection", "show", AP_CON_NAME],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        ssid = (result.stdout or "").strip()
        if result.returncode == 0 and ssid:
            return ssid
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        mac = Path(f"/sys/class/net/{WLAN_INTERFACE}/address").read_text().strip()
        return f"BigA-{mac.replace(':', '').upper()[-4:]}"
    except OSError:
        return "BigA-Setup"
