"""Inning-by-inning linescore + R / H / E (compact row for final scenes)."""

from __future__ import annotations

from typing import Any, NamedTuple

import pygame

from ..assets import AssetManager

LINESCORE_MAX_INNINGS = 9


def _nine_cells(state: dict[str, Any], key: str) -> list[str]:
    raw = state.get(key) or []
    if not isinstance(raw, list):
        return ["-"] * LINESCORE_MAX_INNINGS
    out: list[str] = []
    for x in raw[:LINESCORE_MAX_INNINGS]:
        s = str(x).strip()
        out.append(s if s else "-")
    while len(out) < LINESCORE_MAX_INNINGS:
        out.append("-")
    return out


def _blit_centered(
    screen: pygame.Surface,
    font: pygame.font.Font,
    text: str,
    color: tuple[int, int, int],
    cx: int,
    y: int,
) -> None:
    surf = font.render(text, True, color)
    screen.blit(surf, (cx - surf.get_width() // 2, y))


class LinescoreGeometry(NamedTuple):
    """Pixel layout for the inning grid (matches ``draw_linescore_table_centered``)."""

    origin_x: int
    total_w: int
    cell_w: int
    team_w: int
    row_h: int
    away_abbr: str
    home_abbr: str
    away_cells: list[str]
    home_cells: list[str]
    away_runs: int
    home_runs: int
    away_hits: int
    home_hits: int
    away_errors: int
    home_errors: int


def compute_linescore_geometry(
    assets: AssetManager,
    state: dict[str, Any],
    center_x: int,
    *,
    fg: tuple[int, int, int],
    cell_pad: int = 4,
    table_font: pygame.font.Font | None = None,
) -> LinescoreGeometry:
    font = table_font or assets.font_small
    away_abbr = str(state.get("away_abbr", "AWY"))[:4]
    home_abbr = str(state.get("home_abbr", "HME"))[:4]
    away_cells = _nine_cells(state, "linescore_away_innings")
    home_cells = _nine_cells(state, "linescore_home_innings")
    ar = int(state.get("away_runs", 0))
    hr = int(state.get("home_runs", 0))
    ah = int(state.get("away_hits", 0))
    hh = int(state.get("home_hits", 0))
    ae = int(state.get("away_errors", 0))
    he = int(state.get("home_errors", 0))

    cell_w = max(
        font.render(s, True, fg).get_width()
        for s in ("-", "0", "8", "10", "99", "R", "H", "E", "1", "9")
    ) + cell_pad
    team_w = max(
        28,
        font.render(away_abbr, True, fg).get_width() + 6,
        font.render(home_abbr, True, fg).get_width() + 6,
    )
    row_h = font.get_height() + 2
    total_w = team_w + 12 * cell_w
    origin_x = center_x - total_w // 2
    return LinescoreGeometry(
        origin_x,
        total_w,
        cell_w,
        team_w,
        row_h,
        away_abbr,
        home_abbr,
        away_cells,
        home_cells,
        ar,
        hr,
        ah,
        hh,
        ae,
        he,
    )


def draw_linescore_table_centered(
    screen: pygame.Surface,
    assets: AssetManager,
    state: dict[str, Any],
    center_x: int,
    top_y: int,
    *,
    fg: tuple[int, int, int],
    hdr: tuple[int, int, int],
    cell_pad: int = 4,
    table_font: pygame.font.Font | None = None,
) -> int:
    """Draw 1–9 + R H E table centered at ``center_x``; return total pixel height."""
    g = compute_linescore_geometry(
        assets, state, center_x, fg=fg, cell_pad=cell_pad, table_font=table_font
    )
    font = table_font or assets.font_small
    origin_x = g.origin_x
    total_w = g.total_w
    cell_w = g.cell_w
    team_w = g.team_w
    row_h = g.row_h
    away_abbr = g.away_abbr
    home_abbr = g.home_abbr
    away_cells = g.away_cells
    home_cells = g.home_cells
    ar, hr, ah, hh, ae, he = (
        g.away_runs,
        g.home_runs,
        g.away_hits,
        g.home_hits,
        g.away_errors,
        g.home_errors,
    )
    y0 = top_y

    for k in range(LINESCORE_MAX_INNINGS):
        cx = origin_x + team_w + int((k + 0.5) * cell_w)
        _blit_centered(screen, font, str(k + 1), hdr, cx, y0)
    for j, lab in enumerate(("R", "H", "E")):
        cx = origin_x + team_w + int((LINESCORE_MAX_INNINGS + j + 0.5) * cell_w)
        _blit_centered(screen, font, lab, hdr, cx, y0)

    line_y = y0 + row_h + 1
    pygame.draw.line(screen, hdr, (origin_x, line_y), (origin_x + total_w, line_y), 1)

    y_away = line_y + 2
    y_home = y_away + row_h
    screen.blit(font.render(away_abbr, True, fg), (origin_x + 2, y_away))
    screen.blit(font.render(home_abbr, True, fg), (origin_x + 2, y_home))

    for row_cells, y_row, rhe in (
        (away_cells, y_away, (ar, ah, ae)),
        (home_cells, y_home, (hr, hh, he)),
    ):
        for k in range(LINESCORE_MAX_INNINGS):
            cx = origin_x + team_w + int((k + 0.5) * cell_w)
            _blit_centered(screen, font, row_cells[k], fg, cx, y_row)
        for j, val in enumerate(rhe):
            cx = origin_x + team_w + int((LINESCORE_MAX_INNINGS + j + 0.5) * cell_w)
            _blit_centered(screen, font, str(int(val)), fg, cx, y_row)

    return y_home + row_h - top_y
