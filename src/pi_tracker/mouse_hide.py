"""
Kiosk mouse suppression — the panel never needs a pointer.

Uses SDL_ShowCursor(SDL_DISABLE) at the libSDL level (survives some pygame
re-inits better than pygame.mouse alone), plus pygame hide/grab on headless Linux.
``hide_cursor_hard()`` swaps in a 1×1 invisible SDL cursor on the DRM plane
(after ``set_mode`` only — never before a window exists).

Call ``apply()`` every frame on the Pi so mpv/SDL DRM handoffs cannot flash it.
Re-call ``hide_cursor_hard()`` after mpv exits (``handoff_from_mpv``).

Set ``BIGA_SKIP_COLOR_CURSOR=1`` if ``SDL_CreateColorCursor`` segfaults on your Pi
(``SDL_ShowCursor(0)`` still runs).
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys

import pygame

log = logging.getLogger(__name__)

SDL_DISABLE = 0

_sdl2_ctypes: ctypes.CDLL | None | bool = None


def kiosk_mode() -> bool:
    """Headless Pi panel (no X11/Wayland) or explicit BIGA_KIOSK=1."""
    if os.environ.get("BIGA_KIOSK", "").strip().lower() in ("1", "true", "yes"):
        return True
    return sys.platform.startswith("linux") and not (
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    )


def _sdl2():
    if hasattr(pygame, "dlllib"):
        lib = getattr(pygame.dlllib, "SDL2", None)
        if lib is not None:
            return lib
    try:
        from pygame import dlllib as _dlllib  # noqa: PLC0415

        return getattr(_dlllib, "SDL2", None)
    except ImportError:
        return None


def _configure_sdl2_ctypes(lib: ctypes.CDLL) -> None:
    lib.SDL_ShowCursor.argtypes = [ctypes.c_int]
    lib.SDL_ShowCursor.restype = ctypes.c_int
    lib.SDL_CreateRGBSurface.argtypes = [
        ctypes.c_uint32,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_uint32,
    ]
    lib.SDL_CreateRGBSurface.restype = ctypes.c_void_p
    lib.SDL_CreateColorCursor.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    lib.SDL_CreateColorCursor.restype = ctypes.c_void_p
    lib.SDL_SetCursor.argtypes = [ctypes.c_void_p]
    lib.SDL_SetCursor.restype = None
    lib.SDL_FreeSurface.argtypes = [ctypes.c_void_p]
    lib.SDL_FreeSurface.restype = None


def _sdl2_ctypes_lib() -> ctypes.CDLL | None:
    """Loaded libSDL2 for ctypes calls (same .so pygame uses when possible)."""
    global _sdl2_ctypes
    if _sdl2_ctypes is False:
        return None
    if isinstance(_sdl2_ctypes, ctypes.CDLL):
        return _sdl2_ctypes

    pg = _sdl2()
    if pg is not None:
        try:
            _configure_sdl2_ctypes(pg)
            _sdl2_ctypes = pg
            return pg
        except (AttributeError, OSError, TypeError, ValueError):
            pass

    for name in ("libSDL2-2.0.so.0", "libSDL2-2.0.so", "SDL2"):
        try:
            lib = ctypes.CDLL(name)
            _configure_sdl2_ctypes(lib)
            _sdl2_ctypes = lib
            return lib
        except OSError:
            continue

    _sdl2_ctypes = False
    return None


def _display_ready() -> bool:
    try:
        return pygame.display.get_init() and pygame.display.get_surface() is not None
    except pygame.error:
        return False


def hide_cursor_hard() -> bool:
    """
    Hide the SDL/DRM hardware cursor plane via ctypes.

    Only safe **after** ``pygame.display.set_mode`` — do not call at bootstrap.
    """
    if not _display_ready():
        return False

    lib = _sdl2_ctypes_lib()
    if lib is None:
        return False

    try:
        lib.SDL_ShowCursor(SDL_DISABLE)
    except (AttributeError, OSError, ValueError) as exc:
        log.debug("hide_cursor_hard: SDL_ShowCursor failed: %s", exc)
        return False

    skip_color = os.environ.get("BIGA_SKIP_COLOR_CURSOR", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    if not skip_color:
        try:
            surface = lib.SDL_CreateRGBSurface(0, 1, 1, 32, 0, 0, 0, 0)
            if surface:
                cursor = lib.SDL_CreateColorCursor(surface, 0, 0)
                if cursor:
                    lib.SDL_SetCursor(cursor)
                lib.SDL_FreeSurface(surface)
        except (AttributeError, OSError, ValueError) as exc:
            log.debug("hide_cursor_hard: blank cursor pixmap failed: %s", exc)

    try:
        lib.SDL_ShowCursor(SDL_DISABLE)
        return True
    except (AttributeError, OSError, ValueError) as exc:
        log.debug("hide_cursor_hard: final SDL_ShowCursor failed: %s", exc)
        return False


def _sdl_hide_cursor() -> bool:
    """SDL_ShowCursor(SDL_DISABLE) on the loaded SDL2 library."""
    lib = _sdl2()
    if lib is None:
        return False
    try:
        lib.SDL_ShowCursor(SDL_DISABLE)
        return True
    except (AttributeError, OSError, ValueError):
        return False


def _hide_linux_vt_cursor() -> None:
    """Hide the text-mode VT cursor (openvt tty2); harmless if it fails."""
    if not sys.platform.startswith("linux"):
        return
    for path in ("/dev/tty2", "/dev/tty", "/dev/console"):
        try:
            with open(path, "w", encoding="utf-8") as tty:
                tty.write("\033[?25l")
            return
        except OSError:
            continue


def _pygame_hide_mouse() -> None:
    """Pygame-level hide — only when a display surface exists."""
    if not _display_ready():
        return
    try:
        pygame.mouse.set_visible(False)
        if kiosk_mode():
            pygame.event.set_grab(True)
    except pygame.error:
        pass


def apply(screen: pygame.Surface | None = None) -> None:
    """Hide the pointer at SDL + pygame level; grab input on kiosk."""
    if kiosk_mode():
        _hide_linux_vt_cursor()

    _sdl_hide_cursor()
    if screen is not None or _display_ready():
        _pygame_hide_mouse()

    if screen is not None:
        try:
            pygame.event.pump()
        except pygame.error:
            pass
        _sdl_hide_cursor()
        _pygame_hide_mouse()


def handoff_from_mpv(screen: pygame.Surface, *, fill: tuple[int, int, int] = (0, 0, 0)) -> None:
    """Re-hide after mpv releases DRM/KMS — mpv resets the cursor plane on exit."""
    try:
        pygame.mouse.set_visible(False)
    except pygame.error:
        pass
    hide_cursor_hard()
    for _ in range(3):
        apply(screen)
        try:
            screen.fill(fill)
            pygame.display.flip()
        except pygame.error:
            break
        try:
            pygame.time.wait(1)
        except pygame.error:
            pass
    apply(screen)
