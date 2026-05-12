"""MLB Stats API — Angels next-game lookup for idle screen."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from .mlb_http import ANGELS_TEAM_ID, LIVE_DETAILED, api_get as _get

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
    st = game.get("status") or {}
    if st.get("abstractGameState") == "Live":
        return True
    detailed = st.get("detailedState") or ""
    if detailed in LIVE_DETAILED:
        return True
    if detailed in ("Warmup", "Pre-Game"):
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
    out: dict[str, Any] = {
        "scene": "live",
        "live_game_pk": game.get("gamePk"),
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
    return "Angels", ""


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
    venue = (game.get("venue") or {}).get("name") or ""
    opp_tid = opponent_team_id_for_next_game(game, angels_id)

    return {
        "schedule_status": "ok",
        "schedule_error": "",
        "next_game_date_display": date_disp,
        "next_game_time_display": time_disp,
        "next_game_matchup": matchup,
        "next_game_venue": venue,
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
