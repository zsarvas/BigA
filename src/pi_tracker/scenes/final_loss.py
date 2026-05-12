from __future__ import annotations

from typing import Any

import pygame

from .. import config
from ..assets import AssetManager


class FinalLossScene:
    def draw(self, screen: pygame.Surface, assets: AssetManager, state: dict[str, Any]) -> None:
        screen.fill((18, 18, 22))
        ar = int(state.get("away_runs", 0))
        hr = int(state.get("home_runs", 0))
        line = assets.font_score.render(f"{ar}  —  {hr}", True, config.GRAY)
        screen.blit(line, line.get_rect(center=(config.SCREEN_WIDTH // 2, 130)))

        w = assets.font_title.render("FINAL", True, config.WHITE)
        screen.blit(w, w.get_rect(center=(config.SCREEN_WIDTH // 2, 200)))
