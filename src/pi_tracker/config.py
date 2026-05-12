"""
Display constants.

Hardware notes (see BigA_project_notes.txt): SPI panel is 320×480 portrait.
This app targets the same pixel count in landscape: 480×320.
"""

import os
from pathlib import Path

# Repo root (…/BigA) — pi_tracker lives in src/pi_tracker/
REPO_ROOT = Path(__file__).resolve().parent.parent.parent

LOGOS_DIR = REPO_ROOT / "logos"
ASSETS_DIR = Path(__file__).resolve().parent / "assets"

SCREEN_WIDTH = 480
SCREEN_HEIGHT = 320

FPS = 10

BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
GRAY = (140, 140, 140)
GREEN_FIELD = (34, 139, 34)
DIRT = (139, 90, 43)
BASE_EMPTY = (255, 255, 255)
BASE_OCCUPIED = (255, 200, 0)
ANGELS_GOLD = (186, 147, 62)

# Logo tile size for header row (landscape)
LOGO_HEADER_SIZE = (72, 72)

# Idle fullscreen clips: mpv plays between random intervals while scene is idle.
# Override with env BIGA_IDLE_VID_MIN / BIGA_IDLE_VID_MAX (seconds).
IDLE_VIDEO_MIN_INTERVAL_SEC = float(os.environ.get("BIGA_IDLE_VID_MIN", "300"))
IDLE_VIDEO_MAX_INTERVAL_SEC = float(os.environ.get("BIGA_IDLE_VID_MAX", "1200"))

# mpv video output: drm on Pi KMS; gpu (or libmpv) for desktop dev.
IDLE_MPV_VO = os.environ.get("BIGA_MPV_VO", "").strip()
