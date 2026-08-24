from __future__ import annotations


def test_clock_non_linux_is_synced(monkeypatch):
    from pi_tracker import clock

    monkeypatch.setattr(clock.platform, "system", lambda: "Darwin")
    assert clock.clock_is_synchronized() is True


def test_clock_linux_requires_flag_or_timedatectl(monkeypatch, tmp_path):
    from pi_tracker import clock
    import subprocess

    monkeypatch.setattr(clock.platform, "system", lambda: "Linux")
    monkeypatch.setattr(clock, "_SYNC_FLAG", tmp_path / "synchronized")
    monkeypatch.setattr(
        clock.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(["timedatectl"], 0, stdout="no\n", stderr=""),
    )
    clock._synced_cached = False
    assert clock.clock_is_synchronized() is False

    (tmp_path / "synchronized").write_text("")
    clock._synced_cached = False
    assert clock.clock_is_synchronized() is True


def test_clock_timedatectl_yes(monkeypatch, tmp_path):
    from pi_tracker import clock
    import subprocess

    monkeypatch.setattr(clock.platform, "system", lambda: "Linux")
    monkeypatch.setattr(clock, "_SYNC_FLAG", tmp_path / "missing")
    monkeypatch.setattr(
        clock.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(["timedatectl"], 0, stdout="yes\n", stderr=""),
    )
    clock._synced_cached = False
    assert clock.clock_is_synchronized() is True
