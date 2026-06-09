"""One-time load of fonts and team logos."""

from __future__ import annotations

import io
import os
import platform
import sys
import warnings
from pathlib import Path

import pygame

from . import config

# On Pi OS Lite, pygame.font.match_font() runs fc-list and often hangs or times out.
# Prefer explicit paths; set BIGA_FONT_PATH=/path/to/font.ttf to override.
_SYSTEM_SANS = (
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
    Path("/usr/share/fonts/TTF/DejaVuSans.ttf"),
)


def _is_raspberry_pi() -> bool:
    model = Path("/proc/device-tree/model")
    try:
        if model.is_file():
            raw = model.read_bytes().replace(b"\x00", b"").decode("utf-8", errors="ignore").lower()
            return "raspberry" in raw
    except OSError:
        pass
    return False


def _linux_arm() -> bool:
    if not sys.platform.startswith("linux"):
        return False
    m = platform.machine().lower()
    return m.startswith("arm") or m == "aarch64"


def _skip_match_font() -> bool:
    if os.environ.get("BIGA_SKIP_SYSFONT", "").strip().lower() in ("1", "true", "yes"):
        return True
    if _is_raspberry_pi() or _linux_arm():
        return True
    return False


def _repo_font(size: int) -> pygame.font.Font:
    """Bundled TTF, BIGA_FONT_PATH, known Debian paths, then match_font (non-Pi) or bitmap."""
    env_fp = os.environ.get("BIGA_FONT_PATH", "").strip()
    if env_fp and Path(env_fp).is_file():
        return pygame.font.Font(env_fp, size)

    bundled = config.ASSETS_DIR / "DejaVuSans.ttf"
    if bundled.is_file():
        return pygame.font.Font(str(bundled), size)

    for p in _SYSTEM_SANS:
        if p.is_file():
            return pygame.font.Font(str(p), size)

    if _skip_match_font():
        return pygame.font.Font(None, size)

    for name in ("dejavusans", "DejaVu Sans", "arial", "helvetica"):
        path = pygame.font.match_font(name)
        if path:
            return pygame.font.Font(path, size)
    return pygame.font.Font(None, size)


_SVG_DEP_WARNED = False


def _raster_svg_to_surface(path: Path) -> pygame.Surface | None:
    """Render ``path`` to a raster surface, or ``None`` if cairosvg is missing or conversion fails."""
    global _SVG_DEP_WARNED
    try:
        import cairosvg
    except ImportError:
        if not _SVG_DEP_WARNED:
            warnings.warn(
                f'{path.name}: SVG logo present but package "cairosvg" is not installed; '
                "`pip install cairosvg` (and libcairo where needed) or add a matching `.png`.",
                stacklevel=1,
            )
            _SVG_DEP_WARNED = True
        return None
    buf = io.BytesIO()
    try:
        edge = max(config.LOGO_HEADER_SIZE) * 3
        cairosvg.svg2png(bytestring=path.read_bytes(), write_to=buf, output_width=edge)
    except Exception:
        return None
    buf.seek(0)
    try:
        return pygame.image.load(buf).convert_alpha()
    except Exception:
        return None


def _ensure_scalable_surface(surf: pygame.Surface) -> pygame.Surface:
    """Return 24/32-bit RGB/RGBA safe for ``pygame.transform.smoothscale`` (fbcon can load 8-bit PNGs)."""
    if surf.get_bitsize() in (24, 32):
        return surf
    try:
        converted = surf.convert_alpha()
        if converted.get_bitsize() in (24, 32):
            return converted
    except pygame.error:
        pass
    out = pygame.Surface(surf.get_size(), pygame.SRCALPHA, 32)
    out.blit(surf, (0, 0))
    return out


def scale_surface(surf: pygame.Surface, size: tuple[int, int]) -> pygame.Surface:
    """``smoothscale`` with a guaranteed-compatible source format."""
    return pygame.transform.smoothscale(_ensure_scalable_surface(surf), size)


def _cover_scale(img: pygame.Surface, dest: tuple[int, int]) -> pygame.Surface:
    """Scale ``img`` to fully cover ``dest`` (keep aspect, crop overflow), centered."""
    img = _ensure_scalable_surface(img)
    dw, dh = dest
    iw, ih = img.get_size()
    if iw <= 0 or ih <= 0:
        return scale_surface(img, dest)
    scale = max(dw / iw, dh / ih)
    nw, nh = max(1, int(round(iw * scale))), max(1, int(round(ih * scale)))
    scaled = scale_surface(img, (nw, nh))
    out = pygame.Surface(dest)
    out.blit(scaled, ((dw - nw) // 2, (dh - nh) // 2))
    return out


def _letterbox_logo(img: pygame.Surface, dest: tuple[int, int]) -> pygame.Surface:
    """Scale ``img`` to fit inside ``dest`` (keep aspect); center on transparent square."""
    img = _ensure_scalable_surface(img)
    dw, dh = dest
    iw, ih = img.get_size()
    if iw <= 0 or ih <= 0:
        return scale_surface(img, dest)
    scale = min(dw / iw, dh / ih)
    nw, nh = max(1, int(round(iw * scale))), max(1, int(round(ih * scale)))
    scaled = scale_surface(img, (nw, nh))
    out = pygame.Surface((dw, dh), pygame.SRCALPHA)
    out.fill((0, 0, 0, 0))
    out.blit(scaled, ((dw - nw) // 2, (dh - nh) // 2))
    return out


class AssetManager:
    def __init__(self) -> None:
        self.font_title: pygame.font.Font
        self.font_score: pygame.font.Font
        self.font_ui: pygame.font.Font
        self.font_small: pygame.font.Font
        self.font_linescore: pygame.font.Font
        self.font_idle_clock: pygame.font.Font
        self.logos: dict[int, pygame.Surface] = {}
        self.background_src: pygame.Surface | None = None
        self._bg_cache: dict[tuple[int, int], pygame.Surface] = {}

    def load(self, team_ids: set[int]) -> None:
        pygame.font.init()
        self.font_title = _repo_font(config.layout_size(20))
        self.font_score = _repo_font(config.layout_size(38))
        self.font_ui = _repo_font(config.layout_size(14))
        self.font_small = _repo_font(config.layout_size(11))
        self.font_linescore = _repo_font(
            max(1, int(round(config.layout_size(11) * config.LINESCORE_SCALE)))
        )
        self.font_idle_clock = _repo_font(config.layout_size(22))

        self.background_src = None
        self._bg_cache.clear()
        if config.BG_IMAGE:
            bg_path = config.ASSETS_DIR / config.BG_IMAGE
            if bg_path.is_file():
                try:
                    self.background_src = pygame.image.load(str(bg_path)).convert()
                except Exception as exc:  # noqa: BLE001 - missing/corrupt image is non-fatal
                    warnings.warn(f"{bg_path.name}: could not load background ({exc})", stacklevel=1)

        self.logos.clear()
        for tid in team_ids:
            logos_dir = config.LOGOS_DIR
            png_path = logos_dir / f"{tid}.png"
            svg_path = logos_dir / f"{tid}.svg"
            img: pygame.Surface | None = None
            if png_path.is_file():
                img = pygame.image.load(str(png_path)).convert_alpha()
            elif svg_path.is_file():
                img = _raster_svg_to_surface(svg_path)
            if img is not None:
                self.logos[tid] = _letterbox_logo(img, config.LOGO_HEADER_SIZE)
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

    def background_for(self, size: tuple[int, int]) -> pygame.Surface | None:
        """Cover-scaled background for ``size``, or ``None`` if no image is loaded."""
        if self.background_src is None:
            return None
        cached = self._bg_cache.get(size)
        if cached is None:
            cached = _cover_scale(self.background_src, size)
            if config.BG_DIM > 0:
                scrim = pygame.Surface(size, pygame.SRCALPHA)
                scrim.fill((0, 0, 0, config.BG_DIM))
                cached.blit(scrim, (0, 0))
            self._bg_cache[size] = cached
        return cached

    def draw_background(
        self, screen: pygame.Surface, fallback: tuple[int, int, int] = config.BLACK
    ) -> None:
        """Blit the stadium background (dimmed) or fill ``fallback`` if unavailable."""
        bg = self.background_for(screen.get_size())
        if bg is None:
            screen.fill(fallback)
        else:
            screen.blit(bg, (0, 0))
