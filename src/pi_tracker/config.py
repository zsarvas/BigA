"""
Display constants.

**Window size** is whatever you set with ``BIGA_SCREEN_WIDTH`` and ``BIGA_SCREEN_HEIGHT`` (defaults
in this file are for local dev only). Change those env vars when the target panel resolution is
final; no code edits are required for a new resolution.

**Vertical layout:** scene code uses small integers (tuned on a short landscape panel) and
``layout_y()`` maps them onto the current height using ``LAYOUT_REF_HEIGHT`` — that reference is
*not* the same as the default window height; it is only the baseline for proportional Y scaling.
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


SCREEN_WIDTH = _env_positive_int("BIGA_SCREEN_WIDTH", 640)
SCREEN_HEIGHT = _env_positive_int("BIGA_SCREEN_HEIGHT", 480)

# Original pygame layout was tuned for this height (480×320 landscape).
LAYOUT_REF_HEIGHT = 320


def layout_y(y_for_ref: int) -> int:
    """Map a Y coordinate from the ``LAYOUT_REF_HEIGHT`` reference frame onto the current height."""
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

