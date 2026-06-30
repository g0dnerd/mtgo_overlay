"""Bootstrap a ground-truth JSON from detection, for you to correct by hand.

Runs detect_slots on a screenshot and emits the eval.GroundTruth schema with
detected boxes and placeholder ``"?"`` names. Open the JSON, fix any boxes and
fill in the real names (top-left to bottom-right), and drop it next to the
screenshot as ``tests/fixtures/<set>/<case>.json``.

  uv run python tools/propose_groundtruth.py shot.png --expected 15 \
      --expansion MH3 --out tests/fixtures/mh3/pack1.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mtgo_overlay.recognition.config import RecognitionConfig  # noqa: E402
from mtgo_overlay.recognition.region import detect_slots  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("screenshot", type=Path)
    ap.add_argument("--expected", type=int, default=0)
    ap.add_argument("--expansion", default="")
    ap.add_argument("--format", dest="fmt", default="PremierDraft")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    screen = cv2.imread(str(args.screenshot))
    if screen is None:
        ap.error(f"Could not read screenshot: {args.screenshot}")

    slots = detect_slots(screen, RecognitionConfig(), args.expected)
    h, w = screen.shape[:2]
    payload = {
        "expansion": args.expansion,
        "format": args.fmt,
        "screen_size": [w, h],
        "cards": [
            {"name": "?", "bbox": list(s.bbox.as_tuple()), "synthetic": s.synthetic}
            for s in slots
        ],
    }
    out_path = args.out or args.screenshot.with_suffix(".gt.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(
        f"Wrote {len(slots)} proposed boxes to {out_path} - correct names/boxes by hand."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
