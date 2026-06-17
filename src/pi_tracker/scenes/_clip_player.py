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
import re
from pathlib import Path
from typing import Any

from .. import config

# Filename slug tokens → display abbreviations (from MLB highlight blurbs).
_TITLE_ABBREV = {
    "hr": "HR",
    "rbi": "RBI",
    "ab": "AB",
    "so": "SO",
    "bb": "BB",
    "k": "K",
    "vs": "vs",
    "dp": "DP",
}


def clip_title_from_path(path: Path) -> str:
    """
    Humanize a highlight clip filename back into a short title.

    Downloaded clips are named from MLB blurbs via slugification, e.g.
    ``mike-trout-s-hr-16.mp4`` → ``Mike Trout's HR (16)``.
    """
    stem = path.stem.replace("_", "-")
    words = [w for w in stem.split("-") if w]
    if not words:
        return path.name

    out: list[str] = []
    for w in words:
        low = w.lower()
        if low in _TITLE_ABBREV:
            out.append(_TITLE_ABBREV[low])
        elif low == "s" and out:
            out[-1] = out[-1] + "'s"
        elif w.isdigit():
            out.append(f"({w})")
        else:
            out.append(w.capitalize())

    title = re.sub(r"\s+", " ", " ".join(out)).strip()
    return title or stem


def _rand_gap_ms() -> int:
    lo = config.HIGHLIGHT_MIN_GAP_MIN * 60_000
    hi = max(lo, config.HIGHLIGHT_MAX_GAP_MIN * 60_000)
    return random.randint(lo, hi)


def _playable_clip_paths(folder: Path) -> list[Path]:
    """Finished .mp4 clips only — skip in-progress download/transcode temps."""
    out: list[Path] = []
    for p in folder.glob("*.mp4"):
        low = p.name.lower()
        if ".raw" in low or ".tmp" in low:
            continue
        out.append(p)
    return out


def _pick_clip(folder: Path, played: set[str]) -> Path | None:
    """Choose a random clip from *folder*, preferring smaller (transcoded) files."""
    clips = _playable_clip_paths(folder) + list(folder.glob("*.gif"))
    if not clips:
        return None
    unseen = [p for p in clips if p.name not in played]
    if not unseen:
        played.clear()
        unseen = clips
    # Smallest files are usually our 480×320 transcodes; huge ones are full 720p API pulls.
    unseen.sort(key=lambda p: p.stat().st_size if p.exists() else 0)
    small_pool = unseen[:max(1, (len(unseen) + 1) // 2)]
    path = random.choice(small_pool)
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

    def _cp_tick(self, folder: Path | None, gap_min: int | None = None) -> None:
        """
        Check if it's time to queue a clip.  Sets ``self._pending_clip`` when
        the gap has elapsed and a clip exists in *folder*.

        *gap_min* overrides the default random gap (uses config values if None).
        """
        self.__init_cp()

        # Don't queue another until the current one has been consumed by app.py.
        if self._pending_clip is not None:
            return

        import pygame  # local import; mixin is shared across display-less contexts
        now = pygame.time.get_ticks()

        gap_ms = gap_min * 60_000 if gap_min is not None else _rand_gap_ms()

        if self._cp_next_play_ms == 0:
            self._cp_next_play_ms = now + gap_ms
            return

        if now < self._cp_next_play_ms:
            return

        if folder and folder.is_dir():
            path = _pick_clip(folder, self._cp_played)
            if path:
                self._pending_clip = path

        self._cp_next_play_ms = now + gap_ms
