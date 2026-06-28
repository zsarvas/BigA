#!/usr/bin/env python3
"""
Boot-time WiFi prep — sync saved networks to NetworkManager profiles.

Provisioning / QR setup is only entered via the reset button (GPIO 26), not
automatically when internet is down or WiFi is slow to connect.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

_PORTAL_DIR = Path(__file__).resolve().parent.parent / "portal"
sys.path.insert(0, str(_PORTAL_DIR))

from wifi_store import (  # noqa: E402
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
    if is_provisioning():
        log.info("provisioning active — preparing AP mode")
        prepare_ap_provisioning_mode()
        return 0

    seed_wifi_creds_from_nm()

    if not has_networks():
        log.warning(
            "no saved WiFi networks — hold reset (GPIO 26) for 5s to open QR setup"
        )
        return 0

    networks = load_networks()
    sync_nm_profiles(networks)
    log.info("synced %d saved network(s) to NetworkManager", len(networks))
    return 0


if __name__ == "__main__":
    if os.geteuid() != 0:
        sys.exit("check_wifi_connectivity.py must run as root")
    raise SystemExit(main())
