"""First-run onboarding wizard: a privacy/affiliation consent gate followed by
the minimal setup a friend needs (log folder, MTGO username) plus the one
invisible prerequisite (enabling draft logging inside MTGO).

The wizard is the *consent point* for the live 17Lands endpoint: accepting the
privacy notice flips ``use_live_17lands`` on. Each page commits its result to
:class:`Settings` and ``save()``s on completion, so a half-finished wizard keeps
its progress and never re-shows the already-accepted disclaimer (the
:func:`needs_onboarding` gate is on ``accepted_disclaimer`` alone).

Pages own their input widgets and override ``isComplete``/``validatePage`` so the
flow is exercisable under ``QT_QPA_PLATFORM=offscreen`` without driving Qt's field
system or ``exec()``-ing a modal loop.
"""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWizard,
    QWizardPage,
)

from ..config.settings import Settings

REPO_URL = "https://github.com/g0dnerd/mtgo_overlay"

PRIVACY_TEXT = (
    "<p><b>What this tool does.</b> It reads your MTGO draft log and takes "
    "screenshots of the MTGO window to find the cards you're being shown, then "
    "draws 17Lands win rates over them.</p>"
    "<p><b>Your screen stays on your machine.</b> Screenshots are processed "
    "locally and are never uploaded anywhere.</p>"
    "<p><b>Network access.</b> The only outbound requests are to <b>Scryfall</b> "
    "(card images) and <b>17Lands</b> (win-rate data). No personal data is sent.</p>"
    "<p><b>Not affiliated.</b> This project is not affiliated with, endorsed by, "
    "or sponsored by 17Lands or Wizards of the Coast. All trademarks belong to "
    "their respective owners.</p>"
    f"<p><b>Open source</b> (GPL-3.0): <a href=\"{REPO_URL}\">{REPO_URL}</a></p>"
)

LOGGING_REMINDER_TEXT = (
    "<p>One last thing — the overlay can only see your picks if MTGO is writing a "
    "draft log.</p>"
    "<p><b>Turn on draft logging in MTGO:</b></p>"
    "<ol>"
    "<li>Open the MTGO client and go to <b>Options</b> (the gear / settings).</li>"
    "<li>Find the logging / journaling setting and enable draft logging.</li>"
    "<li>Start (or replay) a draft — a new log file appears in the folder you "
    "just picked.</li>"
    "</ol>"
    "<p>If win rates don't appear, open the tray menu and choose "
    "<b>Setup status…</b> to check what's missing.</p>"
)


def likely_mtgo_log_dir() -> str:
    """The folder MTGO logs usually live in, as a *starting hint* for the picker.

    Never auto-committed — returns ``""`` off Windows (no ``%APPDATA%``) so the
    picker just opens at its default location.
    """
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return ""
    return str(Path(appdata) / "Wizards of the Coast" / "Magic Online")


def needs_onboarding(settings: Settings) -> bool:
    """True until the user has accepted the privacy/affiliation notice.

    A missing config file loads as defaults (``accepted_disclaimer=False``), so
    this also covers a genuine first run.
    """
    return not settings.accepted_disclaimer


class _Page(QWizardPage):
    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.settings = settings


class PrivacyPage(_Page):
    """Consent gate. Accepting commits the disclaimer *and* enables live data."""

    def __init__(self, settings: Settings, parent=None):
        super().__init__(settings, parent)
        self.setTitle("Privacy & affiliation")
        self.setSubTitle("Please read and accept before continuing.")
        layout = QVBoxLayout(self)
        notice = QLabel(PRIVACY_TEXT)
        notice.setWordWrap(True)
        notice.setOpenExternalLinks(True)
        layout.addWidget(notice)
        self.accept_box = QCheckBox("I understand and accept.")
        self.accept_box.toggled.connect(self.completeChanged)
        layout.addWidget(self.accept_box)

    def isComplete(self) -> bool:
        return self.accept_box.isChecked()

    def validatePage(self) -> bool:
        self.settings.accepted_disclaimer = True
        self.settings.use_live_17lands = True
        self.settings.save()
        return True


class LogFolderPage(_Page):
    """Pick the MTGO log folder. Pre-navigates to the likely path as a hint."""

    def __init__(self, settings: Settings, parent=None):
        super().__init__(settings, parent)
        self.setTitle("MTGO log folder")
        self.setSubTitle(
            "Pick the folder where MTGO writes its draft logs. It's usually under "
            "your AppData; the Browse button starts you there."
        )
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.path_edit = QLineEdit(settings.log_dir)
        self.path_edit.setReadOnly(True)
        self.path_edit.setPlaceholderText("No folder selected")
        self.path_edit.textChanged.connect(self.completeChanged)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(self.path_edit)
        row.addWidget(browse)
        layout.addLayout(row)

    def _browse(self) -> None:
        start = self.path_edit.text() or likely_mtgo_log_dir()
        folder = QFileDialog.getExistingDirectory(
            self, "Select MTGO log folder", start
        )
        if folder:
            self.path_edit.setText(folder)

    def isComplete(self) -> bool:
        return bool(self.path_edit.text().strip())

    def validatePage(self) -> bool:
        self.settings.log_dir = self.path_edit.text().strip()
        self.settings.save()
        return True


class UsernamePage(_Page):
    """Enter the MTGO username (used to attribute picks in the draft log)."""

    def __init__(self, settings: Settings, parent=None):
        super().__init__(settings, parent)
        self.setTitle("MTGO username")
        self.setSubTitle("Enter your exact MTGO screen name.")
        layout = QVBoxLayout(self)
        self.name_edit = QLineEdit(settings.mtgo_username)
        self.name_edit.setPlaceholderText("Your MTGO username")
        self.name_edit.textChanged.connect(self.completeChanged)
        layout.addWidget(self.name_edit)

    def isComplete(self) -> bool:
        return bool(self.name_edit.text().strip())

    def validatePage(self) -> bool:
        self.settings.mtgo_username = self.name_edit.text().strip()
        self.settings.save()
        return True


class LoggingReminderPage(_Page):
    """Static instructions for the invisible prerequisite: MTGO draft logging."""

    def __init__(self, settings: Settings, parent=None):
        super().__init__(settings, parent)
        self.setTitle("Enable draft logging in MTGO")
        layout = QVBoxLayout(self)
        text = QLabel(LOGGING_REMINDER_TEXT)
        text.setWordWrap(True)
        layout.addWidget(text)


class OnboardingWizard(QWizard):
    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("MTGO 17lands Overlay — Setup")
        # AeroStyle (the Windows default) leaves the page's text palette light,
        # expecting a dark glass header that Win11 no longer provides — so the
        # body renders white-on-white. ModernStyle uses the normal palette.
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.addPage(PrivacyPage(settings, self))
        self.addPage(LogFolderPage(settings, self))
        self.addPage(UsernamePage(settings, self))
        self.addPage(LoggingReminderPage(settings, self))


def run_onboarding(settings: Settings, parent=None) -> bool:
    """Show the wizard modally. Returns True if the user finished it.

    Pages persist as they're completed, so even a False return may have committed
    the disclaimer (and any folder/username entered before cancelling).
    """
    wizard = OnboardingWizard(settings, parent)
    return wizard.exec() == QDialog.DialogCode.Accepted
