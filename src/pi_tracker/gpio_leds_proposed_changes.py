"""
Proposed win-scene NeoPixel animation (Adafruit ``neopixel`` + Blinka).

Same public API as ``gpio_leds.py`` for ``app.py``:
    init_gpio() / set_win_led(active) / cleanup_gpio()
    is_muted() / set_muted(bool)

Core animation (from the other dev's Arduino port):
    solid red → white racer ×3 → white theater → red racer ×3 → red theater → repeat

Test **LEDs only** (no pygame):

    sudo BIGA_LED_WIN_DEBUG=1 PYTHONPATH=src python3 -m pi_tracker.gpio_leds_proposed_changes

Test **wiring / strip length** (solid white):

    sudo BIGA_LED_DEBUG=1 PYTHONPATH=src python3 -m pi_tracker.gpio_leds_proposed_changes

Test **win screen + LEDs** (point ``app.py`` at this module or rename to ``gpio_leds.py``):

    sudo BIGA_LED_WIN_DEBUG=1 openvt -c 2 -f -w -- python3 run_pi_ui.py --demo-final

Env tunables: ``BIGA_WIN_LED_GPIO`` (default 19), ``BIGA_WIN_LED_COUNT`` (30),
``BIGA_WIN_LED_BRIGHTNESS`` (10, 0–255 — matches Arduino ``setBrightness(10)``).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config (env)
# ---------------------------------------------------------------------------


def _env_bool(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int, lo: int | None = None, hi: int | None = None) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        v = int(raw, 10)
    except ValueError:
        return default
    if lo is not None and v < lo:
        return default
    if hi is not None and v > hi:
        return default
    return v


_WIN_LED_GPIO = _env_int("BIGA_WIN_LED_GPIO", 19)
_WIN_LED_COUNT = _env_int("BIGA_WIN_LED_COUNT", 30, lo=1, hi=600)
_WIN_LED_BRIGHTNESS = _env_int("BIGA_WIN_LED_BRIGHTNESS", 10, lo=0, hi=255)
_LED_DEBUG = _env_bool("BIGA_LED_DEBUG")
_LED_DEBUG_COLOR = (255, 255, 255)
_LED_WIN_DEBUG = _env_bool("BIGA_LED_WIN_DEBUG")

_RACER_LENGTH = 3
_WAIT_SEC = 0.030  # 30 ms — Arduino delay

RED = (255, 0, 0)
WHITE = (255, 255, 255)
OFF = (0, 0, 0)

_lock = threading.RLock()
_strip: Any = None
_led_count = _WIN_LED_COUNT
_initialized = False
_win_active = False
_anim_thread: threading.Thread | None = None
_anim_stop = threading.Event()

# ---------------------------------------------------------------------------
# Mute button (BCM 25 by default)
# ---------------------------------------------------------------------------

_MUTE_PIN = _env_int("BIGA_MUTE_PIN", 25)
_MUTE_STATE_FILE = Path(os.environ.get("BIGA_MUTE_STATE_FILE", "/etc/biga/mute.json"))

_mute_lock = threading.Lock()
_muted = False
_mute_button = None


def _load_mute_state() -> bool:
    try:
        data = json.loads(_MUTE_STATE_FILE.read_text())
        return bool(data.get("muted", False))
    except Exception:
        return False


def _save_mute_state(muted: bool) -> None:
    try:
        _MUTE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _MUTE_STATE_FILE.write_text(json.dumps({"muted": muted}))
    except Exception as e:
        log.debug("could not save mute state: %s", e)


def is_muted() -> bool:
    with _mute_lock:
        return _muted


def set_muted(muted: bool) -> None:
    global _muted
    with _mute_lock:
        _muted = muted
    _save_mute_state(muted)
    log.info("audio %s", "muted" if muted else "unmuted")


def _toggle_mute() -> None:
    global _muted
    with _mute_lock:
        _muted = not _muted
        new_state = _muted
    _save_mute_state(new_state)
    log.info("mute toggled → %s", "muted" if new_state else "unmuted")


def _init_mute_button() -> None:
    global _muted, _mute_button
    _muted = _load_mute_state()
    try:
        from gpiozero import Button  # type: ignore[import-untyped]

        btn = Button(_MUTE_PIN, pull_up=True, bounce_time=0.05)
        btn.when_pressed = _toggle_mute
        _mute_button = btn
        log.info("mute button on BCM %d (press to toggle)", _MUTE_PIN)
    except Exception as e:
        log.debug("mute button GPIO unavailable (BCM %d): %s", _MUTE_PIN, e)


# ---------------------------------------------------------------------------
# NeoPixel (Adafruit Blinka)
# ---------------------------------------------------------------------------


def _import_neopixel():
    try:
        import board  # type: ignore[import-untyped]
        import neopixel  # type: ignore[import-untyped]

        return board, neopixel
    except ImportError:
        return None, None


def _board_pin(board: Any, gpio: int) -> Any:
    pin = getattr(board, f"D{gpio}", None)
    if pin is None:
        raise ValueError(f"board has no D{gpio} for BIGA_WIN_LED_GPIO={gpio}")
    return pin


def _fill_blocking(rgb: tuple[int, int, int]) -> None:
    if _strip is None:
        return
    try:
        _strip.fill(rgb)
        _strip.show()
    except Exception as e:  # noqa: BLE001
        log.debug("NeoPixel fill failed: %s", e)


def _racer_chase(strip: Any, color1: tuple[int, int, int], color2: tuple[int, int, int], stop: threading.Event) -> None:
    """A racer of color1 sweeps across a color2 background."""
    n = _led_count
    for i in range(_RACER_LENGTH, n):
        if stop.is_set():
            return
        strip[i] = color1
        if i > _RACER_LENGTH:
            strip[i - (_RACER_LENGTH + 1)] = color2
        strip.show()
        if stop.wait(_WAIT_SEC):
            return

    for i in range(n - _RACER_LENGTH - 1, n):
        if stop.is_set():
            return
        strip[i] = color2
        strip[i - n + _RACER_LENGTH + 1] = color1
        strip.show()
        if stop.wait(_WAIT_SEC):
            return


def _theater_chase(strip: Any, color: tuple[int, int, int], wait_ms: int, stop: threading.Event) -> None:
    """Every third pixel lights up, cycling offsets 0–2, ten times."""
    for _ in range(10):
        if stop.is_set():
            return
        for b in range(3):
            if stop.is_set():
                return
            strip.fill(OFF)
            for c in range(b, _led_count, 3):
                strip[c] = color
            strip.show()
            if stop.wait(wait_ms / 1000.0):
                return


def _win_animation_cycle(strip: Any, stop: threading.Event) -> None:
    """One full red/white racer + theater sequence (original Arduino loop body)."""
    if stop.is_set():
        return
    strip.fill(RED)
    strip.show()

    for _ in range(3):
        _racer_chase(strip, WHITE, RED, stop)
        if stop.is_set():
            return
    _theater_chase(strip, WHITE, 50, stop)
    if stop.is_set():
        return

    for _ in range(3):
        _racer_chase(strip, RED, WHITE, stop)
        if stop.is_set():
            return
    _theater_chase(strip, RED, 50, stop)


def _animate_loop(stop: threading.Event) -> None:
    if _strip is None:
        return
    try:
        while not stop.is_set():
            _win_animation_cycle(_strip, stop)
    except Exception as e:  # noqa: BLE001
        log.warning("NeoPixel animation thread crashed: %s", e)
    finally:
        _fill_blocking(OFF)


def _start_anim_thread(name: str) -> None:
    global _anim_thread
    _anim_stop.clear()
    t = threading.Thread(target=_animate_loop, args=(_anim_stop,), name=name, daemon=True)
    _anim_thread = t
    t.start()


def init_gpio() -> None:
    """Build NeoPixel strip + mute button on first use."""
    global _strip, _initialized, _led_count
    _init_mute_button()
    with _lock:
        if _initialized:
            return
        board, neopixel = _import_neopixel()
        if board is None or neopixel is None:
            log.debug("board/neopixel not installed; win LED disabled")
            return
        _led_count = _WIN_LED_COUNT
        try:
            pin = _board_pin(board, _WIN_LED_GPIO)
            _strip = neopixel.NeoPixel(
                pin,
                _led_count,
                brightness=_WIN_LED_BRIGHTNESS / 255.0,
                auto_write=False,
                pixel_order=neopixel.GRB,
            )
            _initialized = True
            if _LED_DEBUG:
                print(
                    f"[biga] LED DEBUG: lighting {_led_count} LEDs solid on GPIO "
                    f"{_WIN_LED_GPIO}. Tune BIGA_WIN_LED_COUNT to your strip length.",
                    flush=True,
                )
                _fill_blocking(_LED_DEBUG_COLOR)
            elif _LED_WIN_DEBUG:
                print(
                    f"[biga] LED WIN DEBUG: racer/theater animation on {_led_count} LEDs "
                    f"(GPIO {_WIN_LED_GPIO}). Ctrl-C to exit.",
                    flush=True,
                )
                _start_anim_thread("win-leds-debug")
            else:
                _fill_blocking(OFF)
        except Exception as e:  # noqa: BLE001
            log.warning("NeoPixel init failed (GPIO %s): %s", _WIN_LED_GPIO, e)
            _strip = None


def set_win_led(active: bool) -> None:
    """Start or stop the win animation (ignored in LED debug modes)."""
    global _win_active, _anim_thread
    if _LED_DEBUG or _LED_WIN_DEBUG:
        if not _initialized:
            init_gpio()
        return
    with _lock:
        if active == _win_active:
            return
        if not _initialized:
            init_gpio()
        if _strip is None:
            _win_active = active
            return
        if active:
            _start_anim_thread("win-leds")
        else:
            _anim_stop.set()
            t = _anim_thread
            _anim_thread = None
            if t is not None:
                t.join(timeout=2.0)
            _fill_blocking(OFF)
        _win_active = active


def cleanup_gpio() -> None:
    """Stop animation, blank strip, release GPIO."""
    global _strip, _initialized, _win_active, _mute_button
    with _lock:
        _anim_stop.set()
    t = _anim_thread
    if t is not None:
        t.join(timeout=2.0)
    with _lock:
        _fill_blocking(OFF)
        _strip = None
        _initialized = False
        _win_active = False
    btn = _mute_button
    if btn is not None:
        try:
            btn.close()
        except Exception:
            pass
        _mute_button = None


def main() -> None:
    """Standalone entry — same as BIGA_LED_WIN_DEBUG=1."""
    os.environ.setdefault("BIGA_LED_WIN_DEBUG", "1")
    global _LED_WIN_DEBUG
    _LED_WIN_DEBUG = True
    init_gpio()
    if not _initialized:
        print("NeoPixel unavailable (need Pi + sudo + adafruit-blinka + neopixel)", flush=True)
        return
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        cleanup_gpio()


if __name__ == "__main__":
    main()
