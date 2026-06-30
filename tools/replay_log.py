"""Replay a saved draft log into the watched folder to drive the state machine.

Creates a ``<username>-replay<EXP>.txt`` file in the watched folder and grows it
one pick-block at a time, so the running app sees draftStarted -> new pack ->
picked -> new pack ... end-to-end, with no MTGO. Pair with tools/fake_mtgo.py to
also see labels render.

  uv run python tools/replay_log.py tests/fixtures/logs/draft_sample.txt \
      "C:/Users/you/AppData/.../Logs" --username YourName --expansion MH3 --delay 1.5
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

HEADER_LINES = 12


def _split_blocks(lines: list[str]) -> tuple[list[str], list[list[str]]]:
    """Header + per-pick blocks (each block ends at a 'Picked:' line, or EOF)."""
    header, rest = lines[:HEADER_LINES], lines[HEADER_LINES:]
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in rest:
        current.append(line)
        if "Picked: " in line:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return header, blocks


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source_log", type=Path)
    ap.add_argument("dest_dir", type=Path)
    ap.add_argument("--username", required=True)
    ap.add_argument("--expansion", default="MH3")
    ap.add_argument("--delay", type=float, default=1.5, help="seconds between blocks")
    args = ap.parse_args()

    lines = args.source_log.read_text(encoding="utf-8").splitlines(keepends=True)
    header, blocks = _split_blocks(lines)

    args.dest_dir.mkdir(parents=True, exist_ok=True)
    dest = args.dest_dir / f"{args.username}-replay{args.expansion}.txt"

    # Create the file with the header (fires on_created -> draftStarted).
    dest.write_text("".join(header), encoding="utf-8")
    print(f"Created {dest}")
    time.sleep(args.delay)

    # Append each block (fires on_modified -> logModified).
    with dest.open("a", encoding="utf-8") as fh:
        for i, block in enumerate(blocks, 1):
            fh.write("".join(block))
            fh.flush()
            print(f"Appended block {i}/{len(blocks)}")
            time.sleep(args.delay)

    print("Replay complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
