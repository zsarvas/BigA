"""
Pygame main loop: default 480×320 (set ``BIGA_SCREEN_HEIGHT=640`` for Waveshare portrait), scene state machine.

Dev: ``--demo`` / ``--demo-live`` = live scoreboard sample. ``--demo-final`` = final win screen (no network pollers in demo mode).
``--debug-hud`` or ``BIGA_DEBUG_HUD=1`` draws a small updating clock + frame counter (confirms the main loop is alive).
On Linux Pi **from a text VT** (no X11), ``bootstrap_sdl.configure_sdl()`` runs before pygame
imports: dummy SDL audio on the console to avoid mixer/ALSA threading quirks. Video is not
forced (SPI panels often need ``fbcon``). For DRM panels (HDMI / DSI), set ``BIGA_SDL_VIDEO=kmsdrm``.
``SDL_VIDEODRIVER=fbcon`` can still hit pygame/SDL GIL bugs on some builds (pygame#3687).
"""

from __future__ import annotations

import datetime
import os
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
from .idle_mpv import IdleMpvScheduler, discover_idle_videos, suspend_pygame_run_mpv_resume
from .mlb_schedule import try_restore_final_scene_for_today
from .state import SharedGameState
from .scenes import FinalLossScene, FinalWinScene, IdleScene, LiveScene


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


def _open_pygame_window(width: int, height: int, flags: int) -> pygame.Surface:
    """
    Headless Linux: ``pygame.init()`` before ``set_mode``.

    On fbcon failure we **do not** drop ``SDL_VIDEODRIVER=fbcon`` by default: SPI-only systems
    then fall through to KMS/Wayland and fail with ``No available video device`` (no
    ``/dev/dri``). We retry once with the same env after ``pygame.quit()``.

    Optional: ``BIGA_SDL_FALLBACK_KMS=1`` — if ``/dev/dri/card0`` exists, try KMSDRM after
    fbcon still fails (HDMI / DSI setups).
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
        err_l = str(first).lower()
        if "fbcon" not in err_l and "not available" not in err_l:
            raise

        print(
            f"BigA: set_mode failed ({first!s}); retrying same SDL video driver after pygame.quit().",
            file=sys.stderr,
            flush=True,
        )
        try:
            pygame.quit()
        except Exception:
            pass
        _pygame_bootstrap_linux_console()
        try:
            return _set_mode()
        except pygame.error as second:
            want_kms = os.environ.get("BIGA_SDL_FALLBACK_KMS", "").strip().lower() in (
                "1",
                "true",
                "yes",
            )
            dri = Path("/dev/dri/card0")
            if want_kms and dri.exists():
                print(
                    "BigA: BIGA_SDL_FALLBACK_KMS=1 and /dev/dri/card0 present; trying KMSDRM.",
                    file=sys.stderr,
                    flush=True,
                )
                os.environ["SDL_VIDEODRIVER"] = "KMSDRM"
                os.environ.pop("SDL_FBDEV", None)
                os.environ.pop("FRAMEBUFFER", None)
                try:
                    pygame.quit()
                except Exception:
                    pass
                _pygame_bootstrap_linux_console()
                return _set_mode()

            print(
                "BigA: fbcon still unavailable. SPI/console: use pygame linked to SDL with fbcon "
                "(e.g. distro python3-pygame), run from the framebuffer VT (e.g. login on tty2), "
                "and ensure /dev/fb0 is readable. HDMI/DSI with DRI: BIGA_SDL_FALLBACK_KMS=1.",
                file=sys.stderr,
                flush=True,
            )
            raise second from first


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
    st = SharedGameState()
    st.update(
        scene="live",
        away_team_id=137,
        home_team_id=108,
        away_abbr="SF",
        home_abbr="LAA",
        away_name="Giants",
        home_name="Angels",
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
        pitcher_name="Logan Webb",
        batter_name="Mike Trout",
        pitcher_team_id=137,
        batter_team_id=108,
        last_play="Trout doubles to left field; runner scores from first.",
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
    """Final win screen sample (same teams/score as live demo)."""
    st = SharedGameState()
    st.update(
        scene="win",
        away_team_id=137,
        home_team_id=108,
        away_abbr="SF",
        home_abbr="LAA",
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


def main() -> None:
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
        108,
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

    idle_video_paths: list[Path] = []
    if "--no-idle-videos" not in sys.argv:
        idle_video_paths = discover_idle_videos()
    idle_debug = "--idle-video-debug" in sys.argv
    idle_mpv = IdleMpvScheduler(idle_video_paths, debug_interval_sec=10.0 if idle_debug else None)
    debug_hud = _debug_hud_enabled()

    def play_idle_clip(path: Path) -> None:
        nonlocal screen
        screen = suspend_pygame_run_mpv_resume(path, display_flags)
        s = state.snapshot()
        tids = {
            108,
            int(s.get("away_team_id") or 0),
            int(s.get("home_team_id") or 0),
            int(s.get("next_opponent_team_id") or 0),
        }
        tids.discard(0)
        assets.load(tids)
        pygame.display.set_caption("BigA Pi Tracker")

    last_team_key: tuple[int, int, int] | None = None
    running = True
    loop_start = time.monotonic()
    frame_i = 0
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
        )
        if reload_key != last_team_key:
            last_team_key = reload_key
            team_ids = {108, reload_key[0], reload_key[1], reload_key[2]}
            team_ids.discard(0)
            assets.load(team_ids)

        scene_key = str(snap.get("scene", "idle"))
        scene = scenes.get(scene_key, scenes["idle"])
        scene.draw(screen, assets, snap)
        if debug_hud:
            _draw_debug_hud(
                screen, assets, frame_i=frame_i, scene_key=scene_key, loop_start=loop_start
            )
        pygame.display.flip()

        idle_mpv.tick(scene_key, play_idle_clip)

        clock.tick(config.FPS)
        frame_i += 1

    stop_schedule.set()
    stop_game_day.set()
    if schedule_thread is not None:
        schedule_thread.join(timeout=3.0)
    if game_day_thread is not None:
        game_day_thread.join(timeout=3.0)

    pygame.quit()


if __name__ == "__main__":
    main()
