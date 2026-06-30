"""Set / format identifiers and the log-code -> expansion mapping.

MTGO draft-log filenames embed a 3-letter set code that, for almost every set,
equals the uppercase 17Lands / Scryfall expansion code (e.g. ``mh3`` -> ``MH3``).
The few divergences go in ``_LOG_CODE_OVERRIDES``.
"""

from __future__ import annotations

from enum import Enum


class Format(str, Enum):
    """17Lands ``format`` query values."""

    PREMIER_DRAFT = "PremierDraft"
    TRAD_DRAFT = "TradDraft"
    QUICK_DRAFT = "QuickDraft"
    SEALED = "Sealed"
    TRAD_SEALED = "TradSealed"


# Override only when MTGO's 3-letter log code differs from the 17Lands code.
_LOG_CODE_OVERRIDES: dict[str, str] = {}


def expansion_from_log_code(code: str) -> str:
    """Map a 3-letter MTGO log code to its 17Lands/Scryfall expansion code."""
    normalized = code.strip().lower()
    return _LOG_CODE_OVERRIDES.get(normalized, normalized.upper())


BASIC_LANDS = frozenset(
    {"Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes"}
)


def is_basic_land(name: str) -> bool:
    return name in BASIC_LANDS
