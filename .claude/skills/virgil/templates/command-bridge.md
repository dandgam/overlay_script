# command-bridge.md

> Как skill `/virgil` формирует команды для `bmad-orchestrator <subcommand>` и стримит вывод в чат.
> Читать **перед каждым** запуском CLI из меню.

## 0. ⚠️ Как вызывать CLI — полный путь обязателен

Исполняемый файл `bmad-orchestrator` живёт **внутри venv проекта**, его НЕТ в системном PATH. Голый вызов `bmad-orchestrator` упадёт с `command not found` (exit 127).

**Везде где в этом skill написано `bmad-orchestrator` — подставляй полный путь:**

```
/home/server/bmad-orchestrator/.venv/bin/bmad-orchestrator
```

Удобно задать переменную один раз в начале `Bash`-вызова:
```bash
VIRGIL=/home/server/bmad-orchestrator/.venv/bin/bmad-orchestrator
"$VIRGIL" status
```

**Резолюция пути (на случай если venv переедет):**
1. Сначала пробуй `/home/server/bmad-orchestrator/.venv/bin/bmad-orchestrator`.
2. Если файла нет — `/home/server/bmad-orchestrator/venv/bin/bmad-orchestrator`.
3. Если и его нет — `command -v bmad-orchestrator` (вдруг добавлен в PATH).
4. Ничего не нашлось → сказать пользователю: «CLI оркестратора не найден — проверь что `.venv` создан (`cd /home/server/bmad-orchestrator && python -m venv .venv && .venv/bin/pip install -e .`)».

## 1. Формат вызова

Skill **только формирует команду** из ответов меню и вызывает `Bash`. Никаких альтернативных путей запуска (Python import, subprocess.Popen, etc.) — только CLI.

Шаблон (с полным путём из раздела 0):
```bash
/home/server/bmad-orchestrator/.venv/bin/bmad-orchestrator <subcommand> [--flag1 value1 ...]
```

**Запуск:**
- **Foreground** (`run_in_background: false`) — для быстрых команд: `status`, `dag`, `logs`, `budget`, `retro`, `memory`, `scan`, `pause`, `resume`, `model show`, `validate-policy`, `skill-status`
- **Background** (`run_in_background: true`) — для long-running: `run`, `resume <project> <wave>`, `multi`, `status --live`, `eval run --mode real`, `sl-run`, `bot-start`

При background — сообщить пользователю: «Запустил в фоне. Буду уведомлён по завершении. Статус сейчас: `/virgil status`.»

## 2. Регистр доступных команд (что используется в Session 1)

| Subcommand | Назначение | Long-running? | Используется в |
|---|---|---|---|
| `run --project X --wave Y --max-parallel N` | Старт нового wave | ✅ background | Запустить → Новый wave |
| `resume --project X --wave Y` | Продолжить прерванный wave | ✅ background | Запустить → Продолжить |
| `pause` | Мягкая пауза | ❌ foreground | Запустить → Остановить |
| `resume` (без args) | Снять паузу | ❌ foreground | Запустить → Остановить |
| `stop --graceful` | Стоп после текущих stories | ⚠ background | Запустить → Остановить (destructive) |
| `stop --hard` | Немедленный SIGKILL | ❌ foreground | Запустить → Остановить (destructive) |
| `dag --wave X` | ASCII-граф DAG | ❌ foreground | Запустить → Dry-run + Статус → Подробно |
| `status` | Snapshot текущего состояния | ❌ foreground | Статус → Текущий |
| `status --live` | Live-обновление каждую сек | ✅ background | Статус → Текущий → Live |
| `budget [--wave X]` | Траты по wave/всем | ❌ foreground | Статус → Бюджет |
| `logs --worker W --tail N` | Логи worker'а | ❌ foreground | Статус → Логи |
| `retro --wave X` | Retrospective | ❌ foreground | Статус → Подробно |
| `memory --wave X` | Накопленная память | ❌ foreground | Статус → Подробно |
| `scan` | Список зарегистрированных проектов | ❌ foreground | Запустить (получить список проектов) |
| `init <path>` | Регистрация проекта | ❌ foreground | Запустить → Новый wave → ➕ |

**Команды для Session 2** (Настройка / Качество): `doctor`, `multi`, `resume-project`, `model show/set/save`, `validate-policy`, `policy-apply`, `policy-rollback`, `skill-update`, `skill-status`, `eval run`, `sl-run`, `sl-status`, `sl-rollback`, `sl-cron-emit`, `bot-start`, `bot-stop`, `sprint-planning`, `correct-course`, `investigate`.

## 3. Валидация free-text параметров

Skill принимает свободный текст от пользователя для параметров, которые нельзя описать enum'ом. Каждый ввод **обязательно** валидируется regex'ом **до** формирования команды.

| Параметр | Regex / правило | Пример валидного | Пример невалидного |
|---|---|---|---|
| `--wave` | `^[1-9][a-z]?$` | `1a`, `2b`, `3`, `9z` | `01a`, `wave1`, `1A`, `12a` |
| `--project` (slug) | существует в `bmad-orchestrator scan` | `antares`, `odyssey` | `not_registered`, `_bad` |
| `--worker` | `^w-[0-9]+$` | `w-1`, `w-42` | `worker1`, `W-1`, `w_1` |
| `--story` | `^[0-9]+\.[0-9]+$` | `1.1`, `2.3`, `15.7` | `1`, `1.`, `1.1.1`, `v1.1` |
| `--reason` (correct-course / investigate) | непустая строка, ≤500 символов | любой текст | пусто |
| Path для `init` | absolute path + `os.path.exists` | `/home/server/antares` | `./antares`, `~/p`, `/nonexistent` |
| Custom model id | `^claude-[a-z0-9-]+$` | `claude-opus-4-7`, `claude-haiku-4-5` | `gpt-4`, `Opus`, пусто |
| `--max-parallel` | integer 1 ≤ N ≤ 16 | `1`, `3`, `8`, `16` | `0`, `17`, `3.5`, `auto` |
| `--tail` | integer ≥ 1 (default 50 если пусто) | `50`, `200`, пусто | `0`, `-1`, `lots` |
| `--wave` ИЛИ `all` (для budget) | regex выше ИЛИ строка `all` | `1a`, `all` | `ALL`, `всё` |

**При невалидном вводе:**
1. Объяснить пользователю что именно не так (формат, диапазон).
2. Показать пример валидного значения.
3. Переспросить.
4. После 3 неуспешных попыток — предложить вернуться в меню или отменить операцию.

## 4. Сборка команды (примеры)

### Пример 1 — новый wave

Ответы меню:
- project = `antares` (выбор из scan)
- wave = `1a` (text, regex passed)
- parallelism = «🚶 3 (default)» → max_parallel = 3

Сборка: `bmad-orchestrator run --project antares --wave 1a --max-parallel 3`

Подтверждение пользователю (перед запуском): покажи команду точно как выше, спроси Да/Нет/Изменить.

Запуск: `Bash` с `run_in_background: true`.

### Пример 2 — статус

Skip-menu `/virgil status` → сразу `Bash` (foreground): `bmad-orchestrator status`. Без подтверждения.

### Пример 3 — логи worker'а с custom tail

Ответы:
- worker = `w-3` (text, regex passed)
- tail = `100` (text, integer)

Сборка: `bmad-orchestrator logs --worker w-3 --tail 100`. Foreground.

### Пример 4 — destructive stop

Drill-down → опция «💥 Stop --hard» → AskUserQuestion confirm placeholder → выбор «✅ Подтвердить» → `Bash` (foreground): `bmad-orchestrator stop --hard`.

**Session 3 апгрейд:** после первого confirm — текстовый ввод «STOP HARD» с exact match. Сейчас (Session 1) достаточно одного AskUserQuestion confirm.

## 5. Streaming stdout

### Foreground команды

`Bash` возвращает stdout как часть результата tool call. Skill показывает его пользователю как обычное сообщение. Для коротких команд (`status`, `dag`) — пользователь видит результат сразу.

Если stdout > 50 строк — обернуть в код-блок и упомянуть размер: «Полный вывод status (87 строк):».

### Background команды

`Bash` с `run_in_background: true` сразу возвращает control. Skill сообщает: «Запустил в фоне (`<command>`). Будут уведомления по завершении.»

**Не пытаться** читать output_file напрямую (overflow context). Если пользователь спросит прогресс — предложить `/virgil status` или `/virgil logs --worker <name>`.

При завершении background команды skill получит `<task-notification>` — тогда показать пользователю summary + предложить следующее действие (например, после успешного `run` → «Wave завершён. Посмотреть retro? `/virgil retro <wave>`»).

## 6. Error handling (Session 1 skeleton)

Полный decision tree придёт в `templates/error-handling.md` (Session 3). Сейчас минимум:

**Exit code 0:** ✅ «Готово.» + последние 10 строк stdout если они информативны (status, budget) или просто «Готово» если команда тихая (pause).

**Exit code != 0:** ❌ Показать stderr дословно + одну строку suggestion:

| Pattern в stderr | Suggestion |
|---|---|
| `BMadProjectNotFound` / `project '... ' not registered` | «Проект не зарегистрирован. Запустить `/virgil` → ⚙️ Настройка → ➕ Зарегистрировать? (доступно в Session 2)» |
| `WaveAlreadyRunning` | «Wave уже идёт. Проверь `/virgil status` или останови через `/virgil stop`.» |
| `BudgetExceeded` | «Превышен бюджет. Посмотреть детали: `/virgil` → 📊 Статус → 💰 Бюджет.» |
| `WaveNotFound` | «Такого wave нет в state.db. Проверь имя или зарегистрируй wave.» |
| `WorkerNotFound` | «Такого worker'а нет в текущем wave. Проверь имя через `/virgil status`.» |
| `Connection refused` / `bot not running` | «Сервис не запущен. Это команда для Session 2 (bot management).» |
| Unknown stderr | Просто показать stderr + «Покажи output если нужна помощь с разбором.» |

**Не пытаться auto-retry** — пользователь решает.

**Не пытаться auto-fix** ошибки регистрации/конфига — это требует подтверждения.

## 7. Security / safety guardrails

- **Никаких shell-инъекций.** Параметры передаются как **отдельные аргументы** Bash команды, не интерполируются в строку. Если пользователь ввёл что-то с `;`, `&&`, `|`, `$()`, backticks — отказать с сообщением «Недопустимые символы в параметре».
- **Path validation для init.** Перед `bmad-orchestrator init <path>` — проверить:
  - `os.path.isabs(path)` (absolute)
  - `os.path.exists(path)` (существует)
  - Не префикс: `/etc`, `/root`, `/proc`, `/sys`, `/dev`, `/boot`, `/lib`, `/sbin`, `/bin`, `/usr/bin`, `/var/log` (системные)
- **Destructive операции** — всегда через подтверждение (Session 1 placeholder + Session 3 typed-name).
- **Никогда не вызывать** команды не из таблицы раздела 2.
- **Никогда не предлагать** `git push --force`, `git reset --hard`, `rm -rf` — это вне scope skill.

## 8. References

- **SKILL.md** — главный файл (триггеры, меню)
- **Spec:** `spec/spec_virgil_skill.md` §9 (validation table), §11 (bash-bridge), §13 (skip-menu)
- **Python CLI source:** `src/bmad_orchestrator/cli/main.py` — источник истины по аргументам каждой команды
