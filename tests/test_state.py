from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path


def test_expire_stale_final_when_clock_unsynced(monkeypatch, tmp_path):
    from pi_tracker import clock, config
    from pi_tracker.state import SharedGameState

    monkeypatch.setattr(config, "STATE_PATH", tmp_path / "state.json")
    (tmp_path / "state.json").write_text(
        json.dumps(
            {
                "scene": "win",
                "final_display_date": date.today().isoformat(),
                "live_game_pk": 1,
            }
        )
    )
    monkeypatch.setattr(clock, "clock_is_synchronized", lambda: False)
    st = SharedGameState(persist=True)
    assert st.snapshot()["scene"] == "idle"


def test_expire_final_from_yesterday(monkeypatch, tmp_path):
    from pi_tracker import clock, config
    from pi_tracker.state import SharedGameState

    monkeypatch.setattr(config, "STATE_PATH", tmp_path / "state.json")
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    (tmp_path / "state.json").write_text(
        json.dumps({"scene": "loss", "final_display_date": yesterday, "live_game_pk": 2})
    )
    monkeypatch.setattr(clock, "clock_is_synchronized", lambda: True)
    st = SharedGameState(persist=True)
    assert st.snapshot()["scene"] == "idle"


def test_keep_final_from_today(monkeypatch, tmp_path):
    from pi_tracker import clock, config
    from pi_tracker.state import SharedGameState

    monkeypatch.setattr(config, "STATE_PATH", tmp_path / "state.json")
    (tmp_path / "state.json").write_text(
        json.dumps(
            {
                "scene": "win",
                "final_display_date": date.today().isoformat(),
                "live_game_pk": 3,
                "home_runs": 5,
            }
        )
    )
    monkeypatch.setattr(clock, "clock_is_synchronized", lambda: True)
    st = SharedGameState(persist=True)
    snap = st.snapshot()
    assert snap["scene"] == "win"
    assert snap["live_game_pk"] == 3


def test_blank_final_date_drops_to_idle(monkeypatch, tmp_path):
    from pi_tracker import clock, config
    from pi_tracker.state import SharedGameState

    monkeypatch.setattr(config, "STATE_PATH", tmp_path / "state.json")
    (tmp_path / "state.json").write_text(json.dumps({"scene": "win", "final_display_date": ""}))
    monkeypatch.setattr(clock, "clock_is_synchronized", lambda: True)
    st = SharedGameState(persist=True)
    assert st.snapshot()["scene"] == "idle"


def test_demo_pk_ignored(monkeypatch, tmp_path):
    from pi_tracker import config
    from pi_tracker.state import SharedGameState

    monkeypatch.setattr(config, "STATE_PATH", tmp_path / "state.json")
    (tmp_path / "state.json").write_text(
        json.dumps({"scene": "win", "live_game_pk": 999999, "final_display_date": date.today().isoformat()})
    )
    st = SharedGameState(persist=True)
    assert st.snapshot()["scene"] == "idle"


def test_update_persists_subset(monkeypatch, tmp_path):
    from pi_tracker import config
    from pi_tracker.state import SharedGameState

    path = tmp_path / "state.json"
    monkeypatch.setattr(config, "STATE_PATH", path)
    st = SharedGameState(persist=True)
    st.update(scene="live", live_game_pk=42, last_play="not persisted")
    saved = json.loads(path.read_text())
    assert saved["scene"] == "live"
    assert saved["live_game_pk"] == 42
    assert "last_play" not in saved
