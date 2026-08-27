from __future__ import annotations

import importlib.util
import threading
import time
from pathlib import Path

from helpers_mlb import live_feed

ROOT = Path(__file__).resolve().parents[1]


def test_ota_setup_rev_is_positive_int():
    rev = (ROOT / "scripts" / "ota_setup.rev").read_text().strip()
    assert int(rev) >= 1


def test_update_biga_tracks_origin_main():
    text = (ROOT / "scripts" / "update_biga.sh").read_text()
    assert "origin/main" in text
    assert "maybe_ota_setup" in text
    assert "ota_setup.rev" in text


def test_connectivity_joins_saved_network(monkeypatch, wifi_paths):
    spec = importlib.util.spec_from_file_location(
        "check_wifi_connectivity", ROOT / "scripts" / "check_wifi_connectivity.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    import wifi_store

    wifi_store.save_networks([{"ssid": "Home", "password": "x"}])
    monkeypatch.setattr(mod, "ensure_ssh_running", lambda: None)
    monkeypatch.setattr(mod, "seed_wifi_creds_from_nm", lambda: True)
    monkeypatch.setattr(mod, "has_networks", lambda: True)
    monkeypatch.setattr(mod, "load_networks", wifi_store.load_networks)
    monkeypatch.setattr(mod, "sync_nm_profiles", lambda nets: None)
    monkeypatch.setattr(mod, "connect_saved_networks", lambda: True)
    exited = []
    monkeypatch.setattr(mod, "exit_provisioning", lambda: exited.append(True))
    monkeypatch.setattr(mod, "enter_provisioning", lambda: (_ for _ in ()).throw(AssertionError("should join")))
    assert mod.main() == 0
    assert exited


def test_connectivity_honors_provisioning_flag(monkeypatch, wifi_paths):
    spec = importlib.util.spec_from_file_location(
        "check_wifi_connectivity", ROOT / "scripts" / "check_wifi_connectivity.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    monkeypatch.setattr(mod, "ensure_ssh_running", lambda: None)
    monkeypatch.setattr(mod, "seed_wifi_creds_from_nm", lambda: True)
    monkeypatch.setattr(mod, "is_provisioning", lambda: True)
    monkeypatch.setattr(mod, "has_networks", lambda: True)
    monkeypatch.setattr(
        mod, "connect_saved_networks", lambda: (_ for _ in ()).throw(AssertionError("must not join"))
    )
    ap = []
    monkeypatch.setattr(mod, "prepare_ap_provisioning_mode", lambda: ap.append(True))
    monkeypatch.setattr(mod, "exit_provisioning", lambda: (_ for _ in ()).throw(AssertionError("keep flag")))
    assert mod.main() == 0
    assert ap


def test_connectivity_reenters_ap_when_join_fails(monkeypatch, wifi_paths):
    spec = importlib.util.spec_from_file_location(
        "check_wifi_connectivity", ROOT / "scripts" / "check_wifi_connectivity.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    monkeypatch.setattr(mod, "ensure_ssh_running", lambda: None)
    monkeypatch.setattr(mod, "seed_wifi_creds_from_nm", lambda: False)
    monkeypatch.setattr(mod, "has_networks", lambda: True)
    monkeypatch.setattr(mod, "load_networks", lambda: [{"ssid": "Office", "password": ""}])
    monkeypatch.setattr(mod, "sync_nm_profiles", lambda nets: None)
    monkeypatch.setattr(mod, "connect_saved_networks", lambda: False)
    entered = []
    monkeypatch.setattr(mod, "enter_provisioning", lambda: entered.append("prov"))
    monkeypatch.setattr(mod, "prepare_ap_provisioning_mode", lambda: entered.append("ap"))
    assert mod.main() == 0
    assert entered == ["prov", "ap"]
    import wifi_store

    trail = wifi_store.LAST_WIFI_LOG.read_text()
    assert "boot: join failed" in trail
    assert "sudo cat /var/log/biga-wifi-last.log" in trail


def test_game_day_poller_idle_helpers_are_imported():
    """#121 dropped these names and idle never flipped to live (NameError each poll)."""
    from pi_tracker import game_day_poller

    for name in (
        "fetch_angels_schedule_for_date",
        "find_todays_scoreboard_angels_game",
        "live_transition_from_schedule_game",
        "find_todays_final_angels_game",
        "patch_from_final_schedule_game",
        "refresh_idle_schedule",
        "SharedGameState",
    ):
        assert hasattr(game_day_poller, name), name


def test_idle_transitions_to_live_when_schedule_in_progress(monkeypatch):
    from helpers_mlb import schedule_game, schedule_payload
    from pi_tracker import game_day_poller
    from pi_tracker.state import SharedGameState

    live_g = schedule_game(pk=823989, abstract="Live", detailed="In Progress")
    monkeypatch.setattr(game_day_poller, "clock_is_synchronized", lambda: True)
    monkeypatch.setattr(
        game_day_poller,
        "fetch_angels_schedule_for_date",
        lambda *a, **k: schedule_payload(live_g),
    )
    monkeypatch.setattr(game_day_poller, "IDLE_WATCH_SEC", 0.05)

    state = SharedGameState(persist=False)
    assert state.snapshot()["scene"] == "idle"
    stop = threading.Event()
    t = threading.Thread(target=game_day_poller.game_day_loop, args=(state, stop), daemon=True)
    t.start()
    deadline = time.time() + 3
    scene = ""
    while time.time() < deadline:
        scene = str(state.snapshot().get("scene"))
        if scene == "live":
            break
        time.sleep(0.02)
    stop.set()
    t.join(1)
    assert scene == "live"
    assert state.snapshot().get("live_game_pk") == 823989


def test_game_day_live_final_stops_clips(monkeypatch):
    from pi_tracker import game_day_poller, playback
    from pi_tracker.state import SharedGameState

    feed = live_feed(abstract="Final", detailed="Final", away_runs=1, home_runs=6)
    monkeypatch.setattr(game_day_poller, "clock_is_synchronized", lambda: True)
    monkeypatch.setattr(game_day_poller, "fetch_live_feed_v11", lambda pk: feed)
    monkeypatch.setattr(game_day_poller, "LIVE_POLL_SEC", 0.05)

    state = SharedGameState(persist=False)
    state.update(scene="live", live_game_pk=777)
    stop = threading.Event()
    t = threading.Thread(target=game_day_poller.game_day_loop, args=(state, stop), daemon=True)
    t.start()
    deadline = time.time() + 3
    scene = ""
    while time.time() < deadline:
        scene = str(state.snapshot().get("scene"))
        if scene == "win":
            break
        time.sleep(0.02)
    stop.set()
    t.join(1)
    assert scene == "win"
    assert playback.clips_stop_requested()
    assert playback.stop_reason() == "game final"


def test_game_day_unsynced_clock_drops_final(monkeypatch):
    from pi_tracker import game_day_poller
    from pi_tracker.state import SharedGameState

    monkeypatch.setattr(game_day_poller, "clock_is_synchronized", lambda: False)
    monkeypatch.setattr(game_day_poller, "IDLE_WATCH_SEC", 0.05)
    state = SharedGameState(persist=False)
    state.update(scene="win", final_display_date="2026-08-18")
    stop = threading.Event()
    t = threading.Thread(target=game_day_poller.game_day_loop, args=(state, stop), daemon=True)
    t.start()
    deadline = time.time() + 2
    scene = ""
    while time.time() < deadline:
        scene = str(state.snapshot().get("scene"))
        if scene == "idle":
            break
        time.sleep(0.02)
    stop.set()
    t.join(1)
    assert scene == "idle"


def test_gpio_env_int_clamp(monkeypatch):
    from pi_tracker import gpio_leds

    monkeypatch.setenv("BIGA_HR_WIN_CYCLES", "99")
    assert gpio_leds._env_int("BIGA_HR_WIN_CYCLES", 2, lo=1, hi=10) == 2
    monkeypatch.setenv("BIGA_HR_WIN_CYCLES", "3")
    assert gpio_leds._env_int("BIGA_HR_WIN_CYCLES", 2, lo=1, hi=10) == 3
    assert gpio_leds._HR_WIN_CYCLES >= 1
