from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

import pygame

import logging

from .. import config
from ..assets import AssetManager, StreamingGif, scale_surface
from ..drawing.diamond import draw_diamond
from ..gpio_leds import flash_halo
from .linescore_table import compute_linescore_geometry, draw_linescore_table_centered
from ._clip_player import _playable_clip_paths, game_highlights_blocked
from ..highlight_meta import ended_half_before_break, pick_break_highlight

log = logging.getLogger(__name__)

# Filename aliases under live_animations/ (first match wins).
_EVENT_GIF_FILES: dict[str, tuple[str, ...]] = {
    "homerun": ("homerun.gif", "TroutHR.gif", "trouthr.gif"),
    "strikeout": ("strikeout.gif", "strikeout-struck-out.gif"),
    "walk": ("walk.gif",),
    "double": ("double.gif",),
    "triple": ("triple.gif",),
    "hit": ("hit.gif",),
    "out": ("out.gif",),
    "stolen_base": ("stolen_base.gif",),
}

# MLB linescore.inningState during commercial breaks (may be brief or skipped).
_INNING_BREAK_STATES = frozenset({"middle", "between"})
_UNSET_HALF_KEY: tuple[int, str] | None = None


def _half_key(state: dict[str, Any]) -> tuple[int, str]:
    """Current (inning number, top|bottom) from the live feed."""
    try:
        inn = int(state.get("inning") or 0)
    except (TypeError, ValueError):
        inn = 0
    half = str(state.get("inning_half", "top")).lower()
    if half not in ("top", "bottom"):
        half = "top"
    return inn, half


def _at_bat_underway(state: dict[str, Any]) -> bool:
    """True once the new half has an active plate appearance (first pitch thrown)."""
    return int(state.get("balls") or 0) + int(state.get("strikes") or 0) > 0

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
        self._last_half_key: tuple[int, str] | None = _UNSET_HALF_KEY
        self._break_reel_active: bool = False
        self._ended_half_key: tuple[int, str] = (0, "")
        self._break_prefer_tiered: bool = True
        self._played_clips: set[str] = set()
        self._pending_clip: Path | None = None  # consumed by app.py → mpv
        self._anim_play_id: str = ""  # last live_last_play_id we started an anim for

        # GIF animation state (pygame-rendered, not mpv — short canned overlays)
        self._anim: StreamingGif | None = None
        self._anim_frame: int = 0
        self._anim_deadline_ms: int = 0
        self._anim_cur: pygame.Surface | None = None
        self._anim_done = True
        self._anim_event: str = ""

    # ------------------------------------------------------------------
    # Half-inning break reel (continuous until the next half starts)
    # ------------------------------------------------------------------

    def in_inning_break(self, state: dict[str, Any]) -> bool:
        """
        True during commercial time after any half-inning ends.

        Triggered on (inning, half) changes — e.g. top 4th → bottom 4th — not
        only on full-inning ``between`` states.  Keeps rolling until the first
        pitch of the new half.
        """
        st = str(state.get("inning_state", "")).lower()
        if st in _INNING_BREAK_STATES:
            return True
        if self._break_reel_active and not _at_bat_underway(state):
            return True
        return False

    def _pick_next_break_clip(
        self,
        playable: list[Path],
        *,
        tiered: bool,
        ended_inning: int,
        ended_half: str,
    ) -> Path | None:
        """Next clip; first pick of each break prefers the half that just ended."""
        path: Path | None = None
        if tiered and ended_inning >= 1 and ended_half:
            path = pick_break_highlight(
                self._played_clips,
                ended_inning,
                ended_half,
                playable_paths=playable,
            )
        if path is None:
            unseen = [p for p in sorted(playable) if p.name not in self._played_clips]
            if not unseen:
                self._played_clips.clear()
                unseen = sorted(playable)
            if unseen:
                path = unseen[0]
        return path

    def _maybe_queue_clip(self, state: dict[str, Any]) -> None:
        """Queue highlights on every half-inning change; app.py chains until first pitch."""
        key = _half_key(state)
        st = str(state.get("inning_state", "")).lower()

        if self._last_half_key is not None and key != self._last_half_key and key[0] > 0:
            self._break_reel_active = True
            self._break_prefer_tiered = True
            self._ended_half_key = self._last_half_key
            log.info(
                "half-inning change: %s%s → %s%s (state=%s)",
                self._last_half_key[0],
                self._last_half_key[1][:1].upper(),
                key[0],
                key[1][:1].upper(),
                st,
            )
        elif st in _INNING_BREAK_STATES and not self._break_reel_active:
            # Feed jumped to middle/between without a visible half-key step (common).
            self._break_reel_active = True
            self._break_prefer_tiered = True
            self._ended_half_key = ended_half_before_break(st, state)
            log.info(
                "inning break state=%s (ended %s%s)",
                st,
                self._ended_half_key[0],
                self._ended_half_key[1][:1].upper() if self._ended_half_key[1] else "?",
            )
        self._last_half_key = key

        if _at_bat_underway(state) and st not in _INNING_BREAK_STATES:
            if self._break_reel_active:
                log.debug("break reel ended — at-bat underway (%s%s)", key[0], key[1][:1].upper())
            self._break_reel_active = False
            self._break_prefer_tiered = True

        if self.in_inning_break(state) and self._pending_clip is None:
            game_pk = int(state.get("live_game_pk") or 0)
            if game_pk:
                folder = config.GAME_HIGHLIGHTS_DIR / str(game_pk)
                if folder.is_dir():
                    if game_highlights_blocked(
                        folder, block_download=False, block_transcode=False
                    ):
                        log.debug("half-inning break: waiting for highlight folder")
                    else:
                        playable = _playable_clip_paths(folder)
                        if playable:
                            ended_inn, ended_half = self._ended_half_key
                            path = self._pick_next_break_clip(
                                playable,
                                tiered=self._break_prefer_tiered,
                                ended_inning=ended_inn,
                                ended_half=ended_half,
                            )
                            if self._break_prefer_tiered:
                                self._break_prefer_tiered = False
                            if path is not None:
                                self._pending_clip = path
                                log.info(
                                    "half-inning break: queued %s (ended %s%s)",
                                    path.name,
                                    ended_inn,
                                    ended_half[:1].upper() if ended_half else "?",
                                )

    # ------------------------------------------------------------------
    # Canned GIF animations triggered by in-game events
    # ------------------------------------------------------------------

    def _resolve_event_gif(self, event: str) -> Path | None:
        names = _EVENT_GIF_FILES.get(event, (f"{event}.gif",))
        for name in names:
            path = config.LIVE_ANIMATIONS_DIR / name
            if path.is_file():
                return path
        return None

    def _start_anim(self, event: str, size: tuple[int, int]) -> bool:
        """Load and start a canned GIF for *event*. Returns True if started."""
        path = self._resolve_event_gif(event)
        if path is None:
            log.debug("no live animation GIF for event=%s", event)
            return False
        try:
            # Letterbox (no crop) — strikeout is square; TroutHR is 16:9 on a 3:2 panel.
            anim = StreamingGif(
                path,
                size,
                fit="contain",
                min_frame_ms=config.LIVE_ANIM_MIN_FRAME_MS,
            )
            if not anim.ok:
                return False
            surf, dur = anim.decode(0)
            if surf is None:
                return False
            self._anim = anim
            self._anim_frame = 0
            self._anim_deadline_ms = pygame.time.get_ticks() + dur
            self._anim_cur = surf
            self._anim_done = False
            self._anim_event = event
            log.info("live animation started: %s (%s)", event, path.name)
            return True
        except Exception as exc:
            log.warning("live animation failed for %s: %s", event, exc)
            return False

    def _tick_anim(self, screen: pygame.Surface, state: dict[str, Any]) -> bool:
        """
        Trigger new animations from state, advance running ones.
        Returns True if a GIF frame was drawn (caller should still draw
        the scoreboard on top — animations are background replacements).
        """
        event = str(state.get("live_event", "")).strip()
        play_id = str(state.get("live_last_play_id", "")).strip()
        if event and play_id and play_id != self._anim_play_id and not self._anim:
            self._anim_play_id = play_id
            if self._start_anim(event, screen.get_size()):
                if event == "homerun":
                    flash_halo()

        if self._anim_done or self._anim is None:
            return False

        now = pygame.time.get_ticks()
        if now >= self._anim_deadline_ms:
            self._anim_frame += 1
            if self._anim_frame >= self._anim.n_frames:
                # Animation finished — hold for LIVE_ANIM_HOLD_MS then clear.
                if now >= self._anim_deadline_ms + config.LIVE_ANIM_HOLD_MS:
                    self._anim = None
                    self._anim_cur = None
                    self._anim_done = True
                    self._anim_event = ""
                    return False
            else:
                surf, dur = self._anim.decode(self._anim_frame)
                if surf is None:
                    self._anim = None
                    self._anim_cur = None
                    self._anim_done = True
                    self._anim_event = ""
                    return False
                self._anim_cur = surf
                self._anim_deadline_ms = now + dur

        if self._anim_cur is not None:
            screen.blit(self._anim_cur, (0, 0))
            return True
        return False

    # ------------------------------------------------------------------

    def draw(self, screen: pygame.Surface, assets: AssetManager, state: dict[str, Any]) -> None:
        self._maybe_queue_clip(state)

        # Choose background: GIF animation overlay OR stadium image.
        if not self._tick_anim(screen, state):
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
