# Initiative Tracker — Pilot Findings Closure v7 (review-runner root-fix + security_review fallback symmetry)

## Metadata
- **Spec:** spec/spec_pilot_findings_closure_v7.md
- **Integration branch:** integration/pilot_findings_closure_v7
- **Created:** 2026-05-19
- **Bootstrap completed:** 2026-05-19 by /auto-loop-spec-short
- **Scope frozen:** 2026-05-19
- **Runtime:** schedule_wakeup
- **Delay seconds:** 120
- **Auto merge:** false

## Scope Freeze

### In scope
- NEW-21 (P1, КОРЕНЬ): review runner возвращает verdict=error. Root-fix + observability.
- NEW-20 (P1): security_review verdict=error → fallback-путь симметрично NEW-15.
- NEW-22 (P2): `_canonical_status` warning — поля в message-строке.
- methodology-virgil.md §5 — NEW-20/21/22 DONE; ложная гипотеза NEW-22 исправлена.
- Tests target ≥2116 PASS (факт: 2114), mypy/ruff clean на changed files.

### Out of scope (explicit)
- P3 backlog-writer subscriber — отдельная инициатива.
- Phase 4 deploy-elicitation (888-persona-ops 5-field) — после merge v7.
- 3 pre-existing mypy errors (sandbox.py / phase4_subscribers.py / main_merge_token.py) — не из v7.

### Deferred to follow-up initiative
- Полный 3/3-story production pilot — после merge v7 (финальная валидация = один replay на 1 истории).

## Sessions

### Pending
(none)

### Current
(none — initiative complete)

### Completed

- **id:** S2
  **title:** NEW-20 security_review fallback symmetry + NEW-22 unknown_status лог
  **completed:** 2026-05-19 16:52 UTC
  **commit:** 45a6180
  **files_changed:** 7
  **tests_passed:** 2114 (2107 → +7; test_new20 ×5, test_new22 ×2)
  **decisions_made:**
    - NEW-20 fallback переиспользует `parse_runner_review_log` (общий Stage-6 log core) через тонкий wrapper `parse_security_runner_fallback` с маппингом code→security verdict. «Общий helper» = единый log-reader, два verdict-словаря.
    - NEW-20: при отсутствии fallback-сигнала story всё ещё escalate+reject — это conditional (после retry+fallback), human-in-loop; «не безусловно» соблюдено.
    - NEW-22: гипотеза методички опровергнута диагностикой — один callsite, баг в stdlib logging extra= не рендерится. Fix = поля в message-строке, extra= сохранён.
  **deferred_items:**
    - KNOWN_STATUSES не расширялся — реальные нераспознанные статусы из лога #6 не видны (баг их скрывал); после NEW-22 будущие логи покажут raw_status.

- **id:** S1
  **title:** NEW-21 review-runner root-fix (spike + fix)
  **completed:** 2026-05-19 16:45 UTC
  **commit:** 8184366
  **files_changed:** 5
  **tests_passed:** 2107 (2102 → +5; test_new21_review_jsonl.py)
  **decisions_made:**
    - Root cause найден по on-disk артефакту (review worker events.jsonl): EROFS на ~/.claude.json, claude num_turns=0. Гипотеза H3 (read-only HOME), не H1/H2.
    - Fix = isolated_home=True на 4 review-спавнах (тот же механизм, что у работающих dev workers).
    - Observability-фикс: стейдж-функции переведены на 4-tuple (verdict, summary, metrics, jsonl_path); test_phase4 + test_new15 обновлены под сигнатуру.
  **deferred_items:**
    - H2 (формат verdict-строки reviewer'а: markdown `**Verdict:**` может не матчить `_VERDICT_LINE_RE`) — не подтверждён, проверяется финальным replay.
    - Латентный риск: non-overlay `--bind ~/.claude.json` под sandbox — вне scope v7.

## Safety Gates Triggered
(none)

## Blockers / Pauses

- **date:** 2026-05-19 16:52 UTC
  **session:** S2
  **type:** manual_merge_pending
  **detail:** initiative complete on integration/pilot_findings_closure_v7 (S1..S2, NEW-20/21/22). Auto merge=false — user merges manually:
    git checkout main && git merge --no-ff integration/pilot_findings_closure_v7 -m "merge pilot_findings_closure_v7 S1..S2"
  **resolution:** PENDING (user action)

## Decisions Log

- **date:** 2026-05-19
  **session:** bootstrap
  **decision:** S1 (NEW-21) — диагностика-first; фикс scoped после spike
  **rationale:** root cause не доказан; прескриптивный фикс рискует закрыть симптом
  **impact:** S2 NEW-20 переиспользует verdict-extraction из S1

- **date:** 2026-05-19 16:45 UTC
  **session:** S1
  **decision:** NEW-21 root = EROFS на ~/.claude.json (read-only HOME), fix = isolated_home=True
  **rationale:** on-disk review events.jsonl показал claude num_turns=0 + EROFS error; dev workers выживают только из-за isolated_home=True
  **impact:** S2 NEW-20 — security_review тоже получил isolated_home=True в S1; S2 fallback стал страховкой, а не основным путём

## Journal

[2026-05-19 bootstrap] tracker + branches created, 2 sessions planned (S1 NEW-21, S2 NEW-20+22). Runtime=schedule_wakeup, delay=120s, Auto merge=false.
[2026-05-19 16:45 UTC] S1 execution: NEW-21 root найден (EROFS ~/.claude.json, num_turns=0) → isolated_home=True на 4 review-спавнах + jsonl_path observability. Suite 2107 PASS. commit 8184366.
[2026-05-19 16:45 UTC] S1 completed, S2 promoted to Current.
[2026-05-19 16:52 UTC] S2 execution: NEW-20 security_review fallback (parse_security_runner_fallback, симметрия с NEW-15) + NEW-22 unknown_status message-строка. Suite 2114 PASS, mypy/ruff clean. commit 45a6180.
[2026-05-19 16:52 UTC] S2 completed, Pending empty — initiative complete. Auto merge=false → manual_merge_pending.

## Final Report

Initiative: Pilot Findings Closure v7 (review-runner root-fix + security_review fallback symmetry)
Spec: spec/spec_pilot_findings_closure_v7.md
Started: 2026-05-19 (bootstrap)
Completed: 2026-05-19 16:52 UTC
Sessions: 2 planned, 2 executed, 0 buffered
Safety gate trips: 0
Human pauses: 0
Integration branch: integration/pilot_findings_closure_v7
Commits on integration: 8184366 (S1 NEW-21), 45a6180 (S2 NEW-20+22) + 3 tracker commits
Diff vs main: 12 files changed, +744 / -46
Tests: 2102 → 2114 PASS (+12: NEW-21 ×5, NEW-20 ×5, NEW-22 ×2); mypy/ruff clean на changed
Closed: NEW-21 (root — review worker EROFS на ~/.claude.json → isolated_home=True),
        NEW-20 (security_review verdict=error → shared runner-log fallback),
        NEW-22 (unknown_status — поля в message-строке; ложная гипотеза опровергнута)
Recommendation: MERGE TO MAIN — все 3 находки закрыты, suite зелёный. Перед закрытием
        Phase 4 рекомендован один production replay (Antares 1.5) для живой валидации
        что review runner теперь даёт реальный verdict (не error).
Merge hint: git checkout main && git merge --no-ff integration/pilot_findings_closure_v7 -m "merge pilot_findings_closure_v7 S1..S2"
Rollback: git reset --hard backup/pilot_findings_closure_v7-pre-2026-05-19
