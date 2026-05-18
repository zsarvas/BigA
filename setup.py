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

REPO = "/home/pi/BigA"

print("=" * 50)
print("  BigA Angels Tracker — Setup")
print("=" * 50)

# 1. apt deps
print("\n[1/7] Installing system packages...")
run("sudo apt update -q")
run("sudo apt install -y "
    "python3-pygame "
    "fonts-dejavu-core "
    "libsdl2-dev "
    "libsdl2-image-dev "
    "libsdl2-ttf-dev "
    "libcairo2-dev "
    "pkg-config "
    "python3-dev",
    "apt packages")

# 2. pip deps (Pi-specific only)
print("\n[2/7] Installing Python packages...")
run(f"pip3 install -r {REPO}/requirements-pi.txt --break-system-packages",
    "pip requirements-pi.txt")

# 3. video group
print("\n[3/7] Configuring user permissions...")
run("sudo usermod -a -G video pi", "adding pi to video group")

# 4. timezone
print("\n[4/7] Setting timezone...")
run("sudo timedatectl set-timezone America/Los_Angeles", "timezone → America/Los_Angeles")

# 5. display drivers
print("\n[5/7] Installing display drivers...")
overlays = f"{REPO}/overlays"
if os.path.isdir(overlays) and os.listdir(overlays):
    run(f"sudo cp {overlays}/*.dtbo /boot/overlays/", "copying .dtbo overlay files")
else:
    print("  ⚠ No overlay files found in overlays/ — skipping")

# 6. config.txt
print("\n[6/7] Updating /boot/config.txt...")
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

# 7. systemd service
print("\n[7/7] Setting up systemd service...")
run(f"sudo cp {REPO}/biga.service.example /etc/systemd/system/biga.service",
    "copying service file")
run("sudo systemctl daemon-reload", "reloading systemd")
run("sudo systemctl enable biga", "enabling biga service")

print("\n" + "=" * 50)
print("  Setup complete! Rebooting in 5 seconds...")
print("=" * 50)
run("sleep 5 && sudo reboot")