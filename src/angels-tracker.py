#!/usr/bin/env python3
"""
Angels Real-Time Game Tracker
Fetches today's Angels game and polls for live updates every 2 minutes.
No API key or registration needed — MLB Stats API is free and open.
"""

import requests
import time
import os
import sys
from datetime import datetime, timezone

BASE_URL = "https://statsapi.mlb.com"
ANGELS_TEAM_ID = 108
REFRESH_SECONDS = 120  # 2 minutes

# Abstract game states
PREVIEW_STATES   = {"Preview", "Pre-Game", "Warmup", "Scheduled"}
LIVE_STATES      = {"In Progress", "Manager Challenge", "Delayed", "Delay"}
FINAL_STATES     = {"Final", "Game Over", "Completed Early"}


# ── Helpers ────────────────────────────────────────────────────────────────────

def clear():
    os.system("cls" if os.name == "nt" else "clear")


def get(path: str, params: dict = None) -> dict:
    url = BASE_URL + path
    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def now_str() -> str:
    return datetime.now().strftime("%H:%M:%S")


# ── Fetch game pk for today ─────────────────────────────────────────────────

def fetch_angels_game(date: str = None) -> dict | None:
    date = date or today()
    data = get("/api/v1/schedule", {
        "sportId": 1,
        "teamId": ANGELS_TEAM_ID,
        "date": date,
        "hydrate": "team,venue,game(content(summary)),linescore,decisions",
    })
    dates = data.get("dates", [])
    if not dates:
        return None
    games = dates[0].get("games", [])
    if not games:
        return None
    return games[0]  # Return first game (could be DH)


# ── Fetch live game feed ─────────────────────────────────────────────────────

def fetch_live_feed(game_pk: int) -> dict:
    return get(f"/api/v1.1/game/{game_pk}/feed/live")


# ── Display helpers ──────────────────────────────────────────────────────────

def divider(char="─", width=60):
    print(char * width)


def format_inning(linescore: dict) -> str:
    inning = linescore.get("currentInning", "?")
    half   = linescore.get("inningHalf", "")
    arrow  = "▲" if half.lower() == "top" else "▼"
    outs   = linescore.get("outs", 0)
    return f"{arrow} {inning}  |  {outs} out{'s' if outs != 1 else ''}"


def format_bases(offense: dict) -> str:
    runners = {
        "first":  "1B",
        "second": "2B",
        "third":  "3B",
    }
    occupied = [label for key, label in runners.items() if offense.get(key)]
    return "Runners on: " + (", ".join(occupied) if occupied else "bases empty")


def print_linescore_table(linescore: dict):
    teams      = linescore.get("teams", {})
    away_info  = teams.get("away", {})
    home_info  = teams.get("home", {})
    innings    = linescore.get("innings", [])

    away_name = away_info.get("team", {}).get("abbreviation", "AWY")
    home_name = home_info.get("team", {}).get("abbreviation", "HME")

    # Header row
    inn_nums = [str(i.get("num", "?")) for i in innings]
    header   = f"{'':5}" + "".join(f"{n:>3}" for n in inn_nums) + f"{'R':>4}{'H':>4}{'E':>4}"
    print(header)
    divider("-", len(header))

    for side, info in [("away", away_info), ("home", home_info)]:
        tag    = away_name if side == "away" else home_name
        row    = f"{tag:<5}"
        for inn in innings:
            runs = inn.get(side, {}).get("runs", "")
            row += f"{str(runs) if runs != '' else '-':>3}"
        r = info.get("runs", 0)
        h = info.get("hits", 0)
        e = info.get("errors", 0)
        row += f"{r:>4}{h:>4}{e:>4}"
        print(row)


def print_decisions(decisions: dict):
    if not decisions:
        return
    for role, label in [("winner", "W"), ("loser", "L"), ("save", "SV")]:
        p = decisions.get(role)
        if p:
            name    = p.get("fullName", "?")
            stats   = p.get("stats", [])
            # stats not always present in schedule hydration; just show name
            print(f"  {label}: {name}")


# ── Main display ─────────────────────────────────────────────────────────────

def display_preview(game: dict):
    gd         = game.get("gameDate", "")
    venue      = game.get("venue", {}).get("name", "?")
    away       = game.get("teams", {}).get("away", {}).get("team", {}).get("name", "Away")
    home       = game.get("teams", {}).get("home", {}).get("team", {}).get("name", "Home")
    status     = game.get("status", {}).get("detailedState", "?")

    # Parse game time to local-ish display
    try:
        from datetime import timezone as tz
        game_dt = datetime.fromisoformat(gd.replace("Z", "+00:00"))
        local_t = game_dt.astimezone().strftime("%I:%M %p %Z")
    except Exception:
        local_t = gd

    clear()
    divider("═")
    print(f"  ⚾  ANGELS GAME TRACKER  —  {today()}")
    divider("═")
    print(f"  {away}  vs  {home}")
    print(f"  🕐  {local_t}  |  📍 {venue}")
    print(f"  Status: {status}")
    divider()
    print(f"  Next refresh in {REFRESH_SECONDS}s  |  Last checked: {now_str()}")
    divider("═")


def display_live(game: dict, feed: dict):
    live_data  = feed.get("liveData", {})
    game_data  = feed.get("gameData", {})
    linescore  = live_data.get("linescore", {})
    plays      = live_data.get("plays", {})
    teams_gd   = game_data.get("teams", {})

    away_name  = teams_gd.get("away", {}).get("abbreviation", "AWY")
    home_name  = teams_gd.get("home", {}).get("abbreviation", "HME")
    venue      = game_data.get("venue", {}).get("name", "?")

    away_runs  = linescore.get("teams", {}).get("away", {}).get("runs", 0)
    home_runs  = linescore.get("teams", {}).get("home", {}).get("runs", 0)

    current    = plays.get("currentPlay", {})
    offense    = linescore.get("offense", {})
    defense    = linescore.get("defense", {})

    # Current pitcher / batter
    batter_id  = offense.get("batter", {}).get("id")
    pitcher_id = defense.get("pitcher", {}).get("id")
    batter     = game_data.get("players", {}).get(f"ID{batter_id}", {}).get("fullName", "?") if batter_id else "?"
    pitcher    = game_data.get("players", {}).get(f"ID{pitcher_id}", {}).get("fullName", "?") if pitcher_id else "?"

    # Last play description
    last_play  = ""
    all_plays  = plays.get("allPlays", [])
    if all_plays:
        last = all_plays[-1]
        last_play = last.get("result", {}).get("description", "")

    # Count
    count = current.get("count", {})
    balls  = count.get("balls", 0)
    strikes = count.get("strikes", 0)

    clear()
    divider("═")
    print(f"  ⚾  ANGELS LIVE  —  {today()}  |  📍 {venue}")
    divider("═")
    print(f"  {away_name}  {away_runs}  —  {home_runs}  {home_name}")
    print(f"  {format_inning(linescore)}")
    divider()
    print_linescore_table(linescore)
    divider()
    print(f"  {format_bases(offense)}")
    print(f"  Count: {balls}-{strikes}")
    print(f"  🏏  Batter:  {batter}")
    print(f"  ⚾  Pitcher: {pitcher}")
    if last_play:
        # Wrap long descriptions
        import textwrap
        wrapped = textwrap.fill(last_play, width=56, initial_indent="  ", subsequent_indent="     ")
        divider()
        print("  Last play:")
        print(wrapped)
    divider()
    print(f"  🔄  Refreshing every {REFRESH_SECONDS}s  |  {now_str()}")
    divider("═")


def display_final(game: dict, feed: dict):
    live_data  = feed.get("liveData", {})
    game_data  = feed.get("gameData", {})
    linescore  = live_data.get("linescore", {})
    decisions  = live_data.get("decisions", {})
    teams_gd   = game_data.get("teams", {})

    away_name  = teams_gd.get("away", {}).get("abbreviation", "AWY")
    home_name  = teams_gd.get("home", {}).get("abbreviation", "HME")
    away_runs  = linescore.get("teams", {}).get("away", {}).get("runs", 0)
    home_runs  = linescore.get("teams", {}).get("home", {}).get("runs", 0)

    winner     = away_name if away_runs > home_runs else home_name

    clear()
    divider("═")
    print(f"  ⚾  ANGELS FINAL  —  {today()}")
    divider("═")
    print(f"  {away_name}  {away_runs}  —  {home_runs}  {home_name}  ✅ FINAL")
    print(f"  Winner: {winner}")
    divider()
    print_linescore_table(linescore)
    divider()
    print("  Decisions:")
    print_decisions(decisions)
    divider("═")


# ── Main loop ────────────────────────────────────────────────────────────────

def run(date: str = None):
    print("⚾  Angels Tracker starting…")

    while True:
        try:
            game = fetch_angels_game(date)

            if game is None:
                clear()
                print(f"  No Angels game found for {date or today()}.")
                print("  Pass a date as argument: python angels_tracker.py 2025-04-15")
                print(f"\n  Checked at {now_str()} — retrying in {REFRESH_SECONDS}s…")
                time.sleep(REFRESH_SECONDS)
                continue

            game_pk = game.get("gamePk")
            state   = game.get("status", {}).get("abstractGameState", "")
            detail  = game.get("status", {}).get("detailedState", "")

            if detail in PREVIEW_STATES or state == "Preview":
                display_preview(game)
                time.sleep(REFRESH_SECONDS)

            elif detail in FINAL_STATES or state == "Final":
                feed = fetch_live_feed(game_pk)
                display_final(game, feed)
                print("\n  Game is final. Exiting.")
                break

            else:
                # Live / in progress
                feed = fetch_live_feed(game_pk)
                display_live(game, feed)
                time.sleep(REFRESH_SECONDS)

        except KeyboardInterrupt:
            print("\n\n  Stopped. Go Halos! 👼")
            sys.exit(0)
        except requests.RequestException as e:
            print(f"\n  ⚠️  Network error: {e}")
            print(f"  Retrying in {REFRESH_SECONDS}s…")
            time.sleep(REFRESH_SECONDS)
        except Exception as e:
            print(f"\n  ⚠️  Unexpected error: {e}")
            time.sleep(REFRESH_SECONDS)


if __name__ == "__main__":
    # Optional: pass a date like: python angels_tracker.py 2025-04-15
    date_arg = sys.argv[1] if len(sys.argv) > 1 else None
    run(date_arg)