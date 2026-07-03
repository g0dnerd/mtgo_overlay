# Windows-side PyInstaller build. Run from the repo root in PowerShell.
#   .\build.ps1
# Produces dist\MtgoOverlay\ (one folder, no console) with MtgoOverlay.exe inside.
# --onedir over --onefile: faster cold start and no self-extract for AV to flag;
# the Inno Setup installer ships the whole folder anyway.

$ErrorActionPreference = "Stop"

# Keep the Windows venv separate from the WSL .venv living in the same tree.
$env:UV_PROJECT_ENVIRONMENT = ".venv-win"

uv sync --extra dev

# Let PyInstaller's PySide6 hook bundle only the Qt modules actually imported
# (QtCore/QtGui/QtWidgets) rather than --collect-all PySide6, which drags in the
# whole ~650 MB Qt tree. QtSvg is collected explicitly
# because the tray icon needs its imageformat plugin, and scipy's
# compiled submodules are collected because we import scipy.optimize dynamically.
uv run pyinstaller `
    --onedir `
    --noconsole `
    --name MtgoOverlay `
    --icon assets/tray.ico `
    --add-data "assets;assets" `
    --collect-submodules PySide6.QtSvg `
    --collect-submodules scipy `
    --exclude-module PySide6.QtWebEngineCore `
    --exclude-module PySide6.QtWebEngineWidgets `
    --exclude-module PySide6.QtWebChannel `
    --exclude-module PySide6.QtQuick `
    --exclude-module PySide6.QtQuick3D `
    --exclude-module PySide6.QtQuickWidgets `
    --exclude-module PySide6.QtQml `
    --exclude-module PySide6.QtMultimedia `
    --exclude-module PySide6.QtMultimediaWidgets `
    --exclude-module PySide6.QtPdf `
    --exclude-module PySide6.QtPdfWidgets `
    --exclude-module PySide6.QtDesigner `
    --exclude-module PySide6.QtCharts `
    --exclude-module PySide6.QtDataVisualization `
    --exclude-module PySide6.Qt3DCore `
    --exclude-module PySide6.QtSql `
    --exclude-module pytest `
    --exclude-module _pytest `
    --exclude-module pygments `
    --noconfirm `
    run.py

Write-Host "Built dist\MtgoOverlay\MtgoOverlay.exe"
