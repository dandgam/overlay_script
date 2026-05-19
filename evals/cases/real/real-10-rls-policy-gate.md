# Story real-10: Add RLS-policy compliance gate for multi-tenant SQL diffs

**Epic:** orchestrator-compliance
**Status:** ready-for-dev
**Level:** hard
**Tags:** compliance, security-critical, sql

## Acceptance Criteria

1. New `agent/skills/merge-gate-rls/SKILL.md` documents the gate's contract (block merge if SQL diff lacks `tenant_id` predicate in any WHERE/UPDATE/DELETE/INSERT)
2. `runtime/rls_policy_gate.py` parses unified diff hunks for SQL files (`*.sql`, `migrations/**/*.py`) and flags violations
3. Materialized-view DDL must include `WITH (security_invoker = on)` on PG15+ — gate flags missing
4. Integration into `code_review_subscriber`: gate runs AFTER spec/quality stages, BEFORE merge_to_integration; verdict=block stops merge
5. 8 unit tests: parser positives (4) + negatives (2) + integration with subscriber chain (2)
6. New event type `RLS_POLICY_VIOLATION` + inventory tests bumped (+1)
7. mypy + ruff clean; no false positives on existing migration history (regression test pins curated diff fixtures)

## Tasks

- [ ] AC1: Author SKILL.md (rule list + examples)
- [ ] AC2: Implement diff hunk parser + violation detector
- [ ] AC3: Wire as third merge-gate stage
- [ ] AC4: Add `RLS_POLICY_VIOLATION` to EventType
- [ ] AC5: 8 tests covering parser + subscriber chain
- [ ] AC6: Regression-pin: existing curated migration set parses clean
- [ ] AC7: Bump both inventory tests

## File List

- `src/bmad_orchestrator/agent/skills/merge-gate-rls/SKILL.md`
- `src/bmad_orchestrator/runtime/rls_policy_gate.py`
- `src/bmad_orchestrator/runtime/event_loop.py`
- `src/bmad_orchestrator/agent/run.py`
- `tests/test_rls_policy_gate.py`
- `tests/test_canonical_patches_p6.py`
- `tests/test_s3_runtime.py`

## Dev Notes

Largest case in the suite: parser logic + subscriber integration + new event type
+ inventory bump + skill authoring. Expected 3+ iterations. Security-critical:
RLS bypass is a 152-ФЗ ст.13.11 compliance breach in multi-tenant SaaS.
Compliance: multi-tenant safety baseline (Universal Security Defaults §Multi-tenant queries).
