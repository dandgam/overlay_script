# Spec — Skill `/virgil` для Claude Code

> Замена Textual TUI на интерактивное меню **прямо в чате Claude Code**.
> Стиль и механика — как у `/build-agent`. Все кнопки на русском.
> Python CLI (`bmad-orchestrator`) остаётся as is — skill это **тонкий UI-слой** поверх.

**Версия:** v1
**Дата:** 2026-05-19
**Автор:** user + Claude (build-agent flow)
**Статус:** draft, готов к имплементации

---

## 1. Назначение и scope

### Что это

Skill для Claude Code, который позволяет пользователю управлять Virgil (bmad-orchestrator) **через кликабельное меню в чате** без захода в отдельный Textual TUI. Триггер: `/virgil` или фраза «запусти Virgil / покажи статус оркестратора».

### Зачем

| Сейчас (Textual TUI) | Цель (skill `/virgil`) |
|---|---|
| Отдельный full-screen терминальный экран | Меню прямо в чате Claude Code |
| Стрелки + Enter | Клик мышкой по кнопке |
| Зависимость от Textual | Только `AskUserQuestion` (встроен) |
| Работает только в терминале | Работает в Claude Code (CLI / desktop / web / IDE) |
| FormScreen с widgets для типов | `AskUserQuestion` для enum + текстовые сообщения для строк |

### Что **не** меняется

- Python CLI `bmad-orchestrator` остаётся стабильным API. Skill только зовёт его через `Bash`.
- Textual TUI **остаётся** для пользователей терминала (запуск через `bmad-orchestrator menu`). Два пути сосуществуют.
- Логика runtime, DAG planner, worker pool, gates — без изменений.

### Не входит в scope

- Замена самого CLI (не делаем)
- Удаление Textual (оставляем для терминальной среды)
- Веб-интерфейс или REST API (отдельная инициатива)

---

## 2. Структура файлов skill

```
~/.claude/skills/virgil/              ← user-level (доступен во всех проектах)
├── SKILL.md                          ← главный файл, описание меню и flow
├── REFERENCE.md                      ← lazy-load: описание всех CLI команд (для QUESTION mode)
└── templates/
    ├── confirm-destructive.md        ← шаблон двухшагового подтверждения
    ├── command-bridge.md             ← как skill зовёт `bmad-orchestrator <cmd>`
    └── error-handling.md             ← что делать при ошибках CLI
```

**Почему user-level (`~/.claude/skills/virgil/`), а не project-level:**
Virgil — project-agnostic (работает с любым target BMad-проектом через `--project`). Skill должен быть доступен из любой рабочей директории, не только из `bmad-orchestrator/`.

**Альтернатива** (если решим иначе): `bmad-orchestrator/.claude/skills/virgil/` + симлинк в `~/.claude/skills/`. Решается на старте имплементации.

---

## 3. Триггеры и skip-menu

### Триггеры на вход

| Юзер пишет | Что делает skill |
|---|---|
| `/virgil` (без аргументов) | Показывает главное меню |
| `/virgil run antares 1a` | Skip-menu — сразу `bmad-orchestrator run --project antares --wave 1a` |
| `/virgil status` | Skip-menu — сразу `bmad-orchestrator status` |
| «запусти virgil на antares» | Главное меню → 🚀 Запустить → новый wave (предзаполнить project=antares) |
| «покажи статус оркестратора» | Skip-menu — сразу status |
| «останови всё» | Главное меню → 🚀 Запустить → ⏹ Остановить (с destructive confirm) |
| «как сделать X в virgil» | QUESTION mode — читать `REFERENCE.md`, ответить, без действий |

### Правило skip-menu

Если **первое сообщение** содержит явное имя команды (`run`, `status`, `logs`, `stop`, `eval`, `doctor`, `scan`) или однозначный intent — пропустить меню и сразу собирать недостающие параметры либо выполнять. Меню — для входа с «привет» / «`/virgil`» / «что у нас?».

---

## 4. Главное меню (1-й экран)

Вызов `AskUserQuestion`:

```
┌─ Virgil — оркестратор BMad ───────────────────────────────────┐
│ Что делаем?                                                   │
├───────────────────────────────────────────────────────────────┤
│ 1. 🚀 Запустить                                               │
│    Старт нового wave, продолжить прерванный, или dry-run DAG  │
│    preview: bmad-orchestrator run --project X --wave Y        │
│                                                               │
│ 2. 📊 Статус и логи                                           │
│    Текущий wave, бюджет, retrospective, граф зависимостей     │
│    preview: bmad-orchestrator status (live снимок)            │
│                                                               │
│ 3. ⚙️  Настройка                                               │
│    Регистрация проектов, проверка окружения, модели, policy   │
│    preview: init / scan / doctor / model / policy / skill     │
│                                                               │
│ 4. 🧪 Качество и обучение                                     │
│    Eval suite, self-learning loop, корректировка курса        │
│    preview: eval / sl-run / correct-course / investigate      │
└───────────────────────────────────────────────────────────────┘
```

**По выбору:**
- 1 → раздел 5 (sub-меню «Запустить»)
- 2 → раздел 6 (sub-меню «Статус»)
- 3 → раздел 7 (sub-меню «Настройка»)
- 4 → раздел 8 (sub-меню «Качество»)

---

## 5. Sub-меню «🚀 Запустить»

```
┌─ Virgil → Запустить ──────────────────────────────────────────┐
│ Что запускаем?                                                │
├───────────────────────────────────────────────────────────────┤
│ 1. 🆕 Новый wave                                              │
│    Старт BMad Phase 4 на target проекте                       │
│    preview: спрошу --project, --wave, --max-parallel          │
│                                                               │
│ 2. 🔄 Продолжить прерванный wave                              │
│    Найду незакрытые waves в registry, дам выбрать             │
│    preview: scan .orchestrator-state/*.db                     │
│                                                               │
│ 3. 🧪 Dry-run (только DAG, без workers)                       │
│    Построить граф зависимостей и оценить parallelism          │
│    preview: bmad-orchestrator dag --wave X                    │
│                                                               │
│ 4. ⏹  Остановить / пауза                                      │
│    Pause / resume / stop текущего wave                        │
│    preview: pause | resume | stop --graceful (destructive)    │
└───────────────────────────────────────────────────────────────┘
```

### 5.1 «🆕 Новый wave» — сбор параметров

**Шаг 1 — выбор проекта.** `AskUserQuestion` с динамически собранным списком из `bmad-orchestrator scan` (max 4 опции):

```
┌─ Новый wave → Проект ─────────────────────────────────────────┐
│ Какой target проект?                                          │
├───────────────────────────────────────────────────────────────┤
│ 1. antares (готов, последний wave: 1a)                        │
│    preview: /home/server/antares · 8 эпиков · 47 stories     │
│                                                               │
│ 2. odyssey (готов, последний wave: 2b)                        │
│    preview: /home/server/odyssey · 5 эпиков · 23 stories     │
│                                                               │
│ 3. ➕ Зарегистрировать новый проект                            │
│    Запустить init для пути который введу следом              │
│                                                               │
│ 4. ↩ Назад в главное меню                                     │
└───────────────────────────────────────────────────────────────┘
```

**Шаг 2 — wave identifier.** Свободный текст (не через меню, т.к. enum'ом не описать):
> Сообщение от skill: «Какой wave? (например `1a`, `2b`, `3a`). Введи в чат.»
>
> Юзер: «1a»
>
> Skill валидирует regex `^[1-9][a-z]?$`, при ошибке переспрашивает.

**Шаг 3 — parallelism.** `AskUserQuestion`:

```
┌─ Новый wave → Параллелизм ────────────────────────────────────┐
│ Сколько параллельных workers?                                 │
├───────────────────────────────────────────────────────────────┤
│ 1. 🐢 1 (последовательно — отладка)                           │
│    preview: --max-parallel 1                                  │
│                                                               │
│ 2. 🚶 3 (default — баланс скорости и стабильности)            │
│    preview: --max-parallel 3 (рекомендуется)                  │
│                                                               │
│ 3. 🏃 4-6 (быстро, требует ≥16GB RAM)                         │
│    preview: --max-parallel 4 | 5 | 6 (уточню следом)         │
│                                                               │
│ 4. 🚀 7-8 (макс — для крупных wave'ов и мощного хоста)        │
│    preview: --max-parallel 7 | 8                              │
└───────────────────────────────────────────────────────────────┘
```

При выборе 3 или 4 — отдельный текстовый запрос точного числа.

**Шаг 4 — подтверждение и запуск.** Skill показывает превью команды:

> «Сейчас запущу: `bmad-orchestrator run --project antares --wave 1a --max-parallel 3`
> Это создаст git worktree, спавнит 3 workers, мержит через gate.
> Запускаю?»

`AskUserQuestion` Да / Нет / Изменить параметры.

**Шаг 5 — выполнение.** `Bash` вызов команды, stdout стримится в чат сообщениями.

---

### 5.2 «🔄 Продолжить прерванный wave»

Skill сам выполняет `bmad-orchestrator scan` чтобы найти незакрытые waves, и показывает их как опции:

```
┌─ Продолжить wave ─────────────────────────────────────────────┐
│ Какой wave продолжить?                                        │
├───────────────────────────────────────────────────────────────┤
│ 1. antares · wave 1a (5/8 stories, прерван 2 часа назад)      │
│    preview: resume --project antares --wave 1a               │
│                                                               │
│ 2. odyssey · wave 2b (12/23 stories, прерван вчера)           │
│    preview: resume --project odyssey --wave 2b               │
│                                                               │
│ 3. ↩ Назад                                                    │
└───────────────────────────────────────────────────────────────┘
```

Прямой запуск без дополнительных вопросов (параметры уже в state.db).

### 5.3 «🧪 Dry-run»

Только wave id. Без параллелизма (DAG не запускает workers). Вызывает `bmad-orchestrator dag --wave X` и стримит ASCII-граф в чат.

### 5.4 «⏹ Остановить / пауза» (destructive flow)

```
┌─ Остановить ──────────────────────────────────────────────────┐
│ Какое действие?                                               │
├───────────────────────────────────────────────────────────────┤
│ 1. ⏸  Пауза (мягко, workers допилят текущую story)            │
│    preview: bmad-orchestrator pause                           │
│                                                               │
│ 2. ▶️  Resume после паузы                                      │
│    preview: bmad-orchestrator resume                          │
│                                                               │
│ 3. 🛑 Stop --graceful (закрыть после текущих stories) ⚠       │
│    preview: stop --graceful (destructive — двойное подтв.)    │
│                                                               │
│ 4. 💥 Stop --hard (немедленно убить workers) ⚠⚠               │
│    preview: stop --hard (DESTRUCTIVE — typed confirm)         │
└───────────────────────────────────────────────────────────────┘
```

Для опций 3-4 — см. раздел 10 (destructive confirm flow).

---

## 6. Sub-меню «📊 Статус и логи»

```
┌─ Virgil → Статус ─────────────────────────────────────────────┐
│ Что посмотреть?                                               │
├───────────────────────────────────────────────────────────────┤
│ 1. 📈 Текущий статус (все waves)                              │
│    preview: bmad-orchestrator status                          │
│                                                               │
│ 2. 💰 Бюджет (траты по wave'у)                                │
│    preview: bmad-orchestrator budget --wave X                 │
│                                                               │
│ 3. 📜 Логи конкретного worker'а                               │
│    preview: bmad-orchestrator logs --worker w-3 --tail 50     │
│                                                               │
│ 4. 🔍 Подробно (DAG / retro / память)                         │
│    preview: drill-down → dag / retro / memory                 │
└───────────────────────────────────────────────────────────────┘
```

### 6.1 «📈 Текущий статус»

Прямой запуск `bmad-orchestrator status`. Опционально предложить `--live` режим:

> Сначала `AskUserQuestion`: «Snapshot или live?»
> - 📸 Snapshot (один раз) → `status`
> - 🎥 Live (обновление каждую секунду) → `status --live`

### 6.2 «💰 Бюджет»

Спросить wave: «Какой wave? (или `all` для всех)» — текстовое сообщение.
Запустить `budget --wave X`.

### 6.3 «📜 Логи worker'а»

Spawn вопрос: «Имя worker'а? (например `w-1`, `w-2`)» + опционально tail.

### 6.4 «🔍 Подробно» — drill-down

```
┌─ Статус → Подробно ───────────────────────────────────────────┐
│ Что именно?                                                   │
├───────────────────────────────────────────────────────────────┤
│ 1. 🕸  DAG конкретного wave'а                                 │
│    preview: bmad-orchestrator dag --wave X                    │
│                                                               │
│ 2. 📝 Retrospective (что произошло в wave'е)                  │
│    preview: bmad-orchestrator retro --wave X                  │
│                                                               │
│ 3. 🧠 Память по wave'у (что усвоил)                           │
│    preview: bmad-orchestrator memory --wave X                 │
│                                                               │
│ 4. ↩ Назад                                                    │
└───────────────────────────────────────────────────────────────┘
```

Каждая опция — текстовый вопрос wave id и запуск соответствующей команды.

---

## 7. Sub-меню «⚙️ Настройка»

```
┌─ Virgil → Настройка ──────────────────────────────────────────┐
│ Что настраиваем?                                              │
├───────────────────────────────────────────────────────────────┤
│ 1. 📁 Проекты (init / scan / doctor)                          │
│    preview: регистрация и health-check target проектов        │
│                                                               │
│ 2. 🤖 Модели (Opus / Sonnet / Haiku по ролям)                 │
│    preview: model show / set / save                           │
│                                                               │
│ 3. 📜 Policy (валидация / apply / rollback)                   │
│    preview: validate-policy / policy-apply / policy-rollback ⚠│
│                                                               │
│ 4. 🔧 Skills (обновить из upstream, статус)                   │
│    preview: skill-update / skill-status                       │
└───────────────────────────────────────────────────────────────┘
```

### 7.1 «📁 Проекты»

```
┌─ Настройка → Проекты ─────────────────────────────────────────┐
│ Что с проектами?                                              │
├───────────────────────────────────────────────────────────────┤
│ 1. ➕ Зарегистрировать проект (init)                          │
│    Спрошу путь, добавлю в registry                            │
│    preview: bmad-orchestrator init <path>                     │
│                                                               │
│ 2. 📋 Список всех проектов (scan)                             │
│    preview: bmad-orchestrator scan                            │
│                                                               │
│ 3. 🩺 Health-check одного проекта (doctor)                    │
│    Спрошу slug                                                │
│    preview: bmad-orchestrator doctor <slug>                   │
│                                                               │
│ 4. ↩ Назад                                                    │
└───────────────────────────────────────────────────────────────┘
```

### 7.2 «🤖 Модели»

```
┌─ Настройка → Модели ──────────────────────────────────────────┐
│ Что с моделями?                                               │
├───────────────────────────────────────────────────────────────┤
│ 1. 👀 Показать текущие (model show)                           │
│    preview: planner=Opus, reviewer=Opus, dev=Sonnet, ...      │
│                                                               │
│ 2. ✏️  Изменить роль                                          │
│    Спрошу роль и модель                                       │
│    preview: model set <role> <model> [--save]                 │
│                                                               │
│ 3. 💾 Сохранить текущее в YAML (model save)                   │
│    preview: model save                                        │
│                                                               │
│ 4. ↩ Назад                                                    │
└───────────────────────────────────────────────────────────────┘
```

Для «✏️ Изменить» — два `AskUserQuestion` экрана:

**Шаг 1 — роль:**
```
1. 🧭 planner (DAG строит)        — сейчас: claude-opus-4-7
2. 👨‍⚖️ reviewer (code review)      — сейчас: claude-opus-4-7
3. 👷 dev (имплементация)          — сейчас: claude-sonnet-4-6
4. 🤖 all (применить ко всем)
```

**Шаг 2 — модель:**
```
1. claude-opus-4-7    (самая мощная, дорогая)
2. claude-sonnet-4-6  (баланс)
3. claude-haiku-4-5   (быстрая, дешёвая)
4. ✏️ Ввести вручную  (для будущих моделей)
```

Затем спросить save (`AskUserQuestion`: Да сохранить в YAML / Нет, только на сессию).

### 7.3 «📜 Policy»

```
┌─ Настройка → Policy ──────────────────────────────────────────┐
│ Что с policy?                                                 │
├───────────────────────────────────────────────────────────────┤
│ 1. ✅ Валидировать YAML файл (validate-policy)                │
│    Спрошу путь                                                │
│                                                               │
│ 2. 📥 Применить policy на проект (policy-apply)               │
│    Спрошу slug проекта, опции (--auto-apply)                  │
│                                                               │
│ 3. ↩️  Rollback применённой policy ⚠                          │
│    Спрошу slug. DESTRUCTIVE — двойное подтверждение           │
│                                                               │
│ 4. ↩ Назад                                                    │
└───────────────────────────────────────────────────────────────┘
```

### 7.4 «🔧 Skills»

```
┌─ Настройка → Skills ──────────────────────────────────────────┐
│ Что со skills?                                                │
├───────────────────────────────────────────────────────────────┤
│ 1. 🔄 Обновить из upstream (skill-update)                     │
│    Спрошу source путь, опции (--force, --apply-patches)       │
│                                                               │
│ 2. 📊 Статус всех skills (skill-status)                       │
│    preview: bmad-orchestrator skill-status                    │
│                                                               │
│ 3. ↩ Назад                                                    │
│                                                               │
│ 4. (зарезервировано)                                          │
└───────────────────────────────────────────────────────────────┘
```

---

## 8. Sub-меню «🧪 Качество и обучение»

```
┌─ Virgil → Качество ───────────────────────────────────────────┐
│ Что делаем?                                                   │
├───────────────────────────────────────────────────────────────┤
│ 1. 🧪 Eval suite (прогнать тесты на stories)                  │
│    preview: bmad-orchestrator eval run --mode mock|real       │
│                                                               │
│ 2. 🧬 Self-learning loop (обучение на прошлых wave'ах)        │
│    preview: sl-run / sl-status / sl-rollback                  │
│                                                               │
│ 3. 🧭 Корректировка курса (correct-course)                    │
│    Сменить scope/AC по живой story                            │
│    preview: correct-course --story X --reason Y               │
│                                                               │
│ 4. 🔬 Расследование инцидента (investigate)                   │
│    preview: investigate --subject X --reason Y                │
└───────────────────────────────────────────────────────────────┘
```

### 8.1 «🧪 Eval suite»

**Шаг 1 — режим:**
```
1. 🎭 Mock (синтетические cases, быстро)   — preview: --mode mock
2. 🌍 Real (реальные claude -p, медленно)   — preview: --mode real
```

**Шаг 2 — scope:**
```
1. Все cases       (preview: eval run)
2. Один case       (preview: eval run --case <name> — спрошу имя)
3. По tier         (easy / medium / hard — спрошу)
4. ↩ Назад
```

### 8.2 «🧬 Self-learning»

```
┌─ Качество → Self-learning ────────────────────────────────────┐
│ Что с обучением?                                              │
├───────────────────────────────────────────────────────────────┤
│ 1. ▶️  Запустить цикл (sl-run)                                │
│    preview: sl-run --trigger manual                           │
│                                                               │
│ 2. 📊 Статус последнего цикла (sl-status)                     │
│    preview: bmad-orchestrator sl-status                       │
│                                                               │
│ 3. ↩️  Откатить последний apply (sl-rollback) ⚠               │
│    DESTRUCTIVE — двойное подтверждение                        │
│                                                               │
│ 4. ↩ Назад                                                    │
└───────────────────────────────────────────────────────────────┘
```

### 8.3 «🧭 Корректировка курса»

Два текстовых вопроса:
> «Какая story? (например `1.1`)» → `--story`
> «Причина? (PM scope drop / AC change / ...)» → `--reason`

Дальше `AskUserQuestion`: Mock / Real (как в eval).

### 8.4 «🔬 Investigate»

Два текстовых вопроса (subject + reason), затем подтверждение и запуск.

---

## 9. Free-text parameters — когда чат вместо меню

`AskUserQuestion` **не умеет** свободный текст. Поэтому для параметров где enum невозможен — skill отправляет обычное сообщение и ждёт ответа.

| Параметр | Способ | Валидация |
|---|---|---|
| `--wave` (1a, 2b, ...) | Текст | regex `^[1-9][a-z]?$` |
| `--project` (slug) | Меню из `scan` (динамически) | существует в registry |
| `--worker` (w-1, w-2) | Текст | regex `^w-[0-9]+$` |
| `--story` (1.1, 2.3) | Текст | regex `^[0-9]+\.[0-9]+$` |
| `--reason` (свободный текст) | Текст | непустой |
| Path (для init) | Текст | absolute path + exists |
| Custom model id | Текст | regex `^claude-[a-z0-9-]+$` |
| `--max-parallel` integer | Меню (1/3/4-6/7-8) → текст для точного числа | 1 ≤ n ≤ 16 |

**Формат запроса свободного текста:**

> 💬 **Skill:** «Введи wave (например `1a`):»
>
> **Юзер:** «1a»
>
> 💬 **Skill:** проверяет regex → ОК → переходит к следующему шагу. Если не ОК — переспрашивает с пояснением.

---

## 10. Destructive confirm flow

Для команд: `stop --hard`, `policy-rollback`, `sl-rollback`, `skill-update --force`.

**Двухшаговое подтверждение** (т.к. `AskUserQuestion` нет free-text):

**Шаг 1 — `AskUserQuestion` с явным предупреждением:**

```
┌─ ⚠ DESTRUCTIVE ───────────────────────────────────────────────┐
│ Действие: stop --hard (немедленное завершение workers)        │
│ Последствия:                                                  │
│   • Все workers убиваются SIGKILL                             │
│   • Незакоммиченная работа в worktrees ТЕРЯЕТСЯ               │
│   • Wave помечается как aborted в state.db                    │
├───────────────────────────────────────────────────────────────┤
│ 1. ⛔ Отменить (recommended)                                   │
│ 2. ✅ Продолжить — попрошу ввести подтверждение               │
└───────────────────────────────────────────────────────────────┘
```

**Шаг 2 — если выбрано «Продолжить»:**

> 💬 **Skill:** «Введи `STOP HARD` (заглавными) для подтверждения:»
>
> **Юзер:** «STOP HARD»
>
> 💬 **Skill:** сверяет точное совпадение → выполняет команду.
> Если не совпало — отмена с сообщением «Подтверждение не совпало, отменяю».

**Альтернатива** (если двухшаговое подтверждение покажется громоздким): для часто-используемых destructive — `--yes` флаг через CLI без меню. Меню только для редких опасных операций.

---

## 11. Bash-bridge: skill → Python CLI

Skill **не дублирует логику** Virgil. Каждый выбор в меню → формирование точной команды → один вызов `Bash`:

```bash
bmad-orchestrator <subcommand> --flag1 value1 --flag2 value2
```

**Streaming output:** stdout команды отображается в чат сообщениями. Long-running команды (`run`, `eval run --mode real`) могут использовать `run_in_background: true` — skill получит уведомление о завершении.

**Error handling:**
- Exit code 0 → ✅ «Готово» + последние строки stdout
- Exit code != 0 → ❌ stderr + предложение fix (см. `templates/error-handling.md`):
  - `BMadProjectNotFound` → предложить «Запустить scan / init?»
  - `WaveAlreadyRunning` → предложить «status или stop?»
  - `BudgetExceeded` → предложить «budget --wave X посмотреть детали?»

---

## 12. Lazy load и file structure

### `SKILL.md` (главный файл) — ≤300 строк

Содержит:
- Триггеры (раздел 3 этого spec'а)
- Главное меню + 4 sub-меню (разделы 4-8) — ASCII-описания
- Skip-menu rules
- Указание когда читать REFERENCE.md / templates

**НЕ содержит** (lazy load по требованию):
- Детальное описание всех CLI команд → `REFERENCE.md`
- Шаблоны destructive confirm → `templates/confirm-destructive.md`
- Error recovery decision tree → `templates/error-handling.md`

### `REFERENCE.md` — lazy, только в QUESTION mode

Содержит:
- Полный список всех 30+ CLI команд с описанием каждой
- Примеры использования
- Edge cases
- Cross-references на `_bmad/` структуру

Читается **только** когда юзер спросил «как сделать X» — не в обычном menu flow.

### `templates/command-bridge.md`

Документирует:
- Как формировать команды из ответов на меню
- Валидация параметров перед запуском (regex таблица из раздела 9)
- Как стримить stdout
- Как обрабатывать `--mode mock|real` для команд где это важно

### `templates/confirm-destructive.md`

Шаблоны двухшагового подтверждения для:
- `stop --hard` → «STOP HARD»
- `policy-rollback` → «ROLLBACK POLICY»
- `sl-rollback` → «ROLLBACK LEARNING»
- `skill-update --force` → «FORCE UPDATE»

### `templates/error-handling.md`

Decision tree «exit code != 0 → что предложить юзеру»:
- Mapping типичных error message patterns на recovery actions
- Когда автоматически suggesting follow-up command, когда просто показать stderr

---

## 13. Skip-menu shortcuts (детально)

Skill распознаёт следующие триггерные фразы и пропускает меню:

| Фраза юзера | Действие skill |
|---|---|
| `/virgil run <project> <wave>` | Сразу собрать `--max-parallel` (один вопрос) → запустить |
| `/virgil run <project> <wave> <N>` | Полный skip — `run --project X --wave Y --max-parallel N` |
| `/virgil status` | Сразу `status` без меню |
| `/virgil status --live` | Сразу `status --live` |
| `/virgil stop` | Sub-меню «Stop» (раздел 5.4) — destructive, всё равно нужно подтверждение |
| `/virgil dag <wave>` | Сразу `dag --wave <wave>` |
| `/virgil logs <worker>` | Сразу `logs --worker <worker>` |
| `/virgil eval` | Sub-меню «Eval» (раздел 8.1) — нужен mode/scope |
| `/virgil scan` | Сразу `scan` |
| `/virgil doctor <slug>` | Сразу `doctor <slug>` |
| «запусти Y на X» | Главное меню skip → 🚀 → новый wave с предзаполненными project=X, wave=Y |
| «покажи статус» / «как там оркестратор» | Skip → `status` |
| «сколько денег уже потратили» | Skip → `budget` (спросить wave) |
| «что не так с проектом X» | Skip → `doctor X` |

---

## 14. Headless mode (для автоматизации)

Если skill вызывается из другого agent'а или скрипта — должен поддерживать pass-through режим:

```
/virgil --headless run --project antares --wave 1a --max-parallel 3
```

В этом режиме:
- Меню **не показывается** вообще
- Параметры берутся из CLI args
- Destructive confirm **пропускается** только если есть `--yes` флаг
- stdout/stderr передаётся as is

Так skill становится тонким wrapper'ом для использования в pipeline.

---

## 15. Acceptance criteria

Skill готов когда:

1. ✅ Файлы созданы: `SKILL.md`, `REFERENCE.md`, 3 templates
2. ✅ Главное меню (раздел 4) работает: вход через `/virgil` → 4 опции
3. ✅ Все 4 sub-меню (разделы 5-8) реализованы с указанными опциями
4. ✅ Skip-menu shortcuts (раздел 13) работают на минимум 8 фразах
5. ✅ Destructive confirm (раздел 10) работает для `stop --hard` и одной policy-операции
6. ✅ Free-text запросы (раздел 9) валидируют regex'ами из таблицы
7. ✅ Bash-bridge передаёт stdout в чат
8. ✅ Минимум одна успешная end-to-end проверка: `/virgil` → выбор → запуск `bmad-orchestrator status` → видимый output в чате
9. ✅ `REFERENCE.md` читается **только** в QUESTION mode (проверить grep'ом в SKILL.md)
10. ✅ Textual TUI **не сломан** — `bmad-orchestrator menu` продолжает работать

---

## 16. Open questions (требуют решения до имплементации)

1. **User-level или project-level skill?**
   - User-level (`~/.claude/skills/virgil/`): доступен из любой директории, обновляется централизованно.
   - Project-level (`bmad-orchestrator/.claude/skills/virgil/`): живёт с кодом, версионируется в git, но видим только из этого проекта.
   - **Рекомендация:** project-level + симлинк в user. Тогда `git log` показывает эволюцию skill, и symlink делает его глобально доступным.

2. **Динамический выбор проекта — через scan на каждом запуске?**
   - Альтернатива: кэшировать registry в самом skill (читать `~/.bmad-orchestrator/registry.json` напрямую).
   - **Рекомендация:** через `bmad-orchestrator scan --json` — один Bash вызов, актуальные данные, без дублирования логики.

3. **Двухшаговое destructive confirm — UX приемлем?**
   - Альтернатива: один шаг + ввод подтверждения свободным текстом (без типа «yes/no» опции).
   - **Рекомендация:** двухшаговое (как в spec'е). Защищает от случайного клика.

4. **Headless mode на v1 или v2?**
   - **Рекомендация:** v2. v1 = только интерактив, потом добавим автоматизацию когда появятся реальные use cases.

5. **Локализация — только русский или RU/EN switch?**
   - **Рекомендация:** только русский в v1 (юзер solo, не нужен switch). Если будут команды или вклад — добавим i18n потом, как сделали в TUI (`cli/i18n.py`).

---

## 17. Trade-offs vs Textual TUI

| Аспект | Textual TUI | Skill `/virgil` |
|---|---|---|
| **UX в терминале (ssh, tmux)** | ✅ нативный | ⚠ работает только в Claude Code |
| **UX в Claude Code (desktop/web/IDE)** | ❌ отдельный процесс | ✅ нативно в чате |
| **Сложность кода** | Высокая (5 screens, Textual deps) | Низкая (один SKILL.md + Bash) |
| **Live-updating вывод** | ✅ RichLog, 100ms flush | ⚠ stream через сообщения, дискретно |
| **Form widgets (typed inputs)** | ✅ Select / Checkbox / Input | ⚠ только enum + free-text |
| **Стабильность** | Tests/hangs возможны | Меню — declarative, без багов |
| **Цена изменений** | Изменить screen → код + тесты | Изменить меню → markdown |
| **Доступность не-разработчикам** | Терминал нужен | Любой Claude Code клиент |

**Вывод:** skill дополняет, не заменяет. Юзер в терминале (ssh) → `bmad-orchestrator menu`. Юзер в Claude Code → `/virgil`. Логика общая.

---

## 18. План имплементации

**Сессия 1** (~2-3 часа):
- Создать структуру `~/.claude/skills/virgil/`
- `SKILL.md`: главное меню + sub-меню «Запустить»
- `templates/command-bridge.md`
- E2E проверка: `/virgil` → main menu → 🚀 → новый wave → `run` запускается

**Сессия 2** (~2 часа):
- Sub-меню «Статус» + «Настройка»
- `REFERENCE.md` skeleton (с 30+ командами)
- E2E: `/virgil` → 📊 → status

**Сессия 3** (~2 часа):
- Sub-меню «Качество» + destructive confirm + `templates/error-handling.md`
- Skip-menu shortcuts (8+ фраз)
- Финальный e2e + acceptance check (раздел 15)

**Итого:** 6-7 часов, одна неделя при не-полной загрузке.

---

## 19. References

- **Образец:** `~/.claude/skills/build-agent/SKILL.md` (структура меню)
- **Tool docs:** `AskUserQuestion` parameters (limit 4 options, single-select preview)
- **Virgil CLI:** `src/bmad_orchestrator/cli/main.py` (30+ команд, источник истины)
- **Текущий TUI:** `src/bmad_orchestrator/cli/menu/*.py` (что заменяем как UI)
- **Methodology:** `spec/methodology-virgil.md` (общая стратегия агента, обновить с упоминанием skill после имплементации)

---

**Последнее обновление:** 2026-05-19
**Готов к:** имплементации (сессия 1)
**Owner:** user + Claude
