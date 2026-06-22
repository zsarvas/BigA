"""
Saved WiFi networks + provisioning mode for the BigA portal.

``/etc/biga/wifi_creds.json`` holds up to ``MAX_NETWORKS`` entries (newest first).
``/etc/biga/provisioning_active`` — when present, AP + QR setup run instead of biga.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

CREDS_FILE = Path("/etc/biga/wifi_creds.json")
PROVISIONING_FLAG = Path("/etc/biga/provisioning_active")
WLAN_INTERFACE = "wlan0"
AP_CON_NAME = "biga-ap"
MAX_NETWORKS = int(os.environ.get("BIGA_WIFI_MAX_NETWORKS", "7"))

log = logging.getLogger(__name__)


def _con_name_for_ssid(ssid: str) -> str:
    slug = re.sub(r"[^\w]+", "-", ssid.strip()).strip("-").lower()[:32] or "net"
    return f"biga-wifi-{slug}"


def load_networks() -> list[dict[str, Any]]:
    """Return saved networks (newest first). Migrates legacy single-SSID JSON."""
    if not CREDS_FILE.exists():
        return []
    try:
        data = json.loads(CREDS_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    if isinstance(data, dict) and "networks" in data:
        nets = data.get("networks") or []
        return [n for n in nets if isinstance(n, dict) and n.get("ssid")]
    if isinstance(data, dict) and data.get("ssid"):
        return [
            {
                "ssid": str(data["ssid"]),
                "password": str(data.get("password", "")),
                "added": data.get("added", time.time()),
            }
        ]
    return []


def save_networks(networks: list[dict[str, Any]]) -> None:
    CREDS_FILE.parent.mkdir(parents=True, exist_ok=True)
    CREDS_FILE.write_text(json.dumps({"networks": networks}, indent=2))
    CREDS_FILE.chmod(0o600)


def has_networks() -> bool:
    return bool(load_networks())


def is_provisioning() -> bool:
    return PROVISIONING_FLAG.exists()


def enter_provisioning() -> None:
    PROVISIONING_FLAG.parent.mkdir(parents=True, exist_ok=True)
    PROVISIONING_FLAG.touch()
    log.info("provisioning mode enabled (%s)", PROVISIONING_FLAG)


def exit_provisioning() -> None:
    PROVISIONING_FLAG.unlink(missing_ok=True)
    log.info("provisioning mode disabled")


def append_network(ssid: str, password: str) -> None:
    """Add or refresh *ssid* (moves to front). Trim to ``MAX_NETWORKS`` oldest."""
    ssid = ssid.strip()
    networks = [n for n in load_networks() if n.get("ssid") != ssid]
    networks.insert(
        0,
        {"ssid": ssid, "password": password, "added": time.time()},
    )
    if len(networks) > MAX_NETWORKS:
        dropped = networks[MAX_NETWORKS:]
        networks = networks[:MAX_NETWORKS]
        for net in dropped:
            _delete_nm_profile(_con_name_for_ssid(str(net.get("ssid", ""))))
        log.info(
            "trimmed %d old network(s); keeping %d",
            len(dropped),
            len(networks),
        )
    save_networks(networks)
    sync_nm_profiles(networks)
    exit_provisioning()


def sync_nm_profiles(networks: list[dict[str, Any]]) -> None:
    """Create/update NM client profiles for every saved network."""
    keep = {_con_name_for_ssid(str(n["ssid"])) for n in networks}
    subprocess.run(["nmcli", "connection", "delete", "biga-client"], capture_output=True, check=False)

    result = subprocess.run(
        ["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"],
        capture_output=True,
        text=True,
        check=False,
    )
    for line in result.stdout.splitlines():
        if not line.endswith(":802-11-wireless"):
            continue
        name = line.split(":", 1)[0]
        if name == AP_CON_NAME:
            continue
        if name.startswith("biga-wifi-") and name not in keep:
            subprocess.run(["nmcli", "connection", "delete", name], capture_output=True, check=False)

    for i, net in enumerate(networks):
        ssid = str(net.get("ssid", "")).strip()
        password = str(net.get("password", ""))
        if not ssid:
            continue
        con_name = _con_name_for_ssid(ssid)
        priority = max(0, 100 - i * 10)
        _delete_nm_profile(con_name)
        add = subprocess.run(
            [
                "nmcli", "connection", "add",
                "type", "wifi",
                "con-name", con_name,
                "ifname", WLAN_INTERFACE,
                "ssid", ssid,
                "wifi-sec.key-mgmt", "wpa-psk",
                "wifi-sec.psk", password,
                "ipv4.method", "auto",
                "connection.autoconnect", "yes",
                "connection.autoconnect-priority", str(priority),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if add.returncode != 0:
            log.warning(
                "nmcli add %s failed: %s",
                con_name,
                (add.stderr or add.stdout or "").strip(),
            )


def _delete_nm_profile(name: str) -> None:
    subprocess.run(["nmcli", "connection", "delete", name], capture_output=True, check=False)


def wipe_all_networks() -> None:
    """Full factory wipe — golden image prep only."""
    for net in load_networks():
        _delete_nm_profile(_con_name_for_ssid(str(net.get("ssid", ""))))
    _delete_nm_profile("biga-client")
    CREDS_FILE.unlink(missing_ok=True)
    exit_provisioning()
