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
_transcode_busy = False
_transcode_lock = threading.Lock()


def download_begin() -> None:
    """Highlight downloader started a network fetch for one clip."""
    global _download_busy_count
    with _download_busy_lock:
        _download_busy_count += 1


def download_end() -> None:
    """Highlight downloader finished (or aborted) a network fetch."""
    global _download_busy_count
    with _download_busy_lock:
        if _download_busy_count > 0:
            _download_busy_count -= 1


def is_download_busy() -> bool:
    """True while a highlight clip is being fetched over the network."""
    with _download_busy_lock:
        return _download_busy_count > 0


def transcode_begin() -> None:
    """ffmpeg re-encode running — do not start mpv or other heavy CPU work."""
    global _transcode_busy
    with _transcode_lock:
        _transcode_busy = True


def transcode_end() -> None:
    """ffmpeg finished."""
    global _transcode_busy
    with _transcode_lock:
        _transcode_busy = False


def is_transcode_busy() -> bool:
    with _transcode_lock:
        return _transcode_busy


def reset_download_busy() -> None:
    """Clear leaked busy flags (e.g. downloader stopped mid-clip)."""
    global _download_busy_count, _transcode_busy
    with _download_busy_lock:
        _download_busy_count = 0
    with _transcode_lock:
        _transcode_busy = False


def wait_while_active(stop: threading.Event, poll: float = 0.25) -> None:
    """
    Block while playback is active, returning early if *stop* is set.

    Polling threads should call this at the top of their loop so no new
    network/CPU work starts mid-clip.
    """
    while _active.is_set() and not stop.is_set():
        stop.wait(poll)


def wait_for_clip_idle(poll: float = 0.25) -> None:
    """Block until mpv clip playback finishes (used before heavy ffmpeg work)."""
    wait_while_active(threading.Event(), poll)


_live_break_priority = False
_live_break_lock = threading.Lock()


def set_live_break_priority(active: bool) -> None:
    """True while live half-inning break clips should play before background ffmpeg."""
    global _live_break_priority
    with _live_break_lock:
        _live_break_priority = active


def is_live_break_priority() -> bool:
    with _live_break_lock:
        return _live_break_priority


def wait_for_transcode_slot(stop: threading.Event | None = None, poll: float = 0.25) -> None:
    """
    Block until mpv is idle and no live break reel is waiting to play.

    Background ffmpeg should not compete with ready-to-play highlight clips.
    """
    wait = stop or threading.Event()
    while not wait.is_set():
        with _live_break_lock:
            blocked = _active.is_set() or _live_break_priority
        if not blocked:
            return
        wait.wait(poll)
