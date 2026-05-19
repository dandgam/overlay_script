# spec_pilot_findings_closure_v4 — verdict source-of-truth + observability

> **Owner:** user + Virgil orchestrator
> **Created:** 2026-05-19
> **Goal:** Закрыть NEW-9 (runner exit перекрывает verdict) и NEW-10 (worker events не доходят до главного events.jsonl) + перепроверить NEW-5. Разблокировать NEW-7 (integration pipeline) — без NEW-9 ни одна story не доходит до success.
> **Source:** `spec/methodology-virgil.md §5` — backlog «pilot run #3 findings». Pilot run #3 (Antares 1a, 2026-05-19): `succeeded=0 failed=3`, хотя story 1.5 прошла полный цикл (2 коммита).

---

## 0. Context

### Inventory at start

- Branch: `main` @ `00c1c12`
- Tests: **2038 PASS** (после v3 merge `be655eb`), mypy/ruff clean
- EventType: 36 (`INTEGRATION_MERGE_SKIPPED` последний)

### Корневая проблема (NEW-9)

Pilot run #3: story 1.5 прошла полный цикл — dev-story commit `63dc8d4` + Stage 6 review +
autofix commit `79bc93c` (F1-F4). Реальная завершённая работа, 2 коммита. Но
`real_pilot_done succeeded=0` — 1.5 помечена `failed`.

Цепочка бага:
1. Story делает полную работу (commits есть, Stage 6 review дал verdict).
2. `bmad-auto-dev-runner.sh` exit non-zero на финальной стадии (Stage 7 cleanup / прочее).
3. NEW-4 fix `parse_inner_exit_code`: inner exit != 0 + outer == 0 → `worker_completed status=failure`.
4. Counter (S1): failed.
5. NEW-7 `_reconcile_success_verdicts`: success verdict'ов нет → нечего мержить → нет integration ветки.

**NEW-4 over-correction:** fix сделал orchestrator строже к runner exit code. Теперь
завершённая story с коммитами = failed только из-за non-zero exit на cleanup-стадии.

### Что НЕ трогаем

- NEW-2 graceful Stage 7 cleanup — оставить как есть (validated).
- NEW-4 `parse_inner_exit_code` — не удалять; exit code остаётся СИГНАЛОМ, просто перестаёт
  быть единственным решающим фактором (см. §1).

---

## 1. NEW-9 (P1) — verdict как source-of-truth для success

**Зачем.** `verdict=approve` из Stage 6 code-review — это authoritative сигнал «story сделана
правильно». Runner exit code — вторичный (может быть non-zero из-за cleanup-стадии при
полностью успешной story). Orchestrator должен решать success по verdict + commits, а exit
code использовать только как tie-breaker / диагностику.

**Что добавляем.**

- `runtime/worker_spawn.py:_tail_and_emit_completion` (или где формируется `worker_completed`):
  новая функция `decide_worker_status(verdict, new_commits_count, inner_exit, outer_exit) -> str`:
  - `verdict == "approve" AND new_commits_count > 0` → `"success"` (НЕЗАВИСИМО от inner_exit).
  - `verdict == "request_changes"` или `"reject"` → `"failure"`.
  - verdict отсутствует (None) → fallback на старую exit-code логику (inner != 0 → failure).
  - В payload `worker_completed` добавить поля: `verdict`, `new_commits_count`,
    `status_decided_by` (`verdict` | `exit_code_fallback`) для observability.
- Verdict читается из stage6 review log. `verdict_fallback.py` (S2 `4ac3e56`) уже умеет
  парсить `<worktree>/_bmad/auto-dev-state/reviews/<story>-stage6-*.log` — переиспользовать
  его reader. Если у verdict_fallback нет публичной функции чтения — выделить
  `read_runner_verdict(worktree, story_id) -> str | None`.
- Wire: `_tail_and_emit_completion` вызывает `read_runner_verdict` → `decide_worker_status`.

**Tests.** `tests/test_new9_verdict_source_of_truth.py`:
- 5 unit `decide_worker_status`: approve+commits→success (inner_exit=2 игнорируется),
  approve+0commits→failure, request_changes→failure, verdict=None+inner!=0→failure (fallback),
  verdict=None+inner==0+commits→success.
- 3 unit: `read_runner_verdict` парсит stage6 log (approve / request_changes / missing log).
- 2 integration: `_tail_and_emit_completion` с mock stage6 log → `worker_completed` payload
  содержит `status=success`, `status_decided_by=verdict` при approve+commits+inner_exit=2.
- 1 regression: fixture pilot run #3 1.5 (verdict approve, 2 commits, runner exit non-zero)
  → status=success.
- **Target:** +11 tests

**Acceptance.**
- Story с `verdict=approve` + commits + `inner_exit != 0` → `worker_completed status=success`.
- Story с `verdict=request_changes` → `status=failure` даже если commits есть.
- Без stage6 log (verdict=None) → старое поведение (exit-code fallback) сохранено.
- `_reconcile_success_verdicts` теперь видит success → создаёт `integration/<wave>` ветку
  (разблокировка NEW-7).

---

## 2. NEW-10 (P2) — worker events доходят до главного events.jsonl

**Зачем.** Pilot run #3: `_bmad-output/runs/default/wt-1.X.events.jsonl` остались с mtime
прошлого run'а — worker-события 3-го прогона туда не попали. Orchestrator-лог пуст 30 минут
между `git_worktree_created` и `real_pilot_done`. Диагностика run'а почти невозможна.

**Что добавляем.**

- Найти где worker events пишутся. Гипотеза: воркер пишет в свой
  `<worktree>/_bmad-output/runs/.../wt-<id>.events.jsonl` (worktree-internal), а
  orchestrator читает из главного `<target>/_bmad-output/runs/default/`. Пути расходятся.
- Fix: orchestrator после `worker_completed` копирует/мержит worktree-internal events.jsonl
  в главный `runs/default/wt-<id>.events.jsonl` (append, не overwrite). Либо worker spawn
  env направляет events.jsonl напрямую в главный путь.
- Промежуточные orchestrator-лог строки: эмитить `worker_stage_progress` (или подобное)
  при ключевых событиях воркера (Stage 4 start, Stage 6 start, verdict) — чтобы лог не
  молчал 30 минут. Источник — tail воркер-stdout по интервалу.

**Tests.** `tests/test_new10_worker_events_propagation.py`:
- 3 unit: merge worktree-internal events.jsonl в главный (append semantics, dedup по ts,
  missing source file).
- 2 unit: путь resolution — главный events.jsonl path корректен.
- 2 integration: mock worker run → главный events.jsonl содержит worker события.
- **Target:** +7 tests

**Acceptance.**
- После pilot run главный `runs/default/wt-<id>.events.jsonl` имеет свежий mtime и
  события текущего run'а.
- Orchestrator-лог не молчит >5 мин при активных воркерах.

---

## 3. NEW-5 recheck (P2) — почему 1.3 = 0 commits

**Зачем.** NEW-5 (pre-spawn dirty-worktree gate, `auto_clean_dirty_worktree=True`) закрыт в
v3. Но pilot run #3: story 1.3 = 0 коммитов, упала рано. Fresh worktree из master не должен
быть dirty — но `embedded_skills_applied` пишет 73 файла в `<worktree>/.claude/skills/`,
что делает дерево dirty ДО Stage 0. NEW-5 gate мог либо не сработать, либо вычистить нужные
файлы.

**Что добавляем.**

- Разобрать: при fresh worktree + `embedded_skills_applied` (73 файла) → `git status` worktree
  показывает untracked `.claude/skills/`. Если `auto_clean_dirty_worktree` делает
  `git clean -fd` — он СОТРЁТ embedded skills → worker без skills.
- Fix: dirty-worktree gate должен игнорировать `.claude/` путь (embedded skills — это
  намеренный оркестраторский inject, не «грязь»). `git clean -fd -e .claude` или проверять
  dirty только вне `.claude/`.
- Если корень иной (1.3 упала по другой причине) — задокументировать в tracker journal,
  скорректировать scope.

**Tests.** `tests/test_new5_embedded_skills_not_cleaned.py`:
- 3 unit: dirty-detector игнорирует `.claude/` пути, ловит реальную грязь вне `.claude/`.
- 2 integration: worktree с embedded skills → gate НЕ халтит, НЕ стирает `.claude/skills/`.
- **Target:** +5 tests

**Acceptance.**
- Fresh worktree + 73 embedded skill файла → НЕ считается dirty → Stage 0 проходит.
- Реальная грязь вне `.claude/` (e.g. изменённый `1.3.md`) — по-прежнему ловится.

---

## 4. Session Plan

| S | Items | Зачем вместе |
|---|---|---|
| S1 | NEW-9 (verdict source-of-truth) | Один P1 item, изолированный, +11 tests. Критпуть. |
| S2 | NEW-10 (events propagation) + NEW-5 recheck | Оба про worktree↔orchestrator I/O, общая область. +12 tests. |

Bootstrap может разбить иначе (S1 тяжёлый — может стать S1+S2, NEW-10/5 → S3).

---

## 5. Acceptance — epic level

- ✅ Tests ≥**2055 PASS** (delta +17 от 2038), mypy/ruff clean.
- ✅ NEW-9: story с verdict=approve + commits → `status=success` несмотря на runner exit code.
- ✅ NEW-10: главный events.jsonl получает свежие worker события.
- ✅ NEW-5: embedded skills не считаются dirty.
- ✅ methodology-virgil.md §5: NEW-9/NEW-10/NEW-5-recheck помечены DONE.
- ⏭ Unblocks: NEW-7 валидация — следующий pilot replay должен создать `integration/wave-1a`.

---

## 6. References

- `spec/methodology-virgil.md §5` — backlog «pilot run #3 findings»
- memory `project_pilot_antares_1a_run3_2026-05-19.md` — детали run #3
- `spec/spec_pilot_findings_closure_v3.md` — предыдущая инициатива (NEW-7 fix)
- pilot run #3 артефакты: feature/1.5 commits `63dc8d4` + `79bc93c`

---

**Last updated:** 2026-05-19 (v1.0)
**Status:** READY for `/auto-loop-spec-long` bootstrap
