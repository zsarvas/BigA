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


class GifAnimation:
    """Decoded animated GIF: cover-scaled frames + per-frame durations (ms)."""

    def __init__(self, frames: list[pygame.Surface], durations_ms: list[int]) -> None:
        self.frames = frames
        self.durations = durations_ms
        self.total_ms = max(1, sum(durations_ms))

    def frame_at(self, t_ms: int) -> pygame.Surface | None:
        """Frame for elapsed time ``t_ms`` (loops)."""
        if not self.frames:
            return None
        t = t_ms % self.total_ms
        acc = 0
        for surf, d in zip(self.frames, self.durations):
            acc += d
            if t < acc:
                return surf
        return self.frames[-1]


def _load_gif_cover(path: Path, dest: tuple[int, int]) -> GifAnimation | None:
    """Decode ``path`` to cover-scaled pygame frames via Pillow, or ``None`` on failure."""
    try:
        from PIL import Image
    except ImportError:
        warnings.warn(
            f'{path.name}: animated background needs Pillow ("pip install pillow").', stacklevel=1
        )
        return None
    try:
        im = Image.open(str(path))
    except Exception as exc:  # noqa: BLE001 - missing/corrupt gif is non-fatal
        warnings.warn(f"{path.name}: could not open gif ({exc})", stacklevel=1)
        return None

    frames: list[pygame.Surface] = []
    durations: list[int] = []
    n = getattr(im, "n_frames", 1)
    for i in range(n):
        try:
            im.seek(i)
        except EOFError:
            break
        rgba = im.convert("RGBA")
        surf = pygame.image.fromstring(rgba.tobytes(), rgba.size, "RGBA")
        frames.append(_cover_scale(surf, dest))
        durations.append(int(im.info.get("duration", 100)) or 100)
    if not frames:
        return None
    return GifAnimation(frames, durations)


class StreamingGif:
    """Frame-by-frame GIF player: decodes one frame at a time (low RAM, for long clips).

    Keeps the Pillow image handle open and decodes/cover-scales the requested frame on
    demand. Resident memory is ~one frame, unlike ``GifAnimation`` which preloads all.
    """

    def __init__(self, path: Path, size: tuple[int, int]) -> None:
        self.size = size
        self.n_frames = 0
        self.ok = False
        self._im = None
        try:
            from PIL import Image
        except ImportError:
            warnings.warn(
                f'{path.name}: highlight clip needs Pillow ("pip install pillow").', stacklevel=1
            )
            return
        try:
            self._im = Image.open(str(path))
            self.n_frames = getattr(self._im, "n_frames", 1)
            self.ok = self.n_frames > 0
        except Exception as exc:  # noqa: BLE001 - missing/corrupt gif is non-fatal
            warnings.warn(f"{path.name}: could not open gif ({exc})", stacklevel=1)
            self.ok = False

    def decode(self, index: int) -> tuple[pygame.Surface | None, int]:
        """Decode frame ``index`` → (display-format surface, duration_ms), or (None, 0)."""
        if not self.ok or self._im is None:
            return None, 0
        try:
            self._im.seek(index)
        except (EOFError, OSError):
            return None, 0
        rgba = self._im.convert("RGBA")
        surf = pygame.image.fromstring(rgba.tobytes(), rgba.size, "RGBA")
        if surf.get_size() != self.size:
            surf = _cover_scale(surf, self.size)
        try:
            surf = surf.convert()  # opaque display format: faster blit, smaller resident frame
        except pygame.error:
            pass
        dur = int(self._im.info.get("duration", 83)) or 83
        return surf, dur


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
        self._venue_bg_cache: dict[int, pygame.Surface | None] = {}
        self._gif_cache: dict[tuple[str, tuple[int, int]], GifAnimation | None] = {}

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
        self._venue_bg_cache.clear()
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

    def _load_venue_src(self, venue_id: int) -> pygame.Surface | None:
        """Load and cache the raw surface for a venue-specific stadium image.
        Falls back through: requested venue → Angel Stadium (1) → stadium.jpg.
        This guarantees something always shows, even for overseas games, neutral
        sites, temporary venues (e.g. A's at Sutter Health), or future ballparks
        we haven't added a photo for yet.
        """
        if venue_id in self._venue_bg_cache:
            return self._venue_bg_cache[venue_id]

        stadiums_dir = config.ASSETS_DIR / "stadiums"
        src: pygame.Surface | None = None

        candidates = [venue_id, 1] if venue_id != 1 else [1]
        for vid in candidates:
            if not vid:
                continue
            path = stadiums_dir / f"{vid}.jpg"
            if path.is_file():
                try:
                    src = pygame.image.load(str(path)).convert()
                    break
                except Exception as exc:  # noqa: BLE001
                    warnings.warn(f"stadium {vid}: {exc}", stacklevel=1)

        self._venue_bg_cache[venue_id] = src
        return src

    def background_for_venue(
        self, size: tuple[int, int], venue_id: int
    ) -> pygame.Surface | None:
        """Cover-scaled background for a specific venue.
        Falls back to Angel Stadium if the venue image is missing."""
        src = self._load_venue_src(venue_id) or self.background_src
        if src is None:
            return None
        # Reuse the generic cache for the fallback, keyed by venue for specifics.
        cache_key = (venue_id or -1, *size)
        cached = self._bg_cache.get(cache_key)  # type: ignore[call-overload]
        if cached is None:
            cached = _cover_scale(src, size)
            if config.BG_DIM > 0:
                scrim = pygame.Surface(size, pygame.SRCALPHA)
                scrim.fill((0, 0, 0, config.BG_DIM))
                cached.blit(scrim, (0, 0))
            self._bg_cache[cache_key] = cached  # type: ignore[index]
        return cached

    def draw_background(
        self,
        screen: pygame.Surface,
        fallback: tuple[int, int, int] = config.BLACK,
        venue_id: int = 0,
    ) -> None:
        """Blit the stadium background (dimmed) or fill ``fallback`` if unavailable.

        Pass ``venue_id`` to use a venue-specific image from assets/stadiums/<id>.jpg,
        falling back to the default stadium.jpg if that file doesn't exist yet.
        """
        bg = self.background_for_venue(screen.get_size(), venue_id) if venue_id else self.background_for(screen.get_size())
        if bg is None:
            screen.fill(fallback)
        else:
            screen.blit(bg, (0, 0))

    def gif_animation(self, name: str, size: tuple[int, int]) -> GifAnimation | None:
        """Lazy-load + cache an animated GIF from assets/, cover-scaled to ``size``."""
        key = (name, size)
        if key not in self._gif_cache:
            path = config.ASSETS_DIR / name
            self._gif_cache[key] = _load_gif_cover(path, size) if path.is_file() else None
        return self._gif_cache[key]

    def draw_gif_background(
        self, screen: pygame.Surface, name: str, t_ms: int, fallback: tuple[int, int, int]
    ) -> bool:
        """Blit the current frame of ``name``; fill ``fallback`` if unavailable. Returns True if a frame drew."""
        anim = self.gif_animation(name, screen.get_size())
        frame = anim.frame_at(t_ms) if anim else None
        if frame is None:
            screen.fill(fallback)
            return False
        screen.blit(frame, (0, 0))
        return True
