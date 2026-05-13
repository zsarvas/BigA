"""
Fullscreen idle clips via mpv (subprocess).

**Non-fbcon (KMS / desktop):** release pygame video, run mpv, then ``set_mode`` again::

  pygame.display.quit() → mpv → pygame.display.init() + set_mode

**fbcon (SPI / classic framebuffer):** do **not** call ``pygame.display.quit()`` — on Pi Zero
and similar stacks, re-init often raises ``fbcon not available``. Keep the display open, run
mpv on top (best-effort), then return ``pygame.display.get_surface()``.

On Pi with pygame on **fbcon**, ``--vo=drm`` usually targets KMS (e.g. HDMI), not the SPI
buffer; we default mpv to ``gpu`` when ``SDL_VIDEODRIVER=fbcon``. Override with
``BIGA_MPV_VO=…``. Extra flags: ``BIGA_MPV_OPTS``.
"""

from __future__ import annotations

import logging
import os
import random
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

import pygame

from . import config

log = logging.getLogger(__name__)


def _sdl_fbcon() -> bool:
    return os.environ.get("SDL_VIDEODRIVER", "").strip().lower() == "fbcon"


def discover_idle_videos() -> list[Path]:
    return sorted(p for p in config.ASSETS_DIR.glob("*.mp4") if p.is_file())


def _default_mpv_vo() -> str:
    if config.IDLE_MPV_VO:
        return config.IDLE_MPV_VO
    if not sys.platform.startswith("linux"):
        return "gpu"
    # fbcon + SPI: drm VO is usually the wrong surface; gpu often hits EGL/fbdev context.
    if os.environ.get("SDL_VIDEODRIVER", "").strip().lower() == "fbcon":
        return "gpu"
    return "drm"


def build_mpv_command(video: Path) -> list[str]:
    vo = _default_mpv_vo()
    cmd = [
        "mpv",
        "--fullscreen",
        f"--vo={vo}",
        "--no-osd-bar",
        "--really-quiet",
        "--no-terminal",
    ]
    extra = os.environ.get("BIGA_MPV_OPTS", "").strip()
    if extra:
        cmd.extend(shlex.split(extra))
    cmd.append(str(video))
    return cmd


def suspend_pygame_run_mpv_resume(
    video: Path,
    display_flags: int,
    width: int = config.SCREEN_WIDTH,
    height: int = config.SCREEN_HEIGHT,
) -> pygame.Surface:
    """
    Run mpv fullscreen, then return the surface to draw on.

    fbcon: never ``display.quit()`` — reclaiming fbcon often fails on Pi.
    Other drivers: quit display, mpv, then ``init`` + ``set_mode`` again.
    """
    cmd = build_mpv_command(video)
    mpv_bin = shutil.which(cmd[0])

    def _log_mpv_result(proc: subprocess.CompletedProcess) -> None:
        if proc.returncode != 0:
            err = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
            if err:
                log.warning("mpv exit %s: %s", proc.returncode, err[:800])
            else:
                log.warning("mpv exited with code %s", proc.returncode)

    def _run_mpv() -> subprocess.CompletedProcess | None:
        if mpv_bin is None:
            return None
        log.info("idle clip: %s", " ".join(cmd))
        return subprocess.run(
            cmd,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

    if _sdl_fbcon():
        proc = _run_mpv()
        if proc is not None:
            _log_mpv_result(proc)
        else:
            log.warning("mpv not found on PATH; skipping %s", video.name)
        pygame.mouse.set_visible(False)
        surf = pygame.display.get_surface()
        if surf is None:
            log.error("get_surface() is None after idle clip; recreating window")
            return pygame.display.set_mode((width, height), display_flags)
        return surf

    pygame.display.quit()
    if pygame.mixer.get_init():
        pygame.mixer.quit()

    if mpv_bin is None:
        log.warning("mpv not found on PATH; skipping %s", video.name)
        pygame.display.init()
        screen = pygame.display.set_mode((width, height), display_flags)
        pygame.mouse.set_visible(False)
        return screen

    try:
        proc = _run_mpv()
        assert proc is not None
        _log_mpv_result(proc)
    finally:
        pygame.display.init()
        screen = pygame.display.set_mode((width, height), display_flags)
        pygame.mouse.set_visible(False)
    return screen


class IdleMpvScheduler:
    """
    Fire ``play`` every ``config.IDLE_HIGHLIGHT_INTERVAL_SEC`` wall seconds (fixed;
    ``--idle-video-debug`` uses a shorter fixed interval) while the scene is one of:
    ``idle``, ``win``, or ``loss`` (highlights between games / after final).

    ``live`` does not run clips — the scoreboard stays up for the whole game.

    When leaving those scenes (e.g. to ``live``), the timer is not consumed;
    returning to an mpv-eligible scene picks behavior from ``_prev_scene``:
    from ``live`` we schedule a fresh delay; moving among idle/win/loss keeps
    the same pending fire time.
    """

    _MPV_SCENES = frozenset({"idle", "win", "loss"})

    def __init__(self, paths: list[Path], debug_interval_sec: float | None = None) -> None:
        self.paths = list(paths)
        if debug_interval_sec is not None and debug_interval_sec > 0:
            self._interval_sec = float(debug_interval_sec)
        else:
            self._interval_sec = float(config.IDLE_HIGHLIGHT_INTERVAL_SEC)
        self._next_at: float | None = None
        self._prev_scene: str | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.paths)

    def _roll_next(self, now: float) -> None:
        if self._interval_sec <= 0:
            self._next_at = None
            return
        self._next_at = now + self._interval_sec

    def tick(self, scene_key: str, play: Callable[[Path], None]) -> None:
        if not self.enabled:
            return

        now = time.monotonic()
        if scene_key not in self._MPV_SCENES:
            self._prev_scene = scene_key
            return

        if self._prev_scene not in self._MPV_SCENES:
            self._roll_next(now)
        self._prev_scene = scene_key

        if self._next_at is not None and now >= self._next_at:
            clip = random.choice(self.paths)
            play(clip)
            self._roll_next(time.monotonic())
