#!/usr/bin/env python3
"""
Preview the AP setup screen on a Mac or Linux desktop (480×320 window).

Usage:
  python3 scripts/preview_setup_screen.py
  BIGA_AP_SSID=BigA-AB12 python3 scripts/preview_setup_screen.py

Requires: pygame, qrcode, pillow (pip install pygame qrcode pillow)
Press Escape or close the window to quit.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PORTAL = REPO / "portal"

os.environ["BIGA_SETUP_PREVIEW"] = "1"
os.environ.setdefault("BIGA_AP_SSID", "BigA-DEMO")

sys.path.insert(0, str(PORTAL))

from setup_screen import main  # noqa: E402

if __name__ == "__main__":
    main()
