"""
BigA WiFi Provisioning Portal
------------------------------
Runs in AP mode (hostapd + dnsmasq) to let a new user connect and supply
their home WiFi credentials.  Requires root (same as the biga service).

State machine
  AP mode   – Pi broadcasts its own SSID; this portal is active.
  Client    – user's credentials are written; BigA service takes over.

Env vars
  BIGA_AP_SSID       Override AP SSID  (default: BigA-<last4 of wlan0 MAC>)
  BIGA_AP_PASSWORD   Override AP password (default: bigasetup)
  BIGA_PORTAL_PORT   HTTP port (default: 80)
"""

import io
import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import qrcode
from flask import Flask, jsonify, redirect, render_template, request, send_file, url_for

from captive import (
    APPLE_CNA_HTML,
    PORTAL_HTTP_URL,
    PORTAL_IP,
    wifi_qr_string,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CREDS_FILE = Path("/etc/biga/wifi_creds.json")
INTERFACE = "wlan0"
AP_IP = PORTAL_IP
AP_PASSWORD = os.environ.get("BIGA_AP_PASSWORD", "bigasetup")
PORT = int(os.environ.get("BIGA_PORTAL_PORT", 80))

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("portal")


def _ap_ssid() -> str:
    """Return a unique SSID derived from the wlan0 MAC address."""
    override = os.environ.get("BIGA_AP_SSID", "")
    if override:
        return override
    try:
        mac = Path(f"/sys/class/net/{INTERFACE}/address").read_text().strip()
        suffix = mac.replace(":", "")[-4:].upper()
        return f"BigA-{suffix}"
    except OSError:
        return "BigA-Setup"


AP_SSID = _ap_ssid()

# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

app = Flask(__name__)
app.secret_key = os.urandom(24)


# ---------------------------------------------------------------------------
# WiFi helpers
# ---------------------------------------------------------------------------

def scan_networks() -> list[dict]:
    """Return available networks sorted by signal strength."""
    try:
        result = subprocess.run(
            [
                "nmcli", "--terse", "--fields", "SSID,SIGNAL,SECURITY",
                "device", "wifi", "list", "ifname", INTERFACE,
            ],
            capture_output=True, text=True, timeout=12, check=False,
        )
        seen: set[str] = set()
        networks: list[dict] = []
        for line in result.stdout.strip().splitlines():
            parts = line.split(":")
            ssid = parts[0].strip() if parts else ""
            if not ssid or ssid == AP_SSID:
                continue
            signal = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
            security = parts[2].strip() if len(parts) > 2 else "WPA"
            if ssid not in seen:
                seen.add(ssid)
                networks.append({"ssid": ssid, "signal": signal, "security": security})
        return sorted(networks, key=lambda n: -n["signal"])
    except Exception as exc:
        log.warning("scan_networks failed: %s", exc)
        return []


def connect_wifi(ssid: str, password: str) -> tuple[bool, str]:
    """
    Create an NM connection profile for the user's network and persist creds.
    Does NOT bring down the AP or attempt to connect immediately — the Pi
    reboots after this returns, at which point NM connects cleanly on boot.
    Returns (success, human-readable message).
    """
    log.info("Saving connection profile for %r", ssid)

    # Wipe stale profiles to avoid key-mgmt / duplicate errors.
    for name in (ssid, "biga-client"):
        subprocess.run(["nmcli", "connection", "delete", name], capture_output=True, check=False)

    # Create explicit profile with key-mgmt — avoids "property is missing" errors.
    add = subprocess.run(
        [
            "nmcli", "connection", "add",
            "type", "wifi",
            "con-name", "biga-client",
            "ifname", INTERFACE,
            "ssid", ssid,
            "wifi-sec.key-mgmt", "wpa-psk",
            "wifi-sec.psk", password,
            "ipv4.method", "auto",
            "connection.autoconnect", "yes",
            "connection.autoconnect-priority", "10",
        ],
        capture_output=True, text=True, check=False,
    )
    if add.returncode != 0:
        detail = (add.stderr or add.stdout).strip()
        log.warning("nmcli connection add failed: %s", detail)
        return False, detail or "Failed to save connection profile."

    _persist_creds(ssid, password)
    return True, "Profile saved."


def _persist_creds(ssid: str, password: str) -> None:
    """Save credentials so the factory-reset button can wipe them later."""
    CREDS_FILE.parent.mkdir(parents=True, exist_ok=True)
    CREDS_FILE.write_text(
        json.dumps({"ssid": ssid, "password": password}, indent=2)
    )
    CREDS_FILE.chmod(0o600)
    log.info("Credentials persisted to %s", CREDS_FILE)


def wipe_creds() -> None:
    """Wipe credentials and restore AP mode (also called by the reset button)."""
    if CREDS_FILE.exists():
        CREDS_FILE.unlink()
        log.info("Credentials wiped.")

    subprocess.run(["systemctl", "stop",  "biga"],        check=False)
    subprocess.run(["nmcli", "con", "up", "biga-ap"],     check=False)
    subprocess.run(["systemctl", "start", "biga-portal"], check=False)
    log.info("AP mode restored. Portal active at %s.", AP_IP)


def provisioned() -> bool:
    """True if user credentials have been saved."""
    return CREDS_FILE.exists()


def _switch_to_client_mode() -> None:
    """
    Reboot the Pi after a short delay so the success page can load.
    On reboot, NM auto-connects the biga-client profile, biga starts cleanly.
    """
    def _run() -> None:
        time.sleep(4)
        log.info("Rebooting to apply WiFi credentials…")
        subprocess.run(["reboot"])

    threading.Thread(target=_run, daemon=True).start()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Captive portal detection — iOS, Android, Windows probe these on join.
# Return non-success HTML (Apple) or 302 (others) so the OS opens a browser.
# HTTPS probes cannot be answered without a cert; DHCP option 114 + DNS help.
# ---------------------------------------------------------------------------

def _captive_portal_response():
    path = request.path.rstrip("/") or "/"
    # Apple CNA: anything other than the exact Success page should pop the sheet.
    if path in ("/hotspot-detect.html", "/library/test/success.html", "/canonical.html"):
        return APPLE_CNA_HTML, 200, {"Content-Type": "text/html", "Cache-Control": "no-store"}
    # Android / Windows: redirect to portal (must not return 204 Success).
    return redirect(PORTAL_HTTP_URL, code=302)


@app.route("/hotspot-detect.html")
@app.route("/library/test/success.html")
@app.route("/canonical.html")
@app.route("/generate_204")
@app.route("/gen_204")
@app.route("/connecttest.txt")
@app.route("/ncsi.txt")
@app.route("/redirect")
@app.route("/success.txt")
def captive_portal_check():
    return _captive_portal_response()


@app.route("/")
def index():
    networks = scan_networks()
    return render_template("index.html", networks=networks, ap_ssid=AP_SSID)


@app.route("/connect", methods=["POST"])
def connect():
    ssid = request.form.get("ssid", "").strip()
    password = request.form.get("password", "").strip()

    if not ssid:
        return render_template(
            "index.html",
            networks=scan_networks(),
            ap_ssid=AP_SSID,
            error="Please select a network.",
        )

    success, message = connect_wifi(ssid, password)

    if success:
        _switch_to_client_mode()
        return render_template("success.html", ssid=ssid, ap_ssid=AP_SSID)

    return render_template(
        "index.html",
        networks=scan_networks(),
        ap_ssid=AP_SSID,
        selected_ssid=ssid,
        error=message,
    )


@app.route("/qr.png")
def qr_png():
    """PNG QR code that encodes joining the Pi's AP network."""
    img = qrcode.make(wifi_qr_string(AP_SSID, AP_PASSWORD))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png")


@app.route("/status")
def status():
    active = subprocess.run(
        ["nmcli", "--terse", "--fields", "NAME,STATE,DEVICE", "connection", "show", "--active"],
        capture_output=True, text=True, check=False,
    )
    return jsonify({
        "provisioned": provisioned(),
        "ap_ssid": AP_SSID,
        "connections": active.stdout.strip().splitlines(),
    })


@app.route("/reset", methods=["POST"])
def factory_reset():
    """
    Wipe credentials and restore AP mode.
    Phase 3: this same logic runs from the physical reset button GPIO handler.
    """
    wipe_creds()
    return redirect(url_for("index"))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if os.geteuid() != 0:
        sys.exit("portal.py must run as root (nmcli device wifi connect requires root)")
    log.info("BigA portal starting — AP SSID: %s  port: %d", AP_SSID, PORT)
    app.run(host="0.0.0.0", port=PORT, debug=False)
