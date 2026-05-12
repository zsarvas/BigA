"""Baseball diamond / runner occupancy (from project notes, parameterized)."""

from __future__ import annotations

import pygame

from .. import config


def draw_diamond(
    screen: pygame.Surface,
    runners: dict[str, bool],
    center_x: int,
    center_y: int,
    size: int = 40,
) -> None:
    """
    runners keys: first, second, third (home drawn as fixed plate shape).
    """
    bases = {
        "second": (center_x, center_y - size),
        "third": (center_x - size, center_y),
        "first": (center_x + size, center_y),
        "home": (center_x, center_y + size),
    }

    pygame.draw.circle(screen, config.GREEN_FIELD, (center_x, center_y), int(size * 1.9))
    pygame.draw.circle(screen, config.DIRT, (center_x, center_y), int(size * 1.15))

    for base, (x, y) in bases.items():
        occupied = runners.get(base, False) if base != "home" else False
        color = config.BASE_OCCUPIED if occupied else config.BASE_EMPTY
        points = [
            (x, y - size // 2),
            (x + size // 2, y),
            (x, y + size // 2),
            (x - size // 2, y),
        ]
        pygame.draw.polygon(screen, color, points)
        pygame.draw.polygon(screen, (0, 0, 0), points, 2)
