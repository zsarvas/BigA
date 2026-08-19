"""Minimal MLB Stats API-shaped payloads for unit tests."""

from __future__ import annotations

from typing import Any

ANGELS_ID = 108
YANKEES_ID = 147


def team(tid: int, abbr: str, club: str) -> dict[str, Any]:
    return {"id": tid, "abbreviation": abbr, "clubName": club, "name": club, "teamName": club}


def angels() -> dict[str, Any]:
    return team(ANGELS_ID, "LAA", "Angels")


def yankees() -> dict[str, Any]:
    return team(YANKEES_ID, "NYY", "Yankees")


def schedule_game(
    *,
    pk: int = 777001,
    abstract: str = "Preview",
    detailed: str = "Scheduled",
    home: bool = True,
    away_score: int = 0,
    home_score: int = 0,
    game_date: str = "2026-08-18T19:07:00Z",
    venue: str = "Angel Stadium",
    venue_id: int = 1,
) -> dict[str, Any]:
    home_t = angels() if home else yankees()
    away_t = yankees() if home else angels()
    return {
        "gamePk": pk,
        "gameDate": game_date,
        "status": {"abstractGameState": abstract, "detailedState": detailed},
        "venue": {"id": venue_id, "name": venue},
        "teams": {
            "away": {"team": away_t, "score": away_score},
            "home": {"team": home_t, "score": home_score},
        },
    }


def schedule_payload(*games: dict[str, Any], date: str = "2026-08-18") -> dict[str, Any]:
    return {"dates": [{"date": date, "games": list(games)}]}


def live_feed(
    *,
    abstract: str = "Live",
    detailed: str = "In Progress",
    away_runs: int = 1,
    home_runs: int = 2,
    inning: int = 5,
    half: str = "top",
    event: str | None = None,
    play_id: str = "play-1",
    flattened_teams: bool = False,
) -> dict[str, Any]:
    away_t = yankees()
    home_t = angels()
    if flattened_teams:
        teams = {"away": away_t, "home": home_t}
    else:
        teams = {"away": {"team": away_t}, "home": {"team": home_t}}
    plays: dict[str, Any] = {"allPlays": [], "currentPlay": {"count": {"balls": 2, "strikes": 1}}}
    if event:
        plays["allPlays"] = [
            {
                "playId": play_id,
                "about": {
                    "isComplete": True,
                    "isTopInning": False,
                    "halfInning": "bottom",
                    "inning": inning,
                },
                "result": {"eventType": event, "description": "Trout homers.", "event": event},
            }
        ]
    return {
        "gameData": {
            "status": {"abstractGameState": abstract, "detailedState": detailed},
            "teams": teams,
            "players": {},
        },
        "liveData": {
            "linescore": {
                "currentInning": inning,
                "inningHalf": half,
                "inningState": half,
                "outs": 1,
                "teams": {
                    "away": {"runs": away_runs, "hits": 4, "errors": 0},
                    "home": {"runs": home_runs, "hits": 6, "errors": 1},
                },
                "innings": [
                    {"num": 1, "away": {"runs": 0}, "home": {"runs": 1}},
                    {"num": 2, "away": {"runs": away_runs}, "home": {"runs": home_runs - 1 if home_runs else 0}},
                ],
                "offense": {},
                "defense": {},
            },
            "plays": plays,
            "boxscore": {"teams": {"away": {"players": {}}, "home": {"players": {}}}},
        },
    }
