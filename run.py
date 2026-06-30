"""Entrypoint: DPI awareness -> QApplication -> AppController -> exec.

Run on the Windows side: ``C:\\path\\to\\venv\\Scripts\\python.exe run.py``.
Also the PyInstaller entry script (see build.ps1).
"""

from __future__ import annotations

import pathlib
import sys

# Allow ``python run.py`` from a fresh checkout (no editable install required).
_SRC = pathlib.Path(__file__).resolve().parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from mtgo_overlay.app import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
