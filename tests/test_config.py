from __future__ import annotations

from pi_tracker import config


def test_version_string_shape():
    assert config.VERSION
    assert config.version_string().startswith("v")
    assert config.BUILD_DATE in config.version_string()


def test_layout_maps_reference_panel():
    assert config.layout_x(config.LAYOUT_REF_WIDTH) == config.SCREEN_WIDTH
    assert config.layout_y(config.LAYOUT_REF_HEIGHT) == config.SCREEN_HEIGHT
    assert config.layout_x(0) == 0
    assert config.layout_size(10) >= 10


def test_env_helpers():
    assert config._env_positive_int("MISSING_ENV_XYZ", 7) == 7
    assert config._env_clamp_int("MISSING", 180, lo=30, hi=900) == 180
    assert config._env_float("MISSING", 1.15, lo=0.6, hi=2.0) == 1.15


def test_mpv_timeout_default():
    assert config.MPV_CLIP_TIMEOUT_SEC == 180
    assert 2 <= config.DISPLAY_FAIL_REBOOT_COUNT <= 10
