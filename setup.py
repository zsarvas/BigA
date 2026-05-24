import glob
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PANEL_INCLUDE = os.environ.get("BIGA_PANEL_INCLUDE", "mzp351hv00tr-old.txt")


def run(cmd, desc=None):
    if desc:
        print(f"  → {desc}")
    result = subprocess.run(cmd, shell=True)
    if result.returncode != 0:
        print(f"  ✗ Failed: {cmd}")
        sys.exit(1)


def _pip_break_system_flag() -> str:
    """``--break-system-packages`` exists only on pip 23+ (PEP 668). Omit on older Pi images."""
    help_proc = subprocess.run(
        ["pip3", "install", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    if help_proc.returncode == 0 and "--break-system-packages" in (help_proc.stdout or ""):
        return " --break-system-packages"
    return ""


def _externally_managed_python() -> bool:
    return any(glob.glob("/usr/lib/python3*/EXTERNALLY-MANAGED"))


def _boot_paths() -> tuple[str, str]:
    """(config.txt directory, overlays directory) for this Pi OS generation."""
    if os.path.isdir("/boot/firmware"):
        return "/boot/firmware", "/boot/firmware/overlays"
    return "/boot", "/boot/overlays"


def _sudo_read(path: str) -> str:
    proc = subprocess.run(["sudo", "cat", path], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        print(f"  ✗ Cannot read {path}: {proc.stderr.strip()}")
        sys.exit(1)
    return proc.stdout


def _sudo_write(path: str, content: str) -> None:
    tmp = "/tmp/biga-config-edit.txt"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    run(f"sudo cp {tmp} {path}", f"writing {path}")


def _strip_legacy_inline_panel(text: str) -> str:
    """
    Remove panel block wrongly appended by older setup (inline copy of mzp351hv00tr-*.txt).
    Safe to run when migrating to ``include mzp351hv00tr-old.txt``.
    """
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("dtoverlay=ads7846") and "penirq=27" in stripped:
            i += 1
            while i < len(lines):
                if lines[i].strip().startswith("hdmi_timings=480"):
                    i += 1
                    break
                i += 1
            continue
        out.append(line)
        i += 1
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out).rstrip()) + "\n"


def _strip_old_biga_markers(text: str) -> str:
    """Drop prior BigA snippet lines so re-run does not duplicate includes."""
    drop_prefixes = (
        "disable_fw_kms_setup=1",
        "dtparam=spi=on",
        "include mzp351hv00tr-old.txt",
        "include mzp351hv00tr-new.txt",
        "enable_uart=1",
    )
    lines = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("# BigA"):
            continue
        if any(s == p or s.startswith(p + " ") for p in drop_prefixes):
            continue
        lines.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines).rstrip()) + "\n"


def _install_panel_config(boot_dir: str, config_path: str) -> None:
    panel_name = DEFAULT_PANEL_INCLUDE
    panel_src = os.path.join(REPO, "boot", panel_name)
    if not os.path.isfile(panel_src):
        print(f"  ✗ Missing panel file in repo: boot/{panel_name}")
        sys.exit(1)

    run(f"sudo cp {panel_src} {boot_dir}/{panel_name}", f"installing {boot_dir}/{panel_name}")

    snippet_path = os.path.join(REPO, "config_append.txt")
    if not os.path.isfile(snippet_path):
        print("  ✗ config_append.txt not found in repo")
        sys.exit(1)

    with open(snippet_path, encoding="utf-8") as f:
        snippet_raw = f.read()
    # Allow env override: rewrite include line in snippet copy.
    snippet = snippet_raw.replace("mzp351hv00tr-old.txt", panel_name)

    current = _sudo_read(config_path)
    cleaned = _strip_legacy_inline_panel(current)
    cleaned = _strip_old_biga_markers(cleaned)

    missing: list[str] = []
    for line in snippet.strip().splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s not in cleaned:
            missing.append(s)

    if missing:
        block = (
            "\n\n# BigA panel + touch (480×320 DPI)\n"
            + "\n".join(missing)
            + "\n"
        )
        cleaned = cleaned.rstrip() + block
        _sudo_write(config_path, cleaned)
        print(f"  → {config_path} updated ({', '.join(missing)})")
    elif cleaned != current:
        _sudo_write(config_path, cleaned)
        print(f"  → {config_path} cleaned legacy inline panel block")
    else:
        print(f"  → {config_path} already has BigA snippet ({panel_name})")


print("=" * 50)
print("  BigA Angels Tracker — Setup")
print("=" * 50)

# 1. apt deps
print("\n[1/8] Installing system packages...")
run("sudo apt update -q")
run(
    "sudo apt install -y "
    "python3-pip "
    "python3-pygame "
    "fonts-dejavu-core "
    "libsdl2-dev "
    "libsdl2-image-dev "
    "libsdl2-ttf-dev "
    "libcairo2-dev "
    "pkg-config "
    "python3-dev",
    "apt packages",
)

# 2. pip deps (Pi-specific only; pygame comes from apt above, not requirements-pi.txt)
print("\n[2/8] Installing Python packages...")
pip_extra = _pip_break_system_flag()
if _externally_managed_python() and not pip_extra:
    print(
        "  ⚠ This OS marks Python as externally managed but pip is too old for "
        "--break-system-packages. If pip install fails, upgrade pip or use a venv."
    )
run(
    f"pip3 install -r {REPO}/requirements-pi.txt{pip_extra}",
    "pip requirements-pi.txt",
)

# 3. video group
print("\n[3/8] Configuring user permissions...")
run("sudo usermod -a -G video pi", "adding pi to video group")

# 4. timezone
print("\n[4/8] Setting timezone...")
run("sudo timedatectl set-timezone America/Los_Angeles", "timezone → America/Los_Angeles")

# 5. display drivers
print("\n[5/8] Installing display drivers...")
boot_dir, overlays_dir = _boot_paths()
print(f"  → boot dir: {boot_dir}")
overlays = os.path.join(REPO, "overlays")
if os.path.isdir(overlays) and os.listdir(overlays):
    run(f"sudo cp {overlays}/*.dtbo {overlays_dir}/", "copying .dtbo overlay files")
else:
    print("  ⚠ No overlay files found in overlays/ — skipping (panel uses include file)")

# 6. config.txt + panel include file
print("\n[6/8] Updating boot config + panel include...")
config_path = os.path.join(boot_dir, "config.txt")
if not os.path.exists(config_path):
    print(f"  ✗ {config_path} not found")
    sys.exit(1)
_install_panel_config(boot_dir, config_path)
print(
    f"\n  Diagnostics (SSH): sudo cat {config_path}\n"
    "  SSH daemon config is NOT here — use: sudo cat /etc/ssh/sshd_config\n"
    f"  Panel include on disk: sudo cat {boot_dir}/{DEFAULT_PANEL_INCLUDE}"
)

# 7. start script (fbcon + chvt 2 + openvt wrapper for systemd)
print("\n[7/8] Installing start script...")
start_script = f"""#!/bin/sh
set -eu
export PYTHONUNBUFFERED=1
export BIGA_SDL_FALLBACK_KMS=1
export SDL_VIDEODRIVER=fbcon
export SDL_FBDEV=/dev/fb0
export FRAMEBUFFER=/dev/fb0
exec >>/tmp/biga.log 2>&1
echo "biga-start $(date -Is)"
i=0
while [ ! -e /dev/fb0 ] && [ "$i" -lt 15 ]; do
  echo "waiting for /dev/fb0 ($i)..."
  i=$((i + 1))
  sleep 1
done
ls -l /dev/fb0 /dev/dri/card0 2>&1 || true
/usr/bin/chvt 2 || echo "chvt 2 failed with $?"
exec /usr/bin/openvt -c 2 -f -w -- /bin/sh -c "/usr/bin/python3 {REPO}/run_pi_ui.py --no-idle-videos >>/tmp/biga.log 2>&1; echo PYEXIT=$? >>/tmp/biga.log"
"""

with open("/tmp/biga-start.sh", "w", encoding="utf-8") as f:
    f.write(start_script)

run("sudo mv /tmp/biga-start.sh /usr/local/bin/biga-start.sh", "installing /usr/local/bin/biga-start.sh")
run("sudo chmod +x /usr/local/bin/biga-start.sh", "making start script executable")

# 8. systemd service
print("\n[8/8] Setting up systemd service...")
run(
    f"sudo cp {REPO}/biga.service.example /etc/systemd/system/biga.service",
    "copying service file",
)
run("sudo systemctl daemon-reload", "reloading systemd")
run("sudo systemctl enable biga", "enabling biga service")

print("\n" + "=" * 50)
print("  Setup complete! Rebooting in 5 seconds...")
print("=" * 50)
run("sleep 5 && sudo reboot")
