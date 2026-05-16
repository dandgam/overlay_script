"""Three-level learning writers (spec §6).

- **Tactical** (per story): tokens / cost / time / elicitations breakdown,
  пишется по завершении story; consumed by reflexion-learner для wave-level
  aggregation.
- **Strategic** (per wave): рейкап patterns + optimal parallelism +
  cost distribution; формируется retrospective-writer'ом, перевариваясь
  через compress_wave_lessons на per-wave/<wave>.md.
- **Architectural** (per phase): cross-cutting concerns, security review
  categories, типы stories требующие human review; пишется на phase boundary.

Все три уровня писать в `memory_dir()` под канонические подпапки. Reader API —
`agent/tools/memory.py.read_memory` (path-traversal protected).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from bmad_orchestrator.agent.tools._common import memory_dir, now_iso

# FS3 H11: minimum body length (chars beyond frontmatter) for a retro to be
# considered "done" by gates.can_promote_wave. Empty/seed files (e.g. the
# mock spawn_retro_worktree skeleton ≈ 100 chars) MUST fail this check.
RETRO_MIN_BODY_CHARS = 200
_REQUIRED_FRONTMATTER_KEYS_WAVE = ("wave", "level", "created")


@dataclass(slots=True)
class TacticalLesson:
    """Per-story snapshot (spec §6 «Тактика»)."""

    story_id: str
    tokens_used: int
    cost_usd: float
    duration_seconds: int
    elicitations_auto: int = 0
    elicitations_escalated: int = 0
    notes: str = ""

    def render(self) -> str:
        return (
            f"---\nstory: {self.story_id}\nlevel: tactical\ncreated: {now_iso()}\n"
            f"tokens: {self.tokens_used}\ncost_usd: {self.cost_usd}\n"
            f"duration_s: {self.duration_seconds}\n"
            f"elicitations_auto: {self.elicitations_auto}\n"
            f"elicitations_escalated: {self.elicitations_escalated}\n---\n\n"
            f"# Lesson — {self.story_id}\n\n{self.notes or '_no notes_'}\n"
        )


@dataclass(slots=True)
class StrategicLesson:
    """Per-wave summary (spec §6 «Стратегия»)."""

    wave: str
    patterns: list[str] = field(default_factory=list)
    optimal_parallelism: int = 1
    cost_distribution: str = ""
    notes: str = ""

    def render(self) -> str:
        patterns_block = "\n".join(f"- {p}" for p in self.patterns) or "_none_"
        return (
            f"---\nwave: {self.wave}\nlevel: strategic\ncreated: {now_iso()}\n"
            f"optimal_parallelism: {self.optimal_parallelism}\n---\n\n"
            f"# Wave {self.wave} strategy lessons\n\n"
            f"## Patterns\n{patterns_block}\n\n"
            f"## Cost distribution\n{self.cost_distribution or '_unknown_'}\n\n"
            f"## Notes\n{self.notes or '_none_'}\n"
        )


@dataclass(slots=True)
class ArchitecturalLesson:
    """Per-phase pattern roll-up (spec §6 «Архитектура»)."""

    phase: str
    cross_cutting_concerns: list[str] = field(default_factory=list)
    security_review_categories: list[str] = field(default_factory=list)
    notes: str = ""

    def render(self) -> str:
        concerns = (
            "\n".join(f"- {c}" for c in self.cross_cutting_concerns) or "_none_"
        )
        sec = (
            "\n".join(f"- {s}" for s in self.security_review_categories) or "_none_"
        )
        return (
            f"---\nphase: {self.phase}\nlevel: architectural\ncreated: {now_iso()}\n---\n\n"
            f"# Phase {self.phase} architectural patterns\n\n"
            f"## Cross-cutting concerns\n{concerns}\n\n"
            f"## Security review categories\n{sec}\n\n"
            f"## Notes\n{self.notes or '_none_'}\n"
        )


def _write_exclusive(path: Path, content: str) -> None:
    """Open in O_EXCL mode — refuse to overwrite an existing file.

    FS3 H11: prevents accidental retro overwrite. Caller MUST handle
    FileExistsError or use append_retro_artifact() for legitimate appends.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as f:
        f.write(content)


def record_tactical_lesson(lesson: TacticalLesson) -> Path:
    out = memory_dir() / "per-story" / f"{lesson.story_id}.lesson.md"
    _write_exclusive(out, lesson.render())
    return out


def record_strategic_lesson(lesson: StrategicLesson) -> Path:
    out = memory_dir() / "per-wave" / f"{lesson.wave}.md"
    _write_exclusive(out, lesson.render())
    return out


def record_architectural_lesson(lesson: ArchitecturalLesson) -> Path:
    out = memory_dir() / "per-phase" / f"{lesson.phase}.md"
    _write_exclusive(out, lesson.render())
    return out


def append_retro_artifact(base_path: Path, content: str) -> Path:
    """Legitimate append channel for retro artifacts (FS3 H11).

    Writes to `<base_stem>.<UTC_timestamp_compact><suffix>` (sibling of `base_path`)
    so the original `base_path` is never overwritten. Returns the new path.

    Example:
        base_path = /memory/per-wave/1a-retrospective.md
        append   = /memory/per-wave/1a-retrospective.20260516T180000Z.md
    """
    base_path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    suffix = base_path.suffix or ".md"
    stem = base_path.name[: -len(suffix)] if suffix else base_path.name
    out = base_path.with_name(f"{stem}.{stamp}{suffix}")
    # Defensive: even append channel uses exclusive create (timestamps include
    # seconds; a second write within the same second is the bug we want to surface).
    _write_exclusive(out, content)
    return out


def has_valid_retro_schema(path: Path) -> bool:
    """True iff `path` contains a non-empty body (>= RETRO_MIN_BODY_CHARS)
    AND its YAML frontmatter declares wave/level/created.

    Pure check — doesn't raise. Used by gates.is_retro_done.
    """
    if not path.exists():
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    if not text.startswith("---"):
        return False
    end = text.find("\n---", 3)
    if end == -1:
        return False
    frontmatter_raw = text[3:end]
    body = text[end + 4 :].strip()
    if len(body) < RETRO_MIN_BODY_CHARS:
        return False
    keys_found = {
        line.split(":", 1)[0].strip()
        for line in frontmatter_raw.splitlines()
        if ":" in line
    }
    return all(k in keys_found for k in _REQUIRED_FRONTMATTER_KEYS_WAVE)


__all__ = [
    "RETRO_MIN_BODY_CHARS",
    "ArchitecturalLesson",
    "StrategicLesson",
    "TacticalLesson",
    "append_retro_artifact",
    "has_valid_retro_schema",
    "record_architectural_lesson",
    "record_strategic_lesson",
    "record_tactical_lesson",
]
