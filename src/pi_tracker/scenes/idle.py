from __future__ import annotations

import textwrap
from typing import Any

import pygame

from .. import config
from ..assets import AssetManager


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


class IdleScene:
    def draw(self, screen: pygame.Surface, assets: AssetManager, state: dict[str, Any]) -> None:
        screen.fill(config.BLACK)

        home_id = int(state.get("home_team_id", 108))
        logo = assets.logos.get(home_id)
        logo_y = 52
        if logo:
            r = logo.get_rect(center=(config.SCREEN_WIDTH // 2, logo_y))
            screen.blit(logo, r)
            logo_y = r.bottom + 6
        else:
            logo_y = 28

        title = assets.font_title.render("ANGELS", True, config.ANGELS_GOLD)
        screen.blit(title, title.get_rect(center=(config.SCREEN_WIDTH // 2, logo_y + 18)))

        y = logo_y + 52
        label = assets.font_small.render("NEXT GAME", True, config.ANGELS_GOLD)
        screen.blit(label, label.get_rect(center=(config.SCREEN_WIDTH // 2, y)))
        y += 22

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

            if date_txt:
                y = _blit_wrapped_center(screen, assets.font_ui, date_txt, y, config.WHITE, 40)
            if time_txt:
                t_surf = assets.font_idle_clock.render(time_txt, True, config.WHITE)
                screen.blit(t_surf, t_surf.get_rect(center=(config.SCREEN_WIDTH // 2, y + 18)))
                y += 40
            if matchup:
                y = _blit_wrapped_center(screen, assets.font_small, matchup, y, config.GRAY, 48)
            if venue:
                y = _blit_wrapped_center(screen, assets.font_small, venue, y, config.GRAY, 48)

        hint = assets.font_small.render("1–4 scenes · Esc quit", True, config.GRAY)
        screen.blit(hint, hint.get_rect(center=(config.SCREEN_WIDTH // 2, config.SCREEN_HEIGHT - 14)))
