# Windows-side PyInstaller build. Run from the repo root in PowerShell.
#   .\build.ps1
# Produces dist\MtgoOverlay\ (one folder, no console) with MtgoOverlay.exe inside.
# --onedir over --onefile: faster cold start and no self-extract for AV to flag;
# the Inno Setup installer ships the whole folder anyway.

$ErrorActionPreference = "Stop"

# Keep the Windows venv separate from the WSL .venv living in the same tree.
$env:UV_PROJECT_ENVIRONMENT = ".venv-win"

uv sync --extra dev

# --collect-all PySide6 / --collect-submodules scipy: pull in every Qt plugin and
# scipy compiled submodule so the exe doesn't crash on a friend's machine that
# lacks the dev tree PyInstaller's static analysis silently relied on.
uv run pyinstaller `
    --onedir `
    --noconsole `
    --name MtgoOverlay `
    --icon assets/tray.ico `
    --add-data "assets;assets" `
    --collect-all PySide6 `
    --collect-submodules scipy `
    --noconfirm `
    run.py

Write-Host "Built dist\MtgoOverlay\MtgoOverlay.exe"
