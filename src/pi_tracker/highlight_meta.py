"""
Per-clip sidecar metadata for game highlights (inning / half).

Written once when the downloader finishes a clip; read only at inning breaks
when choosing which highlight to queue — not on every frame.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_META_SUFFIX = ".meta.json"
_VTT_INNING = re.compile(r"[_-]([TB])(\d{1,2})[_\-.]", re.IGNORECASE)

# Do not tag recaps / interviews — they fall through to generic backlog pick.
_NO_TAG_BLURB = (
    "strikes out six",
    "scoreless start",
    "strong pitching",
    "shutout win",
    "condensed game",
    "talks ",
    " joins ",
    "recap",
    "availability",
    "probable pitchers",
    "bullpen availability",
    "fielding alignment",
    "against the angels",
    "against the athletics",
)


def meta_path_for_clip(mp4: Path) -> Path:
    return mp4.with_name(mp4.stem + _META_SUFFIX)


def read_clip_meta(mp4: Path) -> dict[str, Any] | None:
    """Load sidecar if present (~one small json read at inning break)."""
    path = meta_path_for_clip(mp4)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def write_clip_meta(mp4: Path, meta: dict[str, Any]) -> None:
    path = meta_path_for_clip(mp4)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(meta, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)
    try:
        from .mlb_highlights import _chown_for_pi

        _chown_for_pi(path)
    except Exception:
        pass


def parse_inning_from_vtt(vtt_url: str) -> tuple[int, str] | None:
    """Parse ``_T6_`` / ``_B3_`` from MLB closed-caption path when present."""
    if not vtt_url:
        return None
    m = _VTT_INNING.search(vtt_url)
    if not m:
        return None
    half = "top" if m.group(1).upper() == "T" else "bottom"
    return int(m.group(2)), half


def _player_hint_from_blurb(blurb: str) -> str:
    m = re.match(r"^([A-Za-zÀ-ÿ\s\-']+?)'s\b", blurb)
    if m:
        return m.group(1).split()[-1].lower()
    parts = blurb.split()
    return parts[0].lower() if parts else ""


def _should_skip_tagging(blurb: str) -> bool:
    low = blurb.lower()
    return any(p in low for p in _NO_TAG_BLURB)


def _match_play_from_pbp(all_plays: list[dict], blurb: str) -> dict[str, Any] | None:
    """Best-effort link from blurb → one completed at-bat (background thread only)."""
    hint = _player_hint_from_blurb(blurb)
    if not hint or len(hint) < 2:
        return None
    blurb_l = blurb.lower()
    event_terms: list[str] = []
    if "home run" in blurb_l or "homer" in blurb_l:
        event_terms = ["home run"]
    elif "grand slam" in blurb_l:
        event_terms = ["home run", "grand slam"]
    elif "double" in blurb_l:
        event_terms = ["double"]
    elif "triple" in blurb_l:
        event_terms = ["triple"]
    elif "single" in blurb_l:
        event_terms = ["single"]
    elif "strikeout" in blurb_l or " fans " in f" {blurb_l} ":
        event_terms = ["strikeout"]

    best: dict | None = None
    best_score = 0
    for play in all_plays:
        about = play.get("about") or {}
        if not about.get("isComplete"):
            continue
        result = play.get("result") or {}
        desc = (result.get("description") or "").lower()
        event = (result.get("event") or "").lower()
        if hint not in desc:
            continue
        score = 2
        if event_terms and any(t in event or t in desc for t in event_terms):
            score += 3
        if score > best_score:
            best_score = score
            best = play

    if best is None or best_score < 3:
        return None
    about = best.get("about") or {}
    half = str(about.get("halfInning") or "top").lower()
    return {
        "inning": int(about.get("inning") or 0),
        "half": half,
        "atBatIndex": about.get("atBatIndex"),
        "source": "pbp",
    }


def build_clip_meta(game_pk: int, clip: dict, all_plays: list[dict] | None) -> dict[str, Any] | None:
    """
    Derive inning metadata for a downloaded clip.

    Called from the highlight downloader thread — may fetch play-by-play once per clip.
    """
    blurb = str(clip.get("blurb") or "")
    if not blurb or _should_skip_tagging(blurb):
        return None

    vtt = str(clip.get("cclocation_vtt") or "")
    parsed = parse_inning_from_vtt(vtt)
    if parsed:
        inn, half = parsed
        return {
            "game_pk": game_pk,
            "blurb": blurb,
            "inning": inn,
            "half": half,
            "source": "vtt",
        }

    if all_plays is None:
        try:
            from .mlb_http import api_get

            data = api_get(f"/api/v1/game/{game_pk}/playByPlay")
            all_plays = data.get("allPlays") or []
        except Exception as exc:
            log.debug("playByPlay for meta tag failed: %s", exc)
            return None

    matched = _match_play_from_pbp(all_plays, blurb)
    if not matched:
        return None
    matched["game_pk"] = game_pk
    matched["blurb"] = blurb
    return matched


def ended_half_before_break(inning_state: str, state: dict[str, Any]) -> tuple[int, str]:
    """
    Inning/half that just finished when entering *inning_state*.

    ``Middle`` → top half ended; ``Between`` → bottom half ended.
    """
    try:
        inn = int(state.get("inning") or 0)
    except (TypeError, ValueError):
        inn = 0
    st = inning_state.lower()
    if st == "middle":
        return inn, "top"
    if st == "between":
        return inn, "bottom"
    return 0, ""


def pick_break_highlight(
    played: set[str],
    ended_inning: int,
    ended_half: str,
    *,
    playable_paths: list[Path],
) -> Path | None:
    """
    Choose an unplayed clip, preferring the half/inning that just ended.

    *playable_paths* is pre-filtered list (caller builds once per break).
    Reads small sidecars only for those candidates — runs at inning breaks only.
    """
    if not playable_paths or ended_inning < 1:
        return None

    half = ended_half.lower()
    tier_same_half: list[Path] = []
    tier_same_inning: list[Path] = []
    tier_other: list[Path] = []

    for path in playable_paths:
        if path.name in played:
            continue
        meta = read_clip_meta(path)
        if not meta:
            tier_other.append(path)
            continue
        try:
            inn = int(meta.get("inning") or 0)
        except (TypeError, ValueError):
            tier_other.append(path)
            continue
        mh = str(meta.get("half") or "").lower()
        if inn == ended_inning and mh == half:
            tier_same_half.append(path)
        elif inn == ended_inning:
            tier_same_inning.append(path)
        else:
            tier_other.append(path)

    for tier in (tier_same_half, tier_same_inning, tier_other):
        if tier:
            return sorted(tier)[0]
    return None
