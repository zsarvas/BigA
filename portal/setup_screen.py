#!/usr/bin/env python3
"""
BigA Setup Screen
Shown on the Pi's display while in AP provisioning mode.

Two-step flow (WiFi QR cannot open the captive portal reliably):
  1. User joins the BigA-XXXX network using the password on screen.
  2. User scans the QR code → http://biga.setup → WiFi credential portal.
"""

import os
import sys
import threading
import time

from captive import PORTAL_SETUP_URL, ap_ssid
from wifi_store import is_provisioning

AP_PASSWORD = os.environ.get("BIGA_AP_PASSWORD", "bigasetup")


def _make_qr_surface(url: str, qr_size: int):
    """Build a pygame surface for the portal URL QR code."""
    import PIL.Image as PilImage
    import pygame
    import qrcode

    qr = qrcode.QRCode(box_size=4, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    qr_pil = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    qr_scaled = qr_pil.resize((qr_size, qr_size), PilImage.NEAREST)
    return pygame.image.fromstring(qr_scaled.tobytes(), qr_scaled.size, "RGB")


def main() -> None:
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
        f_step = pygame.font.Font(_bold, 15)
        f_label = pygame.font.Font(_normal, 13)
        f_value = pygame.font.Font(_bold, 17)
        f_hint = pygame.font.Font(_normal, 12)
    except Exception:
        f_title = pygame.font.SysFont("sans", 22, bold=True)
        f_step = pygame.font.SysFont("sans", 15, bold=True)
        f_label = pygame.font.SysFont("sans", 13)
        f_value = pygame.font.SysFont("sans", 17, bold=True)
        f_hint = pygame.font.SysFont("sans", 12)

    STEP1_LINES = (
        "1. Join Wi‑Fi (password →)",
        '   Tap “Use Without Internet”',
    )
    STEP2_LINE = "2. Scan QR to configure"

    footer_h = f_hint.get_height() + 6
    instr_h = (
        sum(f_step.get_height() + 2 for _ in STEP1_LINES)
        + f_step.get_height()
        + footer_h
        + 8
    )
    QR_SIZE = min(H - instr_h - 20, 188)
    qr_x = 8
    qr_y = max(6, (H - instr_h - QR_SIZE) // 2)
    RX = qr_x + QR_SIZE + 14
    RW = W - RX - 8

    shown_ssid = ""
    qr_surf = _make_qr_surface(PORTAL_SETUP_URL, QR_SIZE)

    def _sync_ssid() -> None:
        nonlocal shown_ssid
        shown_ssid = ap_ssid()

    def draw() -> None:
        screen.fill(BG)
        pygame.draw.rect(
            screen,
            WHITE,
            (qr_x - 6, qr_y - 6, QR_SIZE + 12, QR_SIZE + 12),
            border_radius=8,
        )
        screen.blit(qr_surf, (qr_x, qr_y))

        y = 18
        ts = f_title.render("BigA  Setup", True, WHITE)
        screen.blit(ts, (RX, y))
        y += ts.get_height() + 4
        pygame.draw.line(screen, RED, (RX, y), (RX + RW, y), 2)
        y += 12

        screen.blit(f_label.render("Network", True, MUTED), (RX, y))
        y += f_label.get_height() + 2
        vs = f_value.render(shown_ssid, True, GOLD)
        screen.blit(vs, (RX, y))
        y += vs.get_height() + 10

        screen.blit(f_label.render("Password", True, MUTED), (RX, y))
        y += f_label.get_height() + 2
        screen.blit(f_value.render(AP_PASSWORD, True, WHITE), (RX, y))

        y_instr = H - 8
        hint = f_hint.render(PORTAL_SETUP_URL, True, MUTED)
        y_instr -= hint.get_height()
        screen.blit(hint, hint.get_rect(midtop=(W // 2, y_instr)))
        y_instr -= 4

        for line in reversed((STEP2_LINE, *reversed(STEP1_LINES))):
            surf = f_step.render(line, True, WHITE)
            y_instr -= surf.get_height()
            screen.blit(surf, surf.get_rect(midtop=(W // 2, y_instr)))
            y_instr -= 2

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
