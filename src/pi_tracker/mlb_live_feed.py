"""Parse MLB v1.1 live game feed into SharedGameState fields."""

from __future__ import annotations

from typing import Any

from .mlb_http import ANGELS_TEAM_ID, FINAL_DETAILED, fetch_live_feed_v11


def _team_profile(side: dict[str, Any] | None) -> dict[str, Any]:
    """
    Schedule JSON nests under ``teams.away.team``; v1.1 live ``gameData.teams.away``
    often flattens id/abbreviation onto the side object with no ``team`` key.
    """
    if not side or not isinstance(side, dict):
        return {}
    inner = side.get("team")
    if isinstance(inner, dict) and inner.get("id") is not None:
        return inner
    return side


_EVENT_MAP: dict[str, str] = {
    "home_run": "homerun",
    "strikeout": "strikeout",
    "strikeout_double_play": "strikeout",
    "walk": "walk",
    "intent_walk": "walk",
    "hit_by_pitch": "walk",
    "double": "double",
    "triple": "triple",
    "single": "hit",
    "field_out": "out",
    "grounded_into_double_play": "out",
    "force_out": "out",
    "double_play": "out",
    "triple_play": "out",
    "sac_fly": "out",
    "sac_bunt": "out",
    "fielders_choice": "out",
    "fielders_choice_out": "out",
    "caught_stealing_2b": "out",
    "caught_stealing_3b": "out",
    "caught_stealing_home": "out",
    "stolen_base_2b": "stolen_base",
    "stolen_base_3b": "stolen_base",
    "stolen_base_home": "stolen_base",
}


def _batting_team_id(play: dict[str, Any], away_id: int, home_id: int) -> int:
    """Team at bat for a completed play (top = away, bottom = home)."""
    about = play.get("about") or {}
    if "isTopInning" in about:
        return away_id if about.get("isTopInning") else home_id
    half = str(about.get("halfInning") or "").lower()
    if half == "top":
        return away_id
    if half == "bottom":
        return home_id
    return 0


def _normalise_event(raw: str) -> str:
    """Map an MLB API event string to one of our canned animation names."""
    return _EVENT_MAP.get(raw, "")


def _safe_int(v: Any) -> int | None:
    try:
        if v is None:
            return None
        return int(v)
    except (TypeError, ValueError):
        return None


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
    away_prof = _team_profile(teams.get("away"))
    home_prof = _team_profile(teams.get("home"))
    aid = _safe_int(away_prof.get("id"))
    hid = _safe_int(home_prof.get("id"))
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


def _pitcher_batter_team_ids(
    linescore: dict[str, Any],
    offense: dict[str, Any],
    defense: dict[str, Any],
    away_id: int,
    home_id: int,
) -> tuple[int, int]:
    """(pitching_team_id, batting_team_id) — defense pitches, offense bats."""
    o_team = offense.get("team") if isinstance(offense.get("team"), dict) else {}
    d_team = defense.get("team") if isinstance(defense.get("team"), dict) else {}
    bat = _safe_int(o_team.get("id")) or 0
    pit = _safe_int(d_team.get("id")) or 0
    half_l = str(linescore.get("inningHalf", "top")).lower()
    if bat == 0:
        bat = away_id if half_l == "top" else home_id
    if pit == 0:
        pit = home_id if half_l == "top" else away_id
    return pit, bat


def _boxscore_stat_group(feed: dict[str, Any], player_id: Any, group: str) -> dict[str, Any]:
    """Per-game stats for ``player_id`` (``group`` = ``pitching`` / ``batting``) from boxscore."""
    if not player_id:
        return {}
    box = (feed.get("liveData") or {}).get("boxscore") or {}
    teams = box.get("teams") or {}
    for side in ("away", "home"):
        players = (teams.get(side) or {}).get("players") or {}
        pl = players.get(f"ID{player_id}")
        if isinstance(pl, dict):
            stats = pl.get("stats") or {}
            g = stats.get(group)
            return g if isinstance(g, dict) else {}
    return {}


def _inning_runs_cell(value: Any) -> str:
    if value is None or value == "":
        return "-"
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return "-"


def linescore_grid_from_feed(feed: dict[str, Any]) -> dict[str, Any]:
    """All innings' runs per side plus R/H/E hits & errors from live feed linescore."""
    linescore = (feed.get("liveData") or {}).get("linescore") or {}
    innings = linescore.get("innings") or []
    by_num: dict[int, dict[str, Any]] = {}
    for inn in innings:
        if not isinstance(inn, dict):
            continue
        n = _safe_int(inn.get("num"))
        if n is None or n < 1:
            continue
        by_num[n] = inn

    max_inning = max(by_num.keys()) if by_num else 9
    away_cells: list[str] = []
    home_cells: list[str] = []
    for col in range(1, max_inning + 1):
        inn = by_num.get(col)
        if inn is None:
            away_cells.append("-")
            home_cells.append("-")
            continue
        aside = inn.get("away") or {}
        hside = inn.get("home") or {}
        away_cells.append(_inning_runs_cell(aside.get("runs")))
        home_cells.append(_inning_runs_cell(hside.get("runs")))

    tr = linescore.get("teams") or {}
    at = tr.get("away") or {}
    ht = tr.get("home") or {}
    return {
        "linescore_away_innings": away_cells,
        "linescore_home_innings": home_cells,
        "away_hits": int(at.get("hits", 0) or 0),
        "home_hits": int(ht.get("hits", 0) or 0),
        "away_errors": int(at.get("errors", 0) or 0),
        "home_errors": int(ht.get("errors", 0) or 0),
    }


def merge_linescore_patch_for_pk(game_pk: int) -> dict[str, Any]:
    """Fetch final feed and return linescore patch; empty dict if unavailable or not final."""
    try:
        feed = fetch_live_feed_v11(game_pk)
        if not game_is_final(feed):
            return {}
        return linescore_grid_from_feed(feed)
    except Exception:
        return {}


def live_feed_to_state_patch(feed: dict[str, Any]) -> dict[str, Any]:
    live_data = feed.get("liveData") or {}
    game_data = feed.get("gameData") or {}
    linescore = live_data.get("linescore") or {}
    plays = live_data.get("plays") or {}
    teams_gd = game_data.get("teams") or {}

    away_side = teams_gd.get("away") or {}
    home_side = teams_gd.get("home") or {}
    away_team = _team_profile(away_side)
    home_team = _team_profile(home_side)
    away_id = int(away_team.get("id") or 0)
    home_id = int(home_team.get("id") or 0)
    away_abbr = str(away_team.get("abbreviation") or away_side.get("abbreviation") or "AWY")
    home_abbr = str(home_team.get("abbreviation") or home_side.get("abbreviation") or "HME")
    away_name = str(
        away_team.get("name")
        or away_team.get("teamName")
        or away_team.get("clubName")
        or away_side.get("teamName")
        or ""
    )
    home_name = str(
        home_team.get("name")
        or home_team.get("teamName")
        or home_team.get("clubName")
        or home_side.get("teamName")
        or ""
    )

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
    pit_tid, bat_tid = _pitcher_batter_team_ids(linescore, offense, defense, away_id, home_id)
    players = game_data.get("players") or {}
    batter = "—"
    pitcher = "—"
    if batter_id:
        batter = str((players.get(f"ID{batter_id}", {}) or {}).get("fullName") or "—")
    if pitcher_id:
        pitcher = str((players.get(f"ID{pitcher_id}", {}) or {}).get("fullName") or "—")

    pst = _boxscore_stat_group(feed, pitcher_id, "pitching")
    bst = _boxscore_stat_group(feed, batter_id, "batting")
    pitcher_ip = str(pst.get("inningsPitched", "") or "").strip()
    pitcher_k = _safe_int(pst.get("strikeOuts")) or 0
    pitcher_bb = _safe_int(pst.get("baseOnBalls")) or 0
    pitcher_pitches = _safe_int(pst.get("numberOfPitches"))
    if pitcher_pitches is None:
        pitcher_pitches = _safe_int(pst.get("pitchesThrown")) or 0
    batter_ab = _safe_int(bst.get("atBats")) if bst else None
    batter_hits = _safe_int(bst.get("hits")) or 0
    batter_rbi = _safe_int(bst.get("rbi")) or 0

    current = plays.get("currentPlay") or {}
    count = current.get("count") or {}
    balls = int(count.get("balls", 0) or 0)
    strikes = int(count.get("strikes", 0) or 0)

    inning_half = str(linescore.get("inningHalf", "top")).lower()
    inning_state = str(linescore.get("inningState", inning_half)).lower()
    inning = linescore.get("currentInning", 1)
    outs = int(linescore.get("outs", 0) or 0)

    last_play = ""
    live_event = ""
    live_last_play_id = ""

    # Walk allPlays in reverse to find the most recent completed play.
    all_plays = plays.get("allPlays") or []
    cur_desc = str((current.get("result") or {}).get("description") or "").strip()
    if cur_desc:
        last_play = cur_desc

    # Find the last *completed* play to extract event type.
    for p in reversed(all_plays):
        if not isinstance(p, dict):
            continue
        result = p.get("result") or {}
        d = str(result.get("description") or "").strip()
        if not d:
            continue
        if not last_play:
            last_play = d
        # play_id lets us deduplicate (don't re-fire same event next poll).
        play_id = str(p.get("playId") or p.get("atBatIndex") or "")
        event_raw = str(result.get("eventType") or result.get("event") or "").lower().replace(" ", "_")
        live_event = _normalise_event(event_raw)
        # Halo + HR GIF are for our team only — skip opponent homers.
        if live_event == "homerun":
            batting_id = _batting_team_id(p, away_id or 0, home_id or 0)
            if batting_id and batting_id != ANGELS_TEAM_ID:
                live_event = ""
        live_last_play_id = play_id
        break

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
        "inning_state": inning_state,
        "outs": outs,
        "balls": balls,
        "strikes": strikes,
        "runners": runners,
        "pitcher_name": pitcher,
        "batter_name": batter,
        "pitcher_team_id": pit_tid,
        "batter_team_id": bat_tid,
        "pitcher_ip": pitcher_ip,
        "pitcher_k": pitcher_k,
        "pitcher_bb": pitcher_bb,
        "pitcher_pitches": pitcher_pitches,
        "batter_ab": batter_ab,
        "batter_hits": batter_hits,
        "batter_rbi": batter_rbi,
        **linescore_grid_from_feed(feed),
    }
    if last_play:
        patch["last_play"] = last_play
    if live_event and live_last_play_id:
        patch["live_event"] = live_event
        patch["live_last_play_id"] = live_last_play_id
    if pk is not None:
        patch["live_game_pk"] = int(pk)
    return patch


def fetch_live_feed(game_pk: int) -> dict[str, Any]:
    """Alias for tests / callers that prefer a verb name."""
    return fetch_live_feed_v11(game_pk)
