#!/usr/bin/env python3
"""
Factory reset button monitor.

Watches GPIO BCM 27 (active-low, internal pull-up).
Hold the button for HOLD_SECONDS to trigger a factory reset:
  1. Wipe saved WiFi credentials.
  2. Stop the biga service.
  3. Start the biga-portal service (AP provisioning mode).
  4. TODO Phase 2: restore hostapd + dnsmasq AP networking.

Env vars
  BIGA_RESET_PIN    BCM pin number (default: 27)
  BIGA_RESET_HOLD   Seconds to hold for reset (default: 5)
"""

import logging
import os
import subprocess
import sys
import time
from pathlib import Path

RESET_PIN = int(os.environ.get("BIGA_RESET_PIN", 26))
HOLD_SECONDS = int(os.environ.get("BIGA_RESET_HOLD", 5))
CREDS_FILE = Path("/etc/biga/wifi_creds.json")

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/var/log/biga-reset.log"),
    ],
)
log = logging.getLogger("reset_button")

try:
    from gpiozero import Button
    _GPIO_AVAILABLE = True
except Exception:
    _GPIO_AVAILABLE = False
    log.warning("gpiozero not available — running in stub mode (no GPIO)")


def _service(action: str, name: str) -> None:
    result = subprocess.run(
        ["sudo", "systemctl", action, name],
        capture_output=True, text=True, check=False,
    )
    if result.returncode == 0:
        log.info("systemctl %s %s → OK", action, name)
    else:
        log.error("systemctl %s %s failed: %s", action, name, result.stderr.strip())


def factory_reset() -> None:
    log.info("Factory reset triggered (GPIO %d held %ds).", RESET_PIN, HOLD_SECONDS)

    if CREDS_FILE.exists():
        CREDS_FILE.unlink()
        log.info("WiFi credentials wiped (%s).", CREDS_FILE)
    else:
        log.info("No credentials file found — already in factory state.")

    _service("stop", "biga")

    # Restore AP mode via NetworkManager
    result = subprocess.run(
        ["nmcli", "con", "up", "biga-ap"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode == 0:
        log.info("AP mode restored (biga-ap up).")
    else:
        log.error("nmcli con up biga-ap failed: %s", result.stderr.strip())

    _service("start", "biga-portal")
    log.info("Factory reset complete. Portal is active at 192.168.4.1.")


def _run_stub() -> None:
    """No-op loop when GPIO is unavailable (dev/Mac environment)."""
    log.info("Stub mode — GPIO %d monitor inactive. Press Ctrl-C to exit.", RESET_PIN)
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        pass


def main() -> None:
    if not _GPIO_AVAILABLE:
        _run_stub()
        return

    log.info("Monitoring GPIO BCM %d — hold %ds to factory reset.", RESET_PIN, HOLD_SECONDS)

    btn = Button(RESET_PIN, pull_up=True, hold_time=HOLD_SECONDS, bounce_time=0.05)
    btn.when_held = factory_reset

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Shutting down.")
    finally:
        btn.close()


if __name__ == "__main__":
    if os.geteuid() != 0:
        sys.exit("reset_button.py must run as root")
    main()
