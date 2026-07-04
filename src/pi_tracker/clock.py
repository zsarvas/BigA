"""
System-clock synchronization check.

The Pi Zero 2W has no battery-backed RTC. On a fresh boot the kernel clock
holds a stale ``fake-hwclock`` value (whatever was baked into the image / last
saved) until ``systemd-timesyncd`` gets its first NTP sample. Until then
``date.today()`` can be days or weeks off, which makes the schedule / recap
logic act on the wrong "today" — e.g. locking an old game's final scene and
downloading its highlights. Every date-based decision must wait for this.
"""

from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path

log = logging.getLogger(__name__)

# systemd-timesyncd touches this file once the clock is NTP-synchronized.
_SYNC_FLAG = Path("/run/systemd/timesync/synchronized")

# Cache a positive result — once the clock is synced this boot it stays synced,
# so we avoid re-running timedatectl on every poll.
_synced_cached = False


def clock_is_synchronized() -> bool:
    """True once the system clock has been NTP-synchronized this boot."""
    global _synced_cached
    if _synced_cached:
        return True

    if _SYNC_FLAG.exists():
        _synced_cached = True
        return True

    # Fallback for non-timesyncd setups (chrony/ntpd) or when the flag path
    # differs: ask timedatectl directly.
    try:
        out = subprocess.run(
            ["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if out.stdout.strip() == "yes":
        _synced_cached = True
        return True
    return False


def wait_for_clock_sync(timeout: float = 0.0, poll_sec: float = 2.0) -> bool:
    """
    Block until the clock is synced (or *timeout* seconds elapse; 0 = forever).

    Returns True if synchronized. Callers on background threads can pass a
    timeout so they stay responsive to shutdown.
    """
    deadline = time.monotonic() + timeout if timeout > 0 else None
    while not clock_is_synchronized():
        if deadline is not None and time.monotonic() >= deadline:
            return False
        time.sleep(poll_sec)
    return True
