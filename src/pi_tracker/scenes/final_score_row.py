"""Away / home logos flanking the final score (win + loss scenes)."""

from __future__ import annotations

from typing import Any

import pygame

from .. import config
from ..assets import AssetManager

_LOGO_SCORE_GAP = 32


def draw_score_with_flanking_logos(
    screen: pygame.Surface,
    assets: AssetManager,
    state: dict[str, Any],
    *,
    y_center: int,
    score_color: tuple[int, int, int],
) -> None:
    ar = int(state.get("away_runs", 0))
    hr = int(state.get("home_runs", 0))
    away_id = int(state.get("away_team_id", 0))
    home_id = int(state.get("home_team_id", 0))
    away_logo = assets.logos.get(away_id)
    home_logo = assets.logos.get(home_id)

    score_s = f"{ar}  —  {hr}"
    line = assets.font_score.render(score_s, True, score_color)
    lw, lh = line.get_size()
    aw, ah = (away_logo.get_size() if away_logo else (0, 0))
    hw, hh = (home_logo.get_size() if home_logo else (0, 0))

    left_w = (aw + _LOGO_SCORE_GAP) if away_logo else 0
    right_w = (_LOGO_SCORE_GAP + hw) if home_logo else 0
    total_w = left_w + lw + right_w
    x = (config.SCREEN_WIDTH - total_w) // 2

    if away_logo:
        screen.blit(away_logo, (x, y_center - ah // 2))
    x_line = x + left_w
    screen.blit(line, (x_line, y_center - lh // 2))
    if home_logo:
        x_home = x_line + lw + _LOGO_SCORE_GAP
        screen.blit(home_logo, (x_home, y_center - hh // 2))
