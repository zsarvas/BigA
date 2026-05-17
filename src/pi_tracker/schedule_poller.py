"""Background refresh of next-game info for the idle screen (every 20 minutes)."""

from __future__ import annotations

import logging
import threading

from .mlb_schedule import fetch_and_format_next_game
from .state import SharedGameState

log = logging.getLogger(__name__)

POLL_INTERVAL_SEC = 20 * 60


def _poll_once(state: SharedGameState) -> None:
    patch = fetch_and_format_next_game()
    state.update(patch)


def refresh_idle_schedule(state: SharedGameState) -> None:
    """Fetch next-game fields (idle scene only)."""
    try:
        _poll_once(state)
    except Exception as e:  # noqa: BLE001
        log.warning("schedule poll failed: %s", e)
        state.update(
            schedule_status="error",
            schedule_error=str(e)[:120],
            idle_subtitle="Could not load schedule.",
            next_opponent_team_id=None,
        )


def idle_schedule_loop(state: SharedGameState, stop: threading.Event) -> None:
    """Refresh next-game info only while scene is idle (no calls during live / final)."""
    refresh_idle_schedule(state)
    while not stop.is_set():
        snap = state.snapshot()
        if str(snap.get("scene", "idle")) == "idle":
            if stop.wait(POLL_INTERVAL_SEC):
                break
            if stop.is_set():
                break
            refresh_idle_schedule(state)
        else:
            if stop.wait(60):
                break


def start_idle_schedule_poller(state: SharedGameState, stop: threading.Event) -> threading.Thread:
    t = threading.Thread(target=idle_schedule_loop, args=(state, stop), name="idle-schedule", daemon=True)
    t.start()
    return t
