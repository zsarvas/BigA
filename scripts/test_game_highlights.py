#!/usr/bin/env python3
"""
Game highlights test — fetches clips for a recent Angels game, downloads them,
then plays them back via mpv.

Usage
-----
    # Auto-detect most recent Angels game with highlights
    python3 scripts/test_game_highlights.py

    # Supply a specific game_pk
    python3 scripts/test_game_highlights.py 825071

    # Download only (no playback, useful over SSH)
    python3 scripts/test_game_highlights.py --download-only

Controls (standard mpv bindings)
----------------------------------
    SPACE   pause/resume
    Q / ESC quit clip, advance to next
    →       skip forward 10 s

Clips are saved to assets/highlights/<game_pk>/ and skipped on re-run if already
on disk, so you can re-run the script to test playback without re-downloading.

Requires mpv:
    Pi:  sudo apt install mpv
    Mac: brew install mpv
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from pi_tracker import config  # noqa: E402
from pi_tracker.mlb_highlights import (  # noqa: E402
    fetch_highlight_clips,
    game_highlights_dir,
)


def _find_recent_game_pk() -> int | None:
    """Return game_pk for the most recent finished Angels game with highlights."""
    import datetime
    import requests

    today = datetime.date.today()
    start = today - datetime.timedelta(days=7)
    url = (
        "https://statsapi.mlb.com/api/v1/schedule"
        f"?teamId=108&sportId=1&startDate={start}&endDate={today}"
    )
    try:
        data = requests.get(url, timeout=10).json()
    except Exception as e:
        print(f"Schedule fetch failed: {e}")
        return None

    pks = [
        game["gamePk"]
        for entry in data.get("dates", [])
        for game in entry.get("games", [])
        if game.get("status", {}).get("abstractGameState") == "Final"
    ]

    for pk in reversed(pks):
        clips = fetch_highlight_clips(pk)
        if clips:
            print(f"Found {len(clips)} highlights for game_pk {pk}")
            return pk
    return None


def _download(game_pk: int) -> list[Path]:
    """Download all play clips for game_pk. Returns list of local paths."""
    import requests

    dest = game_highlights_dir(game_pk)
    clips = fetch_highlight_clips(game_pk)
    if not clips:
        print(f"No play clips found for game_pk {game_pk}.")
        return []

    print(f"\n{len(clips)} clip(s) to download → {dest}\n")
    paths: list[Path] = []
    for i, clip in enumerate(clips, 1):
        already = dest / f"{clip['id']}.mp4"
        if already.exists():
            print(f"  [{i}/{len(clips)}] already on disk: {clip['blurb']}")
            paths.append(already)
            continue
        print(f"  [{i}/{len(clips)}] {clip['blurb']}")
        try:
            r = requests.get(clip["url"], stream=True, timeout=60)
            r.raise_for_status()
            tmp = already.with_suffix(".tmp")
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
            tmp.rename(already)
            size_mb = already.stat().st_size / 1_048_576
            print(f"         {size_mb:.1f} MB  ✓")
            paths.append(already)
        except Exception as e:
            print(f"         FAILED: {e}")

    return paths


def _play_all(paths: list[Path]) -> None:
    print(f"\nPlaying {len(paths)} clip(s) — SPACE=pause  Q=next  ESC=quit all\n")
    for i, path in enumerate(paths, 1):
        print(f"[{i}/{len(paths)}] {path.name}")
        result = subprocess.run(
            ["mpv", "--hwdec=auto", "--really-quiet", "--fs", "--panscan=1.0", str(path)]
        )
        if result.returncode == 2:  # mpv hard-quit (e.g. window close)
            break


def main() -> None:
    parser = argparse.ArgumentParser(description="Test game highlight download + playback.")
    parser.add_argument("game_pk", nargs="?", type=int, help="MLB game_pk (auto-detects if omitted)")
    parser.add_argument("--download-only", action="store_true", help="Skip playback after download")
    args = parser.parse_args()

    game_pk = args.game_pk
    if game_pk is None:
        print("No game_pk supplied — finding most recent Angels game with highlights…")
        game_pk = _find_recent_game_pk()
        if game_pk is None:
            print("Could not find a recent finished game. Supply a game_pk manually.")
            sys.exit(1)

    paths = _download(game_pk)
    if not paths:
        sys.exit(1)

    if args.download_only:
        print(f"\nDownload complete. {len(paths)} clip(s) in {game_highlights_dir(game_pk)}")
        return

    _play_all(paths)


if __name__ == "__main__":
    main()
