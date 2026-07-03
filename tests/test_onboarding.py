"""Onboarding: the first-run gate, the consent commit, and per-page persistence.

Renders under ``QT_QPA_PLATFORM=offscreen`` via the session ``qapp`` fixture;
pages are exercised by calling ``isComplete``/``validatePage`` directly rather
than ``exec()``-ing a modal loop.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mtgo_overlay.config.settings import Settings
from mtgo_overlay.onboarding.wizard import (
    OnboardingWizard,
    PrivacyPage,
    SetupPage,
    likely_mtgo_log_dir,
    needs_onboarding,
)


def test_needs_onboarding_tracks_accepted_disclaimer():
    assert needs_onboarding(Settings()) is True
    assert needs_onboarding(Settings(accepted_disclaimer=True)) is False


def test_likely_mtgo_log_dir_uses_appdata(monkeypatch):
    monkeypatch.setenv("APPDATA", "C:/Users/x/AppData/Roaming")
    # str(Path(...)) uses the host separator, so compare on parts, not slashes.
    assert Path(likely_mtgo_log_dir()).parts[-2:] == (
        "Wizards of the Coast",
        "Magic Online",
    )


def test_likely_mtgo_log_dir_empty_without_appdata(monkeypatch):
    monkeypatch.delenv("APPDATA", raising=False)
    assert likely_mtgo_log_dir() == ""


def test_wizard_has_three_pages(qapp):
    wizard = OnboardingWizard(Settings())
    assert len(wizard.pageIds()) == 3


def _settings(tmp_path, monkeypatch) -> Settings:
    monkeypatch.setenv("MTGO_OVERLAY_HOME", str(tmp_path))
    return Settings()


def test_privacy_page_gates_then_commits_consent(qapp, tmp_path, monkeypatch):
    settings = _settings(tmp_path, monkeypatch)
    page = PrivacyPage(settings)

    assert page.isComplete() is False  # gated until the box is checked
    page.accept_box.setChecked(True)
    assert page.isComplete() is True

    assert page.validatePage() is True
    assert settings.accepted_disclaimer is True
    # Consent no longer picks a data source; that's the setup page's job.
    assert settings.use_live_17lands is False

    # Persisted, so the gate won't re-fire next launch.
    assert Settings.load().accepted_disclaimer is True


def test_setup_page_requires_folder_and_username(qapp, tmp_path, monkeypatch):
    settings = _settings(tmp_path, monkeypatch)
    page = SetupPage(settings)

    assert page.isComplete() is False  # nothing entered yet
    page.path_edit.setText(str(tmp_path / "logs"))
    assert page.isComplete() is False  # username still missing
    page.name_edit.setText("  Tester  ")
    assert page.isComplete() is True  # live is the default source


def test_setup_page_live_choice_commits(qapp, tmp_path, monkeypatch):
    settings = _settings(tmp_path, monkeypatch)
    page = SetupPage(settings)
    page.path_edit.setText(str(tmp_path / "logs"))
    page.name_edit.setText("Tester")

    assert page.validatePage() is True
    assert settings.log_dir == str(tmp_path / "logs")
    assert settings.mtgo_username == "Tester"  # stripped on the line edit
    assert settings.use_live_17lands is True
    loaded = Settings.load()
    assert loaded.use_live_17lands is True
    assert loaded.log_dir == str(tmp_path / "logs")


def test_setup_page_csv_choice_requires_path_then_commits(qapp, tmp_path, monkeypatch):
    settings = _settings(tmp_path, monkeypatch)
    page = SetupPage(settings)
    page.path_edit.setText(str(tmp_path / "logs"))
    page.name_edit.setText("Tester")
    page.csv_radio.setChecked(True)

    assert page.isComplete() is False  # CSV picked but no file yet
    csv = tmp_path / "card_ratings.csv"
    page.csv_edit.setText(str(csv))
    assert page.isComplete() is True

    assert page.validatePage() is True
    assert settings.use_live_17lands is False
    assert settings.manual_csv_path == str(csv)
    assert Settings.load().manual_csv_path == str(csv)


def test_setup_page_defaults_to_csv_when_already_configured(qapp):
    settings = Settings(manual_csv_path="/x/r.csv", use_live_17lands=False)
    page = SetupPage(settings)
    assert page.csv_radio.isChecked() is True
    assert page.live_radio.isChecked() is False


def test_setup_page_prepopulates_from_settings(qapp):
    settings = Settings(mtgo_username="Ann", log_dir="/x/logs")
    page = SetupPage(settings)
    assert page.name_edit.text() == "Ann"
    assert page.path_edit.text() == "/x/logs"
    assert page.live_radio.isChecked() is True  # live default


def test_setup_page_prefills_username_from_logs(qapp, tmp_path):
    (tmp_path / "pjk_-2026.6.30-10814-35390973-MSHMSHMSH.txt").write_text("x")
    page = SetupPage(Settings())
    page._prefill_username(str(tmp_path))
    assert page.name_edit.text() == "pjk_"


def test_setup_page_prefill_respects_typed_username(qapp, tmp_path):
    (tmp_path / "pjk_-2026.6.30-10814-35390973-MSHMSHMSH.txt").write_text("x")
    page = SetupPage(Settings())
    page.name_edit.setText("Ann")
    page._prefill_username(str(tmp_path))
    assert page.name_edit.text() == "Ann"  # never clobbers what the user typed


def test_start_gate_runs_wizard_only_when_unaccepted(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("MTGO_OVERLAY_HOME", str(tmp_path))
    import mtgo_overlay.app as app

    calls: list[bool] = []
    monkeypatch.setattr(app, "run_onboarding", lambda settings: calls.append(True))

    controller = app.AppController(qapp)
    controller._maybe_run_onboarding()
    assert calls == [True]  # first run: disclaimer not accepted

    controller.settings.accepted_disclaimer = True
    controller._maybe_run_onboarding()
    assert calls == [True]  # accepted: no second wizard

    controller.shutdown()
