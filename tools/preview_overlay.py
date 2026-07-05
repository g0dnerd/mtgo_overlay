"""WSL dev preview: run the *full* production chain on a fixture screenshot and
render the exact pills the live overlay would draw onto a copy of the image.

This wires `locate_cards` -> `RatingsRepository.lookup` -> `build_label_specs`
(the same join the app uses) and paints with the overlay's shared painter
(`compute_label_rect` + `paint_label`), so the preview matches the live overlay
and stays matched - no constants are duplicated here.

    uv run python tools/preview_overlay.py tests/fixtures/msh/pack1_pick1.png \
        --expansion MSH --csv tests/fixtures/ratings/sample_card_ratings.csv

Card names come from the fixture's sibling `<stem>.json` (`cards[].name`), the
same schema as tests/fixtures/msh/pack1_pick1.json - `locate_cards` is closed-set
assignment and needs the pack's names. Output defaults to the git-ignored
`label_previews/<stem>_overlay.png`.

Note: the offscreen Qt platform may pick a different default font than Windows,
so pill widths can differ slightly from a live Windows overlay. Acceptable for a
dev preview.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QFontMetrics, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mtgo_overlay.app import build_label_specs  # noqa: E402
from mtgo_overlay.config.settings import OverlayStyle  # noqa: E402
from mtgo_overlay.data.ratings_repo import RatingsRepository  # noqa: E402
from mtgo_overlay.overlay.overlay_window import (  # noqa: E402
    compute_label_rect,
    font_for,
    paint_label,
)
from mtgo_overlay.recognition import scryfall_art  # noqa: E402
from mtgo_overlay.recognition.config import RecognitionConfig  # noqa: E402
from mtgo_overlay.recognition.pipeline import locate_cards  # noqa: E402
from mtgo_overlay.system import logging_setup, paths  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def _load_meta(image: Path, names_json: Path | None) -> dict:
    sidecar = names_json or image.with_suffix(".json")
    if not sidecar.exists():
        raise SystemExit(
            f"Missing card-names sidecar: {sidecar}. The tool needs the pack's "
            f"card names (cards[].name) - see tests/fixtures/msh/pack1_pick1.json."
        )
    return json.loads(sidecar.read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("image", type=Path)
    ap.add_argument("--expansion", default="", help="default: sidecar JSON's expansion")
    ap.add_argument("--csv", type=Path, required=True, help="17lands card_ratings CSV")
    ap.add_argument("--format", dest="fmt", default="PremierDraft")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument(
        "--names-json", type=Path, default=None, help="override sidecar path"
    )
    args = ap.parse_args()

    logging_setup.setup(to_file=False)  # honors MTGO_OVERLAY_DEBUG for stage logs

    screen = cv2.imread(str(args.image))
    if screen is None:
        ap.error(f"Could not read image: {args.image}")

    meta = _load_meta(args.image, args.names_json)
    names = [c["name"] for c in meta.get("cards", [])]
    if not names:
        ap.error("Sidecar JSON has no cards[].name entries.")
    expansion = (args.expansion or meta.get("expansion", "")).upper()
    if not expansion:
        ap.error("No --expansion given and none in the sidecar JSON.")
    out_path = args.out or ROOT / "label_previews" / f"{args.image.stem}_overlay.png"

    QApplication(sys.argv)

    scryfall_art.ensure_set_artwork(expansion, names, paths.scryfall_cache_dir())

    cfg = RecognitionConfig()
    located = locate_cards(
        screen, names, expansion, cfg, cache_dir=paths.scryfall_cache_dir()
    )

    repo = RatingsRepository(paths.ratings_cache_dir(), client=None)
    repo.ensure(expansion, args.fmt, use_live=False, csv_path=args.csv)
    ratings = repo.lookup(expansion, args.fmt, names)
    distribution = repo.distribution(expansion, args.fmt)

    # A static screenshot needs no logical scaling, so boxes are image pixels.
    specs = build_label_specs(located, ratings, dpr=1.0, distribution=distribution)

    style = OverlayStyle()
    img = QImage(str(args.image))
    if img.isNull():
        ap.error(f"Qt could not load image: {args.image}")
    painter = QPainter(img)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    for spec in specs:
        rect = compute_label_rect(spec, style, QFontMetrics(font_for(style, spec.h)))
        paint_label(painter, rect, spec, style)
    painter.end()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(out_path))
    rated = sum(s.gih_wr is not None for s in specs)
    print(
        f"Wrote {out_path} - located {len(located)}/{len(names)} card(s), "
        f"{len(specs)} pill(s) ({rated} rated, set distribution n={len(distribution)})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
