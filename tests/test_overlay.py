"""Overlay: label geometry / render (offscreen) + window tracker state machine."""

from __future__ import annotations

from mtgo_overlay.config.settings import OverlayStyle
from mtgo_overlay.overlay.overlay_window import LabelSpec, OverlayWindow
from mtgo_overlay.overlay.window_tracker import WindowTracker


def _contains(box, rect) -> bool:
    bx, by, bw, bh = box
    return (
        rect.x() >= bx
        and rect.y() >= by
        and rect.x() + rect.width() <= bx + bw
        and rect.y() + rect.height() <= by + bh
    )


CARDS = [
    LabelSpec("GIH 75.7", 100, 100, 120, 168),
    LabelSpec("GIH 62.1", 300, 100, 120, 168),
    LabelSpec("GIH N/A", 100, 320, 120, 168),
]


def test_labels_lie_within_their_card_boxes(qapp):
    win = OverlayWindow(OverlayStyle())
    win.resize(600, 600)
    win.set_labels(CARDS)

    rects = win.label_rects()
    assert len(rects) == 3
    for spec, rect in rects:
        box = (spec.x, spec.y, spec.w, spec.h)
        assert _contains(box, rect), f"{spec.text} escaped its card box"
        # On-screen (inside the widget).
        assert 0 <= rect.x() and rect.x() + rect.width() <= win.width()
        assert 0 <= rect.y() and rect.y() + rect.height() <= win.height()


def test_labels_anchored_top_right(qapp):
    win = OverlayWindow(OverlayStyle())
    win.set_labels([CARDS[0]])
    _, rect = win.label_rects()[0]
    spec = CARDS[0]
    inset = spec.w * win.style.inset_x_frac
    right_edge = spec.x + spec.w
    # Right edge hugs the card's right edge (minus the inset); QRect.right() is
    # the last pixel (x + width - 1).
    assert right_edge - inset - 3 <= rect.right() <= right_edge
    # Top near the card's top (within the configured fraction).
    assert spec.y <= rect.top() <= spec.y + spec.h * win.style.top_y_frac + 2


def test_labels_do_not_overlap(qapp):
    win = OverlayWindow(OverlayStyle())
    win.set_labels(CARDS)
    rects = [r for _, r in win.label_rects()]
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            assert not rects[i].intersects(rects[j])


def test_render_to_image_draws_pixels(qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage

    win = OverlayWindow(OverlayStyle())
    win.resize(600, 600)
    win.set_labels(CARDS)

    img = QImage(600, 600, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    win.render(img)

    # Some non-transparent pixels were drawn, and they fall inside a card box.
    spec, rect = win.label_rects()[0]
    painted = sum(
        img.pixelColor(x, y).alpha() > 0
        for x in range(rect.x(), rect.x() + rect.width())
        for y in range(rect.y(), rect.y() + rect.height())
    )
    assert painted > 0


# --- window tracker (injected hwnd/rect; no Win32 needed) ------------------

def test_tracker_emits_resized_then_moved_then_lost(qapp):
    state = {"hwnd": None, "rect": (0, 0, 0, 0)}
    tracker = WindowTracker(
        find_hwnd=lambda: state["hwnd"],
        get_rect=lambda _h: state["rect"],
    )
    events: list[tuple] = []
    tracker.resized.connect(lambda *a: events.append(("resized", a)))
    tracker.moved.connect(lambda *a: events.append(("moved", a)))
    tracker.lost.connect(lambda: events.append(("lost", ())))

    # No window yet -> nothing.
    tracker.poll()
    assert events == []

    # Window appears -> resized (treat as first layout).
    state["hwnd"], state["rect"] = 1, (10, 20, 800, 600)
    tracker.poll()
    assert events[-1] == ("resized", (10, 20, 800, 600))

    # Same rect -> nothing new.
    tracker.poll()
    assert len(events) == 1

    # Moved only -> moved.
    state["rect"] = (50, 60, 800, 600)
    tracker.poll()
    assert events[-1] == ("moved", (50, 60, 800, 600))

    # Resized -> resized.
    state["rect"] = (50, 60, 1000, 700)
    tracker.poll()
    assert events[-1] == ("resized", (50, 60, 1000, 700))

    # Window gone -> lost.
    state["hwnd"] = None
    tracker.poll()
    assert events[-1] == ("lost", ())
