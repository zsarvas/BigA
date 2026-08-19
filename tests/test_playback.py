from __future__ import annotations

import threading
import time


class _FakeProc:
    def __init__(self) -> None:
        self.pid = 424242
        self._dead = False

    def poll(self):
        return 0 if self._dead else None

    def terminate(self):
        self._dead = True

    def kill(self):
        self._dead = True


def test_begin_end_active():
    from pi_tracker import playback

    assert playback.is_active() is False
    playback.begin()
    assert playback.is_active() is True
    playback.end()
    assert playback.is_active() is False


def test_download_busy_refcount():
    from pi_tracker import playback

    playback.download_begin()
    playback.download_begin()
    assert playback.is_download_busy()
    playback.download_end()
    assert playback.is_download_busy()
    playback.download_end()
    assert not playback.is_download_busy()
    playback.download_end()  # underflow guard
    assert not playback.is_download_busy()


def test_request_stop_clips_kills_mpv(monkeypatch):
    from pi_tracker import playback

    monkeypatch.setattr(
        playback.os,
        "killpg",
        lambda *a, **k: (_ for _ in ()).throw(ProcessLookupError()),
    )
    proc = _FakeProc()
    playback.register_mpv_proc(proc)
    playback.request_stop_clips("game final")
    assert playback.clips_stop_requested()
    assert playback.stop_reason() == "game final"
    assert proc.poll() == 0
    playback.clear_stop_clips()
    assert not playback.clips_stop_requested()
    assert playback.stop_reason() == ""


def test_wait_while_active_returns_on_stop():
    from pi_tracker import playback

    playback.begin()
    stop = threading.Event()
    stop.set()
    playback.wait_while_active(stop, poll=0.01)
    playback.end()


def test_wait_for_transcode_slot_blocked_by_live_break():
    from pi_tracker import playback

    playback.set_live_break_priority(True)
    stop = threading.Event()

    def _clear():
        time.sleep(0.05)
        playback.set_live_break_priority(False)

    threading.Thread(target=_clear, daemon=True).start()
    playback.wait_for_transcode_slot(stop, poll=0.01)


def test_final_scene_prefers_light_transcode():
    from pi_tracker import playback

    playback.set_final_scene_active(True)
    assert playback.prefers_light_transcode()
    playback.set_final_scene_active(False)
    assert not playback.prefers_light_transcode()
