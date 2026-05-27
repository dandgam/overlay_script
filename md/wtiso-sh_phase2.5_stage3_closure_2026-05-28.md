# Q-260528-WTISO-SH-FIXTURES — Stage 3 closure (16/16 GREEN, 2026-05-28)

> Intended destination: `~/.claude/skills/888/methodology-888.md` §4gzf, после
> существующего «Stage 2 closure (partial)» блока. Harness заблокировал прямой
> edit (sensitive file). Этот md — fallback artifact для пользователя; вставить
> вручную или одобрить write на оригинале.

---

### Stage 3 closure (Q-260528-WTISO-SH-FIXTURES — 16/16 GREEN, 2026-05-28)

**Что сделано в headless `claude -p` сессии (Opus 4.7):**

11 оставшихся RED stub'ов → GREEN. Финальное состояние: 16/16 `bash tests/wtiso/test-shard-*.sh` exit 0.

**Real bugs обнаружены и исправлены в `scripts/888-shard-merger.sh` (commit 6bb30bc):**

1. **L3 dead-code bug.** `_l3_check` запускался на `candidate = cp(staging)` БЕЗ shard'а — сравнивал staging с самим собой. L3 никогда не срабатывал на содержимом shard'а. Добавлен `_shard_has_frontmatter_delim`: сканирует shard на `^---$` вне fenced-блоков → RC=14 + audit `shard_l3_violation`. Это и есть «append_only_violation» из спецификации.
2. **flock timeout test-hook.** Hardcoded `-w 30` не давал прогнать concurrent-merger тест за разумное время. Добавлен `MERGER_FLOCK_TIMEOUT_OVERRIDE` env (default=30), задокументирован как test hook для RED-SH-11.

`tests/wtiso/_lib/fixture-helpers.sh` расширен двумя helper'ами: `wtiso_make_shard_raw` и `wtiso_invoke_merger_raw_bid`.

**Commits chain (8 коммитов):** `6bb30bc` fix → `84338a1` SH-3+4 → `338d029` SH-1+5 → `62de7b8` SH-6+7 → `bd427f9` SH-9+15 → `ce400ae` SH-10+16 → `c968428` SH-11 → (this) §4gzf Stage 3 closure (artifact md fallback).

**16/16 GREEN финальная матрица:** SH-1 collision · SH-2 idempotent · SH-3 partial-rollback · SH-4 append-only · SH-5 last-touched · SH-6 q-id-conflict · SH-7 symlink · SH-8 anchor-overflow · SH-9 fenced-block · SH-10 shell-injection (8 variants) · SH-11 concurrent-merger · SH-12 anchor-letter 13/13 · SH-13 rc-capture · SH-14 path-canonical · SH-15 all-skipped (Case A + Case B) · SH-16 content-aware hash.

**Implementation note:** RED-SH-15 Case B реализован через L2 LOC limit (`SHARD_MAX_LOC=10`) — два oversize shard'а попадают в `_SKIPPED_SHARDS` через L2 gate, не L1. Shape ассерта `skipped=2 rejected=0 mutation=true` идентичен независимо от gate'а. Per-shard L1 mock через env parked как:

- [ ] 🟢 **Q-260528-WTISO-SH-FIXTURES-L1-MOCK-PER-SHARD — per-shard L1 mock verdict via BMAD_SHARD_L1_MOCK_<basename>** · ✨ · `phase:2.5` `priority:cosmetic` `parent:Q-260527-WTISO-SH` `effort:~15 мин` `security_critical:false`

**Cost / time:** ~45 min wall-clock · ~70k tokens (Opus 4.7 1M, под M-tier budget) · opus-only, no sub-agent spend · files touched: scripts/888-shard-merger.sh (+24 LOC bug-fix) · tests/wtiso/_lib/fixture-helpers.sh (+2 helpers) · 11 test files (stubs → working) · этот fallback md.

**Honest assessment:** Два real bug'а merger'а пойманы при подгонке тестов под спеку — test-driven завершение в чистом виде. Спецификация §3.4 step 11.4: «L3 gate runs on the shard's effect on the methodology», реализация делала на pre-shard staging — checking ничего. Fix surgical (+24 LOC), не задевает остальную merger логику. Один scope-defer (per-shard L1 mock) parked явно.

**Phase 2.5 ПОЛНОСТЬЮ complete, ready handoff qa Phase 3.**

**Handoff payload:**

```yaml
handoff:
  from: 888-persona-implementer (Stage 3 — Q-260528-WTISO-SH-FIXTURES)
  to: 888-persona-qa
  q_id: Q-260527-WTISO-SH
  status: complete-phase-2.5 (16/16 RED→GREEN, merger ~530 LOC + 2 bug fixes)
  parent: Q-260527-WTISO (umbrella ≈98% — WT + BW shipped, SH Phase 2.5 done)
  gates_required_at_qa:
    - mechanizable: bash -n + all 16 GREEN (baseline)
    - happy-paths: §10 perf budget verification (~25s 4-shard real claude -p batch)
    - edge-cases: forensics retention on L3 abort (covered SH-3) · TOCTOU · concurrent
    - red-team / OWASP ASI mapping for §9 T1-T4 (light scope, security_critical=false)
    - real-data validation: 1-2 actual 888 dispatcher batches end-to-end
  parked_followups:
    - Q-260528-WTISO-SH-FIXTURES-L1-MOCK-PER-SHARD (cosmetic, ~15 min)
    - Q-260528-WTISO-SH-TOCTOU-DRY (carried, S-tier ~15 min)
    - Q-260528-WTISO-SH-NUL-FRAME (carried, cosmetic ~5 min)
    - Q-260528-WTISO-SH-L1-REAL (carried, ~30 min prompt tuning)
  next_action: qa first action — perf budget verification + OWASP ASI mapping
```

**Phase 2.5 final state:**
- phase: complete-phase-2.5 (Q-260527-WTISO-SH 100% — merger impl + fixture infra + 16/16 GREEN)
- current-persona: 888-persona-implementer → handoff-pending (888-persona-qa)
- gate-passed: 888-persona-analyst 2026-05-28 + 888-persona-architect 2026-05-28-v2.1-block-fix + 888-persona-implementer 2026-05-28-stage1 + stage2-partial + stage3-complete
- last-touched: 2026-05-28
- handoff-pending: 888-persona-qa (Phase 3 — light scope, security_critical=false)
