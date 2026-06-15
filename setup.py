import glob
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.abspath(__file__))
# Bookworm + KMS is the target. Use mzp351hv00tr-old.txt for legacy Bullseye/fbcon images.
DEFAULT_PANEL_INCLUDE = os.environ.get("BIGA_PANEL_INCLUDE", "mzp351hv00tr-new.txt")


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
        "disable_splash=1",
        "dtparam=spi=on",
        "dtoverlay=vc4-kms-dpi-generic",
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


def _configure_deploy_key(repo: str) -> None:
    """
    If a deploy key exists at /etc/biga/deploy_key, switch the git remote to
    SSH and pre-accept GitHub's host key so unattended pulls work.
    Skips silently if the key isn't present (public repo / HTTPS still works).
    """
    key_path = "/etc/biga/deploy_key"
    ssh_url = "git@github.com:zsarvas/BigA.git"

    if not os.path.exists(key_path):
        print("  → no deploy key found — skipping SSH remote config (public repo or key not yet set up)")
        return

    # Pre-accept GitHub's host key for root (cron runs as root)
    known_hosts = "/root/.ssh/known_hosts"
    run("mkdir -p /root/.ssh && chmod 700 /root/.ssh", "ensuring /root/.ssh exists")
    proc = subprocess.run(
        ["grep", "-q", "github.com", known_hosts],
        capture_output=True, check=False,
    )
    if proc.returncode != 0:
        run(
            f"ssh-keyscan -t ed25519 github.com >> {known_hosts} 2>/dev/null; chmod 644 {known_hosts}",
            "adding GitHub to root known_hosts",
        )
    else:
        print("  → GitHub already in known_hosts")

    # Switch remote to SSH
    current = subprocess.run(
        ["git", "-C", repo, "remote", "get-url", "origin"],
        capture_output=True, text=True, check=False,
    ).stdout.strip()

    if current != ssh_url:
        run(f"git -C {repo} remote set-url origin {ssh_url}", "switching remote to SSH")
    else:
        print(f"  → remote already set to SSH")

    # Lock down key permissions
    run(f"chmod 600 {key_path}", "locking deploy key permissions")
    print(f"  → deploy key configured: {key_path}")


def _install_auto_update_cron(repo: str) -> None:
    """Install scripts/update_biga.sh + a 4 AM root cron entry (idempotent)."""
    script = os.path.join(repo, "scripts", "update_biga.sh")
    if not os.path.isfile(script):
        print(f"  ✗ update script not found: {script}")
        sys.exit(1)

    run(f"chmod +x {script}", f"making {script} executable")

    cron_entry = f"0 4 * * * {script}"
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, check=False)
    existing = result.stdout if result.returncode == 0 else ""

    if script in existing:
        print(f"  → cron job already present, skipping")
        return

    new_crontab = existing.rstrip("\n") + ("\n" if existing else "") + cron_entry + "\n"
    proc = subprocess.run(["crontab", "-"], input=new_crontab, text=True, check=False)
    if proc.returncode != 0:
        print("  ✗ Failed to write crontab")
        sys.exit(1)
    print(f"  → cron job installed: {cron_entry}")


def _install_splash(repo: str, boot_dir: str) -> None:
    """Install Plymouth theme + quiet boot cmdline (idempotent)."""
    theme_dir = "/usr/share/plymouth/themes/biga"
    splash_src = os.path.join(repo, "splash")

    # --- Plymouth theme files ---
    run(f"sudo mkdir -p {theme_dir}", f"creating {theme_dir}")
    for fname in ("biga.plymouth", "biga.script"):
        src = os.path.join(splash_src, fname)
        if not os.path.isfile(src):
            print(f"  ✗ Missing splash file: {src}")
            sys.exit(1)
        run(f"sudo cp {src} {theme_dir}/{fname}", f"installing {fname}")

    logo_src = os.path.join(repo, "logos", "108.png")
    run(f"sudo cp {logo_src} {theme_dir}/biga-splash.png", "installing splash logo")

    run("sudo plymouth-set-default-theme biga", "setting default Plymouth theme")
    run("sudo update-initramfs -u", "rebuilding initramfs with Plymouth theme")

    # --- Quiet kernel cmdline ---
    cmdline_path = os.path.join(boot_dir, "cmdline.txt")
    proc = subprocess.run(["sudo", "cat", cmdline_path], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        print(f"  ✗ Cannot read {cmdline_path}")
        sys.exit(1)

    tokens = proc.stdout.strip().split()
    quiet_tokens = {
        "quiet", "splash", "plymouth.ignore-serial-consoles",
        "loglevel=3", "logo.nologo", "vt.global_cursor_default=0",
    }
    # strip any existing conflicting values then append ours
    cleaned = [t for t in tokens if t not in quiet_tokens and not t.startswith("loglevel=")]
    new_cmdline = " ".join(cleaned + sorted(quiet_tokens)) + "\n"

    tmp = "/tmp/biga-cmdline.txt"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(new_cmdline)
    run(f"sudo cp {tmp} {cmdline_path}", f"updating {cmdline_path}")
    print(f"  → {cmdline_path} updated with quiet splash tokens")


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
    # Point the include at the selected panel file regardless of which one the snippet ships with.
    snippet = re.sub(
        r"include\s+mzp351hv00tr-(?:old|new)\.txt",
        f"include {panel_name}",
        snippet_raw,
    )

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
print("\n[1/13] Installing system packages...")
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
    "python3-dev "
    "plymouth "
    "plymouth-themes",
    "apt packages",
)

# 2. pip deps (Pi-specific only; pygame comes from apt above, not requirements-pi.txt)
print("\n[2/13] Installing Python packages...")
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
print("\n[3/13] Configuring user permissions...")
run("sudo usermod -a -G video pi", "adding pi to video group")

# 4. timezone
print("\n[4/13] Setting timezone...")
run("sudo timedatectl set-timezone America/Los_Angeles", "timezone → America/Los_Angeles")

# 5. display drivers
print("\n[5/13] Installing display drivers...")
boot_dir, overlays_dir = _boot_paths()
print(f"  → boot dir: {boot_dir}")
overlays = os.path.join(REPO, "overlays")
if os.path.isdir(overlays) and os.listdir(overlays):
    run(f"sudo cp {overlays}/*.dtbo {overlays_dir}/", "copying .dtbo overlay files")
else:
    print("  ⚠ No overlay files found in overlays/ — skipping (panel uses include file)")

# 6. config.txt + panel include file
print("\n[6/13] Updating boot config + panel include...")
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

# 7. start script (Bookworm KMSDRM + chvt 2 + openvt wrapper for systemd)
print("\n[7/13] Installing start script...")
start_script = f"""#!/bin/sh
set -eu
export PYTHONUNBUFFERED=1
# Bookworm + KMS: panel is vc4-kms-dpi-generic, so SDL uses KMSDRM (no SDL_FBDEV).
export BIGA_SDL_VIDEO=kmsdrm
export SDL_VIDEODRIVER=kmsdrm
exec >>/tmp/biga.log 2>&1
echo "biga-start $(date -Is)"
i=0
while [ ! -e /dev/dri/card0 ] && [ ! -e /dev/dri/card1 ] && [ "$i" -lt 20 ]; do
  echo "waiting for /dev/dri/card* ($i)..."
  i=$((i + 1))
  sleep 1
done
ls -l /dev/dri/card* /dev/fb0 2>&1 || true
# Clear any text left on tty2 (login prompt residue) before switching to it.
printf '\\033[2J\\033[H' > /dev/tty2 2>/dev/null || true
# -s switches the active VT to tty2 as part of starting the process (no separate chvt needed).
exec /usr/bin/openvt -c 2 -s -f -w -- /bin/sh -c "/usr/bin/python3 {REPO}/run_pi_ui.py --no-idle-videos >>/tmp/biga.log 2>&1; echo PYEXIT=$? >>/tmp/biga.log"
"""

with open("/tmp/biga-start.sh", "w", encoding="utf-8") as f:
    f.write(start_script)

run("sudo mv /tmp/biga-start.sh /usr/local/bin/biga-start.sh", "installing /usr/local/bin/biga-start.sh")
run("sudo chmod +x /usr/local/bin/biga-start.sh", "making start script executable")

# 8. systemd service
print("\n[8/13] Setting up systemd service...")
run(
    f"sudo cp {REPO}/biga.service.example /etc/systemd/system/biga.service",
    "copying service file",
)
run("sudo systemctl daemon-reload", "reloading systemd")
run("sudo systemctl enable biga", "enabling biga service")
# Mask the getty on tty2 so no "BigA login:" prompt flashes before the app takes the VT.
run("sudo systemctl mask getty@tty2.service", "masking getty on tty2")
run("sudo systemctl mask autovt@tty2.service", "masking autovt on tty2")

# 9. auto-update cron + deploy key SSH config
print("\n[9/13] Configuring auto-update (cron + deploy key)...")
_configure_deploy_key(REPO)
_install_auto_update_cron(REPO)
print("  → update log: /var/log/biga_update.log")

# 10. boot splash (Plymouth theme + quiet cmdline)
print("\n[10/13] Installing boot splash...")
_install_splash(REPO, boot_dir)

# 11. WiFi provisioning portal service
print("\n[11/13] Installing WiFi provisioning portal...")
portal_service_src = os.path.join(REPO, "portal", "biga-portal.service")
run(
    f"sudo cp {portal_service_src} /etc/systemd/system/biga-portal.service",
    "copying portal service file",
)
run("sudo systemctl daemon-reload", "reloading systemd for portal")
run("sudo systemctl enable biga-portal", "enabling biga-portal service")
print("  → portal log: /var/log/biga-portal.log")
print("  → runs on port 80 while in AP mode")

setup_screen_src = os.path.join(REPO, "portal", "biga-setup-screen.service")
run(
    f"sudo cp {setup_screen_src} /etc/systemd/system/biga-setup-screen.service",
    "copying setup screen service file",
)
run("sudo systemctl daemon-reload", "reloading systemd for setup screen")
run("sudo systemctl enable biga-setup-screen", "enabling biga-setup-screen service")
print("  → setup screen shows QR code on tty2 during AP provisioning")

# 12. Factory reset button monitor (GPIO 27)
print("\n[12/13] Installing factory reset button monitor...")
reset_script = os.path.join(REPO, "scripts", "reset_button.py")
reset_service_src = os.path.join(REPO, "scripts", "biga-reset.service")
run(f"chmod +x {reset_script}", "making reset_button.py executable")
run(
    f"sudo cp {reset_service_src} /etc/systemd/system/biga-reset.service",
    "copying reset service file",
)
run("sudo systemctl daemon-reload", "reloading systemd for reset button")
run("sudo systemctl enable biga-reset", "enabling biga-reset service")
print("  → GPIO BCM 26 — hold 5 s to factory reset")
print("  → reset log: /var/log/biga-reset.log")

# 13. AP mode setup (NetworkManager profile + firstboot service)
# If the Pi is already connected to a WiFi network (e.g. flashed with creds for
# development), write wifi_creds.json now so the portal/setup-screen don't start
# and take over the network after reboot.
print("\n  Checking for existing WiFi connection...")
_active = subprocess.run(
    ["nmcli", "-t", "-f", "NAME,TYPE,STATE", "connection", "show", "--active"],
    capture_output=True, text=True, check=False,
).stdout
_already_connected = any(
    ":802-11-wireless:" in line and "biga-ap" not in line
    for line in _active.splitlines()
)
if _already_connected:
    _creds_path = "/etc/biga/wifi_creds.json"
    if not os.path.exists(_creds_path):
        import json as _json
        _ssid_line = next(
            (l for l in _active.splitlines() if ":802-11-wireless:" in l and "biga-ap" not in l), ""
        )
        _ssid = _ssid_line.split(":")[0]
        run(f"sudo mkdir -p /etc/biga", "ensuring /etc/biga exists")
        _tmp = "/tmp/biga-wifi-creds.json"
        with open(_tmp, "w") as _f:
            _json.dump({"ssid": _ssid, "password": ""}, _f)
        run(f"sudo cp {_tmp} {_creds_path} && sudo chmod 600 {_creds_path}",
            f"writing wifi_creds.json (ssid={_ssid}) — portal will not start")
    else:
        print(f"  → wifi_creds.json already exists — portal will not start")
else:
    print("  → no existing WiFi — portal will handle provisioning on first boot")
print("")
print("\n[13/13] Setting up AP mode...")
run(f"chmod +x {os.path.join(REPO, 'scripts', 'setup_ap.sh')}", "making setup_ap.sh executable")
run(f"sudo bash {os.path.join(REPO, 'scripts', 'setup_ap.sh')}", "creating biga-ap NM profile")
firstboot_src = os.path.join(REPO, "scripts", "biga-firstboot.service")
run(
    f"sudo cp {firstboot_src} /etc/systemd/system/biga-firstboot.service",
    "copying firstboot service",
)
run("sudo systemctl daemon-reload", "reloading systemd for firstboot")
run("sudo systemctl enable biga-firstboot", "enabling biga-firstboot service")
print("  → AP profile created (nmcli con show biga-ap)")
print("  → firstboot service regenerates SSID from MAC on each new Pi")

print("\n" + "=" * 50)
print("  Setup complete! Rebooting in 5 seconds...")
print("=" * 50)
run("sleep 5 && sudo reboot")
