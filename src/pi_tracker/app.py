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
import subprocess
import sys
import threading
import time
from pathlib import Path

from .embedded_shim import install_fc_list_stub_if_needed

install_fc_list_stub_if_needed()
os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"

from .bootstrap_sdl import configure_sdl

configure_sdl()

import pygame

from . import config
from .assets import AssetManager
from .mlb_http import ANGELS_TEAM_ID as TRACKED_TEAM_ID
from .mlb_schedule import try_restore_final_scene_for_today
from .state import SharedGameState
from .team_config import tracked_team_abbr, tracked_team_name
from .gpio_leds import cleanup_gpio, init_gpio, is_muted, set_win_led
from .mlb_highlights import HighlightDownloader, wipe_game_highlights
from .scenes import FinalLossScene, FinalWinScene, IdleScene, LiveScene


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
        return _set_mode()
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
                return surface
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
    st = SharedGameState()
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
    st = SharedGameState()
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


def _play_mpv(path: Path, size: tuple[int, int], flags: int) -> "pygame.Surface":
    """
    Hand the display to mpv for one clip, then reclaim it.

    On KMS-mode Raspberry Pi, only one process can be DRM master at a time, so
    we fully tear down the pygame display before launching mpv and reinitialise
    it afterwards.  mpv auto-detects the best video output (drm on Pi, metal/gl
    on Mac dev) so no platform-specific flags are needed.

    Returns a fresh pygame.Surface at the same size/flags so the caller can
    replace its ``screen`` reference.
    """
    pygame.display.quit()
    import platform
    on_pi = platform.system() == "Linux"
    cmd = [
        "mpv",
        "--hwdec=v4l2m2m" if on_pi else "--hwdec=auto",
        "--msg-level=all=warn",  # surface warnings without full verbosity
        "--fs", "--panscan=1.0", "--osd-level=0",
        "--scale=bilinear", "--cscale=bilinear", "--dscale=bilinear",
        "--video-sync=display-resample",
        "--ao=alsa",  # skip JACK/PipeWire, go straight to ALSA
    ]
    if is_muted():
        cmd.append("--no-audio")
    cmd.append(str(path))
    try:
        result = subprocess.run(cmd, timeout=600, capture_output=True, text=True)
        if result.returncode not in (0, 4):  # 4 = quit by user
            logging.warning(
                "mpv exited %d for %s\nstdout: %s\nstderr: %s",
                result.returncode, path.name,
                result.stdout[-500:], result.stderr[-500:],
            )
        elif result.stderr.strip():
            logging.debug("mpv stderr: %s", result.stderr[-300:])
    except FileNotFoundError:
        logging.warning("mpv not found — skipping clip %s", path.name)
    except subprocess.TimeoutExpired:
        logging.warning("mpv timed out on %s", path.name)
    except Exception as exc:  # noqa: BLE001
        logging.warning("mpv error: %s", exc)
    pygame.display.init()
    screen = pygame.display.set_mode(size, flags)
    pygame.mouse.set_visible(False)
    return screen


def main() -> None:
    _configure_logging()
    # SDL video/audio driver env is applied in bootstrap_sdl.configure_sdl() before pygame import.
    demo_live = "--demo" in sys.argv or "--demo-live" in sys.argv
    demo_final = "--demo-final" in sys.argv
    no_schedule = "--no-schedule" in sys.argv
    flags = 0
    if "--fullscreen" in sys.argv:
        flags |= pygame.FULLSCREEN
    display_flags = flags
    screen = _open_pygame_window(config.SCREEN_WIDTH, config.SCREEN_HEIGHT, display_flags)
    pygame.display.set_caption("BigA Pi Tracker")
    pygame.mouse.set_visible(False)
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
    running = True
    loop_start = time.monotonic()
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
                last_scene_key = scene_key
                set_win_led(scene_key == "win")

            # Manage highlight downloader lifecycle.
            # Keep downloading through win/loss scenes — post-game recaps and
            # condensed-game clips upload for hours after the final out.
            # Only stop when returning to idle (no active game context).
            if not demo:
                live_pk = int(snap.get("live_game_pk") or 0)
                if scene_key in ("live", "win", "loss") and live_pk:
                    if live_pk != _last_live_pk:
                        # New game — stop old downloader, wipe old clips, start fresh.
                        if _hl_downloader:
                            _hl_downloader.stop()
                        if _last_live_pk:
                            wipe_game_highlights(_last_live_pk)
                        _last_live_pk = live_pk
                        _hl_downloader = HighlightDownloader(live_pk)
                        _hl_downloader.start()
                elif scene_key == "idle" and _hl_downloader:
                    _hl_downloader.stop()
                    _hl_downloader = None

            scene = scenes.get(scene_key, scenes["idle"])
            scene.draw(screen, assets, snap)
            if debug_hud:
                _draw_debug_hud(
                    screen, assets, frame_i=frame_i, scene_key=scene_key, loop_start=loop_start
                )
            pygame.display.flip()

            # If the scene queued a clip for mpv, play it now and reclaim the display.
            pending = getattr(scene, "_pending_clip", None)
            if pending:
                scene._pending_clip = None  # type: ignore[attr-defined]
                screen = _play_mpv(pending, screen.get_size(), display_flags)

            # Boost tick rate while a pygame-rendered GIF animation is running
            # (live event overlays); normal scenes run at the low base FPS.
            tick_fps = config.HIGHLIGHT_FPS if getattr(scene, "_anim", None) else config.FPS
            clock.tick(tick_fps)
            frame_i += 1
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
