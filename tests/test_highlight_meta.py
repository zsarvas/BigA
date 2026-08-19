from __future__ import annotations

from pathlib import Path


def test_clip_title_humanizes_slug():
    from pi_tracker.scenes._clip_player import clip_title_from_path

    assert clip_title_from_path(Path("mike-trout-s-hr-16.mp4")) == "Mike Trout's HR (16)"
    assert "RBI" in clip_title_from_path(Path("ward-2-rbi-double.mp4"))


def test_ended_half_before_break():
    from pi_tracker.highlight_meta import ended_half_before_break

    assert ended_half_before_break("Middle", {"inning": 3}) == (3, "top")
    assert ended_half_before_break("Between", {"inning": 7}) == (7, "bottom")
    assert ended_half_before_break("top", {"inning": 1}) == (0, "")


def test_vtt_inning_parse():
    from pi_tracker.highlight_meta import parse_inning_from_vtt

    assert parse_inning_from_vtt("https://mlb.com/foo_T6_bar.vtt") == (6, "top")
    assert parse_inning_from_vtt("https://mlb.com/foo_B3_bar.vtt") == (3, "bottom")
    assert parse_inning_from_vtt("") is None


def test_build_clip_meta_from_vtt_skips_interviews():
    from pi_tracker.highlight_meta import build_clip_meta

    clip = {
        "blurb": "Mike Trout's homer",
        "cclocation_vtt": "https://x/_T4_.vtt",
    }
    meta = build_clip_meta(1, clip, [])
    assert meta is not None
    assert meta["inning"] == 4
    assert meta["half"] == "top"
    assert meta["source"] == "vtt"
    assert build_clip_meta(1, {"blurb": "Trout talks about the homer"}, []) is None


def test_pick_break_highlight_prefers_same_half(tmp_path):
    from pi_tracker.highlight_meta import pick_break_highlight, write_clip_meta

    a = tmp_path / "a.mp4"
    b = tmp_path / "b.mp4"
    c = tmp_path / "c.mp4"
    for p in (a, b, c):
        p.write_bytes(b"x")
    write_clip_meta(a, {"inning": 5, "half": "top"})
    write_clip_meta(b, {"inning": 5, "half": "bottom"})
    write_clip_meta(c, {"inning": 2, "half": "top"})
    picked = pick_break_highlight(set(), 5, "top", playable_paths=[a, b, c])
    assert picked == a
    picked2 = pick_break_highlight({"a.mp4"}, 5, "top", playable_paths=[a, b, c])
    assert picked2 == b
