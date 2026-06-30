"""Overlay: label geometry / render (offscreen) + window tracker state machine."""

from __future__ import annotations

from mtgo_overlay.config.settings import OverlayStyle
from mtgo_overlay.overlay.overlay_window import (
    LabelSpec,
    OverlayWindow,
    percentile_rank,
    ramp_color,
)
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
    LabelSpec(75.7, 0.95, 100, 100, 120, 168),
    LabelSpec(62.1, 0.60, 300, 100, 120, 168),
    LabelSpec(None, None, 100, 320, 120, 168),
]


def test_labels_lie_within_their_card_boxes(qapp):
    win = OverlayWindow(OverlayStyle())
    win.resize(600, 600)
    win.set_labels(CARDS)

    rects = win.label_rects()
    assert len(rects) == 3
    for spec, rect in rects:
        box = (spec.x, spec.y, spec.w, spec.h)
        assert _contains(box, rect), f"label for {spec.gih_wr} escaped its card box"
        # On-screen (inside the widget).
        assert 0 <= rect.x() and rect.x() + rect.width() <= win.width()
        assert 0 <= rect.y() and rect.y() + rect.height() <= win.height()


def test_labels_anchored_bottom_right_of_art(qapp):
    win = OverlayWindow(OverlayStyle())
    win.set_labels([CARDS[0]])
    _, rect = win.label_rects()[0]
    spec = CARDS[0]
    inset = spec.w * win.style.inset_x_frac
    right_edge = spec.x + spec.w
    # Right edge hugs the card's right edge (minus the inset); QRect.right() is
    # the last pixel (x + width - 1).
    assert right_edge - inset - 3 <= rect.right() <= right_edge
    # Sits at the bottom of the art (well below the title bar), bottom edge at the
    # configured fraction of card height.
    assert spec.h * 0.11 <= rect.top() - spec.y
    assert (
        abs((rect.bottom() + 1) - (spec.y + spec.h * win.style.pill_bottom_frac)) <= 2
    )


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


# --- percentile coloring (pure) --------------------------------------------


def test_percentile_rank_orders_and_guards_small_samples():
    dist = sorted(float(v) for v in range(50, 70))  # 20 values, 50..69
    assert percentile_rank(50.0, dist) < 0.1  # bottom
    assert percentile_rank(69.0, dist) > 0.9  # top
    assert percentile_rank(59.5, dist) == 0.5  # middle
    # Too few data points to rank meaningfully -> None (neutral pill).
    assert percentile_rank(55.0, [55.0, 56.0]) is None


def test_ramp_color_spans_red_to_green():
    from PySide6.QtGui import QColor

    low = ramp_color(0.0)
    high = ramp_color(1.0)
    assert low.red() > low.green()  # poor -> reddish
    assert high.green() > high.red()  # great -> greenish
    assert ramp_color(None) == QColor("#6b7280")  # unrated -> neutral gray


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


def test_tracker_focus_follows_foreground(qapp):
    state = {"hwnd": 1, "rect": (0, 0, 800, 600), "fg": 0}
    tracker = WindowTracker(
        find_hwnd=lambda: state["hwnd"],
        get_rect=lambda _h: state["rect"],
        get_foreground=lambda: state["fg"],
    )
    focus: list[bool] = []
    tracker.focusChanged.connect(focus.append)

    # MTGO present but not the foreground window -> hidden.
    tracker.poll()
    assert focus[-1] is False

    # MTGO becomes foreground -> shown (rect unchanged, so focus must still flip).
    state["fg"] = 1
    tracker.poll()
    assert focus[-1] is True

    # Alt-tab away -> hidden again, and no redundant emits for an unchanged state.
    state["fg"] = 99
    tracker.poll()
    tracker.poll()
    assert focus[-1] is False
    assert focus == [False, True, False]

    # Window disappears -> also reported unfocused.
    state["hwnd"] = None
    focus.clear()
    tracker.poll()
    assert focus == []  # already False; no redundant emit
