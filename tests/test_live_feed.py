from __future__ import annotations

from helpers_mlb import live_feed


def test_game_is_final_abstract_and_detailed():
    from pi_tracker.mlb_live_feed import game_is_final

    assert game_is_final(live_feed(abstract="Final", detailed="Final"))
    assert game_is_final(live_feed(abstract="Live", detailed="Game Over"))
    assert game_is_final(live_feed(abstract="Live", detailed="Completed Early"))
    assert not game_is_final(live_feed(abstract="Live", detailed="In Progress"))


def test_angels_won_home_and_away():
    from pi_tracker.mlb_live_feed import angels_won

    win = live_feed(abstract="Final", detailed="Final", away_runs=1, home_runs=4)
    assert angels_won(win) is True
    loss = live_feed(abstract="Final", detailed="Final", away_runs=8, home_runs=1)
    assert angels_won(loss) is False
    tie = live_feed(abstract="Final", detailed="Final", away_runs=3, home_runs=3)
    assert angels_won(tie) is None
    assert angels_won(live_feed(abstract="Live", detailed="In Progress")) is None


def test_angels_won_flattened_team_objects():
    from pi_tracker.mlb_live_feed import angels_won

    feed = live_feed(abstract="Final", detailed="Final", away_runs=0, home_runs=2, flattened_teams=True)
    assert angels_won(feed) is True


def test_live_feed_patch_and_homerun_event():
    from pi_tracker.mlb_live_feed import live_feed_to_state_patch

    feed = live_feed(
        away_runs=2,
        home_runs=5,
        inning=6,
        half="bottom",
        event="home_run",
        play_id="abc",
    )
    patch = live_feed_to_state_patch(feed)
    assert patch["away_runs"] == 2
    assert patch["home_runs"] == 5
    assert patch["inning"] == 6
    assert patch["live_event"] == "homerun"
    assert patch["live_last_play_id"] == "abc"
    assert patch["linescore_away_innings"]
    assert patch["home_errors"] == 1


def test_opponent_homer_does_not_fire_event():
    from pi_tracker.mlb_live_feed import live_feed_to_state_patch

    feed = live_feed(event="home_run", play_id="opp")
    feed["liveData"]["plays"]["allPlays"][0]["about"]["isTopInning"] = True
    feed["liveData"]["plays"]["allPlays"][0]["about"]["halfInning"] = "top"
    patch = live_feed_to_state_patch(feed)
    assert patch.get("live_event") in ("", None)
