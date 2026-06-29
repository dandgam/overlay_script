# Batch Spec: wtiso-bw-fx

<!-- batch_id: batch-wtiso-bw-fx-8a6ed0ad0838 -->
<!-- generated: 2026-05-31T18:43:42+07:00 -->
<!-- avg_pairwise_cohesion: 0.5367 -->
<!-- pipeline_recommended: batch-cycle -->
<!-- q_ids: Q-260527-WTISO-BW-FX3, Q-260527-WTISO-BW-FX5, Q-260527-WTISO-BW-FX-RUNNER -->
<!-- parallel_groups: [["Q-260527-WTISO-BW-FX-RUNNER"], ["Q-260527-WTISO-BW-FX3"], ["Q-260527-WTISO-BW-FX5"]] -->
<!-- parallel_safe: false -->
<!-- dag_unknown: ["Q-260527-WTISO-BW-FX-RUNNER", "Q-260527-WTISO-BW-FX3", "Q-260527-WTISO-BW-FX5"] -->

## Batch Summary

**Theme:** wtiso-bw-fx  
**Q-NNN count:** 3  
**Avg pairwise cohesion:** 0.5367 (floor ≥0.5 ✓)  
**Clusters:** 3  
**Generated:** 2026-05-31T18:43:42+07:00  

## Included Q-NNN

### Q-260527-WTISO-BW-FX3: WTISO-BW-FX3: M1 baseline real-data capture

WTISO-BW-FX3: M1 baseline real-data capture · ✨ · `phase:1` `priority:P2-quality` `source:Q-260527-WTISO-BW-S6-bmad-code-review` `bucket:active` `type:enhancement-S` `status:open · non-blocking` `effort:~30 мин S-tier` `dep:none` `parent:Q-260527-WTISO-BW` `security_critical:false`

### Q-260527-WTISO-BW-FX5: WTISO-BW-FX5: §5.1 ALLOW+CGROUP audit emission + REQUIRE_SANDBOX vs ALLOW_DOCKER_GROUP conflict guard

WTISO-BW-FX5: §5.1 ALLOW+CGROUP audit emission + REQUIRE_SANDBOX vs ALLOW_DOCKER_GROUP conflict guard · ✨ · `phase:1` `priority:P2-audit` `source:Q-260527-WTISO-BW-S6-bmad-code-review` `bucket:active` `type:enhancement-S` `status:open · non-blocking` `effort:~30 мин S-tier` `dep:none` `parent:Q-2605

### Q-260527-WTISO-BW-FX-RUNNER: WTISO-BW-FX-RUNNER: test runner script с env-var split (AC6 без opt-in, остальные с opt-in)

WTISO-BW-FX-RUNNER: test runner script с env-var split (AC6 без opt-in, остальные с opt-in) · ✨ · `phase:1` `priority:P2-quality` `source:Q-260527-WTISO-BW-§4ge-qa-discovery` `bucket:active` `type:enhancement-S` `status:open · non-blocking` `effort:~15 мин S-tier` `dep:none` `parent:Q-260527-WTISO-B

## Pairwise Cohesion Scores

| Q-NNN A | Q-NNN B | Score |
|---------|---------|-------|
| Q-260527-WTISO-BW-FX3 | Q-260527-WTISO-BW-FX5 | 0.3800 |
| Q-260527-WTISO-BW-FX3 | Q-260527-WTISO-BW-FX-RUNNER | 0.6800 |
| Q-260527-WTISO-BW-FX5 | Q-260527-WTISO-BW-FX-RUNNER | 0.5500 |

## Implementation Notes

This batch was composed via `compose-batch.sh --theme wtiso-bw-fx` (Q-260526-TBAT).
Run as a single batch-cycle session covering all 3 Q-NNN above.
