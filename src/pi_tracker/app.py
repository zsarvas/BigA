"""
Pygame main loop: default **480×320** landscape (``BIGA_SCREEN_WIDTH`` / ``BIGA_SCREEN_HEIGHT``), scene state machine.
Win scene drives GPIO BCM 19 HIGH (``BIGA_WIN_LED_GPIO`` to override).

Dev: ``python run_pi_ui.py yankees --debug-hud`` — first non-flag arg is an MLB team slug (see
``team_config``). Same as ``BIGA_TEAM_ID`` / ``BIGA_TEAM_NAME`` env if set before launch.

``--demo`` / ``--demo-live`` = live scoreboard sample. ``--demo-final`` = final win screen (no network pollers in demo mode).
``--debug-hud`` or ``BIGA_DEBUG_HUD=1`` draws a small updating clock + frame counter (confirms the main loop is alive).
On Linux Pi **from a text VT** (no X11), ``bootstrap_sdl.configure_sdl()`` runs before pygame
imports: dummy SDL audio plus the video backend. Target is **Bookworm + KMS**, so the default
is ``BIGA_SDL_VIDEO=kmsdrm`` (panel = ``vc4-kms-dpi-generic``). Legacy Bullseye/SPI panels can
set ``BIGA_SDL_VIDEO=fbcon``; ``_open_pygame_window`` falls back across KMSDRM/fbcon by probing
``/dev/dri/card*`` and ``/dev/fb0``.
"""

from __future__ import annotations

import datetime
import logging
import os
import platform
import subprocess
import sys
import textwrap
import threading
import time
from collections.abc import Callable
from pathlib import Path

from .embedded_shim import install_fc_list_stub_if_needed

install_fc_list_stub_if_needed()
os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"

from .bootstrap_sdl import configure_sdl

configure_sdl()

import pygame

from . import config
from . import mouse_hide
from . import playback
from . import drm_health
from .assets import AssetManager, _repo_font
from .gpio_leds import cleanup_gpio, init_gpio, is_muted, set_win_led
from .mlb_highlights import (
    HighlightDownloader,
    is_game_highlight_file,
    is_panel_sized_mp4,
    is_playable_highlight_mp4,
    is_valid_highlight_mp4,
    seed_idle_recap_from_schedule,
    sync_highlight_downloader,
)
from .mlb_http import ANGELS_TEAM_ID as TRACKED_TEAM_ID
from .mlb_schedule import try_restore_final_scene_for_today
from .scenes import FinalLossScene, FinalWinScene, IdleScene, LiveScene
from .scenes._clip_player import clip_title_from_path
from .state import SharedGameState
from .team_config import tracked_team_abbr, tracked_team_name


def _demo_opponent() -> tuple[int, str, str]:
    """Sample away club: Giants unless the tracked team is SF, then Dodgers."""
    if TRACKED_TEAM_ID == 137:
        return 119, "LAD", "Dodgers"
    return 137, "SF", "Giants"


def _demo_pitcher_name(away_abbr: str) -> str:
    if away_abbr == "SF":
        return "Logan Webb"
    if away_abbr == "LAD":
        return "Clayton Kershaw"
    return "Opponent pitcher"


def _demo_batter_name() -> str:
    if TRACKED_TEAM_ID == 108:
        return "Mike Trout"
    return f"{tracked_team_name()} batter"


def _linux_text_console() -> bool:
    return sys.platform.startswith("linux") and not (
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    )


def _pygame_bootstrap_linux_console() -> None:
    """Full SDL init; mixer is unused (mpv for video). Helps some fbcon/SDL builds."""
    pygame.init()
    try:
        pygame.mixer.quit()
    except pygame.error:
        pass
    mouse_hide.apply()


def _try_set_mode(
    width: int, height: int, flags: int, driver: str
) -> tuple[pygame.Surface | None, "pygame.error | None"]:
    """Re-init SDL with ``driver`` and attempt ``set_mode``; return (surface, error)."""
    if driver == "KMSDRM":
        os.environ["SDL_VIDEODRIVER"] = "KMSDRM"
        os.environ.pop("SDL_FBDEV", None)
        os.environ.pop("FRAMEBUFFER", None)
    elif driver == "fbcon":
        os.environ["SDL_VIDEODRIVER"] = "fbcon"
        os.environ.setdefault("SDL_FBDEV", "/dev/fb0")
        os.environ.setdefault("FRAMEBUFFER", os.environ["SDL_FBDEV"])

    try:
        pygame.quit()
    except Exception:
        pass
    _pygame_bootstrap_linux_console()
    try:
        return pygame.display.set_mode((width, height), flags), None
    except pygame.error as e:
        return None, e


def _finalize_display(screen: pygame.Surface) -> pygame.Surface:
    """Hide SDL/DRM cursor plane once a window exists."""
    mouse_hide.hide_cursor_hard()
    return screen


def _open_pygame_window(width: int, height: int, flags: int) -> pygame.Surface:
    """
    Open the display. Target is **Bookworm + KMSDRM**.

    Order:
      1. Whatever ``configure_sdl()`` selected (KMSDRM by default).
      2. If that fails on a text console, retry **KMSDRM** explicitly when
         ``/dev/dri/card0`` exists.
      3. Last resort: legacy **fbcon** when ``/dev/fb0`` exists (Bullseye / SPI).
    """
    if _linux_text_console():
        _pygame_bootstrap_linux_console()

    def _set_mode() -> pygame.Surface:
        return pygame.display.set_mode((width, height), flags)

    try:
        return _finalize_display(_set_mode())
    except pygame.error as first:
        if not _linux_text_console():
            raise

        print(
            f"BigA: set_mode failed with {os.environ.get('SDL_VIDEODRIVER', '?')} ({first!s}); "
            "trying fallbacks.",
            file=sys.stderr,
            flush=True,
        )

        attempts: list[str] = []
        if Path("/dev/dri/card0").exists() or Path("/dev/dri/card1").exists():
            attempts.append("KMSDRM")
        if Path("/dev/fb0").exists():
            attempts.append("fbcon")

        last_err: pygame.error = first
        for driver in attempts:
            if driver == os.environ.get("SDL_VIDEODRIVER", ""):
                continue
            print(f"BigA: retrying SDL_VIDEODRIVER={driver}.", file=sys.stderr, flush=True)
            surface, err = _try_set_mode(width, height, flags, driver)
            if surface is not None:
                return _finalize_display(surface)
            if err is not None:
                last_err = err
                print(f"BigA: {driver} failed ({err!s}).", file=sys.stderr, flush=True)

        print(
            "BigA: no usable SDL video device. Check: ls -l /dev/dri/card* /dev/fb0; "
            "Bookworm needs vc4-kms-dpi-generic (KMS) and BIGA_SDL_VIDEO=kmsdrm; "
            "reboot after config.txt changes.",
            file=sys.stderr,
            flush=True,
        )
        raise last_err from first


def _debug_hud_enabled() -> bool:
    if "--debug-hud" in sys.argv:
        return True
    return os.environ.get("BIGA_DEBUG_HUD", "").strip().lower() in ("1", "true", "yes")


def _draw_debug_hud(
    screen: pygame.Surface,
    assets: AssetManager,
    *,
    frame_i: int,
    scene_key: str,
    loop_start: float,
) -> None:
    """Top-right overlay: wall time (ms), frame index, uptime — updates every tick if the loop runs."""
    now = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
    up = time.monotonic() - loop_start
    line_a = f"{now}  f={frame_i}  +{up:.1f}s"
    line_b = f"scene={scene_key}"
    s_a = assets.font_small.render(line_a, True, config.GRAY)
    s_b = assets.font_small.render(line_b, True, config.GRAY)
    pad = 4
    y = pad
    screen.blit(s_a, (config.SCREEN_WIDTH - pad - s_a.get_width(), y))
    y += s_a.get_height() + 1
    screen.blit(s_b, (config.SCREEN_WIDTH - pad - s_b.get_width(), y))


def _demo_state() -> SharedGameState:
    aw_id, aw_abbr, aw_name = _demo_opponent()
    hb, hn = tracked_team_abbr(), tracked_team_name()
    tid = TRACKED_TEAM_ID
    st = SharedGameState(persist=False)
    st.update(
        scene="live",
        away_team_id=aw_id,
        home_team_id=tid,
        away_abbr=aw_abbr,
        home_abbr=hb,
        away_name=aw_name,
        home_name=hn,
        away_runs=4,
        home_runs=3,
        linescore_away_innings=["0", "2", "0", "1", "1", "0", "-", "-", "-"],
        linescore_home_innings=["1", "0", "0", "0", "0", "2", "-", "-", "-"],
        away_hits=9,
        home_hits=7,
        away_errors=0,
        home_errors=1,
        inning=6,
        inning_half="bottom",
        outs=2,
        balls=2,
        strikes=1,
        runners={"first": True, "second": False, "third": True},
        pitcher_name=_demo_pitcher_name(aw_abbr),
        batter_name=_demo_batter_name(),
        pitcher_team_id=aw_id,
        batter_team_id=tid,
        pitcher_ip="5.2",
        pitcher_k=7,
        pitcher_bb=2,
        pitcher_pitches=87,
        batter_ab=3,
        batter_hits=1,
        batter_rbi=1,
        last_play=f"{_demo_batter_name()} doubles to left field; runner scores from first.",
        schedule_status="ok",
        schedule_error="",
        next_game_date_display="Wednesday, May 14, 2026",
        next_game_time_display="7:07 PM PDT",
        next_game_matchup="vs Athletics",
        next_game_venue="Angel Stadium",
        next_game_pk=999999,
        live_game_pk=999999,
        next_opponent_team_id=133,
        idle_subtitle="Wednesday, May 14, 2026  ·  7:07 PM PDT",
    )
    return st


def _demo_final_state() -> SharedGameState:
    """Final win screen sample (tracked team wins at home vs demo opponent)."""
    aw_id, aw_abbr, _aw_name = _demo_opponent()
    hb = tracked_team_abbr()
    tid = TRACKED_TEAM_ID
    st = SharedGameState(persist=False)
    st.update(
        scene="win",
        away_team_id=aw_id,
        home_team_id=tid,
        away_abbr=aw_abbr,
        home_abbr=hb,
        away_runs=4,
        home_runs=3,
        linescore_away_innings=["0", "2", "0", "1", "1", "0", "-", "-", "-"],
        linescore_home_innings=["1", "0", "0", "0", "0", "2", "-", "-", "-"],
        away_hits=9,
        home_hits=7,
        away_errors=0,
        home_errors=1,
        live_game_pk=999999,
        next_opponent_team_id=133,
        schedule_status="ok",
        schedule_error="",
        next_game_date_display="Wednesday, May 14, 2026",
        next_game_time_display="7:07 PM PDT",
        next_game_matchup="vs Athletics",
        next_game_venue="Angel Stadium",
        next_game_pk=999999,
        idle_subtitle="Wednesday, May 14, 2026  ·  7:07 PM PDT",
    )
    return st


def _configure_logging() -> None:
    """Emit INFO+ logs (scene transitions, final lock/release) to stdout/biga.log.

    Without this, the module-level ``log.info(...)`` calls are dropped and we have
    no record of when the win/loss scene actually cuts over to idle.
    """
    if logging.getLogger().handlers:
        return
    level_name = os.environ.get("BIGA_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    # Keep HTTP libraries quiet even at INFO.
    for noisy in ("urllib3", "requests"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


_MPV_LOG = Path("/tmp/biga-mpv.log")
# Brief pause after mpv exits so vo=drm releases KMS master before pygame KMSDRM init.
_MPV_DRM_HANDOFF_SEC = 0.2
_drm_monitor: drm_health.DrmHealthMonitor | None = None
# Hold the "NOW SHOWING" card before handing the display to mpv.
_NOW_SHOWING_HOLD_SEC = float(os.environ.get("BIGA_NOW_SHOWING_SEC", "10"))
_NOW_SHOWING_POLL_SEC = 0.25


def _draw_now_showing(screen: pygame.Surface, title: str) -> None:
    """Full-screen interstitial: NOW SHOWING + sanitized clip title."""
    w, h = screen.get_size()
    screen.fill(config.BLACK)
    label_font = _repo_font(config.layout_size(22))
    title_font = _repo_font(config.layout_size(16))

    label = label_font.render("NOW SHOWING", True, (255, 50, 50))
    screen.blit(label, label.get_rect(midtop=(w // 2, int(h * 0.28))))

    max_chars = max(12, w // max(8, title_font.size("M")[0]))
    lines = textwrap.wrap(title, width=max_chars)[:3] or [title]
    y = int(h * 0.48)
    for line in lines:
        surf = title_font.render(line, True, config.WHITE)
        screen.blit(surf, surf.get_rect(midtop=(w // 2, y)))
        y += surf.get_height() + 4

    mouse_hide.apply(screen)
    pygame.display.flip()
    mouse_hide.apply(screen)


def _hold_now_showing(
    screen: pygame.Surface,
    path: Path,
    *,
    should_abort: Callable[[], bool] | None = None,
) -> str:
    """
    Show the NOW SHOWING card for ``_NOW_SHOWING_HOLD_SEC``.

    Returns:
      ``"ok"``     — hold finished; safe to start mpv
      ``"abort"``  — *should_abort* became true (e.g. next half started)
      ``"quit"``   — user asked to exit (QUIT / Escape)
    """
    _draw_now_showing(screen, clip_title_from_path(path))
    deadline = time.monotonic() + max(0.0, _NOW_SHOWING_HOLD_SEC)
    while time.monotonic() < deadline:
        if should_abort is not None and should_abort():
            logging.info("now-showing aborted before mpv: %s", path.name)
            return "abort"
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                logging.info("now-showing quit requested during %s", path.name)
                return "quit"
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                logging.info("now-showing escape during %s", path.name)
                return "quit"
        time.sleep(_NOW_SHOWING_POLL_SEC)
    if should_abort is not None and should_abort():
        logging.info("now-showing aborted before mpv: %s", path.name)
        return "abort"
    return "ok"


def _filter_valid_clip_paths(paths: list[Path], *, validate: bool = True) -> list[Path]:
    from .mlb_highlights import is_skip_highlight_path

    good: list[Path] = []
    for path in paths:
        if is_skip_highlight_path(path):
            logging.info("skipping interview/fluff clip: %s", path.name)
            continue
        if validate and path.suffix.lower() == ".mp4":
            if not is_valid_highlight_mp4(path):
                logging.warning("skipping corrupt highlight clip: %s", path.name)
                path.unlink(missing_ok=True)
                continue
            if is_game_highlight_file(path) and not is_panel_sized_mp4(path):
                logging.warning(
                    "skipping oversized highlight clip: %s (needs panel transcode)",
                    path.name,
                )
                continue
        good.append(path)
    return good


def _mpv_cmd(w: int, h: int, *, on_pi: bool) -> list[str]:
    cmd = [
        "mpv",
        "--no-terminal",
        "--quiet",
        "--osd-level=0",
        "--cursor-autohide=always",
        "--input-cursor=no",
        f"--log-file={_MPV_LOG}",
        "--msg-level=cplayer=warn,vo=warn,vd=warn,ao=warn,ffmpeg=warn",
    ]
    if on_pi:
        cmd += [
            "--vo=drm",
            "--drm-device=/dev/dri/card0",
            f"--drm-mode={w}x{h}",
            "--hwdec=v4l2m2m",
            "--hwdec-software-fallback=yes",
            "--vd-lavc-threads=4",
            "--panscan=1.0",
        ]
    else:
        cmd += ["--hwdec=auto", "--fs", "--panscan=1.0"]
    if is_muted():
        cmd.append("--no-audio")
    elif on_pi:
        cmd.append("--ao=alsa,null")
    # Desktop (Mac/Linux): let mpv pick CoreAudio/Pulse/etc.
    return cmd


def _run_mpv_subprocess(cmd: list[str], label: str) -> None:
    playback.begin()
    try:
        result = subprocess.run(cmd, timeout=3600, capture_output=True, text=True)
        if result.returncode not in (0, 4):
            log_tail = ""
            try:
                log_tail = _MPV_LOG.read_text(errors="replace")[-2000:]
            except OSError:
                pass
            logging.warning(
                "mpv exited %d for %s\nstdout: %s\nstderr: %s\nmpv log: %s",
                result.returncode,
                label,
                result.stdout[-500:],
                result.stderr[-500:],
                log_tail[-1000:] or "(empty)",
            )
        elif result.stderr.strip():
            logging.debug("mpv stderr: %s", result.stderr[-300:])
    except FileNotFoundError:
        logging.warning("mpv not found — skipping %s", label)
    except subprocess.TimeoutExpired:
        logging.warning("mpv timed out on %s", label)
    except Exception as exc:  # noqa: BLE001
        logging.warning("mpv error: %s", exc)
    finally:
        playback.end()


def _play_mpv_sequence(
    paths: list[Path],
    screen: pygame.Surface,
    flags: int,
    *,
    validate: bool = True,
) -> "pygame.Surface":
    """
    Play one or more clips in a single mpv process (one DRM handoff).

    mpv plays multiple files back-to-back without exiting — avoids ~15s black
    gaps from pygame quit/init between each clip on the Pi panel.
    """
    raw_count = len(paths)
    paths = _filter_valid_clip_paths(paths, validate=validate)
    if not paths:
        logging.warning("mpv: no valid clips in batch (skipped %d path(s))", raw_count)
        return screen

    size = screen.get_size()
    mouse_hide.apply(screen)
    pygame.display.flip()
    mouse_hide.apply(screen)
    pygame.display.quit()

    on_pi = platform.system() == "Linux"
    w, h = size
    cmd = _mpv_cmd(w, h, on_pi=on_pi) + [str(p) for p in paths]
    if len(paths) == 1:
        logging.info("mpv launching: %s", paths[0].name)
    else:
        logging.info(
            "mpv launching %d-clip sequence: %s",
            len(paths),
            ", ".join(p.name for p in paths[:4]) + ("…" if len(paths) > 4 else ""),
        )

    _run_mpv_subprocess(cmd, paths[0].name if len(paths) == 1 else f"{len(paths)} clips")

    if on_pi:
        time.sleep(_MPV_DRM_HANDOFF_SEC)
    pygame.display.init()
    screen = _finalize_display(pygame.display.set_mode(size, flags))
    mouse_hide.handoff_from_mpv(screen, fill=config.BLACK)
    if _drm_monitor is not None:
        _drm_monitor.note_mpv_finished()
        _drm_monitor.check_after_mpv(mpv_playback_active=False)
    return screen


def _play_mpv(path: Path, screen: pygame.Surface, flags: int) -> "pygame.Surface":
    """Hand the display to mpv for one clip, then reclaim it."""
    return _play_mpv_sequence([path], screen, flags)


def _play_live_break_reel(
    scene: object,
    state: SharedGameState,
    screen: pygame.Surface,
    flags: int,
    first_path: Path,
) -> tuple["pygame.Surface", str]:
    """
    Play ready highlight clips one at a time until the half-inning break ends.

    Once a title card / clip starts, it always finishes — we only stop *queueing
    the next* clip after play resumes. Returns ``(screen, status)`` where status
    is ``"ok"``, ``"abort"`` (break over after a clip), or ``"quit"``.
    """
    playback.set_live_break_priority(True)
    scene._pending_clip = None  # type: ignore[attr-defined]
    pending: Path | None = first_path
    status = "ok"

    def _break_over() -> bool:
        return not scene.in_inning_break(state.snapshot())  # type: ignore[attr-defined]

    try:
        while True:
            if pending is None:
                snap = state.snapshot()
                # Don't start another clip once live play has resumed.
                if not scene.in_inning_break(snap):  # type: ignore[attr-defined]
                    break
                scene._maybe_queue_clip(snap)  # type: ignore[attr-defined]
                pending = scene._pending_clip  # type: ignore[attr-defined]
                scene._pending_clip = None  # type: ignore[attr-defined]
                if pending is None:
                    break

            batch = _filter_valid_clip_paths([pending], validate=False)
            clip = batch[0] if batch else None
            if clip is None:
                logging.warning("break reel: skipping unreadable clip %s", pending.name)
                played = getattr(scene, "_played_clips", None)
                if isinstance(played, set):
                    played.add(pending.name)
                pending = None
                continue
            pending = None

            # Finish the full NOW SHOWING card even if the next half has started.
            hold = _hold_now_showing(screen, clip)
            if hold != "ok":
                status = hold
                break

            played = getattr(scene, "_played_clips", None)
            if isinstance(played, set):
                played.add(clip.name)

            # Always finish the video once started.
            screen = _play_mpv_sequence([clip], screen, flags, validate=False)

            if _break_over():
                status = "abort"
                break
    finally:
        playback.set_live_break_priority(False)
        scene._pending_clip = None  # type: ignore[attr-defined]

    return screen, status


def _flash_boot_logo(screen: pygame.Surface) -> None:
    """Paint Angels logo immediately after display init (service restarts, post-Plymouth)."""
    logo_path = config.LOGOS_DIR / f"{TRACKED_TEAM_ID}.png"
    if not logo_path.is_file():
        return
    try:
        w, h = screen.get_size()
        img = pygame.image.load(str(logo_path)).convert_alpha()
        target_h = max(1, int(h * 0.60))
        scale = target_h / max(1, img.get_height())
        target_w = max(1, int(img.get_width() * scale))
        img = pygame.transform.smoothscale(img, (target_w, target_h))
        screen.fill(config.BLACK)
        screen.blit(img, img.get_rect(center=(w // 2, h // 2)))
        pygame.display.flip()
        mouse_hide.apply(screen)
    except (pygame.error, OSError):
        pass


def main() -> None:
    _configure_logging()
    log = logging.getLogger(__name__)
    log.info("argv: %s", " ".join(sys.argv))
    # SDL video/audio driver env is applied in bootstrap_sdl.configure_sdl() before pygame import.
    demo_live = "--demo" in sys.argv or "--demo-live" in sys.argv
    demo_final = "--demo-final" in sys.argv
    if demo_final:
        log.info("demo-final mode (sample win screen; state is not persisted)")
    elif demo_live:
        log.info("demo-live mode (sample scoreboard; state is not persisted)")
    no_schedule = "--no-schedule" in sys.argv
    flags = 0
    if "--fullscreen" in sys.argv:
        flags |= pygame.FULLSCREEN
    display_flags = flags
    screen = _open_pygame_window(config.SCREEN_WIDTH, config.SCREEN_HEIGHT, display_flags)
    pygame.display.set_caption("BigA Pi Tracker")
    _flash_boot_logo(screen)
    mouse_hide.apply(screen)
    clock = pygame.time.Clock()

    state: SharedGameState
    if demo_final:
        state = _demo_final_state()
    elif demo_live:
        state = _demo_state()
    else:
        state = SharedGameState()
        if not no_schedule:
            try_restore_final_scene_for_today(state)

    team_ids = {
        int(state.snapshot().get("away_team_id", 0)),
        int(state.snapshot().get("home_team_id", 0)),
        int(state.snapshot().get("next_opponent_team_id") or 0),
        int(state.snapshot().get("pitcher_team_id") or 0),
        int(state.snapshot().get("batter_team_id") or 0),
        TRACKED_TEAM_ID,
    }
    team_ids.discard(0)
    assets = AssetManager()
    assets.load(team_ids)

    scenes = {
        "idle": IdleScene(),
        "live": LiveScene(),
        "win": FinalWinScene(),
        "loss": FinalLossScene(),
    }

    stop_schedule = threading.Event()
    stop_game_day = threading.Event()
    schedule_thread: threading.Thread | None = None
    game_day_thread: threading.Thread | None = None
    demo = demo_live or demo_final
    if not demo and not no_schedule:
        from .game_day_poller import start_game_day_poller
        from .schedule_poller import start_idle_schedule_poller

        schedule_thread = start_idle_schedule_poller(state, stop_schedule)
        game_day_thread = start_game_day_poller(state, stop_game_day)

    debug_hud = _debug_hud_enabled()

    init_gpio()
    last_team_key: tuple[int, int, int, int, int] | None = None
    last_scene_key: str | None = None
    _hl_downloader: HighlightDownloader | None = None
    _last_live_pk: int = 0
    _last_dl_key: tuple[str, int, int] | None = None
    if not demo and not no_schedule:
        seed_idle_recap_from_schedule(state)
    running = True
    loop_start = time.monotonic()
    global _drm_monitor
    _drm_monitor = drm_health.DrmHealthMonitor(boot_monotonic=loop_start)
    drm_monitor = _drm_monitor
    frame_i = 0
    try:
        while running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        running = False
                    elif event.key == pygame.K_1:
                        state.update(scene="idle")
                    elif event.key == pygame.K_2:
                        sn = state.snapshot()
                        pk = sn.get("live_game_pk") or sn.get("next_game_pk")
                        p2: dict = {"scene": "live"}
                        if pk is not None:
                            p2["live_game_pk"] = pk
                        state.update(p2)
                    elif event.key == pygame.K_3:
                        state.update(scene="win")
                    elif event.key == pygame.K_4:
                        state.update(scene="loss")
                    elif event.key == pygame.K_h:
                        # Demo: Angels HR celebration (GIF bg + halo flash).
                        state.update(
                            scene="live",
                            live_event="homerun",
                            live_last_play_id=f"demo-hr-{time.time():.0f}",
                        )

            snap = state.snapshot()
            reload_key = (
                int(snap.get("away_team_id") or 0),
                int(snap.get("home_team_id") or 0),
                int(snap.get("next_opponent_team_id") or 0),
                int(snap.get("pitcher_team_id") or 0),
                int(snap.get("batter_team_id") or 0),
            )
            if reload_key != last_team_key:
                last_team_key = reload_key
                team_ids = {
                    TRACKED_TEAM_ID,
                    reload_key[0],
                    reload_key[1],
                    reload_key[2],
                    reload_key[3],
                    reload_key[4],
                }
                team_ids.discard(0)
                assets.load(team_ids)

            scene_key = str(snap.get("scene", "idle"))
            if scene_key != last_scene_key:
                prev_scene = last_scene_key
                last_scene_key = scene_key
                set_win_led(scene_key == "win")
                if scene_key == "idle" and prev_scene in ("win", "loss"):
                    scenes["idle"]._cp_arm_immediate()

            # Manage highlight downloader lifecycle (only when scene / game pk changes).
            if not demo:
                dl_key = (
                    scene_key,
                    int(snap.get("live_game_pk") or 0),
                    int(snap.get("next_game_pk") or 0),
                )
                if dl_key != _last_dl_key:
                    _last_dl_key = dl_key
                    _hl_downloader, _last_live_pk = sync_highlight_downloader(
                        snap, _hl_downloader, _last_live_pk
                    )

            scene = scenes.get(scene_key, scenes["idle"])
            playback.set_final_scene_active(scene_key in ("win", "loss"))
            scene.draw(screen, assets, snap)
            if debug_hud:
                _draw_debug_hud(
                    screen, assets, frame_i=frame_i, scene_key=scene_key, loop_start=loop_start
                )
            if mouse_hide.kiosk_mode():
                mouse_hide.apply(screen)
            pygame.display.flip()
            if mouse_hide.kiosk_mode():
                mouse_hide.apply(screen)
            if frame_i == 0:
                log.info("main loop first frame (scene=%s)", scene_key)

            # Hold background ffmpeg until ready break clips have played.
            if scene_key == "live":
                br_snap = state.snapshot()
                in_br = getattr(scene, "in_inning_break", lambda _s: False)(br_snap) or getattr(
                    scene, "_break_reel_active", False
                )
                playback.set_live_break_priority(
                    in_br and getattr(scene, "_pending_clip", None) is not None
                )
            elif not playback.is_active():
                playback.set_live_break_priority(False)

            # Queued clip(s): live breaks play one ready clip at a time.
            pending = getattr(scene, "_pending_clip", None)
            if pending:
                snap = state.snapshot()
                live_scene = scene_key == "live"
                in_break = live_scene and (
                    getattr(scene, "in_inning_break", lambda _s: False)(snap)
                    or getattr(scene, "_break_reel_active", False)
                )
                # Ready clips on disk can play during background ffmpeg (win/loss + live breaks).
                clip_ready = (
                    is_playable_highlight_mp4(pending)
                    if is_game_highlight_file(pending)
                    else is_valid_highlight_mp4(pending)
                )
                if in_break or not playback.is_transcode_busy() or clip_ready:
                    if in_break:
                        screen, reel_status = _play_live_break_reel(
                            scene, state, screen, display_flags, pending
                        )
                        if reel_status == "quit":
                            running = False
                    else:
                        scene._pending_clip = None  # type: ignore[attr-defined]
                        hold = _hold_now_showing(screen, pending)
                        if hold == "ok":
                            screen = _play_mpv(pending, screen, display_flags)
                        elif hold == "quit":
                            running = False
                        # Re-arm the gap from the clip's END so the score scene is
                        # shown between clips (get_ticks advances during mpv).
                        if hasattr(scene, "_cp_notify_played"):
                            scene._cp_notify_played()  # type: ignore[attr-defined]

            # Boost tick rate for pygame-rendered GIFs (live events, win/loss backgrounds).
            if getattr(scene, "_anim", None) or scene_key in ("win", "loss"):
                tick_fps = config.HIGHLIGHT_FPS
            else:
                tick_fps = config.FPS
            clock.tick(tick_fps)
            frame_i += 1
            drm_monitor.tick(mpv_playback_active=playback.is_active())
    finally:
        set_win_led(False)
        cleanup_gpio()

    stop_schedule.set()
    stop_game_day.set()
    if schedule_thread is not None:
        schedule_thread.join(timeout=3.0)
    if game_day_thread is not None:
        game_day_thread.join(timeout=3.0)

    pygame.quit()


if __name__ == "__main__":
    main()
