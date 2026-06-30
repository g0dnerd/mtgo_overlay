"""Foundation: config TOML round-trip + per-user path resolution."""

from __future__ import annotations

import importlib

from mtgo_overlay.config.settings import OverlayStyle, Settings


def test_settings_defaults_csv_first():
    s = Settings()
    assert s.use_live_17lands is False  # CSV is the sanctioned default
    assert s.fmt == "PremierDraft"
    assert isinstance(s.overlay, OverlayStyle)


def test_settings_roundtrip(tmp_path):
    cfg = tmp_path / "config.toml"
    original = Settings(
        mtgo_username="tester",
        log_dir="/some/log/dir",
        fmt="TradDraft",
        use_live_17lands=True,
        manual_csv_path="/data/card_ratings.csv",
        overlay=OverlayStyle(font_h_frac=0.09, inset_x_frac=0.05, fg="#222222"),
    )
    original.save(cfg)
    assert cfg.exists()

    loaded = Settings.load(cfg)
    assert loaded == original
    assert loaded.overlay.font_h_frac == 0.09
    assert loaded.overlay.inset_x_frac == 0.05


def test_settings_load_missing_returns_defaults(tmp_path):
    assert Settings.load(tmp_path / "nope.toml") == Settings()


def test_settings_ignores_unknown_keys(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        'mtgo_username = "x"\nbogus_key = 1\n\n[overlay]\nfont_h_frac = 0.09\njunk = true\n',
        encoding="utf-8",
    )
    loaded = Settings.load(cfg)
    assert loaded.mtgo_username == "x"
    assert loaded.overlay.font_h_frac == 0.09


def test_paths_honor_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("MTGO_OVERLAY_HOME", str(tmp_path))
    from mtgo_overlay.system import paths

    importlib.reload(paths)
    assert str(tmp_path) in str(paths.config_dir())
    assert str(tmp_path) in str(paths.cache_dir())
    assert paths.config_file().name == "config.toml"
