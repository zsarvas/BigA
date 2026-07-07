#!/usr/bin/env python3
"""
BigA Setup Screen
Shown on the Pi's display while in AP provisioning mode.

  1. User joins the BigA-XXXX Wi‑Fi (SSID + password on screen).
  2. Setup usually opens automatically; scan the QR code (http://biga.setup)
     if it does not.
"""

import os
import threading
import time

from captive import PORTAL_SETUP_URL, ap_ssid, wlan_mac
from wifi_store import is_provisioning

AP_PASSWORD = os.environ.get("BIGA_AP_PASSWORD", "bigasetup")
PREVIEW = os.environ.get("BIGA_SETUP_PREVIEW", "").lower() in ("1", "true", "yes")


def _instr_lines(ssid: str) -> tuple[tuple[str, bool], ...]:
    """(text, primary) — primary lines use the step font; hints are smaller."""
    name = ssid or "BigA-Setup"
    return (
        (f"Join the {name} network", True),
        ("If prompted, join without internet", False),
        ("Scan the QR code if setup doesn't open", False),
    )


def _load_qr_surface(url: str, pixel_size: int):
    """Render a QR code for *url* as a pygame surface."""
    import PIL.Image as PilImage
    import pygame
    import qrcode

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        border=1,
        box_size=8,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    img = img.resize((pixel_size, pixel_size), PilImage.NEAREST)
    return pygame.image.fromstring(img.tobytes(), img.size, "RGB")


def main() -> None:
    try:
        import pygame
        import PIL.Image  # noqa: F401
        import qrcode  # noqa: F401
    except ImportError:
        print("pillow / pygame / qrcode not available — setup screen cannot render", flush=True)
        if PREVIEW:
            return
        while is_provisioning():
            time.sleep(2)
        return

    if PREVIEW:
        os.environ.pop("SDL_VIDEODRIVER", None)
    else:
        os.environ.setdefault("SDL_VIDEODRIVER", os.environ.get("BIGA_SDL_VIDEO", "kmsdrm"))

    pygame.init()
    pygame.mouse.set_visible(False)
    W = int(os.environ.get("BIGA_SCREEN_WIDTH", 480))
    H = int(os.environ.get("BIGA_SCREEN_HEIGHT", 320))

    try:
        if PREVIEW:
            screen = pygame.display.set_mode((W, H))
        else:
            screen = pygame.display.set_mode((W, H), pygame.FULLSCREEN | pygame.NOFRAME)
    except Exception:
        try:
            screen = pygame.display.set_mode((W, H))
        except Exception as exc:
            print(f"Cannot open display: {exc}", flush=True)
            if PREVIEW:
                return
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
        f_step = pygame.font.Font(_bold, 14)
        f_hint = pygame.font.Font(_normal, 11)
        f_label = pygame.font.Font(_normal, 13)
        f_value = pygame.font.Font(_bold, 17)
        f_mac = pygame.font.Font(_normal, 11)
        f_qr = pygame.font.Font(_normal, 10)
    except Exception:
        f_title = pygame.font.SysFont("sans", 22, bold=True)
        f_step = pygame.font.SysFont("sans", 14, bold=True)
        f_hint = pygame.font.SysFont("sans", 11)
        f_label = pygame.font.SysFont("sans", 13)
        f_value = pygame.font.SysFont("sans", 17, bold=True)
        f_mac = pygame.font.SysFont("sans", 11)
        f_qr = pygame.font.SysFont("sans", 10)

    STEPS_GAP = 5
    footer_h = f_mac.get_height() + 6
    instr_h = (
        f_step.get_height()
        + 2 * (STEPS_GAP + f_hint.get_height())
        + footer_h
        + 8
    )
    QR_SIZE = min(H - instr_h - 28, 136)
    qr_x = 10
    qr_y = max(8, (H - instr_h - QR_SIZE - f_qr.get_height() - 4) // 2)
    RX = qr_x + QR_SIZE + 14
    RW = W - RX - 8

    shown_ssid = ""
    qr_surf = None

    def _load_qr() -> None:
        nonlocal qr_surf
        try:
            qr_surf = _load_qr_surface(PORTAL_SETUP_URL, QR_SIZE)
        except Exception as exc:
            print(f"Cannot render setup QR: {exc}", flush=True)

    def _sync_ssid() -> None:
        nonlocal shown_ssid
        shown_ssid = ap_ssid()

    def draw() -> None:
        screen.fill(BG)
        if qr_surf is not None:
            pad = 6
            box = pygame.Rect(qr_x - pad, qr_y - pad, QR_SIZE + 2 * pad, QR_SIZE + 2 * pad)
            pygame.draw.rect(screen, WHITE, box, border_radius=4)
            screen.blit(qr_surf, (qr_x, qr_y))
            qr_label = f_qr.render("biga.setup", True, MUTED)
            screen.blit(
                qr_label,
                qr_label.get_rect(midtop=(qr_x + QR_SIZE // 2, qr_y + QR_SIZE + 4)),
            )

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

        mac = wlan_mac()
        if mac:
            mac_surf = f_mac.render(f"MAC {mac}", True, MUTED)
            screen.blit(mac_surf, mac_surf.get_rect(midbottom=(W // 2, H - 4)))

        lines = _instr_lines(shown_ssid)
        total_h = f_step.get_height() + 2 * (STEPS_GAP + f_hint.get_height())
        y_instr = H - footer_h - total_h
        for text, primary in lines:
            font = f_step if primary else f_hint
            color = WHITE if primary else MUTED
            surf = font.render(text, True, color)
            screen.blit(surf, surf.get_rect(midtop=(W // 2, y_instr)))
            y_instr += surf.get_height() + STEPS_GAP

        pygame.display.flip()

    def _watch() -> None:
        while True:
            time.sleep(2)
            if not is_provisioning():
                pygame.event.post(pygame.event.Event(pygame.QUIT))
                return

    if not PREVIEW:
        threading.Thread(target=_watch, daemon=True).start()

    _load_qr()
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
