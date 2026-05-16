"""Mandatory retrospective schedule (spec §6.1).

9 обязательных retros across Phase 4 → Phase 5:
- 6 wave retros: 0a, 0b, 1a, 1b, 1c, 1d
- 2 epic-deep retros: Epic 1 (Platform Shell), Epic 7
- 1 phase-5 final retro

`WAVE_SEQUENCE` задаёт каноничный порядок — `gates.can_promote_wave(...)`
проверяет что retro предыдущей wave выполнен прежде чем разрешать
переход к следующей.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class RetroLevel(StrEnum):
    """Type of retrospective (spec §6.1)."""

    WAVE = "wave"
    EPIC = "epic-deep"
    PHASE = "phase-5"


@dataclass(slots=True, frozen=True)
class RetroId:
    """Stable identifier для одного из 9 mandatory retros.

    `key` интерпретируется по `level`:
    - WAVE: один из элементов `WAVE_SEQUENCE` (например `"1a"`)
    - EPIC: epic number as string (например `"1"`, `"7"`)
    - PHASE: `"5"` (единственный phase retro)
    """

    level: RetroLevel
    key: str

    @property
    def slug(self) -> str:
        if self.level is RetroLevel.WAVE:
            return f"wave-{self.key}"
        if self.level is RetroLevel.EPIC:
            return f"epic-{self.key}-deep"
        return "phase-5-final"


WAVE_SEQUENCE: Final[tuple[str, ...]] = ("0a", "0b", "1a", "1b", "1c", "1d")

MANDATORY_RETROS: Final[tuple[RetroId, ...]] = (
    *(RetroId(RetroLevel.WAVE, w) for w in WAVE_SEQUENCE),
    RetroId(RetroLevel.EPIC, "1"),
    RetroId(RetroLevel.EPIC, "7"),
    RetroId(RetroLevel.PHASE, "5"),
)

# Спека §6.1 фиксирует ровно 9 retros — assertion как single source of truth.
assert len(MANDATORY_RETROS) == 9, "spec §6.1 fixes 9 mandatory retros"


def previous_wave(current: str) -> str | None:
    """Return wave immediately preceding `current` in WAVE_SEQUENCE, or None
    if `current` is the first wave (0a)."""
    if current not in WAVE_SEQUENCE:
        raise ValueError(f"unknown wave: {current!r}")
    idx = WAVE_SEQUENCE.index(current)
    return WAVE_SEQUENCE[idx - 1] if idx > 0 else None


__all__ = [
    "MANDATORY_RETROS",
    "WAVE_SEQUENCE",
    "RetroId",
    "RetroLevel",
    "previous_wave",
]
