from __future__ import annotations

from helpers_mlb import ANGELS_ID, schedule_game, schedule_payload, yankees


def test_find_live_vs_pregame():
    from pi_tracker.mlb_schedule import find_todays_scoreboard_angels_game

    pre = schedule_game(pk=1, abstract="Preview", detailed="Pre-Game")
    live = schedule_game(pk=2, abstract="Live", detailed="In Progress")
    assert find_todays_scoreboard_angels_game(schedule_payload(pre)) is None
    found = find_todays_scoreboard_angels_game(schedule_payload(pre, live))
    assert found is not None
    assert found["gamePk"] == 2


def test_find_todays_final_picks_latest_first_pitch():
    from pi_tracker.mlb_schedule import find_todays_final_angels_game

    early = schedule_game(
        pk=10, abstract="Final", detailed="Final", game_date="2026-08-18T16:00:00Z"
    )
    late = schedule_game(
        pk=11, abstract="Final", detailed="Final", game_date="2026-08-18T23:00:00Z"
    )
    found = find_todays_final_angels_game(schedule_payload(late, early))
    assert found is not None
    assert found["gamePk"] == 11


def test_pick_next_skips_final_and_postponed():
    from pi_tracker.mlb_schedule import pick_next_angels_game

    done = schedule_game(pk=1, abstract="Final", detailed="Final", game_date="2026-08-18T16:00:00Z")
    ppd = schedule_game(pk=2, abstract="Preview", detailed="Postponed", game_date="2026-08-19T16:00:00Z")
    next_g = schedule_game(pk=3, abstract="Preview", detailed="Scheduled", game_date="2026-08-20T19:00:00Z")
    found = pick_next_angels_game(schedule_payload(done, ppd, next_g))
    assert found is not None
    assert found["gamePk"] == 3


def test_patch_from_final_schedule_win_and_loss():
    from pi_tracker.mlb_schedule import patch_from_final_schedule_game

    win = schedule_game(abstract="Final", detailed="Final", home=True, away_score=1, home_score=4)
    patch = patch_from_final_schedule_game(win)
    assert patch["scene"] == "win"
    assert patch["home_runs"] == 4
    assert patch["final_display_date"]
    assert patch["live_game_pk"] == 777001

    loss = schedule_game(abstract="Final", detailed="Final", home=True, away_score=9, home_score=1)
    assert patch_from_final_schedule_game(loss)["scene"] == "loss"


def test_live_transition_sets_scene_live():
    from pi_tracker.mlb_schedule import live_transition_from_schedule_game

    g = schedule_game(pk=55, abstract="Live", detailed="In Progress")
    patch = live_transition_from_schedule_game(g)
    assert patch["scene"] == "live"
    assert patch["live_game_pk"] == 55
    assert patch["home_team_id"] == ANGELS_ID


def test_format_next_game_none():
    from pi_tracker.mlb_schedule import format_next_game_for_ui

    patch = format_next_game_for_ui(None)
    assert patch["schedule_status"] == "none"
    assert patch["next_game_pk"] is None


def test_format_next_game_home():
    from pi_tracker.mlb_schedule import format_next_game_for_ui

    g = schedule_game(pk=9, home=True)
    patch = format_next_game_for_ui(g)
    assert patch["next_game_pk"] == 9
    assert "Yankees" in patch["next_game_matchup"] or "vs" in patch["next_game_matchup"]
    assert patch["next_opponent_team_id"] == yankees()["id"]
