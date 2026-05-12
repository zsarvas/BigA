"""Parse MLB v1.1 live game feed into SharedGameState fields."""

from __future__ import annotations

from typing import Any

from .mlb_http import ANGELS_TEAM_ID, FINAL_DETAILED, fetch_live_feed_v11


def game_is_final(feed: dict[str, Any]) -> bool:
    st = (feed.get("gameData") or {}).get("status") or {}
    if st.get("abstractGameState") == "Final":
        return True
    return (st.get("detailedState") or "") in FINAL_DETAILED


def angels_won(feed: dict[str, Any], angels_id: int = ANGELS_TEAM_ID) -> bool | None:
    """True / False if Angels won or lost; None if tie or Angels not in game."""
    if not game_is_final(feed):
        return None
    gd = feed.get("gameData") or {}
    teams = gd.get("teams") or {}
    aid = (teams.get("away", {}).get("team") or {}).get("id")
    hid = (teams.get("home", {}).get("team") or {}).get("id")
    ls = (feed.get("liveData") or {}).get("linescore") or {}
    tr = ls.get("teams") or {}
    ar = int((tr.get("away") or {}).get("runs", 0) or 0)
    hr = int((tr.get("home") or {}).get("runs", 0) or 0)
    if aid == angels_id:
        if ar == hr:
            return None
        return ar > hr
    if hid == angels_id:
        if ar == hr:
            return None
        return hr > ar
    return None


def live_feed_to_state_patch(feed: dict[str, Any]) -> dict[str, Any]:
    live_data = feed.get("liveData") or {}
    game_data = feed.get("gameData") or {}
    linescore = live_data.get("linescore") or {}
    plays = live_data.get("plays") or {}
    teams_gd = game_data.get("teams") or {}

    away_team = (teams_gd.get("away") or {}).get("team") or {}
    home_team = (teams_gd.get("home") or {}).get("team") or {}
    away_id = int(away_team.get("id") or 0)
    home_id = int(home_team.get("id") or 0)
    away_abbr = str(away_team.get("abbreviation") or "AWY")
    home_abbr = str(home_team.get("abbreviation") or "HME")
    away_name = str(away_team.get("name") or away_team.get("clubName") or "")
    home_name = str(home_team.get("name") or home_team.get("clubName") or "")

    tr = linescore.get("teams") or {}
    ar = int((tr.get("away") or {}).get("runs", 0) or 0)
    hr = int((tr.get("home") or {}).get("runs", 0) or 0)

    offense = linescore.get("offense") or {}
    runners = {
        "first": "first" in offense,
        "second": "second" in offense,
        "third": "third" in offense,
    }

    defense = linescore.get("defense") or {}
    batter_id = (offense.get("batter") or {}).get("id")
    pitcher_id = (defense.get("pitcher") or {}).get("id")
    players = game_data.get("players") or {}
    batter = "—"
    pitcher = "—"
    if batter_id:
        batter = str((players.get(f"ID{batter_id}", {}) or {}).get("fullName") or "—")
    if pitcher_id:
        pitcher = str((players.get(f"ID{pitcher_id}", {}) or {}).get("fullName") or "—")

    current = plays.get("currentPlay") or {}
    count = current.get("count") or {}
    balls = int(count.get("balls", 0) or 0)
    strikes = int(count.get("strikes", 0) or 0)

    inning_half = str(linescore.get("inningHalf", "top")).lower()
    inning = linescore.get("currentInning", 1)
    outs = int(linescore.get("outs", 0) or 0)

    last_play = ""
    all_plays = plays.get("allPlays") or []
    if all_plays:
        last = all_plays[-1]
        last_play = str((last.get("result") or {}).get("description") or "")

    pk = feed.get("gamePk")
    if pk is None:
        g = game_data.get("game")
        if isinstance(g, dict):
            pk = g.get("pk")

    patch: dict[str, Any] = {
        "away_team_id": away_id,
        "home_team_id": home_id,
        "away_abbr": away_abbr,
        "home_abbr": home_abbr,
        "away_name": away_name,
        "home_name": home_name,
        "away_runs": ar,
        "home_runs": hr,
        "inning": inning,
        "inning_half": inning_half,
        "outs": outs,
        "balls": balls,
        "strikes": strikes,
        "runners": runners,
        "pitcher_name": pitcher,
        "batter_name": batter,
        "last_play": last_play,
    }
    if pk is not None:
        patch["live_game_pk"] = int(pk)
    return patch


def fetch_live_feed(game_pk: int) -> dict[str, Any]:
    """Alias for tests / callers that prefer a verb name."""
    return fetch_live_feed_v11(game_pk)
