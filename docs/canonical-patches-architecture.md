# Canonical BMad patches — architecture

> Port of 8 production-tested patches from Odyssey's
> `bmad-auto-dev-runner.sh` (bash, sequential) to the bmad-orchestrator's
> async event-driven subscriber chain (Python). Spec:
> `spec/spec_canonical_patches_port.md`. Initiative:
> `.claude/initiative-tracker-canonical_patches_port.md`.

## Why port instead of mirror

The Odyssey runner is a single-process bash loop with stages 1-8 hardcoded
inline. Each patch was added as a `# Patch <X> 2026-05-15:` block at a
specific stage boundary. That works for one project but does not scale:

1. **No parallelism.** The runner walks one story at a time.
2. **No observability.** Bash has no event bus; failures surface only via
   exit codes + log greps.
3. **Per-project drift.** Each target project copies the runner and
   diverges.
4. **No unit tests.** The runner is integration-tested through real pilot
   runs only.

The orchestrator already has an `EventLoop` with `bus.on(callback)` and
`dispatch_one()` (sequential per-event subscriber iteration under a lock).
Porting the patches as **subscribers** gives us per-patch unit tests,
per-patch policy YAMLs (`skills/policy/*.yaml`), and a single chain that
can fan out across stories via the DAG planner.

## Port methodology — per patch

Each P-session followed the same protocol:

1. `grep -nE "Patch <ID>" ~/.claude/skills/bmad-auto-dev/scripts/bmad-auto-dev-runner.sh`
   to locate the canonical bash block + read it.
2. Read the matching block in `~/.claude/skills/bmad-auto-dev/skill.customize.toml`
   for triggers + thresholds.
3. Decide: **new subscriber** (independent halt point) vs **extension of
   existing subscriber** (post-processing inside an existing gate).
4. Lift policy literals (thresholds, keyword lists, epic numbers) into a
   YAML under `skills/policy/` with a loader in
   `src/bmad_orchestrator/runtime/<name>.py`.
5. Write regression tests **before** the subscriber compiles, then the
   subscriber, then a re-read of the bash impl to catch missed edge
   cases.
6. Wire the subscriber into `agent/run.py::_run_real_pilot` in the
   canonical order (see below).
7. Cross-impact pass: grep callers of any new event type, verify no
   silent no-ops in mock pilots.

The bash → Python translation is **semantic**, not syntactic. Bash
`return 1` becomes either (a) an event emission (`HUMAN_QUERY`) plus a
payload mutation that downstream subscribers short-circuit on, or (b) a
hard `raise` if the failure is unrecoverable.

## Subscriber chain — canonical order

`agent/run.py::_run_real_pilot` registers 7 subscribers on
`WORKER_COMPLETED` (plus their downstream events). Order matters because
each upstream halter mutates `event.payload['status']` and each
downstream gate short-circuits on `status != 'success'`. Cheapest checks
run first so an early halt skips the expensive Opus review.

| # | Subscriber                              | Listens on             | Patch ID    | Emits / mutates                                                  |
|---|-----------------------------------------|------------------------|-------------|------------------------------------------------------------------|
| 1 | `stage5_completeness_subscriber`        | `WORKER_COMPLETED`     | **S**       | Auto-stages + commits uncommitted Stage 5 residue                |
| 2 | `build_check_subscriber`                | `WORKER_COMPLETED`     | **N**       | Halts via `payload['status']='build_failed'` + `HUMAN_QUERY`     |
| 3 | `deletion_safety_subscriber`            | `WORKER_COMPLETED`     | **C**       | Halts via `payload['status']='unsafe_deletion'` + `HUMAN_QUERY`  |
| 4 | `code_review_subscriber`                | `WORKER_COMPLETED`     | (E5 + Q)    | Emits `CODE_REVIEW_VERDICT`. Patch Q embedded: downgrades approve→reject on oversize diff. |
| 5 | `security_review_subscriber`            | `CODE_REVIEW_VERDICT`  | **X**       | On approve+critical: spawns 4-hunter `/bmad-security-review`. BLOCK mutates verdict→reject. Emits `SECURITY_REVIEW_PASSED` (audit). |
| 6 | `merge_to_integration_subscriber`       | `CODE_REVIEW_VERDICT`  | (W4 + R + W)| ff-merge. Patch R embedded: auto-commits residue before merge. Patch W embedded: rejects file-list scope violations. |
| 7 | `quarterly_sweep_subscriber`            | `CODE_REVIEW_VERDICT`  | (E9)        | Periodic deferred-work audit                                     |

### Why this order

- **S first** — Stage 5 residue must be committed BEFORE build_check /
  deletion_safety inspect the worktree, otherwise an uncommitted file
  is silently lost when the worktree is cleaned post-merge.
- **N second** — pytest/ruff is the cheap fail-fast; broken build skips
  the ~$15 Opus review.
- **C third** — deletion scan is also cheap and a halt here mutates
  `WORKER_COMPLETED` BEFORE code_review fires.
- **code_review fourth** — the only gate that costs real money. Patch Q
  (diff size) lives INSIDE this subscriber, not as a separate one,
  because it post-processes the same verdict.
- **security_review fifth** — listens on `CODE_REVIEW_VERDICT`, only
  fires when verdict is approve AND story is security-critical. BLOCK
  mutates verdict in-place so merge naturally skips.
- **merge sixth** — gates on `verdict=approve`. Patch R + Patch W live
  INSIDE this subscriber for the same reason as Patch Q lives inside
  code_review.
- **quarterly_sweep last** — pure audit, ignores verdict.

### Halt-via-payload-mutation contract

Subscribers do not raise to halt. Instead they:

1. Emit a `HUMAN_QUERY` event with diagnostic payload.
2. Mutate `event.payload` to set a non-success status (e.g.
   `status='build_failed'`, `status='unsafe_deletion'`) or to change a
   verdict (`verdict='reject'`).
3. Return normally.

Downstream subscribers MUST inspect that status / verdict at entry and
return early if non-success. This contract keeps the event bus
exception-free in normal halting and makes each halt observable as a
distinct `HUMAN_QUERY` payload.

## Patch ID → file map

| Patch | bash anchor in runner.sh | Python module                                                | Policy YAML                            |
|-------|--------------------------|--------------------------------------------------------------|----------------------------------------|
| H     | lines 91-127             | `agent/safety/worker_spawn.py::BMAD_WORKER_TIMEOUT_SEC`      | (constant, no YAML)                    |
| C     | smart-deletion block     | `runtime/deletion_safety.py`                                 | `skills/policy/deletion-safety.yaml`   |
| N     | stage 5.5 (~589-695)     | `runtime/build_check.py`                                     | `skills/policy/build-check.yaml`       |
| Q     | ~821+ pre-merge          | `runtime/diff_size_gate.py` (embedded in `code_review_subscriber`) | `skills/policy/diff-size-gate.yaml` |
| R     | ~821-848 auto-stage      | `runtime/commit_recovery.py` (embedded in `merge_to_integration_subscriber`) | (shared with Q)        |
| S     | ~566-580 stage 5 commit  | `runtime/stage5_completeness.py`                             | `skills/policy/stage5-completeness.yaml`|
| W     | Odyssey skill_improvement_patch_W_candidate.md | `runtime/file_list_parser.py` + scope check in `merge_to_integration_subscriber` | `skills/policy/code-review-gates.yaml::file_list_allow_list` |
| X     | conditional bmad-security-review invoke | `runtime/security_review.py`                                 | `skills/policy/security-review.yaml`   |

## Trigger configuration — security_review (Patch X)

`security_review_subscriber` is conditional. Triggers, checked in order
(first match wins, no else-clause):

1. **frontmatter flag** — story file has `security_critical: true` in
   YAML frontmatter. Manual override, always wins.
2. **epic** — extracted from the first digit segment of `story_id` (e.g.
   `4.1` → epic 4). Critical epics list lives in
   `skills/policy/security-review.yaml::triggers.epics` (default:
   `[3, 4, 5, 7, 9, 10]` — auth/billing/RLS/audit/AI/crypto).
3. **spec keyword** — case-insensitive match against the story body for
   any keyword in `triggers.keywords` (default: `auth`, `jwt`, `rls`,
   `dpa`, `crypto`, `billing`, `pii`, `audit`, `hmac`, `argon`,
   `session`).
4. **diff keyword** — same keyword list, matched against the git diff
   for the worker's commits.

If none match → `SECURITY_REVIEW_PASSED` with `reason="not_security_critical"`
is emitted as an audit trail (so absence of security review is
deliberate and observable, not a silent skip).

### Example: tuning the trigger list

```yaml
# skills/policy/security-review.yaml
triggers:
  epics: [3, 4, 5, 7, 9, 10]
  keywords:
    - auth
    - jwt
    - rls
    - session
    # project-specific additions:
    - tenant_id
    - encryption_at_rest
```

Project-level overrides go in the target's
`<target>/.claude/skills/policy/security-review.yaml` (overlay on the
embedded default).

## Verdicts — security_review (Patch X)

The 4-hunter pipeline runs 4 parallel `claude -p` calls (Injection /
Auth Bypass / Crypto / Data Leak) and aggregates:

- **APPROVE** — no findings. Verdict left as `approve`, merge proceeds.
- **MERGE-WITH-FIXES** — non-blocking findings. Verdict left as
  `approve`, findings appended to `gate_reasons` for human follow-up.
- **BLOCK** — at least one blocking finding. Verdict mutated to
  `reject`, blocking findings appended to `gate_reasons`, `HUMAN_QUERY`
  emitted, merge subscriber skips on entry check.

## Customize.toml → policy.yaml mapping

The Odyssey `skill.customize.toml` blocks moved into per-patch YAMLs:

| Odyssey customize.toml block       | bmad-orchestrator YAML                  |
|------------------------------------|------------------------------------------|
| `[migration_patterns]`             | `skills/policy/deletion-safety.yaml`     |
| `[build_check]`                    | `skills/policy/build-check.yaml`         |
| `[diff_size]`                      | `skills/policy/diff-size-gate.yaml`      |
| `[stage5_completeness]`            | `skills/policy/stage5-completeness.yaml` |
| `[security_review]`                | `skills/policy/security-review.yaml`     |
| (file list allow-list — Patch W)   | `skills/policy/code-review-gates.yaml`   |
| `[retry_policy]`                   | `skills/policy/retry-policy.yaml`        |
| `[cost_tuning]`                    | `skills/policy/cost-tuning.yaml`         |

All 8 YAMLs load cleanly at orchestrator startup; failure to load any
of them aborts the pilot (no silent default fallback — see
`test_all_bundled_policy_yamls_load_without_error` in
`tests/test_canonical_patches_p6.py`).

## EventType inventory

`runtime/event_loop.py::EventType` now has 17 members. New events
added by this initiative:

- `BUILD_CHECK_FAILED` (P2 — Patch N audit)
- `UNSAFE_DELETION_DETECTED` (P1 — Patch C audit)
- `STAGE5_COMMIT_RECOVERED` (P3 — Patch S audit)
- `DIFF_SIZE_EXCEEDED` (P3 — Patch Q audit)
- `SECURITY_REVIEW_PASSED` (P5 — Patch X audit on approve path)

The audit events are emit-only; no current subscriber listens to them.
They exist so an external collector (or future retrospective skill) can
reconstruct the gate decisions for a pilot run from the event log alone.

## Coverage delta

- Before initiative: 5/22 patches covered (H wrong default, partial Q/R/W).
- After P6 merge: 13/22 patches covered (H/C/N/Q/R/S/W/X + the 5
  pre-existing).
- Wrong default H fixed (1800s vs 86400s).
- ~120 new regression tests (1034 → 1160 PASS).

The remaining 9 patches (A/B/D/E/F/G/I/J/K) are non-critical for pilot
safety; backlog item in `project_lesson_canonical_bmad_chain_gaps.md`.

## Test layout

Each P-session shipped its own test file:

- `tests/test_canonical_patches_p1.py` — Patch H + Patch C
- `tests/test_canonical_patches_p2.py` — Patch N
- `tests/test_canonical_patches_p3.py` — Patches Q / R / S
- `tests/test_canonical_patches_p4.py` — Patch W
- `tests/test_canonical_patches_p5.py` — Patch X
- `tests/test_canonical_patches_p6.py` — E2E + integration (this session)

The P6 E2E tests wire all 7 subscribers in canonical order, monkeypatch
`_spawn_code_review_worker` (returns fixture JSONL) and
`_ff_merge_to_integration` (returns stub SHA), and exercise:

- happy path (security-critical + non-critical)
- build_check halt
- deletion_safety halt
- code_review reject
- security_review BLOCK

Each test asserts the full event sequence + verifies the merge
subscriber's fast-forward call count matches the expected gate
outcome.
