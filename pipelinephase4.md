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

### S0 — pre-flight (validator · «впустить ли конвейер стартовать»)
**Кто исполняет.** Сам раннер `bmad-auto-dev-runner.sh` — это **один детерминированный скрипт**, который в начале выполняет inline-чек-лист из **7 проверок** (bash, коды выхода, без LLM). Любой провал → `HALT:preflight` (конвейер не стартует). Проверяется **окружение/предусловия**, а НЕ код и НЕ тесты (те — на S5/S6/S6.9).

| # | Проверка (как в коде) | Что валидирует | Провал → |
|---|---|---|---|
| 1 | `[[ -f epics.md ]]` | присутствует **план эпиков** (вход конвейера) | `exit 1` |
| 2 | `[[ -f sprint-status.yaml ]]` | присутствует **трекер спринта** (чем рулит выбор story) | `exit 1` |
| 3 | `[[ -f dependency_analyzer.py ]]` | на месте **валидатор выбора story** (нужен на S1) | `exit 1` |
| 4 | `[[ -f gauntlet_injector.py ]]` | на месте **инжектор Gauntlet** (нужен на S3) | `exit 1` |
| 5 | `[[ -f batch_gate.py ]]` | на месте **batch-гейт** (нужен на S7) | `exit 1` |
| 6 | `halt-reason.txt`: есть и нет `--resume`? | **безопасность возобновления** — не проскочить молча прошлый halt (с `--resume` файл удаляется) | `exit 2` (дамп halt) |
| 7 | `git diff` + `git diff --cached` чисто? | **нет незакоммиченных правок** (иначе подмешаются в ветку story / потеряются) | `exit 1` |

Проверки 3–5 — раннер убеждается, что **его собственные скрипты-помощники на месте**, прежде чем начать (без них поздние стадии упадут). Наличие `_bmad/` подразумевается проверками 1–2.

**Зачем все 7 (одной фразой):** убедиться, что (а) есть **чем работать** — план + трекер + скрипты (1–5), (б) **безопасно продолжать** — не проскакиваем прошлый halt (6), (в) **не испортим чужие правки** — дерево чистое (7). Это «read-only» контроль предусловий: ничего не меняет, только пускает/останавливает.

#### Действия S0 (после проверок — это уже НЕ валидации, а подготовка)
Проверка только *читает* и говорит да/нет. Действие *меняет* рабочее дерево, готовя место для запуска. После зелёных 7 проверок раннер делает два действия:

**1. `mkdir` каталогов состояния** — создаёт папки, куда конвейер складывает «память» и результаты (без них последующим стадиям некуда писать):

| Папка | Зачем / что хранит |
|---|---|
| `_bmad/auto-dev-state/` | блокнот раннера: `state.json` (на какой story), `halt-reason.txt`, `current-batch.json` — для восстановления после краха/resume |
| checkpoint-каталог | сводки на границах батча (для человека) |
| `_bmad/stories/` | сюда **S4 (create-story)** кладёт спеки историй |

**2. Интеграционная ветка** — создать/переключиться на долгоживущую `integration/<batch>`:
- раннер **никогда не льёт прямо в `main`**; он копит feature-ветки историй на `integration/<batch>`, а слияние `integration → main` делает **человек** по подтверждению;
- логика: с `--batch-name` → `integration/<name>` (существует — checkout; нет — создать из `main`); иначе — ветка из state-файла либо текущая; выбранную ветку раннер пишет в state (чтобы вернуться при resume).

После двух действий → **S1**.

> Таблица переходов выше даёт сжатую форму гейта S0 (`git_clean ∧ exists(epics, sprint-status) ∧ halt_reason==none`); полный список — эти 7 проверок (раннер дополнительно проверяет 3 свои скрипта).

### S1 — выбор следующей готовой story (selector · `dependency_analyzer.py`)
**Кто исполняет.** Раннер зовёт `python3 dependency_analyzer.py --next` — **детерминированный селектор** (не LLM; не валидатор кода — он РЕШАЕТ, за какую story браться).

**Читает 2 источника правды:**
- `epics.md` (ПЛАН) → по каждой story: `Dependencies` (от кого зависит), `Wave` (фаза), порядок в документе.
- `sprint-status.yaml` (ПРОГРЕСС) → по каждой story: `status` (done/pending/…), флаг `deferred`.

**Правило `next_ready`:** идти по story **в порядке документа**, вернуть **первую**, которая
① сама не `done` и не `deferred`; ② все её зависимости «закрыты» — каждая dep `done` ИЛИ `deferred`.
Нет такой → `{all_done:true}` → раннер `exit 0`. Пусто/сбой → `exit 4`.

**Три оси story (не путать):**
- **Epic** — *про что* (область фичи). Раннер **эпик-агностичен**: одна волна содержит story из РАЗНЫХ эпиков → они **перемешиваются** (можно 4.3 → 6.1 → 4.4, а НЕ «весь Epic 4, потом весь Epic 6»).
- **Wave** — *когда по плану* (умный порядок: фундамент → MVP-срез → детали). **Впечатана в порядок документа** → задаёт последовательность выбора; `--wave 0a` ограничивает раннер одной фазой. Зачем: из последовательной работы рано получить рабочий продукт + вехи/гейты, а не «эпик целиком».
- **Dependencies** — *что строго раньше* (жёсткий гейт).

**Риск `deferred`:** отложенная считается «закрытой» как зависимость → не блокирует зависимых. Безопасно для **мягких/внешних** deps (юр-консультация, параллельная фича); для **жёсткого** код-пререквизита — техдолг (зависимые «на песке»), который потом ловит **харнесс** при доделке. Скрипт мягкое от жёсткого НЕ отличает — доверяет человеку. Отложенное трекается в `deferred-work.md`; флип `deferred`→рабочий → анализатор подхватит.

### S2 — создание ветки story (action · git)
**Кто.** Сам раннер: `git checkout -b feature/story-<id>` **от интеграционной ветки** (не от `main`); имя из `[branches].feature_pattern` = `feature/story-{id}`.
- **Коллизия** (ветка уже есть — напр. прошлый провал) → F2-recovery: свободный суффикс `-retry-N` (старую ветку НЕ трогаем).
- **Явной проверки «создалось?» нет**, но `set -euo pipefail`: если `git checkout -b` упал → раннер **немедленно стоп** (до S3 не дойдёт). Имя ветки пишется в `current-batch.json` (resume).
- **Асимметрия с S4–S6 (gap):** этот стоп «грубый» — S2 **НЕ пишет** `halt-reason.txt` и отдаёт **сырой git-код** (часто 128), а не документированный 1/2/3/4. S2 — единственная мутирующая стадия без graceful-halt: для `--resume` следа причины не остаётся (валит сам `set -e`, не явный гейт).
**Зачем.** Изоляция: весь код story (S5) пишется в её ветку. Провал ревью → ветку **выбросить** (integration/main чисты); PASS → **слить в integration**. Это **действие** (git, детерминированно), не валидатор — единственное «решение» = обработка коллизии имени.

### S3 — gauntlet (hardener · FMEA / pre-mortem · ДЕТЕРМИНИРОВАННАЯ сборка промптов)
Закалка СПЕКИ враждебными линзами ДО кода. 5 линз (порядок фиксирован):
1. `failure_mode` — что сломается?
2. `edge_case` — граничные случаи?
3. `pre_mortem` — почему провалимся?
4. `devils_advocate` — а если наоборот?
5. `security_red_team` — как взломать? (STRIDE)

**Как в раннере (важно — здесь LLM НЕ вызывается):**
1. `gauntlet_injector.py --print-mode` → режим: **quick** (1 промпт, 5 линз вместе) · **deep** (5 промптов, по линзе) · **auto**. `auto` = DEEP, если эпик ∈ `deep_epics` (epic-4/5/7/10 = IAM/Billing/Compliance/AI-Gateway) ИЛИ текст истории содержит `deep_tag` (security-critical/compliance/billing/pii/crypto/rls/auth/credential/ai gateway), иначе QUICK. [Patch M: env-override режима для проблемных историй.]
2. `gauntlet_injector.py --mode <m> --dry-run` → `auto-dev-state/gauntlet/<id>/prompts.json`. Это **чистая сборка текста** (mail-merge): шаблоны 5 линз (`gauntlet-prompts.md`) + данные истории (заголовок/ACs/deps из `epics.md`) → промпты. **`claude` НЕ зовётся** (`--dry-run`). Лимиты `max_section_words=300` / `max_total=2000`.
3. **S3.5 — Patch J (гейт ФОРМЫ):** в `prompts.json` JSON непустой И присутствуют ВСЕ 5 маркеров линз? Нет → **HALT** (`missing-lenses`) — лучше стоп, чем сломанная спека уедет в dev-story. (Проверяет наличие линз, НЕ их качество.)

**Конец S3 = на диске `prompts.json` + Patch J зелёный.** LLM-вывода и обогащённой истории тут ещё НЕТ.

**Природа.** S3 в раннере **детерминированный** (Python: print-mode + dry-run + Patch J — без LLM). ⚠️ Расхождение со спиной-таблицей (`runner: [gauntlet_injector.py, opus]`): рассуждение Opus по линзам **отложено на S4** — инжектор *умеет* звать LLM сам (без `--dry-run`), но раннер этот путь не использует, делая закалку+написание истории одним вызовом Opus на S4. Запускать нечего → **не верифаер**; кода нет → **не ревью**; это **сборщик промптов + форм-гейт**.

### S4 — create-story (producer · `claude -p`, Opus) ⭐ ПЕРВЫЙ реальный вызов LLM в конвейере
**Кто.** Раннер зовёт `claude -p` моделью **Opus** (`[models].create_story`) выполнить `bmad-create-story`, скармливая промпты S3 (`auto-dev-state/gauntlet/<id>/prompts.json`). Это **водораздел**: S0–S3 были детерминированными (bash/Python), а здесь впервые «думает» LLM.
**Что делает Opus (два дела за один вызов):** (1) **ОТВЕЧАЕТ на 5 линз** из `prompts.json` — то самое адверсариальное рассуждение, что таблица приписывала S3, фактически происходит ТУТ (находки: что сломается / края / причины провала / контр-доводы / атаки); (2) **пишет детальный файл** `_bmad/stories/<id>.md`: задачи/подзадачи + контекст имплементатору + ACs, в которые **вшиты конвергентные находки** (edge-cases, риски, security).
**Автономность (Patch O).** Если workflow спрашивает «accept/reject» по Gauntlet-находкам → промпт велит **ACCEPT ALL** (конвейер автономный, человека не ждём); спорное → в R-list (deferred risks) внутри файла.
**Надёжность (обёртка `claude_with_api_retry`, Patch G/H/P).** Каждый `claude -p` (S4/S5/S6) обёрнут в страховку:
- **Patch G** — обрыв сети / 5xx → ∞ ретрай с backoff 30→300с;
- **Patch H** — жёсткий потолок одного вызова **2700с (45 мин)**; реальное зависание → kill, ≤2 попытки, затем halt;
- **Patch P** (2026-06-09) — rate-limit ИЛИ «кончились деньги / quota / usage-limit» → ждать **40 мин** и пере-проверять, ∞ (проверка ДО сетевой ветки, чтобы billing-ошибка не падала в быстрый backoff).
**Детерминированный гейт ПОСЛЕ (форма):** файл `_bmad/stories/<id>.md` создан? нет → **halt** (`missing-story-file`). Так проверяется, что producer реально выдал артефакт. → **S5** (dev-story, Sonnet — пишет код по этому файлу).

**Пошагово S4** (что · патч · строки `bmad-auto-dev-runner.sh`):

| # | Что происходит | Патч | Строки |
|---|---|---|---|
| ① | раннер строит строку-промпт: задача + путь к `prompts.json` + куда писать + автономность («NEVER ask human, ACCEPT ALL») | **Patch O** | `:434-437` |
| ② | вызов не голого `claude -p`, а через обёртку `claude_with_api_retry`; вывод → `stage4.log` | обёртка | вызов `:434`, фн `:109-162` |
| ②a | └ зависание процесса → kill на **2700с (45 мин)**, ≤2 попытки, потом halt | **Patch H** | `:99`, `:132-136` |
| ②b | └ обрыв сети (socket / 5xx / ECONNRESET) → backoff 30→300с, ∞ | **Patch G** | `:152-156` |
| ②c | └ **rate-limit / деньги / quota / usage-limit** → опрос каждые **40 мин (2400с)**, ∞; ДО сетевой ветки | **Patch P** | `:108`, `:140-150` |
| ③ | агент **сам** читает `prompts.json` по адресу (передача по ссылке) | — | директива «Use enriched prompts at …» |
| ④ | агент грузит скилл `bmad-create-story` (шаблон истории + чек-лист) | — | директива «Execute bmad-create-story» |
| ⑤ | думанье: отвечает на 5 линз (первый LLM) | — | внутри скилла |
| ⑥ | пишет `_bmad/stories/<id>.md` — задачи + контекст + ACs с вшитыми находками | — | директива «Output story file to …» |
| ⑦ | агент завершается; код ≠0 после 2 попыток → halt (graceful: пишет `halt-reason.txt`) | бюджет G/H/P | `:439-441` |
| ⑧ | **форм-гейт**: файл истории появился? нет → `HALT:missing-story-file`; да → **S5** | — (base) | `:444-446` |

#### S4 внутри: скилл `bmad-create-story` (что делает Opus ПОСЛЕ загрузки скилла)

Файлы скилла: `odyssey/.claude/skills/bmad-create-story/` — `SKILL.md` (429 строк) + `template.md` (каркас файла истории) + `checklist.md` (самопроверка) + `discover-inputs.md` (протокол загрузки артефактов) + `customize.toml` (база установок).

**Фаза А — Активация («установки», SKILL.md:26-66), 6 шагов:**

| # | Шаг | Что конкретно в Odyssey |
|---|---|---|
| 1 | `resolve_customization.py` склеивает 3 слоя TOML: база скилла → проект `_bmad/custom/bmad-create-story.toml` → личный `.user.toml` | user-слоя нет; правила слияния: скаляры — побеждает ближний слой, списки — складываются |
| 2 | activation_steps_prepend | пусто |
| 3 | persistent_facts («вечные факты» на всю сессию) | база: `file:**/project-context.md`; проект добавляет 8 фактов = **политика Gauntlet-3-сеньоров** на Шаге 5 |
| 4 | конфиг `_bmad/bmm/config.yaml` | язык, пути planning/implementation_artifacts, имя юзера |
| 5 | greet | в headless вхолостую |
| 6 | activation_steps_append | пусто |

⚠️ **Конфликт двух приказов (живой пример «промпт-гейт мягкий»):** проектный TOML (2026-05-14, писался для интерактива) велит на Шаге 5 показать таблицу находок Gauntlet и ждать accept/reject по каждой («DECISION STAYS WITH USER»); промпт раннера (Patch O) поверх: «NEVER ask human, ACCEPT ALL». Арбитра-реле нет — разруливает сам Opus (слушает более позднюю/конкретную директиву раннера). Два текстовых приказа могут спорить — исход решает LLM, не bash.

**Фаза Б — Workflow (SKILL.md:89-429), 6 шагов:**

| # | Шаг | Суть | Строки |
|---|---|---|---|
| ① | Чья история | id уже в промпте раннера → парс `5-1` → GOTO ②. Ветки «спросить юзера» / «найти первый backlog в sprint-status» — мёртвые в конвейере (выбор сделал dependency_analyzer на S1) | `:91-246` |
| ② | Артефакты | `discover-inputs.md` → epics (эпик целиком + AC истории) + PRD/arch/UX кусочно (SELECTIVE_LOAD) + **прошлая история эпика** (dev notes, грабли) + последние 5 коммитов git | `:248-284` |
| ③ | Архитектура → guardrails | стек/структура/API/DB/security/testing-правила; **CRITICAL: прочитать каждый меняемый файл** (что есть → что меняем → что не сломать) — «главная причина провалов имплементации» | `:286-317` |
| ④ | Web-research | свежие версии библиотек, breaking changes, security-патчи | `:319-339` |
| ⑤ | Написать файл | по `template.md` секция за секцией: header → requirements → developer context → 5 блоков guardrails → прошлый опыт → git → web → status; тут же Gauntlet 3 сеньоров (ACCEPT ALL в автономе) → находки вшить в ACs; `Status="ready-for-dev"` | `:341-392` |
| ⑥ | Финал | самопроверка по `checklist.md` → save → `sprint-status.yaml`: backlog → ready-for-dev → on_complete (пусто) → exit | `:394-427` |

**Суть скилла одной строкой:** не «скопируй из epics.md», а **сборка полного досье для Sonnet** — S5 стартует с пустой памятью и узнает о проекте ровно столько, сколько S4 положил в файл.

Побочная находка: в Шаге 1 блок авто-поиска backlog-истории продублирован дважды (`SKILL.md:130-189` ≈ `:190-245`) — copy-paste артефакт, в конвейере недостижим (GOTO на `:95`).

#### Под капотом `claude -p` (общее для S4/S5/S6): жизнь процесса-«курьера»

```
bash runner (диспетчер, живёт весь батч)
 └─ timeout --foreground --kill-after=10s 2700 (будильник, Patch H)
     └─ claude (Node.js, «курьер-однодневка»: 1 история = 1 жизнь)
         └─ HTTPS → api.anthropic.com (тут думает модель)
```

1. **Спавн** (`runner.sh:120`): блокирующий foreground-вызов; stdout+stderr → tmp-файл → в лог `reviews/<id>-stageN.log` **только после смерти процесса** (`cat tmp | tee -a`) — live-наблюдения НЕТ.
2. **cwd**: раннер сделал `cd "$PROJECT_DIR"` один раз (`:67`); дети наследуют. «Working dir:» в промпте — лишь страховка текстом.
3. **Settings** (приоритет выше→ниже): managed → CLI-флаги → `.claude/settings.local.json` → `.claude/settings.json` (у нас НЕТ) → `~/.claude/settings.json`. В команде раннера флагов кроме `-p` НЕТ → модель и права берутся из user-настроек.
4. **Сборка контекста до первой мысли**: системный промпт + `~/.claude/CLAUDE.md` + `<project>/CLAUDE.md` + MEMORY.md + **только name+description** всех скиллов (тела грузятся on-demand) + MCP/инструменты/git-статус; SessionStart-хуки срабатывают и в headless.
5. **Промпт `-p` = первое и единственное user-сообщение**; человека в цикле нет — отсюда директива Patch O в тексте.
6. **Активация скилла**: модель матчит «Execute bmad-create-story» с description → Skill tool → тело SKILL.md инжектится в контекст → Фаза А → Фаза Б.
7. **Каждый tool call → permission-фильтр**: у нас headless едет на user-allowlist **`Bash(*)`** + Read/Edit/Write/Agent/mcp__* при коротком deny (`rm -rf`, force-push, reset --hard, DROP/TRUNCATE, .env). НИ `bypassPermissions`, ни `--dangerously-skip-permissions` нигде нет.
8. **Смерть**: финальный текст → stdout → лог; exit 0 = «не крашнулся», НЕ «сделал хорошо» (агент может сдаться текстом и выйти нулём — поэтому нужен форм-гейт файла).
9. **Диспетчер судит**: exit≠0 → классификация обёрткой (hang ≤2 / quota 40-мин ∞ / сеть backoff ∞ / прочее → halt); exit 0 → форм-гейт → следующая стадия.

**Слабые места (аудит 2026-06-10):**

| # | Слабость | Где | Риск / фикс |
|---|---|---|---|
| 1 | модель S4/S6 не закреплена флагом — едет user-default (сейчас Fable 5, не Opus из доки) | `:434`, `:501` | поведение дрейфует от ручки `/model`; фикс: явный `--model opus` |
| 2 | auto-fix и re-review — голый `claude -p` БЕЗ `claude_with_api_retry` | `:534`, `:631` | нет 45-мин будильника/ретраев: зависание вечно, обрыв сети = HALT; фикс: обернуть |
| 3 | Patch P судит по словам лога (`billing\|quota\|insufficient`), а лог = текст агента | `:140` | упавшая billing-история (Эпик 5) с «billing» в выводе → вечный 40-мин опрос вместо HALT; фикс: сузить до HTTP 429/402 |
| 4 | `Bash(*)` у всех headless при коротком deny-list | user settings | промпт-инъекция из артефактов = почти любая команда; риск принят (свои артефакты), пересмотреть при внешних входах |
| 5 | вывод не live (tmp → лог после смерти) | `:120-126` | 45 мин не видно жив ли агент (урок Story 1.5); фикс: tee напрямую |
| 6 | гейт формы, не содержания (файл есть?) + checklist = самопроверка той же сессией | `:444` | плохое досье едет до S6; фикс: python-валидатор секций (как Patch J для линз) |

### S5 — dev-story (producer · `claude -p`, **Sonnet**) — пишет КОД по спеке
**Кто.** Раннер зовёт `claude --model sonnet -p` выполнить `bmad-dev-story` (`:449-455`). **Смена модели Opus→Sonnet:** на S4 нужна «творческая голова» (придумать спеку), на S5 — исполнитель по готовому чертежу; имплементация по спеке дешевле (cost-routing `[models].dev_story = sonnet`). Танец моделей: Opus думает (S4) → Sonnet делает (S5) → Opus проверяет (S6).
**Вход — по ссылке (как S4).** Sonnet НЕ видит сессию S4; он читает файл `_bmad/stories/<id>.md` (что S4 написал) своим Read. Контекст между стадиями — через диск.
**Что делает Sonnet:** (1) грузит скилл `bmad-dev-story` (роль «разработчик»); (2) читает спеку; (3) **пишет код**; (4) **коммитит на ветку story** `feature/story-<id>` — здесь впервые появляются коммиты кода; (5) перед «готово» сам гоняет `cargo check --workspace` (директива в промпте).
**Метод — TDD (red-green-refactor), скилл `bmad-dev-story`.** Не «просто пиши код», а тест-первым, по каждой задаче спеки строго по порядку (`:298`, `:322` — никакой отсебятины сверх задачи):
- 🔴 **RED** — пишет ПАДАЮЩИЙ тест первым, убеждается что падает (`:304-305`); пока тест не упал — он ничего не проверяет;
- 🟢 **GREEN** — минимальный код, чтобы тест прошёл (`:308-309`);
- 🔵 **REFACTOR** — чистит структуру, тесты держит зелёными (`:313`);
- шаг 6: дописывает тесты unit+integration+e2e (`:328`); шаг 7: гоняет ВСЕ тесты (регрессий нет?) + новые + lint + сверка ВСЕХ AC (`:335-340`);
- шаг 8: отметить задачу `[x]` ТОЛЬКО если реально зелено (**«NO LYING OR CHEATING»** `:346`) → есть задачи? назад на шаг 5; нет → шаг 9;
- шаг 9: финал — все `[x]`? полный регресс? Definition-of-Done? → `Status="review"` (`:396-414`).

**Один агент, без субагентов** (в отличие от S6 с 3 охотниками): Sonnet работает последовательно, непрерывно до COMPLETE или HALT.

**Внутренние HALT'ы скилла** (отдельно от halt'ов раннера): новая зависимость сверх спеки (`:318`), **3 провала подряд** (`:319`), нет конфига (`:320`), неоднозначное требование (`:206`), файл спеки недоступен (`:205`); регрессия упала → STOP и чинить (`:341`).

**Слои доверия (зачем дубли проверок):** ① LLM-самоотчёт «тесты прошли» (скилл — верить нельзя на 100%) → ② `cargo check` (S5.5, детерминир.) → ③ реальные тесты на живой БД (S6.9 ci-local, truth-moat). «NO LYING» в скилле — признание, что LLM может отрапортовать о непрогнанных тестах; поверх стоят детерминированные гейты.

**Надёжность.** Тот же `claude_with_api_retry` (Patch G/H/P). Код ≠0 после 2 попыток → `halt` (`stage=5 reason=claude-nonzero-after-retry`, `:456-458`).
**Гейта «файл создан?» тут НЕТ** (в отличие от S4) — роль гейта берёт на себя S5.5.

### S5.5 — build-check (validator · `cargo check`, Patch N) — «компилируется?» + self-heal
**Зачем.** Перед дорогим Opus-ревью (S6) — дешёвая проверка, что workspace вообще собирается. Ловит отсутствующий `Cargo.toml`, неразрешённые импорты, несовпадение типов (урок Story 1.11: 2806-строчный фикс ссылался на ненаписанные типы; ревью пропустило — **ревьюер читает код, а не граф сборки**). Цена: `cargo check` ≈10с / ~$0.001 против ~$15 впустую на Opus-ревью сломанной сборки.
**Что делает (`:468-496`):**
1. Если `cargo` нет ИЛИ нет `Cargo.toml` (не-Rust история) → **скип** (warn). S5.5 — Rust-specific.
2. `cargo check --workspace`. Чисто → **S6**.
3. Сломалось → **self-heal loop** (не просто гейт): хвост ошибок сборки скармливается обратно Sonnet с директивой «почини минимально, спеку не трогай» (`:482-488`), ≤2 попытки.
4. После `PATCH_N_MAX_RETRIES` без зелёного → `halt` (`reason=cargo-check-fail-after-N-retries`, `:491-493`).
**Природа.** Гейт boolean (`exit_code==0`) **+ ремонтная петля**: в отличие от чистого гейта, S5.5 не только судит, но и чинит (Sonnet-retry с ошибками как контекстом). Отличие от S6.9 verify: S5.5 только КОМПИЛИРУЕТ (`cargo check`), не запускает тесты/миграции.

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

### S7 / S8 — батч-гейт и чекпоинт (каденция остановок на человека)
**S7 (`batch_gate.py --check`)** после **каждой** story проверяет границу батча. Граница = **первое из** (по приоритету):
1. `batch_size_reached` — набрали `[batch].size` story (дефолт скилла **10**; в odyssey `customize.toml` → **20**) — жёсткий потолок;
2. `gate_story` — последняя story помечена `**Type:** 🚦 GATE story` (или в config-списке / в названии «gate») — плановая пауза;
3. `wave_transition` — следующая готовая story в **другой волне**.
Нет границы → назад на **S1** (батч копится). Есть → **S8**.

**S8 (checkpoint halt):** собрать сводку батча в `auto-dev-state/checkpoint-log/<batch-N>.md` → `exit 3` («готово к ревью AABIT»). Раннер **сам в `main` НЕ льёт** — копит в `integration/<batch>`; merge `integration → main` делает **человек** (approve / abort / inspect).

**Две каденции — не путать:**
| Каденция | Когда | Что | Кто |
|---|---|---|---|
| **Batch checkpoint** (S7→S8) | каждые **≤10 story / gate / смена волны** | approve merge батча, продолжить | авто-стоп + человек (быстро) |
| **Эпик-ретроспектива** | после **каждого эпика** | уроки + правка процесса (12 шагов) | человек (`bmad-retrospective`, вне раннера) |

Батч — это про **частоту ревью человеком** (единица исполнения), волна — про **план**, эпик — область фичи + триггер **ретроспективы**.

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
