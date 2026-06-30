"""identify.py — the threshold-free assignment core (synthetic, no real art)."""

from __future__ import annotations

import numpy as np

from mtgo_overlay.recognition import identify


def _patterns(k, rng):
    return [rng.integers(0, 256, size=(140, 100), dtype=np.uint8) for _ in range(k)]


def test_match_score_identical_is_one():
    rng = np.random.default_rng(0)
    img = rng.integers(0, 256, size=(140, 100), dtype=np.uint8)
    assert identify.match_score(img, img) > 0.999


def test_assignment_recovers_permutation():
    rng = np.random.default_rng(42)
    k = 8
    patterns = _patterns(k, rng)
    names = [f"card_{j}" for j in range(k)]
    templates = {name: [patterns[j]] for j, name in enumerate(names)}

    # Shuffle patterns into slots; assignment must recover the mapping.
    perm = list(rng.permutation(k))
    slot_images = [patterns[p] for p in perm]

    scores = identify.build_score_matrix(
        slot_images, names, lambda name: templates[name]
    )
    pairs = identify.assign(scores)

    recovered = {slot_i: name_j for slot_i, name_j, _ in pairs}
    for slot_i, original in enumerate(perm):
        assert recovered[slot_i] == original  # name index == pattern it holds


def test_min_affinity_drops_low_scores():
    scores = np.array([[0.92, 0.10], [0.10, 0.04]], dtype=np.float32)
    full = identify.assign(scores, min_affinity=-1.0)
    assert len(full) == 2
    floored = identify.assign(scores, min_affinity=0.2)
    assert [(i, j) for i, j, _ in floored] == [(0, 0)]


def test_assign_empty():
    assert identify.assign(np.empty((0, 0), dtype=np.float32)) == []


def test_rectangular_matrix_matches_min_dim():
    # More names than slots: 2 slots, 4 names -> 2 assignments.
    scores = np.array([[0.9, 0.1, 0.2, 0.1], [0.1, 0.1, 0.8, 0.2]], dtype=np.float32)
    pairs = identify.assign(scores)
    assert len(pairs) == 2
    assert {(i, j) for i, j, _ in pairs} == {(0, 0), (1, 2)}
