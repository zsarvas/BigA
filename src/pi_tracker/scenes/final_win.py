from __future__ import annotations

from typing import Any

import pygame

from .. import config
from ..assets import AssetManager


class FinalWinScene:
    def draw(self, screen: pygame.Surface, assets: AssetManager, state: dict[str, Any]) -> None:
        screen.fill((12, 40, 12))
        ar = int(state.get("away_runs", 0))
        hr = int(state.get("home_runs", 0))
        line = assets.font_score.render(f"{ar}  —  {hr}", True, config.ANGELS_GOLD)
        screen.blit(line, line.get_rect(center=(config.SCREEN_WIDTH // 2, 120)))

        w = assets.font_title.render("HALOS WIN", True, config.WHITE)
        screen.blit(w, w.get_rect(center=(config.SCREEN_WIDTH // 2, 200)))

        sub = assets.font_small.render("(GPIO LED pulse hooks here later)", True, config.GRAY)
        screen.blit(sub, sub.get_rect(center=(config.SCREEN_WIDTH // 2, 260)))
