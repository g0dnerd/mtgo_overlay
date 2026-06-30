"""Single click-through overlay window that draws every label in one paintEvent.

Replaces the old N-`Toplevel`-windows hack. Receives labels already in
overlay-logical coordinates (the AppController maps capture-px -> logical), pins
to MTGO's client origin, and draws each GIH label anchored to the top-right of
its card box with fractional (not pixel) insets so it survives any size/DPI.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QWidget

from ..config.settings import OverlayStyle
from ..system import win32


@dataclass(frozen=True)
class LabelSpec:
    """A label and the card box (overlay-logical px) it belongs to."""

    text: str
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

    def font(self) -> QFont:
        return QFont(self.style.font_family, self.style.font_size_pt)

    def label_rects(self) -> list[tuple[LabelSpec, QRect]]:
        """Computed background rects for the current labels (no painting)."""
        fm = QFontMetrics(self.font())
        return [(spec, self._compute_rect(spec, fm)) for spec in self._labels]

    # --- geometry ------------------------------------------------------------

    def _compute_rect(self, spec: LabelSpec, fm: QFontMetrics) -> QRect:
        pad = self.style.padding_px
        inset_x = self.style.inset_x_frac * spec.w
        bg_w = min(fm.horizontalAdvance(spec.text) + 2 * pad, max(1, spec.w - 2 * inset_x))
        bg_h = min(fm.height() + 2 * pad, spec.h)
        right = spec.x + spec.w - inset_x
        x = int(right - bg_w)
        y = int(spec.y + self.style.top_y_frac * spec.h)
        # Clamp fully inside the card box.
        x = max(spec.x, min(x, spec.x + spec.w - int(bg_w)))
        y = max(spec.y, min(y, spec.y + spec.h - int(bg_h)))
        return QRect(x, y, int(bg_w), int(bg_h))

    # --- painting ------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setFont(self.font())
        fm = QFontMetrics(self.font())

        bg = QColor(self.style.bg)
        bg.setAlphaF(self.style.bg_opacity)
        fg = QColor(self.style.fg)

        for spec in self._labels:
            rect = self._compute_rect(spec, fm)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(bg)
            painter.drawRoundedRect(rect, 3, 3)
            painter.setPen(fg)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, spec.text)
        painter.end()

    # --- Win32 click-through (Windows only) ----------------------------------

    def showEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().showEvent(event)
        if win32.IS_WINDOWS:
            try:
                win32.set_click_through(int(self.winId()))
            except OSError:
                pass
