# BigA — MLB Pi Scoreboard

> Light that baby up. 🔴

A Raspberry Pi scoreboard for one MLB team (default: the **Angels**). It shows the
next scheduled game while idle, switches to a live scoreboard during the game, and
holds a **WIN / LOSS** final screen afterward. On a win it lights a **NeoPixel strip**
on GPIO 19.

Built with **pygame** rendering straight to the framebuffer/KMS (no desktop), driven
by the public **MLB Stats API**.

---

## Table of contents

- [Hardware](#hardware)
- [How it behaves](#how-it-behaves)
- [Quick start (Raspberry Pi)](#quick-start-raspberry-pi)
- [What `setup.py` does](#what-setuppy-does)
- [Display backend: Bookworm (KMS) vs Bullseye (fbcon)](#display-backend-bookworm-kms-vs-bullseye-fbcon)
- [The panel / `config.txt`](#the-panel--configtxt)
- [systemd service](#systemd-service)
- [NeoPixel win lights (GPIO 19)](#neopixel-win-lights-gpio-19)
- [Choosing a team](#choosing-a-team)
- [Environment variables](#environment-variables)
- [Local development (macOS / Linux desktop)](#local-development-macos--linux-desktop)
- [Troubleshooting](#troubleshooting)
- [Project layout](#project-layout)

---

## Hardware

| Item | Notes |
|------|-------|
| Raspberry Pi | Developed/tested on a **Pi Zero 2 W**. Any modern Pi works. |
| Display | 480×320 DPI panel (**MZP351HV00TR**). On Bookworm it uses `vc4-kms-dpi-generic` (KMS). |
| Touch (optional) | ADS7846 resistive controller over SPI (`penirq=27`). |
| NeoPixel strip | WS2812B on **GPIO 19** (PWM channel 1). |

The display is **480×320 landscape** by default. Other resolutions work via
`BIGA_SCREEN_WIDTH` / `BIGA_SCREEN_HEIGHT` (the UI scales).

---

## How it behaves

The UI is a small scene state machine. A background poller talks to the MLB Stats API
and updates shared state; the render loop draws whichever scene is active.

| Scene | When | Notes |
|-------|------|-------|
| **idle** | No game in progress | Shows the next scheduled game (date, time, opponent, venue). Schedule refreshes every 20 min while idle. |
| **live** | Today's game is in progress | Live scoreboard: score, inning, count, base diamond, linescore grid, last play, pitcher/batter. Polls every 2 s. |
| **win** | Game final, tracked team won | Final score + linescore. **NeoPixel strip pulses.** |
| **loss** | Game final, tracked team lost/tied | Final score + linescore. |

**Final-day lock (saves API calls + battery):**

- When a game goes final, the result screen is **held with no routine API polling**.
- It stays up until the **next local calendar day** (the Pi's timezone — `setup.py`
  sets `America/Los_Angeles`), at which point it returns to **idle** and fetches the
  next game.
- **Doubleheaders:** while on a final screen, it does **one** schedule check every
  5 minutes; if a second game goes live, it flips back to **live**.
- The win LEDs follow the `win` scene, so they turn off automatically at the day
  rollover or when a second game starts.

No reboot is needed for the day-to-day cycle.

---

## Quick start (Raspberry Pi)

1. **Flash Raspberry Pi OS Lite (Bookworm, 64-bit recommended).** Enable SSH and
   Wi-Fi in Raspberry Pi Imager.

2. **Clone the repo to `/home/pi/BigA`:**

```bash
sudo apt update && sudo apt install -y git
git clone https://github.com/zsarvas/BigA /home/pi/BigA
cd /home/pi/BigA
```

3. **Run setup (installs everything, configures the panel, enables the service, reboots):**

```bash
sudo python3 BigA/setup.py
```

That's it. After reboot the scoreboard auto-starts on the panel.

> **Bullseye (legacy)?** Run setup with the legacy panel + fbcon backend:
> ```bash
> sudo BIGA_PANEL_INCLUDE=mzp351hv00tr-old.txt python3 setup.py
> ```
> and set `Environment=BIGA_SDL_VIDEO=fbcon` in the service (see below). New installs
> should use Bookworm.

---

## What `setup.py` does

`setup.py` is idempotent — safe to re-run. Steps:

1. **System packages** (apt): `python3-pip`, `python3-pygame`, fonts, SDL2 dev libs,
   `libcairo2-dev`, etc.
2. **Python packages** (pip): from `requirements-pi.txt`. Auto-adds
   `--break-system-packages` only if your pip supports it (PEP 668 / Bookworm).
3. **Permissions:** adds `pi` to the `video` group.
4. **Timezone:** `America/Los_Angeles` (controls the win-screen "next day" rollover).
5. **Display overlays:** copies any `overlays/*.dtbo` if present (optional).
6. **Boot config + panel include:**
   - Detects `/boot/firmware` (Bookworm) vs `/boot` (older) automatically.
   - Installs the panel file (`boot/mzp351hv00tr-new.txt` by default) next to `config.txt`.
   - Appends the BigA snippet (`config_append.txt`) under `[all]`, after cleaning any
     previous BigA block so re-runs don't duplicate.
7. **Start script:** writes `/usr/local/bin/biga-start.sh` (KMSDRM env, waits for
   `/dev/dri/card*`, switches to VT2, launches the app under `openvt`).
8. **systemd service:** installs and enables `biga.service`.

Then it reboots in 5 seconds.

---

## Display backend: Bookworm (KMS) vs Bullseye (fbcon)

This is the most important platform detail.

| | **Bookworm (default)** | **Bullseye (legacy)** |
|---|---|---|
| Panel overlay | `vc4-kms-dpi-generic` (KMS) | firmware DPI (`enable_dpi_lcd`, `dpi_*`, `hdmi_timings`) |
| Device node | `/dev/dri/card0` | `/dev/fb0` |
| SDL backend | **KMSDRM** | **fbcon** |
| `BIGA_SDL_VIDEO` | `kmsdrm` | `fbcon` |
| Panel include | `boot/mzp351hv00tr-new.txt` | `boot/mzp351hv00tr-old.txt` |

**How the app picks a backend** (`src/pi_tracker/bootstrap_sdl.py`,
`src/pi_tracker/app.py`):

- `configure_sdl()` runs **before** `import pygame` and sets `SDL_VIDEODRIVER` from
  `BIGA_SDL_VIDEO` (default **`kmsdrm`**).
- `_open_pygame_window()` tries the selected driver first, then **falls back** by
  probing devices: KMSDRM if `/dev/dri/card*` exists, then fbcon if `/dev/fb0` exists.
  This means a misconfigured `BIGA_SDL_VIDEO` usually still finds a working backend,
  but you should set it correctly for your OS.

---

## The panel / `config.txt`

Panel timings live in a small include file under the boot partition; `config.txt`
just `include`s it. This keeps `config.txt` clean and makes Bullseye↔Bookworm a
one-line swap.

- **`boot/mzp351hv00tr-new.txt`** — Bookworm/KMS (`vc4-kms-dpi-generic`). **Default.**
- **`boot/mzp351hv00tr-old.txt`** — Bullseye/firmware-DPI + `disable_fw_kms_setup=1`.
- **`config_append.txt`** — the lines appended under `[all]`:
  ```ini
  dtparam=spi=on
  include mzp351hv00tr-new.txt   # rewritten to the selected panel by setup.py
  enable_uart=1
  ```

> ⚠️ **Verify the Bookworm panel file.** `boot/mzp351hv00tr-new.txt` translates the
> legacy pixel timings 1:1 into `vc4-kms-dpi-generic`, but the **RGB bus format**
> (legacy `dpi_output_format=0x07f203`) is a best-effort guess (`rgb666`). If colors
> look wrong, replace the file with the **panel manufacturer's Bookworm config**, or
> try `rgb666-padhi` / `rgb888` / `rgb565` in the overlay line. Pixel timings/porches
> are correct; only the bus format is uncertain.

Boot-config changes require a **reboot** (`sudo reboot`), not just a service restart.

To switch generations later:

```bash
# Bookworm (default)
sudo python3 setup.py

# Bullseye / fbcon
sudo BIGA_PANEL_INCLUDE=mzp351hv00tr-old.txt python3 setup.py
# ...and set BIGA_SDL_VIDEO=fbcon in the service.
```

---

## systemd service

`biga.service.example` is installed to `/etc/systemd/system/biga.service`:

```ini
[Unit]
Description=Big A Angels Tracker
After=network-online.target multi-user.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/home/pi/BigA
ExecStart=/usr/local/bin/biga-start.sh
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

`/usr/local/bin/biga-start.sh` (generated by `setup.py`) does the platform glue:

- Exports `BIGA_SDL_VIDEO=kmsdrm` (Bookworm).
- Waits for `/dev/dri/card*` to appear at boot.
- `chvt 2`, then `openvt -c 2 -f -w` to run the app on a dedicated VT.
- Logs everything (incl. Python exit code) to **`/tmp/biga.log`**.

### Common commands

```bash
sudo systemctl status biga          # is it running?
sudo systemctl restart biga         # after a code change / git pull
sudo systemctl stop biga            # stop it
sudo journalctl -u biga -b          # service log this boot
sudo tail -f /tmp/biga.log          # app stdout/stderr + PYEXIT
```

> **Code change?** `git pull` then `sudo systemctl restart biga`.
> **Boot/display change?** `sudo reboot`.
>
> To pass options (team, env), edit the service or the start script. App/team-config
> changes only need a service restart; `config.txt`/panel changes need a reboot.

---

## NeoPixel win lights (GPIO 19)

WS2812B strips need a continuous data signal (not a static GPIO level), so BigA drives
them with `rpi_ws281x` (`src/pi_tracker/gpio_leds.py`).

- On the **win** scene, a daemon thread runs a red/white **racer + theater chase**
  (Arduino-style animation).
- It turns **off** automatically when the scene leaves `win` (day rollover or a
  second game starting).
- Default pin **GPIO 19 → PWM channel 1** (DMA 10). Valid NeoPixel pins: 12, 13, 18, 19, 21.

Tunables (set in the service `Environment=` or your shell):

| Variable | Default | Meaning |
|----------|---------|---------|
| `BIGA_WIN_LED_GPIO` | `19` | Data pin (BCM). Must be PWM/PCM-capable. |
| `BIGA_WIN_LED_COUNT` | `30` | Number of LEDs. |
| `BIGA_WIN_LED_BRIGHTNESS` | `10` | 0–255. |

Notes:
- Requires **root** (the service already runs as root).
- PWM channel 1 (GPIO 19) **conflicts with onboard audio** while active — fine for
  this scoreboard (no audio used).
- No `rpi_ws281x` (e.g. on a Mac) → the LED code is a safe no-op.

Test the win scene + LEDs without waiting for a real game:

```bash
sudo /usr/bin/openvt -c 2 -f -w -- python3 /home/pi/BigA/run_pi_ui.py --demo-final --no-idle-videos
```

---

## Choosing a team

The first non-flag argument to `run_pi_ui.py` is a team **slug** or **numeric MLB
team id**. Default is the Angels.

```bash
python3 run_pi_ui.py angels       # slug
python3 run_pi_ui.py dodgers
python3 run_pi_ui.py 147          # numeric MLB team id (Yankees)
```

Slugs include: `angels, dodgers, yankees, redsox, cubs, giants, padres, mariners,
astros, pirates, …` (see `src/pi_tracker/team_config.py` for the full list / aliases).

This sets `BIGA_TEAM_ID` / `BIGA_TEAM_ABBR` / `BIGA_TEAM_NAME` for the process. You can
also export those env vars directly instead of passing a slug.

To change the team for the **service**, edit the launch line in
`/usr/local/bin/biga-start.sh` (e.g. `run_pi_ui.py dodgers --no-idle-videos`) and
`sudo systemctl restart biga`.

---

## Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `BIGA_TEAM_ID` | `108` (Angels) | Tracked MLB team id. |
| `BIGA_TEAM_ABBR` | `LAA` | Display abbreviation. |
| `BIGA_TEAM_NAME` | `Angels` | Display name. |
| `BIGA_SDL_VIDEO` | `kmsdrm` | SDL backend: `kmsdrm` (Bookworm) or `fbcon` (Bullseye). |
| `BIGA_SCREEN_WIDTH` | `480` | Display width (px). |
| `BIGA_SCREEN_HEIGHT` | `320` | Display height (px). |
| `BIGA_UI_SCALE` | `1.15` | Readability multiplier for all fonts, logos, and the linescore table. `1.0` = original size; higher = bigger. Clamped 0.6–2.0. |
| `BIGA_LINESCORE_SCALE` | `1.3` | Extra size multiplier for the linescore/score table only (on top of `BIGA_UI_SCALE`). Clamped 0.6–2.5. |
| `BIGA_WIN_LED_GPIO` | `19` | NeoPixel data pin (BCM). |
| `BIGA_WIN_LED_COUNT` | `30` | NeoPixel count. |
| `BIGA_WIN_LED_BRIGHTNESS` | `10` | NeoPixel brightness (0–255). |
| `BIGA_PANEL_INCLUDE` | `mzp351hv00tr-new.txt` | Panel file `setup.py` installs/includes. |
| `BIGA_FONT_PATH` | (auto) | Override the TTF used for all text. |
| `BIGA_DEBUG_HUD` | (off) | `1` to draw a clock/frame-counter HUD (confirms the loop is alive). |

### CLI flags (`run_pi_ui.py`)

| Flag | Effect |
|------|--------|
| `--demo` / `--demo-live` | Sample **live** scoreboard (no network pollers). |
| `--demo-final` | Sample **win** screen (no network pollers). |
| `--debug-hud` | Same as `BIGA_DEBUG_HUD=1`. |
| `--fullscreen` | Request a fullscreen SDL window. |
| `--no-schedule` | Don't start schedule/live pollers (offline UI testing). |
| `--no-idle-videos` | Accepted but currently a **no-op** — idle highlight-clip (mpv) playback was removed for fbcon/KMS stability (see `src/pi_tracker/idle_mpv.py`). The service still passes it for forward compatibility. |

---

## Local development (macOS / Linux desktop)

You don't need a Pi to work on the UI.

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt        # desktop deps (pygame, requests, pillow, cairosvg)
python3 run_pi_ui.py angels --demo-live   # live scoreboard sample in a window
python3 run_pi_ui.py angels --demo-final  # win screen sample
```

On a desktop with `DISPLAY`/`WAYLAND_DISPLAY` set, the SDL backend selection is skipped
and a normal window opens. GPIO/NeoPixel code is a no-op without `rpi_ws281x`.

> **Python version:** pygame has wheels for 3.11–3.13. On 3.14 it may try to build from
> source (needs SDL2 headers) — use a 3.12/3.13 venv for the smoothest local setup.

---

## Troubleshooting

**Check the app log first:** `sudo tail -100 /tmp/biga.log`. Each launch prints a
`biga-start <timestamp>` line and ends with `PYEXIT=<code>`.

| Symptom | Likely cause / fix |
|---------|--------------------|
| `pygame.error: No available video device` | Wrong backend for the OS. Bookworm needs KMS: confirm `ls -l /dev/dri/card*`, `BIGA_SDL_VIDEO=kmsdrm`, and that `config.txt` has the KMS panel include. Reboot after `config.txt` edits. |
| Service shows `active` but nothing on screen | The app runs on **VT2**. Switch with `sudo chvt 2` (or it auto-switches). Check `/tmp/biga.log`. |
| Service flaps / exits 127 | `ExecStart`/start-script path or quoting problem. Inspect `/usr/local/bin/biga-start.sh`; re-run `sudo python3 setup.py`. |
| Colors look wrong on the panel | RGB bus format in `boot/mzp351hv00tr-new.txt`. Try `rgb666-padhi` / `rgb888` / `rgb565`, or use the manufacturer's Bookworm panel file. Reboot. |
| A team logo is missing/garbled | Place a `logos/<id>.png` (or `.svg`, rasterized via cairosvg). Non-square art is letterboxed. |
| LEDs don't light on a win | Needs root + `rpi_ws281x` installed; confirm pin is NeoPixel-capable (12/13/18/19/21). Test with `--demo-final`. |
| Idle never advances to a game | Pi clock/timezone. Verify `timedatectl`; schedule logic uses the local date. |
| `pip install` fails: externally managed | Bookworm PEP 668. `setup.py` adds `--break-system-packages` when supported; otherwise use a venv. |

Useful one-liners:

```bash
ls -l /dev/dri/card* /dev/fb0           # which display devices exist
sudo cat /boot/firmware/config.txt      # Bookworm boot config (or /boot/config.txt)
sudo cat /boot/firmware/mzp351hv00tr-new.txt   # installed panel file
systemctl status ssh                    # SSH is NOT in config.txt
```

---

## Project layout

```
BigA/
├── run_pi_ui.py              # entry point (adds src/ to path, parses team arg, runs app)
├── setup.py                  # Pi installer (apt/pip, panel config, start script, service)
├── biga.service.example      # systemd unit (installed to /etc/systemd/system/biga.service)
├── config_append.txt         # lines appended under [all] in config.txt
├── boot/
│   ├── mzp351hv00tr-new.txt  # Bookworm / KMS panel (default)
│   └── mzp351hv00tr-old.txt  # Bullseye / firmware-DPI panel (legacy)
├── logos/                    # <mlb_team_id>.png / .svg
├── requirements.txt          # desktop/dev deps
├── requirements-pi.txt       # Pi runtime deps (rpi_ws281x, cairosvg, …)
└── src/pi_tracker/
    ├── app.py                # main loop, scene switch, display open, LED hook
    ├── bootstrap_sdl.py      # SDL env (KMSDRM/fbcon) before pygame import
    ├── config.py             # screen size, layout scaling, colors
    ├── state.py              # thread-safe shared game state
    ├── game_day_poller.py    # idle→live→final state machine + final-day lock
    ├── schedule_poller.py    # next-game refresh while idle
    ├── mlb_http.py / mlb_schedule.py / mlb_live_feed.py   # MLB Stats API
    ├── assets.py             # fonts + logo loading (PNG/SVG, letterbox)
    ├── gpio_leds.py          # NeoPixel win animation (GPIO 19)
    ├── drawing/diamond.py    # base-runner diamond
    └── scenes/               # idle, live, final_win, final_loss, linescore_table, …
```

---

## Golden Image (flash and go)

A pre-built image is published on the [Releases](https://github.com/zsarvas/BigA/releases) page.
Flash it with [Raspberry Pi Imager](https://www.raspberrypi.com/software/) and the device boots
straight to the Angels splash screen with everything pre-installed — no `setup.py` required.

> In Imager's **Advanced settings** (⚙) set your hostname, SSH credentials, and WiFi before writing.
> The app, services, splash screen, auto-update cron, provisioning portal, and reset button are
> all pre-configured.

### Building a new golden image

**Step 1 — Prepare the golden Pi** (run on the Pi as root):

```bash
sudo bash /home/pi/BigA/scripts/prep_golden.sh
```

This stops all services, clears logs/caches, wipes SSH host keys (they regenerate on first boot),
removes any saved WiFi credentials, and shuts down cleanly.

**Step 2 — Capture, shrink, and publish** (run on your Mac after removing the SD card):

```bash
./scripts/build_image.sh          # interactive — picks disk, prompts for version tag
./scripts/build_image.sh disk4 v1.2   # non-interactive
```

Requires **Docker Desktop** (for pishrink on macOS) and the **`gh` CLI** for release upload.
The script will `dd` the card, shrink it with [PiShrink](https://github.com/Drewsif/PiShrink),
compress with `xz`, and offer to create a GitHub release and upload the asset automatically.

What gets stripped from the image before publishing:
- SSH host keys (regenerated uniquely on each first boot)
- `machine-id` (regenerated on first boot)
- Saved WiFi credentials (`/etc/biga/wifi_creds.json`)
- All logs and package caches

---

## Auto-update (daily cron)

`scripts/update_biga.sh` checks origin/main every morning at 4 AM and pulls if the
commit hash has changed, then restarts the service automatically.

**One-time setup on the Pi:**

```bash
# Make the script executable
chmod +x /home/pi/BigA/scripts/update_biga.sh

# Add to root's crontab (service restart requires root)
sudo crontab -e
```

Add this line:

```
0 4 * * * /home/pi/BigA/scripts/update_biga.sh
```

**Check the update log:**

```bash
sudo tail -f /var/log/biga_update.log
```

Sample output when an update is found:

```
[2026-06-15 04:00:01] --- update check start ---
[2026-06-15 04:00:03] Update found: 41f7a76... -> 9ba38e4...
[2026-06-15 04:00:05] Pull successful. Restarting biga service...
[2026-06-15 04:00:06] Service restarted successfully.
[2026-06-15 04:00:06] --- update complete ---
```

No action is taken (and nothing is logged beyond "Already up to date") when the repo
hasn't changed.

---

## License

See [LICENSE](LICENSE).
