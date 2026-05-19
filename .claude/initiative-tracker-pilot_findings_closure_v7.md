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
- NEW-21 (P1, КОРЕНЬ): review runner возвращает verdict=error (пустой review_jsonl). Диагностический spike → root-fix. Observability: `jsonl_path` в `_MergeGateStageResult`.
- NEW-20 (P1): security_review verdict=error → fallback-путь симметрично NEW-15; escalate-story только при отсутствии сигнала. Общий helper `parse_review_runner_output`.
- NEW-22 (P2): `_canonical_status` warning — поля story_id/raw_status/layout в message-строке, не только в `extra`.
- methodology-virgil.md §5 — пометить NEW-20/21/22 DONE; исправить ложную гипотезу NEW-22.
- Tests target ≥2116 PASS, mypy/ruff clean на changed files.

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

- **id:** S2
  **title:** NEW-20 security_review fallback symmetry + NEW-22 unknown_status лог
  **surface:** backend-python
  **spec_section:** §2 (NEW-20), §3 (NEW-22)
  **depends_on:** [S1]
  **acceptance:**
    - security_review verdict=error → fallback-путь (runner-вывод → holistic verdict); escalate-story только без сигнала
    - общий helper `parse_review_runner_output` переиспользуется code_review + security_review
    - `bmad_format_unknown_status` warning содержит story_id/raw_status/layout в тексте сообщения
    - +~7 tests PASS, mypy/ruff clean на changed
    - methodology §5 NEW-20/22 → DONE, ложная гипотеза NEW-22 исправлена
  **safety_gates:**
    - L3 branch isolation — работа только на integration/pilot_findings_closure_v7
  **checkpoint:** false
  **estimated_retries_allowed:** 3
  **started:** 2026-05-19 16:36 UTC
  **workflow:** workflows/backend-python.md
  **retry_count:** 0
  **worker_branches:** []

### Completed

- **id:** S1
  **title:** NEW-21 review-runner root-fix (spike + fix)
  **completed:** 2026-05-19 16:36 UTC
  **commit:** 8184366
  **files_changed:** 5
  **tests_passed:** 2107 (2102 → +5; test_new21_review_jsonl.py)
  **decisions_made:**
    - Root cause найден по on-disk артефакту (review worker events.jsonl), без дорогого replay-spike: EROFS на ~/.claude.json, claude num_turns=0. Гипотеза H3 (read-only HOME), не H1/H2.
    - Fix = isolated_home=True на 4 review-спавнах (тот же механизм, что у работающих dev workers). Не чинил non-overlay --bind путь sandbox — dev workers его не триггерят, вне scope NEW-21.
    - Observability-фикс: стейдж-функции переведены на 4-tuple (verdict, summary, metrics, jsonl_path); 2 существующих теста (test_phase4, test_new15) обновлены под новую сигнатуру в том же коммите.
  **deferred_items:**
    - H2 (формат verdict-строки reviewer'а: markdown `**Verdict:**` может не матчить `_VERDICT_LINE_RE`) — не подтверждён, проверяется финальным replay; если всплывёт — отдельный мелкий фикс regex.
    - Латентный риск: non-overlay `--bind ~/.claude.json` под sandbox может быть ro и для dev workers при max_parallel=1 — вне scope v7.

## Safety Gates Triggered
(none)

## Blockers / Pauses
(none)

## Decisions Log

- **date:** 2026-05-19
  **session:** bootstrap
  **decision:** S1 (NEW-21) — диагностика-first; фикс scoped после spike
  **rationale:** root cause не доказан; прескриптивный фикс рискует закрыть симптом
  **impact:** S2 NEW-20 переиспользует verdict-extraction из S1

- **date:** 2026-05-19 16:36 UTC
  **session:** S1
  **decision:** NEW-21 root = EROFS на ~/.claude.json (read-only HOME), fix = isolated_home=True
  **rationale:** on-disk review events.jsonl показал claude num_turns=0 + EROFS error; dev workers выживают только из-за isolated_home=True
  **impact:** S2 NEW-20 — security_review тоже получил isolated_home=True в S1; S2 теперь только про error-handling fallback (страховка поверх рабочего раннера)

## Journal

[2026-05-19 bootstrap] tracker + branches created, 2 sessions planned (S1 NEW-21, S2 NEW-20+22). Runtime=schedule_wakeup, delay=120s, Auto merge=false.
[2026-05-19 16:36 UTC] S1 execution: NEW-21 root найден (EROFS ~/.claude.json, num_turns=0) → isolated_home=True на 4 review-спавнах + jsonl_path observability. Suite 2107 PASS, mypy/ruff clean. commit 8184366.
[2026-05-19 16:36 UTC] S1 completed, S2 promoted to Current.

## Final Report (populated on last session completion)

(empty)
