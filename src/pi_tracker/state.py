"""Thread-safe snapshot of game + UI state (poller thread writes, pygame reads)."""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import date
from typing import Any

from . import config

log = logging.getLogger(__name__)

# Fields written to STATE_PATH on update — enough to restore win/loss after reboot.
_PERSIST_KEYS = frozenset({
    "scene",
    "live_game_pk",
    "final_display_date",
    "away_team_id",
    "home_team_id",
    "away_abbr",
    "home_abbr",
    "away_name",
    "home_name",
    "away_runs",
    "home_runs",
    "linescore_away_innings",
    "linescore_home_innings",
    "away_hits",
    "home_hits",
    "away_errors",
    "home_errors",
    "live_venue_id",
    "live_venue_name",
})


def _env_tracked_home() -> tuple[int, str, str]:
    try:
        tid = int(os.environ.get("BIGA_TEAM_ID", "108"), 10)
    except ValueError:
        tid = 108
    abbr = os.environ.get("BIGA_TEAM_ABBR", "LAA").strip() or "LAA"
    name = os.environ.get("BIGA_TEAM_NAME", "Angels").strip() or "Angels"
    return tid, abbr, name


def _expire_stale_final_scene(data: dict[str, Any]) -> None:
    """
    Win/loss from a prior calendar day should not survive a reboot.

    A restored final scene with no valid ``final_display_date`` is treated as
    stale too: every real final stamps that field, so a blank/garbage one means
    leftover or baked-in state — drop to idle rather than pin a fresh device on
    an old game.
    """
    scene = str(data.get("scene", "idle"))
    if scene not in ("win", "loss"):
        return

    def _drop_to_idle() -> None:
        data["scene"] = "idle"
        data["final_display_date"] = ""

    # Pi Zero has no RTC: if the clock hasn't NTP-synced yet, today's date is
    # unreliable, so we can't trust a restored final. Defer to idle — the game
    # day poller re-locks a genuine final once the clock is correct.
    from .clock import clock_is_synchronized

    if not clock_is_synchronized():
        log.info("clock not synced at restore — deferring final scene to idle")
        _drop_to_idle()
        return

    raw = data.get("final_display_date")
    if not raw or not isinstance(raw, str):
        _drop_to_idle()
        return
    try:
        locked = date.fromisoformat(raw.strip())
    except ValueError:
        _drop_to_idle()
        return
    if date.today() > locked:
        _drop_to_idle()


def _load_persisted() -> dict[str, Any]:
    path = config.STATE_PATH
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {}
        return {k: raw[k] for k in _PERSIST_KEYS if k in raw}
    except Exception as exc:  # noqa: BLE001
        log.warning("could not load persisted state from %s: %s", path, exc)
        return {}


def _persist(data: dict[str, Any]) -> None:
    path = config.STATE_PATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        blob = {k: data[k] for k in _PERSIST_KEYS if k in data}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(blob, separators=(",", ":")), encoding="utf-8")
        tmp.replace(path)
    except Exception as exc:  # noqa: BLE001
        log.warning("could not persist state to %s: %s", path, exc)


def clear_persisted_state() -> None:
    """Remove saved win/loss context (e.g. after accidental --demo-final persist)."""
    path = config.STATE_PATH
    try:
        path.unlink(missing_ok=True)
        log.info("cleared persisted state at %s", path)
    except Exception as exc:  # noqa: BLE001
        log.warning("could not clear persisted state: %s", exc)


class SharedGameState:
    """Minimal dict-backed state with copy-on-read for the render thread."""

    def __init__(self, *, persist: bool = True) -> None:
        home_id, home_abbr, home_name = _env_tracked_home()
        self._persist = persist
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
            "inning_state": "top",
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
            # Fired by the live feed when a notable play happens; consumed by
            # LiveScene to trigger the matching GIF animation.  One of:
            # "homerun" | "strikeout" | "walk" | "double" | "triple" |
            # "hit" | "out" | "stolen_base" | "" (no pending event)
            "live_event": "",
            # play ID of the most recently processed play (prevents re-firing
            # the same event on repeated polls)
            "live_last_play_id": "",
            "schedule_status": "loading",
            "schedule_error": "",
            "schedule_updated_at": "",
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
        persisted = _load_persisted() if self._persist else {}
        if persisted:
            pk = persisted.get("live_game_pk")
            if pk in (999999, "999999"):
                log.warning("ignoring persisted demo sample state (live_game_pk=999999)")
                persisted = {}
        if persisted:
            self._data.update(persisted)
            _expire_stale_final_scene(self._data)
            log.info(
                "restored persisted state: scene=%s live_game_pk=%s",
                self._data.get("scene"),
                self._data.get("live_game_pk"),
            )

    def update(self, patch: dict[str, Any] | None = None, **kwargs: Any) -> None:
        with self._lock:
            if patch:
                self._data.update(patch)
            if kwargs:
                self._data.update(kwargs)
            keys = set(kwargs.keys())
            if patch:
                keys.update(patch.keys())
            if self._persist and keys & _PERSIST_KEYS:
                _persist(self._data)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._data)
