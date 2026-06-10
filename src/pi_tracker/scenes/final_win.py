from __future__ import annotations

from typing import Any

import pygame

from .. import config
from ..assets import AssetManager
from ..mlb_http import ANGELS_TEAM_ID as TRACKED_TEAM_ID
from .final_score_row import draw_score_with_flanking_logos
from .linescore_table import draw_linescore_table_centered


class FinalWinScene:
    def draw(self, screen: pygame.Surface, assets: AssetManager, state: dict[str, Any]) -> None:
        assets.draw_gif_background(screen, "win.gif", pygame.time.get_ticks(), fallback=(12, 40, 12))
        draw_score_with_flanking_logos(
            screen, assets, state, y_center=config.layout_y(72), score_color=config.ANGELS_GOLD
        )

        ar = int(state.get("away_runs", 0))
        hr = int(state.get("home_runs", 0))
        away_abbr = str(state.get("away_abbr", "AWY"))
        home_abbr = str(state.get("home_abbr", "HME"))
        away_id = int(state.get("away_team_id", 0))
        home_id = int(state.get("home_team_id", 0))
        if hr > ar:
            win_abbr, win_id = home_abbr, home_id
        elif ar > hr:
            win_abbr, win_id = away_abbr, away_id
        else:
            headline = "WIN"
        if hr != ar:
            if win_id == 108 and TRACKED_TEAM_ID == 108:
                headline = "HALOS WIN"
            else:
                headline = f"{win_abbr} WIN"

        w = assets.font_title.render(headline, True, config.WHITE)
        screen.blit(w, w.get_rect(center=(config.SCREEN_WIDTH // 2, config.layout_y(128))))

        draw_linescore_table_centered(
            screen,
            assets,
            state,
            config.SCREEN_WIDTH // 2,
            config.layout_y(152),
            fg=config.WHITE,
            hdr=(160, 200, 160),
            cell_pad=2,
            table_font=assets.font_linescore,
        )
