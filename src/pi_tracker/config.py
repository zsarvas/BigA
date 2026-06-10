"""
Display constants.

**Window size** defaults to **480×320** (landscape panel). Override with ``BIGA_SCREEN_WIDTH`` /
``BIGA_SCREEN_HEIGHT`` when needed.

**Layout:** scenes use reference coordinates for 480×320; ``layout_x`` / ``layout_y`` / ``layout_size``
scale to the current resolution.
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


def _env_float(name: str, default: float, *, lo: float, hi: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        v = float(raw)
    except ValueError:
        return default
    return max(lo, min(hi, v))


def _env_clamp_int(name: str, default: int, *, lo: int, hi: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        v = int(raw, 10)
    except ValueError:
        return default
    return max(lo, min(hi, v))


SCREEN_WIDTH = _env_positive_int("BIGA_SCREEN_WIDTH", 480)
SCREEN_HEIGHT = _env_positive_int("BIGA_SCREEN_HEIGHT", 320)

# Base UI frame rate (kept low to save CPU on the Pi Zero).
FPS = 10

# Layout reference: 480×320 landscape (Waveshare / Pi Zero panel).
LAYOUT_REF_WIDTH = 480
LAYOUT_REF_HEIGHT = 320

# Global readability multiplier applied to every sized element (fonts, logos,
# the linescore table, size-based gaps) via ``layout_size``. Positions
# (``layout_x`` / ``layout_y``) are unaffected, so elements grow in place.
# Tune with BIGA_UI_SCALE (e.g. 1.0 = original, 1.25 = bigger). Clamped 0.6–2.0.
UI_SCALE = _env_float("BIGA_UI_SCALE", 1.15, lo=0.6, hi=2.0)

# Extra multiplier applied ONLY to the linescore table (on top of UI_SCALE).
# The table sizes itself from its font, so this grows cells + rows together.
# Tune with BIGA_LINESCORE_SCALE. Clamped 0.6–2.5.
LINESCORE_SCALE = _env_float("BIGA_LINESCORE_SCALE", 1.3, lo=0.6, hi=2.5)

# Background image (in assets/) drawn behind scenes instead of a flat fill.
# Empty string disables it (falls back to BLACK). BIGA_BG_DIM is a 0–255 black
# scrim drawn over the image to keep text readable (higher = darker/easier).
BG_IMAGE = os.environ.get("BIGA_BG_IMAGE", "stadium.jpg").strip()
BG_DIM = _env_clamp_int("BIGA_BG_DIM", 130, lo=0, hi=255)

# Highlight clip streamed full-screen during the idle scene at random intervals
# (decoded frame-by-frame to keep RAM low — see assets.StreamingGif). Empty string
# disables it. Plays roughly HIGHLIGHT_MIN..MAX minutes apart (default 20–30 → ~2–3/hr).
HIGHLIGHT_GIF = os.environ.get("BIGA_HIGHLIGHT_GIF", "highlight.gif").strip()
HIGHLIGHT_MIN_GAP_MIN = _env_clamp_int("BIGA_HIGHLIGHT_MIN_MIN", 20, lo=1, hi=720)
HIGHLIGHT_MAX_GAP_MIN = _env_clamp_int("BIGA_HIGHLIGHT_MAX_MIN", 30, lo=1, hi=720)
# Frame rate while a highlight clip is streaming. The rest of the UI runs at FPS
# (10) to save CPU; the clip is ~12 fps, so the loop must tick faster than that
# during playback or it looks slow/choppy. Tune with BIGA_HIGHLIGHT_FPS.
HIGHLIGHT_FPS = _env_clamp_int("BIGA_HIGHLIGHT_FPS", 24, lo=FPS, hi=60)


def layout_scale() -> float:
    """Uniform scale vs. the 480×320 reference panel."""
    return min(SCREEN_WIDTH / LAYOUT_REF_WIDTH, SCREEN_HEIGHT / LAYOUT_REF_HEIGHT)


def layout_size(base: int) -> int:
    """Scale a pixel size (logo, font, margin) for the current resolution.

    Includes the global ``UI_SCALE`` readability multiplier.
    """
    return max(1, int(round(base * layout_scale() * UI_SCALE)))


def layout_x(x_for_ref: int) -> int:
    """Map an X coordinate from the reference width onto the current width."""
    return int(round(x_for_ref * SCREEN_WIDTH / LAYOUT_REF_WIDTH))


def layout_y(y_for_ref: int) -> int:
    """Map a Y coordinate from the reference height onto the current height."""
    return int(round(y_for_ref * SCREEN_HEIGHT / LAYOUT_REF_HEIGHT))


# Logo tile for header / final rows (scaled from 56px on 480×320).
LOGO_HEADER_SIZE = (layout_size(56), layout_size(56))

BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
GRAY = (140, 140, 140)
GREEN_FIELD = (34, 139, 34)
DIRT = (139, 90, 43)
BASE_EMPTY = (255, 255, 255)
BASE_OCCUPIED = (255, 200, 0)
ANGELS_GOLD = (186, 147, 62)

