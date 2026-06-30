# BMAD v6.2.2 — Phase 4 «Implementation»: подробный разбор

> Продолжение серии разборов. Phase 1 была целиком optional и держалась на дисциплине документов (HALT-гейты, FORBIDDEN-роли, frontmatter-state, JSON-контракты субагентов, анти-галлюцинация через сорсинг). Phase 4 — первая фаза, где LLM трогает **настоящий код**, и единственная, где появляются **required-компоненты** и хоть какая-то машинная истина (exit-коды тестов). Как автор BMAD распорядился этой истиной — главный сюжет разбора.

**Честность о покрытии.** В `4-implementation/` — 13 подкаталогов. Глубокие пограничные раскопки (построчные, с цитатами) сделаны по четырём: `bmad-create-story`, `bmad-dev-story`, `bmad-code-review`, `bmad-qa-generate-e2e-tests`. По остальным девяти (sprint-planning, sprint-status, correct-course, retrospective, quick-dev и 4 agent-персоны) — только верхнеуровневая проверка критика: ключевые факты есть, гранулярного разбора нет. Где данных нет — так и пишу.

---

## 1. Карта фазы

### 1.1 Откуда порядок известен

Единственный **формальный** источник порядка скиллов фазы — `_bmad/bmm/module-help.csv` (машиночитаемая карта с полями `after` / `before` / `required`). Всё остальное — проза «Next Steps» в конце workflow'ов, которая этот порядок дублирует словами (и, как увидим, местами уже разъехалась с реальностью).

### 1.2 Конвейер

```
Phase 3 (Solutioning)                Phase 4 (Implementation)                         дальше
─────────────────────   ──────────────────────────────────────────────────────   ─────────────
bmad-create-epics-       sprint-planning ──> create-story:create ──> [validate]
and-stories                 (required)           (required)          (optional)
        │                                             │
        ▼                                             ▼
bmad-check-implemen-                            dev-story ──> code-review ──> qa-generate-e2e
tation-readiness                                (required)    (optional,        / retrospective
   (required gate)                                            auto-marks done)   (optional)
                                                      ▲              │
                                                      └──── петля ───┘  ← НА БУМАГЕ. По факту
                                                                          разорвана (см. §2.3)
```

Параллельно основному конвейеру живут:
- **bmad-sprint-status** — диспетчер-навигатор «где мы и что дальше» (вызывается в любой момент);
- **bmad-correct-course** — аварийный выход из фазы 4 **обратно в фазы 2-3** («требования поменялись — переделать PRD/architecture»);
- **bmad-quick-dev** — **альтернативный конвейер** (Quick Flow), минующий весь story-цикл: intent → spec → implement → review;
- **4 agent-персоны** — второй способ вызова тех же скиллов (через «персонажа» вместо прямого `/skill`).

### 1.3 Таблица компонентов

| Компонент | Required? (module-help.csv) | Роль | Вход | Выход | Глубина раскопок |
|---|---|---|---|---|---|
| `bmad-sprint-planning` | **required** | генерит `sprint-status.yaml` из epics.md | epics.md | `sprint-status.yaml` (state machine на диске) | поверхностная |
| `bmad-create-story` (action: create) | **required** | «контекст-инжиниринг»: упаковывает ВСЁ нужное dev-агенту в один story-файл | epics.md (primary), PRD/architecture/UX (fallback), git-история, web | story-файл `ready-for-dev` + статус в sprint-status.yaml | **глубокая** |
| `bmad-create-story` (action: validate) | optional | fresh-context валидатор story («Validate Story (VS)» в module-help.csv) | story-файл | улучшенный story-файл | глубокая (checklist.md) |
| `bmad-dev-story` | **required** | исполнение story по red-green-refactor + DoD | story-файл `ready-for-dev` | код + тесты + story в статусе `review` | **глубокая** |
| `bmad-code-review` | optional (рекомендован; «auto-marks done») | адверсариальное ревью 3 параллельными слоями + триаж | diff (5 источников) + story | находки в story, `deferred-work.md`, статус `done`/`in-progress` | **глубокая** |
| `bmad-qa-generate-e2e-tests` | optional | пост-имплементационные API/E2E-тесты | готовый код | тесты + `tests/test-summary.md` | глубокая |
| `bmad-retrospective` | optional | пост-epic ретро, party-mode мультиагентный круглый стол | завершённый epic | retrospective-документ | поверхностная |
| `bmad-sprint-status` | — (сервисный) | навигатор/валидатор состояния спринта | sprint-status.yaml + story-файлы | отчёт + роутинг к следующему скиллу | поверхностная |
| `bmad-correct-course` | — (аварийный) | управление изменениями, импакт-анализ | PRD+epics (обязательны, HALT без них) | `sprint-change-proposal-{date}.md` → в planning-artifacts | поверхностная |
| `bmad-quick-dev` | — (альтернативный путь) | мини-конвейер без story-цикла | запрос пользователя | `spec-wip.md` (900-1600 токенов) + код | поверхностная |
| `bmad-agent-dev` (Amelia), `bmad-agent-sm` (Bob), `bmad-agent-qa` (Quinn), `bmad-quick-flow-solo-dev` (Barry) | — (слой вызова) | персоны-обёртки над скиллами (DS+CR / SP+CS+ER+CC / QA / QD+CR) | — | — | поверхностная |

### 1.4 Реестр артефактов фазы (что появляется на диске)

| Артефакт | Кто пишет | Кто читает |
|---|---|---|
| `{implementation_artifacts}/sprint-status.yaml` | sprint-planning (создаёт), create/dev-story, code-review (синкают) | все скиллы фазы |
| story-файлы `<epic>-<story>-<slug>.md` | create-story (верх), dev-story (низ), code-review (findings) | dev-story, code-review, человек |
| `{implementation_artifacts}/deferred-work.md` | code-review (defer-корзина) | человек, будущие ревью |
| `{implementation_artifacts}/tests/test-summary.md` | qa-generate-e2e-tests | человек |
| retrospective-документ | retrospective | человек, будущие story |
| `{implementation_artifacts}/spec-wip.md` | quick-dev (WIP-state) | quick-dev |
| `{planning_artifacts}/sprint-change-proposal-{date}.md` | correct-course | фазы 2-3 |

### 1.5 Зависимость от `_bmad/core/`

Фаза 4 **не самодостаточна**: ревью-охотники (`bmad-review-adversarial-general`, `bmad-review-edge-case-hunter`), `bmad-init` (конфиг персон), `bmad-shard-doc` (производит шардированные доки, которые потом читает discover-inputs), `bmad-party-mode`, `bmad-advanced-elicitation` — всё это живёт в `_bmad/core/` и переиспользуется фазой, а не дублируется.

---

## 2. Покрупичный разбор компонентов

### 2.1 bmad-create-story — «контекст-инжиниринг story»

Автор объявляет этот скилл «самой важной функцией всего процесса разработки». Ставка: качество работы LLM-разработчика определяется качеством упакованного контекста, а не способностями модели.

#### Крупица 1: активация скилла
**Файл:** `bmad-create-story/SKILL.md`

- **Названо в файле:** ничего — методологических имён нет.
- **Узнаваемо без имени:** thin-shim активация — весь файл 6 строк: frontmatter (триггеры вроде «create the next story») + «Follow the instructions in ./workflow.md.» Паттерн skill-манифеста из Claude Code skills (Anthropic, 2024-2025) — атрибуция раскопщика, confidence high.
- **Изобретение BMAD:** триггерные фразы в `description` — натуральный язык вместо CLI-аргументов как контракт вызова.
- **Замысел автора:** манифест не раздувает контекст при индексации скиллов; workflow правится без «перерегистрации».
- **Настроить под себя:** триггерные фразы — правкой `description`.

#### Крупица 2: преамбула роли + INITIALIZATION
**Файл:** `workflow.md`, строки 1-48

- **Названо в файле:** **BDD** («Enhanced epics+stories file with BDD and source hints») — Dan North, 2003-2006.
- **Узнаваемо без имени (атрибуция раскопщика):**
  - *Context engineering* для LLM-агентов: «Your Role: Story context engine that prevents LLM developer mistakes, omissions, or disasters» — практика 2024-2025, BMAD — один из ранних носителей в agile-обёртке (high);
  - *Таксономия отказов как дизайн-вход*: список «COMMON LLM MISTAKES TO PREVENT: reinventing wheels, wrong libraries, wrong file locations, breaking regressions, ignoring UX, vague implementations, lying about completion, not learning from past work» — по духу FMEA (анализ видов отказов, военпром 1950-70е), перенесённая на LLM; атрибуция по аналогии (medium);
  - *Map-reduce через параллельных субагентов*: «research subagents... analyze different artifacts simultaneously» (high);
  - *Батчинг прерываний*: «SAVE QUESTIONS... for the end» + «ZERO USER INTERVENTION» — слабая атрибуция к maker's schedule (low).
- **Изобретение BMAD:** конфиг-резолюция из `_bmad/bmm/config.yaml` ({communication_language}, {user_skill_level}); Input Files таблица с декларативными load strategy; иерархия источников «epics уже обогащён, PRD/architecture — fallback».
- **Замысел автора:** преамбула — контракт роли, прямо нацеленный против известных провалов LLM. Каждый шаг workflow дальше закрывает конкретный пункт таксономии.
- **Настроить под себя:** язык/skill level/пути — `config.yaml`; состав входов — Input Files таблица; глобальные стандарты — `**/project-context.md` (подхватывается автоматически).

> ❗ **Дрейф (системный, уровня всего модуля):** workflow резолвит `user_name`, `document_output_language`, `date` — в установленном `config.yaml` v6.2.2 этих ключей **нет** (есть 7: project_name, user_skill_level, planning_artifacts, implementation_artifacts, project_knowledge, communication_language, output_folder). Проверка критика: те же несуществующие ключи резолвятся в **8 workflow фазы** (create-story, dev-story, code-review, sprint-planning, sprint-status, correct-course, qa-generate, quick-dev). Это дефект инсталлятора/шаблона, не одного файла. LLM на неразрешимой переменной импровизирует.
>
> ❗ **Дрейф:** двойной источник путей — секция Paths задаёт `epics_file={planning_artifacts}/epics.md`, а Input Files таблица — glob `*epic*.md`. Классический single-source drift.
>
> ❗ **Дрейф:** Input Files помечает prd/architecture/ux как `SELECTIVE_LOAD`, тогда как `discover-inputs.md` прямо предписывает `FULL_LOAD` «Use this for PRD, Architecture, UX». Деталь критика: epics в таблице тоже SELECTIVE_LOAD, но у него есть переменная `{{epic_num}}` — исполнимо; у prd/ux переменной нет → стратегия **неисполнима по букве протокола**.

#### Крупица 3: Step 1 — выбор целевой story + sprint state machine
**Файл:** `workflow.md`, строки 54-209

- **Названо в файле:** **Sprint planning** («Run `sprint-planning` to initialize sprint tracking») — Scrum, Schwaber & Sutherland; **Backlog** («Status value equals "backlog"») — Scrum Guide; **Retrospective** — Scrum / Norm Kerth 2001; **Epic** — agile-фольклор (Cohn, SAFe).
- **Узнаваемо без имени:**
  - *Pull-система с WIP=1*: «Find the FIRST story (by reading in order from top to bottom)» — порядок файла = приоритет, берётся ровно одна. Kanban pull / one-piece flow (Anderson 2010) — medium;
  - *State machine со статусами и валидацией переходов*: нелегальные состояния → ERROR + HALT — Jira-style ticket lifecycle (high);
  - *Backward compatibility легаси-статусов*: «contexted (legacy) → in-progress» — tolerant reader (Fowler), medium.
- **Изобретение BMAD:** `sprint-status.yaml` как **state-на-диске между скиллами** (create-story пишет, dev-story читает) — машиночитаемый канал вместо памяти LLM; жёсткое «MUST read COMPLETE file from start to end to preserve order» — защита от LLM-привычки грепать вместо полного чтения; HALT-выходы с меню восстановления (3 пронумерованные опции + [q]).
- **Замысел автора:** детерминированный диспетчер поверх недетерминированного исполнителя. Следующая story выбирается **позицией в файле**, а не «суждением» LLM. Это Scrum-механика, сведённая к парсингу YAML.
- **Настроить под себя:** приоритизация = переупорядочить ключи `development_status` в sprint-status.yaml; обход диспетчера = передать `{{story_path}}` аргументом.

> ❗ **Дрейф (крупный дубль):** строки 153-208 дословно повторяют 94-152 — блок auto-discovery продублирован вне `<check>`. Дубль — мёртвый код (обе ветки выше заканчиваются GOTO), различие — только убранные эмодзи 🚫/📋/📊. След copy-paste-редактуры.
>
> ❗ **Дрейф (с поправкой критика):** «GOTO step 2a» встречается **5 раз** (строки 58, 84, 89, 151, 208 — раскопки писали «4», проверка показала 5), но шага `2a` в workflow **нет** (шаги n=1..6). Нюанс: «2a» существует как подшаг в `discover-inputs.md:18` («2a: Try Sharded Documents First») — вероятный источник заражения нумерации. LLM вынужден догадываться, что имеется в виду step 2.

#### Крупица 4: Step 2 — анализ артефактов + previous story intelligence + git intelligence
**Файл:** `workflow.md`, строки 211-248

- **Названо в файле:** **User story (As a / I want / so that)** — XP (Beck), популяризовано Cohn 2004; **BDD** («acceptance criteria already BDD formatted») — North; Given/When/Then — North & Matts.
- **Узнаваемо без имени:**
  - *Connextra-шаблон* user story — команда Connextra (Rachel Davies и др.), 2001 (high);
  - *Lessons-learned carry-forward*: «PREVIOUS STORY INTELLIGENCE: Dev notes and learnings from previous story... Testing approaches that worked/didn't work» — knowledge management (Kerth 2001, PMBOK); в LLM-мире — agent memory pattern (high);
  - *Mining software repositories*: «Get last 5 commit titles... Analyze 1-5 most recent commits... Code patterns and conventions used» — MSR-сообщество с 2004 (medium);
  - *ATDD-подготовка*: критерии приёмки фиксируются до разработки — Pugh 2010, корни в XP/FIT. **Важная оговорка раскопщика:** это ATDD только по букве — автоматических приёмочных тестов до кода никто не пишет (medium).
- **Изобретение BMAD:** «Previous story intelligence» как формализованный раздел — **память конвейера живёт в story-файлах**, а не в голове модели; иерархия источников (epics primary, PRD/architecture/ux fallback).
- **Замысел автора:** закрывает пункт «not learning from past work»: предыдущая story и git-история — единственные носители фактических паттернов кодовой базы; автор заставляет извлекать их явно.
- **Настроить под себя:** глубина git-анализа — правка «last 5 commit titles» в тексте шага.

> ❗ **Дрейф:** форматирование шага разрушено автоформаттером — маркеры **EPIC ANALYSIS:** / **STORY FOUNDATION:** влиты в одну строку с тегами `<action>`. LLM прочитает, структура списков потеряна.

#### Крупица 5: Step 3 — architecture analysis for developer guardrails
**Файл:** `workflow.md`, строки 250-269

- **Названо в файле:** ничего.
- **Узнаваемо без имени:** *architecture conformance / guardrails*: «Extract everything the developer MUST follow!» + 9 категорий (Stack / Structure / API / DB / Security / Performance / Testing / Deployment / Integration) — школа SEI / ISO 42010, «architecture erosion» Perry & Wolf 1992 (medium); чек-лист точек зрения — по духу Kruchten 4+1 (low); «decisions that override previous patterns» — ADR-сознание, Nygard 2011 (low).
- **Изобретение BMAD:** «developer guardrails» применительно к LLM: архитектурные ограничения упаковываются в story, потому что **dev-агент не будет читать architecture.md сам**.
- **Замысел автора:** create-story — фильтр-компилятор: из общего architecture.md извлекаются только релевантные данной story предписания и встраиваются как обязательные.
- **Настроить под себя:** категории извлечения — правка списка CRITICAL ARCHITECTURE EXTRACTION; вход — `*architecture*.md` в planning-artifacts (поддерживается шардирование в папку).

#### Крупица 6: Step 4 — web research for latest technical specifics
**Файл:** `workflow.md`, строки 271-291

- **Названо в файле:** ничего.
- **Узнаваемо без имени:** *компенсация knowledge cutoff через retrieval*: «ENSURE LATEST TECH KNOWLEDGE — Prevent outdated implementations!» — RAG-философия (Lewis et al. 2020) в прикладном виде, стандарт LLM-dev 2023+ (high); *dependency hygiene*: «Security vulnerabilities or updates» — дух OWASP SCA, инструментов нет (low).
- **Изобретение BMAD:** результаты ресёрча вшиваются прямо в story — **story-файл как кэш свежих знаний** для офлайнового dev-агента.
- **Замысел автора:** закрывает «wrong libraries»: модель училась на устаревших версиях — свежие брейкинги добываются в момент создания story и фиксируются текстом; dev-агенту запрещено гадать.
- **Настроить под себя:** шаг целиком опционален (Step 5 оборачивает выводы в `check if="web research completed"`).

> ❗ **Дрейф:** нет ни инструкции какими инструментами искать (WebSearch/WebFetch не упомянуты), ни fallback'а при отсутствии сети — шаг полностью на усмотрение LLM-рантайма.

#### Крупица 7: Step 5 — сборка story через template-output
**Файл:** `workflow.md`, строки 293-344

- **Названо в файле:** ничего.
- **Узнаваемо без имени:** *Definition of Ready*: «ready-for-dev» только после заполнения всех секций — Scrum-сообщество ~2010-е; имени DoR в файле нет, есть только статус-строка (high); *шаблонная генерация документов* — наследие BMAD v4/v5 create-doc engine (high).
- **Изобретение BMAD:** DSL-тег `<template-output>` — декларативный контракт «какие секции LLM обязан выдать» (слотовая генерация); условные секции (previous_story_intelligence и т.п. появляются только если анализ был) — graceful degradation контента; водяной знак «Ultimate context engine analysis completed» в completion note.
- **Замысел автора:** детерминированная **структура** при недетерминированном содержимом — LLM не может «забыть» секцию, потому что каждая объявлена тегом.
- **Настроить под себя:** набор/порядок секций — теги `<template-output>`; форма документа — `template.md`.

> ❗ **Дрейф (контракт разъехался):** 13 имён template-output (story_header, story_requirements, developer_context_section, technical_requirements, architecture_compliance, library_framework_requirements, file_structure_requirements, testing_requirements, previous_story_intelligence, git_intelligence_summary, latest_tech_information, project_context_reference, story_completion_status) **не соответствуют ни одной** секции template.md (Story, Acceptance Criteria, Tasks/Subtasks, Dev Notes, Project Structure Notes, References, Dev Agent Record). Маппинг целиком на интуиции LLM. Проверено критиком: точно.

#### Крупица 8: Step 6 — checklist-валидация, обновление статуса, handoff
**Файл:** `workflow.md`, строки 346-378

- **Названо в файле:** **Code review** («Run `code-review` when complete (auto-marks done)») — Fagan, IBM 1976.
- **Узнаваемо без имени:** *self-check по чек-листу перед публикацией* — Gawande «The Checklist Manifesto» 2009, авиационные чек-листы (medium); *optimistic concurrency*: «Verify current status is "backlog" (expected previous state)» — compare-and-swap из БД-практики, перенесённый на YAML (medium); *DoR как выходной критерий* (high).
- **Изобретение BMAD:** «Save story document unconditionally» — артефакт сохраняется даже при проблемах валидации (fail-open by design); «preserving ALL comments and structure including STATUS DEFINITIONS» — защита человекочитаемых YAML-комментариев от LLM-перезаписи; Next Steps как явный конвейерный handoff (create-story → dev-story → code-review).
- **Замысел автора:** двойная запись состояния (story-файл + sprint-status.yaml) + рельсы дальше по конвейеру прямо в финальном выводе — пользователю не нужно знать карту фазы.
- **Настроить под себя:** критерии валидации — `checklist.md`.

> ❗ **Дрейф:** финальный вывод использует `{{story_file}}` — переменная нигде не определена (есть `{default_output_file}`); вывод адресуется `{user_name}`, которого нет в config.yaml.
>
> ❗ **Дрейф:** «Run `code-review` when complete (auto-marks done)» — поведенческий контракт **чужого** скилла, зашитый строкой; при изменении bmad-code-review молча устареет. (Критик подтвердил: пока совпадает — step-04 code-review действительно ставит done.)

#### Крупица 9: discover-inputs.md — протокол загрузки документов
**Файл:** `bmad-create-story/discover-inputs.md`

- **Названо в файле:** ничего.
- **Узнаваемо без имени:**
  - *Chunking/шардирование под контекстное окно*: «Try Sharded Documents First» + конкатенация «index.md first, then alphabetical» — RAG-chunking (2023+) + BMAD v4 document sharding, без векторов, чисто файловое (high);
  - *Manual retrieval по индексу (RAG-lite без эмбеддингов)*: INDEX_GUIDED — «Load index.md, analyze... intelligently load relevant docs» — agentic retrieval 2024 (high);
  - *Recall-over-precision*: «DO NOT BE LAZY -- load documents that might have relevant information, even if there is only a 5% chance» + «When in doubt, LOAD IT» — осознанный выбор эпохи больших окон (medium);
  - *Прозрачность загрузки*: «List all loaded content variables with file counts» — context manifest, дух observability (low).
- **Изобретение BMAD:** три именованные стратегии **FULL_LOAD / SELECTIVE_LOAD / INDEX_GUIDED** как декларативный словарь, разделяемый между workflow-таблицей и протоколом; graceful not-found («empty string... this is not an error» + предложение донести файл).
- **Замысел автора:** библиотечная функция загрузки контекста, вынесенная из workflow — стандартизация «как ЛЮБОЙ workflow находит документы», чтобы каждый скилл не изобретал свой glob. «Не ленись» — прямая борьба с чтением по диагонали.
- **Настроить под себя:** стратегия на каждый вход — колонка Load Strategy; шардирование — разложить PRD/architecture в папки с index.md.

> ❗ **Дрейф:** см. крупицу 2 — конфликт SELECTIVE_LOAD (таблица) vs FULL_LOAD (протокол); для prd/ux SELECTIVE_LOAD неисполним по букве (нет template-переменной в паттерне).

#### Крупица 10: template.md — канон story-файла
**Файл:** `bmad-create-story/template.md`

- **Названо в файле:** **Acceptance Criteria** (секция) — agile/ATDD-традиция; **User Story** «As a {{role}}, I want {{action}}, so that {{benefit}}» — XP; шаблон — Connextra 2001.
- **Узнаваемо без имени:** *requirements traceability*: «- [ ] Task 1 (AC: #)» — каждая задача маппится на номер критерия — упрощённая traceability matrix (IEEE 830 школа), medium; *цитирование против галлюцинаций*: «Cite all technical details with source paths and sections, e.g. [Source: docs/<file>.md#Section]» — grounded generation / RAG-citation 2023+ (high); *WBS-декомпозиция* двумя уровнями чекбоксов — PMBOK в форме GitHub task lists (medium).
- **Изобретение BMAD:** секция **Dev Agent Record** (Agent Model Used / Debug Log References / Completion Notes List / File List) — «бортовой самописец» LLM-агента прямо в артефакте, включая версию модели — воспроизводимость; Status первой строкой тела — машиночитаемость без frontmatter; Project Structure Notes с «Detected conflicts or variances (with rationale)».
- **Замысел автора:** шаблон — интерфейсный контракт трёх скиллов: create-story заполняет верх, dev-story пишет низ, code-review читает всё. Один файл = вся правда о story.
- **Настроить под себя:** прямое редактирование шаблона.

> ❗ **Дрейф (три шва шаблон↔workflow):** (1) комментарий «Run validate-create-story for quality check» — скилла с таким именем нет; уточнение критика: `module-help.csv` регистрирует «Validate Story (VS)» как **action=validate скилла bmad-create-story** — ссылка ведёт на menu-код агентного слоя, не полностью битая, но как команда не работает; (2) в шаблоне **нет секции Change Log**, при этом dev-story разрешает её модифицировать и DoD требует «Change Log includes summary of changes»; (3) нет подсекции «Implementation Plan», на которую ссылается dev-story Step 5.

#### Крупица 11: checklist.md — fresh-context «конкурсный» валидатор
**Файл:** `bmad-create-story/checklist.md`

- **Названо в файле:** ничего.
- **Узнаваемо без имени:**
  - *Независимая инспекция (валидатор ≠ автор, чистый контекст)*: «You are an independent quality validator in a **FRESH CONTEXT**» — Fagan inspections 1976 + LLM-as-judge с контекст-изоляцией 2023-2024 (high);
  - *Адверсариальная gamification*: «This is a COMPETITION to create the ULTIMATE story context», «You WIN against the original LLM if...» — промпт-фольклор 2023+ (incentive framing), строгой академической атрибуции нет (medium);
  - *Re-performance audit*: «You will systematically re-do the entire story creation process» — аудиторская техника re-performance; N-version programming по духу (Avizienis 1985) (medium);
  - *Severity-триаж находок*: Critical Misses / Enhancement / Optimization — IEEE 1044 + MoSCoW-подобная градация (medium);
  - *Human-in-the-loop выбор правок*: «all / critical / select / none / details» (high).
- **Изобретение BMAD:** «prompt-as-checklist» — файл называется checklist, но это полноценный промпт-агент со своими шагами 1-8 и dual-mode (из workflow или в свежем чате); **LLM-Dev-Agent Optimization Analysis** — токен-экономия как критерий качества документа («Pack maximum information into minimum text») — артефакт оптимизируется под машинного читателя; правило бесшовного вживления правок («make them look natural, as if they were always there»).
- **Замысел автора:** LLM плохо проверяет свою работу в том же контексте (self-consistency bias) → валидатор инструктирован как соперник с чистой памятью, который заново проходит весь процесс.
- **Настроить под себя:** категории дефектов и метрики «победы» — разделы Disaster Prevention Gap Analysis / Success Metrics.

> ❗ **Дрейфы:** (1) двойная нумерация — два разных «Step 5» (Improvement Recommendations и Present Improvement Suggestions), сквозная нумерация сломана; (2) методологическое противоречие — Step 7 требует «DO NOT reference the review process» (стирание следов ревью) vs культура audit trail в Dev Agent Record: история качества story теряется; (3) интерактивный выбор правок противоречит «ZERO USER INTERVENTION» workflow — поведение при вызове из Step 6 не определено.

---

### 2.2 bmad-dev-story — исполнение story

#### Крупица 1: активация
**Файл:** `bmad-dev-story/SKILL.md` — thin-shim, 6 строк (high, Anthropic skill manifest). **Изобретение BMAD:** вход декларирован как «context filled story spec file» — зависимость от выхода create-story зафиксирована прямо в манифесте. Скилл не работает с «голой» задачей.

#### Крупица 2: преамбула + блок `<critical>` (контракт исполнения)
**Файл:** `workflow.md`, строки 1-51

- **Названо в файле:** ничего.
- **Узнаваемо без имени:** *least privilege на запись*: «Only modify the story file in these areas: Tasks/Subtasks checkboxes, Dev Agent Record, File List, Change Log, and Status» — Saltzer & Schroeder 1975, перенесённый с системных ресурсов на секции markdown (medium); *separation of duties*: запрет править Story/AC/Dev Notes — baseline/change control (IEEE 828) (medium).
- **Изобретение BMAD:**
  - **анти-стоп контракт**: «Absolutely DO NOT stop because of "milestones", "significant progress", or "session boundaries". Continue in a single execution until the story is COMPLETE... UNLESS a HALT condition is triggered» — прямое подавление LLM-привычки досрочно завершать с отчётом о прогрессе;
  - разделение каналов: user_skill_level «affects conversation style ONLY, not code updates» — тон не должен деградировать код;
  - блок `<critical>` продублирован дважды (проза + теги) — повторение как механизм удержания внимания модели.
- **Замысел автора:** dev-агент — долгоиграющий процесс с runtime-контрактом: завершение решает машина состояний (HALT + completion gates), а не «ощущение прогресса». Права на запись урезаны, чтобы агент **не переписал себе приёмочные критерии под фактический результат**.
- **Настроить под себя:** перечень разрешённых секций — critical-инструкция; язык/уровень — config.yaml; кодстандарты — project-context.md.

> ❗ **Дрейф:** «Only Step 6 decides completion» — но completion-последовательность это Steps 9-10, а Step 6 — «Author comprehensive tests». Stale-ссылка после перенумерации (видимо, раньше шагов было 6). Плюс тот же конфиг-дрейф user_name/document_output_language.

#### Крупица 3: Steps 1-2 — поиск ready-story и загрузка контекста
**Файл:** `workflow.md`, строки 53-186

- **Названо в файле:** ничего.
- **Узнаваемо без имени:** *pull WIP=1* — «Find the FIRST story... ready-for-dev» — зеркало диспетчера create-story (medium); *DoR как входной гейт* (high); *graceful degradation*: ветка «sprint_status does NOT exist» → прямой поиск story-файлов `*-*-*.md` и чтение Status из них (medium).
- **Изобретение BMAD:** `<anchor id="task_check"/>` + `<goto anchor=...>` — метки переходов в псевдо-DSL: **управляющий граф из markdown, исполняемый LLM**; двухканальное чтение состояния (sprint-status.yaml ИЛИ Status-строка story-файлов) — резервирование источника истины; HALT-меню с 4 опциями на каждое тупиковое состояние.
- **Замысел автора:** идемпотентный вход — с явным путём, со sprint-трекингом или без него агент детерминированно находит ровно одну story; Dev Notes заменяют самостоятельное чтение архитектуры.
- **Настроить под себя:** `{{story_path}}` аргументом; очередность — порядок ключей sprint-status.yaml.

> ❗ **Дрейфы:** (1) условие «no ready-for-dev **or in-progress** story found» шире критерия поиска — ищется только ready-for-dev → **авто-resume прерванной story через discovery невозможен** (только явным путём), хотя Step 4 умеет обрабатывать in-progress; (2) меню предлагает «Run `*validate-create-story`» — звёздочный синтаксис агентов v4/v5, чужеродный для v6 skills, и скилла с таким именем нет (см. уточнение про module-help.csv выше); (3) Step 2 дословно дублирует действия Step 1; (4) `{{sprint_status_summary}}` ниоткуда не вычисляется.

#### Крупица 4: Steps 3-4 — детект «возврата после ревью» + пометка in-progress
**Файл:** `workflow.md`, строки 188-261

- **Названо в файле:** ничего (имена Approve/Changes Requested/Blocked — GitHub-модель, названы как значения, не как методология).
- **Узнаваемо без имени:** *rework-цикл код-ревью*: «Extract from "Senior Developer Review (AI)" section: Review outcome... Severity breakdown» + приоритет задач `[AI-Review]` — Fagan rework-фаза + PR-циклы (high); *severity-триаж High/Med/Low* — IEEE 1044 (high); *optimistic state verification* при переходе — но **fail-open**: неожиданный статус → «Continuing anyway...» с warning (medium).
- **Изобретение BMAD:** **резюмируемость по артефакту** — режим (fresh vs continuation) выводится из СОДЕРЖИМОГО story-файла (наличие секции ревью), а не из внешнего state: story-файл одновременно спецификация, журнал и снапшот состояния конвейера; маркер `[AI-Review]` — машиночитаемая связь finding ↔ task.
- **Замысел автора:** замкнуть петлю dev → review → dev без нового скилла; состояние хранится там, где его прочитает любой следующий процесс — в самом документе.
- **Настроить под себя:** имя секции-маркера — «константа, менять синхронно в обоих скиллах».

> ❗❗ **ДРЕЙФ P0 (главная находка проверки, раскопки его пропустили): петля dev→review→dev РАЗОРВАНА.** dev-story Step 3 ждёт секцию **«Senior Developer Review (AI)»**, подсекцию «Review Follow-ups (AI)» и маркер `[AI-Review]` (workflow.md:191-216, 321-326). А bmad-code-review (step-04-present.md:21-30) пишет **«### Review Findings»** с маркерами `[Review][Decision]/[Review][Patch]/[Review][Defer]`. Имена секций и тегов **не пересекаются вообще** → детект «продолжение после ревью» никогда не сработает на выходе этого code-review. Совет раскопок «менять синхронно в обоих» фактически неверен: **синхронизировать уже нечего, контракта нет** — это след старой версии review-workflow. Бонус-несовместимость: dev-story ждёт severity High/Med/Low, а триаж code-review даёт корзины decision/patch/defer/dismiss. Текстовый контракт между двумя промптами сгнил молча — некому было его проверить.
>
> ❗ **Дрейф:** fail-open «Continuing anyway...» — единственный переход состояния без HALT, противоречит жёсткости остальных гейтов. Секции «Senior Developer Review (AI)» нет и в template.md — контракт жил только между двумя workflow (и умер).

#### Крупица 5: Step 5 — red-green-refactor (ядро)
**Файл:** `workflow.md`, строки 263-292

- **Названо в файле:** **Red-Green-Refactor** — прямо в goal шага: «<!-- RED PHASE --> Write FAILING tests first... <!-- GREEN PHASE --> Implement MINIMAL code... <!-- REFACTOR PHASE --> Improve code structure while keeping tests green». Это мантра TDD Кента Бека («Test-Driven Development: By Example», 2002; корни в XP 1999). **Важно: слов «TDD»/«test-driven» в файле нет** — назван только сам цикл.
- **Узнаваемо без имени:**
  - *Оценка каноничности TDD (раскопщик, high):* канонично — (1) «Confirm tests fail before implementation - this validates test correctness» (бековское «убедись, что тест падает по правильной причине»), (2) «MINIMAL code to make tests pass» («simplest thing that could possibly work»), (3) рефакторинг строго при зелёных. НЕканонично — цикл крутится на уровне task/subtask (крупная партия), а не «один микротест за раз»; нет правила «не пиши второй тест, пока первый красный»; Step 6 дописывает тесты ПОСЛЕ (test-after хвост);
  - *YAGNI*: «NEVER implement anything not mapped to a specific task/subtask» — XP ~1999 (high);
  - *Circuit breaker*: «3 consecutive implementation failures → HALT and request guidance» — Nygard «Release It!» 2007, адаптирован как анти-зацикливание LLM (medium);
  - *Dependency control gate*: «new dependencies → HALT: "Additional dependencies need user approval"» — supply-chain governance (medium);
  - *журналирование решений* — ADR-дух в облегчённой форме (low).
- **Изобретение BMAD:** story объявлена «authoritative implementation guide» с запретом отклонений — спецификация важнее суждения исполнителя; три **перечислимых** HALT-условия как exit-контракт шага (deps / 3 фейла / нет конфига); анти-пауза «Do NOT propose to pause for review until Step 9 completion gates are satisfied».
- **Замысел автора (ключевой инсайт фазы):** red-green-refactor взят **не ради дизайн-преимуществ TDD, а как верификационная упряжь для LLM**: красный тест до кода — единственное доказательство, что тест вообще что-то проверяет (анти-«lying about completion»); минимальный код — анти-overengineering; рефакторинг под зелёными — безопасная уборка. Грануляция по подзадаче — осознанный компромисс между строгостью и стоимостью токенов.
- **Настроить под себя:** порог циркит-брейкера («3 consecutive»); сами задачи цикла — Tasks/Subtasks в story.

> ❗ **Дрейфы:** «Implementation Plan» в Dev Agent Record — подсекции нет в template.md; «until Step 9» здесь vs «Only Step 6 decides completion» в преамбуле — два разных номера «решающего» шага в одном файле.

#### Крупица 6: Steps 6-7 — comprehensive tests + валидации
**Файл:** `workflow.md`, строки 294-309

- **Названо в файле:** **Unit / Integration / End-to-End tests** — классическая таксономия уровней; пропорция — Test Pyramid (Cohn 2009), но **слово «pyramid» в файле отсутствует**; **Regression testing** — классика SE с 1970-х; **Linting** — S.C. Johnson, Bell Labs, 1978.
- **Узнаваемо без имени:**
  - *CI по духу*: полный прогон всей сюиты после каждой партии + «regression tests fail → STOP and fix before continuing» — Fowler/Beck «почини сборку немедленно». **Чего нет (раскопщик, high):** ни CI-сервера, ни единой git-команды во всём dev-story — интеграция отложена внешнему конвейеру. CI здесь — **дисциплина, не инфраструктура**;
  - *ATDD-замыкание*: «Validate implementation meets ALL story acceptance criteria» — но это пост-хок валидация LLM'ом, а не исполняемые приёмочные тесты до кода; ATDD по намерению, не по механике (medium);
  - *test framework inference*: «infer test framework from project structure» — convention over configuration (Rails/DHH 2004) силами агента (low).
- **Изобретение BMAD:** двухпроходное тестирование — тесты red-фазы (Step 5) + «comprehensive» добор (Step 6: unit/integration/e2e) — осознанный гибрид test-first ядра и test-after покрытия; два STOP-гейта с разной семантикой (regression fail = «breaking changes» vs new-test fail = «implementation correctness»).
- **Замысел автора:** red-фаза проверяет только функциональность подзадачи — автор не доверяет ей покрытие и добавляет второй проход; прогон ВСЕЙ сюиты на каждую задачу — перенос CI-дисциплины внутрь одной LLM-сессии.
- **Настроить под себя:** обязательность уровней тестов управляется содержимым story (testing_requirements из create-story); линтеры — конфиги проекта.

> ❗ **Дрейфы:** (1) граница цикла не объявлена — Step 8 делает goto step 5, значит Steps 6-7-8 исполняются на КАЖДОЙ итерации, но Step 6 написан как одноразовый финальный добор; цикл «5→8» существует только неявно; (2) три противоречивых указателя на «решающий» шаг (Step 6 / Step 9 / completion sequence).

#### Крупица 7: Step 8 — validation gates и пометка задачи complete
**Файл:** `workflow.md`, строки 311-360

- **Названо в файле:** ничего.
- **Узнаваемо без имени:** *анти-fabrication верификация*: «Verify ALL tests for this task/subtask ACTUALLY EXIST and PASS 100%» + «NEVER mark a task complete unless ALL conditions are met - NO LYING OR CHEATING» — QA sign-off + LLM-специфика защиты от reward hacking 2023+ (high); *scope conformance*: «matches EXACTLY what the task specifies - no extra features» (high); *двусторонняя синхронизация трекеров*: чекбокс в Review Follow-ups + «matching description» в секции ревью (medium); *changelog-запись о rework* — Keep a Changelog (Lacan 2014) по духу (low).
- **Изобретение BMAD:** **чекбокс `[x]` как защищённая валюта** — право его поставить выдаётся только после 4 валидационных ворот (tests exist+pass / exact match / AC satisfied / no regressions); чекбокс из UI-украшения превращён в гейт-артефакт; явный запрет «прогресса ради прогресса» (validation fails → DO NOT mark complete, fix first, HALT if unable); инкрементальное сохранение story после каждой задачи — checkpoint на случай обрыва сессии.
- **Замысел автора:** сердце анти-«lying about completion» — разделить **ФАКТ** выполнения (тесты реально существуют и проходят) и **ОТМЕТКУ** выполнения (чекбокс), и разрешить второе только после первого. Матчинг finding'ов «by matching description» — слабое место, оставленное на семантику LLM (автор это, видимо, понимал).
- **Настроить под себя:** состав ворот — список VALIDATION GATES.

> ❗ **Дрейфы:** маршрутизация хвоста «no tasks remain → goto step 9» конфликтует со Step 1, шлющим в Step 6 — два разных входа в завершение; матчинг review-item без ID — при перефразировке finding'а связь рвётся молча.

#### Крупица 8: Step 9 — completion + Definition of Done
**Файл:** `workflow.md`, строки 362-411

- **Названо в файле:** **Definition of Done** — «Execute enhanced definition-of-done validation» — Scrum Guide, обязательство качества инкремента.
- **Узнаваемо без имени:** *полный регрессионный прогон как release-гейт*: «Run the full regression suite (do not skip)» + HALT — Continuous Delivery (Humble & Farley 2010) (high); *re-verification перед сдачей*: «re-scan the story document now» — read-back verification, чеклист-культура авиации/медицины (medium); *статус «review» как handoff* — Kanban-колонка In Review / PR-workflow (high).
- **Изобретение BMAD:** инлайн-дубликат DoD — 11 пунктов зашиты прямо в шаг (плюс внешний checklist.md) — двойное закрепление контракта; **четыре финальных HALT'а** (incomplete tasks / regression failures / File List incomplete / DoD fail) — exit-контракт перечислим; деградация с предупреждением: рассинхрон sprint-status озвучивается («may be out of sync»), а не глотается.
- **Замысел автора:** DoD — последний шлюз между «агент считает, что готово» и «человек получает на проверку»; статус review недостижим без зелёного прогона и полноты File List (нужного дальше code-review для diff-границ).
- **Настроить под себя:** пункты DoD — **синхронно** в инлайн-списке Step 9 и checklist.md.

> ❗ **Дрейфы:** (1) DoD требует «Change Log includes summary of changes» — секции Change Log в template.md нет: пункт выполним, только если агент самовольно создаст секцию; (2) два источника DoD уже разъехались — checklist.md добавляет «Dependencies Within Scope» и «Previous Story Learnings», которых нет в инлайн-списке.

#### Крупица 9: Step 10 — коммуникация завершения и handoff
**Файл:** `workflow.md`, строки 413-448

- **Названо в файле:** **Code review / peer review** («Run `code-review` workflow for peer review») — Fagan 1976 / PR-процесс.
- **Узнаваемо без имени:** *диверсификация ревьюера*: «💡 Tip: For best results, run `code-review` using a **different** LLM than the one that implemented this story» — независимость инспектора (Fagan) + N-version diversity (Avizienis 1985) + cross-model review 2023-2024: другая модель не разделяет слепых пятен автора (high); *адаптивная передача знаний по уровню слушателя* (low, generic).
- **Изобретение BMAD:** Q&A-сессия после сдачи как штатный шаг — единственная точка, где человеку штатно возвращают контроль («Remain flexible - allow user to choose their own path»); прокладка конвейера в выводе (code-review + опциональный /bmad:tea:automate + sprint-status).
- **Замысел автора:** завершение — не «готово, пока», а **передача смены**; рекомендация сменить модель для ревью — автор знает, что та же модель в том же контексте склонна одобрять собственный код.
- **Настроить под себя:** тон объяснений — user_skill_level; расширение тестов — TEA-модуль (в этой инсталляции не установлен).

> ❗ **Дрейф:** «/bmad:tea:automate» условно-мёртв — TEA-модуля в инсталляции нет; обёрнуто в «If Test Architect module installed», но КАК проверить установку — LLM не инструктирован.

#### Крупица 10: checklist.md — Enhanced Definition of Done
**Файл:** `bmad-dev-story/checklist.md`

- **Названо в файле:** **Definition of Done** (title + заголовок); **Unit / Integration / End-to-End Tests** (отдельные чекбоксы); **Test Coverage** («cover acceptance criteria and edge cases» — качественное, не процентное); **Regression Prevention**.
- **Узнаваемо без имени:** *checklist manifest с frontmatter-контрактом*: validation-target, validation-criticality: 'HIGHEST', required-inputs, validation-rules — schema/manifest в стиле CI-конфигов (medium); *текстовый exit-контракт*: «Definition of Done: {{PASS/FAIL}}» + «Completion Score: {{completed_items}}/{{total_items}}» — quality gate в стиле SonarQube, **но вердикт пишет сам проверяемый LLM** (medium); *DoD из 5 групп, 25 пунктов* (Context / Implementation / Testing / Documentation / Final Status) — типично для зрелых agile-команд (high).
- **Изобретение BMAD (рефлексивные пункты — фреймворк проверяет сам себя):** «Story Structure Compliance: Only permitted sections of story file were modified» — DoD проверяет соблюдение write-привилегий агента; «No HALT Conditions» — HALT-механика включена в критерии готовности; «Dependencies Within Scope» — дублирует HALT-гейт Step 5 как чек-пункт.
- **Замысел автора:** все runtime-запреты workflow переведены в проверяемые ретроспективно пункты — нарушение, проскочившее в процессе, ловится хотя бы на выходе. **Слабость осознанная:** проверяет тот же агент, что и делал; независимость отдана следующему звену (code-review другой моделью).
- **Настроить под себя:** добавить командные пункты (security-скан, перфоманс-бюджет); ужесточить «when story requirements demand them» до безусловных.

> ❗ **Дрейфы:** (1) циркулярность — frontmatter required-inputs требует «all items marked [x]» как ВХОД, а сам чеклист содержит «All Tasks Complete» как ПРОВЕРКУ; (2) «Change Log» и «Review Follow-ups» — обе секции отсутствуют в template.md; (3) расхождение с инлайн-DoD Step 9 (4 лишних пункта).

---

### 2.3 bmad-code-review — адверсариальное ревью (упущено раскопками, восстановлено проверкой)

Третий скилл ядра — без него «create → dev → review» неполон. Архитектурно **другой**, чем монолиты create/dev-story.

#### Крупица 1: SKILL.md
- **Названо в файле:** **Adversarial review** («Review code changes adversarially using parallel review layers (Blind Hunter, Edge Case Hunter, Acceptance Auditor)») — red-team традиция, в LLM-эру — anti-sycophancy приём; **Triage** — bug triage из OSS-трекеров ~2000-х, метафора военно-полевой медицины.
- **Узнаваемо без имени:** progressive disclosure / тонкий entry-point (high, Anthropic 2025).
- **Изобретение BMAD:** трёхслойный состав ревьюеров с говорящими именами вынесен прямо в description — триггерная фраза одновременно документирует архитектуру.

#### Крупица 2: workflow.md — step-file architecture
- **Названо в файле:** **step-file architecture** — «This uses step-file architecture for disciplined execution». Собственный термин BMAD, не индустриальный; идейно близок к runbook-инжинирингу SRE.
- **Узнаваемо без имени:** *runbook с принудительной последовательностью*: «FOLLOW SEQUENCE... NEVER skip steps or optimize the sequence» — SRE runbooks (Google SRE book 2016) + чеклист-дисциплина авиации (medium); *context window management через ленивую загрузку*: «Just-In-Time Loading: Only load the current step file», «NEVER load multiple step files simultaneously» — context engineering против instruction dilution (high); локализация через конфиг (medium).
- **Изобретение BMAD:** **step-file architecture целиком**: workflow.md (55 строк) = диспетчер + 4 микрофайла в `steps/`; JIT-загрузка + sequential enforcement + state в runtime-переменных + append-only артефакты; «ALWAYS halt at checkpoints and wait for human input» как НЕотключаемое правило (Critical Rules NO EXCEPTIONS).
- **Замысел автора:** не доверять LLM длинный многошаговый процесс в одном промпте — дробить на микрофайлы и запрещать «оптимизацию» последовательности (LLM любит срезать углы).

> ❗ **Дрейф:** загружаются `user_skill_level` и `document_output_language`, но ни один step-файл их не использует — мёртвые переменные (копипаста шаблона инициализации).

#### Крупица 3: step-01-gather-context.md
- **Узнаваемо без имени:** *intent detection по таблице фраз* («staged» → Staged changes only и т.д., правило «prefer the most specific match») — rule-based intent routing (medium); *ограничение размера ревью*: порог ~3000 строк diff с предложением чанкинга — наследник «не более ~400 LOC за сессию» (SmartBear/Cisco study 2006, Google small CLs), ослаблен под LLM-контекст (high); *fail-fast валидация входов* (существование base branch, парсимость diff — каждая с HALT) (high); *pull из статуса*: авто-подхват story в статусе `review` из sprint-status (medium); *read-only фаза*: «Do not modify any files. This step is read-only» — CQS Мейера на фазах агентного процесса (low).
- **Изобретение BMAD:** runtime-переменные в frontmatter step-файла (diff_output, spec_file, review_mode, story_key) как state-контракт между шагами; правило «The prompt that triggered this workflow IS the intent — not a hint» — запрет переинтерпретировать запрос; двухрежимность `review_mode = full | no-spec`; CHECKPOINT с обязательным HALT до запуска ревью.
- **Замысел автора:** детерминированно зафиксировать ЧТО ревьюим до того, как LLM начнёт ревьюить — закрывает анти-паттерн «LLM угадал область ревью и проревьюил не то».
- **Настроить под себя:** таблица intent-фраз; порог 3000 строк; glob sprint-трекинга.

#### Крупица 4: step-02-review.md — три охотника
- **Названо в файле:** **Acceptance criteria** (проверка соответствия) — Scrum/XP; верификация против них — ATDD (Hendrickson 2008) / Specification by Example (Adzic 2011).
- **Узнаваемо без имени:**
  - *Perspective-Based Reading*: три ревьюера с разными перспективами И **разными входами** — Basili et al., NASA SEL, 1996 (high);
  - *Blind review / контекстная изоляция против anchoring*: «Launch parallel subagents without conversation context»; Blind Hunter — «No spec, no context docs, no project access» — double-blind + context quarantine: свежий контекст не «заражён» авторским нарративом (high);
  - *N-version redundancy*: «run each in a separate session (ideally a different LLM)» (medium);
  - *graceful degradation*: отказ слоя → имя в `{failed_layers}`, продолжить на оставшихся (high).
- **Изобретение BMAD:** **информационная асимметрия по дизайну** — Blind Hunter получает только diff; Edge Case Hunter — diff + чтение проекта; Acceptance Auditor — diff + spec + context docs. Градиент контекста = механизм ортогональности находок. Fallback-протокол без субагентов: сгенерировать prompt-файлы, HALT, человек запускает их в отдельных сессиях и вставляет находки обратно — ручной fan-out. Композиция через core-скиллы: Blind Hunter = `bmad-review-adversarial-general`, Edge = `bmad-review-edge-case-hunter` — **ревью-движок живёт в `_bmad/core/`, ВНЕ фазы 4**.
- **Замысел автора:** решает главную проблему LLM-ревью — модель хвалит код, если видела контекст его написания. Blind Hunter без контекста не может оправдать код намерением; Auditor наоборот видит спеку, чтобы ловить расхождения с ней.
- **Настроить под себя:** добавить слой (напр. Security Hunter) — пункт в instruction 2 + формат в step-03; подменить скиллы охотников на свои.

> ❗ **Дрейфы:** (1) «resume from this point and proceed to step 3» — двусмысленно: instruction 3 этого файла или файл step-03-triage.md; (2) изоляция Blind Hunter декларирована промптом, но **ничем не enforce'ится механически** — субагент с tool harness физически может читать проект; (3) формат failed_layers («comma-separated») описан в двух местах.

#### Крупица 5: step-03-triage.md
- **Названо в файле:** **Triage** (заголовок); **False positive** («dismiss -- Noise, false positive, or handled elsewhere») — терминология SAST-триажа (Coverity/Fortify, 2000-е).
- **Узнаваемо без имени:**
  - *классификация по actionability вместо severity*: ровно 4 корзины `decision_needed / patch / defer / dismiss` — определены через «что с этим делать», а не «насколько страшно» — родня Google-разметке комментариев (Nit/FYI/blocking) + GTD «next action» (medium);
  - *дедупликация с сохранением наиболее специфичной находки* («prefer edge-case JSON with location over adversarial prose») — crash bucketing (Microsoft Watson ~2005) (medium);
  - *консервативная классификация при неопределённости* — fail-safe defaults, Saltzer & Schroeder (medium);
  - *защита от ложно-зелёного*: «If zero findings remain AND {failed_layers} is non-empty → warn that the review may be incomplete rather than announcing a clean review» — различение skipped vs passed (high);
  - *«не наша регрессия» vs «наш дефект»*: defer = pre-existing, не actionable сейчас — Google eng-practices «не требуй чинить то, что CL не трогал» (high).
- **Изобретение BMAD:** жёсткий нормализационный контракт входов (Blind = markdown list, Edge = JSON c location/trigger_condition/guard_snippet, Auditor = markdown с AC-ссылкой) + best-effort parsing; правило даунгрейда — в no-spec режиме корзина decision_needed **запрещена** (без спеки не у кого спрашивать о намерении); чистый вердикт «✅ Clean review» разрешён только при пустом failed_layers.
- **Замысел автора:** заменить субъективную severity-шкалу (где модели плавают) на более проверяемый вопрос «однозначен ли фикс» — и тем самым промаршрутизировать находки между человеком и LLM.

> ❗ **Дрейфы:** instruction 6 — самоссылка, написанная с точки зрения внешнего шага («Step 3 already warned...» внутри самого step 3 — текст перенесён из step-04); severity в триаже НЕТ вообще, но step-04 на неё ссылается — см. следующую крупицу.

#### Крупица 6: step-04-present.md
- **Названо в файле:** **Sprint** («Update story status and sync sprint tracking») — Scrum.
- **Узнаваемо без имени:** *technical debt register*: defer-находки в `{deferred_work_file}` под «## Deferred from: code review ({date})» с обязательной однострочной причиной — Cunningham 1992 + debt register из ITIL (high); *DoD через статусный гейт*: все decision/patch решены → done, иначе in-progress — Modern Code Review (Bacchelli & Bird 2013) (high); *rework + follow-up*: «Fix them automatically» → патчи → чекбоксы → «Re-run code review» — фазы Fagan-инспекции (medium); *decision-first*: «decision-needed must be resolved before patch findings» (low); *audit trail*: «always write findings to the story file BEFORE offering action choices» — IEEE 1028 record-keeping (medium).
- **Изобретение BMAD:** формат чекбокс-находок с тегами происхождения — `- [ ] [Review][Decision] ...`, `- [ ] [Review][Patch] ... [<file>:<line>]`, `- [x] [Review][Defer] ... — deferred, pre-existing`; жёсткий HALT-протокол выбора: «Reply with only the number... Do not proceed until you select an option» — анти-паттерн «LLM сам выбрал опцию и поехал»; опция 0 Batch-apply появляется только при >3 патчах и пропускает «any finding that requires judgment» — **бюджетирование внимания человека**; синк sprint-status с warning при рассинхроне; clean-review shortcut при нуле находок.
- **Замысел автора:** разделить исходы на «LLM может сам» (patch) и «обязателен человек» (decision); запись в story ДО действий — страховка от потери находок при обрыве сессии; defer-журнал с причинами — чтобы долг не испарялся между ревью.
- **Настроить под себя:** путь deferred_work_file (frontmatter); маппинг исходов на статусы (секция 6); формат тегов; меню next steps.

> ❗ **Дрейфы (включая баг):** (1) **БАГ:** секция 6 определяет done условием «no unresolved HIGH/MEDIUM issues remain» — шкалы HIGH/MEDIUM в этом workflow не существует (триаж даёт decision/patch/defer/dismiss). Рудимент прежней severity-версии скилла; **условие невычислимо** по данным триажа; (2) противоречие нумерации опций в секции 5 (проза «omit option 2» vs блок, где walk-through показан как опция 2); (3) RULES «When {spec_file} is set, always write findings» vs секция 2 «If spec_file exists AND contains Tasks/Subtasks» — поведение при спеке без Tasks/Subtasks не определено; (4) underscore vs дефис в имени категории (decision_needed / decision-needed) между step-03 и step-04; (5) дублирующий HALT-абзац в секции 5.
>
> ❗❗ И главный: **выход step-04 не читается dev-story** — см. врезку P0 в §2.2. Имена «### Review Findings»/`[Review][...]` против ожидаемых «Senior Developer Review (AI)»/`[AI-Review]`.

---

### 2.4 bmad-qa-generate-e2e-tests — лёгкий QA

#### Крупица 1: SKILL.md
Thin-shim. **Изобретение BMAD:** триггер сужен до пост-имплементационной генерации — «Generate end to end automated tests for **existing** features» — тесты ПОСЛЕ кода, явное отмежевание от TDD.

#### Крупица 2: Initialization + Step 0 (Detect Test Framework)
- **Узнаваемо без имени:** *convention over configuration*: «Use whatever test framework the project already has» — Rails/DHH 2004 + правило consistency из Google review guidance (high); *tool detection через манифест*: «Look for package.json dependencies (playwright, jest, vitest, cypress...)» (medium); *свежий ресёрч вместо запечённого знания*: при пустом проекте — «Search online for current recommended test framework», а не из памяти модели (medium).
- **Изобретение BMAD:** анти-scope-creep контракт между скиллами прямо в роли: «You generate tests ONLY — no code review or story validation (use the bmad-code-review skill for that)».

> ❗ **Дрейф:** `source_dir = {project-root}` объявлен в Paths, но ни один шаг его не использует — мёртвая переменная.

#### Крупица 3: Steps 1-2 (Identify Features + API Tests)
- **Названо в файле:** **Happy path** («Cover happy path + 1-2 error cases») — SE-фольклор из use-case моделирования 1990-х.
- **Узнаваемо без имени:** *API contract testing по статус-кодам* (200/400/404/500 + структура ответа) — Postman/REST-assured школа, Pact-идея без брокера (medium); *negative testing в минимальной дозе* — Myers 1979 error guessing, сознательно усечённый (medium); *characterization tests* — тесты после кода фиксируют текущее поведение (Feathers 2004), противоположность TDD (medium).
- **Изобретение BMAD:** трёхвариантный ввод области (конкретная фича / директория / auto-discover).
- **Замысел автора:** минимально достаточное покрытие быстро; полный test design сознательно не делается — «для этого продаётся TEA-модуль».

#### Крупица 4: Step 3 (E2E) + Keep It Simple
- **Названо в файле:** **Semantic locators** («Use semantic locators (roles, labels, text)») — Playwright best practices + Testing Library principle (Kent C. Dodds 2018); **Risk-based test strategy / Quality gates / NFR assessment** — названы только как функции ВНЕШНЕГО модуля TEA (Bach/Amland 1999; ISO 25010 школа) с upsell-URL (workflow.md:130).
- По данным критика: вывод — `{implementation_artifacts}/tests/test-summary.md`. Дальше этой точки JSON раскопок обрывается — полного разбора Step 3 нет, фиксирую честно.

---

### 2.5 Остальные компоненты (только верхнеуровневые факты критика; глубоких раскопок нет)

**bmad-sprint-planning** — генератор `sprint-status.yaml` из epics: правила конверсии `### Story 1.1: User Authentication` → ключ `1-1-user-authentication` (workflow.md:77-83); **preservation rule «never downgrade status»** (строки 115-117) — повторный прогон не откатит ушедшую вперёд story; задокументирована полная статусная машина (Epic: backlog→in-progress→done; Story: backlog→ready-for-dev→in-progress→review→done; Retro: optional↔done); метаданные дублируются «как комментарии И как YAML-поля» (строка 180 — заведомый двойной источник). В `sprint-status-template.yaml` — workflow-нота «Dev runs code-review (fresh context, ideally different LLM)»: рекомендация cross-model ревью продублирована и здесь.

**bmad-sprint-status** — навигатор: 3 режима (`interactive/data/validate`; Step 0 с Jump to Step 20/30 — **машинный API для других скиллов**, не для человека); легаси-маппинг `drafted→ready-for-dev`, `contexted→in-progress` (строки 75-77); риск-детект (stale >7 дней, orphaned story, эпик без stories) + роутинг к следующему скиллу.
> ❗ **Дрейф:** рекомендует команды старого синтаксиса `/bmad:bmm:workflows:sprint-planning` (строка 62) — namespace v6-команд, не skill-имена.

**bmad-correct-course** — управление изменениями: импакт-анализ по PRD/epics/architecture/UX → `sprint-change-proposal-{date}.md`; PRD и epics обязательны (HALT без них); режимы Incremental/Batch. Это **петля выхода фазы 4 обратно в фазы 2-3** («update PRD, redo architecture») — единственное легальное место, где имплементация может потребовать пересмотра планирования.

**bmad-retrospective** — крупнейший workflow фазы (1479 строк, 13 шагов): **party-mode мультиагентный круглый стол** (формат «Name (Role): dialogue»), реестр агентов из `_bmad/_config/agent-manifest.csv`.
> ❗ **Дрейф:** step 0.5 расположен ПОСЛЕ step 1 (строки 66 vs 195) — тот же класс нумерационного дрейфа, что и «GOTO step 2a».

**bmad-quick-dev** — альтернативный конвейер Quick Flow, минуя весь story-цикл: step-file архитектура, intent → spec → implement → review → present; спека «900-1600 токенов» с **явным обоснованием в файле**: «above 1600 risks context-rot in implementation agents» (workflow.md:29) — редкий случай, когда автор объясняет числовой лимит; early-exit маршрут `step-oneshot.md`; `spec-wip.md` как WIP-state на диске.

**4 agent-персоны** — двойная модель вызова фазы: напрямую скиллы ИЛИ через персон. Amelia/dev (DS+CR), Bob/sm (SP+CS+ER+CC), Quinn/qa (QA), Barry/quick-flow (QD+CR). Каждая: persona-frame «must not break character», capability-таблица menu-кодов, конфиг через `bmad-init` из `_bmad/core/`, плюс `bmad-skill-manifest.yaml` — машиночитаемый реестр (type/displayName/icon/principles).

---

## 3. Карта детерминизма фазы

**Главный факт:** исполнитель ВСЕГО компонента — LLM, интерпретирующий markdown + псевдо-XML DSL (`<step>/<action>/<check>/<goto>/<anchor>/<critical>/<template-output>`). **Ни одного скрипта, ни одного хука, ни одного машинно-проверяемого exit-кода на уровне фреймворка.** Все «гейты» текстовые и исполняются добровольной дисциплиной модели. Единственная квази-машинная истина — прогон тестов/линтеров даёт реальные exit-коды, но их запуск, интерпретация и **честность отчёта** — на LLM.

| Решение | Кто решает | Чем подкреплено |
|---|---|---|
| Какая story следующая | LLM, парсящий порядок ключей sprint-status.yaml | квази-детерминизм: позиция в файле, не «суждение»; но читает и парсит LLM |
| Старт разработки разрешён? (DoR) | LLM (статус ready-for-dev) | текстовый state machine + CAS-проверка ожидаемого статуса |
| Нелегальное состояние (done-эпик, неизвестный статус) | LLM-HALT + меню для человека | текст |
| Что грузить в контекст (discover-inputs) | LLM по декларативным стратегиям | текст («не ленись», recall-over-precision) |
| Тест падает до имплементации? (red) | **exit-код реальный**, интерпретация LLM | единственная машинная истина фазы |
| Регрессии после партии | exit-код реальный, STOP-fix — LLM | то же |
| Ставить чекбокс `[x]` | LLM-самоотчёт через 4 validation gates | текст: «ACTUALLY EXIST and PASS 100%», «NO LYING OR CHEATING» |
| Новая зависимость | **человек-гейт** (HALT) | текст, но эскалация реальная |
| 3 фейла подряд | LLM-циркит-брейкер → HALT к человеку | текст |
| DoD перед статусом review | LLM-самопроверка, вердикт «PASS/FAIL» пишет проверяемый | текст, без enforcement |
| Источник diff для ревью | LLM intent-детект по таблице фраз + fail-fast валидации | текст + реальные git-команды |
| Запуск ревью после сводки diff | **человек-гейт** (CHECKPOINT HALT) | неотключаемое правило |
| Изоляция Blind Hunter | декларация промптом | **ничем не enforce'ится** — субагент физически может читать проект |
| «Чистое ревью» | LLM, но только при пустом {failed_layers} | анти-false-green правило (текстовое) |
| Применить патчи / решить decision | **человек-гейт**: нумерованное меню, «Reply with only the number» | жёсткий HALT |
| Статус done после ревью | LLM по условию «no unresolved HIGH/MEDIUM» | **невычислимо** (баг: шкалы нет) |
| Возврат dev после ревью | LLM по наличию секции в story | **разорвано** (P0: имена секций не совпадают) |
| Q&A после сдачи | человек (не блокирующий) | текст |

**Человек намеренно исключён** из основного цикла dev-story (Steps 5-9): «ZERO USER INTERVENTION», «Absolutely DO NOT stop». Человек стоит на границах: выбор story без трекинга, approve зависимостей, checkpoint ревью, меню патчей, Q&A.

**Характерная закономерность (зафиксирована раскопками):** самые жёсткие формулировки компонента («NO LYING OR CHEATING», «ACTUALLY EXIST», «do not skip») стоят ровно там, где **нет технической возможности проверить** — доверие к самоотчёту LLM компенсируется громкостью капса.

### Ползунок доверия vs Phase 1

| Измерение | Phase 1 | Phase 4 |
|---|---|---|
| Обязательность | вся фаза optional | впервые есть **required**-цепочка (sprint-planning → create-story → dev-story) |
| Предмет работы | документы | **реальный код** — цена ошибки выросла на порядок |
| State | frontmatter внутри документов | вынесен на диск в **машиночитаемый канал** (sprint-status.yaml) + Status-строка story; CAS-проверки переходов |
| Машинная истина | ноль | появилась одна — exit-коды тестов; **но автор не привязал к ней enforcement** — интерпретирует LLM |
| Контракты субагентов | JSON-контракты | смешанные: Edge Hunter — JSON, остальные — markdown-проза + best-effort parsing |
| Анти-галлюцинация | сорсинг (цитаты с источниками) | сорсинг остался ([Source: ...] в References) + **анти-фабрикация поведения** (red-тест как доказательство, чекбокс-валюта) |
| Независимость проверки | FORBIDDEN-роли внутри одного промпта | **контекстная и модельная изоляция**: fresh-context валидатор, 3 охотника с градиентом доступа, рекомендация другой LLM |
| Человек | ведёт фазу (guided) | вытеснен из внутреннего цикла, оставлен на гейтах |

Итог: ползунок сдвинулся от «человек ведёт, LLM пишет документы» к «LLM ведёт, человек дежурит на КПП». Но фундамент не поменялся: **enforcement остался текстовым**. Фаза, которая впервые получила доступ к настоящей машинной истине, продолжает охранять её капслоком.

---

## 4. Соответствие канону

| Канон (названо в файлах?) | Что взято | Что выкинуто | Что переосмыслено под solo+LLM |
|---|---|---|---|
| **Scrum** (названо: sprint planning, backlog, retrospective, Definition of Done; НЕ названо: Definition of Ready — есть только статус) | события (planning, retro), бэклог, DoD/DoR, статусная модель story | команда и роли как люди, таймбокс спринта, daily, оценки/velocity, инкремент как релизное событие | церемонии стали **скиллами**, роли — промпт-персонами (Bob/sm, Amelia/dev); «спринт» — это yaml-файл со статусами; planning = генерация state-файла, а не встреча |
| **TDD / Red-Green-Refactor** (назван цикл, слово «TDD» отсутствует) | red до кода с проверкой «падает по правильной причине», minimal code, refactor под зелёными | микрогрануляция «один тест за раз», «не пиши второй тест пока первый красный», TDD как инструмент дизайна | цикл укрупнён до task/subtask (экономия токенов); назначение смещено с дизайна на **верификацию честности LLM** — красный тест = доказательство, что тест не фиктивен; плюс test-after хвост (Step 6), Беку чуждый |
| **BDD** (названо) | формат критериев приёмки (Given/When/Then приходит из epics) | исполняемые сценарии (Gherkin-раннеры), «три амиго», ubiquitous language как процесс | BDD сведён к **формату текста** в epics; проверка — пост-хок LLM-валидацией, не автоматикой |
| **User stories / Connextra** (названо «As a / I want / so that»; имя Connextra — нет) | шаблон дословно, INVEST-дух через AC | story как «обещание разговора» (Jeffries) — разговаривать не с кем | story раздута из карточки в **полный контекст-пакет** на тысячи токенов — анти-Jeffries: вся суть в том, чтобы разговор НЕ понадобился |
| **Kanban** (не названо) | pull из упорядоченной очереди, WIP=1, колонки-статусы | доска, метрики потока (lead time, CFD), классы обслуживания | очередь = порядок ключей YAML; pull выполняет LLM-диспетчер по правилу «первая сверху» |
| **Fagan inspections / Modern Code Review** (названо: code review, peer review, triage, false positive) | независимость инспектора, фазы rework/follow-up, запись находок, «ревью дельты, не legacy» | встречи, метрики инспекций, человеческие роли moderator/reader | независимость достигается **информационной асимметрией субагентов** (Blind/Edge/Auditor) и сменой модели; severity заменена на actionability (decision/patch/defer/dismiss) — маршрутизация человек-vs-LLM |
| **Test Pyramid** (уровни названы, слово «pyramid» — нет) | unit/integration/e2e как обязательные уровни | пропорции пирамиды, антипаттерн ice-cream cone | уровни — пункты DoD-чеклиста; пропорцию решает LLM |
| **CI** (не названо) | «прогнать всё после каждой партии», «чинить сборку немедленно» | сервер, git-интеграция (в dev-story НЕТ ни одной git-команды), автоматический триггер | CI как **поведенческая дисциплина внутри одной LLM-сессии**; интеграция отдана внешнему конвейеру |
| **ATDD** (не названо) | критерии до разработки, финальная сверка с AC | исполняемые приёмочные тесты до кода (FIT/FitNesse) | «ATDD по букве спецификации, не по механике» — формулировка раскопщика |
| **FMEA** (не названо; атрибуция по аналогии, medium) | дизайн от перечня отказов | таблицы RPN, количественные оценки | таксономия отказов **LLM** (8 пунктов COMMON LLM MISTAKES) как каркас всего create-story: каждый шаг закрывает свой пункт |
| **Circuit breaker** (не названо) | порог отказов → останов | автоматический half-open/recovery | «3 consecutive failures → HALT» — эскалация к человеку вместо авто-восстановления |

---

## 5. Карта настройки фазы

Принцип выживаемости при обновлении: BMAD vendorит файлы и хеширует их в `files-manifest.csv` — правка любого vendored-файла станет видимой инсталлятору как модификация и рискует быть перезатёртой/конфликтовать при апдейте. (Честность: сам манифест раскопками не разбирался — механика «что именно делает инсталлятор при mismatch» не проверена.) Поэтому ручки делятся на «живут вне vendored-файлов» (безопасные) и «правка vendored» (форк).

### Безопасные ручки (переживают обновление)

| Ручка | Что меняет | Файл |
|---|---|---|
| `communication_language`, `user_skill_level`, `project_name`, пути артефактов | язык, тон, расположение | `_bmad/bmm/config.yaml` ⚠️ workflows ссылаются также на `user_name`/`document_output_language`, которых в конфиге нет — можно **дописать** их и закрыть дрейф |
| Глобальные кодстандарты | подхватываются обоими скиллами автоматически; единственный легальный источник зависимостей помимо story | `**/project-context.md` |
| Приоритизация очереди | порядок ключей `development_status` | `{implementation_artifacts}/sprint-status.yaml` |
| Обход диспетчеров | явный `{{story_path}}` / номер epic-story | аргумент вызова |
| Шардирование документов | разложить PRD/architecture по папкам с index.md → discover-inputs переключится сам | `_bmad/planning-artifacts/` |
| Качество входа | epics «with BDD and source hints» из фаз 2-3: чем богаче epics, тем меньше fallback-чтения | epics.md |
| TEA-модуль | расширение тестов после dev (здесь НЕ установлен) | установка модуля |

### Правка vendored-файлов (форк — фиксируйте свои диффы)

| Ручка | Файл |
|---|---|
| Триггерные фразы скиллов | `*/SKILL.md` (description) |
| Input Files таблица (glob + FULL_LOAD/SELECTIVE_LOAD/INDEX_GUIDED), категории architecture extraction, глубина git-анализа (last 5), набор `<template-output>` секций | `bmad-create-story/workflow.md` |
| Форма story (секции, Dev Agent Record) | `bmad-create-story/template.md` |
| Категории дефектов и метрики «победы» валидатора | `bmad-create-story/checklist.md` |
| Разрешённые к правке секции story, порог циркит-брейкера (3), validation gates Step 8, HALT-условия | `bmad-dev-story/workflow.md` |
| Пункты DoD — **синхронно** с инлайн-DoD Step 9 (уже дрейфуют!) | `bmad-dev-story/checklist.md` + workflow.md |
| Таблица intent-фраз, порог diff ~3000 строк | `bmad-code-review/steps/step-01-gather-context.md` |
| Состав охотников (добавить Security Hunter), подмена core-скиллов, промпт Auditor | `steps/step-02-review.md` (+ формат в step-03) |
| Таксономия корзин триажа (согласованно со step-04!) | `steps/step-03-triage.md` |
| `deferred_work_file`, маппинг статусов, формат тегов `[Review][...]`, меню next steps | `steps/step-04-present.md` |
| Статус-коды и глубина error cases, пути test_dir | `bmad-qa-generate-e2e-tests/workflow.md` |

**Самая ценная правка форк-класса для этой инсталляции** — починить разорванную петлю ревью: привести имена секции/тегов step-04 к ожиданиям dev-story Step 3 (или наоборот). Без этого «code-review → dev-story дочинит» работает только вручную.

---

## 6. Уроки для своего пайплайна

1. **Story-файл как компилят контекста.** Не давайте исполнителю «задачу + доступ к докам» — дайте артефакт, в который компилятор-фаза уже отфильтровала релевантные guardrails из архитектуры, уроки прошлой итерации, git-конвенции и свежие версии библиотек. Исполнитель НЕ будет читать architecture.md сам — и не должен. Дизайн от таксономии отказов: сначала перечислите 8 способов, которыми ваш LLM лажает, потом постройте шаги, закрывающие каждый.

2. **Red-тест как детектор лжи, не как дизайн-практика.** Перенос красивый и дешёвый: «тест обязан упасть до кода» — это единственное доказательство, что тест что-то проверяет, а отчёт «всё прошло» не сфабрикован. Грануляцию можно укрупнять (подзадача, не микротест) — верификационная ценность сохраняется.

3. **Чекбокс — валюта, выдаваемая через ворота.** Разделяйте ФАКТ выполнения (артефакты существуют, exit-код зелёный) и ОТМЕТКУ выполнения. Право поставить `[x]` — после перечислимых ворот. И — урок ОТ ПРОТИВНОГО: BMAD охраняет это капсом («NO LYING OR CHEATING»). В своём пайплайне ставьте на этом месте **скрипт с реальным exit-кодом**, а не заклинание: фаза имела машинную истину (тесты) и не привязала к ней enforcement — не повторяйте.

4. **Информационная асимметрия ревьюеров.** Три охотника с градиентом доступа (только diff / diff+проект / diff+спека) дают ортогональные находки и убивают сикофантию: слепой ревьюер не может оправдать код намерением автора. Плюс правило «другая LLM на ревью». Помните только, что изоляция «промптом» не enforce'ится — если важно, режьте доступ инструментально (sandbox), не текстом.

5. **Триаж по actionability, не по severity.** `decision_needed / patch / defer / dismiss` — это маршрутизация «кому решать»: человеку / LLM сам / в журнал долга / в мусор. LLM плохо оценивает «важность», но неплохо отвечает на «однозначен ли фикс». Плюс анти-false-green: «ноль находок при упавшем слое = warning, а не ✅».

6. **State на диске + резюмируемость из артефакта.** sprint-status.yaml (с CAS-проверкой ожидаемого статуса и «never downgrade»), Status-строка в документе, режим continuation, выводимый из содержимого story — любой следующий процесс восстанавливает контекст из файлов, не из памяти модели. Каждое тупиковое состояние — HALT с нумерованным меню выхода для человека.

7. **Текстовые контракты между промптами гниют молча — нужен contract-test.** Центральная мораль фазы: петля dev→review→dev разорвана, потому что один промпт ждёт «Senior Developer Review (AI)»/`[AI-Review]`, а второй пишет «### Review Findings»/`[Review][...]` — и **никто этого не заметил при релизе v6.2.2**. Туда же: 13 template-output имён без единого совпадения с template.md, GOTO на несуществующий step 2a (5 раз), «HIGH/MEDIUM» в потребителе при «decision/patch/defer» в производителе, два дрейфующих DoD, конфиг-ключи, которых нет, в 8 workflow. Везде один класс: один факт — два независимых написания. В своём пайплайне: каждый producer↔consumer стык (имя секции, тег, статус, ключ конфига) — либо единый источник, либо автоматический тест стыка. Дублирование инструкций как «удержание внимания» (двойной `<critical>`) — приём рабочий, но дублирование **данных** — всегда бомба замедленного действия.

## Термины

- **HALT-гейт** — точка в инструкции, где LLM обязан остановиться и отдать решение человеку.
- **Thin-shim** — файл-заглушка из пары строк, который только перенаправляет к настоящей логике.
- **State machine (машина состояний)** — фиксированный набор статусов и разрешённых переходов между ними (backlog → ready-for-dev → in-progress → review → done).
- **DoR / DoD** — Definition of Ready/Done: чек-листы «можно брать в работу» / «можно считать готовым».
- **Red-Green-Refactor** — цикл TDD: сначала падающий тест, потом минимальный код до зелёного, потом уборка.
- **Триаж** — сортировка находок по корзинам «что с этим делать».
- **CAS-проверка (compare-and-swap)** — перед записью нового статуса сверить, что старый — ожидаемый.
- **Single-source drift** — один факт записан в двух местах независимо → копии разъезжаются → баг на стыке.
- **Fail-open / fail-closed** — при ошибке продолжить с предупреждением / остановиться.
- **Vendored-файлы** — скопированные в проект файлы фреймворка; обновление может их перезатереть.