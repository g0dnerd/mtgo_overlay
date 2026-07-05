"""A fake MTGO window: shows a screenshot under an MTGO-like title.

Lets you exercise the entire Windows path (window discovery, client capture,
client->screen + DPI mapping, click-through, always-on-top, move/resize tracking)
with NO real MTGO - just a screenshot you provide. Run it, then run the overlay
(`uv run python run.py`) and point it at this window.

  uv run python tools/fake_mtgo.py shot.png --geometry 1600x1000+200+100
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtGui import QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow  # noqa: E402


def _parse_geometry(spec: str | None):
    if not spec:
        return None
    size, _, pos = spec.partition("+")
    w, _, h = size.partition("x")
    geom = [int(w), int(h)]
    if pos:
        x, _, y = pos.partition("+")
        geom += [int(x), int(y)]
    else:
        geom += [100, 100]
    return geom[2], geom[3], geom[0], geom[1]  # x, y, w, h


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("screenshot", type=Path)
    ap.add_argument(
        "--title",
        default="Magic: The Gathering Online - Draft League",
        help="window title (must contain an MTGO-recognized substring)",
    )
    ap.add_argument("--geometry", default=None, help="WxH+X+Y, e.g. 1600x1000+200+100")
    args = ap.parse_args()

    app = QApplication(sys.argv)
    win = QMainWindow()
    win.setWindowTitle(args.title)

    label = QLabel()
    pix = QPixmap(str(args.screenshot))
    if pix.isNull():
        ap.error(f"Could not load screenshot: {args.screenshot}")
    label.setPixmap(pix)
    label.setScaledContents(True)
    win.setCentralWidget(label)

    geom = _parse_geometry(args.geometry)
    if geom:
        win.setGeometry(*geom)
    else:
        win.resize(pix.width(), pix.height())
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
