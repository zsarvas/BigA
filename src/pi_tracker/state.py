"""Thread-safe snapshot of game + UI state (poller thread writes, pygame reads)."""

from __future__ import annotations

import os
import threading
from typing import Any


def _env_tracked_home() -> tuple[int, str, str]:
    try:
        tid = int(os.environ.get("BIGA_TEAM_ID", "108"), 10)
    except ValueError:
        tid = 108
    abbr = os.environ.get("BIGA_TEAM_ABBR", "LAA").strip() or "LAA"
    name = os.environ.get("BIGA_TEAM_NAME", "Angels").strip() or "Angels"
    return tid, abbr, name


class SharedGameState:
    """Minimal dict-backed state with copy-on-read for the render thread."""

    def __init__(self) -> None:
        home_id, home_abbr, home_name = _env_tracked_home()
        self._lock = threading.Lock()
        self._data: dict[str, Any] = {
            "scene": "idle",
            "away_team_id": 137,
            "home_team_id": home_id,
            "away_abbr": "SF",
            "home_abbr": home_abbr,
            "away_name": "Giants",
            "home_name": home_name,
            "away_runs": 0,
            "home_runs": 0,
            "linescore_away_innings": ["-"] * 9,
            "linescore_home_innings": ["-"] * 9,
            "away_hits": 0,
            "home_hits": 0,
            "away_errors": 0,
            "home_errors": 0,
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
            "pitcher_ip": "",
            "pitcher_k": 0,
            "pitcher_bb": 0,
            "pitcher_pitches": 0,
            "batter_ab": None,
            "batter_hits": 0,
            "batter_rbi": 0,
            "last_play": "",
            "schedule_status": "loading",
            "schedule_error": "",
            "next_game_date_display": "",
            "next_game_time_display": "",
            "next_game_matchup": "",
            "next_game_venue": "",
            "next_game_venue_id": 0,
            "live_venue_id": 0,
            "live_venue_name": "",
            "next_game_pk": None,
            "live_game_pk": None,
            "next_opponent_team_id": None,
            # Calendar day (local) when win/loss was shown; idle resumes after this date.
            "final_display_date": "",
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
