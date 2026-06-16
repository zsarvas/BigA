"""
MLB game highlights downloader.

Polls ``/api/v1/game/{game_pk}/content`` every POLL_INTERVAL_MIN minutes,
filters for watchable play clips, and downloads mp4Avc files to
``config.GAME_HIGHLIGHTS_DIR / str(game_pk) /``.

Usage
-----
    from .mlb_highlights import HighlightDownloader
    dl = HighlightDownloader(game_pk=825071)
    dl.start()   # background thread
    dl.stop()    # call on game end / new game start

The folder is wiped by ``wipe_game_highlights(game_pk)`` which should be
called at first pitch of a new game.
"""

from __future__ import annotations

import logging
import shutil
import threading
import time
from pathlib import Path
from typing import Collection

import requests

from . import config

log = logging.getLogger(__name__)

CONTENT_URL = "https://statsapi.mlb.com/api/v1/game/{game_pk}/content"
POLL_INTERVAL_MIN = 10  # poll every 10 minutes during a live game

# Blurb substrings that indicate non-play content we skip.
_SKIP_PATTERNS = (
    "through bat tracking",
    "statcast analysis",
    "outing against",
    "breaking down",
    "the distance behind",
    "starting lineups",
    "bench availability",
    "bullpen availability",
    "fielding alignment",
    "joins the broadcast",
    "on d-backs",
    "on angels",
    "on his ",
    "condensed game",        # too long for between-innings; grab separately
)

# Keep these even if they match a skip pattern above (override list).
_KEEP_PATTERNS = (
    "home run",
    "homer",
    "double",
    "triple",
    "single",
    "strikeout",
    "fans ",
    "steals",
    "catch",
    "grab",
    "replay review",
    "challenge",
    "closes out",
    "rbi",
    "walk-off",
    "walkoff",
)


def _is_play_clip(blurb: str) -> bool:
    """Return True if the blurb looks like an on-field play clip worth keeping."""
    lower = blurb.lower()
    for keep in _KEEP_PATTERNS:
        if keep in lower:
            return True
    for skip in _SKIP_PATTERNS:
        if skip in lower:
            return False
    return True  # default include if no skip pattern matched


def _best_mp4_url(playbacks: list[dict]) -> str | None:
    """Return the mp4Avc URL from a clip's playback list, or None."""
    for pb in playbacks:
        name = pb.get("name", "")
        if name == "mp4Avc":
            return pb.get("url")
    # Fallback: any mp4 that isn't the huge highBit stream
    for pb in playbacks:
        name = pb.get("name", "")
        url = pb.get("url", "")
        if "mp4" in name.lower() and "highbit" not in name.lower() and url.endswith(".mp4"):
            return url
    return None


def fetch_highlight_clips(game_pk: int) -> list[dict]:
    """
    Fetch the content endpoint and return a filtered list of play clip dicts:
    ``[{"id": str, "blurb": str, "url": str}, ...]``
    """
    url = CONTENT_URL.format(game_pk=game_pk)
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            raise ValueError(f"unexpected response type: {type(data)}")
    except Exception as exc:
        log.warning("highlights fetch failed for game %s: %s", game_pk, exc)
        return []

    items = (
        (data.get("highlights") or {})
        .get("highlights") or {}
    ).get("items") or []
    if not isinstance(items, list):
        return []

    results = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "video":
            continue
        blurb = str(item.get("blurb") or "")
        if not blurb or not _is_play_clip(blurb):
            continue
        playbacks = item.get("playbacks") or []
        mp4_url = _best_mp4_url(playbacks)
        if not mp4_url:
            continue
        results.append({
            "id": str(item.get("id", "")),
            "blurb": blurb,
            "url": mp4_url,
        })
    return results


def game_highlights_dir(game_pk: int) -> Path:
    d = config.GAME_HIGHLIGHTS_DIR / str(game_pk)
    d.mkdir(parents=True, exist_ok=True)
    return d


def wipe_game_highlights(game_pk: int | None = None) -> None:
    """
    Delete highlight clips for ``game_pk`` (or the entire highlights dir if None).
    Call at first pitch of a new game to start fresh.
    """
    if game_pk is not None:
        target = config.GAME_HIGHLIGHTS_DIR / str(game_pk)
        if target.exists():
            shutil.rmtree(target)
            log.info("wiped game highlights for %s", game_pk)
    else:
        if config.GAME_HIGHLIGHTS_DIR.exists():
            shutil.rmtree(config.GAME_HIGHLIGHTS_DIR)
            config.GAME_HIGHLIGHTS_DIR.mkdir(parents=True, exist_ok=True)
            log.info("wiped all game highlights")


def _download_clip(clip: dict, dest_dir: Path) -> Path | None:
    """Download a single clip to dest_dir. Returns path on success, None on failure."""
    fname = f"{clip['id']}.mp4"
    dest = dest_dir / fname
    if dest.exists():
        return dest  # already downloaded
    try:
        log.info("downloading: %s", clip["blurb"])
        r = requests.get(clip["url"], stream=True, timeout=60)
        r.raise_for_status()
        tmp = dest.with_suffix(".tmp")
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
        tmp.rename(dest)
        log.info("saved %s (%s)", fname, dest.stat().st_size)
        return dest
    except Exception as exc:
        log.warning("download failed for %s: %s", clip["blurb"], exc)
        return None


class HighlightDownloader:
    """
    Background thread that polls the MLB content endpoint every POLL_INTERVAL_MIN
    minutes and downloads new play clips for the given game_pk.

    Thread-safe: newly downloaded paths are appended to ``new_clips`` which the
    live/win scene can drain to queue clips for playback.
    """

    def __init__(self, game_pk: int) -> None:
        self.game_pk = game_pk
        self._dest = game_highlights_dir(game_pk)
        self._seen_ids: set[str] = set()
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name=f"highlights-{game_pk}", daemon=True
        )
        self.new_clips: list[Path] = []
        self._lock = threading.Lock()

    def start(self) -> None:
        log.info("highlight downloader starting for game %s", self.game_pk)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def drain_new_clips(self) -> list[Path]:
        """Return and clear the list of newly downloaded clip paths."""
        with self._lock:
            clips = list(self.new_clips)
            self.new_clips.clear()
        return clips

    def all_clips(self) -> list[Path]:
        """All clips currently on disk for this game, sorted by filename (download order)."""
        return sorted(self._dest.glob("*.mp4"))

    def _run(self) -> None:
        while not self._stop.is_set():
            self._poll()
            self._stop.wait(POLL_INTERVAL_MIN * 60)

    def _poll(self) -> None:
        clips = fetch_highlight_clips(self.game_pk)
        for clip in clips:
            cid = clip["id"]
            if cid in self._seen_ids:
                continue
            self._seen_ids.add(cid)
            path = _download_clip(clip, self._dest)
            if path:
                with self._lock:
                    self.new_clips.append(path)
