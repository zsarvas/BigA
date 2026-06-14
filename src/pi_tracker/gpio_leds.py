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
* Override LED count via ``BIGA_WIN_LED_COUNT`` (default 32).
* Override brightness via ``BIGA_WIN_LED_BRIGHTNESS`` (0–255, default 96).

No-op when ``rpi_ws281x`` is not installed (e.g. local Mac dev).
"""

from __future__ import annotations

import logging
import os
import threading
import time

log = logging.getLogger(__name__)


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
_WIN_LED_BRIGHTNESS = _env_int("BIGA_WIN_LED_BRIGHTNESS", 96, lo=0, hi=255)
# Debug: light every configured LED solid white at all times (ignores scene).
# Use to verify wiring / strip length, then unset. BIGA_LED_DEBUG=1 enables.
_LED_DEBUG = _env_bool("BIGA_LED_DEBUG")
_LED_DEBUG_COLOR = (255, 255, 255)
# Debug: run the full win animation (breathing red + gold flashes) from startup,
# regardless of scene. Use to tune the animation. BIGA_LED_WIN_DEBUG=1 enables.
_LED_WIN_DEBUG = _env_bool("BIGA_LED_WIN_DEBUG")
# rpi_ws281x defaults that are safe for WS2812B-style strips.
_LED_FREQ_HZ = 800_000
_LED_INVERT = False
# Channel 0: GPIO 12/18/21. Channel 1: GPIO 13/19. DMA channel 10 for ch 1.
_CHANNEL_FOR_GPIO = {12: 0, 18: 0, 21: 0, 13: 1, 19: 1}
_DMA_FOR_CHANNEL = {0: 10, 1: 10}

# Angels red / gold palette
_HALOS_R = (190, 30, 30)
_HALOS_GOLD = (186, 147, 62)

# Animation timing (seconds)
_PHASE_RED_DUR = 4.0      # red comet chase
_PHASE_WHITE_DUR = 4.0    # white comet chase
_PHASE_RAINBOW_DUR = 6.0  # rainbow scroll
_CHASE_SPEED = 20         # pixels / second for comet phases
_FLASH_INTERVAL = 7.0     # seconds between full-strip white flashes
_FLASH_DUR = 0.13         # seconds a flash lasts

# Reentrant: set_win_led() holds the lock and may call init_gpio(), which re-acquires it.
_lock = threading.RLock()
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
            if _LED_DEBUG:
                # Solid-on diagnostic: confirms how many physical LEDs the configured
                # count actually drives. Bump BIGA_WIN_LED_COUNT until the whole strip lights.
                print(
                    f"[biga] LED DEBUG: lighting {_WIN_LED_COUNT} LEDs solid on "
                    f"GPIO {_WIN_LED_GPIO} (ch {channel}). Set BIGA_WIN_LED_COUNT to your "
                    "strip length.",
                    flush=True,
                )
                _fill_blocking(_LED_DEBUG_COLOR)
            elif _LED_WIN_DEBUG:
                # Win-animation debug: starts the breathing/flash animation immediately so
                # you can tune colors and timing without waiting for an actual win.
                print(
                    f"[biga] LED WIN DEBUG: running win animation on {_WIN_LED_COUNT} LEDs "
                    f"(GPIO {_WIN_LED_GPIO}). Ctrl-C or stop the service to exit.",
                    flush=True,
                )
                _anim_stop.clear()
                t = threading.Thread(
                    target=_animate_loop,
                    args=(_anim_stop,),
                    name="win-leds-debug",
                    daemon=True,
                )
                _anim_thread = t
                t.start()
            else:
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


def _wheel(pos: int) -> tuple[int, int, int]:
    """Classic 256-step color wheel: red → green → blue → red."""
    pos = pos % 256
    if pos < 85:
        return (255 - pos * 3, pos * 3, 0)
    if pos < 170:
        pos -= 85
        return (0, 255 - pos * 3, pos * 3)
    pos -= 170
    return (pos * 3, 0, 255 - pos * 3)


def _render_comet(n: int, head: int, tail_len: int, rgb: tuple[int, int, int]) -> None:
    """Paint a comet with a fading tail; head is the brightest pixel."""
    if _strip is None:
        return
    _, Color = _import_neopixel()
    if Color is None:
        return
    blank = Color(0, 0, 0)
    for i in range(n):
        dist = (head - i) % n
        if dist < tail_len:
            factor = (tail_len - dist) / tail_len
            r, g, b = rgb
            _strip.setPixelColor(i, Color(int(r * factor), int(g * factor), int(b * factor)))
        else:
            _strip.setPixelColor(i, blank)
    _strip.show()


def _animate_loop(stop: threading.Event) -> None:
    """
    Scrolling chase animation: red comet → white comet → rainbow scroll, cycling
    continuously with brief full-strip white flashes for excitement.
    """
    if _strip is None:
        return
    n = _strip.numPixels()
    tail_len = max(4, n // 4)
    cycle_dur = _PHASE_RED_DUR + _PHASE_WHITE_DUR + _PHASE_RAINBOW_DUR

    _, Color = _import_neopixel()
    if Color is None:
        return

    t0 = time.monotonic()
    last_flash = t0 - _FLASH_INTERVAL  # don't flash immediately on start

    try:
        while not stop.is_set():
            now = time.monotonic()

            # Full-strip white flash overrides everything.
            if now - last_flash >= _FLASH_INTERVAL:
                last_flash = now
            if now - last_flash < _FLASH_DUR:
                _fill_blocking((255, 255, 255))
                if stop.wait(0.02):
                    break
                continue

            t = now - t0
            cycle_t = t % cycle_dur

            if cycle_t < _PHASE_RED_DUR:
                # Red comet chase
                head = int(cycle_t * _CHASE_SPEED) % n
                _render_comet(n, head, tail_len, _HALOS_R)

            elif cycle_t < _PHASE_RED_DUR + _PHASE_WHITE_DUR:
                # White comet chase
                phase_t = cycle_t - _PHASE_RED_DUR
                head = int(phase_t * _CHASE_SPEED) % n
                _render_comet(n, head, tail_len, (255, 255, 255))

            else:
                # Rainbow scroll
                phase_t = cycle_t - _PHASE_RED_DUR - _PHASE_WHITE_DUR
                offset = int((phase_t / _PHASE_RAINBOW_DUR) * 256)
                for i in range(n):
                    hue = (i * 256 // n + offset) % 256
                    r, g, b = _wheel(hue)
                    _strip.setPixelColor(i, Color(r, g, b))
                _strip.show()

            if stop.wait(0.04):
                break
    except Exception as e:  # noqa: BLE001
        log.warning("NeoPixel animation thread crashed: %s", e)
    finally:
        _fill_blocking((0, 0, 0))


def set_win_led(active: bool) -> None:
    """Start (active=True) or stop (active=False) the win animation."""
    global _win_active, _anim_thread
    if _LED_DEBUG or _LED_WIN_DEBUG:
        # Debug modes manage the strip themselves; ignore scene-driven calls.
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
