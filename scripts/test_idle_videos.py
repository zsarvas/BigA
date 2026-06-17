#!/usr/bin/env python3
"""
Idle highlight clip viewer — plays every .gif/.mp4 in assets/idle_videos/ via mpv.

Usage
-----
    python3 scripts/test_highlights.py

Controls (standard mpv bindings)
----------------------------------
    SPACE   pause/resume
    Q / ESC quit
    →       skip forward 10 s
    ←       skip back 10 s

Requires mpv to be installed:
    Pi:  sudo apt install mpv
    Mac: brew install mpv
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from pi_tracker import config  # noqa: E402

EXTS = {".gif", ".mp4", ".mov", ".avi", ".mkv"}


def main() -> None:
    folder = config.IDLE_VIDEOS_DIR
    clips = sorted(p for p in folder.iterdir() if p.suffix.lower() in EXTS)
    if not clips:
        print(f"No clips found in {folder}")
        sys.exit(1)

    print(f"Playing {len(clips)} clip(s) from {folder}")
    print("Controls: SPACE=pause  Q=quit  ←/→=seek\n")

    for i, clip in enumerate(clips, 1):
        print(f"[{i}/{len(clips)}] {clip.name}")
        result = subprocess.run(
            [
                "mpv", "--hwdec=auto", "--really-quiet", "--fs", "--panscan=1.0", "--osd-level=0",
                "--osd-font-size=28",
                str(clip),
            ]
        )
        if result.returncode not in (0, 4):  # 4 = quit by user
            print(f"  mpv exited with code {result.returncode}")


if __name__ == "__main__":
    main()
