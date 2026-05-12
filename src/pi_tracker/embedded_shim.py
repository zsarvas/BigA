"""
Headless / Pi bootstrap — run before ``import pygame``.

Pygame's font stack can shell out to ``fc-list`` (fontconfig). On Raspberry Pi OS
Lite that call often blocks or times out for tens of seconds. We short-circuit
only ``fc-list`` invocations with an immediate empty result on real Pis (see
``/proc/device-tree/model``), or when ``BIGA_STUB_FC_LIST=1``.
"""

from __future__ import annotations

import io
import os
import subprocess
from pathlib import Path

_INSTALLED = False
_REAL_POPEN = subprocess.Popen


def _is_raspberry_pi() -> bool:
    model = Path("/proc/device-tree/model")
    try:
        if not model.is_file():
            return False
        raw = model.read_bytes().replace(b"\x00", b"").decode("utf-8", errors="ignore").lower()
        return "raspberry" in raw
    except OSError:
        return False


def _want_fc_list_stub() -> bool:
    if os.environ.get("BIGA_ALLOW_FC_LIST", "").strip().lower() in ("1", "true", "yes"):
        return False
    if os.environ.get("BIGA_STUB_FC_LIST", "").strip().lower() in ("1", "true", "yes"):
        return True
    return _is_raspberry_pi()


def _cmd_is_fc_list(cmd: object, shell: bool | None) -> bool:
    if shell and isinstance(cmd, str) and "fc-list" in cmd:
        return True
    if isinstance(cmd, (list, tuple)) and cmd:
        head = cmd[0]
        if isinstance(head, bytes):
            try:
                s = head.decode("utf-8", errors="ignore")
            except Exception:
                return False
        elif isinstance(head, str):
            s = head
        else:
            return False
        return s == "fc-list" or s.rstrip().endswith("/fc-list")
    return False


class _FcListDummy:
    """Minimal stand-in for subprocess.Popen used by pygame's fc-list probe."""

    returncode = 0

    def __init__(self) -> None:
        self.stdout = io.BytesIO(b"")
        self.stderr = io.BytesIO(b"")

    def communicate(self, input=None, timeout=None):  # noqa: ARG002
        return (b"", b"")

    def kill(self) -> None:
        pass

    def wait(self, timeout=None):  # noqa: ARG002
        return 0

    def poll(self) -> int:
        return 0


def _popen_shim(cmd, *args, **kwargs):  # noqa: ANN001
    shell = kwargs.get("shell")
    if _cmd_is_fc_list(cmd, shell):
        return _FcListDummy()
    return _REAL_POPEN(cmd, *args, **kwargs)


def install_fc_list_stub_if_needed() -> None:
    """Monkeypatch subprocess.Popen so pygame's fontconfig probe does not hang."""
    global _INSTALLED
    if _INSTALLED:
        return
    if not _want_fc_list_stub():
        return
    subprocess.Popen = _popen_shim  # type: ignore[method-assign]
    _INSTALLED = True
    os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
