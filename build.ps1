# Windows-side PyInstaller build. Run from the repo root in PowerShell.
#   .\build.ps1
# Produces dist\MtgoOverlay.exe (one file, no console).

$ErrorActionPreference = "Stop"

# Keep the Windows venv separate from the WSL .venv living in the same tree.
$env:UV_PROJECT_ENVIRONMENT = ".venv-win"

uv sync --extra dev

uv run pyinstaller `
    --onefile `
    --noconsole `
    --name MtgoOverlay `
    --icon assets/tray.ico `
    --add-data "assets;assets" `
    run.py

Write-Host "Built dist\MtgoOverlay.exe"
# If a dependency is missing at runtime (e.g. a Qt plugin or scipy submodule),
# add --collect-all PySide6  /  --collect-submodules scipy  and rebuild.
