#!/usr/bin/env python3
"""
Factory reset button monitor.

Watches GPIO BCM 26 (active-low, internal pull-up).
Hold the button for HOLD_SECONDS to enter **add-network** provisioning:
  1. Keep saved WiFi networks (up to 7); show QR / portal to add another.
  2. Reboot — releases the display/DRM and starts portal + setup screen.

Env vars
  BIGA_RESET_PIN    BCM pin number (default: 26)
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
FIRSTBOOT_SENTINEL = Path("/etc/biga/.firstboot_done")
SETUP_AP = Path("/home/pi/BigA/scripts/setup_ap.sh")
PORTAL_DIR = Path("/home/pi/BigA/portal")
AP_CON_NAME = "biga-ap"

sys.path.insert(0, str(PORTAL_DIR))
from wifi_store import enter_provisioning, has_networks, prepare_ap_provisioning_mode  # noqa: E402

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
    """Turn off win-scene NeoPixels after biga exits (strip holds last frame otherwise)."""
    src = Path("/home/pi/BigA/src")
    if not (src / "pi_tracker" / "gpio_leds.py").is_file():
        log.debug("gpio_leds not found — skip LED shutdown")
        return
    script = (
        "import sys; "
        f"sys.path.insert(0, {str(src)!r}); "
        "from pi_tracker.gpio_leds import cleanup_gpio, init_gpio, set_win_led; "
        "init_gpio(); set_win_led(False); cleanup_gpio()"
    )
    _run([sys.executable, "-c", script], label="shutdown NeoPixels")


def _reboot(delay_sec: float = 3.0) -> None:
    log.info("Rebooting in %.0fs (clean display + provisioning services)…", delay_sec)
    time.sleep(delay_sec)
    _run(["sync"], label="sync")
    _run(["systemctl", "reboot"], label="systemctl reboot")


def factory_reset() -> None:
    log.info("Add-network provisioning triggered (GPIO %d held %ds).", RESET_PIN, HOLD_SECONDS)

    enter_provisioning()
    if has_networks():
        log.info("Keeping existing saved networks — portal will append another.")
    else:
        log.info("No saved networks yet — first-time setup.")

    _recreate_ap_profile()

    # Drop home WiFi before reboot so the next boot does not race NM autoconnect.
    prepare_ap_provisioning_mode()

    # Stop scoreboard before reboot — it holds DRM and GPIO 19 NeoPixels.
    _service("stop", "biga")
    _service("stop", "biga-setup-screen")
    _service("stop", "biga-portal")

    time.sleep(0.75)
    _shutdown_leds()

    _reboot()


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
