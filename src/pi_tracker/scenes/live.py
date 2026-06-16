from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

import pygame

from .. import config
from ..assets import AssetManager, StreamingMp4, open_streaming_clip, scale_surface
from ..drawing.diamond import draw_diamond
from .linescore_table import compute_linescore_geometry, draw_linescore_table_centered

# inning_half values that indicate an inning break (after top or bottom half).
_INNING_BREAK_STATES = {"middle", "end"}

# Tiny caps beside P:/B: (fielding team pitches; batting team at plate).
PB_LOGO_SIZE = (16, 16)
PB_LOGO_GAP = 4
PLAY_TEXT_X = 12
# Pitcher / batter rows measured from bottom (matches _blit_pb_line in draw()).
PITCHER_ROW_FROM_BOTTOM = 44
BATTER_ROW_FROM_BOTTOM = 26
GAP_PLAY_ABOVE_PITCHER = 6

# Live linescore: right half of 480×320 panel.
LINESCORE_CENTER_X_FRAC = 0.58
LINESCORE_TABLE_DROP_REF = 4
LINESCORE_CELL_PAD = 2


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


def _last_name(full: str) -> str:
    """Compact display name: surname only (keeps room for the stat line)."""
    full = full.strip()
    if not full or full == "—":
        return full or "—"
    return full.split()[-1]


def _fmt_pitcher_stats(state: dict[str, Any]) -> str:
    """e.g. ``5.2 IP  7K  2BB  87p`` — empty if the pitcher has no boxscore line yet."""
    ip = str(state.get("pitcher_ip", "")).strip()
    if not ip:
        return ""
    parts = [f"{ip} IP", f"{int(state.get('pitcher_k', 0) or 0)}K", f"{int(state.get('pitcher_bb', 0) or 0)}BB"]
    pitches = int(state.get("pitcher_pitches", 0) or 0)
    if pitches:
        parts.append(f"{pitches}p")
    return "  ".join(parts)


def _fmt_batter_stats(state: dict[str, Any]) -> str:
    """e.g. ``1-3  1 RBI`` — empty if the batter has no boxscore line yet."""
    ab = state.get("batter_ab")
    if ab is None:
        return ""
    hits = int(state.get("batter_hits", 0) or 0)
    out = f"{hits}-{int(ab or 0)}"
    rbi = int(state.get("batter_rbi", 0) or 0)
    if rbi:
        out += f"  {rbi} RBI"
    return out


def _tiny_team_logo(assets: AssetManager, team_id: int) -> pygame.Surface | None:
    if team_id <= 0:
        return None
    base = assets.logos.get(team_id)
    if base is None:
        return None
    return scale_surface(base, PB_LOGO_SIZE)


def _blit_pb_line(
    screen: pygame.Surface,
    assets: AssetManager,
    label_prefix: str,
    name: str,
    team_id: int,
    y: int,
    x0: int = 12,
    stats: str = "",
) -> None:
    font = assets.font_small
    surf = font.render(f"{label_prefix}: {name}", True, config.WHITE)
    tiny = _tiny_team_logo(assets, team_id)
    if tiny is None:
        text_x = x0
        row_h = surf.get_height()
        sy = y
        screen.blit(surf, (text_x, sy))
    else:
        row_h = max(surf.get_height(), tiny.get_height())
        screen.blit(tiny, (x0, y + (row_h - tiny.get_height()) // 2))
        text_x = x0 + tiny.get_width() + PB_LOGO_GAP
        sy = y + (row_h - surf.get_height()) // 2
        screen.blit(surf, (text_x, sy))
    if stats:
        stat_surf = font.render("  " + stats, True, config.GRAY)
        screen.blit(stat_surf, (text_x + surf.get_width(), sy))


class LiveScene:
    """Live scoreboard; vertical positions scale with ``config.SCREEN_HEIGHT``."""

    def __init__(self) -> None:
        self._last_inning_half: str = ""
        self._played_clips: set[str] = set()
        self._clip: Any = None          # StreamingGif | StreamingMp4
        self._clip_playing = False
        self._clip_frame_idx = 0
        self._clip_cur_frame: pygame.Surface | None = None
        self._clip_deadline_ms = 0

    def _next_unseen_clip(self, game_pk: int, size: tuple[int, int]) -> Any:
        """Load the next unseen game highlight clip, or None if none available."""
        folder = config.GAME_HIGHLIGHTS_DIR / str(game_pk)
        if not folder.is_dir():
            return None
        for path in sorted(folder.glob("*.mp4")):
            if path.name not in self._played_clips:
                clip = open_streaming_clip(path, size)
                if clip.ok:
                    self._played_clips.add(path.name)
                    return clip
        return None

    def _maybe_play_clip(self, screen: pygame.Surface, state: dict[str, Any]) -> bool:
        """Play a game highlight between innings. Returns True if a frame was drawn."""
        now = pygame.time.get_ticks()
        inning_half = str(state.get("inning_half", "")).lower()
        game_pk = int(state.get("live_game_pk") or 0)

        # Trigger on transition into an inning break state.
        if (not self._clip_playing
                and inning_half in _INNING_BREAK_STATES
                and inning_half != self._last_inning_half
                and game_pk):
            self._clip = self._next_unseen_clip(game_pk, screen.get_size())
            if self._clip:
                surf, dur = self._clip.decode(0)
                if surf is not None:
                    self._clip_playing = True
                    self._clip_frame_idx = 0
                    self._clip_cur_frame = surf
                    self._clip_deadline_ms = now + dur

        self._last_inning_half = inning_half

        if not self._clip_playing:
            return False

        if now >= self._clip_deadline_ms and self._clip is not None:
            self._clip_frame_idx += 1
            if self._clip_frame_idx >= self._clip.n_frames:
                self._clip_playing = False
                self._clip = None
                self._clip_cur_frame = None
                return False
            surf, dur = self._clip.decode(self._clip_frame_idx)
            if surf is None:
                self._clip_playing = False
                self._clip = None
                self._clip_cur_frame = None
                return False
            self._clip_cur_frame = surf
            self._clip_deadline_ms = now + dur

        if self._clip_cur_frame is not None:
            screen.blit(self._clip_cur_frame, (0, 0))
            return True
        return False

    def draw(self, screen: pygame.Surface, assets: AssetManager, state: dict[str, Any]) -> None:
        if self._maybe_play_clip(screen, state):
            return
        assets.draw_background(screen, venue_id=int(state.get("live_venue_id") or 0))

        away_id = int(state.get("away_team_id", 0))
        home_id = int(state.get("home_team_id", 0))
        away_logo = assets.logos.get(away_id)
        home_logo = assets.logos.get(home_id)

        y_logo = config.layout_y(8)
        logo_h = config.LOGO_HEADER_SIZE[1]
        if away_logo:
            screen.blit(away_logo, (config.layout_x(12), y_logo))
        if home_logo:
            screen.blit(
                home_logo,
                (config.SCREEN_WIDTH - config.layout_x(12) - home_logo.get_width(), y_logo),
            )

        away_abbr = str(state.get("away_abbr", "AWY"))
        home_abbr = str(state.get("home_abbr", "HME"))
        away_tag = assets.font_small.render(away_abbr, True, config.WHITE)
        home_tag = assets.font_small.render(home_abbr, True, config.WHITE)
        tag_y = y_logo + logo_h + 2
        screen.blit(
            away_tag,
            (config.layout_x(12) + (config.LOGO_HEADER_SIZE[0] - away_tag.get_width()) // 2, tag_y),
        )
        screen.blit(
            home_tag,
            (
                config.SCREEN_WIDTH
                - config.layout_x(12)
                - config.LOGO_HEADER_SIZE[0]
                + (config.LOGO_HEADER_SIZE[0] - home_tag.get_width()) // 2,
                tag_y,
            ),
        )

        ar = int(state.get("away_runs", 0))
        hr = int(state.get("home_runs", 0))
        score_s = f"{ar}  —  {hr}"
        score = assets.font_score.render(score_s, True, config.WHITE)
        screen.blit(score, score.get_rect(center=(config.SCREEN_WIDTH // 2, config.layout_y(46))))

        inning_line = assets.font_ui.render(_fmt_inning(state), True, config.WHITE)
        screen.blit(
            inning_line, inning_line.get_rect(center=(config.SCREEN_WIDTH // 2, config.layout_y(98)))
        )

        balls = int(state.get("balls", 0))
        strikes = int(state.get("strikes", 0))
        count = assets.font_ui.render(f"Count: {balls}-{strikes}", True, config.GRAY)
        count_cy = config.layout_y(118)
        count_rect = count.get_rect(center=(config.SCREEN_WIDTH // 2, count_cy))
        screen.blit(count, count_rect)

        gap_after_count = max(
            10, int(round(10 * config.SCREEN_HEIGHT / config.LAYOUT_REF_HEIGHT))
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
        rsize = max(16, min(28, int(round(20 * config.SCREEN_HEIGHT / config.LAYOUT_REF_HEIGHT))))
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

        p = _truncate(_last_name(str(state.get("pitcher_name", "—"))), 16)
        b = _truncate(_last_name(str(state.get("batter_name", "—"))), 16)
        pit_tid = int(state.get("pitcher_team_id") or 0)
        bat_tid = int(state.get("batter_team_id") or 0)
        p_stats = _fmt_pitcher_stats(state)
        b_stats = _fmt_batter_stats(state)

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
            lines = wrapped.split("\n")[:5]
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

        _blit_pb_line(
            screen,
            assets,
            "P",
            p,
            pit_tid,
            config.SCREEN_HEIGHT - PITCHER_ROW_FROM_BOTTOM,
            stats=p_stats,
        )
        _blit_pb_line(
            screen,
            assets,
            "B",
            b,
            bat_tid,
            config.SCREEN_HEIGHT - BATTER_ROW_FROM_BOTTOM,
            stats=b_stats,
        )

        venue_name = str(state.get("live_venue_name") or "").strip()
        if venue_name:
            venue_surf = assets.font_small.render(venue_name, True, config.GRAY)
            margin = config.layout_x(8)
            screen.blit(
                venue_surf,
                (config.SCREEN_WIDTH - venue_surf.get_width() - margin,
                 config.SCREEN_HEIGHT - venue_surf.get_height() - margin),
            )
