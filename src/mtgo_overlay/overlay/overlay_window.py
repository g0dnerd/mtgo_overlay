"""Single click-through overlay window that draws every label in one paintEvent.

Receives labels already in overlay-logical coordinates (the AppController maps
capture-px -> logical) and pins to MTGO's client origin. Each GIH win rate is
drawn as a tier-colored pill (red->green) anchored to the **bottom of the card
art** — below the title bar (name + mana cost) and above the type line / rules
text, so it never hides anything the drafter needs. Every offset is a fraction of
the card box, so it survives any MTGO size / DPI.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..config.settings import OverlayStyle
from ..system import win32

# Red->green ramp keyed on a card's *percentile within its set* (0 worst, 1 best),
# so the color adapts to each set's win-rate distribution instead of fixed cutoffs.
_RAMP: tuple[tuple[float, str], ...] = (
    (0.00, "#c0392b"),  # red
    (0.25, "#e67e22"),  # orange
    (0.50, "#d4ac0d"),  # gold
    (0.75, "#7fb800"),  # yellow-green
    (1.00, "#2e9e3f"),  # green
)

# Below this many rated cards a percentile is meaningless; leave the pill neutral.
MIN_DISTRIBUTION = 10


def format_wr(gih_wr: float | None) -> str:
    return "N/A" if gih_wr is None else f"{gih_wr:.1f}"


def percentile_rank(value: float, sorted_values: list[float]) -> float | None:
    """Midrank percentile of ``value`` in ``sorted_values`` -> 0..1, or ``None``
    when there's too little data to rank against."""
    n = len(sorted_values)
    if n < MIN_DISTRIBUTION:
        return None
    lo = bisect.bisect_left(sorted_values, value)
    hi = bisect.bisect_right(sorted_values, value)
    return (lo + hi) / 2 / n


def ramp_color(tier: float | None, unknown: str = "#6b7280") -> QColor:
    """Map a 0..1 percentile to the red->green ramp; ``None`` -> neutral gray."""
    if tier is None:
        return QColor(unknown)
    t = max(0.0, min(1.0, tier))
    for (lo, c_lo), (hi, c_hi) in zip(_RAMP, _RAMP[1:]):
        if lo <= t <= hi:
            f = (t - lo) / (hi - lo)
            a, b = QColor(c_lo), QColor(c_hi)
            return QColor(
                round(a.red() + f * (b.red() - a.red())),
                round(a.green() + f * (b.green() - a.green())),
                round(a.blue() + f * (b.blue() - a.blue())),
            )
    return QColor(_RAMP[-1][1])


@dataclass(frozen=True)
class LabelSpec:
    """A GIH win rate (shown as text), its percentile-within-set ``tier`` (drives
    the pill color), and the card box (overlay-logical px) it belongs to."""

    gih_wr: float | None
    tier: float | None
    x: int
    y: int
    w: int
    h: int


class OverlayWindow(QWidget):
    def __init__(self, style: OverlayStyle | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.style = style or OverlayStyle()
        self._labels: list[LabelSpec] = []

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

    # --- public API ----------------------------------------------------------

    def set_labels(self, labels: list[LabelSpec]) -> None:
        self._labels = list(labels)
        self.update()

    def clear(self) -> None:
        self._labels = []
        self.update()

    def label_rects(self) -> list[tuple[LabelSpec, QRect]]:
        """Computed pill rects for the current labels (no painting)."""
        return [(spec, self._compute_rect(spec).toRect()) for spec in self._labels]

    # --- geometry ------------------------------------------------------------

    def _font_for(self, card_h: int) -> QFont:
        font = QFont(self.style.font_family)
        font.setPixelSize(max(8, round(card_h * self.style.font_h_frac)))
        font.setBold(True)
        return font

    def _compute_rect(self, spec: LabelSpec) -> QRectF:
        fm = QFontMetrics(self._font_for(spec.h))
        pad_x = self.style.pad_x_frac * spec.w
        pad_y = self.style.pad_y_frac * spec.h
        bw = fm.horizontalAdvance(format_wr(spec.gih_wr)) + 2 * pad_x
        bh = fm.height() + 2 * pad_y
        inset = self.style.inset_x_frac * spec.w
        x = spec.x + spec.w - inset - bw
        y = spec.y + self.style.pill_bottom_frac * spec.h - bh
        # Clamp fully inside the card box.
        x = max(spec.x, min(x, spec.x + spec.w - bw))
        y = max(spec.y, min(y, spec.y + spec.h - bh))
        return QRectF(x, y, bw, bh)

    # --- painting ------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        fg = QColor(self.style.fg)

        for spec in self._labels:
            rect = self._compute_rect(spec)
            radius = rect.height() / 2
            painter.setFont(self._font_for(spec.h))
            # Soft drop shadow so the pill reads on bright art.
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, 90))
            painter.drawRoundedRect(rect.translated(0, max(1.0, rect.height() * 0.06)), radius, radius)
            # Percentile-colored pill with a thin dark rim.
            painter.setBrush(ramp_color(spec.tier, self.style.unknown_color))
            painter.setPen(QPen(QColor(0, 0, 0, 160), 1.2))
            painter.drawRoundedRect(rect, radius, radius)
            painter.setPen(fg)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, format_wr(spec.gih_wr))
        painter.end()

    # --- Win32 click-through (Windows only) ----------------------------------

    def showEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().showEvent(event)
        if win32.IS_WINDOWS:
            try:
                win32.set_click_through(int(self.winId()))
            except OSError:
                pass
