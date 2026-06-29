# Batch Spec: cbpm-ex-single (Q-260527-CBPM-EX via PERMBLK-WIRE)

<!-- batch_id: batch-cbpm-ex-single-2026-05-27 -->
<!-- generated: 2026-05-27T22:50:00+07:00 -->
<!-- pipeline_recommended: full-cycle -->
<!-- q_ids: Q-260527-CBPM-EX -->
<!-- self_modify: true -->

## Batch Summary

**Theme:** cbpm-ex-single (Phase 3+4+5 completion для single self-modify Q-NNN through PERMBLK-WIRE)
**Q-NNN count:** 1
**Pipeline:** full-cycle через wired self-modify path

## Included Q-NNN

### Q-260527-CBPM-EX: 888-batch.sh executor consumption of parallel_groups marker

**Status:** Phase 2.5 implementer DONE — worker output committed на `integration/Q-260527-CBPM-EX` HEAD `9602f49` (variant B PERMBLK 2026-05-27).

**Brief:** §4gn methodology — extend `888-batch.sh` to consume `parallel_groups` marker emitted by CBPM (Q-260527-CBPM-done) → spawn N concurrent claude -p workers per group within batch.

**Design:** §4go — 8 ADRs (ADR-001..007 + ADR-001-EMPTY + ADR-006-CHUNK), Iron Law 5 RED tests T1-T5.

**Phase 2.5 result (worker output 9602f49):**
- `skills/888/scripts/888-batch.sh` extended +153/-15 LOC
- `skills/888/tests/cbpm-ex/_lib.sh` + T1-T5 (worker self-reported GREEN)
- `skills/888/scripts/evals/cbpm-ex-baseline.sh` + `cbpm-ex-replay.sh`
- 2 bonus bug fixes: `batch_event_log` brace bug (Q-260527-EVSC) + `_audit_update` `$$` race
- ADR-002 deviation: pragmatic flock-guarded shared JSONL (documented, not per-worker file)

**Pending (this batch run):**
- Phase 3 qa: independent RED test rerun + 3-4 review-hunters (bmad-code-review, bmad-review-edge-case-hunter, bmad-security-review)
- Phase 4 ops: diff-view confirm (headless behavior to verify) + merge integration/Q-260527-CBPM-EX → main + rollback flag setup
- Phase 5 improver: retro Mode A + queue close Mode C + followups parked

**Self-modify flag:** scope = `~/.claude/skills/888/scripts/888-batch.sh` + sibling test/eval files → requires `BMAD_888_SELF_MODIFY_REPO=$HOME/.claude` for spawn through PERMBLK-WIRE path.

## Execution instructions

```bash
# Fresh interactive session (TTY for any halts):
export BMAD_888_SELF_MODIFY_REPO=$HOME/.claude
bash ~/.claude/skills/888/scripts/888-batch.sh run \
    --from-spec /home/server/bmad-orchestrator/spec/spec_batch_cbpm-ex_single.md \
    --max-budget-usd 3.00
```

Worker spawned with `/888 продолжить Q-260527-CBPM-EX` prompt → dispatcher reads methodology phase:2.5-done-in-branch → routes to Phase 3 qa → auto-handoff through Phase 4 ops + Phase 5 improver.
