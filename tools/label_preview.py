"""Render GIH-label design variants onto the real MSH fixture screenshot.

Dev-only visual harness (WSL, offscreen Qt). Produces one PNG per variant in the
scratch dir so a human can compare looks before we commit one to the overlay.

    QT_QPA_PLATFORM=offscreen uv run python tools/label_preview.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
FIXTURE_PNG = ROOT / "tests/fixtures/msh/pack1_pick1.png"
FIXTURE_JSON = ROOT / "tests/fixtures/msh/pack1_pick1.json"
OUT_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "label_previews"


# Plausible GIH WR spread across the visible cards, chosen to exercise every
# tier color (the real fixture has no ratings attached).
DEMO_WR = [47.8, 51.2, 53.9, 55.1, 56.7, 58.4, 61.3, 64.9, 49.5, 52.5, 54.6, 57.2, 59.8, 50.0]


def tier_color(wr: float) -> QColor:
    """Diverging red->green ramp anchored on real-draft GIH WR (~50 poor, ~59 great)."""
    stops = [
        (49.0, QColor("#c0392b")),  # red
        (52.0, QColor("#e67e22")),  # orange
        (55.0, QColor("#d4ac0d")),  # gold
        (57.5, QColor("#7fb800")),  # yellow-green
        (60.0, QColor("#2e9e3f")),  # green
    ]
    if wr <= stops[0][0]:
        return stops[0][1]
    if wr >= stops[-1][0]:
        return stops[-1][1]
    for (lo, c_lo), (hi, c_hi) in zip(stops, stops[1:]):
        if lo <= wr <= hi:
            t = (wr - lo) / (hi - lo)
            return QColor(
                round(c_lo.red() + t * (c_hi.red() - c_lo.red())),
                round(c_lo.green() + t * (c_hi.green() - c_lo.green())),
                round(c_lo.blue() + t * (c_hi.blue() - c_lo.blue())),
            )
    return stops[-1][1]


def _outlined_text(p: QPainter, rect, text, font, fill, outline, flags):
    """Crisp text on any background: a dark stroked outline behind a solid fill."""
    path = QPainterPath()
    fm = QFontMetrics(font)
    # Center the baseline manually so the stroke is symmetric.
    tw = fm.horizontalAdvance(text)
    tx = rect.x() + (rect.width() - tw) / 2 if flags & Qt.AlignmentFlag.AlignHCenter else rect.x()
    ty = rect.y() + (rect.height() + fm.ascent() - fm.descent()) / 2
    path.addText(tx, ty, font, text)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.strokePath(path, QPen(outline, 3.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.fillPath(path, fill)


# --- variants ---------------------------------------------------------------

def variant_pill(p: QPainter, box, wr):
    """Solid tier-colored rounded pill, white bold number, bottom-right of the art."""
    x, y, w, h = box
    text = f"{wr:.1f}"
    font = QFont("Segoe UI", 0)
    font.setPixelSize(round(h * 0.072))
    font.setBold(True)
    fm = QFontMetrics(font)
    pad_x, pad_y = round(w * 0.035), round(h * 0.012)
    bw = fm.horizontalAdvance(text) + 2 * pad_x
    bh = fm.height() + 2 * pad_y
    inset = round(w * 0.045)
    rx = x + w - inset - bw
    # Sit at the bottom of the art, clear of the title bar (name+mana) above and
    # the type line / rules text below.
    ry = y + round(h * 0.50) - bh
    rect = QRectF(rx, ry, bw, bh)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    # soft shadow
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(0, 0, 0, 90))
    p.drawRoundedRect(rect.translated(0, 1.5), bh / 2, bh / 2)
    # pill
    p.setBrush(tier_color(wr))
    p.setPen(QPen(QColor(0, 0, 0, 160), 1.2))
    p.drawRoundedRect(rect, bh / 2, bh / 2)
    p.setPen(QColor("#ffffff"))
    p.setFont(font)
    p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)


def variant_outlined(p: QPainter, box, wr):
    """No box: large tier-colored number stroked in black, floats on the art."""
    x, y, w, h = box
    text = f"{wr:.0f}"
    font = QFont("Segoe UI", 0)
    font.setPixelSize(round(h * 0.10))
    font.setBold(True)
    fm = QFontMetrics(font)
    bw = fm.horizontalAdvance(text) + round(w * 0.04)
    inset = round(w * 0.04)
    rx = x + w - inset - bw
    ry = y + round(h * 0.075)
    rect = QRect(rx, ry, bw, round(fm.height() * 1.05))
    _outlined_text(p, rect, text, font, QBrush(tier_color(wr).lighter(115)),
                   QColor(0, 0, 0, 235), Qt.AlignmentFlag.AlignHCenter)


def variant_corner_chip(p: QPainter, box, wr):
    """Compact chip pinned to the art's top-left with a colored side accent."""
    x, y, w, h = box
    text = f"{wr:.1f}"
    font = QFont("Segoe UI", 0)
    font.setPixelSize(round(h * 0.060))
    font.setBold(True)
    fm = QFontMetrics(font)
    pad_x, pad_y = round(w * 0.04), round(h * 0.012)
    accent = round(w * 0.022)
    bw = fm.horizontalAdvance(text) + 2 * pad_x + accent
    bh = fm.height() + 2 * pad_y
    inset = round(w * 0.04)
    rx = x + inset
    ry = y + round(h * 0.085)
    rect = QRectF(rx, ry, bw, bh)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(18, 18, 20, 215))
    p.drawRoundedRect(rect, 4, 4)
    accent_rect = QRectF(rx, ry, accent, bh)
    path = QPainterPath()
    path.addRoundedRect(rect, 4, 4)
    p.setClipPath(path)
    p.fillRect(accent_rect, tier_color(wr))
    p.setClipping(False)
    p.setFont(font)
    p.setPen(QColor("#f2f2f2"))
    p.drawText(QRectF(rx + accent, ry, bw - accent, bh),
               Qt.AlignmentFlag.AlignCenter, text)


VARIANTS = {
    "pill": variant_pill,
    "outlined": variant_outlined,
    "chip": variant_corner_chip,
}


def main() -> int:
    QApplication(sys.argv)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    meta = json.loads(FIXTURE_JSON.read_text())
    cards = meta["cards"]

    for name, fn in VARIANTS.items():
        img = QImage(str(FIXTURE_PNG))
        if img.isNull():
            raise SystemExit(f"could not load {FIXTURE_PNG}")
        p = QPainter(img)
        for i, card in enumerate(cards):
            wr = DEMO_WR[i % len(DEMO_WR)]
            fn(p, card["bbox"], wr)
        p.end()
        out = OUT_DIR / f"label_{name}.png"
        img.save(str(out))
        print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
