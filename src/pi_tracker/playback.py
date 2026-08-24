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

import logging
import os
import signal
import subprocess
import threading
import time

log = logging.getLogger(__name__)

_active = threading.Event()
_stop_clips = threading.Event()
_mpv_lock = threading.Lock()
_mpv_proc: subprocess.Popen | None = None
_mpv_reason = ""


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


_final_scene_active = False
_final_scene_lock = threading.Lock()

_live_break_priority = False
_live_break_lock = threading.Lock()


def set_final_scene_active(active: bool) -> None:
    """True while win/loss scene is showing (smooth GIF + lighter background ffmpeg)."""
    global _final_scene_active
    with _final_scene_lock:
        _final_scene_active = active


def prefers_light_transcode() -> bool:
    """Background ffmpeg should yield CPU to the win/loss GIF renderer."""
    with _final_scene_lock:
        return _final_scene_active


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


def request_stop_clips(reason: str = "") -> None:
    """Ask the main thread to stop NOW SHOWING / mpv (e.g. game went final)."""
    global _mpv_reason
    if reason:
        _mpv_reason = reason
        log.info("requesting clip stop: %s", reason)
    else:
        log.info("requesting clip stop")
    _stop_clips.set()
    kill_current_mpv(reason=reason or "stop requested")


def clips_stop_requested() -> bool:
    return _stop_clips.is_set()


def stop_reason() -> str:
    return _mpv_reason


def clear_stop_clips() -> None:
    """Allow a new clip (win/loss highlights after live mpv was aborted)."""
    global _mpv_reason
    _stop_clips.clear()
    _mpv_reason = ""


def register_mpv_proc(proc: subprocess.Popen | None) -> None:
    global _mpv_proc
    with _mpv_lock:
        _mpv_proc = proc


def kill_current_mpv(reason: str = "") -> None:
    """SIGTERM then SIGKILL the running mpv process group (safe from other threads)."""
    with _mpv_lock:
        proc = _mpv_proc
    if proc is None or proc.poll() is not None:
        return
    if reason:
        log.warning("killing mpv (%s) pid=%s", reason, proc.pid)
    else:
        log.warning("killing mpv pid=%s", proc.pid)
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.terminate()
        except OSError:
            return
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return
        time.sleep(0.1)
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except OSError:
            pass
