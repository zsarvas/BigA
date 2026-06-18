"""
Global playback gate.

While a video clip is playing through mpv, the Pi Zero 2W's limited CPU is
best reserved entirely for hardware decode + render.  Background polling
threads (game-day feed, idle schedule, highlight downloader) call
``wait_while_active`` so they pause cleanly during playback and resume the
moment mpv exits.

``app._play_mpv`` brackets the subprocess call with ``begin()`` / ``end()``.
"""

from __future__ import annotations

import threading

_active = threading.Event()


def begin() -> None:
    """Mark video playback as active (pollers will pause)."""
    _active.set()


def end() -> None:
    """Mark video playback as finished (pollers resume)."""
    _active.clear()


def is_active() -> bool:
    return _active.is_set()


_download_busy_count = 0
_download_busy_lock = threading.Lock()


def download_begin() -> None:
    """Highlight downloader started a clip (network + ffmpeg)."""
    global _download_busy_count
    with _download_busy_lock:
        _download_busy_count += 1


def download_end() -> None:
    """Highlight downloader finished a clip."""
    global _download_busy_count
    with _download_busy_lock:
        if _download_busy_count > 0:
            _download_busy_count -= 1


def is_download_busy() -> bool:
    """True while a game highlight is being downloaded or transcoded."""
    with _download_busy_lock:
        return _download_busy_count > 0


def wait_while_active(stop: threading.Event, poll: float = 0.25) -> None:
    """
    Block while playback is active, returning early if *stop* is set.

    Polling threads should call this at the top of their loop so no new
    network/CPU work starts mid-clip.
    """
    while _active.is_set() and not stop.is_set():
        stop.wait(poll)
