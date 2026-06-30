# Batch Spec: eval-quality

<!-- batch_id: batch-eval-quality-c3d8f804a9c6 -->
<!-- generated: 2026-05-31T23:44:57+07:00 -->
<!-- avg_pairwise_cohesion: 0.565 -->
<!-- pipeline_recommended: batch-cycle -->
<!-- q_ids: Q-260523-EVOL, Q-260523-WKRG, Q-260523-CCNG, Q-260523-CMPLX -->
<!-- parallel_groups: [["Q-260523-CCNG"], ["Q-260523-CMPLX"], ["Q-260523-EVOL"], ["Q-260523-WKRG"]] -->
<!-- parallel_safe: true -->
<!-- dag_unknown: [] -->

## Batch Summary

**Theme:** eval-quality  
**Q-NNN count:** 4  
**Avg pairwise cohesion:** 0.5650 (floor ≥0.5 ✓)  
**Clusters:** 3  
**Generated:** 2026-05-31T23:44:57+07:00  

## Included Q-NNN

### Q-260523-EVOL: Eval-on-change — автозапуск evals при правке SKILL.md / templates

Eval-on-change — автозапуск evals при правке SKILL.md / templates · ✨ · `phase:5` `priority:important` `source:audit-quality-gates-2026-05-23` `bucket:active` `type:enhancement` `status:open` `effort:~30 мин` `dep:Q-260523-RGSU` `attachment:cache/audits/quality_gates_small_tier_2026-05-23.md`       

### Q-260523-WKRG: Weekly regression cron (понедельник 9:00) для полного eval-set

Weekly regression cron (понедельник 9:00) для полного eval-set · ✨ · `phase:4` `priority:cosmetic` `source:audit-quality-gates-2026-05-23` `bucket:active` `type:enhancement` `status:pending-user-authorize` `effort:~10 мин` `dep:Q-260523-FRSH` `attachment:cache/audits/quality_gates_small_tier_2026-05

### Q-260523-CCNG: Cyclomatic complexity gate для bash-скриптов 888

Cyclomatic complexity gate для bash-скриптов 888 · ✨ · `phase:5` `priority:important` `source:audit-quality-gates-2026-05-23` `bucket:active` `type:enhancement` `status:open` `effort:~30 мин` `attachment:cache/audits/quality_gates_small_tier_2026-05-23.md`       **Что не так:** скрипты 888 (`gate.sh

### Q-260523-CMPLX: Wire post-factum complexity alert (improver Field 5 ratio >3×) в action

Wire post-factum complexity alert (improver Field 5 ratio >3×) в action · ✨ · `phase:5` `priority:important` `source:audit-quality-gates-2026-05-23` `bucket:active` `type:enhancement` `status:open` `effort:~20 мин` `parent:Q-260519-H9I1` `attachment:cache/audits/quality_gates_small_tier_2026-05-23.m

## Pairwise Cohesion Scores

| Q-NNN A | Q-NNN B | Score |
|---------|---------|-------|
| Q-260523-EVOL | Q-260523-WKRG | 0.7000 |
| Q-260523-EVOL | Q-260523-CCNG | 0.6200 |
| Q-260523-EVOL | Q-260523-CMPLX | 0.5200 |
| Q-260523-WKRG | Q-260523-CCNG | 0.4500 |
| Q-260523-WKRG | Q-260523-CMPLX | 0.4200 |
| Q-260523-CCNG | Q-260523-CMPLX | 0.6800 |

## Implementation Notes

This batch was composed via `compose-batch.sh --theme eval-quality` (Q-260526-TBAT).
Run as a single batch-cycle session covering all 4 Q-NNN above.
