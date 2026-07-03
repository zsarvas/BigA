"""Background refresh of next-game info for the idle screen (every 20 minutes)."""

from __future__ import annotations

import logging
import threading

from .mlb_schedule import fetch_and_format_next_game
from .state import SharedGameState
from . import playback

log = logging.getLogger(__name__)

POLL_INTERVAL_SEC = 20 * 60
# After a failed poll, retry soon instead of waiting the full interval so a
# transient MLB API blip clears the on-screen banner within ~a minute.
ERROR_RETRY_SEC = 60


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
        playback.wait_while_active(stop)
        if stop.is_set():
            break
        snap = state.snapshot()
        if str(snap.get("scene", "idle")) == "idle":
            # Retry quickly while in an error state so a transient failure heals
            # fast; otherwise use the normal long refresh interval.
            errored = str(snap.get("schedule_status", "")) == "error"
            wait = ERROR_RETRY_SEC if errored else POLL_INTERVAL_SEC
            if stop.wait(wait):
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
