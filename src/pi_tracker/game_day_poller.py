"""Idle watch (today's game went live) + live feed polling every 2s + win/loss."""

from __future__ import annotations

import logging
import threading
from datetime import date

from .mlb_http import fetch_live_feed_v11
from .mlb_live_feed import angels_won, game_is_final, live_feed_to_state_patch
from .mlb_schedule import (
    fetch_angels_schedule_for_date,
    find_todays_scoreboard_angels_game,
    live_transition_from_schedule_game,
)
from .state import SharedGameState

log = logging.getLogger(__name__)

# How often to check today's schedule while on idle (game start / warmup).
IDLE_WATCH_SEC = 5
# Live scoreboard refresh while scene is live.
LIVE_POLL_SEC = 2


def game_day_loop(state: SharedGameState, stop: threading.Event) -> None:
    while not stop.is_set():
        snap = state.snapshot()
        scene = str(snap.get("scene", "idle"))
        wait = 5.0
        try:
            if scene == "idle":
                wait = IDLE_WATCH_SEC
                sched = fetch_angels_schedule_for_date(date.today())
                active = find_todays_scoreboard_angels_game(sched)
                if active:
                    state.update(live_transition_from_schedule_game(active))
                    continue
            elif scene == "live":
                wait = LIVE_POLL_SEC
                pk = snap.get("live_game_pk") or snap.get("next_game_pk")
                if pk:
                    feed = fetch_live_feed_v11(int(pk))
                    patch = live_feed_to_state_patch(feed)
                    if game_is_final(feed):
                        won = angels_won(feed)
                        if won is True:
                            patch["scene"] = "win"
                        elif won is False:
                            patch["scene"] = "loss"
                        else:
                            patch["scene"] = "loss"
                        state.update(patch)
                    else:
                        patch["scene"] = "live"
                        state.update(patch)
            else:
                wait = 5.0
        except Exception as e:  # noqa: BLE001
            log.warning("game day poll failed: %s", e)

        if stop.wait(wait):
            break


def start_game_day_poller(state: SharedGameState, stop: threading.Event) -> threading.Thread:
    t = threading.Thread(target=game_day_loop, args=(state, stop), name="game-day", daemon=True)
    t.start()
    return t
