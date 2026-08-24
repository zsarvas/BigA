"""
Detect bad KMS/DRM / SDL states and recover.

Vertical jitter and black panels are often stale Plymouth/mpv DRM clients, or
SDL ``Draw call returned Invalid argument`` after mpv↔pygame handoff.  We
cannot see a physical bounce, but we can detect hung mpv, draw-call spam, and
pygame flip failures.

Recovery ladder:
  1. ``systemctl restart biga`` for DRM-ownership issues (orphan mpv, etc.).
  2. Full ``shutdown -r now`` after several consecutive hard display failures
     in a row (persisted across service restarts in ``/tmp``).
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

from . import config

log = logging.getLogger(__name__)

_DRM_CARD = Path("/dev/dri/card0")
_PLYMOUTH_PID = Path("/run/plymouth/pid")
_RESTART_STAMP = Path("/tmp/biga-drm-restart.ts")
_REBOOT_STAMP = Path("/tmp/biga-drm-reboot.ts")
_FAIL_COUNT_PATH = Path("/tmp/biga-display-fail.count")
_COOLDOWN_SEC = int(os.environ.get("BIGA_DRM_RESTART_COOLDOWN_SEC", "1800"))
_CHECK_INTERVAL_SEC = float(os.environ.get("BIGA_DRM_CHECK_INTERVAL_SEC", "30"))
_POST_MPV_GRACE_SEC = float(os.environ.get("BIGA_DRM_POST_MPV_GRACE_SEC", "5"))
_PLYMOUTH_GRACE_AFTER_BOOT_SEC = float(
    os.environ.get("BIGA_DRM_PLYMOUTH_GRACE_SEC", "90")
)
_CONSECUTIVE_NEEDED = int(os.environ.get("BIGA_DRM_CONSECUTIVE_ISSUES", "2"))

_DRAW_FAIL_MARKERS = (
    "Draw call returned Invalid argument",
    "Expect corruption",
)


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


def _read_fail_count() -> int:
    try:
        return max(0, int(_FAIL_COUNT_PATH.read_text().strip()))
    except (OSError, ValueError):
        return 0


def _write_fail_count(n: int) -> None:
    try:
        _FAIL_COUNT_PATH.write_text(str(max(0, n)))
    except OSError:
        pass


_FAIL_DEBOUNCE_SEC = 15.0
_last_fail_mono = 0.0


def note_display_ok() -> None:
    """A successful pygame flip / mpv handoff — reset the failure streak."""
    global _last_fail_mono
    if _read_fail_count():
        log.info("display recovered — clearing failure streak")
    _write_fail_count(0)
    _last_fail_mono = 0.0


def note_display_failure(detail: str) -> int:
    """
    Record a hard display failure (hung mpv, SDL draw-call, pygame flip error).

    SDL can print the same draw-call line many times per clip; we debounce so
    one wedged handoff counts once. Returns the new consecutive count.
    """
    global _last_fail_mono
    if not enabled():
        return 0
    now = time.monotonic()
    if _last_fail_mono and now - _last_fail_mono < _FAIL_DEBOUNCE_SEC:
        return _read_fail_count()
    _last_fail_mono = now
    n = _read_fail_count() + 1
    _write_fail_count(n)
    log.warning("display failure %d/%d: %s", n, config.DISPLAY_FAIL_REBOOT_COUNT, detail)
    if n >= config.DISPLAY_FAIL_REBOOT_COUNT:
        request_reboot(f"consecutive display failures ({n}): {detail}")
    elif n >= _CONSECUTIVE_NEEDED:
        _maybe_restart_service([DrmIssue("display_fail", detail)], reason="display-fail")
    return n


def stderr_looks_like_draw_fail(text: str) -> bool:
    return any(m in text for m in _DRAW_FAIL_MARKERS)


class _StderrWatch:
    """Tee stderr so SDL's unprefixed 'Draw call returned Invalid argument' is seen."""

    def __init__(self, inner: object) -> None:
        self._inner = inner

    def write(self, s: str) -> int:
        if s and stderr_looks_like_draw_fail(s):
            note_display_failure(s.strip()[:180])
        w = getattr(self._inner, "write", None)
        if callable(w):
            return int(w(s) or 0)
        return 0

    def flush(self) -> None:
        f = getattr(self._inner, "flush", None)
        if callable(f):
            f()

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


def install_stderr_watch() -> None:
    if not enabled():
        return
    if isinstance(sys.stderr, _StderrWatch):
        return
    sys.stderr = _StderrWatch(sys.stderr)  # type: ignore[misc, assignment]


def request_reboot(reason: str) -> None:
    """Full Pi reboot — last resort when KMS is wedged (biga runs as root)."""
    if not enabled():
        log.warning("reboot skipped (DRM health disabled): %s", reason)
        return
    if _in_cooldown(_REBOOT_STAMP, config.DISPLAY_REBOOT_COOLDOWN_SEC):
        log.warning(
            "reboot suppressed (cooldown %ds): %s",
            config.DISPLAY_REBOOT_COOLDOWN_SEC,
            reason,
        )
        return
    _touch_stamp(_REBOOT_STAMP)
    log.error("requesting full reboot: %s", reason)
    try:
        subprocess.Popen(
            ["/sbin/shutdown", "-r", "now"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        log.error("shutdown -r failed: %s", exc)
        return
    sys.exit(0)


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
            _maybe_restart_service(issues, reason="post-mpv")

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
                _maybe_restart_service(issues, reason="periodic")
        else:
            self._consecutive = 0


def _maybe_restart_service(issues: list[DrmIssue], *, reason: str) -> None:
    if _in_cooldown(_RESTART_STAMP, _COOLDOWN_SEC):
        log.warning(
            "DRM restart suppressed (cooldown %ds): %s",
            _COOLDOWN_SEC,
            reason,
        )
        return

    summary = "; ".join(f"{i.code}: {i.detail}" for i in issues)
    log.error("requesting biga restart (%s): %s", reason, summary)
    _touch_stamp(_RESTART_STAMP)
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


def _in_cooldown(stamp: Path, seconds: int) -> bool:
    try:
        last = float(stamp.read_text().strip())
    except (OSError, ValueError):
        return False
    return time.time() - last < seconds


def _touch_stamp(stamp: Path) -> None:
    try:
        stamp.write_text(str(time.time()))
    except OSError:
        pass
