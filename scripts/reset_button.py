#!/usr/bin/env python3
"""
Reset button monitor (GPIO BCM 26, active-low).

Hold timing (release unless noted):
  ~2 s  — NeoPixel ring shows progress (stops biga to access the strip).
  5 s   — release → soft reset (WiFi / add-network; keeps game state).
  10 s  — auto → full factory reset (WiFi + game state + highlights).

NeoPixel feedback:
  0–5 s: slow gold pulse (WiFi reset coming).
  5–10 s: fast gold/red blink (release for WiFi, keep holding for full reset).
  ≥10 s: rapid red pulse (full reset).

Env vars
  BIGA_RESET_PIN          BCM pin (default: 26)
  BIGA_RESET_HOLD_SOFT    Soft-reset threshold seconds (default: 5)
  BIGA_RESET_HOLD_FULL    Full-reset threshold seconds (default: 10)
  BIGA_RESET_LED_TAKEOVER Seconds before stopping biga for LED feedback (default: 2)
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from pathlib import Path

RESET_PIN = int(os.environ.get("BIGA_RESET_PIN", 26))
SOFT_HOLD_SEC = float(os.environ.get("BIGA_RESET_HOLD_SOFT", "5"))
FULL_HOLD_SEC = float(os.environ.get("BIGA_RESET_HOLD_FULL", "10"))
LED_TAKEOVER_SEC = float(os.environ.get("BIGA_RESET_LED_TAKEOVER", "2"))

if FULL_HOLD_SEC <= SOFT_HOLD_SEC:
    FULL_HOLD_SEC = SOFT_HOLD_SEC + 5.0

REPO = Path("/home/pi/BigA")
SRC_DIR = REPO / "src"
PORTAL_DIR = REPO / "portal"

sys.path.insert(0, str(PORTAL_DIR))
sys.path.insert(0, str(SRC_DIR))

from reset_actions import perform_full_reset, perform_soft_reset  # noqa: E402

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

_leds_available = False
try:
    from pi_tracker.gpio_leds import (  # noqa: E402
        begin_reset_hold_feedback,
        end_reset_hold_feedback,
        set_reset_hold_seconds,
    )

    _leds_available = True
except Exception as exc:
    log.warning("NeoPixel feedback unavailable: %s", exc)

_hold_lock = threading.Lock()
_pressed_at: float | None = None
_biga_stopped = False
_poll_thread: threading.Thread | None = None
_reset_in_progress = False


def _service(action: str, name: str) -> None:
    import subprocess

    subprocess.run(
        ["systemctl", action, name],
        capture_output=True,
        text=True,
        check=False,
    )


def _stop_biga_for_leds() -> None:
    global _biga_stopped
    if _biga_stopped:
        return
    log.info("Stopping biga for reset-button NeoPixel feedback…")
    _service("stop", "biga")
    _biga_stopped = True
    time.sleep(0.4)


def _restart_biga_if_needed() -> None:
    global _biga_stopped
    if not _biga_stopped:
        return
    log.info("Reset cancelled — restarting biga.")
    _service("start", "biga")
    _biga_stopped = False


def _end_led_feedback() -> None:
    if not _leds_available:
        return
    try:
        end_reset_hold_feedback()
    except Exception as exc:
        log.debug("end_reset_hold_feedback: %s", exc)


def _update_led_feedback(held: float) -> None:
    if not _leds_available:
        return
    try:
        set_reset_hold_seconds(held)
    except Exception as exc:
        log.debug("set_reset_hold_seconds: %s", exc)


def _begin_led_feedback() -> None:
    if not _leds_available:
        return
    try:
        begin_reset_hold_feedback()
    except Exception as exc:
        log.warning("begin_reset_hold_feedback failed: %s", exc)


def _on_press() -> None:
    global _pressed_at, _poll_thread, _biga_stopped, _reset_in_progress
    with _hold_lock:
        if _reset_in_progress:
            return
        _pressed_at = time.monotonic()
        _biga_stopped = False
        if _poll_thread is None or not _poll_thread.is_alive():
            _poll_thread = threading.Thread(target=_hold_poll_loop, daemon=True)
            _poll_thread.start()


def _hold_poll_loop() -> None:
    global _pressed_at, _reset_in_progress
    btn = _button
    while True:
        if not btn.is_pressed:
            break
        with _hold_lock:
            start = _pressed_at
        if start is None:
            return
        held = time.monotonic() - start

        if held >= LED_TAKEOVER_SEC and not _biga_stopped:
            _stop_biga_for_leds()
            _begin_led_feedback()

        if _biga_stopped:
            _update_led_feedback(held)

        if held >= FULL_HOLD_SEC:
            with _hold_lock:
                _reset_in_progress = True
            _end_led_feedback()
            log.info("Full reset triggered at %.1fs hold.", held)
            perform_full_reset()
            return

        time.sleep(0.05)

    with _hold_lock:
        start = _pressed_at
        _pressed_at = None
    if start is None:
        return

    held = time.monotonic() - start
    _end_led_feedback()

    if held < SOFT_HOLD_SEC:
        log.debug("Button released at %.1fs — below soft threshold, no action.", held)
        _restart_biga_if_needed()
        return

    if held < FULL_HOLD_SEC:
        with _hold_lock:
            _reset_in_progress = True
        log.info("Soft reset triggered at %.1fs hold (release).", held)
        perform_soft_reset()
        return


def _run_stub() -> None:
    log.info(
        "Stub mode — GPIO %d monitor inactive. "
        "Soft=%.0fs full=%.0fs. Press Ctrl-C to exit.",
        RESET_PIN,
        SOFT_HOLD_SEC,
        FULL_HOLD_SEC,
    )
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        pass


_button: Button | None = None


def main() -> None:
    global _button
    if not _GPIO_AVAILABLE:
        _run_stub()
        return

    log.info(
        "Monitoring GPIO BCM %d — hold %.0fs (release) for WiFi reset, "
        "%.0fs for full factory reset.",
        RESET_PIN,
        SOFT_HOLD_SEC,
        FULL_HOLD_SEC,
    )

    _button = Button(RESET_PIN, pull_up=True, bounce_time=0.05)
    _button.when_pressed = _on_press

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Shutting down.")
    finally:
        _end_led_feedback()
        _button.close()


if __name__ == "__main__":
    if os.geteuid() != 0:
        sys.exit("reset_button.py must run as root")
    main()
