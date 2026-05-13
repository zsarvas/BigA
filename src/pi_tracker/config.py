"""
Display constants.

Default window size is **480×320** (original Pi layout). Waveshare 2.8" portrait is **480×640**;
set ``BIGA_SCREEN_HEIGHT=640`` (and optionally ``BIGA_SCREEN_WIDTH=480``) for that panel.

Scene Y positions use ``layout_y`` so they scale when height is not 320.
"""

import os
from pathlib import Path

# Repo root (…/BigA) — pi_tracker lives in src/pi_tracker/
REPO_ROOT = Path(__file__).resolve().parent.parent.parent

LOGOS_DIR = REPO_ROOT / "logos"
ASSETS_DIR = Path(__file__).resolve().parent / "assets"


def _env_positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        v = int(raw, 10)
        return v if v > 0 else default
    except ValueError:
        return default


SCREEN_WIDTH = _env_positive_int("BIGA_SCREEN_WIDTH", 480)
SCREEN_HEIGHT = _env_positive_int("BIGA_SCREEN_HEIGHT", 320)

# Original pygame layout was tuned for this height (480×320 landscape).
LAYOUT_REF_HEIGHT = 320


def layout_y(y_for_ref: int) -> int:
    """Map a Y coordinate from the 480×320 reference layout onto the current screen height."""
    return int(round(y_for_ref * SCREEN_HEIGHT / LAYOUT_REF_HEIGHT))

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

# Seconds between fullscreen highlight (mpv) on idle / win / loss. Not used during live.
IDLE_HIGHLIGHT_INTERVAL_SEC = 600  # 10 minutes

# mpv video output: drm on Pi KMS; gpu (or libmpv) for desktop dev.
IDLE_MPV_VO = os.environ.get("BIGA_MPV_VO", "").strip()
