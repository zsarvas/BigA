from __future__ import annotations

import textwrap
from typing import Any

import pygame

from .. import config
from ..assets import AssetManager
from ..drawing.diamond import draw_diamond
from .linescore_table import compute_linescore_geometry, draw_linescore_table_centered

# Tiny caps beside P:/B: (fielding team pitches; batting team at plate).
PB_LOGO_SIZE = (16, 16)
PB_LOGO_GAP = 4
PLAY_TEXT_X = 12
# Pitcher / batter rows measured from bottom (matches _blit_pb_line in draw()).
PITCHER_ROW_FROM_BOTTOM = 44
BATTER_ROW_FROM_BOTTOM = 26
GAP_PLAY_ABOVE_PITCHER = 6

# Live linescore: nudged toward lower-right vs. screen center; roomier cells + font.
LINESCORE_CENTER_X_FRAC = 0.62
LINESCORE_TABLE_DROP_REF = 6
LINESCORE_CELL_PAD = 4


def _inning_label(n: int | str) -> str:
    try:
        i = int(n)
    except (TypeError, ValueError):
        return str(n)
    if 11 <= i % 100 <= 13:
        return f"{i}th"
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(i % 10, "th")
    return f"{i}{suffix}"


def _fmt_inning(state: dict[str, Any]) -> str:
    half = str(state.get("inning_half", "top")).lower()
    arrow = "▲" if half == "top" else "▼"
    inn = _inning_label(state.get("inning", "?"))
    outs = int(state.get("outs", 0))
    otxt = "out" if outs == 1 else "outs"
    return f"{arrow} {inn}  |  {outs} {otxt}"


def _truncate(s: str, max_chars: int) -> str:
    s = s.strip()
    return s if len(s) <= max_chars else s[: max_chars - 1] + "…"


def _tiny_team_logo(assets: AssetManager, team_id: int) -> pygame.Surface | None:
    if team_id <= 0:
        return None
    base = assets.logos.get(team_id)
    if base is None:
        return None
    return pygame.transform.smoothscale(base, PB_LOGO_SIZE)


def _blit_pb_line(
    screen: pygame.Surface,
    assets: AssetManager,
    label_prefix: str,
    name: str,
    team_id: int,
    y: int,
    x0: int = 12,
) -> None:
    label = f"{label_prefix}: {name}"
    surf = assets.font_small.render(label, True, config.WHITE)
    tiny = _tiny_team_logo(assets, team_id)
    if tiny is None:
        screen.blit(surf, (x0, y))
        return
    row_h = max(surf.get_height(), tiny.get_height())
    y0 = y + (row_h - tiny.get_height()) // 2
    screen.blit(tiny, (x0, y0))
    sx = x0 + tiny.get_width() + PB_LOGO_GAP
    sy = y + (row_h - surf.get_height()) // 2
    screen.blit(surf, (sx, sy))


class LiveScene:
    """Live scoreboard; vertical positions scale with ``config.SCREEN_HEIGHT``."""

    def draw(self, screen: pygame.Surface, assets: AssetManager, state: dict[str, Any]) -> None:
        screen.fill(config.BLACK)

        away_id = int(state.get("away_team_id", 0))
        home_id = int(state.get("home_team_id", 0))
        away_logo = assets.logos.get(away_id)
        home_logo = assets.logos.get(home_id)

        y_logo = 12
        if away_logo:
            screen.blit(away_logo, (16, y_logo))
        if home_logo:
            screen.blit(home_logo, (config.SCREEN_WIDTH - 16 - home_logo.get_width(), y_logo))

        away_abbr = str(state.get("away_abbr", "AWY"))
        home_abbr = str(state.get("home_abbr", "HME"))
        away_tag = assets.font_small.render(away_abbr, True, config.WHITE)
        home_tag = assets.font_small.render(home_abbr, True, config.WHITE)
        screen.blit(away_tag, (16 + (config.LOGO_HEADER_SIZE[0] - away_tag.get_width()) // 2, y_logo + 76))
        screen.blit(
            home_tag,
            (
                config.SCREEN_WIDTH - 16 - config.LOGO_HEADER_SIZE[0]
                + (config.LOGO_HEADER_SIZE[0] - home_tag.get_width()) // 2,
                y_logo + 76,
            ),
        )

        ar = int(state.get("away_runs", 0))
        hr = int(state.get("home_runs", 0))
        score_s = f"{ar}  —  {hr}"
        score = assets.font_score.render(score_s, True, config.WHITE)
        screen.blit(score, score.get_rect(center=(config.SCREEN_WIDTH // 2, config.layout_y(52))))

        inning_line = assets.font_ui.render(_fmt_inning(state), True, config.WHITE)
        screen.blit(
            inning_line, inning_line.get_rect(center=(config.SCREEN_WIDTH // 2, config.layout_y(108)))
        )

        balls = int(state.get("balls", 0))
        strikes = int(state.get("strikes", 0))
        count = assets.font_ui.render(f"Count: {balls}-{strikes}", True, config.GRAY)
        count_cy = config.layout_y(132)
        count_rect = count.get_rect(center=(config.SCREEN_WIDTH // 2, count_cy))
        screen.blit(count, count_rect)

        gap_after_count = max(
            16, int(round(16 * config.SCREEN_HEIGHT / config.LAYOUT_REF_HEIGHT))
        )
        tbl_top = count_rect.bottom + gap_after_count
        tbl_top += int(
            round(LINESCORE_TABLE_DROP_REF * config.SCREEN_HEIGHT / config.LAYOUT_REF_HEIGHT)
        )
        ls_cx = int(config.SCREEN_WIDTH * LINESCORE_CENTER_X_FRAC)

        h_tbl = draw_linescore_table_centered(
            screen,
            assets,
            state,
            ls_cx,
            tbl_top,
            fg=config.WHITE,
            hdr=config.GRAY,
            cell_pad=LINESCORE_CELL_PAD,
            table_font=assets.font_linescore,
        )

        geom = compute_linescore_geometry(
            assets,
            state,
            ls_cx,
            fg=config.WHITE,
            cell_pad=LINESCORE_CELL_PAD,
            table_font=assets.font_linescore,
        )

        runners = state.get("runners") or {}
        rsize = max(20, min(34, int(round(22 * config.SCREEN_HEIGHT / config.LAYOUT_REF_HEIGHT))))
        gap_edge = 4
        table_left = geom.origin_x
        while rsize > 16:
            rf = int(rsize * 1.9) + 2
            if table_left - gap_edge >= 2 * rf + 6:
                break
            rsize -= 1
        r_field = int(rsize * 1.9) + 2
        ideal_cx = (gap_edge + table_left) // 2
        low = gap_edge + r_field
        high = table_left - r_field
        if low <= high:
            diamond_cx = max(low, min(ideal_cx, high))
        else:
            diamond_cx = max(low, min(ideal_cx, table_left - 4))
        diamond_cy = tbl_top + h_tbl // 2
        draw_diamond(screen, runners, diamond_cx, diamond_cy, size=rsize)

        p = _truncate(str(state.get("pitcher_name", "—")), 34)
        b = _truncate(str(state.get("batter_name", "—")), 34)
        pit_tid = int(state.get("pitcher_team_id") or 0)
        bat_tid = int(state.get("batter_team_id") or 0)

        last = str(state.get("last_play", "")).strip()
        if last:
            font = assets.font_small
            line_h = max(font.get_linesize(), font.get_height() + 1)
            gap_p = max(
                GAP_PLAY_ABOVE_PITCHER,
                int(round(GAP_PLAY_ABOVE_PITCHER * config.SCREEN_HEIGHT / config.LAYOUT_REF_HEIGHT)),
            )
            pitcher_y = config.SCREEN_HEIGHT - PITCHER_ROW_FROM_BOTTOM
            play_bottom = pitcher_y - gap_p
            y_table_bottom = tbl_top + h_tbl + 4
            wrap_chars = max(24, (config.SCREEN_WIDTH - PLAY_TEXT_X - 16) // 7)
            wrapped = textwrap.fill(last, width=wrap_chars)
            lines = wrapped.split("\n")[:8]
            while len(lines) > 1:
                y_first = play_bottom - len(lines) * line_h
                if y_first >= y_table_bottom:
                    break
                lines = lines[:-1]
            y_first = play_bottom - len(lines) * line_h
            if y_first < y_table_bottom:
                y_first = y_table_bottom

            for i, part in enumerate(lines):
                surf = font.render(part, True, config.GRAY)
                screen.blit(surf, (PLAY_TEXT_X, y_first + i * line_h))

        _blit_pb_line(screen, assets, "P", p, pit_tid, config.SCREEN_HEIGHT - PITCHER_ROW_FROM_BOTTOM)
        _blit_pb_line(screen, assets, "B", b, bat_tid, config.SCREEN_HEIGHT - BATTER_ROW_FROM_BOTTOM)
