from __future__ import annotations

from pathlib import Path


def test_skip_interview_and_return_from_il_blurbs():
    from pi_tracker.mlb_highlights import is_skip_highlight_blurb, should_download

    skip = [
        "Trout talks about the win",
        "Ohtani on his rehab assignment",
        "Ward on her approach at the plate",
        "Player interview after the game",
        "Angels return from IL",
        "After return, he discusses the injury",
        "Probable pitchers for tonight",
        "Bullpen availability",
        "Condensed Game: Angels vs Yankees",
    ]
    keep = [
        "Mike Trout's 2-run homer",
        "Breaking down Ward's swing",
        "Angels win on a walk-off",
    ]
    for blurb in skip:
        assert is_skip_highlight_blurb(blurb) or not should_download(blurb), blurb
    for blurb in keep:
        assert should_download(blurb), blurb


def test_skip_highlight_path_slugs():
    from pi_tracker.mlb_highlights import is_skip_highlight_path

    assert is_skip_highlight_path(Path("ohtani-on-his-rehab.mp4"))
    assert is_skip_highlight_path(Path("trout-talks-about-the-win.mp4"))
    assert is_skip_highlight_path(Path("return-from-il.mp4"))
    assert not is_skip_highlight_path(Path("mike-trout-s-hr-16.mp4"))


def test_condensed_game_off_by_default(monkeypatch):
    from pi_tracker.mlb_highlights import condensed_games_enabled, is_condensed_game_blurb, should_download

    monkeypatch.delenv("BIGA_ALLOW_CONDENSED", raising=False)
    assert condensed_games_enabled() is False
    assert is_condensed_game_blurb("Condensed Game: Angels vs Yanks")
    assert should_download("Condensed Game: Angels vs Yanks") is False


def test_parse_duration_and_slug():
    from pi_tracker.mlb_highlights import _parse_mlb_duration, _slug

    assert _parse_mlb_duration("00:03:05") == 185
    assert _parse_mlb_duration("1:02") == 62
    assert _parse_mlb_duration("nope") is None
    assert _slug("Mike Trout's HR (16)") == "mike-trout-s-hr-16"


def test_best_mp4_prefers_avc():
    from pi_tracker.mlb_highlights import _best_mp4_url

    url = _best_mp4_url(
        [
            {"name": "highBit", "url": "https://x/hi.mp4"},
            {"name": "mp4Avc", "url": "https://x/avc.mp4"},
        ]
    )
    assert url == "https://x/avc.mp4"


def test_clip_too_large_to_fetch():
    from pi_tracker import mlb_highlights as hl

    assert hl._clip_too_large_to_fetch({"duration_sec": 9999}, None)
    assert hl._clip_too_large_to_fetch({"duration_sec": 30}, hl._MAX_DOWNLOAD_CLIP_BYTES + 1)
    assert hl._clip_too_large_to_fetch({"duration_sec": 30}, 1000) is None


def test_highlight_chill_low_ram(monkeypatch, tmp_path):
    from pi_tracker import mlb_highlights as hl

    monkeypatch.setattr(hl, "_meminfo_kb", lambda: {"MemAvailable": 40 * 1024, "SwapTotal": 0, "SwapFree": 0})
    reason = hl.highlight_work_blocked_reason(tmp_path)
    assert reason is not None
    assert "MemAvailable" in reason


def test_highlight_chill_ignores_sticky_swap(monkeypatch, tmp_path):
    from pi_tracker import mlb_highlights as hl

    # After ffmpeg, Linux often leaves swap used even with healthy MemAvailable.
    # That must not freeze the highlight reel (main's field-learned policy).
    monkeypatch.setattr(
        hl,
        "_meminfo_kb",
        lambda: {
            "MemAvailable": 512 * 1024,
            "SwapTotal": 256 * 1024,
            "SwapFree": 0,
        },
    )
    assert hl.highlight_work_blocked_reason(tmp_path) is None


def test_highlight_chill_enough_ready(monkeypatch, tmp_path, no_ffprobe):
    from pi_tracker import mlb_highlights as hl

    monkeypatch.setattr(hl, "_meminfo_kb", lambda: {})
    monkeypatch.setattr(hl, "_HL_MAX_READY", 4, raising=False)
    for i in range(4):
        (tmp_path / f"clip{i}.mp4").write_bytes(b"\x00" * 4096)
    reason = hl.highlight_work_blocked_reason(tmp_path)
    assert reason is not None
    assert "ready clips" in reason


def test_should_run_highlight_downloader():
    from datetime import date

    from pi_tracker.mlb_highlights import should_run_highlight_downloader

    assert should_run_highlight_downloader({"scene": "live"})
    assert should_run_highlight_downloader({"scene": "win"})
    assert should_run_highlight_downloader({"scene": "idle", "live_game_pk": 123})
    assert should_run_highlight_downloader(
        {"scene": "idle", "final_display_date": date.today().isoformat()}
    )
    assert not should_run_highlight_downloader({"scene": "idle", "live_game_pk": None})


def test_resolve_highlight_game_pk(tmp_path, monkeypatch):
    from pi_tracker import config
    from pi_tracker.mlb_highlights import resolve_highlight_game_pk

    assert resolve_highlight_game_pk({"live_game_pk": 9, "next_game_pk": 1}) == 9
    assert resolve_highlight_game_pk({"next_game_pk": 2}) == 2
    monkeypatch.setattr(config, "GAME_HIGHLIGHTS_DIR", tmp_path)
    (tmp_path / "100").mkdir()
    (tmp_path / "200").mkdir()
    assert resolve_highlight_game_pk({}) == 200


def test_fetch_highlight_clips_filters(monkeypatch):
    from pi_tracker import mlb_highlights as hl

    payload = {
        "highlights": {
            "highlights": {
                "items": [
                    {
                        "type": "video",
                        "id": "1",
                        "blurb": "Mike Trout's homer",
                        "duration": "00:00:20",
                        "playbacks": [{"name": "mp4Avc", "url": "https://x/a.mp4"}],
                    },
                    {
                        "type": "video",
                        "id": "2",
                        "blurb": "Trout talks about the homer",
                        "playbacks": [{"name": "mp4Avc", "url": "https://x/b.mp4"}],
                    },
                    {"type": "photo", "id": "3", "blurb": "photo"},
                ]
            }
        }
    }

    class Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return payload

    monkeypatch.setattr(hl.requests, "get", lambda *a, **k: Resp())
    clips, ok = hl.fetch_highlight_clips_result(1)
    assert ok
    assert [c["id"] for c in clips] == ["1"]


def test_is_likely_playable_skips_temps_and_fluff(tmp_path, monkeypatch):
    from pi_tracker import config
    from pi_tracker.mlb_highlights import is_likely_playable_game_clip

    monkeypatch.setattr(config, "GAME_HIGHLIGHTS_DIR", tmp_path)
    folder = tmp_path / "1"
    folder.mkdir()
    good = folder / "trout-hr.mp4"
    good.write_bytes(b"\x00" * 4096)
    tmp = folder / "trout.rawdl"
    tmp.write_bytes(b"\x00" * 4096)
    assert is_likely_playable_game_clip(good)
    assert not is_likely_playable_game_clip(tmp)
