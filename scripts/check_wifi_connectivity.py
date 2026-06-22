#!/usr/bin/env python3
"""
Boot-time WiFi check — enter provisioning (QR / portal) when offline.

1. Passive wait — let NetworkManager autoconnect + DHCP (slow Pi / weak signal).
2. Active rotation — try each saved network in order (newest first).
3. Only then enable provisioning / QR screen.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path

_PORTAL_DIR = Path(__file__).resolve().parent.parent / "portal"
sys.path.insert(0, str(_PORTAL_DIR))

from wifi_store import (  # noqa: E402
    bring_up_connection,
    enter_provisioning,
    has_networks,
    is_provisioning,
    load_networks,
    saved_connection_names,
    sync_nm_profiles,
)

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("biga-connectivity")

PING_HOST = os.environ.get("BIGA_CONNECTIVITY_PING", "1.1.1.1")
# Let NM autoconnect + DHCP finish before we assume we're offline.
PASSIVE_WAIT_SEC = int(os.environ.get("BIGA_CONNECTIVITY_PASSIVE_SEC", "75"))
# Per saved network when actively cycling profiles.
PER_NETWORK_SEC = int(os.environ.get("BIGA_CONNECTIVITY_PER_NETWORK_SEC", "20"))
POLL_SEC = 3


def _wlan_connected() -> bool:
    result = subprocess.run(
        ["nmcli", "-t", "-f", "STATE", "device", "show", "wlan0"],
        capture_output=True,
        text=True,
        check=False,
    )
    return "connected" in (result.stdout or "").lower()


def _active_connection() -> str:
    result = subprocess.run(
        ["nmcli", "-t", "-f", "GENERAL.CONNECTION", "device", "show", "wlan0"],
        capture_output=True,
        text=True,
        check=False,
    )
    return (result.stdout or "").strip()


def _has_internet() -> bool:
    result = subprocess.run(
        ["ping", "-c", "1", "-W", "3", PING_HOST],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def _wait_for_internet(deadline: float) -> bool:
    while time.time() < deadline:
        if _wlan_connected() and _has_internet():
            return True
        time.sleep(POLL_SEC)
    return False


def _start_provisioning_services() -> None:
    subprocess.run(["systemctl", "stop", "biga"], check=False)
    for unit in ("biga-portal", "biga-setup-screen"):
        # Don't block the oneshot — portal ExecStartPre (nmcli AP up) can take a while.
        subprocess.run(["systemctl", "start", "--no-block", unit], check=False)


def _passive_phase() -> bool:
    """Wait for NM autoconnect; return True if internet comes up."""
    log.info("passive wait up to %ds for autoconnect + internet…", PASSIVE_WAIT_SEC)
    deadline = time.time() + PASSIVE_WAIT_SEC
    while time.time() < deadline:
        if _wlan_connected() and _has_internet():
            log.info("internet OK on %s via %s", _active_connection() or "?", PING_HOST)
            return True
        if _wlan_connected():
            log.debug("wlan up (%s) but no ping yet — waiting…", _active_connection())
        time.sleep(POLL_SEC)
    return False


def _active_phase(networks: list[tuple[str, str]]) -> bool:
    """Try each saved profile explicitly (home → office → …)."""
    log.info(
        "passive wait exhausted — trying %d saved network(s), %ds each…",
        len(networks),
        PER_NETWORK_SEC,
    )
    for ssid, con_name in networks:
        log.info("trying %r (%s)…", ssid, con_name)
        if not bring_up_connection(con_name):
            continue
        deadline = time.time() + PER_NETWORK_SEC
        if _wait_for_internet(deadline):
            log.info("internet OK on %r via %s", ssid, PING_HOST)
            return True
        log.info("no internet on %r after %ds", ssid, PER_NETWORK_SEC)
    return False


def main() -> int:
    if is_provisioning():
        log.info("provisioning already active — skip connectivity check")
        return 0

    if not has_networks():
        log.info("no saved networks — entering provisioning mode")
        enter_provisioning()
        _start_provisioning_services()
        return 0

    networks = load_networks()
    sync_nm_profiles(networks)
    profiles = saved_connection_names(networks)

    if _passive_phase() or _active_phase(profiles):
        return 0

    log.warning(
        "no internet after passive (%ds) + %d network(s) × %ds — provisioning",
        PASSIVE_WAIT_SEC,
        len(profiles),
        PER_NETWORK_SEC,
    )
    enter_provisioning()
    _start_provisioning_services()
    return 0


if __name__ == "__main__":
    if os.geteuid() != 0:
        sys.exit("check_wifi_connectivity.py must run as root")
    raise SystemExit(main())
