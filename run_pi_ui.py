#!/usr/bin/env python3
"""Launch the Pi pygame UI from repo root (adds src/ to import path)."""

import os
import sys
from pathlib import Path

# Before pygame is imported (via pi_tracker.app).
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from pi_tracker.app import main

if __name__ == "__main__":
    main()
