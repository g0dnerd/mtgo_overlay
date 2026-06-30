"""Local-data clearing: caches, config, and logs are removed; the directory
skeleton recreates lazily."""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def paths(tmp_path, monkeypatch):
    monkeypatch.setenv("MTGO_OVERLAY_HOME", str(tmp_path))
    mod = importlib.import_module("mtgo_overlay.system.paths")
    return mod


def test_clear_local_data_removes_caches_config_and_logs(paths):
    (paths.ratings_cache_dir() / "FIN_PremierDraft.json").write_text("{}")
    (paths.scryfall_cache_dir() / "abc.png").write_bytes(b"art")
    (paths.logs_dir() / "capture_gen1.png").write_bytes(b"frame")
    (paths.logs_dir() / "mtgo_overlay.log").write_text("log")
    paths.config_file().write_text("mtgo_username = 'x'\n")

    removed = paths.clear_local_data()

    assert not (paths.cache_dir() / "ratings" / "FIN_PremierDraft.json").exists()
    assert not (paths.cache_dir() / "scryfall" / "abc.png").exists()
    assert not any(paths.logs_dir().glob("capture_gen*.png"))
    assert {p.name for p in removed} == {"cache", "logs", "config.toml"}


def test_clear_local_data_is_idempotent_on_empty(paths):
    paths.clear_local_data()
    # A second call with nothing left to remove must not raise.
    assert paths.clear_local_data() == []
