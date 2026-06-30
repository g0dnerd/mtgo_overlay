"""First-run onboarding wizard: a privacy/affiliation consent gate followed by
the minimal setup a friend needs (log folder, MTGO username) plus the one
invisible prerequisite (enabling draft logging inside MTGO).

The privacy page is the consent gate (it commits ``accepted_disclaimer``); the
setup page is where the user actually chooses their win-rate data source - live
17Lands or their own CSV export - so ``use_live_17lands`` reflects a deliberate
pick rather than a side effect of accepting the notice. Each page commits its
result to :class:`Settings` and ``save()``s on completion, so a half-finished
wizard keeps its progress and never re-shows the already-accepted disclaimer (the
:func:`needs_onboarding` gate is on ``accepted_disclaimer`` alone).

Pages own their input widgets and override ``isComplete``/``validatePage`` so the
flow is exercisable under ``QT_QPA_PLATFORM=offscreen`` without driving Qt's field
system or ``exec()``-ing a modal loop.
"""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
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
    f'<p><b>Open source</b> (GPL-3.0): <a href="{REPO_URL}">{REPO_URL}</a></p>'
)

DATA_SOURCE_TEXT = (
    "<p><b>Live</b> pulls current win rates straight from 17Lands (one small, "
    "cached request per set per day). <b>CSV</b> uses a <i>card_ratings.csv</i> "
    "you download yourself from 17Lands' card data page.</p>"
    "<p><b>Newly released sets:</b> to respect 17Lands' usage guidelines, live "
    "win rates for a just-released set are held back for 12 days. "
    "During that window the overlay shows no win rates for that set "
    "unless you supply your own CSV.</p>"
)

LOGGING_REMINDER_TEXT = (
    "<p>One last thing - the overlay can only see your picks if MTGO is writing a "
    "draft log.</p>"
    "<p><b>Turn on draft logging in MTGO:</b></p>"
    "<ol>"
    "<li>Open the MTGO client and go to <b>Options</b> (the gear / settings icon in the top right).</li>"
    "<li>Navigate to <b>Game History</b> and enable draft logging there.</li>"
    "<li>Start (or replay) a draft - a new log file appears in the folder you "
    "just picked.</li>"
    "</ol>"
    "<p>If win rates don't appear, open the tray menu and choose "
    "<b>Setup status…</b> to check what's missing.</p>"
)


def likely_mtgo_log_dir() -> str:
    """The folder MTGO logs usually live in, as a *starting hint* for the picker.

    Never auto-committed - returns ``""`` off Windows (no ``%APPDATA%``) so the
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
    """Consent gate. Accepting commits the disclaimer; the data-source choice is
    made on the next page."""

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
        self.settings.save()
        return True


class SetupPage(_Page):
    """The actual setup, all on one page: where MTGO writes its draft logs, the
    user's MTGO screen name, and which win-rate data source to use."""

    def __init__(self, settings: Settings, parent=None):
        super().__init__(settings, parent)
        self.setTitle("Set up the overlay")
        self.setSubTitle(
            "Where MTGO logs live, your screen name, and where win rates come from."
        )
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("<b>MTGO log folder</b>"))
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

        layout.addWidget(QLabel("<b>MTGO username</b>"))
        self.name_edit = QLineEdit(settings.mtgo_username)
        self.name_edit.setPlaceholderText("Your exact MTGO screen name")
        self.name_edit.textChanged.connect(self.completeChanged)
        layout.addWidget(self.name_edit)

        layout.addWidget(QLabel("<b>Win-rate data source</b>"))
        self.live_radio = QRadioButton("Live 17Lands data (recommended)")
        self.csv_radio = QRadioButton("Use my own 17Lands CSV export")
        self._source = QButtonGroup(self)
        self._source.addButton(self.live_radio)
        self._source.addButton(self.csv_radio)
        # Default to live unless the user already opted into a CSV.
        prefer_csv = bool(settings.manual_csv_path) and not settings.use_live_17lands
        self.csv_radio.setChecked(prefer_csv)
        self.live_radio.setChecked(not prefer_csv)
        layout.addWidget(self.live_radio)
        layout.addWidget(self.csv_radio)

        csv_row = QHBoxLayout()
        self.csv_edit = QLineEdit(settings.manual_csv_path)
        self.csv_edit.setReadOnly(True)
        self.csv_edit.setPlaceholderText("No CSV selected")
        self.csv_edit.textChanged.connect(self.completeChanged)
        self.csv_browse = QPushButton("Browse…")
        self.csv_browse.clicked.connect(self._browse_csv)
        csv_row.addWidget(self.csv_edit)
        csv_row.addWidget(self.csv_browse)
        layout.addLayout(csv_row)

        explain = QLabel(DATA_SOURCE_TEXT)
        explain.setWordWrap(True)
        layout.addWidget(explain)

        self.csv_radio.toggled.connect(self._sync_source)
        self._sync_source()

    def _sync_source(self) -> None:
        csv = self.csv_radio.isChecked()
        self.csv_edit.setEnabled(csv)
        self.csv_browse.setEnabled(csv)
        self.completeChanged.emit()

    def _browse(self) -> None:
        start = self.path_edit.text() or likely_mtgo_log_dir()
        folder = QFileDialog.getExistingDirectory(self, "Select MTGO log folder", start)
        if folder:
            self.path_edit.setText(folder)

    def _browse_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select 17Lands card_ratings CSV",
            self.csv_edit.text(),
            "CSV files (*.csv);;All files (*)",
        )
        if path:
            self.csv_edit.setText(path)

    def isComplete(self) -> bool:
        if not self.path_edit.text().strip() or not self.name_edit.text().strip():
            return False
        if self.csv_radio.isChecked() and not self.csv_edit.text().strip():
            return False
        return True

    def validatePage(self) -> bool:
        self.settings.log_dir = self.path_edit.text().strip()
        self.settings.mtgo_username = self.name_edit.text().strip()
        if self.csv_radio.isChecked():
            self.settings.use_live_17lands = False
            self.settings.manual_csv_path = self.csv_edit.text().strip()
        else:
            self.settings.use_live_17lands = True
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
        self.setWindowTitle("MTGO Draft Helper - Setup")
        # AeroStyle (the Windows default) leaves the page's text palette light,
        # expecting a dark glass header that Win11 no longer provides - so the
        # body renders white-on-white. ModernStyle uses the normal palette.
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.addPage(PrivacyPage(settings, self))
        self.addPage(SetupPage(settings, self))
        self.addPage(LoggingReminderPage(settings, self))


def run_onboarding(settings: Settings, parent=None) -> bool:
    """Show the wizard modally. Returns True if the user finished it.

    Pages persist as they're completed, so even a False return may have committed
    the disclaimer (and any folder/username entered before cancelling).
    """
    wizard = OnboardingWizard(settings, parent)
    return wizard.exec() == QDialog.DialogCode.Accepted
