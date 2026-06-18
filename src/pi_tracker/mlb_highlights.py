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

import requests

from . import config
from . import playback

log = logging.getLogger(__name__)

CONTENT_URL = "https://statsapi.mlb.com/api/v1/game/{game_pk}/content"
POLL_INTERVAL_MIN = 10  # poll API every 10 minutes when caught up
POLL_RETRY_SEC = 60  # retry sooner when the API is unreachable (e.g. boot before network)
CLIP_GAP_SEC = 15  # pause between back-to-back clip downloads (let CPU cool down)


def _chown_for_pi(path: Path) -> None:
    """When biga runs as root, hand files back to pi so SSH cleanup works."""
    import os
    import pwd
    if os.getuid() != 0:
        return
    try:
        pi = pwd.getpwnam("pi")
        os.chown(path, pi.pw_uid, pi.pw_gid)
    except (KeyError, OSError):
        pass

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
    """Return the best mp4 URL — prefer mp4Avc (4000K) over highBit (16000K)."""
    for pb in playbacks:
        if pb.get("name") == "mp4Avc":
            return pb.get("url")
    for pb in playbacks:
        name = pb.get("name", "")
        url = pb.get("url", "")
        if "mp4" in name.lower() and "highbit" not in name.lower() and url.endswith(".mp4"):
            return url
    return None


def fetch_highlight_clips(game_pk: int) -> list[dict]:
    """Fetch play clips from the MLB content API (empty list on failure)."""
    clips, _ = fetch_highlight_clips_result(game_pk)
    return clips


def fetch_highlight_clips_result(game_pk: int) -> tuple[list[dict], bool]:
    """
    Fetch play clips from the MLB content API.

    Returns ``(clips, api_ok)`` where ``api_ok`` is False when the request failed
    (network down, timeout, bad payload) so callers can retry sooner.
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
        return [], False

    items = (
        (data.get("highlights") or {})
        .get("highlights") or {}
    ).get("items") or []
    if not isinstance(items, list):
        return [], False

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
    return results, True


def resolve_highlight_game_pk(snap: dict) -> int:
    """Best game_pk for highlight downloads from current state."""
    for key in ("live_game_pk", "next_game_pk"):
        raw = snap.get(key)
        if raw is not None:
            try:
                return int(raw)
            except (TypeError, ValueError):
                pass
    if config.GAME_HIGHLIGHTS_DIR.is_dir():
        subs = [
            p for p in config.GAME_HIGHLIGHTS_DIR.iterdir()
            if p.is_dir() and p.name.isdigit()
        ]
        if subs:
            return int(max(subs, key=lambda p: int(p.name)).name)
    return 0


def should_run_highlight_downloader(snap: dict) -> bool:
    """Whether background highlight polling should be active."""
    from datetime import date

    scene = str(snap.get("scene", "idle"))
    if scene in ("live", "win", "loss"):
        return True
    if scene == "idle":
        raw = snap.get("final_display_date")
        if raw and isinstance(raw, str):
            try:
                return date.fromisoformat(raw.strip()) == date.today()
            except ValueError:
                pass
    return False


def game_highlights_dir(game_pk: int) -> Path:
    d = config.GAME_HIGHLIGHTS_DIR / str(game_pk)
    d.mkdir(parents=True, exist_ok=True)
    _chown_for_pi(d)
    return d


def sweep_incomplete_highlights(folder: Path, *, stale_after_sec: float = 120) -> int:
    """
    Delete abandoned partial downloads (``.rawdl``, ``*.tc.tmp``, etc.).

    After a reboot or kill mid-download the in-memory busy flag is clear but
    these files remain.  While ``is_download_busy()`` is true, nothing is
    removed.  Otherwise only files whose mtime is older than *stale_after_sec*
    are removed so a very slow but active write is not deleted.
    """
    if not folder.is_dir():
        return 0
    if playback.is_download_busy():
        return 0
    now = time.time()
    removed = 0
    for p in list(folder.iterdir()):
        if not p.is_file():
            continue
        low = p.name.lower()
        if not (low.endswith(".rawdl") or ".tmp" in low):
            continue
        try:
            age = now - p.stat().st_mtime
        except OSError:
            continue
        if age < stale_after_sec:
            log.debug(
                "keeping in-progress highlight file %s (updated %.0fs ago)",
                p.name,
                age,
            )
            continue
        try:
            p.unlink()
            removed += 1
            log.info("removed stale incomplete highlight: %s (idle %.0fs)", p.name, age)
        except OSError as exc:
            log.warning("could not remove incomplete highlight %s: %s", p.name, exc)
    return removed


def log_highlight_folder_status(folder: Path) -> None:
    """One-line summary for telling active downloads from stuck orphans."""
    if not folder.is_dir():
        return
    mp4s = [
        p for p in folder.glob("*.mp4")
        if ".raw" not in p.name.lower() and ".tmp" not in p.name.lower()
    ]
    parts = [f"{len(mp4s)} ready"]
    for pattern in ("*.rawdl", "*.tc.tmp"):
        for p in sorted(folder.glob(pattern)):
            try:
                st = p.stat()
                parts.append(f"{p.name} {st.st_size // 1024}KB age={time.time() - st.st_mtime:.0f}s")
            except OSError:
                parts.append(p.name)
    busy = playback.is_download_busy()
    log.info("highlights/%s: %s | downloader_busy=%s", folder.name, ", ".join(parts), busy)


def sweep_all_incomplete_highlights() -> int:
    """Sweep every ``highlights/{game_pk}/`` subfolder."""
    if not config.GAME_HIGHLIGHTS_DIR.is_dir():
        return 0
    total = 0
    for sub in config.GAME_HIGHLIGHTS_DIR.iterdir():
        if sub.is_dir():
            total += sweep_incomplete_highlights(sub)
    return total


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


def wipe_stale_highlight_folders(keep_pk: int) -> None:
    """Remove highlight subfolders for other games (e.g. yesterday's recap)."""
    if not config.GAME_HIGHLIGHTS_DIR.is_dir():
        return
    for sub in config.GAME_HIGHLIGHTS_DIR.iterdir():
        if not sub.is_dir() or not sub.name.isdigit():
            continue
        if int(sub.name) == keep_pk:
            continue
        shutil.rmtree(sub)
        log.info("wiped stale game highlights for %s (active pk %s)", sub.name, keep_pk)


def _slug(text: str) -> str:
    """Convert a blurb to a safe filename slug, e.g. 'Mike Trout's HR (16)' → 'mike-trout-s-hr-16'."""
    import re
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")[:80]


_TRANSCODE_WIDTH = config.SCREEN_WIDTH
_TRANSCODE_HEIGHT = config.SCREEN_HEIGHT


def _transcode_for_pi(src: Path, dest: Path) -> bool:
    """
    Re-encode *src* to panel-sized H.264 with cover+crop (no letterboxing/stretch).
    Tries Pi hardware encoder first, then software libx264.
    """
    import shutil
    import subprocess as sp
    if not shutil.which("ffmpeg"):
        return False
    w, h = _TRANSCODE_WIDTH, _TRANSCODE_HEIGHT
    vf = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
    tmp = dest.with_suffix(".tc.tmp")
    kb_in = src.stat().st_size // 1024
    log.info("transcoding %s (%d KB) → %dx%d…", src.name, kb_in, w, h)
    t0 = time.monotonic()

    # Hardware encode when available (much faster on Pi); fall back to ultrafast x264.
    encoder_attempts = (
        ["-c:v", "h264_v4l2m2m", "-b:v", "800k"],
        ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "26"],
    )
    for enc_args in encoder_attempts:
        try:
            result = sp.run(
                [
                    "ffmpeg", "-y", "-i", str(src),
                    "-vf", vf,
                    *enc_args,
                    "-an",  # drop audio — panel playback is usually muted
                    "-movflags", "+faststart",
                    "-f", "mp4",
                    str(tmp),
                ],
                capture_output=True,
                timeout=300,
            )
            if result.returncode == 0 and tmp.stat().st_size > 0:
                tmp.rename(dest)
                src.unlink(missing_ok=True)
                _chown_for_pi(dest)
                log.info(
                    "transcoded → %s (%d KB) in %.1fs via %s",
                    dest.name,
                    dest.stat().st_size // 1024,
                    time.monotonic() - t0,
                    enc_args[1],
                )
                return True
            log.debug(
                "ffmpeg %s failed (rc=%d): %s",
                enc_args[1],
                result.returncode,
                (result.stderr or b"")[-200:],
            )
            tmp.unlink(missing_ok=True)
        except Exception as exc:
            log.debug("ffmpeg %s error: %s", enc_args[1], exc)
            tmp.unlink(missing_ok=True)
    return False


def _download_clip(clip: dict, dest_dir: Path, http: requests.Session | None = None) -> Path | None:
    """Download (and optionally transcode) a single clip to dest_dir."""
    slug = _slug(clip.get("blurb", clip["id"]))
    fname = f"{slug}.mp4"
    dest = dest_dir / fname
    if dest.exists():
        return dest  # already downloaded
    raw = dest.with_suffix(".rawdl")
    tc_tmp = dest.with_suffix(".tc.tmp")
    client = http or requests
    playback.download_begin()
    try:
        log.info("downloading: %s", clip["blurb"])
        with client.get(clip["url"], stream=True, timeout=120) as r:
            r.raise_for_status()
            with open(raw, "wb") as f:
                downloaded = 0
                last_log = 0
                for chunk in r.iter_content(chunk_size=1 << 20):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if downloaded - last_log >= 2 << 20:  # every 2 MB
                            log.info("downloading %s: %d KB so far", slug, downloaded // 1024)
                            last_log = downloaded
        log.info("downloaded %s (%d KB) — transcoding…", slug, raw.stat().st_size // 1024)
        if not _transcode_for_pi(raw, dest):
            # ffmpeg not available or failed — use original as-is
            raw.rename(dest)
            log.info("saved %s (original quality)", fname)
        _chown_for_pi(dest)
        return dest
    except Exception as exc:
        log.warning("download failed for %s: %s", clip["blurb"], exc)
        raw.unlink(missing_ok=True)
        tc_tmp.unlink(missing_ok=True)
        return None
    finally:
        playback.download_end()


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
        sweep_incomplete_highlights(self._dest)
        self._seen_ids: set[str] = set()
        self._seen_urls: set[str] = set()
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name=f"highlights-{game_pk}", daemon=True
        )
        self.new_clips: list[Path] = []
        self._lock = threading.Lock()
        self._http = requests.Session()

    def start(self) -> None:
        log.info("highlight downloader starting for game %s", self.game_pk)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def join(self, timeout: float = 3.0) -> None:
        self._thread.join(timeout=timeout)
        try:
            self._http.close()
        except Exception:
            pass

    def drain_new_clips(self) -> list[Path]:
        """Return and clear the list of newly downloaded clip paths."""
        with self._lock:
            clips = list(self.new_clips)
            self.new_clips.clear()
        return clips

    def all_clips(self) -> list[Path]:
        """All finished clips on disk for this game."""
        return sorted(
            p for p in self._dest.glob("*.mp4")
            if ".raw" not in p.name.lower() and ".tmp" not in p.name.lower()
        )

    def _clip_dest(self, clip: dict) -> Path:
        slug = _slug(clip.get("blurb", clip["id"]))
        return self._dest / f"{slug}.mp4"

    def _run(self) -> None:
        while not self._stop.is_set():
            api_ok, did_work = self._poll()
            if did_work:
                wait_sec = CLIP_GAP_SEC
            elif api_ok:
                wait_sec = POLL_INTERVAL_MIN * 60
            else:
                wait_sec = POLL_RETRY_SEC
            self._stop.wait(wait_sec)

    def _poll(self) -> tuple[bool, bool]:
        """
        Poll the API and download at most one new clip.

        Returns ``(api_ok, did_work)`` where *did_work* is True if a clip was
        downloaded or transcoded this cycle.
        """
        sweep_incomplete_highlights(self._dest)
        log_highlight_folder_status(self._dest)
        clips, api_ok = fetch_highlight_clips_result(self.game_pk)
        for clip in clips:
            cid = clip["id"]
            url = clip.get("url") or ""
            dest = self._clip_dest(clip)
            if dest.exists():
                self._seen_ids.add(cid)
                if url:
                    self._seen_urls.add(url)
                continue
            if cid in self._seen_ids:
                self._seen_ids.discard(cid)
            if url and url in self._seen_urls:
                log.debug("skip duplicate URL: %s", clip.get("blurb", "")[:60])
                self._seen_ids.add(cid)
                continue
            playback.wait_while_active(self._stop)
            if self._stop.is_set():
                return api_ok, False
            self._seen_ids.add(cid)
            if url:
                self._seen_urls.add(url)
            path = _download_clip(clip, self._dest, self._http)
            if path:
                with self._lock:
                    self.new_clips.append(path)
                return api_ok, True
        return api_ok, False


def sync_highlight_downloader(
    snap: dict,
    downloader: HighlightDownloader | None,
    last_pk: int,
) -> tuple[HighlightDownloader | None, int]:
    """
    Start, stop, or swap the highlight downloader to match scene + game context.

    Called at app startup and when scene/game context changes — not every frame.
    """
    if not should_run_highlight_downloader(snap):
        if downloader is not None:
            downloader.stop()
            downloader.join()
        return None, 0

    pk = resolve_highlight_game_pk(snap)
    if not pk:
        if downloader is not None:
            downloader.stop()
            downloader.join()
        return None, 0

    if pk == last_pk and downloader is not None:
        return downloader, last_pk

    if downloader is not None:
        downloader.stop()
        downloader.join()

    sweep_all_incomplete_highlights()
    wipe_stale_highlight_folders(pk)

    dl = HighlightDownloader(pk)
    dl.start()
    return dl, pk
