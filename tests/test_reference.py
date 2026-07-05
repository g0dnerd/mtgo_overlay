"""reference.py template prep - testable with fixture PNGs, no Scryfall call."""

from __future__ import annotations

import pytest

from mtgo_overlay.recognition import reference


def test_prepare_grayscales_and_resizes(fixtures_dir):
    img = reference.load_template_image(fixtures_dir / "art" / "art_a.png")
    out = reference.prepare(img, (100, 140))
    assert out.shape == (140, 100)  # cv2 size is (w, h) -> array (h, w)
    assert out.ndim == 2
    assert str(out.dtype) == "uint8"


def test_prepare_gradient_mode(fixtures_dir):
    img = reference.load_template_image(fixtures_dir / "art" / "art_b.png")
    out = reference.prepare(img, (100, 140), mode="gradient")
    assert out.shape == (140, 100)
    assert str(out.dtype) == "uint8"


def test_templates_from_paths(fixtures_dir):
    paths = tuple((fixtures_dir / "art").glob("*.png"))
    assert len(paths) >= 3
    templates = reference.templates_from_paths(paths, (100, 140))
    assert len(templates) == len(paths)
    assert all(t.shape == (140, 100) for t in templates)


def test_load_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        reference.load_template_image(tmp_path / "nope.png")


def test_reference_templates_uses_scryfall_contract(
    fixtures_dir, tmp_path, monkeypatch
):
    # Wire the consumer to the Scryfall layer without any network: stub the
    # enumeration + fetch to point at a fixture PNG.
    from mtgo_overlay.recognition import scryfall_art

    art = fixtures_dir / "art" / "art_a.png"
    monkeypatch.setattr(
        scryfall_art,
        "booster_artwork_ids",
        lambda exp, name, **kw: [scryfall_art.ArtRef("id1", "http://x/a.png", name)],
    )
    monkeypatch.setattr(scryfall_art, "fetch_artwork", lambda ref, cache_dir: art)

    reference.reference_templates.cache_clear()
    templates = reference.reference_templates(
        "MH3", "Fanged Flames", (100, 140), cache_dir=tmp_path
    )
    reference.reference_templates.cache_clear()

    assert len(templates) == 1
    assert templates[0].shape == (140, 100)
