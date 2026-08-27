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
# Last join attempts — on disk so a reboot into the setup AP still has the trail.
# ``/tmp/biga.log`` is tmpfs and is gone after they unplug.
LAST_WIFI_LOG = Path(os.environ.get("BIGA_WIFI_LAST_LOG", "/var/log/biga-wifi-last.log"))
WLAN_INTERFACE = "wlan0"
AP_CON_NAME = "biga-ap"
MAX_NETWORKS = int(os.environ.get("BIGA_WIFI_MAX_NETWORKS", "7"))

log = logging.getLogger(__name__)

_WIFI_LOG_MAX = 64_000


def record_wifi_event(*lines: str) -> None:
    """Append a durable WiFi-attempt note (survives reboot; SSH from the setup AP)."""
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    body = f"=== {stamp} ===\n" + "\n".join(str(x) for x in lines if x) + "\n"
    for line in lines:
        if line:
            log.info("%s", line)
    try:
        LAST_WIFI_LOG.parent.mkdir(parents=True, exist_ok=True)
        prev = LAST_WIFI_LOG.read_text(encoding="utf-8") if LAST_WIFI_LOG.exists() else ""
        LAST_WIFI_LOG.write_text((prev + body)[-_WIFI_LOG_MAX:], encoding="utf-8")
    except OSError as exc:
        log.warning("could not write %s: %s", LAST_WIFI_LOG, exc)


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


def _nm_field(con_name: str, field: str) -> str:
    proc = subprocess.run(
        ["nmcli", "-s", "-g", field, "connection", "show", con_name],
        capture_output=True,
        text=True,
        check=False,
    )
    return (proc.stdout or "").strip()


def list_nm_wifi_profiles(*, active_only: bool = False) -> list[str]:
    """Saved client WiFi profiles (excludes biga-ap), active first when requested."""
    args = ["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"]
    if active_only:
        args.append("--active")
    proc = subprocess.run(args, capture_output=True, text=True, check=False)
    names: list[str] = []
    for line in (proc.stdout or "").splitlines():
        parts = line.split(":")
        if len(parts) < 2:
            continue
        name, typ = parts[0], parts[1]
        if typ == "802-11-wireless" and AP_CON_NAME not in name:
            names.append(name)
    return names


def seed_wifi_creds_from_nm() -> bool:
    """
    Import Pi Imager / NetworkManager WiFi into ``wifi_creds.json``.

    Called from setup.py and boot-time connectivity check. Imager stores the PSK
    in NM system connections — only root can read it via ``nmcli -s``.
    """
    if CREDS_FILE.exists():
        return True
    if is_provisioning():
        return False

    candidates: list[str] = []
    seen: set[str] = set()
    for active_only in (True, False):
        for name in list_nm_wifi_profiles(active_only=active_only):
            if name not in seen:
                seen.add(name)
                candidates.append(name)

    for con_name in candidates:
        ssid = _nm_field(con_name, "802-11-wireless.ssid") or con_name
        password = _nm_field(con_name, "802-11-wireless-security.psk")
        if not password:
            continue
        save_networks(
            [{"ssid": ssid, "password": password, "added": time.time()}]
        )
        exit_provisioning()
        log.info(
            "seeded wifi_creds.json from NM profile %r (ssid=%r)",
            con_name,
            ssid,
        )
        return True

    CREDS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if candidates:
        log.warning(
            "WiFi profile(s) present but PSK unreadable — hold reset 5s for QR setup"
        )
    return False


def has_networks() -> bool:
    return bool(load_networks())


def is_provisioning() -> bool:
    return PROVISIONING_FLAG.exists()


def enter_provisioning() -> None:
    PROVISIONING_FLAG.parent.mkdir(parents=True, exist_ok=True)
    PROVISIONING_FLAG.touch()
    log.info("provisioning mode enabled (%s)", PROVISIONING_FLAG)


def ensure_ssh_running() -> None:
    """
    Keep SSH up across provisioning / golden-image boots.

    ``setup_ap.sh`` only runs while the portal is active; after reset + WiFi
    setup the Pi may boot with ``sshd`` down (especially if host keys were
    wiped).  Call from boot connectivity, reset handler, and portal exit.
    """
    key_dir = Path("/etc/ssh")
    if not any(key_dir.glob("ssh_host_*_key")):
        log.warning("ssh host keys missing — regenerating")
        subprocess.run(["ssh-keygen", "-A"], capture_output=True, check=False)
    for boot_ssh in (Path("/boot/firmware/ssh"), Path("/boot/ssh")):
        try:
            boot_ssh.parent.mkdir(parents=True, exist_ok=True)
            boot_ssh.touch(exist_ok=True)
        except OSError:
            pass
    subprocess.run(["systemctl", "enable", "ssh"], capture_output=True, check=False)
    start = subprocess.run(
        ["systemctl", "start", "ssh"],
        capture_output=True,
        text=True,
        check=False,
    )
    if start.returncode != 0:
        log.warning("systemctl start ssh failed: %s", (start.stderr or "").strip())
    else:
        log.info("ssh service enabled and started")


def prepare_ap_provisioning_mode() -> bool:
    """
    Drop client WiFi and bring up ``biga-ap``.

    Without this, NM auto-connects a saved home profile on boot and AP mode
    fails — users often need to press reset twice before the QR screen appears.
    """
    result = subprocess.run(
        ["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show", "--active"],
        capture_output=True,
        text=True,
        check=False,
    )
    for line in (result.stdout or "").splitlines():
        parts = line.split(":")
        if len(parts) < 2:
            continue
        name, typ = parts[0], parts[1]
        if typ == "802-11-wireless" and name != AP_CON_NAME:
            subprocess.run(
                ["nmcli", "connection", "down", name],
                capture_output=True,
                check=False,
            )
    subprocess.run(
        ["nmcli", "device", "disconnect", WLAN_INTERFACE],
        capture_output=True,
        check=False,
    )
    time.sleep(0.5)
    up = subprocess.run(
        ["nmcli", "connection", "up", AP_CON_NAME],
        capture_output=True,
        text=True,
        check=False,
    )
    if up.returncode != 0:
        log.warning(
            "biga-ap up failed: %s",
            (up.stderr or up.stdout or "").strip(),
        )
        return False
    log.info("biga-ap active for provisioning")
    return True


def exit_provisioning() -> None:
    PROVISIONING_FLAG.unlink(missing_ok=True)
    log.info("provisioning mode disabled")


def network_needs_password(security: str) -> bool:
    """True for WPA/WEP PSK networks; open networks use ``--`` in nmcli scans."""
    if is_open_security(security) or is_enterprise_security(security):
        return False
    s = (security or "").strip()
    return bool(s)


def is_open_security(security: str) -> bool:
    """True for open / MAC-allow-list networks (no WPA passphrase)."""
    s = (security or "").strip().lower()
    return s in ("", "--", "none", "open")


def is_enterprise_security(security: str) -> bool:
    """True for 802.1X / WPA-Enterprise (username/cert — not supported)."""
    s = (security or "").lower().replace(" ", "")
    return any(tok in s for tok in ("802.1x", "8021x", "enterprise", "wpa-eap", "wpaeap", "eap"))


def _existing_client_profile_for_ssid(ssid: str) -> str | None:
    """NM client profile name for *ssid*, if any (excludes ``biga-ap``)."""
    target = ssid.strip()
    if not target:
        return None
    for name in list_nm_wifi_profiles():
        if name == AP_CON_NAME:
            continue
        if _nm_field(name, "802-11-wireless.ssid") == target:
            return name
    return None


def append_network(
    ssid: str,
    password: str,
    *,
    sync_nm: bool = True,
    security: str = "",
) -> None:
    """
    Add or refresh *ssid* (moves to front). Trim to ``MAX_NETWORKS`` oldest.

    ``sync_nm=False`` skips creating NetworkManager profiles here. The portal
    uses this so saving credentials never touches ``wlan0`` (which would tear
    down the AP and drop the phone before the success page loads). Boot-time
    ``check_wifi_connectivity`` re-creates the profiles from disk instead.
    """
    ssid = ssid.strip()
    networks = [n for n in load_networks() if n.get("ssid") != ssid]
    entry: dict[str, Any] = {
        "ssid": ssid,
        "password": password,
        "added": time.time(),
    }
    if security:
        entry["security"] = security
    networks.insert(0, entry)
    if len(networks) > MAX_NETWORKS:
        dropped = networks[MAX_NETWORKS:]
        networks = networks[:MAX_NETWORKS]
        if sync_nm:
            for net in dropped:
                _delete_nm_profile(_con_name_for_ssid(str(net.get("ssid", ""))))
        log.info(
            "trimmed %d old network(s); keeping %d",
            len(dropped),
            len(networks),
        )
    save_networks(networks)
    if sync_nm:
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
        security = str(net.get("security", "") or "")
        if not ssid:
            continue
        con_name = _con_name_for_ssid(ssid)
        if is_enterprise_security(security):
            log.warning("skip %r — 802.1X/enterprise is not supported", ssid)
            continue
        if not password:
            existing = _existing_client_profile_for_ssid(ssid)
            if existing and not existing.startswith("biga-wifi-"):
                # Pi Imager / foreign profile — don't clobber a working PSK.
                log.warning(
                    "skip %r — no password in wifi_creds.json; keeping existing NM profile %r",
                    ssid,
                    existing,
                )
                continue
            log.info("creating open WiFi profile for %r (no PSK / MAC allow-list)", ssid)
            _add_nm_wifi_profile(con_name, ssid, password="", priority=max(0, 100 - i * 10))
            continue
        _add_nm_wifi_profile(
            con_name, ssid, password=password, priority=max(0, 100 - i * 10)
        )


def _add_nm_wifi_profile(con_name: str, ssid: str, *, password: str, priority: int) -> None:
    _delete_nm_profile(con_name)
    cmd = [
        "nmcli",
        "connection",
        "add",
        "type",
        "wifi",
        "con-name",
        con_name,
        "ifname",
        WLAN_INTERFACE,
        "ssid",
        ssid,
        "ipv4.method",
        "auto",
        "connection.autoconnect",
        "yes",
        "connection.autoconnect-priority",
        str(priority),
    ]
    if password:
        cmd.extend(["wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", password])
    else:
        cmd.extend(["wifi-sec.key-mgmt", "none"])
    add = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if add.returncode != 0:
        log.warning(
            "nmcli add %s failed: %s",
            con_name,
            (add.stderr or add.stdout or "").strip(),
        )
    else:
        log.info("nmcli profile %s ready for %r", con_name, ssid)


def _delete_nm_profile(name: str) -> None:
    subprocess.run(["nmcli", "connection", "delete", name], capture_output=True, check=False)


def saved_connection_names(
    networks: list[dict[str, Any]] | None = None,
) -> list[tuple[str, str]]:
    """(ssid, nm con-name) newest first."""
    nets = networks if networks is not None else load_networks()
    out: list[tuple[str, str]] = []
    for net in nets:
        ssid = str(net.get("ssid", "")).strip()
        if ssid:
            out.append((ssid, _con_name_for_ssid(ssid)))
    return out


def bring_up_connection(con_name: str) -> bool:
    """Activate one saved profile on wlan0. Returns True if nmcli succeeded."""
    subprocess.run(
        ["nmcli", "device", "disconnect", WLAN_INTERFACE],
        capture_output=True,
        check=False,
    )
    time.sleep(0.5)
    result = subprocess.run(
        ["nmcli", "connection", "up", con_name, "ifname", WLAN_INTERFACE],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        log.warning("connection up %s failed: %s", con_name, err)
        record_wifi_event(f"FAIL nmcli connection up {con_name}", err or "(no nmcli output)")
        return False
    log.info("connection up %s ok", con_name)
    record_wifi_event(f"OK nmcli connection up {con_name}")
    return True


def wlan_has_client_ip() -> bool:
    """True when wlan0 holds a routable (non-AP) IPv4 lease."""
    proc = subprocess.run(
        ["nmcli", "-g", "IP4.ADDRESS", "device", "show", WLAN_INTERFACE],
        capture_output=True,
        text=True,
        check=False,
    )
    ip = (proc.stdout or "").strip().split("/", 1)[0]
    return bool(ip and not ip.startswith("192.168.4."))


def connect_saved_networks(timeout: float = 45) -> bool:
    """
    Join a saved network on boot. Returns True once wlan0 gets a non-AP lease.

    First give NetworkManager time to autoconnect, then explicitly bring up each
    saved profile (newest first). A False result means the credentials are bad or
    every saved network is out of range — the caller re-enters AP provisioning.
    Note: this only fails on association/DHCP failure, not on "internet down"
    (an associated network still yields a LAN IP), so it won't flap on outages.
    """
    names = [con for _, con in saved_connection_names()]
    if not names:
        record_wifi_event("no saved NetworkManager profile names — cannot join")
        return False

    record_wifi_event(
        f"trying saved networks (newest first): {', '.join(names)}",
        f"wait {timeout:.0f}s for NetworkManager autoconnect",
    )

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if wlan_has_client_ip():
            record_wifi_event("joined via NetworkManager autoconnect (client IP on wlan0)")
            return True
        time.sleep(2)

    record_wifi_event("autoconnect did not get a client IP — explicit nmcli up per profile")
    for con_name in names:
        record_wifi_event(f"bring_up {con_name}")
        if not bring_up_connection(con_name):
            continue
        end = time.monotonic() + 12
        while time.monotonic() < end:
            if wlan_has_client_ip():
                record_wifi_event(f"got client IP after bringing up {con_name}")
                return True
            time.sleep(1)
        record_wifi_event(f"{con_name} associated or up but no client IP within 12s")
    ok = wlan_has_client_ip()
    record_wifi_event(
        "join succeeded" if ok else "join failed — no client IP on any saved network"
    )
    return ok


def wipe_all_networks() -> None:
    """Remove all saved WiFi networks and NM profiles (full factory reset)."""
    for net in load_networks():
        _delete_nm_profile(_con_name_for_ssid(str(net.get("ssid", ""))))
    _delete_nm_profile("biga-client")
    CREDS_FILE.unlink(missing_ok=True)
    exit_provisioning()
