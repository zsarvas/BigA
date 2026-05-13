"""Set SDL environment variables before ``import pygame`` (import side effects load SDL)."""

from __future__ import annotations

import os
import sys


def configure_sdl() -> None:
    """
    Headless / text-VT Linux: default ``SDL_AUDIODRIVER`` to ``dummy`` so SDL_mixer
    does not probe ALSA (extra threads; some pygame/SDL2 + fbcon builds mis-handle GIL).

    Video driver is **not** forced here: SPI panels often need ``SDL_VIDEODRIVER=fbcon``
    (no KMSDRM). HDMI / DSI DRM on Bookworm can use KMSDRM explicitly::

        BIGA_SDL_VIDEO=kmsdrm

    That sets ``SDL_VIDEODRIVER=KMSDRM`` and clears ``SDL_FBDEV`` (not used by KMS).

    ``fbcon`` + pygame on Pi is a known source of ``PyEval_SaveThread`` crashes
    (see pygame#3687); prefer KMSDRM when your hardware supports it, or try a
    newer pygame / pygame-ce build when you must use fbcon.

    If ``DISPLAY`` or ``WAYLAND_DISPLAY`` is set (desktop session), only generic
    ``setdefault`` calls apply and your environment wins.
    """
    if not sys.platform.startswith("linux"):
        return
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        return

    mode = os.environ.get("BIGA_SDL_VIDEO", "").strip().lower()
    if mode == "kmsdrm":
        os.environ["SDL_VIDEODRIVER"] = "KMSDRM"
        os.environ.pop("SDL_FBDEV", None)
    elif mode == "fbcon":
        os.environ["SDL_VIDEODRIVER"] = "fbcon"
        os.environ.setdefault("SDL_FBDEV", "/dev/fb0")
