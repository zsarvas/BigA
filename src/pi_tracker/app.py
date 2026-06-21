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
from pathlib import Path

from .embedded_shim import install_fc_list_stub_if_needed

install_fc_list_stub_if_needed()
os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"

from .bootstrap_sdl import configure_sdl

configure_sdl()

import pygame

from . import config
from .assets import AssetManager, _repo_font
from .mlb_http import ANGELS_TEAM_ID as TRACKED_TEAM_ID
from .mlb_schedule import try_restore_final_scene_for_today
from .state import SharedGameState
from .team_config import tracked_team_abbr, tracked_team_name
from . import mouse_hide
from . import playback
from .gpio_leds import cleanup_gpio, init_gpio, is_muted, set_win_led
from .mlb_highlights import HighlightDownloader, is_valid_highlight_mp4, seed_idle_recap_from_schedule, sync_highlight_downloader
from .scenes import FinalLossScene, FinalWinScene, IdleScene, LiveScene
from .scenes._clip_player import clip_title_from_path


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


_NOW_SHOWING_COLOR = (255, 50, 50)
_NOW_SHOWING_HOLD_SEC = 0.5
_MPV_LOG = Path("/tmp/biga-mpv.log")
# Brief pause after mpv exits so vo=drm releases KMS master before pygame KMSDRM init.
_MPV_DRM_HANDOFF_SEC = 0.2


def _draw_now_showing_transition(screen: pygame.Surface, title: str) -> None:
    """Brief full-screen card before handing the display to mpv."""
    w, _h = screen.get_size()
    screen.fill(config.BLACK)

    head_font = _repo_font(config.layout_size(22))
    body_font = _repo_font(config.layout_size(13))

    head = head_font.render("NOW SHOWING", True, _NOW_SHOWING_COLOR)
    screen.blit(head, head.get_rect(center=(w // 2, config.layout_y(96))))

    # ~26 chars fits two lines on 480px at this font size.
    wrapped = textwrap.fill(title, width=26)
    lines = wrapped.split("\n")
    max_lines = 3
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        if not lines[-1].endswith("…"):
            lines[-1] = lines[-1].rstrip() + "…"

    line_h = body_font.get_height() + 2
    block_h = len(lines) * line_h - 2
    y = config.layout_y(132) - block_h // 2
    for line in lines:
        surf = body_font.render(line, True, config.WHITE)
        screen.blit(surf, surf.get_rect(center=(w // 2, y + line_h // 2)))
        y += line_h

    pygame.display.flip()
    time.sleep(_NOW_SHOWING_HOLD_SEC)


def _filter_valid_clip_paths(paths: list[Path]) -> list[Path]:
    good: list[Path] = []
    for path in paths:
        if path.suffix.lower() == ".mp4" and not is_valid_highlight_mp4(path):
            logging.warning("skipping corrupt highlight clip: %s", path.name)
            path.unlink(missing_ok=True)
        else:
            good.append(path)
    return good


def _mpv_cmd(w: int, h: int, *, on_pi: bool) -> list[str]:
    cmd = [
        "mpv",
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
    else:
        cmd.append("--ao=alsa,null")
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
    show_transition: bool = False,
) -> "pygame.Surface":
    """
    Play one or more clips in a single mpv process (one DRM handoff).

    mpv plays multiple files back-to-back without exiting — avoids ~15s black
    gaps from pygame quit/init between each clip on the Pi panel.
    """
    paths = _filter_valid_clip_paths(paths)
    if not paths:
        return screen

    size = screen.get_size()
    if show_transition:
        _draw_now_showing_transition(screen, clip_title_from_path(paths[0]))
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
    screen = pygame.display.set_mode(size, flags)
    mouse_hide.handoff_from_mpv(screen, fill=config.BLACK)
    return screen


def _play_mpv(
    path: Path,
    screen: pygame.Surface,
    flags: int,
    *,
    show_transition: bool = True,
) -> "pygame.Surface":
    """Hand the display to mpv for one clip, then reclaim it."""
    return _play_mpv_sequence([path], screen, flags, show_transition=show_transition)


def _collect_break_clip_batch(scene: object, state: SharedGameState, first: Path) -> list[Path]:
    """Gather every clip already on disk for one mpv run (gapless within batch)."""
    batch = [first]
    while True:
        snap = state.snapshot()
        if not scene.in_inning_break(snap):  # type: ignore[attr-defined]
            break
        scene._maybe_queue_clip(snap)  # type: ignore[attr-defined]
        nxt = scene._pending_clip  # type: ignore[attr-defined]
        if nxt is None:
            break
        batch.append(nxt)
        scene._pending_clip = None  # type: ignore[attr-defined]
    return batch


def _play_live_break_reel(
    scene: object,
    state: SharedGameState,
    screen: pygame.Surface,
    flags: int,
    first_path: Path,
) -> "pygame.Surface":
    """Chain highlight batches until the half-inning break ends or clips run out."""
    pending: Path | None = first_path
    while scene.in_inning_break(state.snapshot()):  # type: ignore[attr-defined]
        if pending is None:
            scene._maybe_queue_clip(state.snapshot())  # type: ignore[attr-defined]
            pending = scene._pending_clip  # type: ignore[attr-defined]
            if pending is None:
                if playback.is_transcode_busy():
                    time.sleep(0.25)
                    continue
                break
        scene._pending_clip = None  # type: ignore[attr-defined]
        batch = _collect_break_clip_batch(scene, state, pending)
        pending = None
        screen = _play_mpv_sequence(batch, screen, flags)
    return screen


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
    if not demo:
        _hl_downloader, _last_live_pk = sync_highlight_downloader(
            state.snapshot(), None, 0
        )
        s0 = state.snapshot()
        _last_dl_key = (
            str(s0.get("scene", "idle")),
            int(s0.get("live_game_pk") or 0),
            int(s0.get("next_game_pk") or 0),
        )
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

            # Queued clip(s): live breaks batch into one mpv run to avoid DRM black gaps.
            pending = getattr(scene, "_pending_clip", None)
            if pending and not playback.is_transcode_busy():
                snap = state.snapshot()
                in_break = scene_key == "live" and getattr(
                    scene, "in_inning_break", lambda _s: False
                )(snap)
                if in_break:
                    screen = _play_live_break_reel(scene, state, screen, display_flags, pending)
                else:
                    scene._pending_clip = None  # type: ignore[attr-defined]
                    screen = _play_mpv(pending, screen, display_flags)

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
