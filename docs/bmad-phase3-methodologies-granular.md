# Phase 3 — Solutioning: подробный разбор

> Контекст для читателя: это третья фаза BMM (BMAD METHOD v6.2.2). Если Phase 1 (Analysis) была целиком optional и учила нас дисциплине HALT-гейтов и анти-галлюцинации через сорсинг, то Phase 3 — **обязательный мост между «что строим» (PRD из Phase 2) и «руками агентов строим» (Phase 4)**. Здесь рождаются два главных артефакта, которые потом машинно потребляет весь конвейер имплементации: `architecture.md` и `epics.md`.
>
> Главная идея автора, проходящая через всю фазу: **архитектурный документ — это не документ для людей, а «контракт консистентности» для флота LLM-агентов без общей памяти.** Всё остальное — следствия.

---

## 1. Карта фазы

Фаза живёт в `/home/server/Downloads/crm/_bmad/bmm/3-solutioning/` и состоит из **5 компонентов** (раскопки изначально покрыли 2, критик дополнил остальные 3):

| # | Компонент | Тип | Роль в фазе | Required? | Вход | Выход |
|---|---|---|---|---|---|---|
| 0 | `bmad-agent-architect` | агент-персона («Winston», 🏗️) | Точка входа: роль + меню CA/IR + HALT | Опционален (workflow вызываются и напрямую) | config через `bmad-init`, `project-context.md` | Маршрутизация в CA / IR |
| 1 | `bmad-create-architecture` | workflow, 8 шагов | Архитектурные решения + паттерны + структура | Де-факто обязателен: его выход читает всё ниже | **PRD (жёсткий HALT без него)**, brief, ux-design, research, project-context | `{planning_artifacts}/architecture.md` |
| 2 | `bmad-create-epics-and-stories` | workflow, 4 шага | PRD+Architecture+UX → эпики и stories | Де-факто обязателен: `epics.md` — топливо Phase 4 | PRD, **Architecture**, UX spec | `{planning_artifacts}/epics.md` |
| 3 | `bmad-check-implementation-readiness` | workflow, 6 шагов | **Exit-гейт фазы**: трассировка и аудит перед Phase 4 | Рекомендован (формально его можно пропустить — машинного замка нет) | PRD, Architecture, Epics, UX | `implementation-readiness-report-{{date}}.md` |
| 4 | `bmad-generate-project-context` | workflow, 3 шага | Поперечный producer `project-context.md` | `anytime`; в module-help.csv: «Essential for brownfield projects» | **Реальный код проекта** (package.json, конфиги, паттерны кодовой базы) | `{output_folder}/project-context.md` |

**Порядок задан данными, а не промптами.** `module-help.csv` (читается core-скиллом `bmad-help`, который вызывается в финале каждого workflow) содержит колонку предшественника:

```
bmad-create-architecture → bmad-create-epics-and-stories → bmad-check-implementation-readiness → [Phase 4]
```

Это **единственный живой CSV в округе** — важный контраст: два CSV внутри create-architecture (`data/*.csv`) — орфаны, их никто не читает, а вот этот реально определяет маршрутизацию. Честность: явных флагов required/optional в раскопках module-help.csv нет — выше моя реконструкция по графу предшественников и потребителей.

**Стыки с соседними фазами:**

- **Вход:** Phase 2 (`2-plan-workflows/`) производит PRD (`bmad-create-prd`) и UX spec (`bmad-create-ux-design`) — ровно те глобы `*prd*`, `*ux-design*`, которые ищет step-01-init.
- **Выход:** Phase 4 (`4-implementation/`) — `bmad-create-story` режет `epics.md` по заголовкам `### Story N.M` и читает `architecture.md`; цепочка дальше: `bmad-sprint-planning → bmad-create-story → bmad-dev-story → bmad-code-review → bmad-retrospective`.
- **Откат:** в Phase 4 живёт `bmad-correct-course` — официальный механизм «вернуться и переделать архитектуру/эпики», если на имплементации всё развалилось. В самой Phase 3 ответа на «валидация провалена — что дальше?» нет.
- **Тонкая, но важная деталь хореографии:** step-01 create-architecture **не ищет** `*epics*.md` — и это не баг, а порядок: в v6 эпики создаются **после** архитектуры. Но steps 02/06/07 при этом пишут «From Epics (if available)» — обещание входа, которого discovery никогда не принесёт (остаток старого порядка фаз).

---

## 2. Покомпонентный разбор

### 2.0 bmad-agent-architect — персона Winston (точка входа)

*(Не раскопан в основном проходе, восстановлен критиком.)*

- **Файлы:** `SKILL.md` (52 строки) + `bmad-skill-manifest.yaml` (11 строк, `type: agent`, displayName: Winston, icon 🏗️).
- **Что это:** не workflow, а **агент-обёртка** — персона «Senior architect… distributed systems, cloud infrastructure, API design» с принципами вроде «Embrace boring technology for stability» и «Developer productivity is architecture».
- **On Activation:** грузит config через core-скилл `bmad-init`, ищет `**/project-context.md` как «foundational reference», показывает меню **Capabilities: CA → bmad-create-architecture, IR → bmad-check-implementation-readiness** и встаёт в HALT: «STOP and WAIT… DO NOT invent capabilities on the fly».
- **Удержание роли:** «you must not break character until the user dismisses this persona… when the user calls a skill, this persona must carry through» — это объясняет загадочную фразу «In addition to your name, communication_style, and persona…» в workflow.md обоих больших компонентов: **workflow исполняется ВНУТРИ персоны** (persona-композиция: роль скилла наслаивается, а не заменяет).
- **Примечательная асимметрия:** в меню Winston **нет** CE (create-epics-and-stories) и GPC (generate-project-context). Во вселенной BMAD эпики создаёт PM/SM-персона, не архитектор — разделение ответственности ролей дотянуто до меню.

**Настроить под себя:** меню Capabilities и принципы персоны — обычный markdown в SKILL.md; можно добавить CE в меню, если работаешь один и смена персон — лишний клик.

---

### 2.1 bmad-create-architecture — 8-шаговый workflow архитектуры

Самый большой компонент фазы (~2444 строки markdown). Из искомых канонических методологий **ЯВНО не названа НИ ОДНА** — ни ADR, ни C4, ни arc42, ни 4+1, ни ATAM, ни DDD, ни 12-factor, ни ISO 25010. Все присутствуют как неназванные следы или сознательно заменены самодельными механизмами. Дальше — по крупицам.

#### Крупица 1: точка входа — `SKILL.md`

- **Названо в файле:** ничего.
- **Узнаваемо без имени:** thin dispatcher / индирекция точки входа — весь файл 6 строк: frontmatter с триггерами («lets create architecture») + «Follow the instructions in ./workflow.md.» Паттерн манифеста slash-command/skill Claude Code (Anthropic, 2024–2025); аналог shebang-лаунчера. *Confidence: high.*
- **Изобретение BMAD:** разделение манифеста (триггеры) и тела (роль+инициализация) — контекст-экономия: в системный промпт грузится только description, остальное по требованию.
- **Замысел автора:** минимальная точка входа; фраза «for AI agent consistency» в description — заявка главной цели всего компонента уже на уровне триггера.
- **Настроить под себя:** триггерные фразы — поле description во frontmatter.

#### Крупица 2: оркестрация — `workflow.md`

- **Названо в файле:** ничего из индустриального канона.
- **Узнаваемо без имени:**
  - *Architect-as-facilitator* — «This is a partnership, not a client-vendor relationship… Work together as equals». Школа Richards & Ford, «Fundamentals of Software Architecture» (2020). *Confidence: medium.*
  - *Document-as-state + checkpoint/resume* — «Document state tracked in frontmatter. Append-only document building». Отголоски BPM/saga state persistence и Event Sourcing (Fowler, 2005). *Confidence: medium.*
  - *i18n через конфиг* — «YOU MUST ALWAYS SPEAK OUTPUT… with the config {communication_language}». *Confidence: high.*
- **Изобретение BMAD:**
  - **«Micro-file architecture»** — собственный, названный в самом файле термин: каждый шаг = самодостаточный файл со встроенными правилами; контекст-окно LLM получает только текущий шаг.
  - Жёсткий prompt-гейт перехода: «You NEVER proceed to a step file if the current step file indicates the user must approve».
  - Конфиг-инъекция из `_bmad/bmm/config.yaml` — параметризация поведения LLM данными.
- **Замысел автора:** две болезни LLM — (1) проскакивание шагов → микрофайлы + запрет читать вперёд; (2) галлюцинация контента → роль «фасилитатор среди равных»: domain expertise приносит человек, LLM даёт структуру. State во frontmatter = возобновляемость без внешней БД.
- **Настроить под себя:** язык, skill level, пути артефактов — `_bmad/bmm/config.yaml`.

> ⚠️ **Дрейф (с поправкой критика):** workflow.md резолвит 8 переменных, но не включает `{project_knowledge}` в resolve-список — однако сам ключ **есть** в установленном config.yaml (`project_knowledge: "{project-root}/docs"`), так что LLM, загрузивший полный конфиг, переменную разрешит. Риск ниже, чем казалось в первом проходе.
> ⚠️ **Дрейф общесистемный:** workflow.md:27-28 требует резолвить `user_name` и `document_output_language` — **обоих полей нет в установленном config v6.2.2** (они существуют только как дефолты в `bmad-init resources/core-module.yaml`: 'BMad'/'English'). Бьёт по всем четырём workflow-компонентам фазы разом — это drift «шаблон workflow.md ↔ установленный config», а не локальная находка. `{{user_name}}` в architecture-decision-template.md неразрешим по той же причине.

#### Крупица 3: шаблон документа — `architecture-decision-template.md`

- **Названо:** ничего.
- **Узнаваемо без имени:** YAML frontmatter как machine-readable контракт состояния (`stepsCompleted: []`, `inputDocuments: []`, `workflowType`) — конвенция Jekyll/static-site frontmatter (Tom Preston-Werner, 2008), адаптированная под workflow-state. *Confidence: high.*
- **Изобретение BMAD:** **пустой «растущий» документ** — тело шаблона = одна строка «Sections are appended as we work through each architectural decision together». В отличие от arc42 или шаблона Нюгарда, скелет секций НЕ предопределён.
- **Замысел автора:** структуру несут step-файлы, чтобы LLM не заполнял пустые заголовки галлюцинациями раньше времени. Инкрементальная сборка под контролем человека вместо «вот тебе 12 разделов arc42, заполни».
- **Настроить под себя:** стартовые поля состояния и заголовок (копируется в `planning_artifacts/architecture.md` на step-01).

> ⚠️ **Дрейф:** шаблон не инициализирует поле `lastStep`, которое step-01b читает (строки 44, 63), а пишет только step-08 — при первом резюме прерванного workflow поля просто нет.
> ⚠️ **Дрейф:** step-01 ищет существующий документ по wildcard `*architecture*.md`, а создаёт `architecture.md` — посторонний файл (`architecture-notes.md`) ложно уведёт в режим continuation.

#### Крупица 4: данные — `data/project-types.csv`

- **Названо:** ничего.
- **Узнаваемо без имени:** rule-based intent classification по ключевым словам (detection_signals → project_type → typical_starters; 6 типов: web_app, mobile_app, api_backend, full_stack, cli_tool, desktop_app). Keyword-spotting NLP + таблицы «тип системы → референсный стек» в духе tech-radar'ов (ThoughtWorks, 2010+). *Confidence: medium.*
- **Изобретение BMAD:** таксономия доменов вынесена из промпта в CSV — редактируемая ручка без правки промптов. **Задумка** — см. дрейф.
- **Замысел автора:** детерминированная таблица маппинга «сигналы в PRD → тип проекта → стартеры», чтобы step-03 не выдумывал стек.

> 🔴 **Дрейф (тройной):**
> 1. **ОРФАН:** ни workflow.md, ни один из 9 step-файлов не ссылается на `data/` (grep по «csv|project-types|domain-complexity|data/» — 0 совпадений, верифицировано). Справочник продублирован хардкодом в step-03 — классический single-source drift: правка CSV ничего не изменит.
> 2. **CSV синтаксически битый:** поле `typical_starters` не взято в кавычки («…browsers,Next.js, Vite, Remix») — при честном парсинге строка распадается на 6 колонок вместо 4.
> 3. **Идейное противоречие:** step-03 требует «NEVER trust hardcoded versions», при этом справочник стартеров — хардкод 2024 года (Blitz, RedwoodJS уже в закате).

#### Крупица 5: данные — `data/domain-complexity.csv`

- **Названо в файле (единственное место компонента с именованными стандартами!):** **PCI compliance** (PCI DSS, Security Standards Council 2004), **HIPAA** (закон США 1996), **SCADA/OT cybersecurity** (подразумевается ISA/IEC 62443 — не назван), **BACnet** (ASHRAE стандарт 135, 1995) — всё в колонке `web_searches` как готовые поисковые строки.
- **Узнаваемо без имени:**
  - *Risk-based tailoring* — глубина процесса от риска домена (fintech/healthcare/government → high+enhanced). Спираль Boehm (1988), тейлоринг Disciplined Agile, Cynefin-подобная сортировка — без названий. *Confidence: medium.*
  - *Предзаготовленные research-запросы per domain* — query templates из research-ops/RAG-практик (2023+). *Confidence: medium.*
- **Изобретение BMAD:** трёхрежимная шкала workflow (standard/enhanced/advanced) + **12 доменов** (поправка критика: раскопки писали «13», по факту `cut -d, -f1` даёт 12: e_commerce, fintech, healthcare, social, education, productivity, media, iot, government, process_control, building_automation, gaming) с детекторами, включая экзотические process_control (PLC/SCADA/DCS/HMI/MES/P&ID) и building_automation (BAS/BMS/HVAC/DDC) — следы реального OT-опыта автора.
- **Замысел автора:** регуляторно-тяжёлые домены должны получать углублённый процесс и направленный compliance-ресёрч вместо общего чеклиста.

> 🔴 **Дрейф:** ОРФАН — файл не упоминается ни в одном md. Хуже: режимы standard/enhanced/advanced **не существуют** — в скилле один линейный путь step-01→08, никаких ветвлений «enhanced/advanced» нет. Мёртвая ось конфигурации: красивая задумка, к которой не построили исполнителя.

#### Крупица 6: `steps/step-01-init.md` — инициализация

- **Названо в файле:** **PRD** — «Architecture requires a PRD to work from… Do NOT proceed without PRD» (традиция продакт-менеджмента, Ben Horowitz «Good PM/Bad PM», HP/Intel).
- **Узнаваемо без имени:**
  - *Stage-Gate entry criteria* — нет PRD → HALT. Robert G. Cooper, 1986; entry/exit criteria CMMI. *Confidence: high.*
  - *Document discovery с конвенцией шардирования* — «if searching for \*foo\*.md and not found, also search for a folder called \*foo\*/index.md (sharded content)». LLM-инженерия 2023+ (BMAD сам её и популяризовал в v4). *Confidence: high.*
  - *Human confirmation перед загрузкой* — «Confirm what you have found with the user…». HCI/automation safety. *Confidence: high.*
- **Изобретение BMAD:**
  - **Экзоскелет step-файла** (повторяется во всех 9 шагах): MANDATORY EXECUTION RULES (эмодзи-запреты) + EXECUTION PROTOCOLS + CONTEXT BOUNDARIES + SUCCESS METRICS (✅) + FAILURE MODES (❌) — двойное позитивно/негативное подкрепление.
  - «ALWAYS read the complete step file before taking any action» — борьба с частичным чтением файлов.
  - **«ABSOLUTELY NO TIME ESTIMATES - AI development speed has fundamentally changed»** — запрет выученных LLM «2-3 weeks».
  - Ветка-детектор continuation (есть документ со stepsCompleted → STOP → step-01b) — state-machine на frontmatter.
  - Трекинг `inputDocuments[]` — audit trail того, что LLM реально читал.
- **Замысел автора:** детерминированная инициализация для недетерминированного исполнителя. SUCCESS/FAILURE-пары — самопроверка LLM в отсутствие машинного гейта.
- **Настроить под себя:** пути discovery (глобы `*brief*`, `*prd*`, `*ux-design*`, `*research*`, project-context.md) — секция A; обязательность PRD — блок «PRD Validation».

> ⚠️ **Дрейф:** discovery не ищет `*epics*.md`, при этом steps 02/06/07 строят анализ «From Epics (if available)» — в v6 эпики создаются ПОСЛЕ архитектуры, так что условие почти всегда пусто (см. карту фазы).
> ⚠️ **Дрейф:** меню одной кнопки [C] здесь vs A/P/C в шагах 2-7 — асимметрия: advanced elicitation недоступна именно на discovery.

#### Крупица 7: `steps/step-01b-continue.md` — resume

- **Названо:** ничего.
- **Узнаваемо без имени:**
  - *Checkpoint/resume по персистентному состоянию* — BPM/durable execution, crash-recovery по журналу. *Confidence: high.*
  - *Destructive-action double confirmation* — «This will delete all existing architectural decisions. Are you sure? (y/n)». Стандарт CLI/GUI safety. *Confidence: high.*
  - *State drift repair* — «If stepsCompleted is empty but document has content… Should I analyze what's here?». Reconciliation loop в духе Kubernetes-контроллеров (2015+), ручной вариант. *Confidence: medium.*
- **Изобретение BMAD:** меню **R/C/O/X** — Resume (формально по stepsCompleted) / Continue (LLM смыслово оценивает полноту контента) / Overview (нелинейный выбор шага) / Start over. Плюс белый список целей навигации (step-02…step-08), чтобы LLM не выдумал несуществующий файл.
- **Замысел автора:** прерванная сессия — норма; автор различает «формальный резюм» и «смысловой резюм» — признание, что state-флаги и реальность документа разъезжаются, и для каждого случая прописан ремонт.

> ⚠️ **Дрейф:** читает `lastStep`, который шаблон не инициализирует. Опция 'O' разрешает нелинейный порядок («Don't assume sequential progression is always best») — противоречит духу остального компонента (жёсткая цепь 1→8 с запретом смотреть вперёд).

#### Крупица 8: `steps/step-02-context.md` — анализ контекста

- **Названо в файле:** **NFR** («performance, security, compliance» — SEI, Bass/Clements/Kazman 1998), **FR** (IEEE 830), **Cross-cutting concerns** (AOP, Kiczales/Xerox PARC 1997), **WCAG** (W3C 1999+).
- **Узнаваемо без имени:**
  - *Quality attributes БЕЗ таксономии ISO/IEC 25010* — вместо 8 характеристик стандарта самодельный список «Complexity Indicators» (real-time, multi-tenancy, compliance, integration, data volume…). ISO 25010 / FURPS+ заменены ad-hoc шкалой. *Confidence: high.*
  - *Active listening / reflect-back* — секция «Reflect Understanding». BABOK-техника, Rogers/Farson 1957. *Confidence: medium.*
  - *T-shirt sizing сложности* — «[low/medium/high/enterprise]». *Confidence: medium.*
- **Изобретение BMAD:**
  - **A/P/C меню** (Advanced Elicitation / Party Mode / Continue) — фирменный цикл: черновик → углубление (A) или мультиперсонный разбор (P) → сохранение только по явному C («ONLY save when user chooses C»).
  - Контракт повторного входа: после A/P всегда возврат в это же меню + y/n приём изменений — вложенный гейт внутри гейта.
  - «Content Structure» с {{placeholder}} — фиксированный скелет markdown (контроль формы выхода).
  - Условные ветки в скрипте речи: `{if_epics_loaded}/{if_no_epics}/{if_ux_loaded}` — псевдо-шаблонизатор внутри промпта.
- **Замысел автора:** «No technology decisions yet - pure analysis phase» — отделение problem space от solution space. FAILURE MODE «Skimming documents without deep architectural analysis» бьёт по главной болезни LLM на длинных PRD.
- **Настроить под себя:** complexity indicators (секции 2, 4); замена bmad-advanced-elicitation/party-mode на свои скиллы.

> ⚠️ **Дрейф:** CONTEXT BOUNDARIES обещает входы «(PRD, epics, UX spec)», но step-01 эпики не ищет. И терминологическая ловушка: «APPEND TO DOCUMENT: …using the structure from step 4» — «step 4» означает ВНУТРЕННЮЮ секцию 4 файла, не step-04-decisions.md; перегрузка слова step повторяется во всех шагах — риск, что LLM прыгнет в чужой файл.

#### Крупица 9: `steps/step-03-starter.md` — выбор стартера

- **Названо в файле:** **PWA** («Offline capability → Service worker or PWA configured starter» — Google/Alex Russell, 2015).
- **Узнаваемо без имени:**
  - *Reuse-first / golden path* — «saves us from making dozens of small technical choices». Netflix «paved road» (~2017), Spotify Golden Path (2020), scaffolding-генераторы (Rails, 2004). *Confidence: high.*
  - *Walking skeleton* — «Project initialization… should be the first implementation story». Cockburn (~2004), tracer bullet Hunt/Thomas (1999). *Confidence: medium.*
  - *Anti-hallucination grounding* — «🌐 ALWAYS search the web to verify current versions - NEVER trust hardcoded versions» + 6+ шаблонов запросов. Retrieval grounding, LLM-инженерия 2023+. *Confidence: high.*
  - *Instructional scaffolding* — три скрипта подачи Expert/Intermediate/Beginner («Think of it like buying a prefab house frame…»). Wood, Bruner & Ross (1976); progressive disclosure Нильсена — не названы. *Confidence: medium.*
  - *Выбор технологии БЕЗ формальной матрицы* — качественные рубрики вместо weighted scoring / Pugh matrix (1981). *Confidence: high.*
- **Изобретение BMAD:** maintenance-status как критерий (поиск «recent updates maintenance status» — проверка живости проекта); мост UX→инфраструктура (анимации → Framer Motion, realtime → Socket.io); **секция 0 «Check Technical Preferences & Context»** — наследование правил из project-context.md, предпочтения сильнее рекомендаций.
- **Замысел автора:** решает «LLM рекомендует устаревший стек из training data»: версии и CLI-команды — только из веба. Стартер = «архитектура по умолчанию»: всё, что он решил, в step-04 не перерешивается.
- **Настроить под себя:** маппинг домен→стартеры (секция 1), шаблоны web-запросов (3, 4, 7), скрипты подачи (секция 6 + `user_skill_level` в config).

> ⚠️ **Дрейф:** нумерация секций с «### 0.» — единственный шаг с нулевой секцией; дублирует справочник из орфанного CSV; не предусмотрен исход «стартер не нужен» — секция «Selected Starter: {{starter_name}}» обязательна.

#### Крупица 10: `steps/step-04-decisions.md` — ядро: решения по 5 категориям

- **Названо в файле:** **REST / GraphQL** (Fielding 2000; Facebook 2015), **CI/CD** (Fowler/Beck XP; Humble & Farley 2010).
- **Узнаваемо без имени:**
  - **ADR в разобранном виде** — «Record the Decision: Category / Decision / Version / Rationale / Affects / Provided by Starter» ≈ поля Нюгарда (2011), который НЕ назван; вместо отдельных immutable ADR-файлов — секции одного документа. *Confidence: high.*
  - *MoSCoW-подобная приоритизация* — Critical / Important / Nice-to-Have + «Deferred Decisions (Post-MVP)». Dai Clegg, DSDM 1994 — не назван, трёхуровневая адаптация. *Confidence: medium.*
  - *Облегчённый ATAM* — «Check for Cascading Implications: This choice means we'll also need to decide…». SEI sensitivity/tradeoff points (Kazman, 2000) без сценариев качества и utility tree. *Confidence: medium.*
  - *Самодельная таксономия вместо view-моделей* — 5 категорий (Data / Auth&Security / API / Frontend / Infra) вместо 4+1 (Kruchten) или arc42. *Confidence: high.*
  - *Facilitated decision-making* — «Making recommendations instead of facilitating decisions» в FAILURE MODES; advice process (Bakke), Richards & Ford. *Confidence: medium.*
- **Изобретение BMAD:**
  - **Реестр «Already Decided (Don't re-decide these)»** — решения стартера + пользовательские предпочтения + project-context исключаются из повторного обсуждения (дедупликация решений, экономия токенов и внимания).
  - Обязательная web-верификация версий на каждое решение.
  - Поле «Provided by Starter» — **провенанс решения** (кто его принял: стартер или человек).
- **Замысел автора:** сердце компонента — дисциплина ADR без имени ADR: каждое решение = выбор человека + рационал + версия + влияние. Приоритизация защищает от paralysis-by-analysis; каскадный анализ не даёт забыть, что выбор БД тянет миграции и кэш.
- **Настроить под себя:** состав категорий (добавить Observability / ML/AI), поля записи решения.

> 🔴 **Дрейф:** внутреннее противоречие частоты гейта — строка 20 требует «Present A/P/C menu after each major decision category» (5 меню), а строка 201 показывает ОДНО меню после всех категорий. LLM получает два несовместимых указания (верифицировано построчно).
> ⚠️ **Дрейф-компромисс:** решения пишутся в общий architecture.md — потеряно ключевое свойство ADR (immutability, supersede-цепочки); замена сознательная, но журналирования/супersede решений нет вовсе.

#### Крупица 11: `steps/step-05-patterns.md` — правила консистентности для ИИ-агентов

- **Названо в файле:** **Anti-Patterns** (Koenig 1995; книга Brown et al. 1998), **Error boundary** (React 16, 2017).
- **Узнаваемо без имени:**
  - *Coding Standards из XP*, расширенные с кода на API/БД/события — «Table naming: users, Users, or user?», «/users or /user? Plural or singular?», «UserCard or user-card?», «snake_case or camelCase?». Kent Beck 1999, style guides Google/Airbnb — не названы. *Confidence: high.*
  - *Convention over Configuration* — DHH/Rails 2004, не назван. *Confidence: medium.*
  - *API guidelines / контракт ошибок* — «{data, error} or direct response?», «{message, code} or {error: {type, detail}}?». Microsoft/Zalando API Guidelines, JSON:API, RFC 7807 — не названы. *Confidence: high.*
  - *Package-by-feature vs by-layer* — Screaming Architecture (Martin, 2011), feature folders. *Confidence: medium.*
  - *Event naming/versioning* — CloudEvents (CNCF 2018), Greg Young — не названы. *Confidence: medium.*
- **Изобретение BMAD — ЦЕНТРАЛЬНОЕ для всего компонента:**
  - **«🎯 EMPHASIZE what agents could decide DIFFERENTLY if not specified»** — вариационный анализ для мульти-агентной разработки: перечислить ВСЕ точки, где два независимых LLM-агента примут разные решения (Naming/Structural/Format/Communication/Process Conflicts), и зафиксировать выбор ДО имплементации. Пример из файла: «one agent might name database tables 'users' while another uses 'Users' - this would cause conflicts».
  - Секция «Enforcement Guidelines: All AI Agents MUST…» — попытка enforcement (см. дрейф).
  - Требование пар Good Examples / Anti-Patterns для каждого правила — few-shot прямо в архитектурном документе.
- **Замысел автора:** coding standards переосмыслены под новую аудиторию — читатель не junior, а флот LLM-агентов без общей памяти. Документ = единственный канал координации, поэтому фиксируется всё, что у людей живёт в устной культуре команды.
- **Настроить под себя:** каталог точек конфликта (добавить i18n, конвенции миграций, feature flags); шаблон Enforcement Guidelines — естественное место для подключения машинных гейтов (линтеры/CI), которых в оригинале нет.

> 🔴 **Дрейф:** Enforcement объявлен («All AI Agents MUST»), но не подключён НИ к одному механизму — нет линтеров, хуков, ссылок на code-review workflow. Правила держатся только на том, что dev-агент прочитает документ.
> ⚠️ **Дрейф-порча:** «Where do tests live? **tests**/ or \*.test.ts co-located?» — в оригинале явно было `__tests__/`, подчёркивания съедены как bold-разметка (строка 149, верифицировано).

#### Крупица 12: `steps/step-06-structure.md` — структура проекта и границы

- **Названо:** ничего.
- **Узнаваемо без имени:**
  - *Requirements traceability в файловую систему* — «Epic: User Management → Components: src/components/features/users/, Services: src/services/users/…». RTM-школа (IEEE 830 / CMMI / DO-178B), но трассировка не в таблицу тестов, а в директории. *Confidence: high.*
  - *Эхо Bounded Context БЕЗ DDD* — «Define Integration Boundaries: API / Component / Data Boundaries»; границы физические (файлы/слои/API), не лингвистически-доменные; Evans не назван, ubiquitous language отсутствует. *Confidence: medium.*
  - *Layered + Repository через пример NestJS* — дерево «modules/, services/, repositories/, guards/, interceptors/». Fowler PoEAA 2002, конвенции NestJS — паттерны заданы примером, не именем. *Confidence: medium.*
  - *Следы 12-factor* — «.env, .env.example» в деревьях; factor III Config (Wiggins, 2011) присутствует только как dotenv-конвенция. *Confidence: medium.*
  - **Структурная модель ВМЕСТО C4/4+1** — единственное представление архитектуры = ASCII-дерево файлов + проза о границах; ни одного diagram/C4/view во всём файле. Сознательная замена: дерево LLM-агент потребляет лучше диаграммы. *Confidence: high.*
- **Изобретение BMAD:**
  - «Create complete project tree, not generic placeholders» + FAILURE MODE против generic-заглушек — **полное дерево до последнего файла = исполняемая спецификация раскладки кода**.
  - Маппинг cross-cutting concerns на конкретные пути (Authentication System → `src/middleware/auth.ts`, `src/guards/auth.guard.ts`) — адресная книга для будущих агентов.
  - Два эталонных дерева (Next.js Full-Stack, NestJS) прямо в промпте — few-shot нужной детализации.
- **Замысел автора:** step-05 отвечает «как писать», step-06 — «куда класть». Самый частый конфликт параллельных агентов — один и тот же код в разных местах; полное дерево + трассировка эпик→директория устраняет свободу размещения.
- **Настроить под себя:** заменить эталонные деревья на свой стек (Rust/Python/Go); добавить security/trust boundaries в рубрики границ.

> ⚠️ **Дрейфы-порча (4 шт., верифицированы построчно):** битый плейсхолдер `{{how_the_project_is organized_for_development}}` (пробел внутри имени, строка 300); артефакт экранирования `prisma/migrations/_*users*_` (строка 222); сломанная вложенность code-fence в Content Structure (строки 240-248 — внешний блок ```markdown закрывается сразу после заголовка дерева, остальной шаблон выпадает); опора на эпики, которые step-01 не discovery-ит.

#### Крупица 13: `steps/step-07-validation.md` — валидация и gap-анализ

- **Названо в файле:** **Gap Analysis** («Identify and document any missing elements» — техника бизнес-анализа, BABOK/IIBA).
- **Узнаваемо без имени:**
  - *Architecture review БЕЗ ATAM* — чеклист когерентности и покрытия вместо сценарного trade-off анализа; ближе к design review checklist (родословная Fagan inspection, 1976) в self-review варианте. *Confidence: high.*
  - *Requirements coverage check* — «Does every epic have architectural support?… every functional requirement?». V-model, IEEE 1012 — не названы. *Confidence: high.*
  - *Severity triage* — Critical/Important/Nice-to-Have gaps. IEEE 1044 / bug triage. *Confidence: high.*
  - *DoD / readiness assessment* — «Overall Status: READY FOR IMPLEMENTATION; Confidence Level». Scrum DoD + phase-gate exit criteria (Cooper). *Confidence: medium.*
- **Изобретение BMAD:**
  - «Implementation Readiness Validation» с уникальным критерием: **«Assess if AI agents can implement consistently»** — готовность измеряется не для людей, а для LLM-агентов (полнота паттернов = «Are all potential conflict points addressed?»).
  - «Implementation Handoff: AI Agent Guidelines — Follow all architectural decisions exactly as documented» — финальная инструкция-обёртка для следующих агентов конвейера.
  - «First Implementation Priority» — первая стори Phase 4 предопределена уже здесь.
- **Замысел автора:** exit-гейт фазы — когерентность, покрытие, готовность. Но валидатор = тот же LLM, что писал документ, поэтому реальная сила гейта — человек, читающий результаты.

> 🔴 **ГЛАВНЫЙ ДЕФЕКТ ГЕЙТА (верифицирован построчно):** чеклист в шаблоне **предзаполнен** — все 16 пунктов уже `[x]` (строки 232–256), все три заголовка «✅», а «**Overall Status:** READY FOR IMPLEMENTATION» **захардкожен** (строка 260). Шаблон структурно не предусматривает исход FAIL — LLM подталкивается объявить успех независимо от находок. Это валидационный театр + self-grading без независимого проверяющего. Сводка секции 7 тоже предзаполнена зелёным ДО показа пользователю.
> Важный контекст от критика: **фаза знает об этой проблеме** — у неё есть ВТОРОЙ, отдельный валидатор (`bmad-check-implementation-readiness`) с независимым промптом и честным ПУСТЫМ шаблоном. См. 2.3.
> ⚠️ Мелкие дрейфы: незакрытая кавычка цитаты в секции 7; «[Show the complete markdown content from step 6]» — снова внутренняя секция, не step-06-structure.md.

#### Крупица 14: `steps/step-08-complete.md` — завершение и handoff

- **Названо в файле:** **Single source of truth (SSOT)** — «The architecture will serve as the single source of truth for all technical decisions…» (information systems design, популяризовано DevOps/IaC).
- **Узнаваемо без имени:** празднование завершения (agile celebration; даже FAILURE MODE «Failing to celebrate the successful completion» — *confidence: medium*); формальное закрытие с terminal state (`status: 'complete'`, `completedAt`) — state machine / BPM lifecycle. *Confidence: high.*
- **Изобретение BMAD:** маршрутизация в следующую фазу через каталог — «Invoke the bmad-help skill» вместо хардкода «иди в create-epics-and-stories» (расцепление фаз; как выяснил критик, под капотом bmad-help — машинно-читаемый граф module-help.csv). Единственный шаг без A/P/C и без [C] — терминальное состояние явно маркировано.
- **Замысел автора:** закрыть состояние машинно (чтобы step-01 будущих запусков и соседние скиллы видели завершённость), закрыть отношение по-человечески (похвала solo-пользователю), передать управление навигатору.
- **Настроить под себя:** заменить bmad-help на прямой переход (например, сразу в check-implementation-readiness).

> ⚠️ **Дрейф:** FAILURE MODES скопированы из общего шаблона («Proceeding with 'C' without fully reading the next step file» — но здесь нет ни 'C', ни следующего файла) — copy-paste дрейф экзоскелета. Плюс `{{current_date}}` vs `{{date}}` — два имени для даты в одном компоненте (микро single-source drift).

---

### 2.2 bmad-create-epics-and-stories — рождение user stories

7 файлов: SKILL.md (6 строк) + workflow.md (53) + 4 step-файла (255+212+255+131) + epics-template.md (61). Исполнитель всего компонента — LLM в диалоге; ни одного скрипта.

#### Крупица 1: SKILL.md + workflow.md — контракт исполнения

- **Названо в файле:**
  - **Product Owner** — «collaborating with a product owner. This is a partnership, not a client-vendor relationship» (Scrum, Schwaber & Sutherland 1995). **Единственный дословно названный индустриальный термин во всём компоненте** (проверено grep).
  - **PRD** — «Transform PRD requirements and Architecture decisions into…».
  - **Epics / Stories / Acceptance Criteria** — агильная лексика (XP user stories ~1998; иерархия epic→story — Mike Cohn, «User Stories Applied», 2004).
- **Узнаваемо без имени:** progressive disclosure / JIT-контекст («Only 1 current step file will be loaded… NEVER create mental todo lists from future steps» — *medium*); чеклист-манифест с запретом импровизации («no skipping or optimization allowed» — Gawande lineage, *medium*); фасилитация vs генерация (Agile Manifesto + Kaner, *medium*).
- **Изобретение BMAD:** step-file architecture; state-контракт через frontmatter выходного epics.md; Append-Only Building (защита от перезаписи одобренного); **Menu-HALT протокол** («ALWAYS halt at menus and wait for user input»); **persona-композиция** («In addition to your name, communication_style, and persona, you are also…» — теперь понятно почему: workflow исполняется внутри Winston или PM-агента).
- **Замысел автора:** SKILL.md сведён к одной строке-редиректу, workflow.md выносит ВСЕ правила дисциплины до начала содержательной работы. «SYSTEM FAILURE» и капс — компенсация отсутствия машинного enforcement.

> ⚠️ **Дрейфы:** требует резолвить `user_name`/`document_output_language` — нет в config (общесистемный drift, см. 2.6); опечатка «adhere too» (too→to); **аудитория поехала**: цель в workflow.md — «for development teams», а step-03 сайзит stories под «a single dev agent» — текст про человеческие команды остался от до-AI редакции BMAD.

#### Крупица 2: `step-01-validate-prerequisites.md` — входной гейт + инвентаризация

- **Названо в файле:** **FR/NFR** («what the system must DO» / «performance, security, usability, reliability» — IEEE 830-1998, Sommerville); **greenfield / starter template** (жаргон ~1990-х).
- **Узнаваемо без имени:** entry criteria / phase-gate (нет PRD/Architecture/UX → не начинаем; Stage-Gate Cooper 1986 / Definition of Ready применительно к workflow — *high*); requirements traceability через нумерованные ID («FR1:», «NFR1:», «UX-DR1:» — RTM-школа, *high*); верифицируемость требований («testable requirement» — IEEE 830 §4.3 / ISO 29148, *high*); review-цикл до подтверждения (*medium*).
- **Изобретение BMAD:**
  - Sharded-document протокол поиска (`*prd*/index.md`).
  - **UX-DR как первоклассный тип требования** — «The UX Design Specification is a first-class input document, not supplementary material», отдельная нумерация.
  - **Анти-компрессия гейт:** «🚨 CRITICAL: Do NOT reduce UX requirements to vague summaries... If the UX spec identifies 6 reusable components, list all 6» — защита от известного сбоя LLM (суммаризация теряет специфику).
  - inputDocuments-контракт; однокнопочное меню [C] с правилом редиспетча (любой вопрос → ответить → заново меню — анти-дрейф диалога); Master Rule «Skipping steps… constitutes SYSTEM FAILURE».
- **Замысел автора:** шаг сжат до «только извлечение» («FORBIDDEN to start creating epics or stories in this step») — сепарация extract→design→generate против забегания вперёд. Epic 1 Story 1 заранее «бронируется» под starter template — хореография будущей структуры уже на извлечении.
- **Настроить под себя:** состав входных документов (человек редактирует inputDocuments); UX-ветка опциональна (активируется наличием `*ux*.md`).

> ⚠️ **Дрейфы:** `{user_name}` неразрешим; опечатка «read then entire document»; сломана нумерация секций (между «### 9» и «### 10» вклинивается ненумерованный H2 «## CONTENT TO SAVE TO DOCUMENT:» — а правила требуют исполнять «in order»); меню только [C] без [A] — глубокая проработка недоступна там, где пропуск требования дороже всего; примеры компонентов в §6 (ConfirmActions, StatusMessage, EmptyState, FocusIndicator) выглядят осадком конкретного UX-спека, вшитым в generic-инструкции (против stock-версии не верифицировано).

#### Крупица 3: `step-02-design-epics.md` — декомпозиция в эпики

- **Названо в файле:** **Incremental Delivery** («Each epic should deliver value independently» — Tom Gilb, 1988); **user journeys/workflows** (UX-школа 2000-х).
- **Узнаваемо без имени:**
  - **Vertical slicing — дух без имени:** «Organize by USER VALUE, not technical layers» + поимённые анти-примеры «❌ Epic 1: Database Setup — No user value; ❌ Epic 2: API Development; ❌ Epic 3: Frontend Components» против «✅ Epic 1: User Authentication & Profiles». Это ровно запрет горизонтальных срезов — «slice the cake» (Bill Wake 2003, Cohn 2004); слово «vertical» в файле отсутствует (grep). *Confidence: high.*
  - **INVEST-Independent на уровне эпиков** — «Each epic must be standalone and enable future epics without requiring future epics to function». I из INVEST (Wake, 2003); акроним не назван. *Confidence: high.*
  - **RTM** — «FR Coverage Map… This ensures no FRs are missed»; слово traceability отсутствует, у практики собственное имя. *Confidence: high.*
  - Слабый след user story mapping (Patton) — группировки без backbone. *Confidence: low.*
  - Explicit sign-off gate — «Must get explicit user approval… Repeat until approval is received». *Confidence: medium.*
- **Изобретение BMAD:**
  - **Двухконтурное правило зависимостей:** эпик обязан быть standalone И «enable future epics» — направленный ацикличный порядок, заточенный под конвейер dev-агентов (**DAG-friendly декомпозиция** — прямо то, что потребляет наш bmad-orchestrator).
  - Пять пронумерованных EPIC DESIGN PRINCIPLES с ✅/❌ контраст-парами — contrastive few-shot прямо в промпте.
  - Меню [A]/[P] — подключаемые скиллы углубления на гейте.
- **Замысел автора:** сломать дефолтную привычку LLM (обученного на миллионах «сначала сделаем БД») резать систему по слоям — поэтому анти-примеры названы поимённо.

> ⚠️ **Дрейфы:** ярлык принципа 5 «Dependency-Free Within Epic» вводит в заблуждение — запрещены только FORWARD-зависимости, backward-цепочка предполагается: это не dependency-free, а топологический порядок. И **фасилитаторский парадокс:** «🛑 NEVER generate content without user input» vs §3 Step B «Propose Epic Structure» (генерация ДО ввода); рабочая трактовка «предлагай, но не финализируй» нигде не сформулирована.

#### Крупица 4: `step-03-create-stories.md` — КЛЮЧЕВОЙ шаг: рождение stories

- **Названо в файле (формат — да, школа — нет):**
  - **As a / I want / So that** — «STORY FORMAT (from template): As a {user_type}, I want {capability}, So that {value_benefit}». Это Connextra template (Лондон, 2001; Rachel Davies и др.), популяризован Cohn 2004 — **слова «Connextra» и «Cohn» в файле отсутствуют.**
  - **Given/When/Then** — «AC Writing Guidelines: Use Given/When/Then format». Это BDD (Dan North & Chris Matts ~2004, Gherkin/Cucumber 2008) — **слова «BDD» и «Gherkin» отсутствуют.**
  - **Acceptance Criteria** — XP/Scrum; формализация ATDD/Specification by Example (Adzic).
- **Узнаваемо без имени — поименный аудит INVEST** (акроним отсутствует, grep подтверждён; *confidence: high*):

  | Буква | Статус в BMAD | Доказательство |
  |---|---|---|
  | **I**ndependent | required, **ослаблен** | «Stories MUST NOT depend on future stories» — запрещён только forward; backward легален («✅ RIGHT: Each story can be completed based only on previous stories») |
  | **N**egotiable | **выброшен** | Collaborative review при создании есть, но после approval story = зафиксированный контракт; переговоры не предусмотрены |
  | **V**aluable | required | «Have clear user value», анти-пример «Set up database (no user value)» |
  | **E**stimable | **отсутствует полностью** | Ни поинтов, ни часов нигде; вместо оценки — бинарный габарит «влезает в одну сессию dev-агента» |
  | **S**mall | required через прокси | «Are sized for single dev agent completion», анти-пример «Build authentication system (too large)» |
  | **T**estable | required | «Each AC should be independently testable» |

  Плюс: **YAGNI / emergent design для данных** — «❌ WRONG: Epic 1 Story 1 creates all 50 database tables; ✅ RIGHT: Each story creates/alters ONLY the tables it needs» (XP YAGNI + Ambler/Sadalage «Refactoring Databases» 2006, *high*); **3C Card-Conversation-Confirmation** (Jeffries 2001, *medium*); Specification by Example дух в AC (*medium*); vertical slicing на уровне story (*high*); WBS-нумерация N.M (*low*).
- **Изобретение BMAD:**
  - **Единица сайзинга = «single dev agent» session** — замена story points/идеальных дней на ёмкость контекстного окна и одной автономной сессии AI-агента. Ключевая инновация компонента относительно классики.
  - **Backward-only dependency contract** — «❌ Login UI (depends on Story 1.3 API endpoint) (future dependency!)» — эпик превращается в линейно исполняемый конвейер без блокировок для последовательных dev-агентов.
  - Per-story микро-гейт: каждая story показывается человеку с тремя контрольными вопросами ДО append — инкрементальное одобрение вместо батч-ревью.
  - Append-протокол с template compliance («FORBIDDEN to deviate from template structure») — формат выходного файла как **парсимый контракт** для следующих скиллов (bmad-create-story читает epics.md).
  - UX-DR coverage обязательство — либо внутри фич-эпиков, либо выделенным «Design System / UX Polish» эпиком.
- **Замысел автора:** мост между человеческим agile и машинным исполнением. Классическая форма (Connextra + GWT) сохранена дословно — она и человекочитаема, и парсима. Всё, что опиралось на живой разговор (Negotiable, Estimable, спринты), вырезано или заменено машинными прокси: AC исчерпывающие заранее, потому что у dev-агента не будет возможности «переспросить заказчика» в середине работы.
- **Настроить под себя:** формат story/AC — через templates/epics-template.md (single source); размер stories — per-story диалог; [A]/[P] на финальном гейте.

> ⚠️ **Дрейфы:** фасилитаторский парадокс в максимуме — Universal Rules «NEVER generate content without user input» vs название шага «Generate Epics and Stories» и команда «Generate Each Story» (на практике побеждает генерация с ревью); формат AC жёстко включает четвёртую строку «**And** {additional_criteria}» как обязательную — в Gherkin `And` опционален, шаблон провоцирует филлерные критерии; «универсальные» правила нестабильны между шагами (слот Universal Rules в step-03 подменён другим правилом).

#### Крупица 5: `step-04-final-validation.md` — выходной чеклист

- **Названо:** только acceptance criteria как объект проверки.
- **Узнаваемо без имени:** **Definition of Ready — дух без имени** (термин отсутствует, grep): «validate complete coverage… ensure stories are ready for development» + 6-пунктный чеклист готовности (*high*); requirements coverage verification («Go through each FR… No FRs should be left uncovered» — IEEE 1012 / CMMI REQM, *high*); exit criteria / quality gate («FORBIDDEN to approve incomplete coverage» + литеральный «HALT», *medium*); INVEST I/S/T как валидационные проверки (*high*); YAGNI-проверка на выходе («No big upfront technical work», анти-BDUF, *high*).
- **Изобретение BMAD:** пятисекционный самопроверочный чеклист (FR Coverage → Architecture Implementation → Story Quality → Epic Structure → Dependency); **машиноподобная индуктивная проверка цепочки** — «Can Story N.3 be completed using only Stories N.1 & N.2 outputs?» — готова стать алгоритмом, но исполняется LLM; жёсткая хореография «If starter template → Epic 1 Story 1 must be 'Set up initial project from starter template'»; CONTEXT BOUNDARIES «Validation only, no new content creation»; хэндофф в bmad-help.
- **Замысел автора:** DoR, переписанный для конвейера AI-агентов: проверяется не «поймёт ли разработчик», а «сможет ли автономный агент исполнить без блокировок» — отсюда одержимость forward-dependencies (заблокированный агент = сломанный конвейер). Дублирование проверок шагов 2-3 вторым проходом — дешёвая страховка от дрейфа LLM на длинной сессии.

> 🔴 **Дрейф-парадокс:** единственный шаг БЕЗ «SYSTEM SUCCESS/FAILURE METRICS» и Master Rule — **самый ответственный гейт получил наименьшее правиловое давление** (и файл вдвое короче остальных: 131 строка против 212–255). Меню без [A]/[P]. И главное: self-validation без независимости — чеклист исполняет тот же LLM-контекст, который генерил stories; adversarial-скиллы инсталляции не подключены. Проверка «AC fully address the FR» неоперациональна — свободная LLM-оценка под видом CRITICAL CHECK.

#### Крупица 6: `templates/epics-template.md` — скелет epics.md

- **Названо (вшито literally, без имён школ):** Connextra-скелет «As a {{user_type}}, I want {{capability}}, So that {{value_benefit}}.» и Gherkin-скелет «**Given** {{precondition}} **When** {{action}} **Then** {{expected_outcome}} **And** {{additional_criteria}}».
- **Узнаваемо без имени:** SRS-инвентарь требований + RTM в одном living document (*medium*); Mustache/Handlebars-стиль плейсхолдеров — не движок, а конвенция для LLM-подстановки (*high*).
- **Изобретение BMAD:** frontmatter state-контракт артефакта (`stepsCompleted: []`, `inputDocuments: []`); **HTML-комментарии как директивы цикла** («<!-- Repeat for each epic in epics_list (N = 1, 2, 3...) -->») — псевдо-шаблонизатор, исполняемый пониманием модели, а не движком; двухиндексная схема {{N}}.{{M}} — нумерационный контракт, на который завязан downstream bmad-create-story.
- **Замысел автора:** шаблон — единственный source of truth формата (step-03 ссылается «STORY FORMAT (from template)»). Индустриальные форматы вшиты литерально, потому что одновременно (а) лучшая практика для людей, (б) стабильная парсимая структура для машинных потребителей (авто-оркестраторы режут epics.md регэкспами по «### Story N.M»).

> ⚠️ **Дрейфы:** обязательная четвёртая строка «**And**» (см. выше); **дублирование данных** — «## Epic List» ({{epics_list}}) сосуществует с пер-эпиковыми секциями «## Epic {{N}}» — название+цель эпика живут в двух местах без синхронизации (single-source drift риск); «NonFunctional Requirements» слитно vs «Non-Functional» в step-01; **дыра покрытия: NFR в эпики формально не маппятся** — FR Coverage Map обязателен, NFR извлекаются в step-01 и дальше нигде не трассируются (step-04 §1 проверяет только FRs).

---

### 2.3 bmad-check-implementation-readiness — exit-гейт фазы

*(Раскопки покрыли частично — entry shim, workflow.md, step-01; остальное дополнил критик.)*

- **Роль (workflow.md, названо):** «You are an expert **Product Manager and Scrum Master**, renowned… in the field of **requirements traceability** and spotting gaps in planning… Your success is measured in spotting the failures others have made» — **единственный компонент фазы с адверсариальной установкой.** Также названо **Just-In-Time Loading** (термин JIT — Toyota Production System, Taiichi Ohno, перенесён на загрузку контекста).
- **Узнаваемо без имени:** весь скилл = **Definition of Ready** (Scrum-сообщество ~2008-2012, Rubin — не назван, *high*); Stage-Gate review на границе «before Phase 4» (*medium*); progressive disclosure (*medium*); в step-01 — document control / configuration management: инвентаризация и **принудительная дедупликация** whole vs sharded версий («FORBIDDEN to proceed with unresolved duplicates»).
- **Выход:** отдельный **датированный отчёт** `{planning_artifacts}/implementation-readiness-report-{{date}}.md`. Шаблон `readiness-report-template.md` — 4 строки, **ПУСТОЙ** (только Date/Project). В отличие от предзаполненного [x]-чеклиста step-07 create-architecture это **честный гейт**: финальный вердикт `[READY/NEEDS WORK/NOT READY]` — плейсхолдер, не хардкод.
- **6 шагов (~1012 строк):**
  1. `step-01-document-discovery` — инвентаризация PRD/Architecture/Epics/UX + дедупликация.
  2. `step-02-prd-analysis` — полное чтение PRD, экстракция всех FR/NFR с нумерацией, «FORBIDDEN to skip or summarize PRD content» (тот же анти-компрессионный приём).
  3. `step-03-epic-coverage-validation` — трассировка каждого PRD-FR против «FR Coverage Map» в epics.md + **обратная проверка** («FRs в epics, но НЕ в PRD» — ловля выдуманных требований).
  4. `step-04-ux-alignment` — UX↔PRD, UX↔Architecture; если UX нет, но UI подразумевается — warning.
  5. `step-05-epic-quality-review` — роль «EPIC QUALITY ENFORCER»: запрет технических эпиков, запрет forward dependencies («Epic N cannot require Epic N+1»), независимость stories — **третий проход** по тем же правилам, что steps 2-4 epics-компонента.
  6. `step-06-final-assessment` — сводка, «Don't soften the message - be direct», завершение через bmad-help.
- **Ключевое отличие дисциплины: НЕТ A/P/C меню вообще** — шаги auto-proceed («immediately load next step»). Read-only валидатор не требует approve-гейтов; единственный human-гейт — дедупликация документов в step-01.
- **Замысел (реконструкция):** это ответ архитектуры фазы на дефект step-07-validation — **второй валидатор с независимым промптом, адверсариальной ролью и пустым шаблоном**. Но честно: валидатор по-прежнему LLM, без машинного гейта, и от свежести контекста зависит, действительно ли он «независим» (если гонять в той же сессии — независимость иллюзорна).

> ⚠️ **Дрейфы:** `user_name`/`document_output_language` неразрешимы (общесистемный drift); шаговые frontmatter используют `{{date}}` в имени outputFile, но INITIALIZATION SEQUENCE workflow.md нигде не резолвит date (в отличие от generate-project-context, где date явно объявлен «system-generated current datetime»).

---

### 2.4 bmad-generate-project-context — producer контекста для агентов

*(Не раскопан в основном проходе, восстановлен критиком.)*

- **Цель:** «lean, LLM-optimized» `{output_folder}/project-context.md` — «critical rules… Focus on unobvious details that LLMs need to be reminded of». В module-help.csv: «Essential for brownfield projects», фаза `anytime` (поперечный).
- **Структура:** SKILL.md + workflow.md (43) + 3 step-файла (~785 строк) + шаблон (21).
  - `step-01-discover.md` (186) — **единственный шаг всей фазы, который читает реальный код**, а не только артефакты: package.json/requirements.txt/Cargo.toml, tsconfig/.eslintrc/jest.config, существующие naming/организационные паттерны кодовой базы.
  - `step-02-generate.md` (321) — A/P/C меню после каждой категории правил (стек+версии → language-specific → framework-specific → testing → anti-patterns), три режима Expert/Intermediate/Beginner — тот же экзоскелет, что в create-architecture.
  - `step-03-complete.md` (278) — финальная оптимизация «for LLM context efficiency»: удаление избыточного, плотность.
- **Почему важен для контура фазы:** это **producer** того самого project-context.md, который create-architecture потребляет в step-03 «секция 0» и step-04 «Already Decided», и который Winston грузит при активации. Без него контур «предпочтения проекта сильнее рекомендаций» неполон. По сути это BMAD-аналог CLAUDE.md: дистиллят правил проекта для холодного LLM-контекста.

> ⚠️ **Дрейф:** в шаблоне `project-context-template.md` битый плейсхолдер `{ { number_of_patterns_discovered } }` (пробелы внутри скобок — Prettier-артефакт, тот же класс порчи разметки, что в step-05/06 create-architecture: видно, что весь vendored-пакет прогнали через форматер, который ест {{}}, `__`, `*` и вложенные code-fence).

---

### 2.5 Сквозные находки фазы (вне отдельных компонентов)

1. **Экзоскелет workflow.md — тиражируемый каркас**: блоки «WORKFLOW ARCHITECTURE / Critical Rules (NO EXCEPTIONS) / INITIALIZATION» и даже опечатка «adhere too» дословно повторяются в epics-and-stories И check-implementation-readiness — это шаблон уровня фазы (а то и всего BMM), не находка одного скилла.
2. **Общесистемный config-drift**: все workflow.md требуют резолвить `user_name` и `document_output_language` — обоих нет в установленном config v6.2.2 (есть только дефолты в bmad-init `resources/core-module.yaml`: 'BMad'/'English'). При запуске скилла в обход bmad-init переменные неразрешимы.
3. **Единственный явный producer↔consumer контракт фазы**: секция `### FR Coverage Map` в epics-template.md — пишет epics step-02 (строки 96, 126-131), читает readiness step-03 («Look for sections like "FR Coverage Map"»). Контракт держится на **совпадении строки заголовка**: переименуешь секцию — трассировка молча деградирует.
4. **Реальные значения конфига этой инсталляции**: `communication_language: Russian`, `user_skill_level: intermediate`, `planning_artifacts: {project-root}/_bmad/planning-artifacts`, `output_folder: _bmad` — весь диалог фазы идёт по-русски, подача Intermediate, артефакты в `_bmad/planning-artifacts/`.
5. **Зависимости от `_bmad/core/`** — 4 core-скилла: `bmad-init` (активация Winston), `bmad-advanced-elicitation` + `bmad-party-mode` (все [A]/[P] ветки), `bmad-help` (финал всех трёх workflow). Фаза не самодостаточна без core.

---

## 3. Карта детерминизма фазы

Ключевой факт: **во всей фазе НЕТ НИ ОДНОГО машинного гейта** — ни скрипта, ни exit-кода, ни схемы валидации. ~5000+ строк markdown исполняет LLM. Всё «детерминированное» — это текстовые контракты + человек.

| Решение | Кто решает | Механизм | Надёжность |
|---|---|---|---|
| Вход в фазу (есть ли PRD) | **Человек-гейт** | step-01 HALT: «Do NOT proceed without PRD» | Высокая (явный стоп) |
| Какие документы грузить | Человек подтверждает находки LLM | confirm перед загрузкой + inputDocuments[] | Средняя |
| Тип проекта / домен / режим workflow | LLM свободно | Задумывались CSV-таблицы — **орфаны**, режимы standard/enhanced/advanced не существуют | Низкая (мёртвая ручка) |
| Версии технологий | **Web search** (промпт-форсированный) | «NEVER trust hardcoded versions» + FAILURE MODES | Средняя (enforcement только промптом) |
| Выбор стартера и архитектурные решения | **Человек** выбирает, LLM фасилитирует опции | A/P/C меню, «Already Decided» реестр, запись с рационалом | Средняя-высокая |
| Запись контента в документ | Человек-гейт | «ONLY save when user chooses C»; append-only | Средняя (дисциплина LLM) |
| Переход между шагами | LLM-дисциплина | prompt-гейты «NEVER proceed…», «FORBIDDEN to load next step» | Вероятностная |
| Состояние / resume | LLM пишет и читает | frontmatter (stepsCompleted, lastStep, inputDocuments) — машинно-читаемо, но без верификатора | Мягкий контракт |
| Валидация архитектуры (step-07) | LLM **self-review** | предзаполненный [x]-чеклист + захардкоженный READY | 🔴 **Анти-гейт** (смещает к успеху) |
| Структура эпиков | LLM предлагает → человек approve | explicit sign-off, «Repeat until approval» | Средняя-высокая |
| Текст story и AC | LLM генерит → человек per-story | 3 контрольных вопроса до append | Средняя-высокая (самый плотный гейт фазы) |
| Покрытие FR→stories | LLM-чеклист дважды | epics step-04 self-check + readiness step-03 трассировка (второй промпт) | Средняя (двойной проход, но оба LLM) |
| Exit фазы (готовность к Phase 4) | LLM-валидатор + человек читает отчёт | readiness: адверсариальная роль, пустой шаблон, auto-proceed без A/P/C, вердикт READY/NEEDS WORK/NOT READY | Средняя (честнее step-07, но self-grading класс тот же) |
| Маршрутизация между скиллами | **Данные** | module-help.csv через bmad-help — единственный живой data-механизм фазы | Высокая (для LLM-исполнителя) |

**Как сместился ползунок доверия vs Phase 1:**

- Phase 1: optional-фаза, дисциплина = HALT-гейты + FORBIDDEN-роли + JSON-контракты субагентов; продукт — документы для человека; цена ошибки — кривой brief.
- Phase 3: цена ошибки выросла на порядок — **выходы фазы машинно парсятся Phase 4** (регэкспы по «### Story N.M», чтение architecture.md dev-агентами). Ответ автора — не машинные гейты (их по-прежнему ноль), а: (1) **плотнее человеческие гейты** (per-story микро-approve вместо approve документа целиком); (2) **жёстче контракты формы** (шаблоны с literal-скелетами, нумерационный контракт N.M, template compliance); (3) **второй LLM-валидатор** с независимым промптом (readiness) — впервые в пайплайне появляется идея «проверяющий ≠ автор», пусть и в слабой форме; (4) **data-driven маршрутизация** (module-help.csv). Парадокс фазы: доверие к LLM в смыслах выросло (он генерит архитектуру и stories почти целиком), доверие в форме упало (всё зажато шаблонами). Человек сместился из соавтора (Phase 1) в **приёмщика контрактов**.

---

## 4. Соответствие канону

«Названо» ≠ «атрибуция»: в большинстве случаев BMAD использует *форму* метода, не называя ни метод, ни автора.

| Канон | Назван в файлах? | Что взято | Что выкинуто | Что переосмыслено под solo+LLM |
|---|---|---|---|---|
| **User Story (Connextra)** | Формат — дословно; имя школы — нет | «As a / I want / So that» literally в шаблоне | — | Формат сохранён именно потому, что он одновременно человекочитаем и **парсим регэкспами** downstream-конвейера |
| **BDD / Gherkin** | Ключевые слова — да; «BDD»/«Gherkin» — нет | Given/When/Then в каждом AC | Автоматизация (Cucumber), living documentation, связь с тестами | AC = исчерпывающая спецификация ДО разработки, потому что dev-агент не сможет «переспросить»; And зашит обязательным (дрейф) |
| **INVEST** | Нет (grep) | I (ослаблен), V, S, T | **N и E выброшены целиком** | N: агент не торгуется — story после approval = контракт. E: оценка заменена бинарным «влезает в одну сессию dev-агента». I: только backward-зависимости → линейный конвейер |
| **3C (Card-Conversation-Confirmation)** | Нет | Все три С структурно | Живой разговор как непрерывная практика | Conversation сжат в per-story микро-ревью с 3 вопросами |
| **Scrum (роли)** | **Да** — Product Owner, Scrum Master (единственные именованные роли фазы) | Роли как персоны-промпты | Спринты, церемонии, инкременты, велосити | Роль = установка тона LLM («partnership, not client-vendor»), не процесс |
| **Definition of Ready** | Нет (grep) | Весь readiness-скилл + step-04 epics = DoR по духу | Командное соглашение о DoR | Проверяется «сможет ли автономный агент исполнить без блокировок», не «поймёт ли разработчик» |
| **Stage-Gate (Cooper)** | Нет | Entry-гейты (PRD HALT), exit-гейт (readiness report) | Гейт-комитеты, бизнес-критерии go/kill | Гейт = HALT-меню + LLM-отчёт; решение go всегда за одним человеком |
| **ADR (Nygard)** | Нет | Поля Decision/Rationale/Affects/Version + провенанс «Provided by Starter» | Отдельные immutable файлы, supersede-цепочки, journaling | Решения — секции одного living-документа; история изменений решений не предусмотрена вовсе |
| **ATAM (SEI)** | Нет | Каскадный анализ последствий, severity triage находок | Quality attribute scenarios, utility tree, sensitivity points, внешние стейкхолдеры | Trade-off анализ → «цепочки зависимых решений»; валидация → чеклист когерентности |
| **C4 / 4+1 / arc42** | Нет | **Ничего** — диаграмм нет в принципе | Все view-модели и шаблоны секций | Единственное представление = полное ASCII-дерево файлов: «LLM-агент потребляет дерево лучше диаграммы»; шаблон документа намеренно пустой (анти-arc42) |
| **ISO/IEC 25010 / FURPS+** | Нет | Идея quality attributes (NFR названы) | Таксономия из 8 характеристик | Самодельный список «Complexity Indicators» |
| **MoSCoW** | Нет | Трёхуровневая приоритизация решений | W (Won't), четырёхуровневость | Critical/Important/Deferred-Post-MVP — защита от paralysis-by-analysis |
| **XP Coding Standards / CoC** | Нет | Конвенции до кодирования, единый стиль | Командная договорённость как процесс | Расширено с кода на API/БД/события; аудитория — флот агентов без общей памяти; **вариационный анализ «что агенты решат по-разному»** — собственный вклад BMAD |
| **YAGNI** | Нет | «tables ONLY when needed», анти-BDUF | — | Страховка от любимой ошибки LLM: сгенерировать все 50 таблиц в первой story |
| **Walking Skeleton (Cockburn)** | Нет | «Project initialization… first implementation story» | — | Первая story конвейера предопределена архитектурной фазой |
| **12-factor** | Нет | Только factor III (config в env) как dotenv-конвенция в деревьях | Остальные 11 факторов | Присутствует неосознанно, примером |
| **Vertical slicing (Wake/Cohn)** | Нет («vertical» отсутствует, grep) | Запрет послойных эпиков с поимёнными анти-примерами | Термин и теория | Аргументация через конвейер: горизонтальный слой = эпик без отгружаемой ценности = блокировка агентов |
| **RTM / IEEE 830** | «Requirements traceability» названо в readiness workflow.md | Нумерация FR/NFR/UX-DR, FR Coverage Map, обратная трассировка | Матрица как таблица тестов | Трассировка живёт строкой-заголовком в markdown (хрупкий контракт); NFR из трассировки выпали (дыра) |
| **PRD** | **Да**, дословно | Как обязательный входной артефакт | — | — |
| **PCI DSS / HIPAA / BACnet / SCADA** | **Да** (в орфанном CSV) | Готовые compliance-запросы per domain | — | Мертво: CSV никто не читает |

---

## 5. Карта настройки фазы

| Ручка | Файл | Что меняет | Статус |
|---|---|---|---|
| Язык, skill level, пути | `_bmad/bmm/config.yaml` | communication_language (все реплики), user_skill_level (скрипты Expert/Intermediate/Beginner в steps 03-04 CA и step-02 GPC), planning_artifacts/output_folder | ✅ Живая; но `user_name`/`document_output_language` — битые (нет в config) |
| Правила своего проекта | `project-context.md` (producer — bmad-generate-project-context) | Инжектирует предпочтения: CA step-03 «секция 0», step-04 «Already Decided», активация Winston | ✅ Живая, главный канал «корпоративного стека» без правки скиллов |
| Формат story/AC всего конвейера | `bmad-create-epics-and-stories/templates/epics-template.md` | Connextra-строки, GWT-строки, состав секций инвентаря, frontmatter-контракт | ✅ Живая, single source формата; осторожно: на «### Story N.M» и «### FR Coverage Map» завязан downstream |
| Сид архитектурного документа | `architecture-decision-template.md` | Стартовые поля frontmatter (стоит добавить `lastStep`!) | ✅ Живая |
| Категории решений | `step-04-decisions.md` секция 2 | Добавить Observability / Compliance / ML-категории | ✅ Живая |
| Каталог точек конфликта агентов | `step-05-patterns.md` | Добавить i18n, миграции, feature flags; вписать машинные гейты в Enforcement Guidelines | ✅ Живая |
| Эталонные деревья | `step-06-structure.md` секция 4 | Заменить Next.js/NestJS на свой стек | ✅ Живая |
| Состав валидаций | `step-07-validation.md` | **Критично: заменить предзаполненный [x]-чеклист на пустой [ ]** и выкинуть хардкод READY | 🔴 Требует фикса, не просто настройки |
| Типы проектов, стартеры | `data/project-types.csv` | Ничего — **орфан** | 🔴 Мертва; чтобы ожила — добавить ссылку на чтение CSV в step-03 |
| Домены, web-запросы, режимы | `data/domain-complexity.csv` | Ничего — **орфан**, режимы не реализованы | 🔴 Мертва |
| Граф переходов фаз | `_bmad/bmm/module-help.csv` | Порядок скиллов, рекомендации bmad-help | ✅ Живая, единственный работающий data-механизм |
| Протоколы углубления | вызовы `bmad-advanced-elicitation` / `bmad-party-mode` / `bmad-help` (`_bmad/core/`) | Заменяемы на свои review/elicitation-скиллы во всех A/P/C точках (12+ вызовов) | ✅ Живая |
| Меню и принципы персоны | `bmad-agent-architect/SKILL.md` | Capabilities (добавить CE/GPC), принципы Winston | ✅ Живая |
| Триггеры активации | frontmatter каждого SKILL.md | Фразы распознавания | ✅ Живая |

**Что переживает обновление BMAD:** по условию, `files-manifest.csv` хеширует vendored-файлы (содержимое самой инсталляции `_bmad/bmm/`, `_bmad/core/`) — раскопки сам механизм апдейта не проверяли, поэтому здесь осторожно:

- **Безопасно (вне vendored-дерева или явно пользовательское):** `config.yaml` (это и есть задуманная панель настройки), `project-context.md`, выходные артефакты (`architecture.md`, `epics.md`, отчёты) — живут в `planning_artifacts`/`output_folder`.
- **Рискованно (vendored, захеширован):** любые правки `steps/*.md`, `templates/*.md`, `data/*.csv`, `workflow.md`, `SKILL.md` — при обновлении версия либо пометится как модифицированная, либо конфликтнёт/перетрётся. Вывод для своего пайплайна — тот же overlay-принцип, что мы уже приняли для bmad-orchestrator: **не форкать vendored-контент, держать свои правки слоем поверх** (свои скиллы-обёртки, свой config, свой project-context), а внутрь vendored лезть только для фиксов класса «предзаполненный чеклист», осознавая, что фикс придётся повторять после апдейта.

---

## 6. Уроки для своего пайплайна

Семь приёмов, переносимых именно из этой фазы (часть — «делай так», часть — «не повторяй»):

1. **Вариационный анализ для мульти-агентности (step-05) — забирать как есть.** Перед запуском параллельных агентов спросить: «в каких точках два независимых LLM примут РАЗНЫЕ решения?» (имена таблиц, формат ошибок, раскладка файлов, naming событий) — и зафиксировать выбор ДО имплементации. Это лучший один абзац всей фазы, и он напрямую относится к нашему orchestrator'у с параллельными worker'ами: список конфликт-точек = кандидат на машинный pre-merge чек.

2. **Полное дерево файлов как исполняемая спецификация (step-06).** Для LLM-потребителя ASCII-дерево «до последнего файла» работает лучше любой диаграммы: убирает свободу размещения кода — главный источник merge-конфликтов параллельных stories. C4/4+1 — для людей; дерево + маппинг «эпик → директория» — для агентов.

3. **Backward-only dependencies + сайзинг «одна сессия агента» (step-03 epics).** Замена story points на «влезает в контекст одной автономной сессии» и запрет только forward-зависимостей дают **линейно исполняемый, DAG-friendly бэклог** — ровно тот формат, который наш DAG-planner может валидировать кодом, а не глазами. Урок: режь работу под исполнителя, а не под традицию оценки.

4. **Никогда не предзаполняй вердикт валидатора (анти-урок step-07 vs урок readiness).** Предзаполненный `[x]`-чеклист + захардкоженный «READY FOR IMPLEMENTATION» = валидационный театр: LLM достроит реальность под заготовленный успех. Контрпример в той же фазе: readiness-гейт с пустым шаблоном, адверсариальной ролью («your success is measured in spotting failures») и плейсхолдером вердикта. Правило: шаблон отчёта проверяющего не должен содержать ни одного заранее проставленного результата, а проверяющий должен жить в **отдельном промпте/контексте** от автора.

5. **Данные-ручка мертва, если её никто не читает — нужен contract-test на каждый стык (анти-урок data/*.csv и FR Coverage Map).** Два тщательно сделанных CSV — орфаны: ни один промпт их не открывает, справочник продублирован хардкодом, ось standard/enhanced/advanced не имеет исполнителя. А единственный живой контракт фазы (секция «FR Coverage Map»: пишет один скилл, читает другой) держится на совпадении строки заголовка. Это в точности наш класс single-source drift: каждый producer↔consumer стык обязан иметь проверку «писатель реально пишет то, что читатель реально читает» — у BMAD её нет, у нас она должна быть кодом.

6. **Анти-компрессия как явный гейт (step-01 epics, step-02 readiness).** «If the UX spec identifies 6 reusable components, list all 6» и «FORBIDDEN to skip or summarize PRD content» — лечат конкретный, воспроизводимый сбой LLM: пересказ теряет специфику. Переносится дословно в любой наш шаг, где LLM транслирует требования между артефактами: запрещай суммаризацию, требуй поимённый перенос с нумерованными ID (FR1/NFR1/UX-DR1) — ID потом дают дешёвую машинную трассировку grep'ом.

7. **Реестр «Already Decided» + провенанс решений (step-04).** Три источника уже принятых решений (стартер, предпочтения пользователя, project-context) исключаются из повторного обсуждения, а каждое решение несёт метку «кто решил». Экономит токены и внимание человека, а провенанс позволяет потом отличить «так решил стартер» от «так решил владелец» — полезно при любом будущем пересмотре. Для нашего пайплайна: аналог — слой resolved-настроек с пометкой источника (config / policy / human override), который агенты обязаны читать перед тем, как «решать заново».

**Сквозная мораль фазы:** автор BMAD построил впечатляющую промпт-инженерную дисциплину (микрофайлы, экзоскелеты, A/P/C, frontmatter-state, контрастные few-shot) — но **весь enforcement живёт в вероятности следования инструкции**. Каждое место, где фаза говорит «MUST» без механизма (Enforcement Guidelines без линтера, чеклист без верификатора, контракт-заголовок без теста), — это готовый список того, что в своём пайплайне надо закрывать кодом. Что мы, собственно, и делаем.