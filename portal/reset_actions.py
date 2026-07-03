"""
Shared provisioning reset actions for the GPIO button and captive portal.

Soft reset (5 s hold / portal add-network):
  Enter AP provisioning, keep saved WiFi + game state + highlights.

Full reset (10 s hold / portal checkbox):
  Wipe WiFi, persisted game state, downloaded highlights, then provision.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from pathlib import Path

REPO = Path("/home/pi/BigA")
SETUP_AP = REPO / "scripts" / "setup_ap.sh"
SRC_DIR = REPO / "src"
PORTAL_DIR = REPO / "portal"
FIRSTBOOT_SENTINEL = Path("/etc/biga/.firstboot_done")
AP_CON_NAME = "biga-ap"

if str(PORTAL_DIR) not in sys.path:
    sys.path.insert(0, str(PORTAL_DIR))

from wifi_store import (  # noqa: E402
    enter_provisioning,
    ensure_ssh_running,
    has_networks,
    prepare_ap_provisioning_mode,
    wipe_all_networks,
)

log = logging.getLogger(__name__)


def _run(cmd: list[str], *, label: str = "") -> subprocess.CompletedProcess[str]:
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode == 0:
        log.info("%s → OK", label or " ".join(cmd))
    else:
        err = (result.stderr or result.stdout or "").strip()
        log.error("%s failed: %s", label or " ".join(cmd), err)
    return result


def _service(action: str, name: str) -> None:
    _run(["systemctl", action, name], label=f"systemctl {action} {name}")


def _recreate_ap_profile() -> None:
    """Rebuild biga-ap from the current wlan0 MAC so SSID/QR stay in sync."""
    FIRSTBOOT_SENTINEL.unlink(missing_ok=True)
    _run(["nmcli", "connection", "down", AP_CON_NAME], label="nmcli down biga-ap")
    if SETUP_AP.is_file():
        _run(["bash", str(SETUP_AP)], label="setup_ap.sh")
    else:
        log.error("setup_ap.sh missing at %s", SETUP_AP)


def _shutdown_leds() -> None:
    """Turn off NeoPixels after biga exits."""
    if not (SRC_DIR / "pi_tracker" / "gpio_leds.py").is_file():
        return
    script = (
        "import sys; "
        f"sys.path.insert(0, {str(SRC_DIR)!r}); "
        "from pi_tracker.gpio_leds import cleanup_gpio, end_reset_hold_feedback, "
        "init_gpio, set_win_led; "
        "end_reset_hold_feedback(); init_gpio(); set_win_led(False); cleanup_gpio()"
    )
    _run([sys.executable, "-c", script], label="shutdown NeoPixels")


def _wipe_game_data() -> None:
    if str(SRC_DIR) not in sys.path:
        sys.path.insert(0, str(SRC_DIR))
    from pi_tracker.mlb_highlights import wipe_game_highlights
    from pi_tracker.state import clear_persisted_state

    clear_persisted_state()
    wipe_game_highlights(None)
    log.info("cleared persisted game state and all downloaded highlights")


def _finish_provisioning_reboot(*, label: str) -> None:
    prepare_ap_provisioning_mode()
    ensure_ssh_running()

    _service("stop", "biga")
    _service("stop", "biga-setup-screen")
    _service("stop", "biga-portal")

    time.sleep(0.75)
    _shutdown_leds()

    log.info("%s — rebooting in 3s…", label)
    time.sleep(3.0)
    _run(["sync"], label="sync")
    _run(["systemctl", "reboot"], label="systemctl reboot")


def perform_soft_reset() -> None:
    """
    Network recovery: AP provisioning while keeping WiFi creds and game data.
    """
    log.info("Soft reset — enter add-network provisioning (keep game state).")
    enter_provisioning()
    if has_networks():
        log.info("Keeping existing saved networks — portal will append another.")
    else:
        log.info("No saved networks yet — first-time setup.")

    _recreate_ap_profile()
    _finish_provisioning_reboot(label="Soft reset complete")


def perform_full_reset() -> None:
    """
    Factory reset: wipe WiFi, game state, highlights, then AP provisioning.
    """
    log.info("Full factory reset — wiping WiFi, game state, and highlights.")
    wipe_all_networks()
    _wipe_game_data()
    enter_provisioning()
    _recreate_ap_profile()
    _finish_provisioning_reboot(label="Full factory reset complete")
