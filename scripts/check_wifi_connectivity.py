#!/usr/bin/env python3
"""
Boot-time WiFi prep — sync saved networks to NetworkManager profiles.

A device that has *never* been provisioned (no saved networks at all, e.g. a
fresh golden-image flash) automatically enters AP provisioning so the QR setup
portal appears on first boot.

When networks *are* saved, we sync them to NetworkManager and try to join. If
none associate/get a DHCP lease (bad password just entered in the portal, or
every saved network is out of range) we re-enter AP provisioning so the QR
setup screen returns. We only fall back on association/DHCP failure — an
associated network with a working LAN but no internet still gets an IP, so a
mere outage never re-provisions and we don't flap.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

_PORTAL_DIR = Path(__file__).resolve().parent.parent / "portal"
sys.path.insert(0, str(_PORTAL_DIR))

from wifi_store import (  # noqa: E402
    connect_saved_networks,
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

    if connect_saved_networks():
        log.info("joined a saved WiFi network")
        return 0

    log.warning(
        "could not join any saved network (wrong password or out of range) — "
        "re-entering AP provisioning so the QR setup screen returns"
    )
    enter_provisioning()
    prepare_ap_provisioning_mode()
    return 0


if __name__ == "__main__":
    if os.geteuid() != 0:
        sys.exit("check_wifi_connectivity.py must run as root")
    raise SystemExit(main())
