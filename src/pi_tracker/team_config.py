"""
CLI / env: which MLB franchise the UI follows (schedule, idle hero, pollers).

``run_pi_ui.py yankees --debug-hud`` sets ``BIGA_TEAM_ID`` / ``BIGA_TEAM_ABBR`` /
``BIGA_TEAM_NAME`` before pygame imports. You can also export those env vars yourself.
"""

from __future__ import annotations

import os
import sys
from typing import Final

import requests

# (team_id, abbr, display_name, slug_variants…)
_TEAM_ROWS: Final[list[tuple[int, str, str, tuple[str, ...]]]] = [
    (108, "LAA", "Angels", ("angels", "laa", "la_angels")),
    (109, "AZ", "D-backs", ("diamondbacks", "dbacks", "d-backs", "arizona")),
    (110, "BAL", "Orioles", ("orioles", "baltimore")),
    (111, "BOS", "Red Sox", ("red_sox", "redsox", "boston")),
    (112, "CHC", "Cubs", ("cubs", "chc", "chicago_cubs")),
    (113, "CIN", "Reds", ("reds", "cincinnati")),
    (114, "CLE", "Guardians", ("guardians", "cleveland", "indians")),
    (115, "COL", "Rockies", ("rockies", "colorado")),
    (116, "DET", "Tigers", ("tigers", "detroit")),
    (117, "HOU", "Astros", ("astros", "houston")),
    (118, "KC", "Royals", ("royals", "kansas_city", "kc")),
    (119, "LAD", "Dodgers", ("dodgers", "lad", "la_dodgers", "los_angeles_dodgers")),
    (120, "WSH", "Nationals", ("nationals", "nats", "washington")),
    (121, "NYM", "Mets", ("mets", "nym", "new_york_mets")),
    (133, "ATH", "Athletics", ("athletics", "as", "oakland", "oak")),
    (134, "PIT", "Pirates", ("pirates", "pittsburgh")),
    (135, "SD", "Padres", ("padres", "sdp", "san_diego")),
    (136, "SEA", "Mariners", ("mariners", "seattle")),
    (137, "SF", "Giants", ("giants", "sfg", "san_francisco")),
    (138, "STL", "Cardinals", ("cardinals", "st_louis", "stl")),
    (139, "TB", "Rays", ("rays", "tampa", "tampa_bay")),
    (140, "TEX", "Rangers", ("rangers", "texas")),
    (141, "TOR", "Blue Jays", ("blue_jays", "bluejays", "jays", "toronto")),
    (142, "MIN", "Twins", ("twins", "minnesota")),
    (143, "PHI", "Phillies", ("phillies", "philadelphia")),
    (144, "ATL", "Braves", ("braves", "atlanta")),
    (145, "CWS", "White Sox", ("white_sox", "whitesox", "chicago_white_sox", "chw")),
    (146, "MIA", "Marlins", ("marlins", "florida", "miami")),
    (147, "NYY", "Yankees", ("yankees", "nyy", "new_york_yankees")),
    (158, "MIL", "Brewers", ("brewers", "milwaukee")),
]

_SLUG_TO_TEAM: dict[str, tuple[int, str, str]] = {}
for tid, abbr, name, slugs in _TEAM_ROWS:
    for s in slugs:
        _SLUG_TO_TEAM[s] = (tid, abbr, name)


def resolve_team_slug(raw: str) -> tuple[int, str, str]:
    """
    Return (team_id, abbreviation, short display name).

    Accepts slug aliases (``yankees``), or a numeric MLB ``teamId`` (fetches API once).
    """
    s = raw.strip().lower().replace(" ", "_").replace("-", "_")
    if not s:
        raise SystemExit("Empty team name.")
    if s.isdigit():
        tid = int(s, 10)
        url = f"https://statsapi.mlb.com/api/v1/teams/{tid}"
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        payload = r.json()
        teams = payload.get("teams") or []
        if not teams:
            raise SystemExit(f"No team found for id {tid}.")
        t = teams[0]
        abbr = str(t.get("abbreviation") or "")
        name = str(t.get("teamName") or t.get("name") or "Team")
        return int(t["id"]), abbr, name
    hit = _SLUG_TO_TEAM.get(s)
    if hit:
        return hit
    keys = ", ".join(sorted(_SLUG_TO_TEAM.keys()))
    raise SystemExit(f"Unknown team {raw!r}. Try a slug (e.g. yankees, dodgers) or numeric MLB team id.\n{keys}")


def apply_team_cli_arg() -> None:
    """
    Pop the first non-flag argv token into BIGA_TEAM_* env vars.

    Example: ``python run_pi_ui.py mets --demo`` → team ``mets``, argv left as ``--demo``.
    """
    argv = sys.argv
    out: list[str] = [argv[0]]
    slug: str | None = None
    for i in range(1, len(argv)):
        a = argv[i]
        if a.startswith("-"):
            out.append(a)
            continue
        if slug is None:
            slug = a
        else:
            out.append(a)
    sys.argv[:] = out
    if not slug:
        return
    tid, abbr, name = resolve_team_slug(slug)
    os.environ["BIGA_TEAM_ID"] = str(tid)
    os.environ["BIGA_TEAM_ABBR"] = abbr
    os.environ["BIGA_TEAM_NAME"] = name


def tracked_team_id() -> int:
    try:
        return int(os.environ.get("BIGA_TEAM_ID", "108"), 10)
    except ValueError:
        return 108


def tracked_team_abbr() -> str:
    return os.environ.get("BIGA_TEAM_ABBR", "LAA").strip() or "LAA"


def tracked_team_name() -> str:
    return os.environ.get("BIGA_TEAM_NAME", "Angels").strip() or "Angels"
