# spec_pilot_findings_closure_v5 — build/commit gates + security_review error handling

> **Owner:** user + Virgil orchestrator
> **Created:** 2026-05-19
> **Goal:** Закрыть NEW-11 (ruff build_check_halt), NEW-12 (pre-commit config missing), NEW-13 (security_review error → circuit breaker abort). Все три блокировали stories 1.4/1.5 на пути в integration в pilot run #4. После закрытия — replay, ожидаем 3/3 в `integration/1a`.
> **Source:** `spec/methodology-virgil.md §5` — backlog «pilot run #4 findings». Pilot run #4 (Antares 1a, 2026-05-19): `succeeded=2 failed=1`, integration/1a создана (story 1.3 смержена), но 1.4/1.5 не дошли.

---

## 0. Context

### Inventory at start

- Branch: `main` @ `c53a0ae` (после v4 merge `48febc0` + methodology §5 sync)
- Tests: **2061 PASS** (после v4 merge), mypy/ruff clean
- EventType: ~36 — bootstrap уточнит по `runtime/event_loop.py`

### Что валидировано pilot run #4 (НЕ трогаем)

- **NEW-7** — verdict→reconcile→merge pipeline работает (story 1.3 смержена в `integration/1a`).
- **NEW-9** — verdict source-of-truth: story с коммитами доходит до success.
- **NEW-8** — чистый shutdown.

Pipeline замкнут. NEW-11/12/13 — конкретные блокеры на пути отдельных stories, не разрыв
pipeline. Все три P2, fixable.

---

## 1. NEW-11 (P2) — ruff build_check_halt в worktree

**Симптом.** `build_check_halt command=ruff exit_code=1` на stories 1.3 и 1.4. Stage build-check
запускает `ruff` в worktree, тот возвращает exit 1 → halt.

**Зачем чинить.** ruff exit 1 = либо реальные lint-ошибки в коде story, либо ложное
срабатывание (конфиг/версия). Halt на build-check останавливает story ДО merge — нужно
понять причина в коде story или в окружении.

**Что делаем.**

- Найти где build-check запускает ruff (вероятно `skills/policy/build-check.yaml` +
  соответствующий subscriber / runner Stage). Определить: ruff бежит против всего worktree
  или только changed files; какой конфиг подхватывает.
- Диагностировать exit 1 на pilot-артефактах 1.3/1.4: реальные нарушения или конфликт
  версии ruff (worktree ruff vs target project ruff) / конфига (`pyproject.toml`/`ruff.toml`
  отсутствует или иной в worktree).
- Fix по итогам диагностики:
  - Если ложное (нет конфига → ruff применяет дефолты строже проекта) → scope ruff на
    changed files и использовать конфиг target-проекта; при отсутствии конфига — graceful
    skip с audit-логом, не halt.
  - Если реальные нарушения → это не баг Virgil, а корректный halt; задокументировать
    в tracker journal и скорректировать scope (тогда NEW-11 = «улучшить сообщение об
    ошибке + отдать в autofix loop, а не halt»).

**Tests.** `tests/test_new11_ruff_build_check.py`:
- 3 unit: build-check ruff invocation — scope (changed files), конфиг resolution,
  отсутствие конфига → graceful path.
- 2 integration: mock worktree без `ruff.toml` → build-check НЕ халтит сразу, идёт в
  ожидаемую ветку (skip / autofix).
- **Target:** +5 tests

**Acceptance.**
- ruff в worktree без конфига target-проекта → не приводит к немедленному halt.
- Реальные lint-нарушения → идут в autofix loop (или явный halt с понятным сообщением).

---

## 2. NEW-12 (P2) — pre-commit config missing

**Симптом.** `stage5_recovery_failed: No .pre-commit-config.yaml file` на stories 1.3/1.4.
Worktree target-проекта не имеет `.pre-commit-config.yaml` → `git commit` через pre-commit
hook падает → Stage 5 recovery не может закоммитить.

**Зачем чинить.** Не все target BMad-проекты имеют pre-commit (project-agnostic — нельзя
предполагать наличие). Отсутствие конфига не должно ломать commit.

**Что делаем.**

- В `runtime/worker_spawn.py:_build_worker_env` (или где формируется commit-окружение)
  выставлять `PRE_COMMIT_ALLOW_NO_CONFIG=1` в env воркера — pre-commit при отсутствии
  конфига завершается успешно вместо ошибки.
- Альтернатива / дополнительно: pre-spawn detector — если в worktree нет
  `.pre-commit-config.yaml`, лог `precommit_config_absent` (audit), env-флаг выставлен.
- НЕ создавать `.pre-commit-config.yaml` в target worktree (write в чужой проект — против
  Critical Boundary).

**Tests.** `tests/test_new12_precommit_no_config.py`:
- 3 unit: `_build_worker_env` содержит `PRE_COMMIT_ALLOW_NO_CONFIG=1`; detector ловит
  отсутствие конфига; при наличии конфига флаг не мешает.
- 2 integration: mock worktree без `.pre-commit-config.yaml` → commit проходит.
- **Target:** +5 tests

**Acceptance.**
- Worktree без `.pre-commit-config.yaml` → `git commit` воркера проходит.
- Worktree С конфигом → pre-commit работает как обычно (флаг безвреден).

---

## 3. NEW-13 (P2) — security_review error → circuit breaker abort

**Симптом.** Story 1.4 прошла reconcile (synthetic approve), ушла в security review. Тот
вернул `verdict=error` (не `approve`/`reject`). Supervisor посчитал это escalation'ом;
3 escalations подряд → `supervisor_abort_pipeline circuit breaker` оборвал весь pipeline.
1.4 не смержена (осталась на `feature/1.4` `4b00212`).

**Зачем чинить.** `verdict=error` — это сбой самого review-шага (review не смог вынести
вердикт), а не «story плохая». Считать его escalation'ом → накопление в circuit breaker →
abort всего pipeline из-за технического сбоя одного шага. error ≠ escalation.

**Что делаем.**

- В `supervisor/` (engine / circuit breaker — `max_consecutive_escalations`) различать:
  - `verdict in {approve, reject, request_changes}` → нормальный исход, обрабатывается.
  - `verdict == error` → **не инкрементить** consecutive-escalation счётчик circuit
    breaker. Вместо этого: retry security_review N раз (config, default 1-2), при
    исчерпании retry → одиночная эскалация на HUMAN_QUERY для конкретной story (НЕ abort
    всего pipeline).
- `runtime/supervisor_subscriber.py` / `code_review`-path: error-verdict от security_review
  → отдельная ветка (retry/escalate-story), не общий escalation-путь.
- Новый EventType (если нужен для observability): `SECURITY_REVIEW_ERROR` —
  bootstrap решит, нужен ли отдельный тип или достаточно payload-поля.

**Tests.** `tests/test_new13_security_review_error.py`:
- 4 unit: circuit breaker НЕ инкрементится на `verdict=error`; инкрементится на реальных
  escalations; retry-счётчик security_review; исчерпание retry → escalate-story (не abort).
- 3 integration: mock security_review возвращает error → pipeline НЕ abort'ится, остальные
  stories продолжают; story с error уходит в HUMAN_QUERY после retry.
- **Target:** +7 tests

**Acceptance.**
- `security_review verdict=error` → circuit breaker не приближается к abort.
- Pipeline не abort'ится из-за технического сбоя одного review-шага.
- Story с устойчивым error → одиночная HUMAN_QUERY эскалация, остальные stories мержатся.

---

## 4. Session Plan

| S | Items | Зачем вместе |
|---|---|---|
| S1 | NEW-11 (ruff build_check) + NEW-12 (pre-commit config) | Оба про worktree build/commit-окружение, общая область `worker_spawn` / build-check. +10 tests. |
| S2 | NEW-13 (security_review error handling) | Изолированная supervisor / circuit-breaker логика. +7 tests. |

Bootstrap может разбить иначе (NEW-11 диагностика-тяжёлая — может стать отдельной S1).

---

## 5. Acceptance — epic level

- ✅ Tests ≥**2078 PASS** (delta +17 от 2061), mypy/ruff clean.
- ✅ NEW-11: ruff в worktree без конфига → не приводит к немедленному halt.
- ✅ NEW-12: worktree без `.pre-commit-config.yaml` → commit воркера проходит.
- ✅ NEW-13: `security_review verdict=error` → не abort'ит pipeline.
- ✅ methodology-virgil.md §5: NEW-11/NEW-12/NEW-13 помечены DONE.
- ⏭ Unblocks: pilot replay Antares 1a → ожидаем `succeeded=3` (все 1.3/1.4/1.5 в `integration/1a`).

---

## 6. References

- `spec/methodology-virgil.md §5` — backlog «pilot run #4 findings»
- memory `project_milestone_first_integration_merge.md` — детали run #4 + NEW-11/12/13
- `spec/spec_pilot_findings_closure_v4.md` — предыдущая инициатива (NEW-9/10/5)
- pilot run #4 артефакты: `integration/1a` (story 1.3), `feature/1.4` `4b00212`

---

**Last updated:** 2026-05-19 (v1.0)
**Status:** READY for `/auto-loop-spec-short` bootstrap (3 P2 items, ~2 сессии)
