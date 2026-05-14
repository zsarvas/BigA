"""Shared MLB Stats API HTTP helpers."""

from __future__ import annotations

import os
from typing import Any

import requests

BASE_URL = "https://statsapi.mlb.com"


def _tracked_team_id_from_env() -> int:
    try:
        return int(os.environ.get("BIGA_TEAM_ID", "108"), 10)
    except ValueError:
        return 108


# Tracked franchise (default Angels). Set BIGA_TEAM_ID before importing this module,
# or use ``run_pi_ui.py <team_slug> …`` (see team_config.apply_team_cli_arg).
ANGELS_TEAM_ID = _tracked_team_id_from_env()

LIVE_DETAILED = {"In Progress", "Manager Challenge", "Delayed", "Delay"}
FINAL_DETAILED = {"Final", "Game Over", "Completed Early"}


def api_get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    url = BASE_URL + path
    resp = requests.get(url, params=params or {}, timeout=15)
    resp.raise_for_status()
    return resp.json()


def fetch_live_feed_v11(game_pk: int) -> dict[str, Any]:
    return api_get(f"/api/v1.1/game/{game_pk}/feed/live")
