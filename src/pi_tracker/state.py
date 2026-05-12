"""Thread-safe snapshot of game + UI state (poller thread writes, pygame reads)."""

from __future__ import annotations

import threading
from typing import Any


class SharedGameState:
    """Minimal dict-backed state with copy-on-read for the render thread."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, Any] = {
            "scene": "idle",
            "away_team_id": 137,
            "home_team_id": 108,
            "away_abbr": "SF",
            "home_abbr": "LAA",
            "away_name": "Giants",
            "home_name": "Angels",
            "away_runs": 0,
            "home_runs": 0,
            "inning": 1,
            "inning_half": "top",
            "outs": 0,
            "balls": 0,
            "strikes": 0,
            "runners": {"first": False, "second": False, "third": False},
            "pitcher_name": "",
            "batter_name": "",
            "pitcher_team_id": 0,
            "batter_team_id": 0,
            "last_play": "",
            "schedule_status": "loading",
            "schedule_error": "",
            "next_game_date_display": "",
            "next_game_time_display": "",
            "next_game_matchup": "",
            "next_game_venue": "",
            "next_game_pk": None,
            "live_game_pk": None,
            "next_opponent_team_id": None,
            "idle_subtitle": "Loading schedule…",
        }

    def update(self, patch: dict[str, Any] | None = None, **kwargs: Any) -> None:
        with self._lock:
            if patch:
                self._data.update(patch)
            if kwargs:
                self._data.update(kwargs)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._data)
