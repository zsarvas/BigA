#!/usr/bin/env python3
"""
Boot-time WiFi prep — sync saved networks to NetworkManager profiles.

A device that has *never* been provisioned (no saved networks at all, e.g. a
fresh golden-image flash) automatically enters AP provisioning so the QR setup
portal appears on first boot. Once networks are saved, provisioning is only
re-entered via the reset button (GPIO 26) — we never auto-provision merely
because the internet is down or WiFi is slow, to avoid flapping.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

_PORTAL_DIR = Path(__file__).resolve().parent.parent / "portal"
sys.path.insert(0, str(_PORTAL_DIR))

from wifi_store import (  # noqa: E402
    ensure_ssh_running,
    enter_provisioning,
    has_networks,
    is_provisioning,
    load_networks,
    prepare_ap_provisioning_mode,
    seed_wifi_creds_from_nm,
    sync_nm_profiles,
)

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("biga-connectivity")


def main() -> int:
    ensure_ssh_running()

    if is_provisioning():
        log.info("provisioning active — preparing AP mode")
        prepare_ap_provisioning_mode()
        return 0

    seed_wifi_creds_from_nm()

    if not has_networks():
        log.warning("no saved WiFi networks — entering AP provisioning for QR setup")
        enter_provisioning()
        prepare_ap_provisioning_mode()
        return 0

    networks = load_networks()
    sync_nm_profiles(networks)
    log.info("synced %d saved network(s) to NetworkManager", len(networks))
    return 0


if __name__ == "__main__":
    if os.geteuid() != 0:
        sys.exit("check_wifi_connectivity.py must run as root")
    raise SystemExit(main())
