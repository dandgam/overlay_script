---
name: virgil
description: Интерактивное меню для управления Virgil (bmad-orchestrator) прямо в чате Claude Code. Запуск waves, статус, бюджет, логи, retrospective, DAG. Триггеры — «/virgil», «запусти virgil», «покажи статус оркестратора», «как там wave», «останови оркестратор», «virgil run», «virgil status».
---

# virgil

Управление Virgil (bmad-orchestrator) через кликабельное меню в чате Claude Code. Меню → выбор → сборка параметров → вызов `bmad-orchestrator <команда>` через `Bash`. Stdout стримится в чат как сообщения.

**Все надписи на русском.** Python CLI остаётся стабильным API — skill это только UI-слой.

## Что делать при вызове

1. **Прочитай этот файл целиком** (≤400 строк, всё нужное здесь).
2. **Перед любым вызовом CLI** прочитай `templates/command-bridge.md` — там regex валидации параметров и правила streaming stdout.
3. **Skip-menu first.** Если первое сообщение пользователя содержит явный intent (см. раздел «Триггеры») — пропусти главное меню и сразу собирай недостающие параметры или выполняй команду. Меню — для входа с `/virgil` без аргументов / «привет» / «что там у нас».
4. **Главное меню → 4 опции.** Показать через `AskUserQuestion` (раздел «Главное меню»). По выбору идти в соответствующий sub-меню.
5. **Drill-down.** Каждое sub-меню вызывается отдельным `AskUserQuestion` после выбора. Не запихивать всё в один вызов.
6. **Free-text параметры** (wave id, путь, имя worker'а) — обычное сообщение в чат с просьбой ввести, валидация по regex (см. `command-bridge.md`). При невалидном вводе — переспросить с пояснением.
7. **Перед запуском CLI** — показать пользователю **сборку команды** одной строкой и спросить подтверждение (`AskUserQuestion` Да / Нет / Изменить). Кроме явных skip-menu кейсов.
8. **Destructive операции** (`stop --hard`, `policy-rollback`, `sl-rollback`) — двухшаговое подтверждение. В Session 1 placeholder: показать предупреждение + опцию «Отменить» / «Продолжить». Полный шаблон с typed-name confirm придёт из `templates/confirm-destructive.md` в Session 3.

**Что НЕ делать:**
- Не дублировать логику Python CLI — только формировать команду и звать `Bash`.
- Не вызывать команды не из таблицы CLI (см. `command-bridge.md`).
- Не предлагать `git commit` / `git push` — это вне scope skill.
- Не модифицировать Textual TUI (`src/bmad_orchestrator/cli/menu/*`) — они сосуществуют.

## Триггеры и skip-menu

### Триггеры на вход

| Пользователь пишет | Действие |
|---|---|
| `/virgil` без аргументов | Главное меню |
| «привет», «что там у нас», «virgil?» | Главное меню |
| `/virgil run <project> <wave>` | Skip → собрать `--max-parallel` одним вопросом → запустить |
| `/virgil run <project> <wave> <N>` | Полный skip → `run --project X --wave Y --max-parallel N` |
| `/virgil status` | Skip → `status` без меню |
| `/virgil status --live` | Skip → `status --live` |
| `/virgil dag <wave>` | Skip → `dag --wave <wave>` |
| `/virgil scan` | Skip → `scan` |
| `/virgil pause` / `/virgil resume` | Skip → соответствующая команда |
| `/virgil stop` | Sub-меню «Остановить» (всё равно нужно подтверждение) |
| «запусти на antares» / «запусти Y на X» | Главное → 🚀 → новый wave с предзаполненным project=X (и wave=Y если указан) |
| «покажи статус» / «как там оркестратор» / «что сейчас работает» | Skip → `status` |
| «сколько потратили» / «какой бюджет» | Skip → спросить wave → `budget --wave X` |
| «логи worker'а X» / «что у воркера X» | Skip → спросить tail → `logs --worker X` |
| «как сделать X в virgil» / «virgil умеет X?» | QUESTION mode — пока (в Session 1) ответить «полная справка появится в Session 2 (REFERENCE.md)»; если вопрос про команды Запустить/Статус — ответить из этого SKILL.md |

### Правило skip-menu

Если фраза содержит **явное имя команды** (run/status/logs/stop/scan/pause/resume/dag) или **однозначный intent** («запусти», «покажи статус», «останови») — пропустить главное меню. Меню — для амбивалентных входов.

## Главное меню

Вызов `AskUserQuestion` с параметрами:

- **question:** «Что делаем?»
- **header:** «Virgil»
- **multiSelect:** false
- **options (4):**

  1. **label:** «🚀 Запустить»
     **description:** «Старт нового wave, продолжить прерванный, dry-run DAG, пауза/стоп»
     **preview:** «bmad-orchestrator run --project X --wave Y --max-parallel N»

  2. **label:** «📊 Статус и логи»
     **description:** «Текущий wave, бюджет, retrospective, граф зависимостей, память»
     **preview:** «status / budget / logs / dag / retro / memory»

  3. **label:** «⚙️ Настройка»
     **description:** «(будет добавлено в Session 2) Регистрация проектов, модели, policy, skills»
     **preview:** «init / scan / doctor / model / policy / skill-update»

  4. **label:** «🧪 Качество и обучение»
     **description:** «(будет добавлено в Session 2) Eval suite, self-learning loop, корректировка курса»
     **preview:** «eval / sl-run / correct-course / investigate»

**Маршрутизация по выбору:**
- 1 → раздел «Sub-меню Запустить»
- 2 → раздел «Sub-меню Статус»
- 3 → ответить: «Этот раздел появится в Session 2. Сейчас доступны Запустить и Статус. Что выбираем?» и повторить главное меню.
- 4 → то же, что и 3.

## Sub-меню «🚀 Запустить»

Вызов `AskUserQuestion`:

- **question:** «Что запускаем?»
- **header:** «Virgil → Запустить»
- **multiSelect:** false
- **options (4):**

  1. **label:** «🆕 Новый wave»
     **description:** «Старт BMad Phase 4 на target проекте»
     **preview:** «Спрошу --project, --wave, --max-parallel»

  2. **label:** «🔄 Продолжить прерванный wave»
     **description:** «Найду незакрытые waves в registry, дам выбрать»
     **preview:** «bmad-orchestrator resume --project X --wave Y»

  3. **label:** «🧪 Dry-run (только DAG, без workers)»
     **description:** «Построить граф зависимостей и оценить parallelism»
     **preview:** «bmad-orchestrator dag --wave X»

  4. **label:** «⏹ Остановить / пауза»
     **description:** «Pause / resume / stop текущего wave»
     **preview:** «pause | resume | stop --graceful | stop --hard ⚠»

### Drill-down: «🆕 Новый wave»

**Шаг 1 — выбор проекта.** Сначала вызови `Bash`: `bmad-orchestrator scan` чтобы получить список зарегистрированных проектов. Если scan вернул пустой список — предложи «➕ Зарегистрировать новый проект» как единственный путь.

Если проекты есть — вызови `AskUserQuestion`:

- **question:** «Какой target проект?»
- **header:** «Новый wave → Проект»
- **options (до 4):** до 3 проектов из scan + опция «➕ Зарегистрировать новый» (или «↩ Назад» если ≥4 проектов и нужен fallback).

Если выбрано «➕ Зарегистрировать новый» — текстовый запрос: «Введи абсолютный путь к BMad-проекту:», валидация (path exists + absolute), запуск `bmad-orchestrator init <path>`, затем продолжить с шага 2 уже зарегистрированного проекта.

Если у пользователя в исходной фразе уже было имя проекта («запусти на antares») — пропустить шаг 1, использовать заданный проект.

**Шаг 2 — wave identifier (free-text).** Отправь обычное сообщение:

> «Какой wave? (например `1a`, `2b`, `3a`). Введи в чат.»

Валидация по regex `^[1-9][a-z]?$`. При ошибке: «Wave должен быть в формате `1a`, `2b`, `3a` (цифра + опциональная буква). Попробуй ещё раз.»

**Шаг 3 — parallelism.** `AskUserQuestion`:

- **question:** «Сколько параллельных workers?»
- **header:** «Новый wave → Параллелизм»
- **options (4):**

  1. **label:** «🐢 1 (последовательно — для отладки)»
     **description:** «Один worker за раз. Стабильно, медленно»
     **preview:** «--max-parallel 1»

  2. **label:** «🚶 3 (default — рекомендуется)»
     **description:** «Баланс скорости и стабильности. Подходит для большинства waves»
     **preview:** «--max-parallel 3»

  3. **label:** «🏃 4-6 (быстро, требует ≥16GB RAM)»
     **description:** «Уточню точное число следом»
     **preview:** «--max-parallel 4 / 5 / 6»

  4. **label:** «🚀 7-8 (макс — для крупных waves)»
     **description:** «Уточню точное число следом»
     **preview:** «--max-parallel 7 / 8»

При выборе 3 или 4 — текстовый вопрос: «Точное число workers (4-6 или 7-8)?». Валидация: integer в диапазоне.

**Шаг 4 — подтверждение.** Покажи сборку команды:

> «Запускаю: `bmad-orchestrator run --project <X> --wave <Y> --max-parallel <N>`
> Это создаст git worktree, спавнит <N> workers, мержит через quality gate.
> Подтверждаешь?»

`AskUserQuestion`:
- **options (3):**
  1. **label:** «✅ Да, запускай»
  2. **label:** «✏️ Изменить параметры»  (вернуться на шаг 1)
  3. **label:** «❌ Отмена»

**Шаг 5 — выполнение.** При «Да» — вызови `Bash` с командой. `run` это long-running операция → используй `run_in_background: true`. Сообщи пользователю: «Запустил в фоне. Статус: `/virgil status` или подожди уведомления о завершении.»

### Drill-down: «🔄 Продолжить прерванный wave»

Сначала вызови `Bash`: `bmad-orchestrator scan` чтобы найти незакрытые waves. (Точный JSON формат скрипта может отличаться — если scan не показывает прерванные waves отдельно, выведи весь список и попроси пользователя указать.)

`AskUserQuestion` с найденными незакрытыми waves (до 3 опций + «↩ Назад»):

- Каждая опция: «<project> · wave <wave_id> (<stories_done>/<stories_total> stories, прерван <when>)»
- preview: «resume --project <X> --wave <Y>»

При выборе → подтверждение → `Bash`: `bmad-orchestrator resume --project X --wave Y` (long-running → background).

Если незакрытых waves нет: «Нет прерванных waves. Запустить новый? (вернёмся в sub-меню Запустить)»

### Drill-down: «🧪 Dry-run»

Текстовый вопрос: «Какой wave для dry-run? (формат `1a`)»

Валидация regex. Затем `Bash` (foreground, быстрая команда): `bmad-orchestrator dag --wave <wave>`. ASCII-граф приходит в stdout — стримится в чат как есть.

### Drill-down: «⏹ Остановить / пауза»

`AskUserQuestion`:

- **question:** «Какое действие?»
- **header:** «Virgil → Остановить»
- **options (4):**

  1. **label:** «⏸ Пауза (мягко — workers допилят текущую story)»
     **description:** «Безопасно. Можно потом resume»
     **preview:** «bmad-orchestrator pause»

  2. **label:** «▶️ Resume после паузы»
     **description:** «Продолжить ранее приостановленный wave»
     **preview:** «bmad-orchestrator resume»

  3. **label:** «🛑 Stop --graceful (закрыть после текущих stories) ⚠»
     **description:** «Дождаться завершения активных stories, потом стоп. Destructive — двойное подтверждение»
     **preview:** «stop --graceful»

  4. **label:** «💥 Stop --hard (немедленно убить workers) ⚠⚠»
     **description:** «SIGKILL всем workers. Незакоммиченная работа теряется. DESTRUCTIVE»
     **preview:** «stop --hard»

- Опции 1, 2 → прямой `Bash` (foreground).
- Опции 3, 4 → **destructive confirm placeholder** (Session 1):

  Покажи `AskUserQuestion`:
  - **question:** «⚠ DESTRUCTIVE: stop --<graceful|hard>. Последствия: <см. ниже>. Подтверждаешь?»
  - Для `--graceful`: «Wave будет помечен как aborted после завершения текущих stories.»
  - Для `--hard`: «Все workers убиваются SIGKILL. Незакоммиченная работа в worktrees ТЕРЯЕТСЯ.»
  - **options (2):**
    1. **label:** «⛔ Отменить» (рекомендуется)
    2. **label:** «✅ Подтвердить и выполнить»
  - При выборе «Подтвердить» — `Bash` команду. **Note:** в Session 3 этот placeholder будет заменён на typed-name confirm («STOP HARD» / «STOP GRACEFUL» текстом).

## Sub-меню «📊 Статус и логи»

`AskUserQuestion`:

- **question:** «Что посмотреть?»
- **header:** «Virgil → Статус»
- **multiSelect:** false
- **options (4):**

  1. **label:** «📈 Текущий статус (все waves)»
     **description:** «Snapshot или live режим»
     **preview:** «bmad-orchestrator status [--live]»

  2. **label:** «💰 Бюджет (траты по wave'у)»
     **description:** «Спрошу wave (или all)»
     **preview:** «bmad-orchestrator budget --wave X»

  3. **label:** «📜 Логи worker'а»
     **description:** «Спрошу имя worker'а и tail»
     **preview:** «bmad-orchestrator logs --worker w-3 --tail 50»

  4. **label:** «🔍 Подробно (DAG / retro / память)»
     **description:** «Drill-down по wave'у»
     **preview:** «dag / retro / memory --wave X»

### Drill-down: «📈 Текущий статус»

`AskUserQuestion`:
- **question:** «Snapshot или live режим?»
- **options (2):**
  1. **label:** «📸 Snapshot (один раз)»  → `Bash`: `bmad-orchestrator status`
  2. **label:** «🎥 Live (обновляется каждую секунду)»  → `Bash`: `bmad-orchestrator status --live` (long-running, background)

### Drill-down: «💰 Бюджет»

Текстовый вопрос: «Какой wave? (формат `1a`, или `all` для всех)»

Валидация: regex `^[1-9][a-z]?$` ИЛИ строка `all`.
- Если `all` — `Bash`: `bmad-orchestrator budget` (без флага).
- Иначе — `Bash`: `bmad-orchestrator budget --wave <wave>`.

### Drill-down: «📜 Логи worker'а»

Два текстовых вопроса:

1. «Имя worker'а? (например `w-1`, `w-2`)»  — regex `^w-[0-9]+$`
2. «Сколько последних строк показать? (default 50, Enter — взять default)»  — integer ≥ 1 или пусто

Затем `Bash`: `bmad-orchestrator logs --worker <name> --tail <N>`.

### Drill-down: «🔍 Подробно»

`AskUserQuestion`:

- **question:** «Что именно?»
- **header:** «Статус → Подробно»
- **options (4):**

  1. **label:** «🕸 DAG конкретного wave'а»
     **description:** «ASCII-граф зависимостей stories»
     **preview:** «bmad-orchestrator dag --wave X»

  2. **label:** «📝 Retrospective (что произошло в wave'е)»
     **description:** «Текстовый разбор: успехи / провалы / lessons»
     **preview:** «bmad-orchestrator retro --wave X»

  3. **label:** «🧠 Память по wave'у (что усвоил)»
     **description:** «Накопленные lessons / proposals»
     **preview:** «bmad-orchestrator memory --wave X»

  4. **label:** «↩ Назад»
     **description:** «Вернуться в Статус»

Для опций 1-3: текстовый запрос wave id (regex `^[1-9][a-z]?$`), затем соответствующая `Bash` команда. Все три — foreground (быстрые).

## Откладываем на Session 2 / 3

| Раздел | Когда | Что войдёт |
|---|---|---|
| Sub-меню «⚙️ Настройка» | Session 2 | Проекты (init/scan/doctor) · Модели (model show/set/save) · Policy · Skills |
| Sub-меню «🧪 Качество» | Session 2 | Eval suite · Self-learning · Correct-course · Investigate |
| `REFERENCE.md` | Session 2 | Справка по всем 30+ CLI командам (lazy-load для QUESTION mode) |
| `templates/confirm-destructive.md` | Session 3 | Полный двухшаговый confirm с typed-name («STOP HARD») |
| `templates/error-handling.md` | Session 3 | Decision tree «exit code != 0 → что предложить» |
| Полный набор skip-menu shortcuts (14 фраз) | Session 3 | Сейчас покрыто ~10 в этой версии |

## Правила (общие)

- **Lazy load.** Этот SKILL.md содержит всё нужное для Запустить + Статус. `REFERENCE.md` не существует в Session 1 — на QUESTION-вопросы про другие команды отвечать «появится в Session 2».
- **Все надписи на русском.** Никаких английских вкраплений в `label` / `description` / `preview` (кроме CLI-команд в `preview` — там английский ожидаем).
- **Preview как навигатор.** В каждой опции `preview` должен показывать **что произойдёт** — команду или результат. Не дублировать `description`.
- **Не больше 4 опций.** `AskUserQuestion` лимит. Если опций больше — группировать в drill-down.
- **Подтверждение перед запуском.** Кроме skip-menu кейсов (где intent уже явный) — всегда показывать сборку команды и спрашивать.
- **Stdout стримится в чат.** Bash foreground → результат сразу видно. Long-running (`run`, `status --live`, `eval run --real`) → background + уведомление.
- **Никаких mock значений.** Если параметр не получен — переспроси, не подставляй default без согласия.

## References

- **Spec:** `spec/spec_virgil_skill.md` (детальный source-of-truth)
- **Образец стиля меню:** `~/.claude/skills/build-agent/SKILL.md`
- **Python CLI:** `src/bmad_orchestrator/cli/main.py` (30+ команд)
- **Templates:**
  - `templates/command-bridge.md` — формирование команд + валидация параметров
  - `templates/confirm-destructive.md` — (Session 3) destructive flow
  - `templates/error-handling.md` — (Session 3) error recovery
- **REFERENCE.md** — (Session 2) полная справка
- **Methodology агента:** `spec/methodology-virgil.md`

**Last updated:** 2026-05-19 (Session 1)
**Status:** v0.1 — главное меню + Запустить + Статус. Настройка и Качество появятся в Session 2.
