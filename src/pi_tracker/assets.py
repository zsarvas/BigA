"""One-time load of fonts and team logos."""

from __future__ import annotations

import pygame

from . import config


def _repo_font(size: int) -> pygame.font.Font:
    """Prefer a bundled TTF, then common system fonts, then default bitmap font."""
    bundled = config.ASSETS_DIR / "DejaVuSans.ttf"
    if bundled.is_file():
        return pygame.font.Font(str(bundled), size)
    for name in ("dejavusans", "DejaVu Sans", "arial", "helvetica"):
        path = pygame.font.match_font(name)
        if path:
            return pygame.font.Font(path, size)
    return pygame.font.Font(None, size)


class AssetManager:
    def __init__(self) -> None:
        self.font_title: pygame.font.Font
        self.font_score: pygame.font.Font
        self.font_ui: pygame.font.Font
        self.font_small: pygame.font.Font
        self.font_idle_clock: pygame.font.Font
        self.logos: dict[int, pygame.Surface] = {}

    def load(self, team_ids: set[int]) -> None:
        pygame.font.init()
        self.font_title = _repo_font(22)
        self.font_score = _repo_font(44)
        self.font_ui = _repo_font(16)
        self.font_small = _repo_font(13)
        self.font_idle_clock = _repo_font(28)

        for tid in team_ids:
            path = config.LOGOS_DIR / f"{tid}.png"
            if path.is_file():
                img = pygame.image.load(str(path)).convert_alpha()
                self.logos[tid] = pygame.transform.smoothscale(
                    img, config.LOGO_HEADER_SIZE
                )
            else:
                surf = pygame.Surface(config.LOGO_HEADER_SIZE, pygame.SRCALPHA)
                surf.fill((60, 60, 60, 255))
                pygame.draw.rect(surf, (200, 200, 200), surf.get_rect(), 1)
                tid_s = self.font_small.render(str(tid), True, config.WHITE)
                surf.blit(
                    tid_s,
                    tid_s.get_rect(center=(surf.get_width() // 2, surf.get_height() // 2)),
                )
                self.logos[tid] = surf
