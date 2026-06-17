"""
Kiosk mouse suppression — the panel never needs a pointer.

Uses SDL_ShowCursor(SDL_DISABLE) at the libSDL level (survives some pygame
re-inits better than pygame.mouse alone), plus pygame hide/grab on headless Linux.
Call ``apply()`` every frame on the Pi so mpv/SDL DRM handoffs cannot flash it.
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


def _sdl_hide_cursor() -> bool:
    """SDL_ShowCursor(SDL_DISABLE) on the loaded SDL2 library."""
    lib = None
    if hasattr(pygame, "dlllib"):
        lib = getattr(pygame.dlllib, "SDL2", None)
    if lib is None:
        try:
            from pygame import dlllib as _dlllib  # noqa: PLC0415

            lib = getattr(_dlllib, "SDL2", None)
        except ImportError:
            return False
    if lib is None:
        return False
    try:
        lib.SDL_ShowCursor(SDL_DISABLE)
        return True
    except (AttributeError, OSError, ValueError):
        return False


def apply(screen: pygame.Surface | None = None) -> None:
    """Hide the pointer at SDL + pygame level; grab input on kiosk."""
    _sdl_hide_cursor()
    try:
        pygame.mouse.set_visible(False)
        if kiosk_mode():
            pygame.event.set_grab(True)
    except pygame.error:
        pass
    if screen is not None:
        try:
            pygame.event.pump()
        except pygame.error:
            pass
        _sdl_hide_cursor()
        try:
            pygame.mouse.set_visible(False)
        except pygame.error:
            pass

