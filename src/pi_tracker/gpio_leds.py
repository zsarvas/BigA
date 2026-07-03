"""
Win-scene NeoPixel animation (racer + theater chase).

Uses ``rpi_ws281x`` (``requirements-pi.txt``). Public API for ``app.py``:
    init_gpio() / set_win_led(active) / cleanup_gpio()
    is_muted() / set_muted(bool)

Animation: solid red → white racer ×3 → white theater → red racer ×3 → red theater → repeat.

Test **LEDs only**:

    sudo systemctl stop biga
    sudo BIGA_LED_WIN_DEBUG=1 PYTHONPATH=src python3 -m pi_tracker.gpio_leds

Test **wiring** (solid white):

    sudo BIGA_LED_DEBUG=1 PYTHONPATH=src python3 -m pi_tracker.gpio_leds

Test **win screen + LEDs**:

    sudo openvt -c 2 -f -w -- python3 run_pi_ui.py --demo-final

Previous comet/rainbow animation: ``gpio_leds_old.py``.
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
# Config
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

_LED_FREQ_HZ = 800_000
_LED_INVERT = False
_CHANNEL_FOR_GPIO = {12: 0, 18: 0, 21: 0, 13: 1, 19: 1}
_DMA_FOR_CHANNEL = {0: 10, 1: 10}

_RACER_LENGTH = 3
_WAIT_SEC = 0.030

RED = (255, 0, 0)
WHITE = (255, 255, 255)
GOLD = (196, 168, 79)
OFF = (0, 0, 0)

_lock = threading.RLock()
_strip: Any = None
_view: "_StripView | None" = None
_led_count = _WIN_LED_COUNT
_initialized = False
_win_active = False
_anim_thread: threading.Thread | None = None
_anim_stop = threading.Event()
_last_init_error: str = ""

# Reset-button hold progress (biga-reset service; exclusive with win animation).
_reset_hold_sec = 0.0
_reset_hold_lock = threading.Lock()
_reset_feedback_stop = threading.Event()
_reset_feedback_thread: threading.Thread | None = None
_RESET_SOFT_SEC = _env_int("BIGA_RESET_HOLD_SOFT", 5, lo=1, hi=60)
_RESET_FULL_SEC = _env_int("BIGA_RESET_HOLD_FULL", 10, lo=2, hi=120)
if _RESET_FULL_SEC <= _RESET_SOFT_SEC:
    _RESET_FULL_SEC = _RESET_SOFT_SEC + 5

# ---------------------------------------------------------------------------
# Mute button
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
# rpi_ws281x strip helpers
# ---------------------------------------------------------------------------


def _import_ws281x():
    try:
        from rpi_ws281x import Color, PixelStrip  # type: ignore[import-untyped]

        return PixelStrip, Color
    except ImportError:
        return None, None


def _color(rgb: tuple[int, int, int]):
    _, Color = _import_ws281x()
    if Color is None:
        return None
    r, g, b = rgb
    return Color(int(r) & 0xFF, int(g) & 0xFF, int(b) & 0xFF)


class _StripView:
    """Thin wrapper over PixelStrip for the racer/theater animation."""

    __slots__ = ("_strip", "_n")

    def __init__(self, strip: Any, count: int) -> None:
        self._strip = strip
        self._n = count

    def show(self) -> None:
        self._strip.show()

    def fill(self, rgb: tuple[int, int, int]) -> None:
        c = _color(rgb)
        if c is None:
            return
        for i in range(self._n):
            self._strip.setPixelColor(i, c)

    def __setitem__(self, index: int, rgb: tuple[int, int, int]) -> None:
        c = _color(rgb)
        if c is not None:
            self._strip.setPixelColor(index, c)


def _fill_blocking(rgb: tuple[int, int, int]) -> None:
    if _view is None:
        return
    try:
        _view.fill(rgb)
        _view.show()
    except Exception as e:  # noqa: BLE001
        log.debug("NeoPixel fill failed: %s", e)


def _racer_chase(
    strip: _StripView,
    color1: tuple[int, int, int],
    color2: tuple[int, int, int],
    stop: threading.Event,
) -> None:
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


def _theater_chase(
    strip: _StripView,
    color: tuple[int, int, int],
    wait_ms: int,
    stop: threading.Event,
) -> None:
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


def _win_animation_cycle(strip: _StripView, stop: threading.Event) -> None:
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
    if _view is None:
        return
    try:
        while not stop.is_set():
            _win_animation_cycle(_view, stop)
    except Exception as e:  # noqa: BLE001
        log.warning("NeoPixel animation thread crashed: %s", e)
    finally:
        _fill_blocking(OFF)


def _start_anim_thread(name: str) -> None:
    global _anim_thread
    _stop_reset_hold_feedback()
    _anim_stop.clear()
    t = threading.Thread(target=_animate_loop, args=(_anim_stop,), name=name, daemon=True)
    _anim_thread = t
    t.start()


def _stop_win_animation() -> None:
    """Stop the win-scene thread without tearing down the strip."""
    global _anim_thread, _win_active
    _anim_stop.set()
    t = _anim_thread
    _anim_thread = None
    if t is not None:
        t.join(timeout=2.0)
    _fill_blocking(OFF)
    _win_active = False


def _reset_feedback_loop(stop: threading.Event) -> None:
    """
    Blink the ring while the reset button is held.

    0–soft: slow gold pulse (WiFi reset coming).
    soft–full: fast gold/red alternation (release for WiFi, keep holding for full).
    ≥ full: solid red pulse (full reset firing).
    """
    while not stop.is_set():
        with _reset_hold_lock:
            held = _reset_hold_sec
        if held >= _RESET_FULL_SEC:
            on = int(time.monotonic() * 8) % 2 == 0
            _fill_blocking(RED if on else OFF)
            stop.wait(0.08)
            continue
        if held >= _RESET_SOFT_SEC:
            period = 0.14
            on = int(time.monotonic() / period) % 2 == 0
            color = GOLD if int(held * 6) % 2 == 0 else RED
            _fill_blocking(color if on else OFF)
            stop.wait(period / 2)
            continue
        period = 0.45
        on = int(time.monotonic() / period) % 2 == 0
        _fill_blocking(GOLD if on else OFF)
        stop.wait(period / 2)


def set_reset_hold_seconds(held: float) -> None:
    """Update hold duration for the reset-button feedback thread."""
    with _reset_hold_lock:
        global _reset_hold_sec
        _reset_hold_sec = max(0.0, held)


def begin_reset_hold_feedback() -> None:
    """Take over the NeoPixel ring for reset-button progress (stops win anim)."""
    global _reset_feedback_thread
    if _LED_DEBUG or _LED_WIN_DEBUG:
        return
    with _lock:
        _stop_win_animation()
        if not _initialized:
            init_gpio()
        if _view is None:
            return
        _stop_reset_hold_feedback()
        set_reset_hold_seconds(0.0)
        _reset_feedback_stop.clear()
        t = threading.Thread(
            target=_reset_feedback_loop,
            args=(_reset_feedback_stop,),
            name="reset-hold-leds",
            daemon=True,
        )
        _reset_feedback_thread = t
        t.start()


def _stop_reset_hold_feedback() -> None:
    global _reset_feedback_thread
    _reset_feedback_stop.set()
    t = _reset_feedback_thread
    _reset_feedback_thread = None
    if t is not None:
        t.join(timeout=1.0)
    set_reset_hold_seconds(0.0)


def end_reset_hold_feedback() -> None:
    """Turn off reset-hold feedback and release the strip."""
    with _lock:
        _stop_reset_hold_feedback()
        _fill_blocking(OFF)


def init_gpio() -> None:
    """Build PixelStrip + mute button on first use."""
    global _strip, _view, _initialized, _led_count, _last_init_error
    _init_mute_button()
    with _lock:
        if _initialized:
            return
        _last_init_error = ""
        PixelStrip, _ = _import_ws281x()
        if PixelStrip is None:
            _last_init_error = "rpi_ws281x not installed"
            log.debug("%s; win LED disabled", _last_init_error)
            return
        channel = _CHANNEL_FOR_GPIO.get(_WIN_LED_GPIO)
        if channel is None:
            _last_init_error = (
                f"BIGA_WIN_LED_GPIO={_WIN_LED_GPIO} is not NeoPixel-capable "
                "(use 12/13/18/19/21)"
            )
            log.warning(_last_init_error)
            return
        dma = _DMA_FOR_CHANNEL.get(channel, 10)
        _led_count = _WIN_LED_COUNT
        try:
            strip = PixelStrip(
                _led_count,
                _WIN_LED_GPIO,
                _LED_FREQ_HZ,
                dma,
                _LED_INVERT,
                _WIN_LED_BRIGHTNESS,
                channel,
            )
            strip.begin()
            _strip = strip
            _view = _StripView(strip, _led_count)
            _initialized = True
            if _LED_DEBUG:
                print(
                    f"[biga] LED DEBUG: lighting {_led_count} LEDs on GPIO {_WIN_LED_GPIO}. "
                    "Tune BIGA_WIN_LED_COUNT to strip length.",
                    flush=True,
                )
                _fill_blocking(_LED_DEBUG_COLOR)
            elif _LED_WIN_DEBUG:
                print(
                    f"[biga] LED WIN DEBUG: racer/theater on {_led_count} LEDs "
                    f"(GPIO {_WIN_LED_GPIO}). Ctrl-C to exit.",
                    flush=True,
                )
                _start_anim_thread("win-leds-debug")
            else:
                _fill_blocking(OFF)
        except Exception as e:  # noqa: BLE001
            _last_init_error = str(e)
            log.warning(
                "NeoPixel init failed (GPIO %s ch %s): %s", _WIN_LED_GPIO, channel, e
            )
            _strip = None
            _view = None


def set_win_led(active: bool) -> None:
    if _LED_DEBUG or _LED_WIN_DEBUG:
        if not _initialized:
            init_gpio()
        return
    global _win_active, _anim_thread
    with _lock:
        if active == _win_active:
            return
        if not _initialized:
            init_gpio()
        if _view is None:
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
    global _strip, _view, _initialized, _win_active, _mute_button
    with _lock:
        _anim_stop.set()
    t = _anim_thread
    if t is not None:
        t.join(timeout=2.0)
    with _lock:
        _fill_blocking(OFF)
        _strip = None
        _view = None
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
    global _LED_WIN_DEBUG
    if not _LED_DEBUG:
        os.environ.setdefault("BIGA_LED_WIN_DEBUG", "1")
        _LED_WIN_DEBUG = True
    init_gpio()
    if not _initialized:
        print("NeoPixel init failed.", flush=True)
        if _last_init_error:
            print(f"  reason: {_last_init_error}", flush=True)
        print(
            "  • stop biga first — GPIO 19 is exclusive:\n"
            "      sudo systemctl stop biga\n"
            "  • rpi_ws281x: sudo pip3 install rpi_ws281x --break-system-packages",
            flush=True,
        )
        return
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        cleanup_gpio()


if __name__ == "__main__":
    main()
