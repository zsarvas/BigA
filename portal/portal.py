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
from pathlib import Path

import qrcode
from flask import Flask, jsonify, redirect, render_template, request, send_file, url_for

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CREDS_FILE = Path("/etc/biga/wifi_creds.json")
INTERFACE = "wlan0"
AP_IP = "192.168.4.1"
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
    Write credentials and connect via nmcli.
    Returns (success, human-readable message).
    """
    log.info("Attempting to connect to %r", ssid)
    try:
        result = subprocess.run(
            [
                "nmcli", "device", "wifi", "connect", ssid,
                "password", password, "ifname", INTERFACE,
            ],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except subprocess.TimeoutExpired:
        return False, "Connection timed out — check the password and try again."
    except Exception as exc:
        return False, str(exc)

    if result.returncode == 0:
        _persist_creds(ssid, password)
        return True, "Connected."

    detail = (result.stderr or result.stdout).strip()
    log.warning("nmcli connect failed (%d): %s", result.returncode, detail)
    return False, detail or "Connection failed — wrong password?"


def _persist_creds(ssid: str, password: str) -> None:
    """Save credentials so the factory-reset button can wipe them later."""
    CREDS_FILE.parent.mkdir(parents=True, exist_ok=True)
    CREDS_FILE.write_text(
        json.dumps({"ssid": ssid, "password": password}, indent=2)
    )
    CREDS_FILE.chmod(0o600)
    log.info("Credentials persisted to %s", CREDS_FILE)


def wipe_creds() -> None:
    """Remove saved credentials (called by factory-reset button, Phase 3)."""
    if CREDS_FILE.exists():
        CREDS_FILE.unlink()
        log.info("Credentials wiped.")


def provisioned() -> bool:
    """True if user credentials have been saved."""
    return CREDS_FILE.exists()


def _switch_to_client_mode() -> None:
    """
    Tear down the AP and hand control to the client network.
    Phase 2 TODO: stop hostapd + dnsmasq, flush static IP,
    then restart NetworkManager so it picks up the new connection.
    For now we just restart biga so it retries the MLB API once online.
    """
    log.info("Switching to client mode…")
    subprocess.run(["sudo", "systemctl", "stop", "biga-portal"], check=False)
    subprocess.run(["sudo", "systemctl", "start", "biga"], check=False)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

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
    wifi_str = f"WIFI:T:WPA;S:{AP_SSID};P:{AP_PASSWORD};;"
    img = qrcode.make(wifi_str)
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
    # TODO Phase 3: bring AP back up (hostapd + dnsmasq + static IP)
    return redirect(url_for("index"))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if os.geteuid() != 0:
        sys.exit("portal.py must run as root (nmcli device wifi connect requires root)")
    log.info("BigA portal starting — AP SSID: %s  port: %d", AP_SSID, PORT)
    app.run(host="0.0.0.0", port=PORT, debug=False)
