"""
Pygame main loop: 480×320 landscape, scene state machine.

Dev: keys 1–4 switch scenes. On Pi, set SDL_VIDEODRIVER / SDL_FBDEV per notes.
"""

from __future__ import annotations

import sys
import threading

import pygame

from . import config
from .assets import AssetManager
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
    screen = pygame.display.set_mode((config.SCREEN_WIDTH, config.SCREEN_HEIGHT), flags)
    clock = pygame.time.Clock()

    state = _demo_state() if demo else SharedGameState()

    team_ids = {
        int(state.snapshot().get("away_team_id", 0)),
        int(state.snapshot().get("home_team_id", 0)),
        108,
    }
    assets = AssetManager()
    assets.load(team_ids)

    scenes = {
        "idle": IdleScene(),
        "live": LiveScene(),
        "win": FinalWinScene(),
        "loss": FinalLossScene(),
    }

    stop_schedule = threading.Event()
    schedule_thread: threading.Thread | None = None
    if not demo and not no_schedule:
        from .schedule_poller import start_idle_schedule_poller

        schedule_thread = start_idle_schedule_poller(state, stop_schedule)

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
                    state.update(scene="live")
                elif event.key == pygame.K_3:
                    state.update(scene="win")
                elif event.key == pygame.K_4:
                    state.update(scene="loss")

        snap = state.snapshot()
        scene_key = str(snap.get("scene", "idle"))
        scene = scenes.get(scene_key, scenes["idle"])
        scene.draw(screen, assets, snap)
        pygame.display.flip()
        clock.tick(config.FPS)

    stop_schedule.set()
    if schedule_thread is not None:
        schedule_thread.join(timeout=3.0)

    pygame.quit()


if __name__ == "__main__":
    main()
