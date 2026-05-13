"""
Fullscreen idle clips via mpv (subprocess).

Pygame cannot embed ``--vo=drm`` output. The safe pattern is:
  pygame.display.quit() → mpv runs → pygame.display.init() + set_mode again.

On Pi you may need pygame and mpv coordinated with your stack (fbcpy vs KMS);
see project notes.
"""

from __future__ import annotations

import logging
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

import pygame

from . import config

log = logging.getLogger(__name__)


def discover_idle_videos() -> list[Path]:
    return sorted(p for p in config.ASSETS_DIR.glob("*.mp4") if p.is_file())


def _default_mpv_vo() -> str:
    if config.IDLE_MPV_VO:
        return config.IDLE_MPV_VO
    return "drm" if sys.platform.startswith("linux") else "gpu"


def build_mpv_command(video: Path) -> list[str]:
    vo = _default_mpv_vo()
    return [
        "mpv",
        "--fullscreen",
        f"--vo={vo}",
        "--no-osd-bar",
        "--really-quiet",
        str(video),
    ]


def suspend_pygame_run_mpv_resume(
    video: Path,
    display_flags: int,
    width: int = config.SCREEN_WIDTH,
    height: int = config.SCREEN_HEIGHT,
) -> pygame.Surface:
    """
    Release the pygame display, run mpv until exit, then recreate the window.
    Returns the new screen surface.
    """
    pygame.display.quit()
    if pygame.mixer.get_init():
        pygame.mixer.quit()

    cmd = build_mpv_command(video)
    if shutil.which(cmd[0]) is None:
        log.warning("mpv not found on PATH; skipping %s", video.name)
        pygame.display.init()
        return pygame.display.set_mode((width, height), display_flags)

    log.info("idle clip: %s", " ".join(cmd))
    try:
        subprocess.run(cmd, check=False)
    finally:
        pygame.display.init()
        screen = pygame.display.set_mode((width, height), display_flags)
    return screen


class IdleMpvScheduler:
    """
    Fire ``play`` after random wall-clock intervals while the scene is one of:
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
            self._lo = self._hi = float(debug_interval_sec)
        else:
            lo = min(config.IDLE_VIDEO_MIN_INTERVAL_SEC, config.IDLE_VIDEO_MAX_INTERVAL_SEC)
            hi = max(config.IDLE_VIDEO_MIN_INTERVAL_SEC, config.IDLE_VIDEO_MAX_INTERVAL_SEC)
            self._lo = float(lo)
            self._hi = float(hi)
        self._next_at: float | None = None
        self._prev_scene: str | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.paths)

    def _roll_next(self, now: float) -> None:
        if self._hi <= 0:
            self._next_at = None
            return
        self._next_at = now + random.uniform(self._lo, self._hi)

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
