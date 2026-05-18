# Initiative Tracker — Pilot Findings Closure + Phase 3 Step B

## Metadata
- **Spec:** spec/spec_pilot_findings_closure.md
- **Integration branch:** integration/pilot_findings_closure
- **Created:** 2026-05-19
- **Bootstrap completed:** 2026-05-19 by /auto-loop-spec-long
- **Scope frozen:** 2026-05-19
- **Runtime:** loop_wrapper
- **Delay seconds:** 300
- **Auto merge:** false

## Scope Freeze

### In scope
- 5 P1 items from pilot findings + research (mark-done normalize, verdict event wiring, autofix routing+split, AbortController, MCP readiness)
- 3 P2 items (budget auto-detect, halt pre-flight, subprocess timeout adaptive)
- 1 P3 item (real_pilot_done counter split)
- Phase 3 closure: Step B real BMad eval cases (≥10 + baseline)
- methodology-virgil.md §5 updates (mark all 10 items DONE, close Phase 3 gate)
- 4-5 new event types (`STORY_AUTO_SPLIT`, `WORKER_CANCELLED`, `MCP_NOT_READY`, `BUDGET_AUTO_DISABLED`, `WORKER_HALT_PRESPAWN`)
- Tests target ≥1950 PASS (delta +90 from baseline 1860), mypy/ruff clean

### Out of scope (explicit)
- R3/R4/R5 research findings (defer)
- P3 backlog-writer subscriber meta-feature
- Observability dashboard (Phase 5 item #12)
- TTS notifications
- Vision steps 3-7 embedding work
- Production pilot itself (#10) — this initiative unblocks it but does not run it

### Deferred to follow-up initiative
- R3 per-turn token snapshot
- R4 stale worktree GC
- R5 fail-closed cleanup policy
- Backlog-writer subscriber meta-feature

## Sessions

### Pending

- **id:** S7
  **title:** #10 Step B real eval cases — harness + first 5 cases
  **surface:** backend-python
  **spec_section:** spec/spec_pilot_findings_closure.md §4 #10 (part 1)
  **depends_on:** []
  **acceptance:**
    - evals/cases/real/ contains 5 cases (3 easy + 2 medium) with YAML schema
    - CLI `bmad-orchestrator eval run --mode real --project-root <path> --cases-dir evals/cases/real/` works
    - +6 tests
  **safety_gates: []
  **checkpoint:** true
  **estimated_retries_allowed:** 3

- **id:** S8
  **title:** #10 part 2 (5 hard cases + baseline) + methodology Phase 3 close-out
  **surface:** backend-python
  **spec_section:** spec/spec_pilot_findings_closure.md §4 #10 (part 2) + §6
  **depends_on:** [S7]
  **acceptance:**
    - ≥10 real cases total in evals/cases/real/
    - baseline file evals/baselines/phase3-step-b-baseline.json with pass_rate/median_latency/median_cost
    - methodology-virgil.md §5 marks all 10 spec items DONE
    - methodology-virgil.md Phase 3 status flipped from 🟡 IN PROGRESS → ✅ DONE
    - Backlog «pilot findings» + R1/R2 sections cleared in methodology
  **safety_gates: []
  **checkpoint:** false
  **estimated_retries_allowed:** 3

### Current

- **id:** S6
  **title:** #6 budget auto-detect (subscription mode) + #7 halt pre-flight check
  **surface:** backend-python
  **spec_section:** spec/spec_pilot_findings_closure.md §2 #6 + #7
  **depends_on:** []
  **acceptance:**
    - subscription_mode detect auto-sets Settings._budget_disabled, emits BUDGET_AUTO_DISABLED
    - Pre-spawn halt-reason.txt check skips spawn with WORKER_HALT_PRESPAWN event
    - --resume flag clears halt before spawn
    - +5 + +5 = +10 tests
  **safety_gates: []
  **checkpoint:** false
  **estimated_retries_allowed:** 3
  **started:** 2026-05-19 (auto-promoted after S5)
  **workflow:** workflows/backend-python.md
  **retry_count:** 0
  **worker_branches:** []

### Completed

- **id:** S5
  **title:** #5 MCP server readiness polling
  **completed:** 2026-05-19 UTC
  **commit:** 2a3a0d6
  **files_changed:** 7 (2 new + 5 modified; runtime + config + tests)
  **tests_passed:** 1928 (was 1920; +8 new = 4 polling unit + 2 parse unit + 2 spawn-gate integration; target +8 ✓)
  **decisions_made:**
    - New module `runtime/mcp_readiness.py` with `poll_mcp_ready(required_tools, *, timeout_s=30, interval_ms=500, cli_argv=None, clock=time.monotonic, sleep=asyncio.sleep)` returning `ReadinessResult(ok, missing, elapsed_ms, polls, last_error)`. Clock+sleep injected for deterministic tests (real callers use defaults).
    - `parse_mcp_list_output` accepts BOTH `{"servers": [...]}` wrapped shape and bare list shape — Claude CLI has emitted both historically. Unknown shape / malformed JSON → empty mapping (treated as "every required tool missing") so CLI breakage degrades into halt-before-spawn, never false positive.
    - `authenticated` is strict-True check (`authed is True`) not truthy — JSON `true` becomes Python `True`, anything else (None, "yes", 1) treated as not ready. Eliminates partial-credit semantics.
    - Empty `required_tools` short-circuits with `ok=True, polls=0` — no subprocess, no overhead for the default (opt-in) case where `Settings.required_mcp_tools=[]`.
    - `spawn_worker` accepts `required_mcp_tools: list[str] | None = None` + `mcp_readiness_timeout_s` + `mcp_readiness_interval_ms`. Backward-compatible default (None/empty = skip check). When non-empty and not-ok → emit `mcp_not_ready` JSONL audit event then raise `MCPNotReadyError(story_id, missing, elapsed_ms, last_error)`. Caller (orchestrator) catches and converts to bus event + story halt.
    - `MCPNotReadyError` is a `RuntimeError` subclass exported from `runtime.worker_spawn` so existing tools/spawn.py wrappers see it as a normal exception. Carries structured fields for callers that want machine-readable halt reasons.
    - Pre-spawn check placed AFTER worktree existence check but BEFORE skills_result/mock branch — so even mock-mode callers can exercise the gate in tests (and prod mock-mode honours the gate when caller opts in).
    - `Settings.required_mcp_tools: list[str] = Field(default_factory=list)` — pydantic v2 friendly, env-overridable via `ORCHESTRATOR_REQUIRED_MCP_TOOLS=["analyzer","postgres-mcp"]` (JSON list per pydantic-settings convention).
    - Inventory tests in `test_canonical_patches_p6.py` (31 → 32) and `test_s3_runtime.py` (31 → 32) bumped — keeps EventType drift visible.
  **deferred_items:**
    - Per-story frontmatter override (`requires_mcp: [analyzer, postgres-mcp]`) — the gate is wired with `required_mcp_tools` param but the per-story override that merges `Settings.required_mcp_tools ∪ story.frontmatter.requires_mcp` is best added at the orchestrator/dispatch layer (where the story md is already parsed), not in `spawn_worker`. Leaving for the session that wires the orchestrator-side bus subscriber for `MCP_NOT_READY` and per-story frontmatter routing.
    - Orchestrator-side `MCP_NOT_READY` bus subscriber that converts the per-worker halt into a `WORKER_HALT_PRESPAWN` semantically equivalent to S6's halt-reason.txt path — same session as S6 wiring.
    - `claude mcp list --json` real-shape verification — current parser supports the two documented shapes; if a third surfaces (e.g. nested under `result.tools`), add a unit test pinning the new shape rather than re-architecting.

- **id:** S4
  **title:** #4 AbortController per worker
  **completed:** 2026-05-19 UTC
  **commit:** 6929ee7
  **files_changed:** 8 (2 new + 6 modified; runtime + supervisor + tests)
  **tests_passed:** 1920 (was 1912; +8 new = 3 token mechanics + 2 cancel_worker semantics + 2 supervisor flow + 1 real-subprocess regression; target +8 ✓)
  **decisions_made:**
    - CancellationToken wraps asyncio.Event with reason + cancelled_by metadata fields; `set` is idempotent (first call wins). Avoids losing audit attribution if multiple paths race to cancel.
    - Module-level _REGISTRY (process-local dict) keyed by `worker_id = story_id::branch::pid`. Orchestrator spawns workers inside one asyncio process, so no cross-process IPC needed. PID disambiguates retries with same story_id (different runs, same epic).
    - cancel_worker SIGTERM → 2s grace → SIGKILL fallback. Grace exposed as `grace_seconds` kwarg for tests. Idempotent: returns False on already-set token without re-killing.
    - SupervisorAction Literal extended with `cancel_worker`. actions.execute_decision resolves worker_id in priority: tool_call.args["worker_id"] → source_payload["worker_id"] → registry lookup by story_id. Avoids forcing the LLM judge to know PID-suffixed worker_ids.
    - WORKER_CANCELLED event written via append_jsonl directly (not bus.emit) because it's a per-worker audit record, not an orchestrator event. Bus-level signal lives in supervisor actions log.
    - Mock-mode workers also get a token registered + immediately unregistered. The token stays on WorkerHandle so tests can assert post-hoc semantics even after the mock "completes". No registry pollution because unregister runs synchronously.
    - _wait_and_finalize gained worker_id kwarg → unregister on subprocess exit. Prevents stale registry entries after natural completion.
  **deferred_items:**
    - Wiring a Tier 0 hard rule `WORKER_SILENT_FAILURE + reason_contains "stuck" → cancel_worker` in config/supervisor-policy.yaml — left for a future supervisor-policy tuning pass; mechanism is now available, policy still defaults to escalate_human for safety.
    - Surfacing cancel_worker from CLI (e.g. `bmad-orchestrator cancel-worker --story <id>`) — out of session scope. Can be added when CLI tooling has a richer worker-state subcommand.

- **id:** S3
  **title:** #3 autofix routing policy + #8 subprocess timeout adaptive
  **completed:** 2026-05-19 UTC
  **commit:** 949d8e737acd
  **files_changed:** 10 (6 new + 4 modified; src + tests + runner.sh + policy yaml)
  **tests_passed:** 1912 (was 1892; +20 new = 10 routing + 8 timeout + 2 inventory bumps; target +16 ✓)
  **decisions_made:**
    - Simplified routing policy schema — instead of a string-expression DSL (`if: story.tags contains "security-critical"`), used a typed pydantic schema (`security_critical_tag: str`, `opus_min_iteration: int`). Easier to validate, no fake DSL.
    - Routing module exposes a `python -m` CLI so runner.sh stays project-agnostic — bash calls `python3 -m bmad_orchestrator.runtime.autofix_routing --print-cli-name` and uses stdout for `claude --model <value>`. On any error CLI falls back to printing `sonnet` so runner never breaks.
    - `pick_timeout_sec` uses `acceptance` list length as AC count fallback (existing frontmatter shape) instead of requiring a new `ac_count` field. Bucket thresholds picked from spec: 0→DEFAULT(3600), 1-3→SMALL(1800), 4-8→MEDIUM(3600), 9+→LARGE(5400).
    - Runner default raised from 1800s → 3600s. Both `BMAD_RUNNER_CLAUDE_TIMEOUT_SEC` (new official) and legacy `PATCH_H_HARD_CEILING_SECS` accepted; new wins via `${PATCH_H_HARD_CEILING_SECS:-${BMAD_RUNNER_CLAUDE_TIMEOUT_SEC:-3600}}`.
    - `decomposer_subscriber` only emits `STORY_AUTO_SPLIT` event (event-only contract). Actual auto-split execution stays in `auto_split_and_execute` / pilot loop — subscriber is a hook, not an executor. Avoids coupling subscriber to decompose_fn + worktree context.
    - Triggered flag in payload reflects `BMAD_AUTO_SPLIT` env so downstream observers see why no split happened when env is off.
    - Inventory tests in `test_canonical_patches_p6.py` and `test_s3_runtime.py` bumped 29→30 (added STORY_AUTO_SPLIT).
  **deferred_items:**
    - Subscriber wiring into `agent/run.py` (the bus bootstrap site) — left for the session that brings auto-split out of opt-in BMAD_AUTO_SPLIT and into default behaviour.
    - Runner-side per-story adaptive timeout call — currently default + env override only; per-story `python -m subprocess_timeout` call could be added before each Stage 4-6 invocation but adds latency on every story. Reassess after S5/S6 wiring is in.

- **id:** S2
  **title:** #2 verdict event runner ↔ orchestrator wiring
  **completed:** 2026-05-19 UTC
  **commit:** 4ac3e565a5e2
  **files_changed:** 6 (2 new + 4 modified; src + tests)
  **tests_passed:** 1892 (was 1883; +9 new spec tests; target +9 ✓)
  **decisions_made:**
    - Chose Variant B (orchestrator-side fallback reader) over Variant A (modify 825-line runner.sh). Reasoning: lower risk, doesn't depend on runner.sh stdout JSON propagation through claude_event parser, easier to test with mock fixtures.
    - New module `runtime/verdict_fallback.py` (single-file, ~80 LOC) — no upstream skill changes.
    - Fallback fires ONLY when spec stage verdict=="error" (no parseable event). Quality stage uses unchanged path — if it returns error after spec approve via fallback, final verdict goes through standard worst-wins logic.
    - Most-recent mtime wins for multi-log resolution — autofix re-review (`<id>-retry-1.log`) supersedes initial review (`<id>.log`). Mirrors runner.sh's `tail -n 5 | grep -Eo | tail -n 1` semantics.
    - Added `source` field to emitted CODE_REVIEW_VERDICT payload (`runner_log_fallback` or `merge_gate_spec`) for observability — fields are passed through bus.emit(**kwargs).
  **deferred_items:**
    - Symmetric fallback for quality stage (low priority — quality-stage 'error' after spec-approve is rare in pilots; current worst-wins+HUMAN_QUERY path is acceptable).

- **id:** S1
  **title:** #1 mark-done ID normalization + #9 spawned/succeeded counter split
  **completed:** 2026-05-18 22:13 UTC
  **commit:** 7edc9bb1c25b6efeb1e434c961e088be406c1331
  **files_changed:** 4
  **tests_passed:** 1883 (was 1860 baseline, +23 incl. +12 new spec tests; target +11)
  **decisions_made:**
    - Added `resolve_sprint_status_key` as a sibling to existing `normalize_story_id` in runtime/bmad_format.py (different semantics — sprint-key lookup vs. canonical dotted) instead of overloading the existing function.
    - Changed `_tail_and_emit_completion` return type from `None` to `str` outcome tag (`completed|failed|halted|silent_failure`) — non-breaking since all existing callers ignore the return.
    - Kept legacy `stories=` key in the `real_pilot_done` log line for backwards-compat alongside new `spawned/succeeded/failed` keys.
    - Mock pilot mark-done loop left unchanged (out of spec scope).
  **deferred_items:**
    - (none)

## Safety Gates Triggered
(none)

## Blockers / Pauses
(none)

## Decisions Log

[2026-05-18 22:13 UTC] S1 — resolver lives in `runtime/bmad_format.py` (not new `agent/story_id.py`) because the file already owns the kebab/dotted regex toolkit; adding `agent/story_id.py` would split related helpers across two modules.

[2026-05-19 UTC] S2 — chose Variant B (orchestrator reads runner log) over Variant A (modify runner.sh stdout). Variant A would require coordinating JSON line format with claude_event parser; Variant B reuses runner's existing on-disk artifact and ships as a single 80-LOC module.

[2026-05-19 UTC] S3 — chose typed policy schema over string-expression DSL for autofix-routing.yaml. Simpler validation, no fake DSL. Runner stays project-agnostic via `python -m` CLI rather than embedded bash logic. Subscriber is event-only (no execution coupling) — `STORY_AUTO_SPLIT` emitted, actual decomposer call stays in `auto_split_and_execute`.

[2026-05-19 UTC] S4 — module-level registry (process-local dict) for worker tokens chosen over async-context-manager scoped registry: orchestrator runs in a single asyncio process, so a dict + worker_id keys is enough. CancellationToken keeps reason+cancelled_by inside the object so audit attribution survives even if the process exits before the JSONL flush, and SupervisorAction.cancel_worker resolves worker_id via tool_call args → payload → story_id lookup so the LLM judge does not have to learn PID-suffixed IDs.

[2026-05-19 UTC] S5 — single-file `runtime/mcp_readiness.py` module with injectable clock+sleep for deterministic tests, rather than embedding polling logic inside `worker_spawn`. Keeps the gate testable without spawning subprocesses and lets future callers (e.g. CLI doctor command, supervisor pre-flight) reuse the same helper. Per-story frontmatter override deferred to orchestrator-layer (where stories are already parsed) — `spawn_worker` stays a pure pre-Popen gate.

## Journal

[2026-05-19 UTC] bootstrap: tracker created via /auto-loop-spec-long, 8 sessions planned, S1 promoted to Current. Slug=pilot_findings_closure, runtime=loop_wrapper, delay=300s, auto_merge=false.

[2026-05-18 22:13 UTC] S1 done, runtime=loop_wrapper — wrapper handles next iteration. commit=7edc9bb, tests 1883 PASS, ruff+mypy clean. S2 promoted to Current.

[2026-05-19 UTC] S2 done, runtime=loop_wrapper — wrapper handles next iteration. commit=4ac3e56, tests 1892 PASS (+9), ruff clean. Variant B (runner-log fallback) shipped. S3 promoted to Current.

[2026-05-19 UTC] S3 done, runtime=loop_wrapper — wrapper handles next iteration. commit=949d8e7, tests 1912 PASS (+20), ruff+mypy clean. Autofix routing (security-critical → opus, iter≥2 → opus) + adaptive subprocess timeout (default 3600s, +adaptive by AC count) + STORY_AUTO_SPLIT event on loc_cap halt. S4 promoted to Current.

[2026-05-19 UTC] S4 done, runtime=loop_wrapper — wrapper handles next iteration. commit=6929ee7, tests 1920 PASS (+8), ruff clean, mypy clean on changed files. Per-worker CancellationToken + module registry + cancel_worker(SIGTERM→SIGKILL); supervisor `cancel_worker` action wired; WORKER_CANCELLED event (+1 → 31 total). S5 promoted to Current.

[2026-05-19 UTC] S5 done, runtime=loop_wrapper — wrapper handles next iteration. commit=2a3a0d6, tests 1928 PASS (+8), ruff clean, mypy clean on changed files. runtime/mcp_readiness.poll_mcp_ready (30s/500ms, injectable clock+sleep) + spawn_worker pre-Popen gate (required_mcp_tools param) + MCPNotReadyError + Settings.required_mcp_tools + MCP_NOT_READY event (+1 → 32 total). S6 promoted to Current.

## Final Report
(empty)
