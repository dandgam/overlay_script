"""Pydantic data contracts.

См. spec/spec_orchestrator_agent.md §5.
Status enum совместим с BMad-Method canonical sprint-status.yaml (v6.6.0).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class Risk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class StoryStatus(str, Enum):
    """BMad-canonical 5 states (см. BMAD-METHOD/src/bmm-skills/4-implementation/bmad-sprint-planning).

    Transitions: backlog → ready-for-dev → in-progress → review → done
    """

    BACKLOG = "backlog"
    READY_FOR_DEV = "ready-for-dev"
    IN_PROGRESS = "in-progress"
    REVIEW = "review"
    DONE = "done"


class EpicStatus(str, Enum):
    """BMad-canonical epic states."""

    BACKLOG = "backlog"
    IN_PROGRESS = "in-progress"
    DONE = "done"


class RetroStatus(str, Enum):
    """Retrospective status per epic."""

    OPTIONAL = "optional"
    DONE = "done"


class Story(BaseModel):
    """Story metadata.

    BMad-canonical id format: `<epic>-<story>-<slug>` (e.g. "1-2-tenant-signup").
    NOT `1.2` or `1.2a` — those были наши локальные конвенции до v0.5 spec'и.
    """

    id: str  # "1-2-tenant-signup"
    epic_id: str  # "1"
    title: str
    file_path: str
    estimated_tokens: int = 50_000
    estimated_minutes: int = 25
    risk: Risk = Risk.MEDIUM
    depends_on: list[str] = Field(default_factory=list)
    touches_files: list[str] = Field(default_factory=list)
    touches_shared: list[str] = Field(default_factory=list)
    security_critical: bool = False
    requires_human: bool = False
    status: StoryStatus = StoryStatus.BACKLOG


class WorkerStatus(BaseModel):
    worktree: str
    pid: int
    story_id: str
    stage: str
    started_at: datetime
    last_event_at: datetime
    tokens_used: int = 0
    cost_usd: float = 0.0
    state: Literal["active", "paused", "review", "halted", "dead"] = "active"


class Worktree(BaseModel):
    path: str
    branch: str
    story_id: str | None = None
    worker_pid: int | None = None


class Budget(BaseModel):
    scope: Literal["story", "batch", "wave", "day", "phase"]
    spent_usd: float
    spent_tokens: int
    alarm_threshold: float
    halt_threshold: float
    breached_alarm: bool = False
    breached_halt: bool = False


class ElicitationEvent(BaseModel):
    story_id: str
    worker_pid: int
    question: str
    context: str
    topics: list[str]
    proposed_answer: str | None = None


class ElicitationResolution(BaseModel):
    action: Literal["auto_resolve", "escalate"]
    answer: str | None = None
    matched_rule_id: str | None = None
    reason: str


class Finding(BaseModel):
    severity: Literal["critical", "high", "medium", "low"]
    category: str
    message: str
    file: str | None = None
    line: int | None = None


class ReviewResult(BaseModel):
    worktree: str
    status: Literal["pass", "fail"]
    findings: list[Finding] = Field(default_factory=list)
    review_type: Literal["code", "security"]


class MergeResult(BaseModel):
    worktree: str
    target_branch: str
    commit_sha: str | None = None
    merged: bool
    reason: str | None = None
