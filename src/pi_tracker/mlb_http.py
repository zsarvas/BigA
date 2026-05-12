"""Shared MLB Stats API HTTP helpers."""

from __future__ import annotations

from typing import Any

import requests

BASE_URL = "https://statsapi.mlb.com"
ANGELS_TEAM_ID = 108

LIVE_DETAILED = {"In Progress", "Manager Challenge", "Delayed", "Delay"}
FINAL_DETAILED = {"Final", "Game Over", "Completed Early"}


def api_get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    url = BASE_URL + path
    resp = requests.get(url, params=params or {}, timeout=15)
    resp.raise_for_status()
    return resp.json()


def fetch_live_feed_v11(game_pk: int) -> dict[str, Any]:
    return api_get(f"/api/v1.1/game/{game_pk}/feed/live")
