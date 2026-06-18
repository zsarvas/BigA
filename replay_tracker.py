#!/usr/bin/env python3
"""Track MLB highlight publish latency vs play-by-play end times (Angels games)."""

from __future__ import annotations

import json
import re
import time
from datetime import date, datetime, timezone

import requests

ANGELS_TEAM_ID = 108
LOG_PATH = "/tmp/replay_latency.json"
POLL_SEC = 60

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}

_MLB_BLOCK_HINT = (
    "MLB API returned 406 — common on cloud VPS IPs (DigitalOcean, AWS, etc.). "
    "Run this script on the Pi or a home machine instead; residential IPs work."
)

seen_plays: dict[int | str, dict] = {}
seen_videos: dict[str, dict] = {}
matched_pairs: list[dict] = []
current_pk: int | None = None


def ts() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S UTC")


def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (text or "").lower())


def parse_api_dt(raw: str | None) -> datetime | None:
    """Parse MLB ISO timestamps (often ending in Z)."""
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def api_get(url: str) -> dict:
    resp = requests.get(url, headers=HEADERS, timeout=30)
    if resp.status_code == 406:
        raise requests.HTTPError(_MLB_BLOCK_HINT, response=resp)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        raise ValueError(f"unexpected JSON from {url}")
    if "error" in data and "dates" not in data and "allPlays" not in data:
        raise ValueError(f"MLB API error from {url}: {data.get('error')}")
    return data


def _game_has_angels(game: dict) -> bool:
    teams = game.get("teams") or {}
    for side in ("away", "home"):
        tid = (teams.get(side) or {}).get("team", {}).get("id")
        if tid == ANGELS_TEAM_ID:
            return True
    return False


def get_todays_angels_game() -> int | None:
    today = date.today().isoformat()
    data = api_get(
        "https://statsapi.mlb.com/api/v1/schedule"
        f"?teamId={ANGELS_TEAM_ID}&sportId=1&startDate={today}&endDate={today}"
    )
    dates = data.get("dates") or []
    if not dates:
        print(f"[{ts()}] No Angels game today")
        return None

    games = [g for g in dates[0].get("games", []) if _game_has_angels(g)]
    if not games:
        print(f"[{ts()}] No Angels game today")
        return None

    def pick_key(g: dict) -> tuple[int, int]:
        st = g.get("status") or {}
        abstract = st.get("abstractGameState") or ""
        if abstract == "Live":
            rank = 0
        elif abstract == "Preview":
            rank = 1
        elif abstract == "Final":
            rank = 3
        else:
            rank = 2
        return (rank, int(g.get("gamePk") or 0))

    game = min(games, key=pick_key)
    pk = int(game["gamePk"])
    status = (game.get("status") or {}).get("detailedState", "?")
    away = game["teams"]["away"]["team"]["name"]
    home = game["teams"]["home"]["team"]["name"]
    print(f"[{ts()}] Game PK: {pk} | {away} @ {home} | {status}")
    return pk


def reset_game_state() -> None:
    global seen_plays, seen_videos, matched_pairs
    seen_plays = {}
    seen_videos = {}
    matched_pairs = []


def score_match(blurb_norm: str, play: dict) -> int:
    event_norm = normalize(play["event"])
    desc_norm = normalize(play["description"])

    blurb_words = [w for w in blurb_norm.split() if len(w) > 2]
    score = sum(1 for w in blurb_words if w in desc_norm or w in event_norm)

    event_map = {
        "home run": ["home run", "homer", "grand slam"],
        "strikeout": ["strikes out", "strikeout", "fans"],
        "double": ["double"],
        "triple": ["triple"],
        "single": ["single"],
    }
    for event_key, terms in event_map.items():
        if event_key in event_norm and any(t in blurb_norm for t in terms):
            score += 3

    return score


def try_match_video(vid_id: str, blurb: str) -> bool:
    """Match one highlight to a play; latency uses API timestamps only."""
    if seen_videos.get(vid_id, {}).get("matched_play_id") is not None:
        return False

    blurb_norm = normalize(blurb)
    best_match: int | str | None = None
    best_score = 0

    for play_id, play in seen_plays.items():
        if play.get("matched_video"):
            continue
        score = score_match(blurb_norm, play)
        if score > best_score:
            best_score = score
            best_match = play_id

    if best_match is None or best_score < 2:
        return False

    play = seen_plays[best_match]
    video = seen_videos[vid_id]
    play_end = parse_api_dt(play.get("end_time"))
    vid_pub = parse_api_dt(video.get("api_date"))

    if not play_end or not vid_pub:
        print(
            f"[{ts()}] MATCH skipped [{best_match}] — missing API time "
            f"(play_end={play.get('end_time')!r}, video_date={video.get('api_date')!r})"
        )
        return False

    delta_min = (vid_pub - play_end).total_seconds() / 60.0
    play["matched_video"] = vid_id
    video["matched_play_id"] = best_match

    pair = {
        "game_pk": current_pk,
        "play_id": best_match,
        "play_uuid": play.get("play_uuid"),
        "event": play["event"],
        "description": play["description"][:60],
        "inning": play["inning"],
        "half": play["half"],
        "blurb": blurb,
        "play_end_time": play["end_time"],
        "video_api_date": video["api_date"],
        "video_first_seen_at": video.get("first_seen_at"),
        "latency_minutes": round(delta_min, 1),
        "latency_source": "api",
    }
    matched_pairs.append(pair)
    print(
        f"[{ts()}] MATCH [{best_match}] '{play['event']}' → '{blurb}' | "
        f"latency: {delta_min:.1f} min (API)"
    )
    save_log()
    return True


def retry_unmatched_videos() -> None:
    """Re-attempt matching when new plays arrive after highlights."""
    for vid_id, video in seen_videos.items():
        if video.get("matched_play_id") is not None:
            continue
        try_match_video(vid_id, video.get("blurb") or "")


def check_plays() -> None:
    data = api_get(f"https://statsapi.mlb.com/api/v1/game/{current_pk}/playByPlay")
    for play in data.get("allPlays") or []:
        result = play.get("result") or {}
        about = play.get("about") or {}
        if not about.get("isComplete") or not result.get("event"):
            continue

        play_id = about.get("atBatIndex")
        if play_id in seen_plays:
            continue

        end_time = about.get("endTime") or about.get("startTime")
        seen_plays[play_id] = {
            "play_uuid": play.get("playId"),
            "event": result.get("event"),
            "description": result.get("description", ""),
            "inning": about.get("inning"),
            "half": about.get("halfInning"),
            "end_time": end_time,
            "first_seen_at": datetime.now(timezone.utc).isoformat(),
            "matched_video": None,
        }
        print(
            f"[{ts()}] PLAY  [{play_id}] "
            f"I{about.get('inning')}/{about.get('halfInning')} "
            f"{result.get('event')} end={end_time}"
        )

    retry_unmatched_videos()


def check_videos() -> None:
    data = api_get(f"https://statsapi.mlb.com/api/v1/game/{current_pk}/content")
    items = (
        (data.get("highlights") or {})
        .get("highlights") or {}
    ).get("items") or []

    for h in items:
        if h.get("type") != "video":
            continue
        vid_id = h.get("id")
        if not vid_id or vid_id in seen_videos:
            continue

        seen_videos[vid_id] = {
            "blurb": h.get("blurb"),
            "api_date": h.get("date"),
            "first_seen_at": datetime.now(timezone.utc).isoformat(),
            "matched_play_id": None,
        }
        print(f"[{ts()}] VIDEO '{h.get('blurb')}' date={h.get('date')}")
        try_match_video(vid_id, h.get("blurb") or "")


def save_log() -> None:
    latencies = [p["latency_minutes"] for p in matched_pairs if p.get("latency_source") == "api"]
    latencies_sorted = sorted(latencies)
    n = len(latencies_sorted)
    avg = round(sum(latencies_sorted) / n, 1) if n else None
    med = latencies_sorted[n // 2] if n else None
    p90 = latencies_sorted[int(n * 0.9)] if n else None
    summary = {
        "match_count": n,
        "avg_latency_minutes": avg,
        "median_latency_minutes": med,
        "p90_latency_minutes": p90,
        "under_2_min": sum(1 for x in latencies if x < 2),
        "under_5_min": sum(1 for x in latencies if x < 5),
        "over_8_min": sum(1 for x in latencies if x > 8),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(
            {
                "current_game_pk": current_pk,
                "latency_note": "latency_minutes = video api_date − play about.endTime",
                "summary": summary,
                "plays": seen_plays,
                "videos": seen_videos,
                "matched_pairs": matched_pairs,
                "avg_latency_minutes": avg,
                "match_count": n,
            },
            f,
            indent=2,
        )


def main() -> None:
    global current_pk

    print("Starting Angels replay latency tracker...")
    print(f"Logs: {LOG_PATH}")
    print("Latency = highlight publish time − play end time (MLB API timestamps)\n")
    save_log()  # create file immediately so off-days / fresh starts still have a log

    while True:
        try:
            todays_pk = get_todays_angels_game()

            if todays_pk is None:
                if current_pk is not None:
                    print(f"[{ts()}] No game today — clearing state (was pk {current_pk})")
                    current_pk = None
                    reset_game_state()
                save_log()
            elif todays_pk != current_pk:
                current_pk = todays_pk
                reset_game_state()
                print(f"[{ts()}] New game detected: {current_pk}\n")

            if current_pk:
                check_plays()
                check_videos()
                save_log()

        except requests.RequestException as exc:
            print(f"[{ts()}] HTTP error: {exc}")
        except Exception as exc:
            print(f"[{ts()}] Error: {exc}")

        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
