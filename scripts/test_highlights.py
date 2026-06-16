#!/usr/bin/env python3
"""
Highlight clip viewer — plays every .gif/.mp4 in assets/highlights/ in sequence.
Lets you verify MP4 decoding, frame rate, and cover-scaling look correct.

Controls:
    SPACE   skip to next clip
    Q/ESC   quit
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

HIGHLIGHTS_DIR = REPO_ROOT / "src" / "pi_tracker" / "assets" / "highlights"
EXTS = {".gif", ".mp4", ".mov", ".avi", ".mkv"}


def main() -> None:
    try:
        import pygame
    except ImportError:
        print("pygame not installed — run: pip install pygame")
        sys.exit(1)

    from pi_tracker.assets import open_streaming_clip

    clips = sorted(p for p in HIGHLIGHTS_DIR.iterdir() if p.suffix.lower() in EXTS)
    if not clips:
        print(f"No clips found in {HIGHLIGHTS_DIR}")
        sys.exit(1)

    pygame.init()
    W, H = 960, 540
    screen = pygame.display.set_mode((W, H))
    pygame.display.set_caption("Highlight Clip Test")
    clock = pygame.time.Clock()
    font = pygame.font.SysFont("DejaVu Sans", 18)

    def play_clip(path: Path) -> None:
        pygame.display.set_caption(f"Loading {path.name}…")
        screen.fill((10, 10, 10))
        msg = font.render(f"Loading {path.name}…", True, (180, 180, 180))
        screen.blit(msg, msg.get_rect(center=(W // 2, H // 2)))
        pygame.display.flip()

        clip = open_streaming_clip(path, (W, H))
        if not clip.ok:
            screen.fill((40, 10, 10))
            err = font.render(f"FAILED to load {path.name}", True, (220, 80, 80))
            screen.blit(err, err.get_rect(center=(W // 2, H // 2)))
            pygame.display.flip()
            pygame.time.wait(2000)
            return

        pygame.display.set_caption(f"{path.name}  ({clip.n_frames} frames)")
        frame_idx = 0
        deadline = pygame.time.get_ticks()

        while frame_idx < clip.n_frames:
            clock.tick(60)
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    pygame.quit()
                    sys.exit(0)
                elif event.type == pygame.KEYDOWN:
                    if event.key in (pygame.K_q, pygame.K_ESCAPE):
                        pygame.quit()
                        sys.exit(0)
                    elif event.key == pygame.K_SPACE:
                        return  # skip to next

            now = pygame.time.get_ticks()
            if now < deadline:
                continue

            surf, dur = clip.decode(frame_idx)
            if surf is None:
                break
            screen.blit(surf, (0, 0))

            # HUD overlay
            scrim = pygame.Surface((W, 28), pygame.SRCALPHA)
            scrim.fill((0, 0, 0, 140))
            screen.blit(scrim, (0, 0))
            label = font.render(
                f"{path.name}  ·  frame {frame_idx + 1}/{clip.n_frames}  ·  SPACE=skip  Q=quit",
                True, (200, 200, 200),
            )
            screen.blit(label, (8, 5))
            pygame.display.flip()

            deadline = now + dur
            frame_idx += 1

    for clip_path in clips:
        play_clip(clip_path)
        # Brief pause between clips
        screen.fill((10, 10, 10))
        done = font.render(f"Finished {clip_path.name} — next up…", True, (120, 200, 120))
        screen.blit(done, done.get_rect(center=(W // 2, H // 2)))
        pygame.display.flip()
        pygame.time.wait(1500)

    screen.fill((10, 10, 10))
    fin = font.render("All clips played.", True, (200, 200, 200))
    screen.blit(fin, fin.get_rect(center=(W // 2, H // 2)))
    pygame.display.flip()
    pygame.time.wait(2000)
    pygame.quit()


if __name__ == "__main__":
    main()
