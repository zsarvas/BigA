from __future__ import annotations

from typing import Any

import pygame

from .. import config
from ..assets import AssetManager
from .final_score_row import draw_score_with_flanking_logos
from .linescore_table import draw_linescore_table_centered


class FinalWinScene:
    def draw(self, screen: pygame.Surface, assets: AssetManager, state: dict[str, Any]) -> None:
        screen.fill((12, 40, 12))
        draw_score_with_flanking_logos(
            screen, assets, state, y_center=config.layout_y(88), score_color=config.ANGELS_GOLD
        )

        w = assets.font_title.render("HALOS WIN", True, config.WHITE)
        screen.blit(w, w.get_rect(center=(config.SCREEN_WIDTH // 2, config.layout_y(148))))

        tbl_y = config.layout_y(176)
        h = draw_linescore_table_centered(
            screen,
            assets,
            state,
            config.SCREEN_WIDTH // 2,
            tbl_y,
            fg=config.WHITE,
            hdr=(160, 200, 160),
        )

        sub = assets.font_small.render("(GPIO LED pulse hooks here later)", True, config.GRAY)
        cy = min(
            tbl_y + h + 8 + sub.get_height() // 2,
            config.SCREEN_HEIGHT - sub.get_height() // 2 - 4,
        )
        screen.blit(sub, sub.get_rect(center=(config.SCREEN_WIDTH // 2, cy)))
