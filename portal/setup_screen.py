#!/usr/bin/env python3
"""
BigA Setup Screen
Shown on the Pi's display while in AP provisioning mode.
Displays a QR code + credentials so the user knows how to connect.
Exits automatically when provisioning completes (new network saved).
"""

import os
import sys
import threading
import time

from captive import PORTAL_HOSTNAME, ap_ssid, wifi_qr_string
from wifi_store import is_provisioning
AP_PASSWORD = os.environ.get("BIGA_AP_PASSWORD", "bigasetup")


def _make_qr_surface(ssid: str, qr_size: int):
    """Build a pygame surface for the WiFi join QR code."""
    import PIL.Image as PilImage
    import pygame
    import qrcode

    qr = qrcode.QRCode(box_size=4, border=2)
    qr.add_data(wifi_qr_string(ssid, AP_PASSWORD))
    qr.make(fit=True)
    qr_pil = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    qr_scaled = qr_pil.resize((qr_size, qr_size), PilImage.NEAREST)
    return pygame.image.fromstring(qr_scaled.tobytes(), qr_scaled.size, "RGB")


def main() -> None:
    # --- Generate QR code image ---
    try:
        import pygame
        import qrcode  # noqa: F401 — availability check
        import PIL.Image  # noqa: F401
    except ImportError:
        print("qrcode / pillow / pygame not available — setup screen cannot render", flush=True)
        while is_provisioning():
            time.sleep(2)
        return

    os.environ.setdefault("SDL_VIDEODRIVER", os.environ.get("BIGA_SDL_VIDEO", "kmsdrm"))

    pygame.init()
    pygame.mouse.set_visible(False)
    W = int(os.environ.get("BIGA_SCREEN_WIDTH", 480))
    H = int(os.environ.get("BIGA_SCREEN_HEIGHT", 320))

    try:
        screen = pygame.display.set_mode((W, H), pygame.FULLSCREEN | pygame.NOFRAME)
    except Exception:
        try:
            screen = pygame.display.set_mode((W, H))
        except Exception as exc:
            print(f"Cannot open display: {exc}", flush=True)
            while is_provisioning():
                time.sleep(2)
            return

    pygame.display.set_caption("BigA Setup")

    BG = (10, 15, 30)
    WHITE = (255, 255, 255)
    GOLD = (196, 168, 79)
    RED = (186, 0, 33)
    MUTED = (107, 127, 153)

    _bold = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    _normal = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    try:
        f_title = pygame.font.Font(_bold, 22)
        f_label = pygame.font.Font(_normal, 14)
        f_value = pygame.font.Font(_bold, 17)
        f_instr = pygame.font.Font(_bold, 20)
    except Exception:
        f_title = pygame.font.SysFont("sans", 22, bold=True)
        f_label = pygame.font.SysFont("sans", 14)
        f_value = pygame.font.SysFont("sans", 17, bold=True)
        f_instr = pygame.font.SysFont("sans", 20, bold=True)

    INSTR_LINES = (
        "Scan QR to join Wi‑Fi",
        "Tap “Use Without Internet” if asked",
        f"Open a browser and go to: {PORTAL_HOSTNAME}",
        f"if you aren't already on the page"
    )
    instr_h = sum(f_instr.get_height() for _ in INSTR_LINES) + 8
    QR_SIZE = min(H - instr_h - 24, 200)
    qr_x = 8
    qr_y = max(8, (H - instr_h - QR_SIZE) // 2)
    RX = qr_x + QR_SIZE + 18
    RW = W - RX - 10

    shown_ssid = ""
    qr_surf = None

    def _sync_ssid() -> None:
        nonlocal shown_ssid, qr_surf
        current = ap_ssid()
        if current != shown_ssid:
            shown_ssid = current
            qr_surf = _make_qr_surface(current, QR_SIZE)

    def draw() -> None:
        screen.fill(BG)
        pygame.draw.rect(
            screen,
            WHITE,
            (qr_x - 6, qr_y - 6, QR_SIZE + 12, QR_SIZE + 12),
            border_radius=8,
        )
        if qr_surf is not None:
            screen.blit(qr_surf, (qr_x, qr_y))

        y = 22
        ts = f_title.render("BigA  Setup", True, WHITE)
        screen.blit(ts, (RX, y))
        y += ts.get_height() + 5
        pygame.draw.line(screen, RED, (RX, y), (RX + RW, y), 2)
        y += 14

        screen.blit(f_label.render("Network", True, MUTED), (RX, y))
        y += f_label.get_height() + 3
        vs = f_value.render(shown_ssid, True, GOLD)
        screen.blit(vs, (RX, y))
        y += vs.get_height() + 14

        screen.blit(f_label.render("Password", True, MUTED), (RX, y))
        y += f_label.get_height() + 3
        screen.blit(f_value.render(AP_PASSWORD, True, WHITE), (RX, y))

        y_instr = H - 10
        for line in reversed(INSTR_LINES):
            surf = f_instr.render(line, True, WHITE)
            y_instr -= surf.get_height()
            screen.blit(surf, surf.get_rect(midtop=(W // 2, y_instr)))
            y_instr -= 4

        pygame.display.flip()

    def _watch() -> None:
        while True:
            time.sleep(2)
            if not is_provisioning():
                pygame.event.post(pygame.event.Event(pygame.QUIT))
                return

    threading.Thread(target=_watch, daemon=True).start()

    _sync_ssid()
    clock = pygame.time.Clock()
    poll_ticks = 0
    while True:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit()
                return
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                pygame.quit()
                return

        poll_ticks += 1
        if poll_ticks >= 20:
            poll_ticks = 0
            _sync_ssid()

        draw()
        clock.tick(10)


if __name__ == "__main__":
    main()
