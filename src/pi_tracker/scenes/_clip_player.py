"""
Reusable highlight-clip player mixin.

Classes that need periodic clip playback (IdleScene, FinalWinScene,
FinalLossScene) inherit ``ClipPlayerMixin`` and call
``self._cp_maybe_play(screen, folder)`` from their ``draw()`` method.

The caller supplies the *folder* of clips to sample each call, allowing
scenes to switch between game highlights and the idle reel dynamically.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any

import pygame

from .. import config
from ..assets import open_streaming_clip


def _rand_gap_ms() -> int:
    lo = config.HIGHLIGHT_MIN_GAP_MIN * 60_000
    hi = max(lo, config.HIGHLIGHT_MAX_GAP_MIN * 60_000)
    return random.randint(lo, hi)


def _pick_clip(folder: Path, size: tuple[int, int], played: set[str]) -> Any:
    """Choose a random un-played clip from *folder*; loop back if all played."""
    clips = [p for p in folder.glob("*.mp4")] + [p for p in folder.glob("*.gif")]
    if not clips:
        return None
    unseen = [p for p in clips if p.name not in played]
    if not unseen:
        played.clear()
        unseen = clips
    path = random.choice(unseen)
    played.add(path.name)
    clip = open_streaming_clip(path, size)
    return clip if clip.ok else None


class ClipPlayerMixin:
    """
    Mix-in that adds timed clip playback.

    Call ``_cp_maybe_play(screen, folder)`` from ``draw()``.
    Returns True if a clip frame was drawn this tick (caller should skip
    drawing the normal scene content).
    """

    def __init_cp(self) -> None:
        if hasattr(self, "_cp_init_done"):
            return
        self._cp_init_done = True
        self._cp_clip: Any = None
        self._cp_playing = False
        self._cp_frame_idx = 0
        self._cp_cur_frame: pygame.Surface | None = None
        self._cp_deadline_ms = 0
        self._cp_next_play_ms = 0
        self._cp_played: set[str] = set()

    @property
    def _playing(self) -> bool:
        """Exposed so app.py can detect an active clip (tick-rate selection)."""
        return getattr(self, "_cp_playing", False)

    def _cp_maybe_play(self, screen: pygame.Surface, folder: Path | None) -> bool:
        """
        Advance the clip if one is playing, or start a new one if the gap has elapsed.
        Returns True if a clip frame was drawn.
        """
        self.__init_cp()
        now = pygame.time.get_ticks()

        if self._cp_next_play_ms == 0:
            self._cp_next_play_ms = now + _rand_gap_ms()

        if not self._cp_playing and now >= self._cp_next_play_ms and folder and folder.is_dir():
            clip = _pick_clip(folder, screen.get_size(), self._cp_played)
            if clip:
                surf, dur = clip.decode(0)
                if surf is not None:
                    self._cp_clip = clip
                    self._cp_playing = True
                    self._cp_frame_idx = 0
                    self._cp_cur_frame = surf
                    self._cp_deadline_ms = now + dur
            self._cp_next_play_ms = now + _rand_gap_ms()

        if not self._cp_playing:
            return False

        if now >= self._cp_deadline_ms and self._cp_clip is not None:
            self._cp_frame_idx += 1
            if self._cp_frame_idx >= self._cp_clip.n_frames:
                self._cp_playing = False
                self._cp_clip = None
                self._cp_cur_frame = None
                return False
            surf, dur = self._cp_clip.decode(self._cp_frame_idx)
            if surf is None:
                self._cp_playing = False
                self._cp_clip = None
                self._cp_cur_frame = None
                return False
            self._cp_cur_frame = surf
            self._cp_deadline_ms = now + dur

        if self._cp_cur_frame is not None:
            screen.blit(self._cp_cur_frame, (0, 0))
            return True
        return False
