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
- NEW-21 (P1, КОРЕНЬ): review runner возвращает verdict=error (пустой review_jsonl). Диагностический spike (H1 path mismatch / H2 reviewer не пишет verdict / H3 права) → root-fix. Observability-фикс: `jsonl_path` в `_MergeGateStageResult`, populate `handle_jsonl_str`.
- NEW-20 (P1): security_review verdict=error → fallback-путь симметрично NEW-15 (чтение runner-вывода → holistic verdict); escalate-story только при отсутствии сигнала. Общий helper `parse_review_runner_output`.
- NEW-22 (P2): `_canonical_status` warning (`bmad_format.py:246`) — поля story_id/raw_status/layout в message-строке, не только в `extra`. Свериться с `KNOWN_STATUSES`.
- methodology-virgil.md §5 — пометить NEW-20/21/22 DONE; исправить ложную гипотезу NEW-22 («4 вызова из top-level scan» → один callsite).
- Tests target ≥2116 PASS (2102 → ~+14), mypy/ruff clean на changed files.

### Out of scope (explicit)
- P3 backlog-writer subscriber — отдельная инициатива.
- Phase 4 deploy-elicitation (888-persona-ops 5-field) — после merge v7.
- 3 pre-existing mypy errors (sandbox.py / phase4_subscribers.py / main_merge_token.py) — не из v7.

### Deferred to follow-up initiative
- Полный 3/3-story production pilot — после merge v7 (финальная валидация = один replay на 1 истории).

## Sessions

### Pending

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

### Current

- **id:** S1
  **title:** NEW-21 review-runner root-fix (spike + fix)
  **surface:** backend-python
  **spec_section:** §1 (NEW-21)
  **depends_on:** []
  **acceptance:**
    - диагностический spike подтверждает root cause (H1/H2/H3) — задокументирован в commit message
    - на replay `wt-1.4`/`wt-1.5` событие code_review_dispatched имеет непустой review_jsonl= и verdict ∈ {approve, request_changes, reject}
    - `_MergeGateStageResult` несёт `jsonl_path`; `handle_jsonl_str` populated непустой
    - +~5 tests PASS (корневой тест падает на main), mypy/ruff clean на changed
    - methodology §5 NEW-21 → DONE
  **safety_gates:**
    - L3 branch isolation — работа только на integration/pilot_findings_closure_v7
  **checkpoint:** false
  **estimated_retries_allowed:** 3
  **started:** (pending first execution wake)
  **workflow:** workflows/backend-python.md
  **retry_count:** 0
  **worker_branches:** []

### Completed
(none)

## Safety Gates Triggered
(none)

## Blockers / Pauses
(none)

## Decisions Log

- **date:** 2026-05-19
  **session:** bootstrap
  **decision:** S1 (NEW-21) — диагностика-first; фикс scoped после spike, не прескриптивен
  **rationale:** root cause не доказан (3 гипотезы H1/H2/H3); прескриптивный фикс рискует закрыть симптом, не корень — повтор паттерна NEW-13/15/20
  **impact:** S2 NEW-20 переиспользует verdict-extraction из S1 → S2 depends_on S1, порядок жёсткий

## Journal

[2026-05-19 bootstrap] tracker + branches created, 2 sessions planned (S1 NEW-21, S2 NEW-20+22). Runtime=schedule_wakeup, delay=120s, Auto merge=false.

## Final Report (populated on last session completion)

(empty)
