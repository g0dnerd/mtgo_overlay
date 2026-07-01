"""Draft log: parser state machine + path helpers + watcher signal wiring."""

from __future__ import annotations

from mtgo_overlay.draft.log_parser import (
    Log,
    get_current_log,
    infer_mtgo_username,
    is_valid_draft,
)

HEADER = [
    "Event #:      123456789",
    "Time:    20240601120000",
    "Players:",
    "    TestUser",
    "    Opp1",
    "    Opp2",
    "    Opp3",
    "    Opp4",
    "    Opp5",
    "    Opp6",
    "    Opp7",
    "",
]
assert len(HEADER) == 12


def build_log(blocks) -> str:
    """Render (pick_no, cards, picked_or_None) blocks in MTGO-log shape."""
    lines = list(HEADER)
    for pick_no, cards, picked in blocks:
        lines.append(f"Pack 1 pick {pick_no}:")
        for card in cards:
            lines.append(f"    --> {card}" if card == picked else f"    {card}")
        if picked is not None:
            lines.append(f"Picked: {picked}")
        lines.append("")
    return "\n".join(lines) + "\n"


PACK1 = ["Aerie Auxiliary", "Fanged Flames", "Expanding Ooze"]
PACK2 = ["Drowner of Truth", "Refurbished Familiar", "Dog Umbra"]
PACK3 = ["Estrid's Invocation", "Muster the Departed", "Gift of the Viper"]


def write(tmp_path, text):
    p = tmp_path / "TestUser-draft-mh3.txt"
    p.write_text(text, encoding="utf-8")
    return p


def test_entry_point_lands_on_current_pack(tmp_path):
    text = build_log([
        (1, PACK1, "Fanged Flames"),
        (2, PACK2, "Refurbished Familiar"),
        (3, PACK3, None),  # current, unpicked
    ])
    log = Log(write(tmp_path, text))
    assert log.picks == ["Fanged Flames", "Refurbished Familiar"]
    assert log.current_pack == PACK3


def test_pick_then_new_then_nothing(tmp_path):
    path = write(tmp_path, build_log([
        (1, PACK1, "Fanged Flames"),
        (2, PACK2, None),  # current, unpicked
    ]))
    log = Log(path)
    assert log.picks == ["Fanged Flames"]
    assert log.current_pack == PACK2

    # No change yet.
    assert log.check_for_update() == "nothing"

    # User picks from pack 2 and pack 3 appears.
    path.write_text(build_log([
        (1, PACK1, "Fanged Flames"),
        (2, PACK2, "Refurbished Familiar"),
        (3, PACK3, None),
    ]), encoding="utf-8")
    assert log.check_for_update() == "picked"
    assert log.picks == ["Fanged Flames", "Refurbished Familiar"]
    assert log.check_for_update() == "new"
    assert log.current_pack == PACK3
    assert log.check_for_update() == "nothing"


def test_static_fixture(fixtures_dir):
    log = Log(fixtures_dir / "logs" / "draft_sample.txt")
    assert log.picks == ["Fanged Flames", "Refurbished Familiar"]
    assert log.current_pack == [
        "Estrid's Invocation",
        "Golden-Tail Trainer",
        "Muster the Departed",
        "Gift of the Viper",
        "Island",
    ]


def test_parses_real_mtgo_draft_log(fixtures_dir):
    # A full 42-pick Marvel Super Heroes draft captured from real MTGO.
    log = Log(fixtures_dir / "logs" / "draft_real_msh.txt")
    assert len(log.picks) == 42
    assert log.picks[0] == "Avengers: Under Siege"
    assert log.picks[-1] == "Swamp"
    assert log.current_pack == ["Swamp"]


def test_get_current_log_picks_newest(tmp_path):
    import os
    import time

    older = tmp_path / "TestUser-1.txt"
    older.write_text("x", encoding="utf-8")
    time.sleep(0.01)
    newer = tmp_path / "TestUser-2.txt"
    newer.write_text("y", encoding="utf-8")
    # Unrelated file for another user must be ignored.
    (tmp_path / "Someone-3.txt").write_text("z", encoding="utf-8")
    os.utime(newer, (time.time() + 5, time.time() + 5))

    assert get_current_log(tmp_path, "TestUser") == str(newer)


def test_infer_username_from_log_filenames(tmp_path):
    (tmp_path / "pjk_-2026.6.30-10814-35390973-MSHMSHMSH.txt").write_text("x")
    assert infer_mtgo_username(tmp_path) == "pjk_"


def test_infer_username_prefers_most_common(tmp_path):
    (tmp_path / "pjk_-2026.6.30-10814-35390973-MSHMSHMSH.txt").write_text("x")
    (tmp_path / "pjk_-2026.6.29-10815-35390974-MSHMSHMSH.txt").write_text("x")
    (tmp_path / "guest-2026.6.28-10816-35390975-MSHMSHMSH.txt").write_text("x")
    assert infer_mtgo_username(tmp_path) == "pjk_"


def test_infer_username_empty_when_no_logs(tmp_path):
    (tmp_path / "notes.txt").write_text("x")  # doesn't match the log pattern
    assert infer_mtgo_username(tmp_path) == ""
    assert infer_mtgo_username(tmp_path / "missing") == ""


def test_is_valid_draft(tmp_path):
    good = tmp_path / "TestUser-draft.txt"
    assert is_valid_draft(good, tmp_path, "TestUser")
    assert not is_valid_draft(good, tmp_path, "OtherUser")
    assert not is_valid_draft(tmp_path / "sub" / "TestUser.txt", tmp_path, "TestUser")


def test_watcher_emits_signals(tmp_path, qapp):
    from mtgo_overlay.draft.log_watcher import DraftLogWatcher

    watcher = DraftLogWatcher(tmp_path, "TestUser")
    started: list[str] = []
    modified: list[str] = []
    watcher.draftStarted.connect(started.append)
    watcher.logModified.connect(modified.append)

    draft_file = tmp_path / "TestUser-draft-mh3.txt"
    draft_file.write_text("x", encoding="utf-8")

    # Unrelated file -> no signal.
    watcher.handle_created(str(tmp_path / "Other-draft.txt"))
    assert started == []

    watcher.handle_created(str(draft_file))
    assert started == [str(draft_file)]

    # Modification of a non-active file -> ignored.
    watcher.handle_modified(str(tmp_path / "TestUser-other.txt"))
    assert modified == []

    watcher.handle_modified(str(draft_file))
    assert modified == [str(draft_file)]


def test_watcher_adopts_existing_log_on_start(tmp_path, qapp):
    from mtgo_overlay.draft.log_watcher import DraftLogWatcher

    # A draft already in progress when the overlay launches: MTGO wrote a new
    # file per draft, so no on_created fires for it -> the startup scan adopts it.
    draft_file = tmp_path / "TestUser-draft-msh.txt"
    draft_file.write_text("x", encoding="utf-8")

    watcher = DraftLogWatcher(tmp_path, "TestUser")
    started: list[str] = []
    watcher.draftStarted.connect(started.append)

    watcher._adopt_existing_log()
    assert started == [str(draft_file)]
    assert watcher.active_log == str(draft_file)


def test_watcher_adopt_noop_when_no_existing_log(tmp_path, qapp):
    from mtgo_overlay.draft.log_watcher import DraftLogWatcher

    watcher = DraftLogWatcher(tmp_path, "TestUser")
    started: list[str] = []
    watcher.draftStarted.connect(started.append)

    watcher._adopt_existing_log()  # empty folder -> no candidates
    assert started == []
    assert watcher.active_log is None
