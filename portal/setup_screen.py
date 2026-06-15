#!/usr/bin/env python3
"""
BigA Setup Screen
Shown on the Pi's display while in AP provisioning mode.
Displays a QR code + credentials so the user knows how to connect.
Exits automatically once wifi_creds.json is written (provisioning complete).
"""

import os
import sys
import threading
import time
from pathlib import Path

CREDS_FILE = Path("/etc/biga/wifi_creds.json")
INTERFACE  = "wlan0"
AP_PASSWORD = os.environ.get("BIGA_AP_PASSWORD", "bigasetup")


def _ap_ssid() -> str:
    if (override := os.environ.get("BIGA_AP_SSID", "")):
        return override
    try:
        mac = Path(f"/sys/class/net/{INTERFACE}/address").read_text().strip().replace(":", "").upper()
        return f"BigA-{mac[-4:]}"
    except OSError:
        return "BigA-Setup"


def main() -> None:
    AP_SSID = _ap_ssid()

    # --- Generate QR code image ---
    try:
        import qrcode
        import PIL.Image as PilImage
    except ImportError:
        print("qrcode / pillow not available — setup screen cannot render QR", flush=True)
        # Keep process alive so systemd doesn't restart-loop
        while not CREDS_FILE.exists():
            time.sleep(2)
        return

    wifi_str = f"WIFI:T:WPA;S:{AP_SSID};P:{AP_PASSWORD};;"
    qr = qrcode.QRCode(box_size=4, border=2)
    qr.add_data(wifi_str)
    qr.make(fit=True)
    qr_pil = qr.make_image(fill_color="black", back_color="white").convert("RGB")

    # --- pygame display ---
    os.environ.setdefault("SDL_VIDEODRIVER", os.environ.get("BIGA_SDL_VIDEO", "kmsdrm"))

    try:
        import pygame
    except ImportError:
        print("pygame not available — setup screen cannot render", flush=True)
        while not CREDS_FILE.exists():
            time.sleep(2)
        return

    pygame.init()
    pygame.mouse.set_visible(False)
    W = int(os.environ.get("BIGA_SCREEN_WIDTH",  480))
    H = int(os.environ.get("BIGA_SCREEN_HEIGHT", 320))

    try:
        screen = pygame.display.set_mode((W, H), pygame.FULLSCREEN | pygame.NOFRAME)
    except Exception:
        try:
            screen = pygame.display.set_mode((W, H))
        except Exception as exc:
            print(f"Cannot open display: {exc}", flush=True)
            while not CREDS_FILE.exists():
                time.sleep(2)
            return

    pygame.display.set_caption("BigA Setup")

    # --- Colors (Angels palette) ---
    BG    = ( 10,  15,  30)
    WHITE = (255, 255, 255)
    GOLD  = (196, 168,  79)
    RED   = (186,   0,  33)
    NAVY  = (  0,  50,  99)
    MUTED = (107, 127, 153)

    # --- Fonts ---
    _bold   = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    _normal = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    try:
        f_title = pygame.font.Font(_bold,   20)
        f_label = pygame.font.Font(_normal, 13)
        f_value = pygame.font.Font(_bold,   15)
        f_hint  = pygame.font.Font(_normal, 11)
    except Exception:
        f_title = f_label = f_value = f_hint = pygame.font.SysFont("sans", 14)

    # --- QR surface ---
    QR_SIZE = min(H - 20, 240)
    qr_scaled = qr_pil.resize((QR_SIZE, QR_SIZE), PilImage.NEAREST)
    qr_surf   = pygame.image.fromstring(qr_scaled.tobytes(), qr_scaled.size, "RGB")
    qr_x = 8
    qr_y = (H - QR_SIZE) // 2

    # Right column origin
    RX = qr_x + QR_SIZE + 18
    RW = W - RX - 10

    def draw() -> None:
        screen.fill(BG)

        # QR — white card background
        pygame.draw.rect(screen, WHITE,
                         (qr_x - 6, qr_y - 6, QR_SIZE + 12, QR_SIZE + 12),
                         border_radius=8)
        screen.blit(qr_surf, (qr_x, qr_y))

        # ── Right column ──────────────────────────────────
        y = 22

        # Title
        ts = f_title.render("BigA  Setup", True, WHITE)
        screen.blit(ts, (RX, y))
        y += ts.get_height() + 5

        # Red rule
        pygame.draw.line(screen, RED, (RX, y), (RX + RW, y), 2)
        y += 14

        def field(label: str, value: str, vc=GOLD) -> None:
            nonlocal y
            screen.blit(f_label.render(label, True, MUTED), (RX, y))
            y += f_label.get_height() + 3
            # value — clip to column width
            vs = f_value.render(value, True, vc)
            screen.blit(vs, (RX, y))
            y += vs.get_height() + 14

        field("Network",  AP_SSID)
        field("Password", AP_PASSWORD, WHITE)

        # Bottom hint
        for i, line in enumerate(("Scan QR to join, then open",
                                   "192.168.4.1 in your browser")):
            screen.blit(f_hint.render(line, True, MUTED),
                        (RX, H - 30 + i * 14))

        pygame.display.flip()

    draw()

    # --- Watch for provisioning completion ---
    def _watch() -> None:
        while True:
            time.sleep(2)
            if CREDS_FILE.exists():
                pygame.event.post(pygame.event.Event(pygame.QUIT))
                return

    threading.Thread(target=_watch, daemon=True).start()

    # --- Event loop ---
    clock = pygame.time.Clock()
    while True:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit()
                return
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                pygame.quit()
                return
        clock.tick(10)


if __name__ == "__main__":
    main()
