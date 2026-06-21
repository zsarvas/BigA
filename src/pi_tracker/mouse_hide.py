"""
Kiosk mouse suppression — the panel never needs a pointer.

Uses SDL_ShowCursor(SDL_DISABLE) at the libSDL level (survives some pygame
re-inits better than pygame.mouse alone), plus pygame hide/grab on headless Linux.
Call ``apply()`` every frame on the Pi so mpv/SDL DRM handoffs cannot flash it.

Note: SDL_SetRelativeMouseMode and set_cursor before a window exists segfault on
Pi KMSDRM — only run pygame mouse calls when the display surface is ready.
"""

from __future__ import annotations

import os
import sys

import pygame

SDL_DISABLE = 0


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


def _display_ready() -> bool:
    try:
        return pygame.display.get_init() and pygame.display.get_surface() is not None
    except pygame.error:
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
    """
    Re-hide after mpv releases DRM/KMS — a few black flips without unsafe SDL calls.
    """
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
