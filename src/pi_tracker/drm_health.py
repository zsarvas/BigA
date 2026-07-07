"""
Detect bad KMS/DRM handoff states and restart biga when they persist.

Vertical display jitter on the Pi panel has been cleared by ``systemctl restart
biga`` — usually stale Plymouth/mpv DRM clients after a handoff.  We cannot
see physical jitter in software, but we can detect the DRM states that precede
it and restart before the user notices.
"""

from __future__ import annotations

import logging
import os
import platform
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

_DRM_CARD = Path("/dev/dri/card0")
_PLYMOUTH_PID = Path("/run/plymouth/pid")
_RESTART_STAMP = Path("/tmp/biga-drm-restart.ts")
_COOLDOWN_SEC = int(os.environ.get("BIGA_DRM_RESTART_COOLDOWN_SEC", "1800"))
_CHECK_INTERVAL_SEC = float(os.environ.get("BIGA_DRM_CHECK_INTERVAL_SEC", "30"))
_POST_MPV_GRACE_SEC = float(os.environ.get("BIGA_DRM_POST_MPV_GRACE_SEC", "5"))
_PLYMOUTH_GRACE_AFTER_BOOT_SEC = float(
    os.environ.get("BIGA_DRM_PLYMOUTH_GRACE_SEC", "90")
)
_CONSECUTIVE_NEEDED = int(os.environ.get("BIGA_DRM_CONSECUTIVE_ISSUES", "2"))


def enabled() -> bool:
    if platform.system() != "Linux":
        return False
    flag = os.environ.get("BIGA_DRM_HEALTH", "1").strip().lower()
    return flag not in ("0", "false", "no", "off")


@dataclass(frozen=True)
class DrmIssue:
    code: str
    detail: str


def _pid_comm(pid: int) -> str:
    try:
        return (Path(f"/proc/{pid}/comm").read_text()).strip()
    except OSError:
        return "?"


def drm_holder_pids(card: Path = _DRM_CARD) -> set[int]:
    if not card.exists():
        return set()
    try:
        proc = subprocess.run(
            ["fuser", str(card)],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return set()
    text = f"{proc.stdout} {proc.stderr}"
    return {int(tok) for tok in text.split() if tok.isdigit()}


def plymouth_active() -> bool:
    return _PLYMOUTH_PID.is_file()


def collect_issues(
    *,
    mpv_playback_active: bool,
    boot_monotonic: float,
    own_pid: int | None = None,
) -> list[DrmIssue]:
    """Return DRM anomalies that may cause panel jitter (empty if healthy)."""
    if not enabled() or not _DRM_CARD.exists():
        return []

    own_pid = own_pid if own_pid is not None else os.getpid()
    issues: list[DrmIssue] = []
    holders = drm_holder_pids()
    by_comm = {pid: _pid_comm(pid) for pid in holders}

    uptime = time.monotonic() - boot_monotonic
    if plymouth_active() and uptime > _PLYMOUTH_GRACE_AFTER_BOOT_SEC:
        issues.append(
            DrmIssue("plymouth_stuck", f"plymouth still running after {uptime:.0f}s")
        )

    mpv_pids = [pid for pid, comm in by_comm.items() if comm == "mpv"]
    if not mpv_playback_active and mpv_pids:
        issues.append(
            DrmIssue("orphan_mpv", f"mpv holding DRM outside playback: {mpv_pids}")
        )

    py_pids = [pid for pid, comm in by_comm.items() if comm in ("python3", "python")]
    if not mpv_playback_active:
        if holders and not py_pids:
            issues.append(
                DrmIssue(
                    "missing_python_drm",
                    f"DRM held by {by_comm}, not python3 (pid {own_pid})",
                )
            )
        elif mpv_pids and py_pids:
            issues.append(
                DrmIssue(
                    "mpv_python_overlap",
                    f"mpv and python3 both on DRM: mpv={mpv_pids} py={py_pids}",
                )
            )
        elif len(holders) > 1 and not mpv_pids:
            issues.append(
                DrmIssue("multiple_drm_clients", f"holders={by_comm}")
            )

    return issues


class DrmHealthMonitor:
    """Periodic checker; restarts biga after consecutive issue samples."""

    def __init__(self, boot_monotonic: float | None = None) -> None:
        self._boot = boot_monotonic if boot_monotonic is not None else time.monotonic()
        self._last_check = 0.0
        self._consecutive = 0
        self._post_mpv_until = 0.0

    def note_mpv_finished(self) -> None:
        self._post_mpv_until = time.monotonic() + _POST_MPV_GRACE_SEC

    def check_after_mpv(self, *, mpv_playback_active: bool) -> None:
        if not enabled():
            return
        issues = collect_issues(
            mpv_playback_active=mpv_playback_active,
            boot_monotonic=self._boot,
        )
        if issues:
            log.warning(
                "post-mpv DRM check: %s",
                "; ".join(f"{i.code}({i.detail})" for i in issues),
            )
            self._consecutive = max(self._consecutive, _CONSECUTIVE_NEEDED)
            self._maybe_restart(issues, reason="post-mpv")

    def tick(self, *, mpv_playback_active: bool) -> None:
        if not enabled():
            return
        now = time.monotonic()
        if now < self._post_mpv_until:
            return
        if now - self._last_check < _CHECK_INTERVAL_SEC:
            return
        self._last_check = now

        issues = collect_issues(
            mpv_playback_active=mpv_playback_active,
            boot_monotonic=self._boot,
        )
        if issues:
            self._consecutive += 1
            log.warning(
                "DRM health (%d/%d): %s",
                self._consecutive,
                _CONSECUTIVE_NEEDED,
                "; ".join(f"{i.code}({i.detail})" for i in issues),
            )
            if self._consecutive >= _CONSECUTIVE_NEEDED:
                self._maybe_restart(issues, reason="periodic")
        else:
            self._consecutive = 0

    def _maybe_restart(self, issues: list[DrmIssue], *, reason: str) -> None:
        if _in_cooldown():
            log.warning(
                "DRM restart suppressed (cooldown %ds): %s",
                _COOLDOWN_SEC,
                reason,
            )
            self._consecutive = 0
            return

        summary = "; ".join(f"{i.code}: {i.detail}" for i in issues)
        log.error("requesting biga restart (%s): %s", reason, summary)
        _touch_restart_stamp()
        try:
            subprocess.Popen(
                ["/bin/systemctl", "restart", "biga"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            log.error("systemctl restart failed: %s", exc)
            return
        sys.exit(0)


def _in_cooldown() -> bool:
    try:
        last = float(_RESTART_STAMP.read_text().strip())
    except (OSError, ValueError):
        return False
    return time.time() - last < _COOLDOWN_SEC


def _touch_restart_stamp() -> None:
    try:
        _RESTART_STAMP.write_text(str(time.time()))
    except OSError:
        pass
