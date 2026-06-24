"""Away / home logos flanking the final score (win + loss scenes)."""

from __future__ import annotations

from typing import Any

import pygame

from .. import config
from ..assets import AssetManager

_LOGO_SCORE_GAP = config.layout_size(20)
_OUTLINE_PX = 2


def _render_outlined_text(
    font: pygame.font.Font,
    text: str,
    fg: tuple[int, int, int],
    outline: tuple[int, int, int],
    *,
    outline_px: int = _OUTLINE_PX,
) -> pygame.Surface:
    """Render text with a simple offset outline for legibility on busy backgrounds."""
    pad = outline_px * 2
    main = font.render(text, True, fg)
    w, h = main.get_size()
    surf = pygame.Surface((w + pad, h + pad), pygame.SRCALPHA)
    ox, oy = outline_px, outline_px
    for dx in range(-outline_px, outline_px + 1):
        for dy in range(-outline_px, outline_px + 1):
            if dx == 0 and dy == 0:
                continue
            layer = font.render(text, True, outline)
            surf.blit(layer, (ox + dx, oy + dy))
    surf.blit(main, (ox, oy))
    return surf


def draw_score_with_flanking_logos(
    screen: pygame.Surface,
    assets: AssetManager,
    state: dict[str, Any],
    *,
    y_center: int,
    score_color: tuple[int, int, int],
    score_outline: tuple[int, int, int] | None = None,
) -> None:
    ar = int(state.get("away_runs", 0))
    hr = int(state.get("home_runs", 0))
    away_id = int(state.get("away_team_id", 0))
    home_id = int(state.get("home_team_id", 0))
    away_logo = assets.logos.get(away_id)
    home_logo = assets.logos.get(home_id)

    score_s = f"{ar}  —  {hr}"
    if score_outline is not None:
        line = _render_outlined_text(
            assets.font_score, score_s, score_color, score_outline
        )
    else:
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
