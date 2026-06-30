"""WSL dev preview: screenshot -> recognition -> annotated PNG (no display).

Two modes:

  # Region only — works NOW, no Scryfall needed. Draws detected slots
  # (green=real, yellow=synthesized) so you can eyeball detect_slots on a real
  # MTGO screenshot.
  uv run python tools/annotate_preview.py shot.png --expected 15 --boxes-only

  # Full — region + identification. Needs the owner's scryfall_art stubs
  # implemented (or a warmed cache), plus the pack name list.
  uv run python tools/annotate_preview.py shot.png --expansion MH3 \
      --names "Fanged Flames" "Drowner of Truth" ... --out annotated.png
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mtgo_overlay.recognition.config import RecognitionConfig  # noqa: E402
from mtgo_overlay.recognition.pipeline import locate_cards  # noqa: E402
from mtgo_overlay.recognition.region import detect_slots  # noqa: E402

GREEN = (0, 255, 0)
YELLOW = (0, 255, 255)
RED = (0, 0, 255)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("screenshot", type=Path)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--expansion", default="")
    ap.add_argument("--names", nargs="*", default=[])
    ap.add_argument("--expected", type=int, default=0, help="expected card count")
    ap.add_argument("--boxes-only", action="store_true")
    args = ap.parse_args()

    screen = cv2.imread(str(args.screenshot))
    if screen is None:
        ap.error(f"Could not read screenshot: {args.screenshot}")
    out_path = args.out or args.screenshot.with_name(
        args.screenshot.stem + "_annotated.png"
    )
    cfg = RecognitionConfig()
    canvas = screen.copy()

    if args.boxes_only or not args.names:
        slots = detect_slots(screen, cfg, args.expected or len(args.names))
        for s in slots:
            color = YELLOW if s.synthetic else GREEN
            b = s.bbox
            cv2.rectangle(canvas, (b.x, b.y), (b.x2, b.y2), color, 2)
            cv2.putText(canvas, f"r{s.row}c{s.col}", (b.x + 3, b.y + 18),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
        print(f"Detected {len(slots)} slots "
              f"({sum(s.synthetic for s in slots)} synthesized).")
    else:
        located = locate_cards(screen, args.names, args.expansion, cfg)
        for loc in located:
            b = loc.bbox
            cv2.rectangle(canvas, (b.x, b.y), (b.x2, b.y2), GREEN, 2)
            cv2.putText(canvas, f"{loc.name} {loc.score:.2f}", (b.x + 3, b.y + 18),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, RED, 1, cv2.LINE_AA)
        print(f"Located {len(located)} / {len(args.names)} cards.")

    cv2.imwrite(str(out_path), canvas)
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
