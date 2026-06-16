from __future__ import annotations

from typing import Any

import pygame

from .. import config
from ..assets import AssetManager
from .final_score_row import draw_score_with_flanking_logos
from .linescore_table import draw_linescore_table_centered
from ._clip_player import ClipPlayerMixin
from .final_win import _game_clip_folder


class FinalLossScene(ClipPlayerMixin):
    def draw(self, screen: pygame.Surface, assets: AssetManager, state: dict[str, Any]) -> None:
        if self._cp_maybe_play(screen, _game_clip_folder(state)):
            return
        screen.fill((18, 18, 22))
        draw_score_with_flanking_logos(
            screen, assets, state, y_center=config.layout_y(76), score_color=config.GRAY
        )

        w = assets.font_title.render("FINAL", True, config.WHITE)
        screen.blit(w, w.get_rect(center=(config.SCREEN_WIDTH // 2, config.layout_y(128))))

        draw_linescore_table_centered(
            screen,
            assets,
            state,
            config.SCREEN_WIDTH // 2,
            config.layout_y(152),
            fg=config.WHITE,
            hdr=config.GRAY,
            cell_pad=2,
            table_font=assets.font_linescore,
        )
