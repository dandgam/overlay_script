# Batch Spec: session-isolation

<!-- batch_id: batch-session-isolation-5d588500a4ec -->
<!-- generated: 2026-05-28T16:03:51+07:00 -->
<!-- avg_pairwise_cohesion: 0.3623 -->
<!-- pipeline_recommended: batch-cycle -->
<!-- q_ids: Q-260526-ICTX, Q-260526-IAPL, Q-260526-IDIS, Q-260526-ICST, Q-260526-IBXL, Q-260526-IADV -->
<!-- parallel_groups: [["Q-260526-IADV"], ["Q-260526-IAPL"], ["Q-260526-IBXL"], ["Q-260526-ICST"], ["Q-260526-ICTX"], ["Q-260526-IDIS"]] -->
<!-- parallel_safe: true -->
<!-- dag_unknown: ["Q-260526-IADV", "Q-260526-IAPL", "Q-260526-IBXL", "Q-260526-ICTX", "Q-260526-IDIS"] -->

## Batch Summary

**Theme:** session-isolation  
**Q-NNN count:** 6  
**Avg pairwise cohesion:** 0.3623 (floor ≥0.5 ✓)  
**Clusters:** 6  
**Generated:** 2026-05-28T16:03:51+07:00  

## Included Q-NNN

### Q-260526-ICTX: ISOL context-pct measurement wiring (с Q-260524-CTXMG или новый context-meter)

ISOL context-pct measurement wiring (с Q-260524-CTXMG или новый context-meter) · `phase:2.5` `priority:important` `source:Q-260526-ISOL-improver-F2-completeness` `bucket:active` `type:enhancement` `status:open · parked` `effort:~20 мин` `dep:Q-260526-ISOL` `parent:Q-260526-BATCH` `security_critical:

### Q-260526-IAPL: ISOL `--apply` mode test (T-ISOL-5: audit log append + idempotency)

ISOL `--apply` mode test (T-ISOL-5: audit log append + idempotency) · `phase:2.5` `priority:cosmetic` `source:Q-260526-ISOL-improver-F2-L5-debt` `bucket:active` `type:test` `status:open · parked` `effort:~15 мин` `dep:Q-260526-ISOL` `parent:Q-260526-BATCH` `security_critical:false`

### Q-260526-IDIS: ISOL dispatcher actual wiring beyond advisory note (PostToolUse hook без блокировки)

ISOL dispatcher actual wiring beyond advisory note (PostToolUse hook без блокировки) · `phase:2.5` `priority:cosmetic` `source:Q-260526-ISOL-improver-F2-SKILL-md-observation` `bucket:active` `type:enhancement` `status:open · parked` `effort:~10 мин` `dep:Q-260526-ISOL` `parent:Q-260526-BATCH` `secur

### Q-260526-ICST: Создать templates/closed-set-taxonomy.md (YAML + python3 stdlib parser pattern)

Создать templates/closed-set-taxonomy.md (YAML + python3 stdlib parser pattern) · `phase:5` `priority:cosmetic` `source:Q-260526-ISOL-improver-F6-L2` `bucket:active` `type:meta-learning` `status:open · parked` `effort:~30 мин` `dep:—` `parent:Q-260526-BATCH` `security_critical:false`

### Q-260526-IBXL: ISOL cross-link к bypass-mode.md (2-persona mini-cycle для umbrella deep-sub-Q)

ISOL cross-link к bypass-mode.md (2-persona mini-cycle для umbrella deep-sub-Q) · `phase:2.5` `priority:cosmetic` `source:Q-260526-ISOL-improver-F6-L1` `bucket:active` `type:docs` `status:open · parked` `effort:~5 мин` `dep:—` `parent:Q-260526-BATCH` `security_critical:false`

### Q-260526-IADV: ISOL cross-link к routing-decision-tree.md (advisory vs blocking hook distinction)

ISOL cross-link к routing-decision-tree.md (advisory vs blocking hook distinction) · `phase:2.5` `priority:cosmetic` `source:Q-260526-ISOL-improver-F6-L3` `bucket:active` `type:docs` `status:open · parked` `effort:~5 мин` `dep:—` `parent:Q-260526-BATCH` `security_critical:false`

## Pairwise Cohesion Scores

| Q-NNN A | Q-NNN B | Score |
|---------|---------|-------|
| Q-260526-ICTX | Q-260526-IAPL | 0.3389 |
| Q-260526-ICTX | Q-260526-IDIS | 0.4050 |
| Q-260526-ICTX | Q-260526-ICST | 0.3000 |
| Q-260526-ICTX | Q-260526-IBXL | 0.3304 |
| Q-260526-ICTX | Q-260526-IADV | 0.3333 |
| Q-260526-IAPL | Q-260526-IDIS | 0.3700 |
| Q-260526-IAPL | Q-260526-ICST | 0.3350 |
| Q-260526-IAPL | Q-260526-IBXL | 0.4050 |
| Q-260526-IAPL | Q-260526-IADV | 0.3737 |
| Q-260526-IDIS | Q-260526-ICST | 0.3292 |
| Q-260526-IDIS | Q-260526-IBXL | 0.3560 |
| Q-260526-IDIS | Q-260526-IADV | 0.4333 |
| Q-260526-ICST | Q-260526-IBXL | 0.3280 |
| Q-260526-ICST | Q-260526-IADV | 0.3304 |
| Q-260526-IBXL | Q-260526-IADV | 0.4667 |

## Implementation Notes

This batch was composed via `compose-batch.sh --theme session-isolation` (Q-260526-TBAT).
Run as a single batch-cycle session covering all 6 Q-NNN above.
