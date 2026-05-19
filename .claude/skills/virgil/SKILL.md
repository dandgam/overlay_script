---
name: virgil
description: Интерактивное меню для управления Virgil (bmad-orchestrator) прямо в чате Claude Code. Запуск waves, статус, бюджет, логи, retrospective, DAG. Триггеры — «/virgil», «запусти virgil», «покажи статус оркестратора», «как там wave», «останови оркестратор», «virgil run», «virgil status».
---

# virgil

Управление Virgil (bmad-orchestrator) через кликабельное меню в чате Claude Code. Меню → выбор → сборка параметров → вызов `bmad-orchestrator <команда>` через `Bash`. Stdout стримится в чат.

**Все надписи на русском.** Python CLI — стабильный API, skill это только UI-слой.

## ⚠️ Как вызывать CLI

`bmad-orchestrator` живёт в venv проекта, **в системном PATH его нет**. Везде где в этом skill и в `menu/*.md` написано `bmad-orchestrator` — подставляй полный путь:

```
/home/server/bmad-orchestrator/.venv/bin/bmad-orchestrator
```

Это касается и сводки прогресса, и skip-menu вызовов. Детали резолюции (fallback если venv переедет) — в `templates/command-bridge.md` раздел 0.

## Что делать при вызове

1. **Skip-menu first.** Если первое сообщение содержит явный intent (см. «Триггеры») — пропусти меню, сразу собирай параметры или выполняй. Меню — для входа с `/virgil` без аргументов / «привет».
2. **Сводка прогресса.** Если идём по пути меню — сначала покажи обзор «Сейчас в работе» (см. ниже), потом меню.
3. **Главное меню → 4 опции** через `AskUserQuestion`. По выбору — **прочитай соответствующий файл** `menu/*.md` и следуй ему. Не держи детали sub-меню в голове заранее.
4. **Перед вызовом CLI** — прочитай `templates/command-bridge.md` (regex валидации, streaming, error-handling).
5. **Перед запуском CLI** — покажи сборку команды и спроси подтверждение. Кроме skip-menu кейсов.

**Что НЕ делать:**
- Не дублировать логику Python CLI — только формировать команду и звать `Bash`.
- Не вызывать команды не из таблицы `command-bridge.md`.
- Не предлагать `git commit` / `git push`.
- Не модифицировать Textual TUI (`src/bmad_orchestrator/cli/menu/*`) — сосуществуют.

## Прогрессивная загрузка (экономия токенов)

Этот SKILL.md — тонкий диспетчер. Детали sub-меню вынесены в отдельные файлы. **Читай их только когда пользователь реально зашёл в соответствующую ветку:**

| Файл | Когда читать |
|---|---|
| `menu/run.md` | Выбран «🚀 Запустить» |
| `menu/status.md` | Выбран «📊 Статус и логи» |
| `menu/config.md` | Выбран «⚙️ Настройка» |
| `menu/quality.md` | Выбран «🧪 Качество и обучение» |
| `templates/command-bridge.md` | Перед любым вызовом CLI |
| `templates/confirm-destructive.md` | (Session 3) destructive flow |
| `templates/error-handling.md` | (Session 3) разбор ошибок |
| `REFERENCE.md` | QUESTION mode — пользователь спрашивает «как сделать X» |

Не читай все файлы сразу — это и есть причина token bloat.

## Триггеры и skip-menu

| Пользователь пишет | Действие |
|---|---|
| `/virgil` без аргументов, «привет», «что там у нас» | Сводка + главное меню |
| `/virgil run <project> <wave>` | Skip → собрать параллелизм одним вопросом → запустить |
| `/virgil run <project> <wave> <N>` | Полный skip → `run --project X --wave Y --max-parallel N` |
| `/virgil status` / «покажи статус» / «что сейчас работает» | Skip → `status` |
| `/virgil status --live` | Skip → `status --live` |
| `/virgil dag <wave>` | Skip → `dag --wave <wave>` |
| `/virgil scan` | Skip → `scan` |
| `/virgil pause` / `/virgil resume` | Skip → соответствующая команда |
| `/virgil stop` | Прочитай `menu/run.md` → drill-down «Остановить» (нужно подтверждение) |
| «запусти на antares» / «запусти Y на X» | Меню → 🚀 → новый проход с предзаполненным проектом |
| «сколько потратили» / «какой бюджет» | Skip → спросить wave → `budget --wave X` |
| «логи работника X» | Skip → спросить tail → `logs --worker X` |
| «как сделать X в virgil» / «virgil умеет X?» | QUESTION mode — прочитай `REFERENCE.md`, ответь простым языком, без действий |

**Правило:** явное имя команды (run/status/logs/stop/scan/pause/resume/dag) или однозначный intent («запусти», «покажи статус», «останови») → пропустить меню.

## Сводка прогресса

Показывается **один раз перед главным меню** (не на skip-menu — там пользователь хочет быстро).

1. Вызови `Bash` (foreground): `bmad-orchestrator status`.
2. Разбери вывод (формат может меняться — бери что есть) и сведи в **короткий русский обзор**: по каждому активному проходу — проект, номер прохода, сделано X из Y задач, состояние (идёт / пауза / прерван), сколько работников активно.
3. Ничего не выполняется → «📋 Сейчас активных проходов нет.»
4. `status` упал → «Не удалось получить сводку» и **всё равно показать меню**.

Формат — компактный, ≤6 строк. Пример:

> 📋 **Сейчас в работе:**
> • **antares** · проход `1a` — сделано 5 из 8 задач, идёт, 3 работника активны
> • **odyssey** · проход `2b` — сделано 12 из 23 задач, на паузе

Не выводить сырой технический вывод `status` — только выжимку. После сводки — сразу главное меню.

## Главное меню

Вызов `AskUserQuestion`:

- **question:** «Что делаем?»
- **header:** «Virgil»
- **multiSelect:** false
- **options (4):**

  1. **label:** «🚀 Запустить»
     **description:** «Запустить новый проход, продолжить прерванный, посмотреть план или остановить»
     **preview:** «Например: запустить проход `1a` на проекте antares c 3 параллельными работниками»

  2. **label:** «📊 Статус и логи»
     **description:** «Что сейчас работает, сколько денег потрачено, логи, разбор прошлых проходов»
     **preview:** «Покажу текущее состояние, бюджет, картинку зависимостей или логи работника»

  3. **label:** «⚙️ Настройка»
     **description:** «Зарегистрировать проект, выбрать модели Claude, policy, обновить skills»
     **preview:** «Проекты · модели по ролям · policy · встроенные BMad-скиллы»

  4. **label:** «🧪 Качество и обучение»
     **description:** «Посмотреть чему агент научился, запустить обучение, отправить уроки на разбор»
     **preview:** «База self-learning: уроки, применённые правки, откаты»

**Маршрутизация по выбору:**
- 1 → прочитай `menu/run.md`, следуй ему
- 2 → прочитай `menu/status.md`, следуй ему
- 3 → прочитай `menu/config.md`, следуй ему
- 4 → прочитай `menu/quality.md`, следуй ему

## Правила (общие)

- **Все надписи на русском** в `label` / `description` / `preview` (CLI-команды в `preview` — английский ожидаем).
- **Preview как навигатор** — показывает что произойдёт, не дублирует `description`.
- **Не больше 4 опций** в `AskUserQuestion`. Больше — группировать в drill-down.
- **Подтверждение перед запуском** CLI. Кроме явных skip-menu кейсов.
- **Stdout стримится в чат.** Foreground — результат сразу; long-running (`run`, `status --live`) — background + уведомление.
- **Никаких mock значений** — параметр не получен → переспроси.

## Откладываем на Session 3

| Раздел | Что войдёт |
|---|---|
| `templates/confirm-destructive.md` | Двухшаговый confirm с вводом фразы («STOP HARD») для `stop --hard`, `policy-rollback`, `sl-rollback` |
| `templates/error-handling.md` | Подробный разбор ошибок CLI (decision tree «exit code != 0 → что предложить») |
| Полный набор skip-menu shortcuts | Доведение до 14 фраз |

## References

- **Spec:** `spec/spec_virgil_skill.md`
- **Python CLI:** `src/bmad_orchestrator/cli/main.py` (30+ команд)
- **Methodology:** `spec/methodology-virgil.md`

**Last updated:** 2026-05-19 (v0.5 — Session 2: Настройка + Качество доделано + REFERENCE.md)
**Status:** v0.5 — все 4 раздела меню готовы (Запустить · Статус · Настройка · Качество). REFERENCE.md для QUESTION mode. Осталась Session 3: destructive confirm + error-handling + skip-menu.
