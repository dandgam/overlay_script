"""Event-driven wakeup loop (spec §4 — 13 event types + 5-min backstop).

Producers (S3+): worker_spawn (subprocess watchers), bot/telegram_bot, safety guards,
scheduled tasks. Consumer: agent/loop top-level orchestrator (one consumer per session).

Spec §4 events:
    worker_completed, worker_halt_file, worker_elicitation, budget_threshold_hit,
    wave_boundary_reached, epic_boundary_reached, user_chat_message,
    monthly_review_scheduled, voice_message_received, story_split_triggered,
    phase4_complete, human_response, scheduled_wakeup_5min.

Initiative #2C extension (sub-story life-cycle) — see runtime/sub_story_executor.py
+ runtime/auto_split.py. Bridge translates executor ``on_event`` dicts into typed
event-bus emissions so downstream observers (telemetry, code-review timing, retro)
can subscribe without depending on the executor's callback contract.

Backstop: `start_backstop_task()` запускает фоновый таск который раз в N секунд
(default 300s = 5min) emits SCHEDULED_WAKEUP — гарантирует пробуждение даже если
ни одного «настоящего» event'а не пришло.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

BACKSTOP_INTERVAL_SECONDS_DEFAULT = 300


class EventType(StrEnum):
    """Spec §4 — все 13 типов + FS4 HUMAN_QUERY. Phase 4 hardening adds up to 30 total."""

    WORKER_COMPLETED = "worker_completed"
    WORKER_HALT_FILE = "worker_halt_file"
    WORKER_ELICITATION = "worker_elicitation"
    WORKER_SILENT_FAILURE = "worker_silent_failure"
    COST_TRACKING_UNAVAILABLE = "cost_tracking_unavailable"
    BUDGET_THRESHOLD_HIT = "budget_threshold_hit"
    WAVE_BOUNDARY_REACHED = "wave_boundary_reached"
    EPIC_BOUNDARY_REACHED = "epic_boundary_reached"
    USER_CHAT_MESSAGE = "user_chat_message"
    MONTHLY_REVIEW_SCHEDULED = "monthly_review_scheduled"
    VOICE_MESSAGE_RECEIVED = "voice_message_received"
    STORY_SPLIT_TRIGGERED = "story_split_triggered"
    # Initiative pilot_findings_closure S3 (#3 rycag 2): emitted by
    # ``runtime/decomposer_subscriber.py`` when a WORKER_HALT_FILE arrives with
    # ``halt_reason="loc_cap_exceeded"`` and the auto-split decomposer is
    # invoked. Distinct from STORY_SPLIT_TRIGGERED (heuristic-only); this fires
    # on the recovery path after Sonnet hit the 300-LOC diff cap.
    STORY_AUTO_SPLIT = "story_auto_split"
    # Initiative pilot_findings_closure S4 (#4 R1): emitted by
    # ``runtime/worker_cancellation.cancel_worker`` after a per-worker
    # cancellation token is tripped. Payload carries ``worker_id``, ``reason``
    # and ``cancelled_by`` (supervisor | user | timeout) so the audit trail and
    # downstream subscribers can attribute kills.
    WORKER_CANCELLED = "worker_cancelled"
    # Initiative pilot_findings_closure S5 (#5 R2): emitted by
    # :func:`runtime.worker_spawn.spawn_worker` when the pre-spawn MCP
    # readiness probe (``runtime/mcp_readiness.poll_mcp_ready``) reports
    # one or more required MCP tools as unauthenticated within 30s. Payload
    # carries ``story_id``, ``missing`` (list[str]), ``elapsed_ms`` and
    # ``last_error`` so the orchestrator can halt the story instead of
    # spawning a worker that would die later on ``tool not found``.
    MCP_NOT_READY = "mcp_not_ready"
    # Initiative pilot_findings_closure S6 (#6 P2): emitted by
    # :mod:`runtime.budget_autodetect` the first time a pilot run detects
    # subscription auth mode (no ANTHROPIC_API_KEY) and auto-skips the
    # $-budget gates. One emission per run (idempotent). Payload carries
    # ``reason`` (always ``"subscription_mode"`` for now) so audit consumers
    # can distinguish auto-disable from the manual ``BMAD_DISABLE_BUDGET=1``
    # path (which does NOT emit this event).
    BUDGET_AUTO_DISABLED = "budget_auto_disabled"
    # Initiative pilot_findings_closure S6 (#7 P2): emitted by
    # :func:`runtime.worker_spawn.spawn_worker` when a pre-spawn check
    # detects ``<worktree>/_bmad/auto-dev-state/halt-reason.txt`` from a
    # prior run. Default behaviour skips spawn and raises
    # :class:`runtime.worker_spawn.WorkerHaltPrespawnError`. With
    # ``auto_clear_halt=True`` (e.g. CLI ``--resume``) the file is removed
    # and the spawn proceeds without emitting this event.
    WORKER_HALT_PRESPAWN = "worker_halt_prespawn"
    SUB_STORY_STARTED = "sub_story_started"
    SUB_STORY_COMPLETED = "sub_story_completed"
    SUB_STORY_SQUASH_DONE = "sub_story_squash_done"
    SUB_STORY_SQUASH_SKIPPED = "sub_story_squash_skipped"
    PHASE4_COMPLETE = "phase4_complete"
    HUMAN_QUERY = "human_query"
    HUMAN_RESPONSE = "human_response"
    SCHEDULED_WAKEUP = "scheduled_wakeup_5min"
    CODE_REVIEW_VERDICT = "code_review_verdict"
    SECURITY_REVIEW_PASSED = "security_review_passed"
    COMPLIANCE_SWEEP_NEEDED = "compliance_sweep_needed"
    # BMad Phase 4 canonical workflow triggers (gap-closure 2026-05-19).
    SPRINT_SCOPE_CHANGE_DETECTED = "sprint_scope_change_detected"
    FORENSIC_INVESTIGATION_NEEDED = "forensic_investigation_needed"
    # Phase 4 hardening #2 — PreCompact memory persistence observability.
    WORKER_STATE_PERSISTED = "worker_state_persisted"
    # Phase 4 hardening #5 — Two-stage merge gate split.
    # Payload: {stage: "spec"|"quality", verdict: str, findings_count: int}
    MERGE_GATE_STAGE_COMPLETED = "merge_gate_stage_completed"
    # Phase 4 hardening #6 — Stop-hook cost + learning consolidation.
    # STORY_COMPLETED: emitted when a story finishes (all stages including merge gate).
    # Payload: {story_id, status, extract_lessons: bool, tokens: {input, cached, output},
    #           cost_usd, retry_count, turn_latencies_ms: [float, ...]}
    STORY_COMPLETED = "story_completed"
    # STORY_METRICS_AGGREGATED: emitted by stop_hook_subscriber after aggregating per-story metrics.
    # Payload: {story_id, total_input_tokens, total_cached_tokens, total_output_tokens,
    #           total_cost_usd, retry_count, p95_turn_latency_ms, lessons_extracted: bool}
    STORY_METRICS_AGGREGATED = "story_metrics_aggregated"
    # Initiative pilot_findings_closure_v2 S2 (#2 NEW-2 Layer B): emitted by
    # ``agent.run._tail_and_emit_completion`` when the worker's stdout carries a
    # ``cannot delete branch ... used by worktree`` line — the runner's Stage 7
    # ``git branch -d`` failed because a reused/stale worktree still holds the
    # feature branch. Payload: {story_id, worktree, branch, commits, jsonl}.
    # When ``commits > 0`` the detector additionally emits a synthetic
    # CODE_REVIEW_VERDICT(verdict=approve, source=runner_cleanup_recovery) so the
    # merge subscriber recovers the work instead of treating it as a halt.
    RUNNER_CLEANUP_FAILED_REUSED_WORKTREE = "runner_cleanup_failed_reused_worktree"
    # Initiative pilot_findings_closure_v3 S3 (#1 NEW-7 observability): emitted
    # when a story finishes ``worker_completed status=success`` but its work
    # never reaches ``integration/<wave>``. Payload: {story_id, reason, worktree,
    # commits}. ``reason`` ∈ {``no_commits`` — success but zero commits past
    # base_sha; ``verdict_missing`` — success but no WorkerHandle/base_sha to
    # verify commits, so no verdict could be reconciled; ``ff_conflict`` —
    # merge_to_integration_subscriber's fast-forward merge failed}. Turns a
    # silent loss of work into a visible audit signal (replay finding NEW-7).
    INTEGRATION_MERGE_SKIPPED = "integration_merge_skipped"
    # Initiative pilot_findings_closure_v5 S2 (NEW-13): emitted by
    # ``runtime/security_review.security_review_subscriber`` each time the
    # security-review runner yields ``verdict=error`` (a technical failure of
    # the review step itself, not a story defect). Payload: {story_id,
    # worktree, attempt, max_retries, retrying (bool), findings}. A technical
    # error is NOT counted toward the supervisor circuit-breaker's
    # consecutive-escalation cap — see ``supervisor/engine.py``. After retries
    # are exhausted the subscriber escalates the single story via HUMAN_QUERY
    # instead of aborting the whole pipeline.
    SECURITY_REVIEW_ERROR = "security_review_error"
    # Initiative pilot_findings_closure_v6 S1 (NEW-19): emitted once by
    # :func:`agent.run.run_replay` at the start of a replay-from-worktree run.
    # Replay skips the expensive ``spawn_worker`` (worker-dev) phase entirely:
    # it takes a worktree that already carries a dev commit and drives only the
    # post-dev pipeline tail (stage5 → build-check → merge-gate → reconcile →
    # merge). Payload: {story_id, worktree, integration_branch, base_sha,
    # dev_commits (int), synthesized (bool — True when --auto-commit-dev
    # synthesized the dev commit from a dirty worktree)}. Lets audit consumers
    # distinguish a cheap validation replay from a real worker-dev pilot run.
    REPLAY_MODE_STARTED = "replay_mode_started"
    # Initiative pilot_findings_closure_v6 S2 (NEW-15): emitted once per failing
    # attempt of the two-stage merge gate when it yields ``verdict=error`` (a
    # technical failure of the review step — empty review JSONL, spawn failure
    # — not a story defect). Payload: {story_id, worktree, attempt, max_retries,
    # retrying (bool), gate_stage}. Mirrors :data:`SECURITY_REVIEW_ERROR`. After
    # retries are exhausted ``code_review_subscriber`` escalates the single
    # story via one HUMAN_QUERY (verdict=code_review_error) instead of emitting
    # a CODE_REVIEW_VERDICT(error) — the latter would feed the supervisor
    # circuit breaker as a story escalation.
    CODE_REVIEW_ERROR = "code_review_error"
    # Initiative pilot_findings_closure_v6 S3 (NEW-16): emitted by
    # :func:`agent.run.merge_to_integration_subscriber` after a feature branch
    # is fast-forward-merged into ``integration/<wave>``. Payload: {story_id,
    # feature, integration, sha}. The positive counterpart of
    # :data:`INTEGRATION_MERGE_SKIPPED` — :func:`runtime.pilot_outcomes.
    # partition_pilot_outcomes` uses it to compute the honest ``succeeded``
    # metric (a worker exiting ``status=success`` only proves dev work landed
    # on the feature branch, not that it reached integration).
    INTEGRATION_MERGE_COMPLETED = "integration_merge_completed"
    # Initiative pilot_findings_closure_v6 S3 (NEW-17): emitted by
    # ``agent.run._tail_and_emit_completion`` when a worker's JSONL tail ends
    # WITHOUT a terminal ``worker_completed``/``worker_halt_file`` event, the
    # worktree still carries uncommitted changes, and no stage5 recovery ran.
    # Turns a silent worker exit (story 1.5 pilot run #5 — files written, never
    # committed, no stage5_recovery_failed) into a loud audit signal. Payload:
    # {story_id, worktree, jsonl, reason}.
    WORKER_EXIT_UNCOMMITTED = "worker_exit_uncommitted"
    INTEGRATION_TEST_FAILED = "integration_test_failed"
    WORKER_STUCK_TIMEOUT = "worker_stuck_timeout"
    # NEW-36 — emitted by ``runtime.worker_spawn._wait_and_finalize`` immediately
    # before SIGKILL when the hard-ceiling timeout fires and the worktree has
    # uncommitted changes. An ``git add -A && git commit`` auto-stage is attempted
    # first so the work survives the kill. Payload: {story_id, worktree,
    # auto_stage_sha (str|None — None if git commit failed), dirty_files (int),
    # hard_timeout_sec (int)}. Distinct from ``subprocess_timeout`` which is
    # emitted *after* the kill; this one fires *before* and only when dirty files
    # are present.
    WORKER_AUTO_STAGE_RECOVERY = "worker_auto_stage_recovery"
    # NEW-37 — emitted by the review-worker tail loops
    # (``_real_security_review_runner``, ``_run_merge_gate_spec_stage``,
    # ``_run_merge_gate_quality_stage``) when ``tail_with_stuck_watchdog``
    # trips because no JSONL events arrived for ``BMAD_REVIEW_TIMEOUT_SEC``
    # seconds (default 900 = 15 min). Distinct from ``WORKER_STUCK_TIMEOUT``
    # (dev workers) so the supervisor can route differently.
    # Payload: {story_id, stage: "security"|"spec"|"quality",
    #           elapsed_seconds, jsonl_path}.
    REVIEW_STUCK_TIMEOUT = "review_stuck_timeout"
    # NEW-38 — emitted by ``supervisor/actions.py`` when the Supervisor LLM
    # judge classifies an event and decides to respawn the story worker.
    # The orchestrator main loop subscribes and re-queues the story_id for
    # the next round, capped at 2 respawns per story.
    # Payload: {story_id, reason, iteration (int), dev_prompt_hint (str|None),
    #           cancelled_worker_id (str|None)}.
    WORKER_RESPAWN_REQUESTED = "worker_respawn_requested"

ALL_EVENT_TYPES: tuple[EventType, ...] = tuple(EventType)


def _utc_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(slots=True)
class Event:
    type: EventType
    payload: dict[str, Any] = field(default_factory=dict)
    emitted_at: str = field(default_factory=_utc_iso)


EventCallback = Callable[[Event], Awaitable[None]]


class EventLoop:
    """Single-process producer/multi-consumer event queue.

    Подписки (`on(...)`) выполняются sequentially per event под `asyncio.Lock`,
    чтобы порядок (FIFO) и effect'ы оставались детерминированными при
    нескольких subscriber'ах.
    """

    def __init__(self) -> None:
        self.queue: asyncio.Queue[Event] = asyncio.Queue()
        self._subs: list[EventCallback] = []
        self._lock = asyncio.Lock()
        self._backstop_task: asyncio.Task[None] | None = None
        self._stopped = asyncio.Event()
        self._corr_futures: dict[str, asyncio.Future[Event]] = {}

    # ── public producer API ──────────────────────────────────────────────────

    async def emit(self, event_or_type: Event | EventType, **payload: Any) -> Event:
        """Emit an event. Accept either Event or (EventType, **payload)."""
        if isinstance(event_or_type, EventType):
            event = Event(type=event_or_type, payload=dict(payload))
        else:
            event = event_or_type
        await self.queue.put(event)
        self._resolve_correlation(event)
        return event

    # ── public consumer API ──────────────────────────────────────────────────

    async def next(self, timeout: float | None = None) -> Event | None:
        """Block for next event. `timeout=None` blocks forever; returns None on timeout."""
        if timeout is None:
            return await self.queue.get()
        try:
            return await asyncio.wait_for(self.queue.get(), timeout=timeout)
        except TimeoutError:
            return None

    def on(self, callback: EventCallback) -> None:
        """Register a subscriber called per dispatched event."""
        self._subs.append(callback)

    async def dispatch_one(self, timeout: float | None = None) -> Event | None:
        """Pull one event and run all subscribers. Returns the event (or None on timeout)."""
        event = await self.next(timeout=timeout)
        if event is None:
            return None
        self._resolve_correlation(event)
        async with self._lock:
            for cb in list(self._subs):
                await cb(event)
        return event

    async def drain(
        self, *, max_events: int = 1000, idle_timeout: float = 0.05
    ) -> list[Event]:
        """Dispatch every queued event (and cascades they emit) until empty.

        Returns the events dispatched, in FIFO order. Used by the real-pilot
        body before ``real_pilot_done``: in real mode nothing else calls
        :meth:`dispatch_one`, so without an explicit drain the
        ``WORKER_COMPLETED`` → ``CODE_REVIEW_VERDICT`` → merge-to-integration
        subscriber chain never runs and ``integration/<wave>`` is silently
        never created (validation-replay finding NEW-7).

        ``max_events`` bounds a runaway cascade (a subscriber re-emitting its
        own trigger). On hitting the cap the drain stops — the queue may
        still hold events, but the loop will not spin forever.
        """
        dispatched: list[Event] = []
        while len(dispatched) < max_events:
            event = await self.dispatch_one(timeout=idle_timeout)
            if event is None:
                break
            dispatched.append(event)
        return dispatched

    # ── single-shot correlation futures (W5 — bot ↔ intent-router bridge) ────

    def subscribe_one_correlation(self, corr_id: str) -> asyncio.Future[Event]:
        """Return an asyncio.Future that resolves on the first ``HUMAN_RESPONSE``
        event whose payload's ``corr_id`` matches.

        Single-shot: resolved on first match, then removed from the registry.
        Callers MUST call :meth:`unsubscribe_correlation` in a ``finally`` block
        if they abandon the wait (timeout, cancellation), otherwise the entry
        leaks until process exit.

        If an outstanding (not-yet-resolved) future already exists for the same
        ``corr_id``, that same future is returned so concurrent callers join the
        single wait. A done/cancelled future is replaced with a fresh one — the
        old corr_id has effectively expired.
        """
        existing = self._corr_futures.get(corr_id)
        if existing is not None and not existing.done():
            return existing
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[Event] = loop.create_future()
        self._corr_futures[corr_id] = fut
        return fut

    def unsubscribe_correlation(self, corr_id: str) -> None:
        """Drop the future registered for ``corr_id``. Idempotent."""
        self._corr_futures.pop(corr_id, None)

    def _resolve_correlation(self, event: Event) -> None:
        """Resolve any pending future whose ``corr_id`` matches this event.

        Fired from both :meth:`emit` (producer-side, wakes awaiter even when
        nobody drains the queue) and :meth:`dispatch_one` (consumer-side, kept
        as defence-in-depth — a future could have been registered after the
        emit but before dispatch). Pop semantics make the resolution idempotent.
        """
        if event.type is not EventType.HUMAN_RESPONSE:
            return
        payload = event.payload or {}
        corr_id = payload.get("corr_id")
        if not isinstance(corr_id, str):
            return
        fut = self._corr_futures.pop(corr_id, None)
        if fut is not None and not fut.done():
            fut.set_result(event)

    # ── backstop wakeup ──────────────────────────────────────────────────────

    def start_backstop_task(
        self, interval_seconds: int = BACKSTOP_INTERVAL_SECONDS_DEFAULT
    ) -> asyncio.Task[None]:
        """Spawn a background task that emits SCHEDULED_WAKEUP every `interval_seconds`.

        Safe to call once per loop instance; second call is a no-op.
        """
        if self._backstop_task is not None and not self._backstop_task.done():
            return self._backstop_task

        async def _runner() -> None:
            while not self._stopped.is_set():
                with suppress(asyncio.CancelledError):
                    try:
                        await asyncio.wait_for(
                            self._stopped.wait(), timeout=float(interval_seconds)
                        )
                    except TimeoutError:
                        await self.emit(
                            EventType.SCHEDULED_WAKEUP,
                            interval_seconds=interval_seconds,
                        )

        self._backstop_task = asyncio.create_task(_runner(), name="event_loop_backstop")
        return self._backstop_task

    async def stop(self) -> None:
        """Signal stop, cancel backstop task, drain (best-effort)."""
        self._stopped.set()
        if self._backstop_task is not None:
            self._backstop_task.cancel()
            with suppress(asyncio.CancelledError, BaseException):
                await self._backstop_task
            self._backstop_task = None


__all__ = [
    "ALL_EVENT_TYPES",
    "BACKSTOP_INTERVAL_SECONDS_DEFAULT",
    "Event",
    "EventCallback",
    "EventLoop",
    "EventType",
]
