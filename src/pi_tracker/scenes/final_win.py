from __future__ import annotations

from typing import Any

import pygame

from .. import config
from ..assets import AssetManager, animation_ms
from ..mlb_http import ANGELS_TEAM_ID as TRACKED_TEAM_ID
from ..mlb_highlights import game_folder_has_playable_clips, is_likely_playable_game_clip
from .final_score_row import draw_score_with_flanking_logos
from .linescore_table import draw_linescore_table_centered
from ._clip_player import ClipPlayerMixin


def _game_clip_folder(state: dict) -> "Path | None":
    """
    Game recap folder for win/loss scenes only — never the idle reel.

    Uses a fast size-based check (no ffprobe) so the main draw loop stays smooth.
    """
    from pathlib import Path  # noqa: PLC0415

    pk = state.get("live_game_pk")
    if pk:
        folder = config.GAME_HIGHLIGHTS_DIR / str(pk)
        if game_folder_has_playable_clips(folder):
            return folder

    # Fallback: any game subfolder with finished clips (pk mismatch edge case).
    if config.GAME_HIGHLIGHTS_DIR.is_dir():
        best: Path | None = None
        best_n = 0
        for sub in config.GAME_HIGHLIGHTS_DIR.iterdir():
            if not sub.is_dir():
                continue
            n = sum(1 for p in sub.glob("*.mp4") if is_likely_playable_game_clip(p))
            if n > best_n:
                best_n = n
                best = sub
        if best:
            return best
    return None


class FinalWinScene(ClipPlayerMixin):
    def draw(self, screen: pygame.Surface, assets: AssetManager, state: dict[str, Any]) -> None:
        self._cp_tick(
            _game_clip_folder(state),
            gap_min=config.GAME_HIGHLIGHT_GAP_MIN,
            prefer_condensed=True,
            allow_during_transcode=True,
        )
        assets.draw_gif_background(screen, "win.gif", animation_ms(), fallback=(12, 40, 12))
        draw_score_with_flanking_logos(
            screen,
            assets,
            state,
            y_center=config.layout_y(72),
            score_color=config.WHITE,
            score_outline=(0, 0, 0),
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

        table_top = config.layout_y(152) + 2 * (assets.font_linescore.get_height() + 2)
        draw_linescore_table_centered(
            screen,
            assets,
            state,
            config.SCREEN_WIDTH // 2,
            table_top,
            fg=config.WHITE,
            hdr=(160, 200, 160),
            cell_pad=2,
            table_font=assets.font_linescore,
        )
