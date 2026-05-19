# Sub-меню «⚙️ Настройка»

Загружается когда пользователь выбрал «⚙️ Настройка» в главном меню. Перед вызовом CLI — прочитай `../templates/command-bridge.md` (там полный путь к `bmad-orchestrator` и валидации).

Вызов `AskUserQuestion`:

- **question:** «Что настраиваем?»
- **header:** «Virgil → Настройка»
- **multiSelect:** false
- **options (4):**

  1. **label:** «📁 Проекты»
     **description:** «Зарегистрировать BMad-проект, посмотреть список, проверить здоровье»
     **preview:** «Регистрация и проверка target-проектов оркестратора»

  2. **label:** «🤖 Модели»
     **description:** «Какая модель Claude для какой роли (планировщик, ревьюер, разработчик…)»
     **preview:** «Показать раскладку, поменять роль, сохранить в файл»

  3. **label:** «📜 Policy»
     **description:** «Правила автоматизации: проверить файл, применить на проект, откатить»
     **preview:** «Валидация YAML · применение предложенных правок · откат»

  4. **label:** «🔧 Skills»
     **description:** «Встроенные BMad-скиллы: обновить из upstream, посмотреть статус»
     **preview:** «Обновление skills/upstream/ + версия и применённые патчи»

## Drill-down: «📁 Проекты»

`AskUserQuestion`:

- **question:** «Что с проектами?»
- **header:** «Настройка → Проекты»
- **options (4):**

  1. **label:** «➕ Зарегистрировать проект»
     **description:** «Добавить BMad-проект в реестр, чтобы Virgil мог на нём работать»
     **preview:** «Спрошу абсолютный путь к папке проекта»

  2. **label:** «📋 Список всех проектов»
     **description:** «Показать все зарегистрированные проекты и их состояние на диске»
     **preview:** «Таблица: slug · layout · статус (ok/stale/missing) · путь»

  3. **label:** «🩺 Проверить здоровье проекта»
     **description:** «Прогнать health-check одного проекта по его slug»
     **preview:** «Спрошу slug. Покажу таблицу проверок ok/fail»

  4. **label:** «↩ Назад»
     **description:** «Вернуться в меню Настройка»
     **preview:** «Возврат на шаг назад»

- ➕ → текстовый вопрос «Введи абсолютный путь к BMad-проекту:» — валидация (absolute + exists, см. command-bridge.md раздел 7). Затем `Bash`: `bmad-orchestrator init <path>`.
- 📋 → прямой `Bash` (foreground): `bmad-orchestrator scan`.
- 🩺 → текстовый вопрос «Slug проекта? (из списка scan)» — regex `^[a-z0-9][a-z0-9_-]*$`. Затем `Bash`: `bmad-orchestrator doctor <slug>`.

## Drill-down: «🤖 Модели»

`AskUserQuestion`:

- **question:** «Что с моделями?»
- **header:** «Настройка → Модели»
- **options (4):**

  1. **label:** «👀 Показать раскладку»
     **description:** «Текущая модель для каждой роли»
     **preview:** «planner, reviewer, dev, routine, mechanical, fallback»

  2. **label:** «✏️ Изменить роль»
     **description:** «Назначить другую модель Claude одной роли (или всем сразу)»
     **preview:** «Спрошу роль и модель. Можно сохранить в файл»

  3. **label:** «💾 Сохранить в файл»
     **description:** «Записать текущую раскладку моделей в YAML target-проекта»
     **preview:** «Фиксирует defaults — переживёт перезапуск»

  4. **label:** «↩ Назад»
     **description:** «Вернуться в меню Настройка»
     **preview:** «Возврат на шаг назад»

- 👀 → прямой `Bash`: `bmad-orchestrator model show`.
- 💾 → прямой `Bash`: `bmad-orchestrator model save`.
- ✏️ → два шага:
  1. Текстовый вопрос: «Какую роль меняем? Доступно: `planner` (планировщик DAG), `reviewer` (код-ревью), `dev` (разработка), `routine` (рутина), `mechanical` (механические правки), `fallback` (запасная), или `all` (все сразу).» — валидация против этого списка.
  2. `AskUserQuestion` модель:
     - **options (4):**
       1. **label:** «claude-opus-4-7 — самая мощная»
          **description:** «Для архитектуры и сложных решений. Дороже»
       2. **label:** «claude-sonnet-4-6 — баланс»
          **description:** «Для имплементации и обычных задач»
       3. **label:** «claude-haiku-4-5 — быстрая и дешёвая»
          **description:** «Для механики и простых задач»
       4. **label:** «✏️ Ввести вручную»
          **description:** «Для будущих моделей — спрошу id текстом (regex `^claude-[a-z0-9-]+$`)»
  3. `AskUserQuestion`: сохранить? «💾 Да, в файл» (добавить `--save`) / «🗒 Только на сессию» (без флага).
  4. `Bash`: `bmad-orchestrator model set <role> <model> [--save]`.

## Drill-down: «📜 Policy»

`AskUserQuestion`:

- **question:** «Что с policy?»
- **header:** «Настройка → Policy»
- **options (4):**

  1. **label:** «✅ Проверить YAML-файл»
     **description:** «Валидация elicitation-policy.yaml на синтаксис и базовые поля»
     **preview:** «Спрошу путь (или возьму путь по умолчанию)»

  2. **label:** «📥 Применить policy на проект»
     **description:** «Применить предложенные правки policy к проекту по его slug»
     **preview:** «Спрошу slug. По умолчанию спросит подтверждение каждой правки»

  3. **label:** «↩️ Откатить правку policy ⚠»
     **description:** «Вернуть policy проекта к состоянию до конкретной правки»
     **preview:** «Спрошу slug и id правки. Восстановит из бэкапа»

  4. **label:** «↩ Назад»
     **description:** «Вернуться в меню Настройка»
     **preview:** «Возврат на шаг назад»

- ✅ → текстовый вопрос «Путь к policy YAML? (Enter — взять путь по умолчанию)». Если пусто — `Bash`: `bmad-orchestrator validate-policy`; иначе `validate-policy --path <path>`.
- 📥 → текстовый вопрос «Slug проекта?» (regex `^[a-z0-9][a-z0-9_-]*$`). Подтверждение. `Bash`: `bmad-orchestrator policy-apply <slug>` (без `--auto-apply` — пусть спрашивает каждую правку).
- ↩️ → два текстовых вопроса: «Slug проекта?» + «id правки (proposal_id из audit-лога)?». Затем **двухшаговое подтверждение** — прочитай `../templates/confirm-destructive.md`, фраза `ROLLBACK POLICY`. При успехе → `Bash`: `bmad-orchestrator policy-rollback <slug> <proposal_id>`. При ошибке — `../templates/error-handling.md`.

## Drill-down: «🔧 Skills»

`AskUserQuestion`:

- **question:** «Что со skills?»
- **header:** «Настройка → Skills»
- **options (3):**

  1. **label:** «🔄 Обновить из upstream»
     **description:** «Подтянуть свежие BMad-скиллы, показать дифф, переприменить патчи»
     **preview:** «По умолчанию сухой прогон (ничего не пишет). Применение — отдельным шагом»

  2. **label:** «📊 Статус skills»
     **description:** «Версия upstream, применённые патчи, незакрытые конфликты»
     **preview:** «bmad-orchestrator skill-status»

  3. **label:** «↩ Назад»
     **description:** «Вернуться в меню Настройка»
     **preview:** «Возврат на шаг назад»

- 🔄 → сначала **сухой прогон** `Bash`: `bmad-orchestrator skill-update` (без `--apply` — только показывает дифф). Покажи результат, спроси через `AskUserQuestion`: «Применить изменения?» «✅ Применить» / «❌ Нет». При «Применить» — `Bash`: `bmad-orchestrator skill-update --apply`.
- 📊 → прямой `Bash`: `bmad-orchestrator skill-status`.
