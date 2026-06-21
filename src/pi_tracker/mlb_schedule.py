"""MLB Stats API — Angels next-game lookup for idle screen."""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from typing import Any

from .mlb_http import ANGELS_TEAM_ID, FINAL_DETAILED, LIVE_DETAILED, api_get as _get

# Skip when picking the next game to advertise on idle
_SKIP_DETAILED = {
    "Final",
    "Game Over",
    "Completed Early",
    "Cancelled",
    "Postponed",
}


def fetch_angels_schedule_for_date(day: date, team_id: int = ANGELS_TEAM_ID) -> dict[str, Any]:
    return _get(
        "/api/v1/schedule",
        {
            "sportId": 1,
            "teamId": team_id,
            "date": day.isoformat(),
            "hydrate": "team,venue",
        },
    )



def fetch_angels_schedule_window(
    start: date | None = None,
    days: int = 21,
    team_id: int = ANGELS_TEAM_ID,
) -> dict[str, Any]:
    start = start or date.today()
    end = start + timedelta(days=max(days, 1))
    return _get(
        "/api/v1/schedule",
        {
            "sportId": 1,
            "teamId": team_id,
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "hydrate": "team,venue",
        },
    )


def _iter_games(schedule_json: dict[str, Any]):
    for d in schedule_json.get("dates", []):
        for g in d.get("games", []):
            yield g


def _schedule_game_is_scoreboard_active(game: dict[str, Any]) -> bool:
    """True only when the game is actually in progress — not hours-ahead ``Pre-Game``."""
    st = game.get("status") or {}
    if st.get("abstractGameState") == "Live":
        return True
    detailed = st.get("detailedState") or ""
    if detailed in LIVE_DETAILED:
        return True
    return False


def find_todays_scoreboard_angels_game(
    schedule_json: dict[str, Any],
    angels_id: int = ANGELS_TEAM_ID,
) -> dict[str, Any] | None:
    """Today's Angels game that should show the live scoreboard (not idle)."""
    for g in _iter_games(schedule_json):
        aid = g.get("teams", {}).get("away", {}).get("team", {}).get("id")
        hid = g.get("teams", {}).get("home", {}).get("team", {}).get("id")
        if aid != angels_id and hid != angels_id:
            continue
        if _schedule_game_is_scoreboard_active(g):
            return g
    return None


def _schedule_game_is_final(game: dict[str, Any]) -> bool:
    st = game.get("status") or {}
    if st.get("abstractGameState") == "Final":
        return True
    return (st.get("detailedState") or "") in FINAL_DETAILED


def find_todays_final_angels_game(
    schedule_json: dict[str, Any],
    angels_id: int = ANGELS_TEAM_ID,
) -> dict[str, Any] | None:
    """Latest (by first pitch) Angels game on this schedule date that is final."""
    return _latest_final_angels_game(schedule_json, angels_id=angels_id)


def fetch_angels_schedule_lookback(
    days: int = 7,
    team_id: int = ANGELS_TEAM_ID,
) -> dict[str, Any]:
    """Schedule from ``days`` calendar days ago through today (inclusive)."""
    end = date.today()
    start = end - timedelta(days=max(days - 1, 0))
    return _get(
        "/api/v1/schedule",
        {
            "sportId": 1,
            "teamId": team_id,
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "hydrate": "team,venue",
        },
    )


def _latest_final_angels_game(
    schedule_json: dict[str, Any],
    angels_id: int = ANGELS_TEAM_ID,
) -> dict[str, Any] | None:
    """Most recent final Angels game in a schedule payload (by first pitch)."""
    finals: list[tuple[datetime, dict[str, Any]]] = []
    for g in _iter_games(schedule_json):
        aid = g.get("teams", {}).get("away", {}).get("team", {}).get("id")
        hid = g.get("teams", {}).get("home", {}).get("team", {}).get("id")
        if aid != angels_id and hid != angels_id:
            continue
        if not _schedule_game_is_final(g):
            continue
        dt = _parse_game_datetime(g)
        if dt is None:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        finals.append((dt, g))
    if not finals:
        return None
    finals.sort(key=lambda x: x[0])
    return finals[-1][1]


def find_most_recent_final_angels_game(
    schedule_json: dict[str, Any],
    angels_id: int = ANGELS_TEAM_ID,
) -> dict[str, Any] | None:
    """Latest final Angels game in a multi-day schedule window."""
    return _latest_final_angels_game(schedule_json, angels_id=angels_id)


def live_transition_from_schedule_game(game: dict[str, Any]) -> dict[str, Any]:
    teams = game.get("teams") or {}
    away = teams.get("away", {}).get("team", {})
    home = teams.get("home", {}).get("team", {})
    aid, hid = away.get("id"), home.get("id")
    opp_id: int | None = None
    try:
        if aid == ANGELS_TEAM_ID and hid is not None:
            opp_id = int(hid)
        elif hid == ANGELS_TEAM_ID and aid is not None:
            opp_id = int(aid)
    except (TypeError, ValueError):
        opp_id = None
    venue_obj = game.get("venue") or {}
    out: dict[str, Any] = {
        "scene": "live",
        "live_game_pk": game.get("gamePk"),
        "live_venue_id": int(venue_obj.get("id") or 0),
        "live_venue_name": str(venue_obj.get("name") or ""),
        "away_team_id": int(away.get("id") or 0),
        "home_team_id": int(home.get("id") or 0),
        "away_abbr": str(away.get("abbreviation") or ""),
        "home_abbr": str(home.get("abbreviation") or ""),
        "away_name": str(away.get("clubName") or away.get("name") or ""),
        "home_name": str(home.get("clubName") or home.get("name") or ""),
    }
    if opp_id is not None:
        out["next_opponent_team_id"] = opp_id
    return out


def angels_won_from_schedule_game(
    game: dict[str, Any],
    angels_id: int = ANGELS_TEAM_ID,
) -> bool | None:
    """True / False from schedule row scores; None if tie or Angels not in game."""
    teams = game.get("teams") or {}
    ta = teams.get("away") or {}
    th = teams.get("home") or {}
    away_team = ta.get("team") or {}
    home_team = th.get("team") or {}
    try:
        aid = int(away_team.get("id") or 0)
        hid = int(home_team.get("id") or 0)
    except (TypeError, ValueError):
        return None
    ar = int(ta.get("score", 0) or 0)
    hr = int(th.get("score", 0) or 0)
    if ar == hr:
        return None
    if aid == angels_id:
        return ar > hr
    if hid == angels_id:
        return hr > ar
    return None


def patch_from_final_schedule_game(game: dict[str, Any]) -> dict[str, Any]:
    """Win/loss scene + runs/teams from a final schedule row."""
    patch = live_transition_from_schedule_game(game)
    ta = game.get("teams", {}).get("away", {})
    th = game.get("teams", {}).get("home", {})
    patch["away_runs"] = int(ta.get("score", 0) or 0)
    patch["home_runs"] = int(th.get("score", 0) or 0)
    won = angels_won_from_schedule_game(game)
    if won is True:
        patch["scene"] = "win"
    elif won is False:
        patch["scene"] = "loss"
    else:
        patch["scene"] = "loss"
    patch["final_display_date"] = date.today().isoformat()
    return patch


def _parse_game_datetime(game: dict[str, Any]) -> datetime | None:
    raw = game.get("gameDate")
    if not raw or not isinstance(raw, str):
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _opponent_line(game: dict[str, Any], angels_id: int = ANGELS_TEAM_ID) -> tuple[str, str]:
    teams = game.get("teams", {})
    away = teams.get("away", {}).get("team", {})
    home = teams.get("home", {}).get("team", {})
    aid, hid = away.get("id"), home.get("id")
    away_name = away.get("clubName") or away.get("name") or "Away"
    home_name = home.get("clubName") or home.get("name") or "Home"
    if aid == angels_id:
        return f"@ {home_name}", home.get("abbreviation", "")
    if hid == angels_id:
        return f"vs {away_name}", away.get("abbreviation", "")
    fb_name = os.environ.get("BIGA_TEAM_NAME", "Team")
    return fb_name, ""


def opponent_team_id_for_next_game(game: dict[str, Any] | None, angels_id: int = ANGELS_TEAM_ID) -> int | None:
    """MLB team id for the Angels' opponent in this schedule row (for idle small logo)."""
    if not game:
        return None
    teams = game.get("teams") or {}
    aid = teams.get("away", {}).get("team", {}).get("id")
    hid = teams.get("home", {}).get("team", {}).get("id")
    try:
        if aid == angels_id and hid is not None:
            return int(hid)
        if hid == angels_id and aid is not None:
            return int(aid)
    except (TypeError, ValueError):
        return None
    return None


def pick_next_angels_game(
    schedule_json: dict[str, Any],
    angels_id: int = ANGELS_TEAM_ID,
) -> dict[str, Any] | None:
    """
    Earliest scheduled / live Angels game in the window, excluding
    final / cancelled / postponed.
    """
    candidates: list[tuple[datetime, dict[str, Any]]] = []
    for g in _iter_games(schedule_json):
        st = g.get("status") or {}
        detailed = st.get("detailedState") or ""
        abstract = st.get("abstractGameState") or ""
        if detailed in _SKIP_DETAILED:
            continue
        if abstract == "Final":
            continue
        dt = _parse_game_datetime(g)
        if dt is None:
            continue
        away_id = g.get("teams", {}).get("away", {}).get("team", {}).get("id")
        home_id = g.get("teams", {}).get("home", {}).get("team", {}).get("id")
        if away_id != angels_id and home_id != angels_id:
            continue
        candidates.append((dt, g))

    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


def format_next_game_for_ui(game: dict[str, Any] | None, angels_id: int = ANGELS_TEAM_ID) -> dict[str, Any]:
    """Patch dict for SharedGameState idle / schedule fields."""
    if game is None:
        return {
            "schedule_status": "none",
            "schedule_error": "",
            "next_game_date_display": "",
            "next_game_time_display": "",
            "next_game_matchup": "",
            "next_game_venue": "",
            "next_game_pk": None,
            "next_opponent_team_id": None,
            "idle_subtitle": "No upcoming games in the next few weeks.",
        }

    dt = _parse_game_datetime(game)
    if dt is None:
        return {
            "schedule_status": "error",
            "schedule_error": "Bad gameDate from API",
            "next_game_date_display": "",
            "next_game_time_display": "",
            "next_game_matchup": "",
            "next_game_venue": "",
            "next_game_pk": game.get("gamePk"),
            "next_opponent_team_id": None,
            "idle_subtitle": "Schedule parse error.",
        }

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone()

    date_disp = local.strftime("%A, %b %d, %Y")
    clock = local.strftime("%I:%M %p")
    if clock.startswith("0"):
        clock = clock[1:]
    tz = local.tzname() or ""
    time_disp = f"{clock} {tz}" if tz else clock

    matchup, _opp_abbr = _opponent_line(game, angels_id)
    venue_obj = game.get("venue") or {}
    venue = venue_obj.get("name") or ""
    venue_id = int(venue_obj.get("id") or 0)
    opp_tid = opponent_team_id_for_next_game(game, angels_id)

    return {
        "schedule_status": "ok",
        "schedule_error": "",
        "next_game_date_display": date_disp,
        "next_game_time_display": time_disp,
        "next_game_matchup": matchup,
        "next_game_venue": venue,
        "next_game_venue_id": venue_id,
        "next_game_pk": game.get("gamePk"),
        "next_opponent_team_id": opp_tid,
        "idle_subtitle": f"{date_disp}  ·  {time_disp}",
    }


def fetch_and_format_next_game(
    start: date | None = None,
    window_days: int = 21,
    team_id: int = ANGELS_TEAM_ID,
) -> dict[str, Any]:
    raw = fetch_angels_schedule_window(start=start, days=window_days, team_id=team_id)
    nxt = pick_next_angels_game(raw, angels_id=team_id)
    return format_next_game_for_ui(nxt, angels_id=team_id)


def try_restore_final_scene_for_today(state: Any) -> None:
    """
    If state is idle and today's Angels game is already final, show win/loss (e.g. after reboot).
    """
    import logging

    log = logging.getLogger(__name__)
    try:
        snap = state.snapshot()
        if str(snap.get("scene", "idle")) != "idle":
            return
        sched = fetch_angels_schedule_for_date(date.today())
        g = find_todays_final_angels_game(sched)
        if g is None:
            return
        patch = patch_from_final_schedule_game(g)
        pk = patch.get("live_game_pk")
        if pk:
            from .mlb_live_feed import merge_linescore_patch_for_pk

            patch.update(merge_linescore_patch_for_pk(int(pk)))
        state.update(patch)
    except Exception as e:  # noqa: BLE001
        log.warning("try_restore_final_scene_for_today: %s", e)
