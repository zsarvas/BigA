"""Win-scene indicator on a GPIO pin (default BCM 19). No-op off-Pi or without RPi.GPIO."""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

_WIN_LED_GPIO = int(os.environ.get("BIGA_WIN_LED_GPIO", "19"))
_initialized = False
_win_active = False


def _gpio_module():
    try:
        import RPi.GPIO as GPIO  # type: ignore[import-untyped]

        return GPIO
    except ImportError:
        return None


def init_gpio() -> None:
    """Configure win LED pin as output (LOW). Safe to call repeatedly."""
    global _initialized
    if _initialized:
        return
    GPIO = _gpio_module()
    if GPIO is None:
        log.debug("RPi.GPIO not available; win LED disabled")
        return
    try:
        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(_WIN_LED_GPIO, GPIO.OUT, initial=GPIO.LOW)
        _initialized = True
    except Exception as e:  # noqa: BLE001
        log.warning("GPIO init failed (pin %s): %s", _WIN_LED_GPIO, e)


def set_win_led(active: bool) -> None:
    """Drive win LED HIGH while ``active`` (win scene), else LOW."""
    global _win_active
    if active == _win_active:
        return
    GPIO = _gpio_module()
    if GPIO is None:
        return
    if not _initialized:
        init_gpio()
    if not _initialized:
        return
    try:
        GPIO.output(_WIN_LED_GPIO, GPIO.HIGH if active else GPIO.LOW)
        _win_active = active
    except Exception as e:  # noqa: BLE001
        log.warning("GPIO output failed (pin %s): %s", _WIN_LED_GPIO, e)


def cleanup_gpio() -> None:
    """Turn LED off and release GPIO."""
    global _initialized, _win_active
    GPIO = _gpio_module()
    if GPIO is None or not _initialized:
        _win_active = False
        return
    try:
        GPIO.output(_WIN_LED_GPIO, GPIO.LOW)
        GPIO.cleanup(_WIN_LED_GPIO)
    except Exception as e:  # noqa: BLE001
        log.debug("GPIO cleanup: %s", e)
    _initialized = False
    _win_active = False
