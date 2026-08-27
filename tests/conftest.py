from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "portal"))


@pytest.fixture(autouse=True)
def _reset_playback_flags():
    from pi_tracker import playback

    playback.clear_stop_clips()
    playback.end()
    playback.reset_download_busy()
    playback.set_live_break_priority(False)
    playback.set_final_scene_active(False)
    yield
    playback.clear_stop_clips()
    playback.end()
    playback.reset_download_busy()
    playback.set_live_break_priority(False)
    playback.set_final_scene_active(False)


@pytest.fixture(autouse=True)
def _default_team_env(monkeypatch):
    monkeypatch.setenv("BIGA_TEAM_ID", "108")
    monkeypatch.setenv("BIGA_TEAM_ABBR", "LAA")
    monkeypatch.setenv("BIGA_TEAM_NAME", "Angels")


@pytest.fixture(autouse=True)
def _reset_clock_cache():
    from pi_tracker import clock

    clock._synced_cached = False
    yield
    clock._synced_cached = False


@pytest.fixture
def wifi_paths(tmp_path, monkeypatch):
    """Point wifi_store at a temp dir so tests never touch /etc/biga."""
    import wifi_store

    creds = tmp_path / "wifi_creds.json"
    flag = tmp_path / "provisioning_active"
    last = tmp_path / "biga-wifi-last.log"
    monkeypatch.setattr(wifi_store, "CREDS_FILE", creds)
    monkeypatch.setattr(wifi_store, "PROVISIONING_FLAG", flag)
    monkeypatch.setattr(wifi_store, "LAST_WIFI_LOG", last)
    return creds, flag


@pytest.fixture
def no_ffprobe(monkeypatch):
    """Treat clips as valid by size so tests do not need ffmpeg."""
    import shutil

    real = shutil.which

    def _which(name, *args, **kwargs):
        if name in ("ffprobe", "ffmpeg"):
            return None
        return real(name, *args, **kwargs)

    monkeypatch.setattr(shutil, "which", _which)
