"""Core geometry / result types for recognition."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BBox:
    x: int
    y: int
    w: int
    h: int

    @property
    def x2(self) -> int:
        return self.x + self.w

    @property
    def y2(self) -> int:
        return self.y + self.h

    @property
    def area(self) -> int:
        return self.w * self.h

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.w / 2.0, self.y + self.h / 2.0)

    def iou(self, other: "BBox") -> float:
        ix1, iy1 = max(self.x, other.x), max(self.y, other.y)
        ix2, iy2 = min(self.x2, other.x2), min(self.y2, other.y2)
        iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
        inter = iw * ih
        union = self.area + other.area - inter
        return inter / union if union > 0 else 0.0

    def as_tuple(self) -> tuple[int, int, int, int]:
        return (self.x, self.y, self.w, self.h)


@dataclass(frozen=True)
class Slot:
    """A detected (or synthesized) card position in the grid."""

    bbox: BBox
    row: int
    col: int
    synthetic: bool = False  # synthesized to fill an occluded grid cell


@dataclass(frozen=True)
class CardLocation:
    """A card name resolved to a screen position with a match score.

    ``printing_id`` is the Scryfall id of the specific printing whose artwork
    won the match, so the overlay can price the right in-set version.
    """

    name: str
    bbox: BBox
    score: float
    printing_id: str | None = None
