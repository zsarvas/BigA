"""MLB Stats API — Angels next-game lookup for idle screen."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

import requests

BASE_URL = "https://statsapi.mlb.com"
ANGELS_TEAM_ID = 108

# Skip when picking the next game to advertise on idle
_SKIP_DETAILED = {
    "Final",
    "Game Over",
    "Completed Early",
    "Cancelled",
    "Postponed",
}


def _get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    url = BASE_URL + path
    resp = requests.get(url, params=params or {}, timeout=15)
    resp.raise_for_status()
    return resp.json()


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
            "idle_subtitle": "Schedule parse error.",
        }

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone()

    date_disp = local.strftime("%A, %b %d, %Y")
    abstract = (game.get("status") or {}).get("abstractGameState", "")
    if abstract == "Live":
        time_disp = "In progress"
    else:
        clock = local.strftime("%I:%M %p")
        if clock.startswith("0"):
            clock = clock[1:]
        tz = local.tzname() or ""
        time_disp = f"{clock} {tz}" if tz else clock

    matchup, _opp_abbr = _opponent_line(game, angels_id)
    venue = (game.get("venue") or {}).get("name") or ""

    return {
        "schedule_status": "ok",
        "schedule_error": "",
        "next_game_date_display": date_disp,
        "next_game_time_display": time_disp,
        "next_game_matchup": matchup,
        "next_game_venue": venue,
        "next_game_pk": game.get("gamePk"),
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
