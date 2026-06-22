#!/usr/bin/env python3
"""
Boot-time WiFi check — enter provisioning (QR / portal) when offline.

Runs once before biga starts. If saved networks exist but there is no usable
internet after a short wait, touch ``/etc/biga/provisioning_active`` so the
portal and setup screen take over.
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
    enter_provisioning,
    has_networks,
    is_provisioning,
    load_networks,
    sync_nm_profiles,
)

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("biga-connectivity")

PING_HOST = os.environ.get("BIGA_CONNECTIVITY_PING", "1.1.1.1")
WAIT_SEC = int(os.environ.get("BIGA_CONNECTIVITY_WAIT_SEC", "45"))
POLL_SEC = 3


def _wlan_connected() -> bool:
    result = subprocess.run(
        ["nmcli", "-t", "-f", "STATE", "device", "show", "wlan0"],
        capture_output=True,
        text=True,
        check=False,
    )
    return "connected" in (result.stdout or "").lower()


def _has_internet() -> bool:
    result = subprocess.run(
        ["ping", "-c", "1", "-W", "3", PING_HOST],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def _start_provisioning_services() -> None:
    subprocess.run(["systemctl", "stop", "biga"], check=False)
    for unit in ("biga-portal", "biga-setup-screen"):
        subprocess.run(["systemctl", "start", unit], check=False)


def main() -> int:
    if is_provisioning():
        log.info("provisioning already active — skip connectivity check")
        return 0

    if not has_networks():
        log.info("no saved networks — entering provisioning mode")
        enter_provisioning()
        _start_provisioning_services()
        return 0

    sync_nm_profiles(load_networks())

    log.info("checking connectivity (up to %ds)…", WAIT_SEC)
    deadline = time.time() + WAIT_SEC
    while time.time() < deadline:
        if _wlan_connected() and _has_internet():
            log.info("internet OK via %s", PING_HOST)
            return 0
        time.sleep(POLL_SEC)

    log.warning("no internet after %ds — entering provisioning mode", WAIT_SEC)
    enter_provisioning()
    _start_provisioning_services()
    return 0


if __name__ == "__main__":
    if os.geteuid() != 0:
        sys.exit("check_wifi_connectivity.py must run as root")
    raise SystemExit(main())
