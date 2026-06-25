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
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any

import requests

from . import config
from . import playback

log = logging.getLogger(__name__)

CONTENT_URL = "https://statsapi.mlb.com/api/v1/game/{game_pk}/content"
POLL_INTERVAL_MIN = 10  # poll API every 10 minutes when caught up
POLL_RETRY_SEC = 60  # retry sooner when the API is unreachable (e.g. boot before network)
CLIP_GAP_SEC = 15  # pause between back-to-back clip downloads (let CPU cool down)
# Fresh install / empty highlights: look back this many days for a final game to recap.
try:
    RECAP_LOOKBACK_DAYS = max(1, min(21, int(os.environ.get("BIGA_RECAP_LOOKBACK_DAYS", "7"))))
except ValueError:
    RECAP_LOOKBACK_DAYS = 7


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

# Blurb substrings for non-play content we skip downloading.
_SKIP_PATTERNS = (
    "bullpen availability",
    "bench availability",
    "starting lineups",
    "fielding alignment",
    "probable pitchers",
    "breaking down",
)


def should_download(blurb: str) -> bool:
    """True when the highlight is worth fetching (default include unless skipped)."""
    lower = blurb.lower()
    return not any(p in lower for p in _SKIP_PATTERNS)


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
        if not blurb or not should_download(blurb):
            continue
        playbacks = item.get("playbacks") or []
        mp4_url = _best_mp4_url(playbacks)
        if not mp4_url:
            continue
        results.append({
            "id": str(item.get("id", "")),
            "blurb": blurb,
            "url": mp4_url,
            "cclocation_vtt": str(item.get("cclocationVtt") or ""),
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


def count_playable_game_highlights() -> int:
    """Finished ``.mp4`` clips under ``highlights/{game_pk}/`` (any game)."""
    if not config.GAME_HIGHLIGHTS_DIR.is_dir():
        return 0
    n = 0
    for sub in config.GAME_HIGHLIGHTS_DIR.iterdir():
        if not sub.is_dir() or not sub.name.isdigit():
            continue
        for p in sub.glob("*.mp4"):
            if is_valid_highlight_mp4(p):
                n += 1
    return n


def seed_idle_recap_from_schedule(state: Any) -> bool:
    """
    Fresh install / wiped highlights: stay on idle but download the most recent
    final game recap (last RECAP_LOOKBACK_DAYS days). Does not switch to win/loss.
    """
    from datetime import date

    from .mlb_schedule import (
        fetch_angels_schedule_for_date,
        fetch_angels_schedule_lookback,
        find_most_recent_final_angels_game,
        find_todays_scoreboard_angels_game,
    )

    snap = state.snapshot()
    if str(snap.get("scene", "idle")) != "idle":
        return False
    if count_playable_game_highlights() > 0:
        return False
    try:
        if int(snap.get("live_game_pk") or 0) > 0:
            return False
    except (TypeError, ValueError):
        pass

    try:
        today_sched = fetch_angels_schedule_for_date(date.today())
        if find_todays_scoreboard_angels_game(today_sched):
            log.debug("idle recap seed: skip — today's game is live")
            return False
    except Exception as exc:  # noqa: BLE001
        log.debug("idle recap seed: today schedule check failed: %s", exc)

    try:
        sched = fetch_angels_schedule_lookback(RECAP_LOOKBACK_DAYS)
        game = find_most_recent_final_angels_game(sched)
    except Exception as exc:  # noqa: BLE001
        log.warning("idle recap seed: schedule lookup failed: %s", exc)
        return False

    if not game:
        log.info("idle recap seed: no final Angels game in last %s days", RECAP_LOOKBACK_DAYS)
        return False

    try:
        game_pk = int(game.get("gamePk") or 0)
    except (TypeError, ValueError):
        return False
    if game_pk <= 0:
        return False

    state.update(live_game_pk=game_pk)
    log.info(
        "idle recap seed: live_game_pk=%s (most recent final, no highlights on disk)",
        game_pk,
    )
    return True


def should_run_highlight_downloader(snap: dict) -> bool:
    """Whether background highlight polling should be active."""
    from datetime import date

    scene = str(snap.get("scene", "idle"))
    if scene in ("live", "win", "loss"):
        return True
    if scene == "idle":
        # Finish yesterday's recap downloads until first pitch of the next game.
        raw = snap.get("live_game_pk")
        if raw is not None:
            try:
                if int(raw) > 0:
                    return True
            except (TypeError, ValueError):
                pass
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


_MIN_MEDIA_BYTES = 2048
_PROBE_TIMEOUT_SEC = 20


def _probe_media_ok(path: Path) -> bool:
    """
    True when *path* looks like a complete, decodable media file.

    Uses ffprobe when available (Pi transcode path always has it). Partial
    downloads and power-cut truncations usually fail here (moov atom missing).
    """
    import shutil
    import subprocess as sp

    try:
        if path.stat().st_size < _MIN_MEDIA_BYTES:
            return False
    except OSError:
        return False

    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return True  # size-only gate when ffprobe is absent (dev machines)

    try:
        result = sp.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            timeout=_PROBE_TIMEOUT_SEC,
        )
    except (OSError, sp.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    try:
        duration = float((result.stdout or b"").decode().strip())
    except ValueError:
        return False
    return duration > 0.1


def is_valid_highlight_mp4(path: Path) -> bool:
    """Finished highlight clip — not a temp name and passes ffprobe."""
    low = path.name.lower()
    if ".raw" in low or ".tmp" in low:
        return False
    if path.suffix.lower() != ".mp4":
        return False
    return _probe_media_ok(path)


def probe_video_dimensions(path: Path) -> tuple[int, int] | None:
    """Return ``(width, height)`` of the first video stream, or None if unknown."""
    import shutil
    import subprocess as sp

    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        result = sp.run(
            [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "csv=p=0:s=x",
                str(path),
            ],
            capture_output=True,
            timeout=_PROBE_TIMEOUT_SEC,
        )
    except (OSError, sp.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    raw = (result.stdout or b"").decode().strip()
    if "x" not in raw:
        return None
    w_s, h_s = raw.split("x", 1)
    try:
        return int(w_s), int(h_s)
    except ValueError:
        return None


def is_game_highlight_file(path: Path) -> bool:
    """True for finished clips under ``highlights/{game_pk}/`` (not idle reel)."""
    if path.suffix.lower() != ".mp4":
        return False
    try:
        path.resolve().parent.resolve().relative_to(config.GAME_HIGHLIGHTS_DIR.resolve())
    except ValueError:
        return False
    return True


def is_panel_sized_mp4(path: Path) -> bool:
    """
    True when the video fits the panel without mpv CPU-scaling (≤ SCREEN_WIDTH×HEIGHT).

    When ffprobe is unavailable (dev machines), returns True so tests are not blocked.
    """
    dims = probe_video_dimensions(path)
    if dims is None:
        return True
    w, h = dims
    return w <= config.SCREEN_WIDTH and h <= config.SCREEN_HEIGHT


def is_playable_highlight_mp4(path: Path) -> bool:
    """Valid highlight that is safe to play on the Pi panel (game clips must be panel-sized)."""
    if not is_valid_highlight_mp4(path):
        return False
    if is_game_highlight_file(path):
        return is_panel_sized_mp4(path)
    return True


_RETRANSCODE_GRACE_SEC = max(
    0,
    int(os.environ.get("BIGA_RETRANSCODE_GRACE_SEC", "60") or "60"),
)


def _retranscode_to_panel(path: Path, *, background: bool = False) -> bool:
    """Re-encode an oversized on-disk highlight to panel resolution in place."""
    if not path.is_file() or is_panel_sized_mp4(path):
        return True
    final_name = path.name
    tmp_dest = path.with_suffix(".panel.mp4")
    tmp_dest.unlink(missing_ok=True)
    playback.transcode_begin()
    try:
        if not _transcode_for_pi(path, tmp_dest, background=background):
            log.warning("re-transcode failed for oversized highlight: %s", final_name)
            return False
        final = tmp_dest.parent / final_name
        if tmp_dest != final:
            if final.exists():
                final.unlink()
            tmp_dest.rename(final)
            _chown_for_pi(final)
        log.info("re-transcoded oversized highlight → panel size: %s", final_name)
        return True
    finally:
        playback.transcode_end()


def sweep_oversized_highlights(folder: Path, *, started_at: float) -> None:
    """
    Background-only repair: re-transcode one 720p clip per call.

  Never call from the pygame main thread — ffmpeg on a large condensed game can
    take many minutes and starve the UI.
    """
    if not folder.is_dir():
        return
    if time.monotonic() - started_at < _RETRANSCODE_GRACE_SEC:
        return
    if playback.is_download_busy() or playback.is_transcode_busy():
        return
    if playback.is_active():
        return
    playback.wait_for_transcode_slot()
    for p in sorted(folder.glob("*.mp4")):
        low = p.name.lower()
        if ".raw" in low or ".tmp" in low or ".panel" in low:
            continue
        if not is_valid_highlight_mp4(p) or is_panel_sized_mp4(p):
            continue
        dims = probe_video_dimensions(p)
        log.warning(
            "oversized highlight %s (%sx%s) — re-transcoding to %dx%d (background)",
            p.name,
            dims[0] if dims else "?",
            dims[1] if dims else "?",
            config.SCREEN_WIDTH,
            config.SCREEN_HEIGHT,
        )
        _retranscode_to_panel(p, background=True)
        return  # one per poll — ffmpeg is heavy on the Zero 2W


def sweep_incomplete_highlights(folder: Path, *, stale_partial_sec: float = 30) -> int:
    """
    Remove partial temps, corrupt clips, and abandoned downloads.

    Guardrails after power loss or ``systemctl restart`` mid-transcode:

    * ``*.tc.tmp`` — dropped whenever ffmpeg is not running
    * corrupt ``*.mp4`` — ffprobe failure → delete (re-download or resume from raw)
    * ``*.rawdl`` — kept when ffprobe-valid (ready to resume transcode); invalid
      partial downloads removed (very fresh partials kept while download_busy)
    """
    if not folder.is_dir():
        return 0
    if playback.is_download_busy() or playback.is_transcode_busy():
        return 0

    now = time.time()
    removed = 0

    for p in list(folder.glob("*.tc.tmp")):
        try:
            p.unlink()
            removed += 1
            log.info("removed partial transcode temp: %s", p.name)
        except OSError as exc:
            log.warning("could not remove transcode temp %s: %s", p.name, exc)

    for p in list(folder.glob("*.mp4")):
        low = p.name.lower()
        if ".raw" in low or ".tmp" in low:
            continue
        if is_valid_highlight_mp4(p):
            continue
        try:
            p.unlink()
            removed += 1
            log.info("removed corrupt highlight mp4: %s", p.name)
        except OSError as exc:
            log.warning("could not remove corrupt mp4 %s: %s", p.name, exc)

    for raw in list(folder.glob("*.rawdl")):
        dest = folder / f"{raw.stem}.mp4"
        if dest.exists() and is_valid_highlight_mp4(dest):
            try:
                raw.unlink()
                removed += 1
                log.info("removed leftover rawdl (mp4 ok): %s", raw.name)
            except OSError as exc:
                log.warning("could not remove leftover rawdl %s: %s", raw.name, exc)
            continue
        if _probe_media_ok(raw):
            continue  # complete download waiting for resume — keep at any age
        try:
            age = now - raw.stat().st_mtime
        except OSError:
            continue
        if age < stale_partial_sec:
            log.debug("keeping very fresh partial rawdl %s (%.0fs old)", raw.name, age)
            continue
        try:
            raw.unlink()
            removed += 1
            log.info("removed corrupt/partial rawdl: %s", raw.name)
        except OSError as exc:
            log.warning("could not remove partial rawdl %s: %s", raw.name, exc)

    return removed


def log_highlight_folder_status(folder: Path) -> None:
    """One-line summary for telling active downloads from stuck orphans."""
    if not folder.is_dir():
        return
    mp4s = [p for p in folder.glob("*.mp4") if is_valid_highlight_mp4(p)]
    parts = [f"{len(mp4s)} ready"]
    for pattern in ("*.rawdl", "*.tc.tmp"):
        for p in sorted(folder.glob(pattern)):
            try:
                st = p.stat()
                parts.append(f"{p.name} {st.st_size // 1024}KB age={time.time() - st.st_mtime:.0f}s")
            except OSError:
                parts.append(p.name)
    busy_dl = playback.is_download_busy()
    busy_tc = playback.is_transcode_busy()
    log.info(
        "highlights/%s: %s | download_busy=%s transcode_busy=%s",
        folder.name,
        ", ".join(parts),
        busy_dl,
        busy_tc,
    )


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


def _transcode_for_pi(src: Path, dest: Path, *, background: bool = False) -> bool:
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
    tmp.unlink(missing_ok=True)
    kb_in = src.stat().st_size // 1024
    light = background or playback.prefers_light_transcode()
    log.info(
        "transcoding %s (%d KB) → %dx%d%s…",
        src.name,
        kb_in,
        w,
        h,
        " (low priority)" if light else "",
    )
    t0 = time.monotonic()

    def _lower_priority() -> None:
        import os

        try:
            os.nice(10)
        except OSError:
            pass

    # Hardware encode when available (much faster on Pi); fall back to ultrafast x264.
    encoder_attempts: tuple[tuple[str, ...], ...] = (
        ("-c:v", "h264_v4l2m2m", "-b:v", "600k" if light else "800k"),
        (
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "26",
            *(("-threads", "2") if light else ()),
        ),
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
                preexec_fn=_lower_priority if light else None,
            )
            if result.returncode == 0 and tmp.stat().st_size > 0:
                tmp.rename(dest)
                if not is_valid_highlight_mp4(dest):
                    log.warning("transcode output failed validation: %s", dest.name)
                    dest.unlink(missing_ok=True)
                    continue
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


def _resume_orphan_transcodes(dest_dir: Path, stop: threading.Event) -> Path | None:
    """
    Finish clips left as ``.rawdl`` after a crash, service restart, or mpv/ffmpeg overlap.

    Returns the finished ``.mp4`` path, or None if nothing to resume.
    """
    if playback.is_active() or playback.is_live_break_priority() or stop.is_set():
        return None
    for raw in sorted(dest_dir.glob("*.rawdl")):
        dest = dest_dir / f"{raw.stem}.mp4"
        if dest.exists():
            if is_valid_highlight_mp4(dest):
                raw.unlink(missing_ok=True)
            else:
                log.warning("removing corrupt mp4 before resume: %s", dest.name)
                dest.unlink(missing_ok=True)
            if dest.exists():
                continue
        if not _probe_media_ok(raw):
            log.warning("removing corrupt rawdl (cannot resume): %s", raw.name)
            raw.unlink(missing_ok=True)
            continue
        tc_tmp = dest.with_suffix(".tc.tmp")
        tc_tmp.unlink(missing_ok=True)
        try:
            kb = raw.stat().st_size // 1024
        except OSError:
            continue
        log.info("resuming transcode for %s (%d KB on disk)", raw.name, kb)
        playback.wait_for_transcode_slot(stop)
        if stop.is_set():
            return None
        playback.transcode_begin()
        try:
            if _transcode_for_pi(raw, dest):
                _chown_for_pi(dest)
                return dest
            raw.rename(dest)
            if not is_valid_highlight_mp4(dest):
                log.warning("resume: original file failed validation: %s", dest.name)
                dest.unlink(missing_ok=True)
                continue
            if not is_panel_sized_mp4(dest):
                dims = probe_video_dimensions(dest)
                log.warning(
                    "resume: oversized original %s (%sx%s) — will re-transcode in background",
                    dest.name,
                    dims[0] if dims else "?",
                    dims[1] if dims else "?",
                )
                continue
            _chown_for_pi(dest)
            log.info("saved %s (original quality, resume)", dest.name)
            return dest
        finally:
            playback.transcode_end()
    return None


def _download_clip(
    clip: dict,
    dest_dir: Path,
    http: requests.Session | None = None,
    *,
    game_pk: int | None = None,
    stop: threading.Event | None = None,
) -> Path | None:
    """Download (and optionally transcode) a single clip to dest_dir."""
    slug = _slug(clip.get("blurb", clip["id"]))
    fname = f"{slug}.mp4"
    dest = dest_dir / fname
    if dest.exists():
        if is_valid_highlight_mp4(dest):
            return dest
        log.warning("removing corrupt highlight %s — will re-download", dest.name)
        dest.unlink(missing_ok=True)
    raw = dest.with_suffix(".rawdl")
    tc_tmp = dest.with_suffix(".tc.tmp")
    client = http or requests
    wait_stop = stop or threading.Event()

    playback.download_begin()
    try:
        log.info("downloading: %s", clip["blurb"])
        with client.get(clip["url"], stream=True, timeout=120) as r:
            r.raise_for_status()
            with open(raw, "wb") as f:
                downloaded = 0
                last_log = 0
                for chunk in r.iter_content(chunk_size=1 << 20):
                    playback.wait_while_active(wait_stop)
                    if wait_stop.is_set():
                        raise InterruptedError("downloader stopped")
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if downloaded - last_log >= 2 << 20:  # every 2 MB
                            log.info("downloading %s: %d KB so far", slug, downloaded // 1024)
                            last_log = downloaded
    except InterruptedError:
        log.info("download stopped for %s", slug)
        raw.unlink(missing_ok=True)
        tc_tmp.unlink(missing_ok=True)
        return None
    except Exception as exc:
        log.warning("download failed for %s: %s", clip["blurb"], exc)
        raw.unlink(missing_ok=True)
        tc_tmp.unlink(missing_ok=True)
        return None
    finally:
        playback.download_end()

    log.info("downloaded %s (%d KB) — transcoding…", slug, raw.stat().st_size // 1024)
    playback.wait_for_transcode_slot(wait_stop)
    if wait_stop.is_set():
        return None  # leave .rawdl for _resume_orphan_transcodes

    playback.transcode_begin()
    try:
        if not _transcode_for_pi(raw, dest):
            if is_panel_sized_mp4(raw):
                raw.rename(dest)
                if not is_valid_highlight_mp4(dest):
                    log.warning("original file failed validation: %s", fname)
                    dest.unlink(missing_ok=True)
                    return None
                log.info("saved %s (original quality)", fname)
            else:
                dims = probe_video_dimensions(raw)
                log.warning(
                    "transcode failed for oversized clip %s (%sx%s, %d KB) — keeping .rawdl for retry",
                    fname,
                    dims[0] if dims else "?",
                    dims[1] if dims else "?",
                    raw.stat().st_size // 1024,
                )
                return None
        _chown_for_pi(dest)
        if game_pk:
            from .highlight_meta import build_clip_meta, write_clip_meta

            meta = build_clip_meta(game_pk, clip, None)
            if meta:
                write_clip_meta(dest, meta)
        return dest
    except Exception as exc:
        log.warning("transcode failed for %s: %s", slug, exc)
        tc_tmp.unlink(missing_ok=True)
        return None  # keep .rawdl for resume
    finally:
        playback.transcode_end()


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
        self._started_at = time.monotonic()
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
        return sorted(p for p in self._dest.glob("*.mp4") if is_valid_highlight_mp4(p))

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
        sweep_oversized_highlights(self._dest, started_at=self._started_at)
        log_highlight_folder_status(self._dest)
        if not playback.is_download_busy():
            resumed = _resume_orphan_transcodes(self._dest, self._stop)
            if resumed is not None:
                with self._lock:
                    self.new_clips.append(resumed)
                return True, True
        clips, api_ok = fetch_highlight_clips_result(self.game_pk)
        for clip in clips:
            cid = clip["id"]
            url = clip.get("url") or ""
            dest = self._clip_dest(clip)
            if dest.exists():
                if not is_valid_highlight_mp4(dest):
                    log.warning("removing corrupt highlight %s (API poll)", dest.name)
                    dest.unlink(missing_ok=True)
                else:
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
            path = _download_clip(
                clip, self._dest, self._http, game_pk=self.game_pk, stop=self._stop
            )
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
            playback.reset_download_busy()
        return None, 0

    pk = resolve_highlight_game_pk(snap)
    if not pk:
        if downloader is not None:
            downloader.stop()
            downloader.join()
            playback.reset_download_busy()
        return None, 0

    if pk == last_pk and downloader is not None:
        return downloader, last_pk

    if downloader is not None:
        downloader.stop()
        downloader.join()
        playback.reset_download_busy()

    sweep_all_incomplete_highlights()
    wipe_stale_highlight_folders(pk)

    dl = HighlightDownloader(pk)
    dl.start()
    return dl, pk
