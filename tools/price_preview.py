"""WSL dev preview for the ticket-price pill: run the full production chain on a
fixture screenshot and render the win-rate pill *and* the price pill below it,
exactly as the live overlay would.

Wires ``locate_cards`` (which emits each card's matched Scryfall ``printing_id``,
joined to its ``mtgo_id`` for the Goatbots feed) -> ``RatingsRepository.lookup`` +
``PricesRepository.lookup`` -> ``build_label_specs`` (``show_prices=True``) and
paints with the overlay's shared painters
(``compute_label_rect``/``paint_label`` + ``compute_price_rect``/``paint_price``),
so the preview matches the live overlay and stays matched — no constants here.

    # real Goatbots prices for the matched printings (one price-feed download):
    uv run python tools/price_preview.py tests/fixtures/msh/pack1_pick1.png \
        --csv tests/fixtures/ratings/sample_card_ratings.csv

    # or eyeball the pill design offline with a synthetic price spread:
    uv run python tools/price_preview.py tests/fixtures/msh/pack1_pick1.png \
        --csv tests/fixtures/ratings/sample_card_ratings.csv --demo-prices

Card names come from the fixture's sibling ``<stem>.json`` (``cards[].name``).
Output defaults to the git-ignored ``label_previews/<stem>_prices.png``.

Note: the offscreen Qt platform may pick a different default font than Windows,
so pill widths can differ slightly from a live Windows overlay. Fine for a preview.
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
from mtgo_overlay.config.settings import OverlayStyle, Settings  # noqa: E402
from mtgo_overlay.data.prices_repo import CardPrice, PricesRepository  # noqa: E402
from mtgo_overlay.data.ratings_repo import RatingsRepository  # noqa: E402
from mtgo_overlay.overlay.overlay_window import (  # noqa: E402
    compute_label_rect,
    compute_price_rect,
    font_for,
    paint_label,
    paint_price,
    price_font_for,
)
from mtgo_overlay.recognition import scryfall_art  # noqa: E402
from mtgo_overlay.recognition.config import RecognitionConfig  # noqa: E402
from mtgo_overlay.recognition.pipeline import locate_cards  # noqa: E402
from mtgo_overlay.system import logging_setup, paths  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

# A spread that straddles the default 1.0-tix threshold, so --demo-prices shows
# both priced and unpriced cards. Cycled across the located cards by index.
_DEMO_TIX = [0.02, 2.11, 0.75, 5.4, 0.3, 1.2, 12.0, 0.9, 3.33, 0.05, 1.75, 8.6]


def _load_meta(image: Path, names_json: Path | None) -> dict:
    sidecar = names_json or image.with_suffix(".json")
    if not sidecar.exists():
        raise SystemExit(
            f"Missing card-names sidecar: {sidecar}. The tool needs the pack's "
            f"card names (cards[].name) — see tests/fixtures/msh/pack1_pick1.json."
        )
    return json.loads(sidecar.read_text(encoding="utf-8"))


def _demo_prices(located) -> list[CardPrice]:
    """A synthetic price per matched printing, cycling ``_DEMO_TIX`` — lets you see
    the pill design without a live price fetch."""
    return [
        CardPrice(loc.printing_id, _DEMO_TIX[i % len(_DEMO_TIX)])
        for i, loc in enumerate(located)
        if loc.printing_id is not None
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("image", type=Path)
    ap.add_argument("--expansion", default="", help="default: sidecar JSON's expansion")
    ap.add_argument("--csv", type=Path, required=True, help="17lands card_ratings CSV")
    ap.add_argument("--format", dest="fmt", default="PremierDraft")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--names-json", type=Path, default=None, help="override sidecar path")
    ap.add_argument(
        "--min-tix",
        type=float,
        default=Settings().price_min_tix,
        help="hide price pills below this many tix (default: settings' 1.0)",
    )
    ap.add_argument(
        "--demo-prices",
        action="store_true",
        help="synthesize a price spread instead of fetching real tix from Scryfall",
    )
    args = ap.parse_args()

    logging_setup.setup(to_file=False)

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
    out_path = args.out or ROOT / "label_previews" / f"{args.image.stem}_prices.png"

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

    if args.demo_prices:
        prices = _demo_prices(located)
    else:
        price_repo = PricesRepository(paths.prices_cache_dir())
        price_repo.ensure()
        sid_to_mtgo = scryfall_art.set_mtgo_ids(
            expansion, cache_dir=paths.scryfall_cache_dir()
        )
        printings = [
            (loc.printing_id, sid_to_mtgo.get(loc.printing_id))
            for loc in located
            if loc.printing_id
        ]
        prices = price_repo.lookup(printings)

    specs = build_label_specs(
        located,
        ratings,
        dpr=1.0,
        distribution=distribution,
        prices=prices,
        show_prices=True,
        price_min_tix=args.min_tix,
    )

    style = OverlayStyle()
    img = QImage(str(args.image))
    if img.isNull():
        ap.error(f"Qt could not load image: {args.image}")
    painter = QPainter(img)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    for spec in specs:
        wr_rect = compute_label_rect(spec, style, QFontMetrics(font_for(style, spec.h)))
        paint_label(painter, wr_rect, spec, style)
        if spec.tix is not None:
            price_rect = compute_price_rect(
                spec, style, QFontMetrics(price_font_for(style, spec.h))
            )
            paint_price(painter, price_rect, spec, style)
    painter.end()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(out_path))
    priced = sum(s.tix is not None for s in specs)
    print(
        f"Wrote {out_path} — located {len(located)}/{len(names)} card(s), "
        f"{len(specs)} pill(s), {priced} price pill(s) "
        f"({'demo' if args.demo_prices else 'live'} prices, min {args.min_tix} tix)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
