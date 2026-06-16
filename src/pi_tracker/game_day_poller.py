"""Idle watch (today's game went live) + live feed polling + final-day lock."""

from __future__ import annotations

import logging
import threading
import time
from datetime import date

from .mlb_http import fetch_live_feed_v11
from .mlb_live_feed import angels_won, game_is_final, live_feed_to_state_patch
from .mlb_schedule import (
    fetch_angels_schedule_for_date,
    find_todays_final_angels_game,
    find_todays_scoreboard_angels_game,
    live_transition_from_schedule_game,
    patch_from_final_schedule_game,
)
from .schedule_poller import refresh_idle_schedule
from .state import SharedGameState

log = logging.getLogger(__name__)

FINAL_SCENES = frozenset({"win", "loss"})

# How often to check today's schedule while on idle (game start / warmup).
IDLE_WATCH_SEC = 5
# Live scoreboard refresh while scene is live.
LIVE_POLL_SEC = 2
# Win/loss: wake often to detect local midnight without API calls.
FINAL_DAYCHECK_SEC = 60
# Same calendar day: occasional schedule peek for game 2 of a doubleheader.
FINAL_DH_CHECK_SEC = 5 * 60

_final_dh_last_mono: float = 0.0


def _parse_final_display_date(snap: dict) -> date | None:
    raw = snap.get("final_display_date")
    if not raw or not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        return None


def _ensure_final_display_date(state: SharedGameState, snap: dict) -> date:
    locked = _parse_final_display_date(snap)
    if locked is not None:
        return locked
    today = date.today()
    state.update(final_display_date=today.isoformat())
    log.info(
        "final scene locked: scene=%s final_display_date=%s",
        snap.get("scene"),
        today.isoformat(),
    )
    return today


def _release_to_idle(state: SharedGameState, locked: date, today: date) -> None:
    """New calendar day after final score — resume idle / next-game cycle."""
    state.update(scene="idle", final_display_date="")
    refresh_idle_schedule(state)
    log.info("final display day ended (locked=%s today=%s); scene -> idle", locked, today)


def _lock_final_patch(patch: dict) -> dict:
    patch.setdefault("final_display_date", date.today().isoformat())
    return patch


def _handle_final_scene(state: SharedGameState, snap: dict) -> float:
    """
    Hold win/loss with no routine API use until the next local day.

    Same day: one schedule check every FINAL_DH_CHECK_SEC for a second live game.
    """
    global _final_dh_last_mono
    locked = _ensure_final_display_date(state, snap)
    today = date.today()
    if today > locked:
        _release_to_idle(state, locked, today)
        return FINAL_DAYCHECK_SEC

    now = time.monotonic()
    if now - _final_dh_last_mono >= FINAL_DH_CHECK_SEC:
        _final_dh_last_mono = now
        try:
            sched = fetch_angels_schedule_for_date(today)
            active = find_todays_scoreboard_angels_game(sched)
            if active:
                patch = live_transition_from_schedule_game(active)
                patch["final_display_date"] = ""
                state.update(patch)
                log.info("doubleheader / second game live; scene -> live")
        except Exception as e:  # noqa: BLE001
            log.warning("final-day DH schedule check failed: %s", e)

    return FINAL_DAYCHECK_SEC


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
                final_g = find_todays_final_angels_game(sched)
                if final_g:
                    patch = patch_from_final_schedule_game(final_g)
                    pk = patch.get("live_game_pk")
                    if pk:
                        from .mlb_live_feed import merge_linescore_patch_for_pk

                        patch.update(merge_linescore_patch_for_pk(int(pk)))
                    state.update(patch)
                    log.info(
                        "today's game already final: scene=%s final_display_date=%s",
                        patch.get("scene"),
                        patch.get("final_display_date"),
                    )
                    continue
            elif scene == "live":
                wait = LIVE_POLL_SEC
                pk = snap.get("live_game_pk") or snap.get("next_game_pk")
                if pk:
                    feed = fetch_live_feed_v11(int(pk))
                    patch = live_feed_to_state_patch(feed)
                    # Only fire live_event when the play_id is new so the
                    # animation isn't re-triggered on every poll.
                    new_play_id = patch.get("live_last_play_id", "")
                    if new_play_id and new_play_id == snap.get("live_last_play_id"):
                        patch.pop("live_event", None)
                        patch.pop("live_last_play_id", None)
                    if game_is_final(feed):
                        won = angels_won(feed)
                        if won is True:
                            patch["scene"] = "win"
                        elif won is False:
                            patch["scene"] = "loss"
                        else:
                            patch["scene"] = "loss"
                        _lock_final_patch(patch)
                        state.update(patch)
                        log.info(
                            "game went final via live feed: scene=%s final_display_date=%s",
                            patch.get("scene"),
                            patch.get("final_display_date"),
                        )
                    else:
                        patch["scene"] = "live"
                        state.update(patch)
            elif scene in FINAL_SCENES:
                wait = _handle_final_scene(state, snap)
            else:
                wait = FINAL_DAYCHECK_SEC
        except Exception as e:  # noqa: BLE001
            log.warning("game day poll failed: %s", e)

        if stop.wait(wait):
            break


def start_game_day_poller(state: SharedGameState, stop: threading.Event) -> threading.Thread:
    t = threading.Thread(target=game_day_loop, args=(state, stop), name="game-day", daemon=True)
    t.start()
    return t
