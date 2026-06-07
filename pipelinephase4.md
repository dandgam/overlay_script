---
artifact: virgil-phase4-pipeline
skill: bmad-auto-dev
phase: 4
type: pipeline-spec
audience: agent
format: deterministic-state-machine
---

# Virgil · bmad-auto-dev · Phase 4 — спецификация конвейера (для агента)

**Назначение.** Одна история проходит конвейер `S0 → S8`. Детерминированные гейты (`runner: python` / `cargo`) решают переход; LLM (`opus`/`sonnet`) только производит артефакты и выносит вердикт. Принцип A1: **скрипт решает «пускать», LLM советует.**

Читать как конечный автомат: на каждой стадии вычисли `gate`, затем перейди по `PASS` / `FAIL`. `HALT` = стоп, нужен человек. `LOOP:Sx` = вернуться на стадию Sx.

## Обозначения

- **runner** — кто исполняет: `python` (детерминированный скрипт) · `cargo` · `git` · `opus` / `sonnet` (LLM) · `human`.
- **kind** — роль стадии:
  - `validator` — проверка ФОРМЫ входа/окружения/сборки; фичу не запускает; гейт = boolean.
  - `hardener` — закалка спеки ДО кода (FMEA / pre-mortem); **не верифаер, не ревью**.
  - `reviewer` — суждение по готовому КОДУ (после, ЧИТАЕТ код глазами LLM).
  - `verifier` — **ИСПОЛНЯЕТ** фичу на реальном прогоне: накатывает все миграции на чистую БД и гоняет integration-тесты; гейт = `exit_code`. Отличие от reviewer: reviewer ЧИТАЕТ, verifier ЗАПУСКАЕТ. Детерминированный скрипт, не LLM.
  - `model` — LLM производит артефакт (спека / код / фикс).
  - `git` / `human` — действие / чекпоинт.
- **переход** — `PASS` (gate=true) · `FAIL` (gate=false) · `HALT:<reason>` · `LOOP:<id>`.

## Таблица переходов (спина — её достаточно для исполнения)

| id | stage | runner | kind | gate / action | PASS → | FAIL → |
|----|-------|--------|------|---------------|--------|--------|
| S0 | pre-flight | `python` | validator | `git_clean ∧ exists(epics, sprint-status) ∧ halt_reason==none` | S1 | HALT:preflight |
| S1 | select-story | `dependency_analyzer.py` | validator | `all(story.deps == done)` | S2 | next-ready-story · если нет → HALT:no-ready |
| S2 | branch | `git` | git | `branch feature/story-X` | S3 | — |
| S3 | gauntlet | `gauntlet_injector.py` + `opus` | hardener | inject 5 линз в спеку | S3.5 | — |
| S3.5 | patch-J | `python` | validator | `lenses_present == 5` | S4 | LOOP:S3 |
| S4 | create-story | `opus` | model | produces: story_spec | S5 | — |
| S5 | dev-story | `sonnet` | model | produces: code + tests(unit, integration) | S5.5 | — |
| S5.5 | build-check (Patch N) | `cargo check` | validator | `exit_code == 0` | S6 | LOOP:S5 |
| S6 | code-review | `opus` ×3 | reviewer | `verdict == PASS` | S6.9 | S6.retry |
| S6.retry | autofix | `sonnet` | model+guard | SAFETY GUARDS → re-review | (re-review) | — |
| re-review | re-review | `opus` ×3 | reviewer | `verdict == PASS` | S6.9 | HALT:manual-override |
| S6.9 | verify | `bash ci-local.sh` | verifier | `exit_code == 0` (миграции на чистой БД + integration-тесты) | S7 | S6.retry · после `verify_attempts ≥ 2` → HALT:verify-fail |
| S7 | batch-gate | `batch_gate.py` | validator | `partition≥10 ∨ wave_changed ∨ is_gate_story` | S8 | LOOP:S1 (следующая история) |
| S8 | checkpoint | `human` | human | ревью человеком | TERMINAL | — |

## Подробно по стадиям с под-пунктами

### S3 — gauntlet (hardener · FMEA / pre-mortem)
Инъектор обязательно подаёт 5 линз в спеку (порядок фиксирован):
1. `failure_mode` — что сломается?
2. `edge_case` — граничные случаи?
3. `pre_mortem` — почему провалимся?
4. `devils_advocate` — а если наоборот?
5. `security_red_team` — как взломать?

Это разбор спеки ДО кода. Запускать нечего → **не верифаер**; кода ещё нет → **не ревью**.

### S6 / re-review — code-review (reviewer ×3, «охотники»)
1. `blind_hunter` — баги «вслепую».
2. `edge_case_hunter` — необработанные ветки.
3. `acceptance_auditor` — соответствие AC спеки.

`verdict ∈ {PASS, NEEDS-FIX, BLOCKED}`. Любой не-PASS → ветка autofix.

### S6.retry — autofix (model + SAFETY GUARDS, Patch C)
`sonnet` правит, затем **детерминированный guard** (validator) перед повтором ревью:
- `diff_lines <= 1000`
- `tests_deleted == 0`
- `dangerous_file_deletions == 0`

Guard PASS → `re-review` (снова 3 охотника). re-review FAIL → `HALT:manual-override`.

### S6.9 — verify (verifier · ИСПОЛНЕНИЕ, закрывает «truth-moat»)

**Зачем.** S5.5 только КОМПИЛИРУЕТ (`cargo check`), S6 только ЧИТАЕТ код. До S6.9 фичу никто **не запускал** — отсюда исторический пробел `verifier: none` (баги в миграциях 14–99 и admin-CRUD пережили и сборку, и ревью, потому что их никто не ИСПОЛНИЛ). S6.9 — недостающее исполнение.

**По какому приказу (триггер).** Раннер Virgil вызывает **детерминированный скрипт** `odyssey:scripts/ci-local.sh` как **обязательный шаг** конвейера сразу после того, как ревью дало PASS, и **до** batch-gate (S7). Это не решение LLM и не «вспомнит ли агент закоммитить» — раннер запускает шаг сам, пропустить нельзя. Команда дословно:

```
bash scripts/ci-local.sh        # cwd = репозиторий odyssey; PREREQ: scripts/test-harness-setup.sh выполнен 1 раз
```

**Что скрипт делает внутри (5 под-гейтов, по порядку, каждый — `exit_code`):**
1. `fmt` — `cargo fmt --all --check` (форматирование).
2. `clippy` — `cargo clippy --workspace --all-targets -- -D warnings` (линт = варнинги это ошибки).
3. `migration-smoke` — создаёт **свежую БД** из `template1`, `sqlx migrate run` (все миграции), дропает. Ловит сломанную миграцию, даже если её таблицу не трогает ни один тест.
4. `unit-tests` — `cargo test --workspace` (быстрые, без `#[ignore]`).
5. `db-integration` — `cargo test … -- --include-ignored` по проверенным DB-сьютам (acl `group_intersection`, api `rbac_tests`/`auth_projects_integration`/`policy_drift`; список расширяется по мере `test-support`).

**Гейт.** `exit_code == 0` (все 5 под-гейтов зелёные) → **PASS → S7**. Любой провал → **FAIL → S6.retry** (тот же autofix + SAFETY GUARDS, что и для ревью); после `verify_attempts ≥ 2` без зелёного → `HALT:verify-fail` (нужен человек).

**Почему скрипт, а не LLM.** Гейт обязан быть глупым и детерминированным: ноль токенов, мгновенно, одинаково каждый раз, **нельзя уговорить** пропустить плохой код. LLM — автор (S4/S5), скрипт — независимый контролёр (S6.9).

## Терминалы (точки останова)

- `HALT:preflight` — S0: окружение/артефакты не готовы.
- `HALT:no-ready` — S1: нет истории с готовыми зависимостями.
- `HALT:manual-override` — re-review снова не PASS; нужен человек.
- `HALT:verify-fail` — S6.9: `ci-local.sh` не зелёный после ≥2 попыток (миграция/тест падает); нужен человек.
- `S8` — штатный CHECKPOINT (ревью человеком при смене партии/волны/gate-story).

## Пробел verifier — ЗАКРЫТ частично (Story 0.harness, 2026-06)

Исторически `verifier: none`: после S6 фичу никто не ИСПОЛНЯЛ → «truth-moat» пуст (баги в миграциях 14–99 и admin-CRUD пережили сборку+ревью). **Закрыто стадией S6.9 verify** (`ci-local.sh`): миграции реально накатываются на чистую БД + integration-тесты реально гоняются. Теперь PASS означает не только «собралось и выглядит верно», но и «миграции применяются и проверенные сценарии проходят».

**Что ещё НЕ покрыто (остаток truth-moat):**
- полноценная **канарейка/e2e на запущенном приложении** (поднять сервис, дёрнуть HTTP) — пока только integration-уровень;
- **~66 `#[tokio::test]`+manual-pool** тестов (RLS, auth, воркеры, gateway) — спят, ждут крейт `crates/test-support` (migrated-pool + app_runtime ctx + boot-router + Redis); по мере оживления добавляются в `DB_SUITES` скрипта.

## Машиночитаемо (YAML — зеркало таблицы для оркестратора)

```yaml
pipeline: virgil.phase4
on_story:
  - id: S0
    runner: python
    kind: validator
    gate: "git_clean AND exists(epics, sprint_status) AND halt_reason == none"
    pass: S1
    fail: HALT:preflight
  - id: S1
    runner: dependency_analyzer.py
    kind: validator
    gate: "all(story.deps == done)"
    pass: S2
    fail: next_ready_story | HALT:no_ready
  - id: S2
    runner: git
    kind: action
    action: "branch feature/story-X"
    next: S3
  - id: S3
    runner: [gauntlet_injector.py, opus]
    kind: hardener
    action: "inject 5 lenses into spec"
    lenses: [failure_mode, edge_case, pre_mortem, devils_advocate, security_red_team]
    next: S3.5
  - id: S3.5
    runner: python
    kind: validator
    gate: "lenses_present == 5"
    pass: S4
    fail: LOOP:S3
  - id: S4
    runner: opus
    kind: model
    produces: story_spec
    next: S5
  - id: S5
    runner: sonnet
    kind: model
    produces: [code, tests_unit, tests_integration]
    next: S5.5
  - id: S5.5
    runner: cargo_check
    kind: validator
    gate: "exit_code == 0"
    pass: S6
    fail: LOOP:S5
  - id: S6
    runner: opus
    kind: reviewer
    reviewers: [blind_hunter, edge_case_hunter, acceptance_auditor]
    gate: "verdict == PASS"
    pass: S6_9
    fail: S6_retry
  - id: S6_retry
    runner: sonnet
    kind: model_with_guard
    guard:                       # Patch C — deterministic validator
      - "diff_lines <= 1000"
      - "tests_deleted == 0"
      - "dangerous_file_deletions == 0"
    on_guard_pass: re_review
  - id: re_review
    runner: opus
    kind: reviewer
    reviewers: [blind_hunter, edge_case_hunter, acceptance_auditor]
    gate: "verdict == PASS"
    pass: S6_9
    fail: HALT:manual_override
  - id: S6_9
    runner: bash                 # odyssey:scripts/ci-local.sh — deterministic, runner-invoked
    kind: verifier
    action: "bash scripts/ci-local.sh"
    subgates: [fmt, clippy, migration-smoke, unit-tests, db-integration]
    gate: "exit_code == 0"       # migrations apply on a fresh DB AND integration tests pass
    pass: S7
    fail: S6_retry               # after verify_attempts >= 2 -> HALT:verify_fail
  - id: S7
    runner: batch_gate.py
    kind: validator
    gate: "partition_size >= 10 OR wave_changed OR is_gate_story"
    pass: S8
    fail: LOOP:S1               # next story
  - id: S8
    runner: human
    kind: checkpoint
    terminal: true

terminals:
  - HALT:preflight
  - HALT:no_ready
  - HALT:manual_override
  - HALT:verify_fail
  - S8

gaps:
  verifier: "partially closed by S6_9 (ci-local.sh): migrations applied on a fresh DB + integration tests run. Remaining: live-app canary/e2e, and ~66 tokio::test+manual-pool tests pending crates/test-support"
```

## Как это читать человеку — кто по какому приказу действует

Одна история едет по конвейеру. **Главный принцип: «скрипт решает пускать — LLM только советует».** Три типа исполнителей:
- **раннер** (Virgil, Python) — дирижёр: вызывает каждую стадию по порядку, не забывает, пропустить шаг нельзя;
- **LLM** (`opus`/`sonnet`) — пишет спеку/код/фиксы (S4/S5/S6.retry);
- **детерминированные гейты** (`python`/`cargo`/`git`/`bash`) — пускают или блокируют (S0, S1, S3.5, S5.5, **S6.9**, S7);
- **человек** — только на чекпоинте S8 и на `HALT`.

Поток простыми словами:
1. **S0–S1 — допуск.** Раннер проверяет: дерево чистое, есть epics/sprint-status, у истории все зависимости done. Нет → стоп (нужен человек).
2. **S2 — ветка.** Раннер командует git: `branch feature/story-X`. Код пишется только тут, не в main.
3. **S3 — закалка спеки.** Раннер даёт LLM 5 «линз» (что сломается, граничные, pre-mortem, адвокат дьявола, red-team) — продумать ДО кода. Гейт S3.5: все 5 на месте.
4. **S4 — спека, S5 — код+тесты.** Это пишет LLM.
5. **S5.5 — сборка.** `cargo check`. Не собралось → назад на S5. (Здесь ловится только «компилируется ли», НЕ «работает ли».)
6. **S6 — ревью.** 3 «охотника»-LLM ЧИТАЮТ код. Не PASS → S6.retry: другой LLM правит, но перед повтором — детерминированный guard (≤1000 строк диффа, тесты не удалены, опасных удалений нет).
7. **S6.9 — verify (исполнение).** ⭐ Раннер сам запускает скрипт `ci-local.sh`: накатывает все миграции на чистую БД + гоняет integration-тесты. **Это не LLM и не «вспомнит ли агент» — раннер обязан прогнать шаг.** Красное → назад в autofix; не чинится → стоп (человек). Тут ловится «фича реально работает», а не только «выглядит верно».
8. **S7 — батч-гейт.** Раннер считает: накопилось ≥10 историй / сменилась волна / это gate-история → зови человека (S8); иначе → следующая история (S1).
9. **S8 — человек.** Ты смотришь партию и решаешь мерджить.

Где «приказ на проверки»: **гейты (S5.5, S6.9, S7) запускает раннер автоматически как ступени конвейера** — не по памяти LLM. Поэтому «забыл проверить» невозможно: пропуск ступени = остановка конвейера.
