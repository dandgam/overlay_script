"""Patch N — build check guard subscriber (canonical port from runner.sh).

On ``WORKER_COMPLETED`` (status=success), this subscriber runs the configured
build commands (default: ``pytest tests/ -q`` and ``ruff check src tests``) in
the worker's worktree. ANY required command with a non-zero exit halts the
event:

  * ``event.payload['status']`` is mutated to ``'halted_build_check_failed'``
    so the downstream ``code_review_subscriber`` (which gates on ``status ==
    'success'``) skips — no ~$15 Opus run on a broken artefact.
  * A ``HUMAN_QUERY`` is emitted with the failing command + tail of its output.

Designed to be wired BEFORE ``deletion_safety_subscriber`` in
``_run_real_pilot`` so the cheap build guard pre-screens before the more
expensive (in human-attention terms) safety scan; either halt skips the
downstream ``code_review`` spawn entirely.

Reference: ~/.claude/skills/bmad-auto-dev/scripts/bmad-auto-dev-runner.sh
Stage 5.5 (lines 521-556 of the original 1100 LOC bash runner). Patch N
2026-05-16.
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
from pathlib import Path

import structlog
import yaml
from pydantic import BaseModel, Field, ValidationError

from bmad_orchestrator.runtime.event_loop import Event, EventLoop, EventType
from bmad_orchestrator.skills_repo import PolicyInvalidError, PolicyNotFoundError

log = structlog.get_logger(__name__)

BUILD_CHECK_POLICY_PATH_DEFAULT = (
    Path(__file__).resolve().parents[3] / "skills" / "policy" / "build-check.yaml"
)


class BuildCheckCommand(BaseModel):
    """A single build-check command entry."""

    name: str
    run: str
    required: bool = True


class BuildCheckPolicy(BaseModel):
    """Schema for ``skills/policy/build-check.yaml``."""

    commands: list[BuildCheckCommand] = Field(default_factory=list)
    timeout_sec: int = 600
    tail_lines: int = 40
    skip_if_missing_executable: bool = True
    escalation_text: str = ""


@dataclass(slots=True, frozen=True)
class BuildCheckResult:
    name: str
    run: str
    exit_code: int
    tail: str
    timed_out: bool = False
    skipped_missing_executable: bool = False


def load_build_check_policy(path: Path | None = None) -> BuildCheckPolicy:
    """Load + validate the build-check policy yaml.

    Raises:
        PolicyNotFoundError — file missing.
        PolicyInvalidError  — YAML parse OR schema validation fail.
    """
    p = path or BUILD_CHECK_POLICY_PATH_DEFAULT
    if not p.exists():
        raise PolicyNotFoundError(f"build-check policy not found: {p}")
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise PolicyInvalidError(f"YAML parse error in {p}: {e}") from e
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise PolicyInvalidError(
            f"top-level structure must be mapping in {p}, got {type(raw).__name__}"
        )
    try:
        return BuildCheckPolicy.model_validate(raw)
    except ValidationError as e:
        raise PolicyInvalidError(
            f"build-check schema validation failed: {e}"
        ) from e


def _first_token(cmd: str) -> str:
    """Return the executable name from a shell command line (first whitespace-
    separated token of the trimmed string). Empty string if cmd is blank."""
    stripped = cmd.strip()
    if not stripped:
        return ""
    return stripped.split(None, 1)[0]


async def _run_command(
    cmd: BuildCheckCommand,
    worktree: Path,
    timeout_sec: int,
    tail_lines: int,
    skip_if_missing_executable: bool,
) -> BuildCheckResult:
    """Run a single build-check command inside the worktree.

    Uses ``/bin/sh -c <cmd.run>`` for shell-style argument handling consistent
    with the bash runner's ``cargo check --message-format=short`` invocation.
    Returns a BuildCheckResult describing exit code + tail of merged stderr/
    stdout; on timeout the result has ``timed_out=True`` and exit_code=124.
    """
    if skip_if_missing_executable:
        token = _first_token(cmd.run)
        if token and shutil.which(token) is None:
            log.info(
                "build_check_skip_missing_executable",
                command=cmd.name,
                token=token,
                worktree=str(worktree),
            )
            return BuildCheckResult(
                name=cmd.name,
                run=cmd.run,
                exit_code=0,
                tail="",
                skipped_missing_executable=True,
            )

    try:
        proc = await asyncio.create_subprocess_exec(
            "/bin/sh",
            "-c",
            cmd.run,
            cwd=str(worktree),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except (OSError, FileNotFoundError) as e:
        log.warning(
            "build_check_spawn_failed",
            command=cmd.name,
            worktree=str(worktree),
            error=str(e),
        )
        return BuildCheckResult(
            name=cmd.name, run=cmd.run, exit_code=127, tail=str(e)[:200]
        )

    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
    except TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            await proc.wait()
        except (OSError, ProcessLookupError):
            pass
        log.warning(
            "build_check_timeout",
            command=cmd.name,
            worktree=str(worktree),
            timeout_sec=timeout_sec,
        )
        return BuildCheckResult(
            name=cmd.name,
            run=cmd.run,
            exit_code=124,
            tail=f"<timed out after {timeout_sec}s>",
            timed_out=True,
        )

    text = stdout.decode("utf-8", errors="replace") if stdout else ""
    tail = "\n".join(text.splitlines()[-tail_lines:]) if tail_lines > 0 else text
    return BuildCheckResult(
        name=cmd.name,
        run=cmd.run,
        exit_code=proc.returncode if proc.returncode is not None else -1,
        tail=tail,
    )


async def build_check_subscriber(
    event: Event, bus: EventLoop, policy_path: Path | None = None
) -> None:
    """Patch N subscriber — see module docstring.

    Idempotent on non-WORKER_COMPLETED events and on already-halted payloads.
    Designed to run BEFORE ``deletion_safety_subscriber`` and
    ``code_review_subscriber`` so a build halt skips the downstream review
    spawn entirely.
    """
    if event.type != EventType.WORKER_COMPLETED:
        return
    payload = event.payload or {}
    if payload.get("status") != "success":
        return

    worktree_raw = payload.get("worktree")
    story_id = payload.get("story_id") or ""
    if not isinstance(worktree_raw, str) or not worktree_raw:
        log.warning("build_check_skip_missing_worktree", payload=payload)
        return
    worktree = Path(worktree_raw)

    try:
        policy = load_build_check_policy(policy_path)
    except (PolicyNotFoundError, PolicyInvalidError) as e:
        log.warning("build_check_policy_load_failed", error=str(e))
        return

    if not policy.commands:
        return

    results: list[BuildCheckResult] = []
    failed_required: BuildCheckResult | None = None
    for cmd in policy.commands:
        result = await _run_command(
            cmd,
            worktree=worktree,
            timeout_sec=policy.timeout_sec,
            tail_lines=policy.tail_lines,
            skip_if_missing_executable=policy.skip_if_missing_executable,
        )
        results.append(result)
        if result.exit_code != 0 and cmd.required:
            failed_required = result
            break

    if failed_required is None:
        log.info(
            "build_check_clean",
            story_id=str(story_id),
            worktree=str(worktree),
            commands=[r.name for r in results],
        )
        return

    log.warning(
        "build_check_halt",
        story_id=str(story_id),
        worktree=str(worktree),
        command=failed_required.name,
        exit_code=failed_required.exit_code,
        timed_out=failed_required.timed_out,
    )

    payload["status"] = "halted_build_check_failed"
    payload["halt_reason"] = "build_check"
    payload["failed_command"] = failed_required.name
    payload["failed_command_exit"] = failed_required.exit_code

    await bus.emit(
        EventType.HUMAN_QUERY,
        story_id=str(story_id),
        worktree=str(worktree),
        verdict="build_check_halt",
        text=(
            f"{policy.escalation_text}\n\n"
            f"Story: {story_id}\n"
            f"Worktree: {worktree}\n"
            f"Failed command: {failed_required.name} ({failed_required.run})\n"
            f"Exit code: {failed_required.exit_code}"
            f"{' [timed out]' if failed_required.timed_out else ''}\n"
            f"--- tail ---\n{failed_required.tail}"
        ),
        failed_command=failed_required.name,
        actions=["abandon", "manual_fix_and_resume"],
    )


__all__ = [
    "BUILD_CHECK_POLICY_PATH_DEFAULT",
    "BuildCheckCommand",
    "BuildCheckPolicy",
    "BuildCheckResult",
    "build_check_subscriber",
    "load_build_check_policy",
]
