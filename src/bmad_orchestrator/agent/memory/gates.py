"""Hard gates — agent физически не может перейти к следующей wave если retro
предыдущей не сделан (spec §6.1 «Hard gate semantics»).

Artifact convention:
- Wave retro:  `<memory>/per-wave/<wave>-retrospective.md`
- Epic deep:   `<memory>/retrospectives/epic-<N>-deep.md`
- Phase 5:     `<memory>/retrospectives/phase-5-final.md`

`memory_dir()` указывает в orchestrator_home/.claude/memory (settings).
"""

from __future__ import annotations

from pathlib import Path

from bmad_orchestrator.agent.memory.levels import has_valid_retro_schema
from bmad_orchestrator.agent.memory.schedule import (
    MANDATORY_RETROS,
    WAVE_SEQUENCE,
    RetroId,
    RetroLevel,
)
from bmad_orchestrator.agent.tools._common import memory_dir


class HardGateError(Exception):
    """Raised on attempted wave promotion when prior retro missing."""


def retro_artifact_path(retro_id: RetroId) -> Path:
    root = memory_dir()
    if retro_id.level is RetroLevel.WAVE:
        return root / "per-wave" / f"{retro_id.key}-retrospective.md"
    if retro_id.level is RetroLevel.EPIC:
        return root / "retrospectives" / f"epic-{retro_id.key}-deep.md"
    return root / "retrospectives" / "phase-5-final.md"


def is_retro_done(retro_id: RetroId) -> bool:
    """True if the artifact file для данного retro contains a complete retro.

    FS3 H11: tightened from `st_size > 0` to a content-schema check that
    requires:
      - YAML frontmatter declaring at least wave / level / created
      - a body of >= RETRO_MIN_BODY_CHARS chars after the frontmatter

    This rejects (a) accidental `touch` files (covered by old size check),
    (b) seed stubs (e.g. spawn_retro_worktree's "_TODO_" skeleton ≈ 100
    chars), (c) frontmatter-only files.
    """
    return has_valid_retro_schema(retro_artifact_path(retro_id))


def can_promote_wave(current: str, next_wave: str) -> None:
    """Raise HardGateError if `current` wave retro missing.

    Semantics:
    - `next_wave` обязан следовать за `current` в `WAVE_SEQUENCE`
      (порядок строго детерминирован).
    - Если retro для `current` отсутствует — promotion blocked.
    - First wave (0a) не имеет предшественника, переход «в неё»
      не модулируется этим gate'ом (но переход «из неё» — модулируется,
      ровно как для остальных).
    """
    if current not in WAVE_SEQUENCE:
        raise ValueError(f"unknown current wave: {current!r}")
    if next_wave not in WAVE_SEQUENCE:
        raise ValueError(f"unknown next wave: {next_wave!r}")

    cur_idx = WAVE_SEQUENCE.index(current)
    nxt_idx = WAVE_SEQUENCE.index(next_wave)
    if nxt_idx != cur_idx + 1:
        raise ValueError(
            f"next wave {next_wave!r} must immediately follow {current!r} in WAVE_SEQUENCE"
        )

    retro = RetroId(RetroLevel.WAVE, current)
    if not is_retro_done(retro):
        raise HardGateError(
            f"Wave {current!r} retrospective not done; cannot promote to {next_wave!r}. "
            f"Expected artifact: {retro_artifact_path(retro)}"
        )


def missing_retros() -> list[RetroId]:
    """List mandatory retros that don't yet have artifacts."""
    return [r for r in MANDATORY_RETROS if not is_retro_done(r)]


__all__ = [
    "HardGateError",
    "can_promote_wave",
    "is_retro_done",
    "missing_retros",
    "retro_artifact_path",
]
