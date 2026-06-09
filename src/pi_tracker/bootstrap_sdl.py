"""Set SDL environment variables before ``import pygame`` (import side effects load SDL)."""

from __future__ import annotations

import os
import sys
import tempfile


def configure_sdl() -> None:
    """
    Headless / text-VT Linux: default ``SDL_AUDIODRIVER`` to ``dummy`` so SDL_mixer
    does not probe ALSA (extra threads; some pygame/SDL2 builds mis-handle the GIL).

    Video driver (target = **Bookworm + KMS**):

    * Default backend is **KMSDRM** (``/dev/dri/card*``). The panel uses
      ``vc4-kms-dpi-generic``, so KMS is the correct path on Bookworm.
    * Override with ``BIGA_SDL_VIDEO``:
        - ``kmsdrm`` (default) → ``SDL_VIDEODRIVER=KMSDRM``, ``SDL_FBDEV`` cleared.
        - ``fbcon``           → legacy framebuffer (Bullseye / SPI panels).
        - any other value     → forced verbatim into ``SDL_VIDEODRIVER``.

    Legacy ``fbcon`` is still selectable but is a known source of pygame/SDL GIL
    crashes on the Pi (pygame#3687); prefer KMSDRM on Bookworm.

    If ``DISPLAY`` / ``WAYLAND_DISPLAY`` is set (desktop session), the driver is
    left to the environment and only ``setdefault`` calls apply.

    Headless Linux also sets a private ``XDG_RUNTIME_DIR`` under ``/tmp`` when unset
    (KMSDRM/SDL probe session paths) to avoid noisy log spam.
    """
    if not sys.platform.startswith("linux"):
        return
    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        return

    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    if not os.environ.get("XDG_RUNTIME_DIR"):
        rt = os.path.join(tempfile.gettempdir(), f"biga-xdg-{os.getuid()}")
        try:
            os.makedirs(rt, mode=0o700, exist_ok=True)
        except OSError:
            pass
        else:
            os.environ["XDG_RUNTIME_DIR"] = rt

    mode = os.environ.get("BIGA_SDL_VIDEO", "kmsdrm").strip().lower()
    if mode == "kmsdrm":
        os.environ["SDL_VIDEODRIVER"] = "KMSDRM"
        os.environ.pop("SDL_FBDEV", None)
        os.environ.pop("FRAMEBUFFER", None)
    elif mode == "fbcon":
        os.environ["SDL_VIDEODRIVER"] = "fbcon"
        os.environ.setdefault("SDL_FBDEV", "/dev/fb0")
        os.environ.setdefault("FRAMEBUFFER", os.environ["SDL_FBDEV"])
    elif mode:
        os.environ["SDL_VIDEODRIVER"] = mode
