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
    PORTAL_HTTP_URL,
    PORTAL_IP,
    ap_ssid,
    wifi_qr_string,
)
from wifi_store import (
    append_network,
    enter_provisioning,
    has_networks,
    is_provisioning,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CREDS_FILE = Path("/etc/biga/wifi_creds.json")  # legacy import path for templates/logs
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
            if not ssid or ssid == ap_ssid():
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
    Append a network profile and persist credentials (keeps up to 7 saved).
    Reboot applies NM profiles cleanly on the client side.
    """
    log.info("Saving connection profile for %r", ssid)
    try:
        append_network(ssid, password)
    except Exception as exc:
        log.warning("append_network failed: %s", exc)
        return False, str(exc) or "Failed to save network."
    return True, "Network saved."


def wipe_creds() -> None:
    """Enter provisioning mode to add/replace WiFi (keeps saved networks)."""
    enter_provisioning()
    subprocess.run(["systemctl", "stop", "biga"], check=False)
    subprocess.run(["nmcli", "con", "up", "biga-ap"], check=False)
    subprocess.run(["systemctl", "start", "biga-portal"], check=False)
    subprocess.run(["systemctl", "start", "biga-setup-screen"], check=False)
    log.info("Provisioning mode — portal at %s", AP_IP)


def provisioned() -> bool:
    """True when at least one network is saved and not in provisioning mode."""
    return has_networks() and not is_provisioning()


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
# Always 302 to the setup portal (never return Apple's "Success" page or 204).
# ---------------------------------------------------------------------------

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
    return redirect(PORTAL_HTTP_URL, code=302)


@app.route("/")
def index():
    networks = scan_networks()
    return render_template("index.html", networks=networks, ap_ssid=ap_ssid())


@app.route("/connect", methods=["POST"])
def connect():
    ssid = request.form.get("ssid", "").strip()
    password = request.form.get("password", "").strip()

    if not ssid:
        return render_template(
            "index.html",
            networks=scan_networks(),
            ap_ssid=ap_ssid(),
            error="Please select a network.",
        )

    success, message = connect_wifi(ssid, password)

    if success:
        _switch_to_client_mode()
        return render_template("success.html", ssid=ssid, ap_ssid=ap_ssid())

    return render_template(
        "index.html",
        networks=scan_networks(),
        ap_ssid=ap_ssid(),
        selected_ssid=ssid,
        error=message,
    )


@app.route("/qr.png")
def qr_png():
    """PNG QR code that encodes joining the Pi's AP network."""
    img = qrcode.make(wifi_qr_string(ap_ssid(), AP_PASSWORD))
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
        "ap_ssid": ap_ssid(),
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
    log.info("BigA portal starting — AP SSID: %s  port: %d", ap_ssid(), PORT)
    app.run(host="0.0.0.0", port=PORT, debug=False)
