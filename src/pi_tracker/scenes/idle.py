from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

import pygame

from .. import config
from ..assets import AssetManager, scale_surface
from ..mlb_http import ANGELS_TEAM_ID as TRACKED_TEAM_ID
from ..team_config import tracked_team_abbr, tracked_team_name
from ._clip_player import ClipPlayerMixin, _playable_clip_paths

# Small logo beside "vs / @ …" on idle (hero logo is the tracked franchise).
IDLE_OPPONENT_LOGO_SIZE = (config.layout_size(28), config.layout_size(28))
MATCHUP_LOGO_GAP = 8


def _blit_wrapped_center(
    screen: pygame.Surface,
    font: pygame.font.Font,
    text: str,
    y: int,
    color: tuple[int, int, int],
    width_chars: int,
    line_gap: int = 2,
) -> int:
    """Draw wrapped lines centered; return y after last line."""
    if not text.strip():
        return y
    lines = textwrap.fill(text, width=width_chars).split("\n")
    for line in lines:
        surf = font.render(line, True, color)
        r = surf.get_rect(center=(config.SCREEN_WIDTH // 2, y))
        screen.blit(surf, r)
        y += surf.get_height() + line_gap
    return y


def _blit_matchup_row(
    screen: pygame.Surface,
    assets: AssetManager,
    matchup: str,
    opponent_team_id: int,
    y_center: int,
) -> int:
    """Single-line matchup with optional small opponent logo; return bottom y."""
    tex = assets.font_small.render(matchup, True, config.GRAY)
    sm: pygame.Surface | None = None
    if opponent_team_id and opponent_team_id != TRACKED_TEAM_ID:
        base = assets.logos.get(opponent_team_id)
        if base is not None:
            sm = scale_surface(base, IDLE_OPPONENT_LOGO_SIZE)
    if sm is not None:
        total_w = sm.get_width() + MATCHUP_LOGO_GAP + tex.get_width()
        max_h = max(sm.get_height(), tex.get_height())
        x0 = (config.SCREEN_WIDTH - total_w) // 2
        y_row = y_center - max_h // 2
        screen.blit(sm, (x0, y_row + (max_h - sm.get_height()) // 2))
        screen.blit(
            tex,
            (x0 + sm.get_width() + MATCHUP_LOGO_GAP, y_row + (max_h - tex.get_height()) // 2),
        )
        return y_row + max_h + 4
    r = tex.get_rect(center=(config.SCREEN_WIDTH // 2, y_center))
    screen.blit(tex, r)
    return r.bottom + 4


def _idle_clip_folder() -> Path | None:
    """
    Game highlights take priority (yesterday's recap plays until first pitch).
    Falls back to the permanent curated reel only on the idle scene.
    """
    if config.GAME_HIGHLIGHTS_DIR.is_dir():
        for sub in config.GAME_HIGHLIGHTS_DIR.iterdir():
            if sub.is_dir() and _playable_clip_paths(sub):
                return sub
    if config.IDLE_VIDEOS_DIR.is_dir() and (
        _playable_clip_paths(config.IDLE_VIDEOS_DIR)
        or list(config.IDLE_VIDEOS_DIR.glob("*.gif"))
    ):
        return config.IDLE_VIDEOS_DIR
    return None


class IdleScene(ClipPlayerMixin):
    """Next-game info; queues a full-screen highlight clip for mpv every 5 minutes."""

    def draw(self, screen: pygame.Surface, assets: AssetManager, state: dict[str, Any]) -> None:
        self._cp_tick(_idle_clip_folder())
        assets.draw_background(screen, venue_id=int(state.get("next_game_venue_id") or 0))

        # Idle hero: tracked franchise logo + name (not the generic "home" club from state).
        logo = assets.logos.get(TRACKED_TEAM_ID)
        logo_y = config.layout_y(36)
        if logo:
            r = logo.get_rect(center=(config.SCREEN_WIDTH // 2, logo_y))
            screen.blit(logo, r)
            logo_y = r.bottom + 6
        else:
            logo_y = config.layout_y(24)

        nm = tracked_team_name().upper()
        if len(nm) > 14:
            nm = tracked_team_abbr().upper()
        title = assets.font_title.render(nm, True, config.ANGELS_GOLD)
        screen.blit(title, title.get_rect(center=(config.SCREEN_WIDTH // 2, logo_y + 16)))

        y = logo_y + config.layout_y(46)
        label = assets.font_small.render("NEXT GAME", True, config.ANGELS_GOLD)
        screen.blit(label, label.get_rect(center=(config.SCREEN_WIDTH // 2, y)))
        y += config.layout_y(28)

        status = str(state.get("schedule_status", "loading"))

        if status == "loading":
            y = _blit_wrapped_center(
                screen, assets.font_ui, "Loading schedule…", y, config.GRAY, 44
            )
        elif status == "error":
            y = _blit_wrapped_center(
                screen, assets.font_ui, "Schedule unavailable", y, config.GRAY, 44
            )
            err = str(state.get("schedule_error", "")).strip()
            if err:
                y = _blit_wrapped_center(screen, assets.font_small, err, y + 4, (200, 80, 80), 52)
        elif status == "none":
            y = _blit_wrapped_center(
                screen,
                assets.font_ui,
                "No upcoming games in the next few weeks.",
                y,
                config.GRAY,
                44,
            )
        else:
            date_txt = str(state.get("next_game_date_display", "")).strip()
            time_txt = str(state.get("next_game_time_display", "")).strip()
            matchup = str(state.get("next_game_matchup", "")).strip()
            venue = str(state.get("next_game_venue", "")).strip()
            opp_id = int(state.get("next_opponent_team_id") or 0)

            if date_txt:
                y = _blit_wrapped_center(screen, assets.font_ui, date_txt, y, config.WHITE, 40)
                y += config.layout_y(12)
            if time_txt:
                t_surf = assets.font_idle_clock.render(time_txt, True, config.WHITE)
                screen.blit(t_surf, t_surf.get_rect(center=(config.SCREEN_WIDTH // 2, y + 14)))
                y += config.layout_y(42)
            if matchup:
                y = _blit_matchup_row(screen, assets, matchup, opp_id, y + config.layout_y(16))
                y += config.layout_y(10)
            if venue:
                y = _blit_wrapped_center(screen, assets.font_small, venue, y, config.GRAY, 48)
