"""Internal skills registry — three-level progressive disclosure (spec §19).

Каждый skill живёт в `agent/skills/<name>/SKILL.md` с YAML frontmatter:

    ---
    name: <skill-name>
    description: <one-line description>
    ---

    # <skill body>

Level 1 — metadata (~100 tokens): name + description, always in system prompt
(cached). См. `iter_metadata()` / `metadata_block()`.

Level 2 — body: full SKILL.md, загружается через `load_body(name)` только когда
dispatcher решает, что skill активирован.

Level 3 — references: `agent/skills/<name>/references/*.md`, on-demand через
`load_reference(name, filename)`.

Dispatcher: `dispatch(event_type)` возвращает list имён skill'ов которые
интересуются данным `EventType`. Loop loop.py читает body выбранных skills.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from bmad_orchestrator.runtime.event_loop import EventType

SKILLS_DIR = Path(__file__).resolve().parent

FRONTMATTER_RE = re.compile(
    r"\A---\s*\n(?P<frontmatter>.*?)\n---\s*\n(?P<body>.*)\Z",
    re.DOTALL,
)
FIELD_RE = re.compile(r"^(?P<key>[a-zA-Z_][a-zA-Z0-9_-]*)\s*:\s*(?P<val>.*?)\s*$")

# Метаданные считаются «short» если ≤ MAX_METADATA_TOKENS_PER_SKILL токенов.
# Эвристика: 1 токен ≈ 4 символа. Spec §19: ~100 tokens / skill, ~1.5K на 12.
MAX_METADATA_TOKENS_PER_SKILL = 120
MAX_METADATA_TOKENS_TOTAL = 1800

# Skill name → событие(я) которые её активируют (spec §19, разделы
# «Когда активируется» в SKILL.md). Static map (а не парсинг markdown) —
# детерминированный routing без regex-эвристик.
TRIGGER_MAP: dict[str, frozenset[EventType]] = {
    "dag-planner": frozenset(
        {EventType.WAVE_BOUNDARY_REACHED, EventType.EPIC_BOUNDARY_REACHED}
    ),
    "worker-dispatcher": frozenset({EventType.SCHEDULED_WAKEUP}),
    # merge-gate is deprecated; kept in map so existing refs stay valid.
    "merge-gate": frozenset({EventType.WORKER_COMPLETED}),
    # Phase 4 hardening #5 — two-stage split.
    "merge-gate-spec": frozenset({EventType.WORKER_COMPLETED}),
    "merge-gate-quality": frozenset({EventType.MERGE_GATE_STAGE_COMPLETED}),
    "elicitation-router": frozenset({EventType.WORKER_ELICITATION}),
    "retrospective-writer": frozenset(
        {
            EventType.WAVE_BOUNDARY_REACHED,
            EventType.EPIC_BOUNDARY_REACHED,
            EventType.PHASE4_COMPLETE,
        }
    ),
    "intent-router": frozenset(
        {EventType.USER_CHAT_MESSAGE, EventType.VOICE_MESSAGE_RECEIVED}
    ),
    "cost-watchdog": frozenset({EventType.BUDGET_THRESHOLD_HIT}),
    "failure-analyst": frozenset(
        {EventType.WORKER_HALT_FILE, EventType.WORKER_COMPLETED}
    ),
    "reflexion-learner": frozenset(
        {EventType.WAVE_BOUNDARY_REACHED, EventType.PHASE4_COMPLETE}
    ),
    "wave-coordinator": frozenset(
        {
            EventType.WAVE_BOUNDARY_REACHED,
            EventType.EPIC_BOUNDARY_REACHED,
            EventType.PHASE4_COMPLETE,
        }
    ),
    "proactive-improver": frozenset(
        {
            EventType.WAVE_BOUNDARY_REACHED,
            EventType.EPIC_BOUNDARY_REACHED,
            EventType.PHASE4_COMPLETE,
            EventType.MONTHLY_REVIEW_SCHEDULED,
            EventType.USER_CHAT_MESSAGE,
        }
    ),
    "story-splitter": frozenset({EventType.STORY_SPLIT_TRIGGERED}),
}

EXPECTED_SKILL_NAMES: frozenset[str] = frozenset(TRIGGER_MAP.keys())


class SkillError(Exception):
    """Raised on malformed SKILL.md or unknown skill name."""


@dataclass(slots=True, frozen=True)
class SkillMetadata:
    """Frontmatter-only view of a skill. Always loaded into system prompt."""

    name: str
    description: str
    path: Path
    triggers: frozenset[EventType]

    @property
    def estimated_tokens(self) -> int:
        # name + ': ' + description + newline. ~4 chars / token.
        return max(1, (len(self.name) + len(self.description) + 4) // 4)


def _parse_frontmatter(text: str, path: Path) -> tuple[dict[str, str], str]:
    match = FRONTMATTER_RE.match(text)
    if not match:
        raise SkillError(f"{path}: missing YAML frontmatter (--- ... ---)")
    raw = match.group("frontmatter")
    body = match.group("body")
    fields: dict[str, str] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = FIELD_RE.match(line)
        if not m:
            raise SkillError(f"{path}: malformed frontmatter line: {line!r}")
        fields[m.group("key")] = m.group("val").strip().strip("\"'")
    return fields, body


@lru_cache(maxsize=1)
def iter_metadata() -> tuple[SkillMetadata, ...]:
    """Read frontmatter of every SKILL.md under `SKILLS_DIR`.

    Cached — система метаданных immutable между deploy'ями.
    """
    result: list[SkillMetadata] = []
    for skill_dir in sorted(SKILLS_DIR.iterdir()):
        if not skill_dir.is_dir() or skill_dir.name.startswith("_"):
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue
        text = skill_md.read_text(encoding="utf-8")
        fields, _body = _parse_frontmatter(text, skill_md)
        name = fields.get("name", "")
        description = fields.get("description", "")
        if not name or not description:
            raise SkillError(
                f"{skill_md}: frontmatter missing 'name' or 'description'"
            )
        if name != skill_dir.name:
            raise SkillError(
                f"{skill_md}: frontmatter name={name!r} does not match dir {skill_dir.name!r}"
            )
        triggers = TRIGGER_MAP.get(name, frozenset())
        result.append(
            SkillMetadata(
                name=name,
                description=description,
                path=skill_md,
                triggers=triggers,
            )
        )
    return tuple(result)


def metadata_block() -> str:
    """Render skill metadata for the cached system-prompt block.

    Total budget — `MAX_METADATA_TOKENS_TOTAL` (~1.5K tokens). Raises SkillError
    если превышен (защита от skill bloat).
    """
    metas = iter_metadata()
    lines = ["# Skill catalog (14 specialized internal skills, spec §19)"]
    total_tokens = sum(m.estimated_tokens for m in metas)
    if total_tokens > MAX_METADATA_TOKENS_TOTAL:
        raise SkillError(
            f"Skill metadata budget exceeded: {total_tokens} > "
            f"{MAX_METADATA_TOKENS_TOTAL} tokens. Shorten descriptions."
        )
    for m in metas:
        if m.estimated_tokens > MAX_METADATA_TOKENS_PER_SKILL:
            raise SkillError(
                f"Skill {m.name!r} metadata too long: "
                f"{m.estimated_tokens} > {MAX_METADATA_TOKENS_PER_SKILL} tokens"
            )
        trig = ",".join(sorted(t.value for t in m.triggers)) or "—"
        lines.append(f"- **{m.name}** — {m.description} (triggers: {trig})")
    return "\n".join(lines)


def dispatch(event_type: EventType) -> list[str]:
    """Return skill names registered for this event, in metadata order.

    Multiple skills may fire on the same event (e.g. WAVE_BOUNDARY_REACHED triggers
    dag-planner + retrospective-writer + reflexion-learner + wave-coordinator +
    proactive-improver). Loop loads bodies in returned order.
    """
    metas = iter_metadata()
    return [m.name for m in metas if event_type in m.triggers]


def load_body(name: str) -> str:
    """Read full SKILL.md body (Level 2). Frontmatter stripped.

    Raises SkillError if name unknown.
    """
    metas_by_name = {m.name: m for m in iter_metadata()}
    if name not in metas_by_name:
        raise SkillError(f"Unknown skill: {name!r}")
    text = metas_by_name[name].path.read_text(encoding="utf-8")
    _fields, body = _parse_frontmatter(text, metas_by_name[name].path)
    return body.strip()


def load_reference(name: str, filename: str) -> str:
    """Read on-demand reference file (Level 3).

    `name` — skill folder name. `filename` — file inside `references/`.
    Path traversal blocked: `..` and absolute paths refused.
    """
    if name not in EXPECTED_SKILL_NAMES:
        raise SkillError(f"Unknown skill: {name!r}")
    if "/" in filename or "\\" in filename or filename.startswith("."):
        raise SkillError(f"Invalid reference filename: {filename!r}")
    ref = (SKILLS_DIR / name / "references" / filename).resolve()
    root = (SKILLS_DIR / name / "references").resolve()
    if root not in ref.parents and ref != root:
        raise SkillError(f"Reference escapes skill dir: {filename!r}")
    if not ref.is_file():
        raise SkillError(f"Reference not found: {name}/references/{filename}")
    return ref.read_text(encoding="utf-8")


def list_references(name: str) -> list[str]:
    """List reference filenames for a skill (Level 3 discovery)."""
    if name not in EXPECTED_SKILL_NAMES:
        raise SkillError(f"Unknown skill: {name!r}")
    ref_dir = SKILLS_DIR / name / "references"
    if not ref_dir.is_dir():
        return []
    return sorted(p.name for p in ref_dir.iterdir() if p.is_file())


__all__ = [
    "EXPECTED_SKILL_NAMES",
    "MAX_METADATA_TOKENS_PER_SKILL",
    "MAX_METADATA_TOKENS_TOTAL",
    "SKILLS_DIR",
    "TRIGGER_MAP",
    "SkillError",
    "SkillMetadata",
    "dispatch",
    "iter_metadata",
    "list_references",
    "load_body",
    "load_reference",
    "metadata_block",
]
