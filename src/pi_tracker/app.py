"""
Pygame main loop: 480×320 landscape, scene state machine.

Dev: ``--demo`` / ``--demo-live`` = live scoreboard sample. ``--demo-final`` = final win screen (no network pollers in demo mode).
On Linux (Pi), fbcon defaults apply before init; on macOS do not set SDL_VIDEODRIVER=fbcon
(use plain ``python3 run_pi_ui.py`` or a desktop driver).
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

from .embedded_shim import install_fc_list_stub_if_needed

install_fc_list_stub_if_needed()
os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"

import pygame

from . import config
from .assets import AssetManager
from .idle_mpv import IdleMpvScheduler, discover_idle_videos, suspend_pygame_run_mpv_resume
from .mlb_schedule import try_restore_final_scene_for_today
from .state import SharedGameState
from .scenes import FinalLossScene, FinalWinScene, IdleScene, LiveScene


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


def _apply_linux_framebuffer_env_defaults() -> None:
    """fbcon is Linux-only; macOS/Windows SDL builds raise 'fbcon not available'."""
    if not sys.platform.startswith("linux"):
        return
    os.environ.setdefault("SDL_VIDEODRIVER", "fbcon")
    os.environ.setdefault("SDL_FBDEV", "/dev/fb0")


def main() -> None:
    # Before pygame.init(): Pi framebuffer (setdefault — explicit shell env still wins).
    _apply_linux_framebuffer_env_defaults()

    demo_live = "--demo" in sys.argv or "--demo-live" in sys.argv
    demo_final = "--demo-final" in sys.argv
    no_schedule = "--no-schedule" in sys.argv
    pygame.init()
    pygame.mouse.set_visible(False)
    pygame.display.set_caption("BigA Pi Tracker")
    flags = 0
    if "--fullscreen" in sys.argv:
        flags |= pygame.FULLSCREEN
    display_flags = flags
    screen = pygame.display.set_mode((config.SCREEN_WIDTH, config.SCREEN_HEIGHT), display_flags)
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
        pygame.display.flip()

        idle_mpv.tick(scene_key, play_idle_clip)

        clock.tick(config.FPS)

    stop_schedule.set()
    stop_game_day.set()
    if schedule_thread is not None:
        schedule_thread.join(timeout=3.0)
    if game_day_thread is not None:
        game_day_thread.join(timeout=3.0)

    pygame.quit()


if __name__ == "__main__":
    main()
