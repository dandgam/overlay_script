# Batch Spec: comp2

<!-- batch_id: batch-comp2-b08eeffade93 -->
<!-- generated: 2026-05-26T22:43:14+07:00 -->
<!-- avg_pairwise_cohesion: 0.606 -->
<!-- pipeline_recommended: batch-cycle -->
<!-- q_ids: Q-260526-COMP2-SEM, Q-260526-COMP2-DAG, Q-260526-COMP2-PRV, Q-260526-COMP2-PAR, Q-260526-COMP2-RSC -->

## Batch Summary

**Theme:** comp2  
**Q-NNN count:** 5  
**Avg pairwise cohesion:** 0.6060 (floor ≥0.5 ✓)  
**Clusters:** 1  
**Generated:** 2026-05-26T22:43:14+07:00  

## Included Q-NNN

### Q-260526-COMP2-SEM: Pre-cluster semantic LLM — замена keyword frequency в cmbm-scan

Pre-cluster semantic LLM — замена keyword frequency в cmbm-scan · `phase:1` `priority:critical` `source:Q-260526-COMP2-analyst-§4eq` `bucket:active` `type:enhancement-M` `status:open · pending architect` `effort:~1 сессия (M-tier)` `dep:—` `parent:Q-260526-COMP2` `security_critical:false`       **Чт

### Q-260526-COMP2-DAG: File-overlap DAG calculator

File-overlap DAG calculator · `phase:1` `priority:critical` `source:Q-260526-COMP2-analyst-§4eq` `bucket:active` `type:enhancement-M` `status:open · pending architect` `effort:~1-2 сессии (M-tier)` `dep:—` `parent:Q-260526-COMP2` `security_critical:false`       **Что не так:** нет понимания «трогают

### Q-260526-COMP2-PRV: Cohesion preview в scan — отсекать темы с прогнозом <0.5

Cohesion preview в scan — отсекать темы с прогнозом <0.5 · `phase:1` `priority:critical` `source:Q-260526-COMP2-analyst-§4eq` `bucket:active` `type:enhancement-S` `status:open · dep COMP2-SEM` `effort:~30 мин (S-tier)` `dep:Q-260526-COMP2-SEM` `parent:Q-260526-COMP2` `security_critical:false`       

### Q-260526-COMP2-PAR: Parallelism marker в spec + executor read

Parallelism marker в spec + executor read · `phase:1` `priority:critical` `source:Q-260526-COMP2-analyst-§4eq` `bucket:active` `type:enhancement-M` `status:open · dep COMP2-DAG` `effort:~1 сессия (M-tier)` `dep:Q-260526-COMP2-DAG` `parent:Q-260526-COMP2` `security_critical:false`       **Что не так:

### Q-260526-COMP2-RSC: Re-scan dedupe — skip уже-сгруппированные Q-NNN

Re-scan dedupe — skip уже-сгруппированные Q-NNN · `phase:1` `priority:critical` `source:Q-260526-COMP2-analyst-§4eq` `bucket:active` `type:enhancement-S` `status:open · dep COMP2-SEM` `effort:~20 мин (S-tier)` `dep:Q-260526-COMP2-SEM` `parent:Q-260526-COMP2` `security_critical:false`       **Что не 

## Pairwise Cohesion Scores

| Q-NNN A | Q-NNN B | Score |
|---------|---------|-------|
| Q-260526-COMP2-SEM | Q-260526-COMP2-DAG | 0.4500 |
| Q-260526-COMP2-SEM | Q-260526-COMP2-PRV | 0.8000 |
| Q-260526-COMP2-SEM | Q-260526-COMP2-PAR | 0.3800 |
| Q-260526-COMP2-SEM | Q-260526-COMP2-RSC | 0.6500 |
| Q-260526-COMP2-DAG | Q-260526-COMP2-PRV | 0.4200 |
| Q-260526-COMP2-DAG | Q-260526-COMP2-PAR | 0.8700 |
| Q-260526-COMP2-DAG | Q-260526-COMP2-RSC | 0.6200 |
| Q-260526-COMP2-PRV | Q-260526-COMP2-PAR | 0.4500 |
| Q-260526-COMP2-PRV | Q-260526-COMP2-RSC | 0.7200 |
| Q-260526-COMP2-PAR | Q-260526-COMP2-RSC | 0.7000 |

## Implementation Notes

This batch was composed via `compose-batch.sh --theme comp2` (Q-260526-TBAT).
Run as a single batch-cycle session covering all 5 Q-NNN above.
