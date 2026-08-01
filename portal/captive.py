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


_SYS_NET = Path("/sys/class/net")
# Virtual radios NetworkManager/wpa_supplicant spawn alongside the real one.
_VIRTUAL_WIFI_PREFIXES = ("p2p-", "ap0", "uap0", "mon.")


def _is_wifi_interface(name: str) -> bool:
    """True only for 802.11 netdevs — never Bluetooth PAN (bnep*) or ethernet."""
    iface = _SYS_NET / name
    return (iface / "phy80211").exists() or (iface / "wireless").is_dir()


def wlan_interface() -> str:
    """
    Name of the real WiFi netdev (``wlan0`` on Pi OS).

    The Pi Zero 2W's combo chip also exposes a Bluetooth controller with its own
    MAC, and Bluetooth tethering adds ``bnep0`` — neither is 802.11, so we pick
    by ``phy80211``/``wireless`` rather than trusting interface order.
    """
    override = os.environ.get("BIGA_WLAN_INTERFACE", "").strip()
    if override:
        return override
    if _is_wifi_interface(WLAN_INTERFACE):
        return WLAN_INTERFACE
    try:
        candidates = sorted(p.name for p in _SYS_NET.iterdir())
    except OSError:
        return WLAN_INTERFACE
    for name in candidates:
        if name.startswith(_VIRTUAL_WIFI_PREFIXES):
            continue
        if _is_wifi_interface(name):
            return name
    return WLAN_INTERFACE


def _permanent_mac(iface: str) -> str:
    """
    Burned-in WiFi MAC, ignoring NetworkManager MAC randomization.

    ``/sys/class/net/<iface>/address`` reports the *current* address, which is a
    random one whenever ``wifi.cloned-mac-address`` is in play — that would make
    the SSID, QR code, and printed MAC disagree with the label on the device.
    """
    try:
        result = subprocess.run(
            ["ethtool", "-P", iface],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        mac = (result.stdout or "").strip().rsplit(" ", 1)[-1]
        if result.returncode == 0 and _looks_like_mac(mac):
            return mac.upper()
    except (OSError, subprocess.TimeoutExpired):
        pass

    iface_dir = _SYS_NET / iface
    try:
        # addr_assign_type 0 == permanent; anything else is random/set/stolen.
        assign_type = (iface_dir / "addr_assign_type").read_text().strip()
    except OSError:
        assign_type = "0"
    try:
        mac = (iface_dir / "address").read_text().strip()
    except OSError:
        return ""
    if assign_type != "0":
        return ""
    return mac.upper() if _looks_like_mac(mac) else ""


def _looks_like_mac(value: str) -> bool:
    parts = value.strip().split(":")
    if len(parts) != 6:
        return False
    if all(p == "00" for p in parts):
        return False
    return all(len(p) == 2 and all(c in "0123456789abcdefABCDEF" for c in p) for p in parts)


def wlan_mac() -> str:
    """WiFi hardware MAC (uppercase colon-separated), or empty if unavailable."""
    override = os.environ.get("BIGA_WLAN_MAC", "").strip()
    if override:
        return override.upper()
    return _permanent_mac(wlan_interface())


def ap_ssid_suffix() -> str:
    """Last 4 hex digits of the WiFi MAC — the per-device SSID suffix."""
    mac = wlan_mac().replace(":", "")
    return mac[-4:] if len(mac) >= 4 else "0000"


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
    suffix = ap_ssid_suffix()
    return f"BigA-{suffix}" if suffix != "0000" else "BigA-Setup"
