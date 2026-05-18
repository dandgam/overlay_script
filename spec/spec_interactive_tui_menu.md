# Spec — Interactive TUI menu

> **Фаза ADLC:** Phase 4 (Deploy) — UX add-on, делает Phase 4 пилоты доступными без запоминания CLI args
> **Patterns used:** P2 Routing (operator → command selection через menu) + P1 Chaining (browse → fill params → execute → render result)
> **Объём:** 1-2 сессии (S-scale initiative)
> **Status:** Draft v1 — awaiting user approval

---

## 1. Зачем это

Сейчас Virgil управляется только через CLI:

```
bmad-orchestrator run --project antares --wave 1a --max-parallel 3 --real
bmad-orchestrator self-learning run --trigger wave_boundary_reached
bmad-orchestrator multi --projects antares,odyssey --wave 1a --daily-max-spend-usd 50
```

Это работает для разработчика, но создаёт **барьер для не-разработчика-оператора**:

| Проблема | Симптом |
|---|---|
| Нужно помнить **30+ subcommand имён** (`run`, `dag`, `retro`, `skill-update`, `self-learning run`, …) | Operator каждый раз `--help` |
| Нужно помнить **формат argument'ов** (`--wave 1a` vs `--project antares`) | Опечатки → `BadParameter` → откат |
| Нет **discovery** — не видно «что вообще можно сделать» | Operator не знает про `multi` / `self-learning` / `policy-apply` |
| Нет **safety prompts** на destructive ops (`stop --hard`, `policy-rollback`) | Случайные потери |
| Readonly TUI (`status --watch`) показывает state, но **не даёт действовать** из того же окна | Переключение терминал ↔ TUI |

**Interactive TUI menu** = терминальное приложение с навигацией стрелками:

```
┌─ Virgil — main menu ─────────────────────────────────┐
│ ▶ run         Запустить оркестратор на wave         │
│   status      Показать состояние                    │
│   dag         Показать готовые stories              │
│   ─ multi-project ─                                 │
│     init      Зарегистрировать проект               │
│     scan      Список проектов                       │
│   ─ self-learning ─                                 │
│     run       Запустить consolidation               │
│     status    Текущие настройки                     │
│   ─ ... ─                                           │
│                                                      │
│ ↑↓ navigate  ⏎ select  / search  ? help  q quit     │
└──────────────────────────────────────────────────────┘
```

Operator выбирает команду → форма с params (с подсказками + defaults) → confirm → run → live output → return-to-menu.

**Аналогия:** банкомат. Раньше нужно было знать «нажать 47 на цифровой панели чтобы перевести деньги». Теперь: «выберите перевод → введите получателя → подтвердите».

---

## 2. Что уже есть (re-use, не строим заново)

| Компонент | Где | Статус |
|---|---|---|
| 30 typer commands + 4 subgroups | `cli/main.py` | ✅ source of truth |
| Readonly TUI dashboard (`render_once`, `run_live`) | `cli/tui.py` | ✅ переиспользуем для embedded view |
| `DashboardSnapshot` schema | `cli/tui.py` | ✅ |
| `_validate_cli_token` для regex-валидации project/wave/story | `cli/main.py:206` | ✅ переиспользуем в форме |
| `load_settings()` для defaults | `config.py` | ✅ |
| Rich primitives (Table, Panel, Layout) | rich>=13.7 | ✅ уже в deps |
| `safe_resolve_path` / `ensure_inside_root` | `cli/path_validation.py` | ✅ для path-параметров |

**Не строим заново:** сами команды, валидация input, snapshot rendering, model resolution.

**Расширяем:** добавим один новый top-level command `menu` (рядом с `run` / `status` / etc.) — он запускает Textual app, который под капотом вызывает те же функции что и обычная CLI invocation.

**Новое:**
- `cli/menu/` модуль (discovery + tree + Textual app + forms)
- Textual dependency (новая)
- Snapshot tests (`pytest-textual-snapshot`) — новая dev dependency

---

## 3. Архитектура

### 3.1 Navigation flow (P2 Routing + P1 Chaining)

```
[Entry] bmad-orchestrator menu
    ↓
[Discovery] Walk typer app → MenuTree (cached on startup)
    ↓
[Browse] Textual ListView с hierarchical items (groups expandable)
    │
    ├── ↑↓ / j k   — navigate
    ├── ⏎          — select (group → expand, command → form)
    ├── /          — search-filter ("self" → all "self-learning *" items)
    ├── ?          — help overlay (keys + current command help)
    ├── b / ←      — back (breadcrumbs)
    └── q          — quit
        ↓
[Form] (для leaf command) — Textual Input fields per parameter
    │
    ├── Required без default → ввод обязателен
    ├── Optional с default → pre-filled
    ├── bool flag → checkbox
    ├── Path → file picker (Textual built-in)
    ├── Choice (литералы из Literal[]) → dropdown
    └── Confirm screen перед execute (показывает финальную CLI команду)
        ↓
[Execute] In-process call → typer command function
    │
    ├── stdout/stderr capture в Textual RichLog widget
    ├── live progress (для run / self-learning run)
    └── exit code → toast (✅ ok / ❌ failed)
        ↓
[Return-to-menu] любая клавиша → обратно в browse
```

### 3.2 Структура модулей

```
src/bmad_orchestrator/cli/menu/
├── __init__.py            # public API (launch_menu)
├── discovery.py           # walk typer.Typer → MenuTree
├── tree.py                # MenuNode pydantic (group/command/parameter)
├── app.py                 # MenuApp(Textual) — main UI
├── browse_screen.py       # ListView screen — навигация по tree
├── form_screen.py         # параметры формы для выбранной команды
├── execute_screen.py      # запуск команды + live output
├── help_overlay.py        # ? overlay с key bindings
└── safety.py              # destructive-command confirmation prompt

src/bmad_orchestrator/cli/main.py
└── new `menu` typer command (~15 строк — просто spawn'ит MenuApp)

tests/
├── test_menu_discovery.py   # parse typer app → tree (pure unit)
├── test_menu_tree.py        # tree navigation logic (pure unit)
├── test_menu_safety.py      # destructive-list классификатор
├── test_menu_app.py         # Textual pilot integration tests
└── snapshots/                # pytest-textual-snapshot baseline screens
```

Итого: **8 новых модулей** + 4 test файла + snapshot directory. Оценка: **~900 строк** (включая тесты).

### 3.3 MenuTree schema (pydantic v2)

```python
from pydantic import BaseModel, ConfigDict
from typing import Literal

class ParamSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str                      # e.g. "wave"
    cli_flag: str                  # e.g. "--wave"
    py_type: str                   # "str" / "int" / "bool" / "Path" / "Literal[...]"
    required: bool
    default: object | None
    help: str | None
    choices: list[str] | None      # для Literal/Choice
    is_path: bool = False          # render как file-picker
    is_secret: bool = False        # mask input (для будущих api-key)

class CommandNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["command"] = "command"
    name: str                      # leaf name: "run" / "skill-update"
    full_path: tuple[str, ...]     # ("self-learning", "run")
    help: str
    callback: object               # typer.models.CommandFunctionType
    params: list[ParamSpec]
    is_destructive: bool = False   # требует confirmation

class GroupNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["group"] = "group"
    name: str                      # "self-learning" / "model" / "bot"
    full_path: tuple[str, ...]
    help: str
    children: list["GroupNode | CommandNode"]
```

### 3.4 Discovery algorithm

```python
def discover(app: typer.Typer) -> GroupNode:
    """
    Walk typer.Typer структуру:
    - app.registered_commands → CommandNode для каждой
    - app.registered_groups   → GroupNode (рекурсивно)

    Для каждой команды:
    - Достать callback (typer.models.CommandInfo.callback)
    - inspect.signature(callback) → параметры
    - typer.Option/Argument metadata (default, help, type) — из default_value typing
    - Распознать Literal[...] → choices
    - Распознать Path → is_path=True
    - Помечать destructive: имена ∈ DESTRUCTIVE_NAMES (см. 3.5)
    """
```

Тесты покажут что discovery даёт стабильный tree (~30 commands, 4 groups) — snapshot закрепляем.

### 3.5 Destructive command list (3.5)

Hardcoded **allowlist** (НЕ configurable — security baseline):

```python
DESTRUCTIVE_COMMANDS: frozenset[tuple[str, ...]] = frozenset({
    ("stop",),                       # --hard kills workers
    ("policy-rollback",),            # перезапись policy YAML
    ("skill-update",),               # с --apply переписывает upstream
    ("self-learning", "rollback"),   # rollback policy
    ("multi",),                      # массовое spawning
    ("run",),                        # запуск real-mode (с --real)
})
```

Для них перед execute показывается **explicit confirmation screen** с текстом *что произойдёт* + повторный ввод имени команды (как `terraform destroy`).

### 3.6 Layout (Textual screens)

```
MenuApp (Textual App)
├── BrowseScreen (default)
│   ├── Header (breadcrumb: "main → self-learning")
│   ├── ListView (current level items)
│   ├── HelpHint (current item .help)
│   └── Footer (key bindings)
├── FormScreen (on command select)
│   ├── Header (full command path)
│   ├── Vertical ScrollableContainer (Input widgets per param)
│   ├── PreviewBox (rendered CLI: `bmad-orchestrator self-learning run --trigger ...`)
│   └── Footer ([⏎ Run] [b Back] [? Help])
├── ConfirmScreen (для destructive)
│   ├── Warning panel (red border)
│   ├── "Type command name to confirm:" Input
│   └── Footer ([⏎ Confirm] [esc Cancel])
├── ExecuteScreen
│   ├── RichLog (capturing stdout/stderr)
│   ├── Status (running / done / failed)
│   └── Footer ("Any key to return")
└── HelpOverlay (? overlay)
    └── ModalScreen со списком key bindings
```

### 3.7 Execution model

In-process (через прямой вызов typer callback'а):

```python
async def execute_command(node: CommandNode, values: dict[str, Any]) -> ExecuteResult:
    """
    1. Build kwargs from values (cast int/bool/Path)
    2. Redirect stdout/stderr to captured StringIO
    3. await asyncio.to_thread(node.callback, **kwargs)  # typer commands sync
    4. Catch typer.Exit → exit_code
    5. Catch any Exception → traceback string
    6. Return ExecuteResult(exit_code, stdout, stderr, error?)
    """
```

**Почему in-process** (не subprocess):
- ✅ Прямой доступ к exception (lockfile errors, parse errors сразу видны)
- ✅ Нет overhead на форк/imports (claude-agent-sdk init = ~2 сек)
- ✅ Capture stdout легко (`contextlib.redirect_stdout`)
- ❌ Долгие команды блокируют (mitigation: `asyncio.to_thread` + cancel button)
- ❌ Если callback вызывает `os._exit` — убивает menu (mitigation: ловим SystemExit)

Для `run` + `multi` (долгие команды) — в дальней perspective можно перейти на subprocess, но это **deferred** для MVP.

---

## 4. Что Interactive TUI menu НЕ делает (out of scope)

| Не делает | Почему |
|---|---|
| Не заменяет CLI | CLI остаётся primary; menu = удобный фронт |
| Не редактирует YAML/policy | Это `policy-apply` / manual edit |
| Не делает batch operations | Один запуск за раз; для batch — `multi` |
| Не показывает live progress workers | Это `status --watch` (отдельный TUI dashboard) |
| Не интегрируется с Telegram bot | Bot — другой UX surface; menu = local TTY |
| Не работает в web UI | Только terminal (Textual = terminal-only) |
| Не персистит history между sessions | Recent-N — in-memory only (MVP); persist — backlog |
| Не делает auto-discovery custom plugins | Только built-in typer commands |
| Не локализуется | UI — English+Russian mix (как остальной CLI); i18n — backlog |

---

## 5. Success criteria (Phase 4 gate)

| Criterion | Threshold |
|---|---|
| Discovery находит все 30+ typer commands + 4 subgroups | ✅ snapshot-test |
| Destructive commands требуют typed confirmation | ✅ test |
| Form правильно строится для всех parameter types (str/int/bool/Path/Literal) | ✅ test per type |
| Search-filter `/` находит команды по подстроке | ✅ test |
| Back navigation (breadcrumb) работает в любой глубине | ✅ test |
| Execute capture exit code + stdout + stderr | ✅ test |
| Long-running `run` не блокирует UI (cancellable) | ✅ manual + test через mock callback |
| Unit tests | ≥ 25, coverage ≥ 85% по `cli/menu/` |
| Snapshot tests (browse / form / confirm / execute screens) | 5 baseline snapshots |
| Full pytest suite без регрессий | 1657+ PASS |
| Mypy + ruff | clean |
| New dependency: textual added to `[project]`, pytest-textual-snapshot to `[dev]` | ✅ pyproject.toml |

---

## 6. План реализации (1-2 сессии)

### Session 1 — Foundation + Browse (M1)
- Добавить `textual>=0.80` в deps, `pytest-textual-snapshot` в dev
- `cli/menu/tree.py` — pydantic schemas (ParamSpec/CommandNode/GroupNode)
- `cli/menu/discovery.py` — walk typer app → MenuTree
- `cli/menu/safety.py` — DESTRUCTIVE_COMMANDS allowlist + classifier
- `cli/menu/app.py` — MenuApp skeleton + BrowseScreen (только навигация, без forms)
- `cli/menu/help_overlay.py` — ? overlay
- `cli/main.py` — добавить `menu` command (~15 строк)
- Tests: `test_menu_discovery.py` + `test_menu_tree.py` + `test_menu_safety.py` (~12 tests)
- Snapshot test для browse screen

**Phase gate session 1:** `bmad-orchestrator menu` запускается, можно навигировать по tree, увидеть help, выйти на `q`. Form/execute — placeholder.

### Session 2 — Forms + Execute + Polish (M2)
- `cli/menu/form_screen.py` — параметры формы (Input / Checkbox / FilePicker / Select)
- `cli/menu/safety.py` (расширить) — ConfirmScreen для destructive
- `cli/menu/execute_screen.py` — in-process call + stdout capture + cancel button
- Search-filter (`/`) в BrowseScreen
- Tests: `test_menu_app.py` (Textual pilot integration) + остальные snapshot'ы
- Methodology v13 update + commit

**Phase gate session 2:** end-to-end — operator может выбрать `self-learning status` → запустить → увидеть output → вернуться. Destructive commands требуют typed confirm. 25+ tests pass, mypy/ruff clean.

---

## 7. Открытые вопросы (нужны ответы ПЕРЕД стартом)

### Q1. UI framework — Textual или rich + prompt_toolkit или pure rich?
**Опции:**
- (a) **Textual** (https://textual.textualize.io) — built на rich, native arrow-key navigation, screens/widgets, pilot для testing
- (b) rich + prompt_toolkit — две библиотеки, ручная склейка, меньше features
- (c) pure rich + raw stdin escape codes — DIY, fragile, нет screen management

**Рекомендация:** (a) **Textual.** От того же maker'а что rich (Will McGugan), seamless integration. Pilot framework делает test'ы выполнимыми. Альтернатива (b) — лишняя сложность без выигрыша. Cost: новая dependency (~3 MB).

### Q2. Где живёт код — `cli/menu/` (новый модуль) или extend `cli/tui.py`?
**Опции:**
- (a) **Новый модуль `cli/menu/`** — изоляция interactive логики
- (b) Расширить `cli/tui.py` — добавить `run_interactive()` к существующему readonly renderer

**Рекомендация:** (a) **Отдельный модуль.** `cli/tui.py` — readonly rich.Live snapshot (используется в `status --watch`); interactive menu — другая парадигма (Textual App), смешивать = когнитивный мусор. `cli/menu/` импортирует `DashboardSnapshot` если понадобится встроенный preview.

### Q3. Execute model — in-process или subprocess?
**Опции:**
- (a) **In-process** — direct typer callback call в `asyncio.to_thread`
- (b) Subprocess — fork `bmad-orchestrator <cmd> --args ...`
- (c) Hybrid — short commands in-process, long (`run`/`multi`) subprocess

**Рекомендация:** (a) **In-process для MVP.** Subprocess добавляет overhead (~2s claude-agent-sdk init) + теряем direct exception. Длинные команды mitigation: `asyncio.to_thread` + cancel button. Hybrid (c) — premature complexity; добавим после первого pilot если будет реальная боль.

### Q4. Какие команды скрывать / помечать как destructive?
**Опции:**
- (a) **Hardcoded allowlist destructive** (6 commands из §3.5), всё остальное — visible
- (b) Hardcoded **hidden** (для internal-only вроде `cron-emit`), плюс destructive marker
- (c) Configurable через YAML

**Рекомендация:** (a) для MVP. Скрывать ничего не нужно — operator должен видеть всё что доступно. Hidden + configurable — добавим если реально understand нужно (YAGNI).

### Q5. Parameter input style — простые text fields или typed widgets?
**Опции:**
- (a) **Typed widgets**: Input для str/int, Checkbox для bool, FilePicker для Path, Select для Literal[...]
- (b) Только Input + parse при submit
- (c) Hybrid: typed для Literal/bool/Path, text для остальных

**Рекомендация:** (a) **Typed.** Textual это даёт почти бесплатно (built-in widgets). UX qualitatively лучше — operator не парсит `True`/`False`, выбирает из dropdown. Validation в форме (regex для `_PROJECT_RE` / `_WAVE_RE`) — inline error display.

### Q6. Recent commands history — persist или session-only?
**Опции:**
- (a) **Session-only** — recent N=5 в памяти, забываются при quit
- (b) Persist в `<orchestrator_home>/.claude/memory/menu-history.jsonl`
- (c) Не показывать recent вообще

**Рекомендация:** (a) **Session-only для MVP.** Persist — может помочь повторам, но рискует утечкой params (project paths, slugs); добавим если operator попросит. (c) теряет ценность (operator чаще всего бежит одну и ту же команду в session).

### Q7. Command name — `menu` или `interactive` или `tui`?
**Опции:**
- (a) **`menu`** — короткое, естественное, описывает паттерн (выбор из списка)
- (b) `interactive` — точнее но длинно (8 chars vs 4)
- (c) `tui` — конфликтует с readonly TUI dashboard (`status --watch`); confusing
- (d) Bare invocation `bmad-orchestrator` без subcommand — но breaks `no_args_is_help=True`

**Рекомендация:** (a) **`menu`.** Короткое, узнаваемое, не конфликтует. `bmad-orchestrator menu` читается естественно. Если operator привыкнет — добавим shell alias.

### Bonus Q. Subagent vs self-implementation
**Опции:**
- (a) Subagent (изолированно, я мерджу обратно) — параллельный путь, требует verify
- (b) Сам в этой сессии — последовательно, дольше но проще координировать

**Рекомендация:** (a) **Subagent с briefing'ом из этого spec'а** — мы уже обсудили tradeoffs. Я финально верифицирую и мержу.

---

## 8. Зависимости

**Pre-requisites (всё done):**
- ✅ 30+ typer commands stable (`cli/main.py`)
- ✅ Readonly TUI baseline (`cli/tui.py`)
- ✅ `_validate_cli_token` regex helpers
- ✅ `safe_resolve_path` для Path params
- ✅ pydantic v2 + rich (transitive: textual builds on rich)

**Unlocks:**
- Phase 4 #10 production pilot — не-разработчику легче запустить
- Onboarding новых users (если когда-то будут не-solo)
- Demos / screencasts — namestreaming menu intuitive

---

## 9. Риски + mitigation

| Риск | Likelihood | Mitigation |
|---|---|---|
| Textual API breaks (новая dep, активно развивается) | Medium | Pin `textual>=0.80,<2.0` + smoke test в CI |
| In-process call перехватывает `sys.exit`/`os._exit` некорректно → menu умирает | Medium | Wrap в `try: ... except (SystemExit, BaseException)`; log + return-to-menu |
| Discovery упускает params если typer signature нестандартен | Low | Snapshot-test закрепляет current 30+ commands; CI fails если расходится |
| Destructive command выполнен случайно через `--auto-apply`-эквивалент | Low | Hardcoded DESTRUCTIVE_COMMANDS + typed-name confirmation; нет «yes-to-all» режима |
| Long-running `run` блокирует UI → operator не может cancel | Medium | `asyncio.to_thread` + cancel button → `task.cancel()` |
| Snapshot tests flaky (terminal size зависим) | Low | Fixed size 80×24 в test fixtures |
| Cross-impact: добавление параметров к existing typer command (e.g. новый `--something`) → menu auto-показывает без human review | Low | Это feature, не bug — discovery should be authoritative |

---

## 10. References

- Universal methodology: `~/.claude/skills/build-agent/REFERENCE.md`
- Patterns используемые:
  - P2 Routing (navigation: tree level → next level)
  - P1 Chaining (browse → form → confirm → execute → result)
- Project methodology: `spec/methodology-virgil.md` Phase 4 (new item #12 после #10 production pilot)
- Sister specs (template applied): `spec/spec_supervisor_llm_loop.md`, `spec/spec_self_learning_loop.md`
- Existing infrastructure: `cli/main.py` (30+ commands), `cli/tui.py` (snapshot pattern), `cli/path_validation.py`
- Textual docs: https://textual.textualize.io (читать context7 при имплементации)
- pytest-textual-snapshot: https://github.com/Textualize/pytest-textual-snapshot

---

## 11. Что нужно от user'а ПЕРЕД стартом Session 1

8 решений:
1. ✅/❌ — Q1: Textual framework (не rich+prompt_toolkit)
2. ✅/❌ — Q2: новый модуль `cli/menu/` (не extend `cli/tui.py`)
3. ✅/❌ — Q3: in-process execute (не subprocess) для MVP
4. ✅/❌ — Q4: hardcoded DESTRUCTIVE_COMMANDS allowlist, остальное visible
5. ✅/❌ — Q5: typed widgets (не text-only)
6. ✅/❌ — Q6: session-only history (не persist)
7. ✅/❌ — Q7: command name `menu`
8. ✅/❌ — Bonus: subagent с briefing'ом vs self в этой сессии

Если по всем 8 рекомендациям согласие — стартуем Session 1.

---

**Last updated:** 2026-05-18
**Status:** Draft v1 — awaiting user review
