"""
Kiosk mouse suppression — the panel never needs a pointer.

Uses SDL_ShowCursor(SDL_DISABLE), relative mouse mode, and a blank cursor pixmap
at the libSDL level (survives some pygame re-inits better than pygame.mouse alone).
Call ``apply()`` every frame on the Pi; use ``handoff_from_mpv()`` after mpv exits.
"""

from __future__ import annotations

import os
import sys

import pygame

SDL_DISABLE = 0
SDL_ENABLE = 1
_blank_cursor_set = False


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


def _sdl_relative_mouse(on: bool) -> bool:
    """SDL_SetRelativeMouseMode — hides the system pointer while active."""
    lib = _sdl2()
    if lib is None:
        return False
    try:
        lib.SDL_SetRelativeMouseMode(SDL_ENABLE if on else SDL_DISABLE)
        return True
    except (AttributeError, OSError, ValueError):
        return False


def _set_blank_cursor() -> None:
    """Replace the visible cursor bitmap with a fully transparent 8×8 sprite."""
    global _blank_cursor_set
    try:
        surf = pygame.Surface((8, 8), pygame.SRCALPHA, 32)
        surf.fill((0, 0, 0, 0))
        pygame.mouse.set_cursor(pygame.cursors.Cursor((0, 0), surf))
        _blank_cursor_set = True
    except (pygame.error, TypeError, ValueError):
        pass


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


def apply(screen: pygame.Surface | None = None) -> None:
    """Hide the pointer at SDL + pygame level; grab input on kiosk."""
    if kiosk_mode():
        _hide_linux_vt_cursor()
        _sdl_relative_mouse(True)

    _sdl_hide_cursor()
    _set_blank_cursor()
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
        _set_blank_cursor()
        try:
            pygame.mouse.set_visible(False)
        except pygame.error:
            pass


def handoff_from_mpv(screen: pygame.Surface, *, fill: tuple[int, int, int] = (0, 0, 0)) -> None:
    """
    Aggressive hide after mpv releases DRM/KMS.

    mpv often restores the hardware cursor for one frame when pygame reclaims the
    display — pump several black flips with repeated SDL hide calls.
    """
    for _ in range(6):
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
