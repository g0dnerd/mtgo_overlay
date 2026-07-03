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


def format_tix(tix: float | None) -> str:
    return "" if tix is None else f"{tix:.1f} tix"


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
    the pill color), the card box (overlay-logical px) it belongs to, and an
    optional MTGO ticket price ``tix`` drawn as a second pill below the win rate.
    ``tix is None`` (below threshold / prices off) draws no price pill."""

    gih_wr: float | None
    tier: float | None
    x: int
    y: int
    w: int
    h: int
    tix: float | None = None


# --- shared geometry + painting --------------------------------------------
# Module-level so the live overlay and the offscreen preview tool
# (tools/preview_overlay.py) draw identical pills from the same code.


def font_for(style: OverlayStyle, card_h: int) -> QFont:
    font = QFont(style.font_family)
    font.setPixelSize(max(8, round(card_h * style.font_h_frac)))
    font.setBold(True)
    return font


def compute_label_rect(
    spec: LabelSpec, style: OverlayStyle, fm: QFontMetrics
) -> QRectF:
    pad_x = style.pad_x_frac * spec.w
    pad_y = style.pad_y_frac * spec.h
    bw = fm.horizontalAdvance(format_wr(spec.gih_wr)) + 2 * pad_x
    bh = fm.height() + 2 * pad_y
    inset = style.inset_x_frac * spec.w
    x = spec.x + spec.w - inset - bw
    y = spec.y + style.pill_bottom_frac * spec.h - bh
    # Clamp fully inside the card box.
    x = max(spec.x, min(x, spec.x + spec.w - bw))
    y = max(spec.y, min(y, spec.y + spec.h - bh))
    return QRectF(x, y, bw, bh)


def paint_label(
    painter: QPainter, rect: QRectF, spec: LabelSpec, style: OverlayStyle
) -> None:
    radius = rect.height() / 2
    painter.setFont(font_for(style, spec.h))
    # Soft drop shadow so the pill reads on bright art.
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(0, 0, 0, 90))
    painter.drawRoundedRect(
        rect.translated(0, max(1.0, rect.height() * 0.06)), radius, radius
    )
    # Percentile-colored pill with a thin dark rim.
    painter.setBrush(ramp_color(spec.tier, style.unknown_color))
    painter.setPen(QPen(QColor(0, 0, 0, 160), 1.2))
    painter.drawRoundedRect(rect, radius, radius)
    painter.setPen(QColor(style.fg))
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, format_wr(spec.gih_wr))


def price_font_for(style: OverlayStyle, card_h: int) -> QFont:
    font = QFont(style.font_family)
    font.setPixelSize(max(8, round(card_h * style.price_font_h_frac)))
    font.setBold(True)
    return font


def compute_price_rect(
    spec: LabelSpec, style: OverlayStyle, fm: QFontMetrics
) -> QRectF:
    """The price pill rect: right-aligned like the WR pill, its top edge a small
    gap below the WR pill's bottom, clamped fully inside the card box. ``fm`` is
    the *price* font's metrics."""
    wr_rect = compute_label_rect(spec, style, QFontMetrics(font_for(style, spec.h)))
    pad_x = style.pad_x_frac * spec.w
    pad_y = style.pad_y_frac * spec.h
    bw = fm.horizontalAdvance(format_tix(spec.tix)) + 2 * pad_x
    bh = fm.height() + 2 * pad_y
    inset = style.inset_x_frac * spec.w
    x = spec.x + spec.w - inset - bw
    y = wr_rect.bottom() + style.price_gap_frac * spec.h
    # Clamp fully inside the card box.
    x = max(spec.x, min(x, spec.x + spec.w - bw))
    y = max(spec.y, min(y, spec.y + spec.h - bh))
    return QRectF(x, y, bw, bh)


def paint_price(
    painter: QPainter, rect: QRectF, spec: LabelSpec, style: OverlayStyle
) -> None:
    radius = rect.height() / 2
    painter.setFont(price_font_for(style, spec.h))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(0, 0, 0, 90))
    painter.drawRoundedRect(
        rect.translated(0, max(1.0, rect.height() * 0.06)), radius, radius
    )
    # Fixed-color pill (distinct from the WR ramp) with a thin dark rim.
    painter.setBrush(QColor(style.price_color))
    painter.setPen(QPen(QColor(0, 0, 0, 160), 1.2))
    painter.drawRoundedRect(rect, radius, radius)
    painter.setPen(QColor(style.fg))
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, format_tix(spec.tix))


# 17Lands asks that tools building on their data keep a visible, top-level credit;
# Scryfall likewise asks for attribution wherever its card data is shown.
CITATION = "Win rates: 17Lands"
PRICE_CITATION = "Prices: Scryfall"


class OverlayWindow(QWidget):
    def __init__(self, style: OverlayStyle | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.style = style or OverlayStyle()
        self._labels: list[LabelSpec] = []
        self._notice: str | None = None

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

    def set_notice(self, text: str | None) -> None:
        """A caption shown in place of the citation — e.g. an embargo note when no
        pills are drawn. ``None`` restores the default 17Lands citation."""
        self._notice = text
        self.update()

    def caption_text(self) -> str | None:
        """The bottom-left caption for the current state: the embargo notice if
        set, else the standing 17Lands citation when pills are showing, with a
        Scryfall credit appended whenever any price pill is drawn."""
        if self._notice:
            return self._notice
        if not self._labels:
            return None
        if any(spec.tix is not None for spec in self._labels):
            return f"{CITATION} · {PRICE_CITATION}"
        return CITATION

    def label_rects(self) -> list[tuple[LabelSpec, QRect]]:
        """Computed pill rects for the current labels (no painting)."""
        return [(spec, self._compute_rect(spec).toRect()) for spec in self._labels]

    def price_rects(self) -> list[tuple[LabelSpec, QRect]]:
        """Computed price-pill rects for labels that carry a ``tix`` (no painting)."""
        return [
            (spec, self._compute_price_rect(spec).toRect())
            for spec in self._labels
            if spec.tix is not None
        ]

    # --- geometry ------------------------------------------------------------

    def _font_for(self, card_h: int) -> QFont:
        return font_for(self.style, card_h)

    def _compute_rect(self, spec: LabelSpec) -> QRectF:
        return compute_label_rect(spec, self.style, QFontMetrics(self._font_for(spec.h)))

    def _compute_price_rect(self, spec: LabelSpec) -> QRectF:
        fm = QFontMetrics(price_font_for(self.style, spec.h))
        return compute_price_rect(spec, self.style, fm)

    # --- painting ------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        for spec in self._labels:
            paint_label(painter, self._compute_rect(spec), spec, self.style)
            if spec.tix is not None:
                paint_price(painter, self._compute_price_rect(spec), spec, self.style)
        caption = self.caption_text()
        if caption:
            self._paint_caption(painter, caption)
        painter.end()

    def _paint_caption(self, painter: QPainter, text: str) -> None:
        font = QFont(self.style.font_family)
        font.setPixelSize(max(11, round(self.height() * 0.018)))
        painter.setFont(font)
        fm = QFontMetrics(font)
        pad = fm.height() * 0.4
        margin = round(self.height() * 0.012) + 4
        bw = fm.horizontalAdvance(text) + 2 * pad
        bh = fm.height() + 2 * pad
        rect = QRectF(margin, self.height() - margin - bh, bw, bh)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 140))
        painter.drawRoundedRect(rect, bh / 4, bh / 4)
        painter.setPen(QColor(self.style.fg))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    # --- Win32 click-through (Windows only) ----------------------------------

    def showEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().showEvent(event)
        if win32.IS_WINDOWS:
            try:
                win32.set_click_through(int(self.winId()))
            except OSError:
                pass
