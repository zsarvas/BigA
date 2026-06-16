"""
Clip-player mixin.

Scenes that want periodic highlight playback inherit ``ClipPlayerMixin`` and
call ``self._cp_tick(state_or_folder)`` from ``draw()``.  When the gap has
elapsed and a clip is available, the mixin sets ``self._pending_clip`` to the
chosen Path.  ``app.py`` picks that up after the draw call and hands it off to
mpv, which plays the clip hardware-accelerated while pygame is suspended.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any

from .. import config


def _rand_gap_ms() -> int:
    lo = config.HIGHLIGHT_MIN_GAP_MIN * 60_000
    hi = max(lo, config.HIGHLIGHT_MAX_GAP_MIN * 60_000)
    return random.randint(lo, hi)


def _pick_clip(folder: Path, played: set[str]) -> Path | None:
    """Choose a random clip from *folder*, cycling back after all have been played."""
    clips = list(folder.glob("*.mp4")) + list(folder.glob("*.gif"))
    if not clips:
        return None
    unseen = [p for p in clips if p.name not in played]
    if not unseen:
        played.clear()
        unseen = clips
    path = random.choice(unseen)
    played.add(path.name)
    return path


class ClipPlayerMixin:
    """
    Mix-in for timed clip playback via mpv.

    After ``_cp_tick(folder)`` sets ``self._pending_clip``, ``app.py``
    drains it and calls ``_play_mpv()``.  The scene itself never renders
    video frames — mpv handles that completely.

    Call ``_cp_tick(folder)`` from ``draw()``; it is a no-op when ``folder``
    is None or empty.
    """

    def __init_cp(self) -> None:
        if hasattr(self, "_cp_init_done"):
            return
        self._cp_init_done = True
        self._cp_next_play_ms: int = 0
        self._cp_played: set[str] = set()
        self._pending_clip: Path | None = None  # read by app.py each frame

    def _cp_tick(self, folder: Path | None) -> None:
        """
        Check if it's time to queue a clip.  Sets ``self._pending_clip`` when
        the gap has elapsed and a clip exists in *folder*.
        """
        self.__init_cp()

        # Don't queue another until the current one has been consumed by app.py.
        if self._pending_clip is not None:
            return

        import pygame  # local import; mixin is shared across display-less contexts
        now = pygame.time.get_ticks()

        if self._cp_next_play_ms == 0:
            self._cp_next_play_ms = now + _rand_gap_ms()
            return

        if now < self._cp_next_play_ms:
            return

        if folder and folder.is_dir():
            path = _pick_clip(folder, self._cp_played)
            if path:
                self._pending_clip = path

        self._cp_next_play_ms = now + _rand_gap_ms()
