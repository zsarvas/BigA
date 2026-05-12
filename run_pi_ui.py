#!/usr/bin/env python3
"""Launch the Pi pygame UI from repo root (adds src/ to import path)."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

# Before pygame: avoid fontconfig fc-list hangs on Pi OS Lite (see embedded_shim).
from pi_tracker.embedded_shim import install_fc_list_stub_if_needed

install_fc_list_stub_if_needed()
os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"

from pi_tracker.app import main

if __name__ == "__main__":
    main()
