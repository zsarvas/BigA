from __future__ import annotations

import pytest


def test_enabled_false_on_non_linux(monkeypatch):
    from pi_tracker import drm_health

    monkeypatch.setattr(drm_health.platform, "system", lambda: "Darwin")
    assert drm_health.enabled() is False


def test_enabled_honors_env(monkeypatch):
    from pi_tracker import drm_health

    monkeypatch.setattr(drm_health.platform, "system", lambda: "Linux")
    monkeypatch.setenv("BIGA_DRM_HEALTH", "0")
    assert drm_health.enabled() is False
    monkeypatch.setenv("BIGA_DRM_HEALTH", "1")
    assert drm_health.enabled() is True


def test_stderr_draw_fail_markers():
    from pi_tracker.drm_health import stderr_looks_like_draw_fail

    assert stderr_looks_like_draw_fail("Draw call returned Invalid argument")
    assert stderr_looks_like_draw_fail("Expect corruption after this")
    assert not stderr_looks_like_draw_fail("all good")


def test_note_display_failure_reboots_after_streak(monkeypatch, tmp_path):
    from pi_tracker import config, drm_health

    monkeypatch.setattr(drm_health, "enabled", lambda: True)
    monkeypatch.setattr(drm_health, "_FAIL_COUNT_PATH", tmp_path / "fail.count")
    monkeypatch.setattr(drm_health, "_FAIL_DEBOUNCE_SEC", 0.0)
    monkeypatch.setattr(config, "DISPLAY_FAIL_REBOOT_COUNT", 3)
    drm_health._last_fail_mono = 0.0
    reboots: list[str] = []
    monkeypatch.setattr(drm_health, "request_reboot", lambda reason: reboots.append(reason))
    monkeypatch.setattr(drm_health, "_maybe_restart_service", lambda *a, **k: None)

    assert drm_health.note_display_failure("a") == 1
    assert drm_health.note_display_failure("b") == 2
    assert drm_health.note_display_failure("c") == 3
    assert reboots
    drm_health.note_display_ok()
    assert drm_health._read_fail_count() == 0


def test_collect_issues_empty_when_disabled_or_no_card(monkeypatch, tmp_path):
    from pi_tracker import drm_health

    monkeypatch.setattr(drm_health, "enabled", lambda: True)
    monkeypatch.setattr(drm_health, "_DRM_CARD", tmp_path / "missing-card")
    assert drm_health.collect_issues(mpv_playback_active=False, boot_monotonic=0) == []


def test_request_reboot_skipped_when_disabled(monkeypatch):
    from pi_tracker import drm_health

    monkeypatch.setattr(drm_health, "enabled", lambda: False)
    called = []
    monkeypatch.setattr(drm_health.subprocess, "Popen", lambda *a, **k: called.append(a))
    drm_health.request_reboot("test")
    assert called == []


def test_request_reboot_exits(monkeypatch, tmp_path):
    from pi_tracker import drm_health

    monkeypatch.setattr(drm_health, "enabled", lambda: True)
    monkeypatch.setattr(drm_health, "_REBOOT_STAMP", tmp_path / "reboot.ts")
    monkeypatch.setattr(drm_health, "_in_cooldown", lambda *a, **k: False)
    pops = []
    monkeypatch.setattr(drm_health.subprocess, "Popen", lambda cmd, **k: pops.append(cmd))
    with pytest.raises(SystemExit):
        drm_health.request_reboot("wedged")
    assert pops[0][:2] == ["/sbin/shutdown", "-r"]
