from __future__ import annotations

from typing import Any, Protocol

import pygame

from ..assets import AssetManager


class Scene(Protocol):
    def draw(self, screen: pygame.Surface, assets: AssetManager, state: dict[str, Any]) -> None: ...
