"""
Win-scene NeoPixel strip (default BCM 19 / PWM channel 1).

Public API kept stable for ``app.py``:
    init_gpio() / set_win_led(active) / cleanup_gpio()

When ``set_win_led(True)`` is called, a daemon thread drives a slow Angels-red
breathing pulse on the strip. ``set_win_led(False)`` stops the animation and
clears all pixels. The thread stays inactive until the next win.

The LEDs follow ``scene == "win"``; ``game_day_poller`` keeps that scene set
until the next local calendar day (per ``date.today()``, system timezone) or
until a doubleheader game 2 transitions back to ``live`` — both cases turn the
LEDs off automatically because the scene leaves ``win``.

Hardware notes (rpi_ws281x):
* GPIO 19 uses PWM channel 1 (``dma=10``). Requires root (the BigA service is
  already root). Conflicts with on-board audio when channel 1 is active.
* Override pin via ``BIGA_WIN_LED_GPIO`` (must be a PWM/PCM-capable pin).
* Override LED count via ``BIGA_WIN_LED_COUNT`` (default 16).
* Override brightness via ``BIGA_WIN_LED_BRIGHTNESS`` (0–255, default 96).

No-op when ``rpi_ws281x`` is not installed (e.g. local Mac dev).
"""

from __future__ import annotations

import logging
import math
import os
import threading
import time

log = logging.getLogger(__name__)


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
_WIN_LED_COUNT = _env_int("BIGA_WIN_LED_COUNT", 16, lo=1, hi=600)
_WIN_LED_BRIGHTNESS = _env_int("BIGA_WIN_LED_BRIGHTNESS", 96, lo=0, hi=255)
# rpi_ws281x defaults that are safe for WS2812B-style strips.
_LED_FREQ_HZ = 800_000
_LED_INVERT = False
# Channel 0: GPIO 12/18/21. Channel 1: GPIO 13/19. DMA channel 10 for ch 1.
_CHANNEL_FOR_GPIO = {12: 0, 18: 0, 21: 0, 13: 1, 19: 1}
_DMA_FOR_CHANNEL = {0: 10, 1: 10}

# Angels red (BGR-order Color constructor takes RGB ints; library handles strip order).
_HALOS_R = (190, 30, 30)
_HALOS_GOLD = (186, 147, 62)

_lock = threading.Lock()
_strip = None  # type: ignore[var-annotated]
_initialized = False
_win_active = False
_anim_thread: threading.Thread | None = None
_anim_stop = threading.Event()


def _import_neopixel():
    """Return (PixelStrip, Color) or (None, None) when rpi_ws281x is unavailable."""
    try:
        from rpi_ws281x import Color, PixelStrip  # type: ignore[import-untyped]

        return PixelStrip, Color
    except ImportError:
        return None, None


def _color(rgb: tuple[int, int, int]):
    _, Color = _import_neopixel()
    if Color is None:
        return None
    r, g, b = rgb
    return Color(int(r) & 0xFF, int(g) & 0xFF, int(b) & 0xFF)


def init_gpio() -> None:
    """Build the PixelStrip on first use. Safe to call repeatedly (no-op when ready)."""
    global _strip, _initialized
    with _lock:
        if _initialized:
            return
        PixelStrip, _ = _import_neopixel()
        if PixelStrip is None:
            log.debug("rpi_ws281x not installed; win LED disabled")
            return
        channel = _CHANNEL_FOR_GPIO.get(_WIN_LED_GPIO)
        if channel is None:
            log.warning(
                "BIGA_WIN_LED_GPIO=%s is not a NeoPixel-capable pin (use 12/13/18/19/21); "
                "LEDs disabled.",
                _WIN_LED_GPIO,
            )
            return
        dma = _DMA_FOR_CHANNEL.get(channel, 10)
        try:
            strip = PixelStrip(
                _WIN_LED_COUNT,
                _WIN_LED_GPIO,
                _LED_FREQ_HZ,
                dma,
                _LED_INVERT,
                _WIN_LED_BRIGHTNESS,
                channel,
            )
            strip.begin()
            _strip = strip
            _initialized = True
            _fill_blocking((0, 0, 0))
        except Exception as e:  # noqa: BLE001
            log.warning("NeoPixel init failed (pin %s ch %s): %s", _WIN_LED_GPIO, channel, e)


def _fill_blocking(rgb: tuple[int, int, int]) -> None:
    """Paint all pixels a solid color and ``show()``. Caller holds no lock requirement."""
    if _strip is None:
        return
    c = _color(rgb)
    if c is None:
        return
    try:
        for i in range(_strip.numPixels()):
            _strip.setPixelColor(i, c)
        _strip.show()
    except Exception as e:  # noqa: BLE001
        log.debug("NeoPixel fill failed: %s", e)


def _scale(rgb: tuple[int, int, int], factor: float) -> tuple[int, int, int]:
    factor = max(0.0, min(1.0, factor))
    return (
        int(round(rgb[0] * factor)),
        int(round(rgb[1] * factor)),
        int(round(rgb[2] * factor)),
    )


def _animate_loop(stop: threading.Event) -> None:
    """Slow breathing pulse in Angels red with brief gold flashes every few seconds."""
    if _strip is None:
        return
    t0 = time.monotonic()
    last_flash = 0.0
    flash_period = 6.0
    flash_dur = 0.18
    try:
        while not stop.is_set():
            now = time.monotonic()
            t = now - t0
            # Breathing curve: 0.20 .. 1.00 over a ~3.2s period.
            phase = (math.sin(t * 2 * math.pi / 3.2) + 1.0) / 2.0
            level = 0.20 + 0.80 * phase

            if now - last_flash >= flash_period and (now - last_flash) - flash_period < flash_dur:
                color = _scale(_HALOS_GOLD, 1.0)
            else:
                color = _scale(_HALOS_R, level)
                if now - last_flash >= flash_period + flash_dur:
                    last_flash = now

            _fill_blocking(color)
            if stop.wait(0.04):
                break
    except Exception as e:  # noqa: BLE001
        log.warning("NeoPixel animation thread crashed: %s", e)
    finally:
        _fill_blocking((0, 0, 0))


def set_win_led(active: bool) -> None:
    """Start (active=True) or stop (active=False) the win animation."""
    global _win_active, _anim_thread
    with _lock:
        if active == _win_active:
            return
        if not _initialized:
            init_gpio()
        if _strip is None:
            _win_active = active
            return
        if active:
            _anim_stop.clear()
            t = threading.Thread(
                target=_animate_loop,
                args=(_anim_stop,),
                name="win-leds",
                daemon=True,
            )
            _anim_thread = t
            t.start()
        else:
            _anim_stop.set()
            t = _anim_thread
            _anim_thread = None
            if t is not None:
                t.join(timeout=1.0)
            _fill_blocking((0, 0, 0))
        _win_active = active


def cleanup_gpio() -> None:
    """Stop animation, blank the strip, drop the strip handle."""
    global _strip, _initialized, _win_active
    with _lock:
        _anim_stop.set()
    t = _anim_thread
    if t is not None:
        t.join(timeout=1.0)
    with _lock:
        if _strip is not None:
            try:
                _fill_blocking((0, 0, 0))
            except Exception as e:  # noqa: BLE001
                log.debug("NeoPixel cleanup fill: %s", e)
        _strip = None
        _initialized = False
        _win_active = False
