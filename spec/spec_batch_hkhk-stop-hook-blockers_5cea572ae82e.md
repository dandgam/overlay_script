# Batch Spec: hkhk-stop-hook-blockers

<!-- batch_id: batch-hkhk-stop-hook-blockers-5cea572ae82e -->
<!-- generated: 2026-05-27T02:38:57+07:00 -->
<!-- avg_pairwise_cohesion: 0.7333 -->
<!-- pipeline_recommended: batch-cycle -->
<!-- q_ids: Q-260525-HKQM2, Q-260525-HKQM3, Q-260525-HKQM5, Q-260525-HKQM6 -->

## Batch Summary

**Theme:** hkhk-stop-hook-blockers  
**Q-NNN count:** 4  
**Avg pairwise cohesion:** 0.7333 (floor ≥0.5 ✓)  
**Clusters:** 1  
**Generated:** 2026-05-27T02:38:57+07:00  

## Included Q-NNN

### Q-260525-HKQM2: Stop hook grep schema mismatch с real Claude Code transcript

Stop hook grep schema mismatch с real Claude Code transcript · 🐛 · `phase:3` `priority:critical-for-flag-on` `bucket:parked-hkhk-flag-on-blockers` `parent:Q-260525-HKHK` · Real JSONL формат `{"message":{"content":[{"type":"tool_use","name":"Skill","input":{"skill":"..."}}]}}` отличается от test fixt

### Q-260525-HKQM3: Stop hook grep false-positive от assistant text

Stop hook grep false-positive от assistant text · 🐛 · `phase:3` `priority:critical-for-flag-on` `bucket:parked-hkhk-flag-on-blockers` `parent:Q-260525-HKHK` · `grep '"skill":"888-persona-'` ловит assistant text content с этой строкой (prose mentioning persona names). Fix: jq-scoped match на `type:to

### Q-260525-HKQM5: Stop hook perf на huge transcript

Stop hook perf на huge transcript · 🐛 · `phase:3` `priority:important-for-flag-on` `bucket:parked-hkhk-flag-on-blockers` `parent:Q-260525-HKHK` · `tail -n 5000` на 200MB rotated transcript через NFS → >5s timeout. Fix: stat size, skip >50MB; tail -c 2M первым. ~25 мин.

### Q-260525-HKQM6: Stop hook transcript rotation gap

Stop hook transcript rotation gap · 🐛 · `phase:3` `priority:important-for-flag-on` `bucket:parked-hkhk-flag-on-blockers` `parent:Q-260525-HKHK` · После rotation `transcript_path` указывает на свежий файл, last persona Skill в rotated chunk invisible → silent fail. Fix: scan sibling `transcript-*.jso

## Pairwise Cohesion Scores

| Q-NNN A | Q-NNN B | Score |
|---------|---------|-------|
| Q-260525-HKQM2 | Q-260525-HKQM3 | 0.8700 |
| Q-260525-HKQM2 | Q-260525-HKQM5 | 0.7000 |
| Q-260525-HKQM2 | Q-260525-HKQM6 | 0.7000 |
| Q-260525-HKQM3 | Q-260525-HKQM5 | 0.6500 |
| Q-260525-HKQM3 | Q-260525-HKQM6 | 0.7800 |
| Q-260525-HKQM5 | Q-260525-HKQM6 | 0.7000 |

## Implementation Notes

This batch was composed via `compose-batch.sh --theme hkhk-stop-hook-blockers` (Q-260526-TBAT).
Run as a single batch-cycle session covering all 4 Q-NNN above.
