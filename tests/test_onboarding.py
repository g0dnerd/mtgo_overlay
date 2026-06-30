"""Onboarding: the first-run gate, the consent commit, and per-page persistence.

Renders under ``QT_QPA_PLATFORM=offscreen`` via the session ``qapp`` fixture;
pages are exercised by calling ``isComplete``/``validatePage`` directly rather
than ``exec()``-ing a modal loop.
"""

from __future__ import annotations

import pytest

from mtgo_overlay.config.settings import Settings
from mtgo_overlay.onboarding.wizard import (
    LogFolderPage,
    OnboardingWizard,
    PrivacyPage,
    UsernamePage,
    likely_mtgo_log_dir,
    needs_onboarding,
)


def test_needs_onboarding_tracks_accepted_disclaimer():
    assert needs_onboarding(Settings()) is True
    assert needs_onboarding(Settings(accepted_disclaimer=True)) is False


def test_likely_mtgo_log_dir_uses_appdata(monkeypatch):
    monkeypatch.setenv("APPDATA", "C:/Users/x/AppData/Roaming")
    assert likely_mtgo_log_dir().endswith("Wizards of the Coast/Magic Online")


def test_likely_mtgo_log_dir_empty_without_appdata(monkeypatch):
    monkeypatch.delenv("APPDATA", raising=False)
    assert likely_mtgo_log_dir() == ""


def test_wizard_has_four_pages(qapp):
    wizard = OnboardingWizard(Settings())
    assert len(wizard.pageIds()) == 4


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
    assert settings.use_live_17lands is True  # consent enables the live endpoint

    # Persisted, so the gate won't re-fire next launch.
    assert Settings.load().accepted_disclaimer is True
    assert Settings.load().use_live_17lands is True


def test_log_folder_page_commits(qapp, tmp_path, monkeypatch):
    settings = _settings(tmp_path, monkeypatch)
    page = LogFolderPage(settings)

    assert page.isComplete() is False
    page.path_edit.setText(str(tmp_path / "logs"))
    assert page.isComplete() is True

    assert page.validatePage() is True
    assert settings.log_dir == str(tmp_path / "logs")
    assert Settings.load().log_dir == str(tmp_path / "logs")


def test_username_page_commits_and_strips(qapp, tmp_path, monkeypatch):
    settings = _settings(tmp_path, monkeypatch)
    page = UsernamePage(settings)

    assert page.isComplete() is False
    page.name_edit.setText("  Tester  ")
    assert page.isComplete() is True

    page.validatePage()
    assert settings.mtgo_username == "Tester"
    assert Settings.load().mtgo_username == "Tester"


def test_pages_prepopulate_from_settings(qapp):
    settings = Settings(mtgo_username="Ann", log_dir="/x/logs")
    assert UsernamePage(settings).name_edit.text() == "Ann"
    assert LogFolderPage(settings).path_edit.text() == "/x/logs"


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
