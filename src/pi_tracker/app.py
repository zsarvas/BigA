"""
Pygame main loop: 480×320 landscape, scene state machine.

Dev: keys 1–4 switch scenes. On Pi, set SDL_VIDEODRIVER / SDL_FBDEV per notes.
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
        inning=6,
        inning_half="bottom",
        outs=2,
        balls=2,
        strikes=1,
        runners={"first": True, "second": False, "third": True},
        pitcher_name="Logan Webb",
        batter_name="Mike Trout",
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


def main() -> None:
    demo = "--demo" in sys.argv
    no_schedule = "--no-schedule" in sys.argv
    pygame.init()
    pygame.display.set_caption("BigA Pi Tracker")
    flags = 0
    if "--fullscreen" in sys.argv:
        flags |= pygame.FULLSCREEN
    display_flags = flags
    screen = pygame.display.set_mode((config.SCREEN_WIDTH, config.SCREEN_HEIGHT), display_flags)
    clock = pygame.time.Clock()

    state = _demo_state() if demo else SharedGameState()

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
