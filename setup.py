import glob
import subprocess
import os
import sys

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


REPO = "/home/pi/BigA"

print("=" * 50)
print("  BigA Angels Tracker — Setup")
print("=" * 50)

# 1. apt deps
print("\n[1/8] Installing system packages...")
run("sudo apt update -q")
run("sudo apt install -y "
    "python3-pip "
    "python3-pygame "
    "fonts-dejavu-core "
    "libsdl2-dev "
    "libsdl2-image-dev "
    "libsdl2-ttf-dev "
    "libcairo2-dev "
    "pkg-config "
    "python3-dev",
    "apt packages")

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
overlays = f"{REPO}/overlays"
if os.path.isdir(overlays) and os.listdir(overlays):
    run(f"sudo cp {overlays}/*.dtbo /boot/overlays/", "copying .dtbo overlay files")
else:
    print("  ⚠ No overlay files found in overlays/ — skipping")

# 6. config.txt
print("\n[6/8] Updating /boot/config.txt...")
config_append = f"{REPO}/config_append.txt"
if os.path.exists(config_append):
    with open(config_append, 'r') as f:
        append_content = f.read()
    with open('/boot/config.txt', 'r') as f:
        current = f.read()
    if "dtoverlay=dpi24" not in current:
        with open('/boot/config.txt', 'a') as f:
            f.write('\n' + append_content)
        print("  → config.txt updated")
    else:
        print("  → config.txt already configured, skipping")
else:
    print("  ✗ config_append.txt not found in repo")
    sys.exit(1)

# 7. start script (fbcon + chvt 2 + openvt wrapper for systemd)
print("\n[7/8] Installing start script...")
start_script = f"""#!/bin/sh
set -eu
export SDL_VIDEODRIVER=fbcon
export SDL_FBDEV=/dev/fb0
export PYTHONUNBUFFERED=1
exec >>/tmp/biga.log 2>&1
echo "biga-start $(date -Is)"
/usr/bin/chvt 2 || echo "chvt 2 failed with $?"
exec /usr/bin/openvt -c 2 -f -w -- /bin/sh -c "/usr/bin/python3 {REPO}/run_pi_ui.py --no-idle-videos >>/tmp/biga.log 2>&1; echo PYEXIT=$? >>/tmp/biga.log"
"""

with open("/tmp/biga-start.sh", "w", encoding="utf-8") as f:
    f.write(start_script)

run("sudo mv /tmp/biga-start.sh /usr/local/bin/biga-start.sh", "installing /usr/local/bin/biga-start.sh")
run("sudo chmod +x /usr/local/bin/biga-start.sh", "making start script executable")

# 8. systemd service
print("\n[8/8] Setting up systemd service...")
run(f"sudo cp {REPO}/biga.service.example /etc/systemd/system/biga.service",
    "copying service file")
run("sudo systemctl daemon-reload", "reloading systemd")
run("sudo systemctl enable biga", "enabling biga service")

print("\n" + "=" * 50)
print("  Setup complete! Rebooting in 5 seconds...")
print("=" * 50)
run("sleep 5 && sudo reboot")