# Phase 2 — Planning: подробный разбор

> Контекст из разбора Phase 1: там вся фаза была optional, а «дисциплина» держалась на HALT-гейтах, FORBIDDEN-ролях, state в frontmatter, JSON-контрактах субагентов и анти-галлюцинации через сорсинг. Phase 2 — первое место, где у метода появляется **обязательный** воркфлоу (PRD), а вместе с ним — первый автономный LLM-конвейер проверки качества (валидатор без человека внутри). Скриптов и кода по-прежнему ноль: всё исполняет LLM, читающий markdown.

**Честность об источниках.** Раскопки детально покрыли 2 каталога из 7 (`bmad-create-prd` + легаси `create-prd`) и валидатор `bmad-validate-prd` (крупицы V-0…V-11; исходный JSON обрывается посреди V-11, шаги V-12+ в моих данных отсутствуют). Агент-персоны, `bmad-create-ux-design`, `bmad-edit-prd` и `module-help.csv` известны только из дополнений критика — по ним разбор заведомо мельче, я это помечаю. Важно: **фаза в этой инсталляции ни разу не запускалась** (`planning-artifacts/` содержит только config.yaml) — всё ниже описывает инструмент «на полке», runtime-поведение (включая найденные дыры маршрутизации) вживую не проверялось.

---

## 1. Карта фазы

Фаза живёт в `/home/server/Downloads/crm/_bmad/bmm/2-plan-workflows/`. Машинный реестр зависимостей — `_bmad/bmm/module-help.csv` (строки 16-19); это единственное место, где порядок и обязательность заданы данными, а не прозой.

| Компонент | Тип | Статус (module-help.csv) | Порядок (after) | Вход | Выход |
|---|---|---|---|---|---|
| `bmad-agent-pm` («John», PM) | агент-персона | точка входа (слой 2) | — | меню capabilities | роутинг: CP→create-prd, VP→validate, EP→edit, CE/IR→**фаза 3**, CC→correct-course |
| `bmad-agent-ux-designer` («Sally», UX) | агент-персона | точка входа (слой 2) | — | меню | CU→create-ux-design |
| `bmad-create-prd` | workflow, 13 шагов + 1 continuation | **required=true — единственный обязательный в фазе** | — | `*brief*.md` (Phase 1 product brief), research/brainstorming docs | `{planning_artifacts}/prd.md` |
| `bmad-validate-prd` | workflow, 13 автономных шагов-рубрик | optional | after=`bmad-create-prd` | prd.md + его frontmatter `inputDocuments` | validation-report рядом с PRD |
| `bmad-edit-prd` | workflow, 5 шагов | optional | after=`bmad-validate-prd` | prd.md + validation report (как гайд) | правленый prd.md |
| `bmad-create-ux-design` | workflow, 14 шагов | optional | after=`bmad-create-prd` | prd.md | `ux-design-specification.md` + **2 интерактивных HTML** |
| `create-prd/` (без префикса) | легаси-каталог | не зарегистрирован (нет SKILL.md) | — | — | мёртвый дубль валидатора, можно сносить |

**Связи между фазами.** Вход фазы — артефакты Phase 1 (`1-analysis/bmad-product-brief` → `*brief*.md`, плюс research). Выход — prd.md (+ ux-spec) → Phase 3: `bmad-create-architecture` (required) → epics/stories → `bmad-check-implementation-readiness`. Заметная асимметрия: агент-PM перешагивает границу фазы — его пункты меню CE (create-epics-and-stories) и IR (check-implementation-readiness) принадлежат фазе 3, а CC (correct-course) объявлен «anytime».

**Два слоя входа.** Воркфлоу можно вызывать напрямую как скиллы (`/bmad-create-prd`) или через персону John/Sally с меню. Персона «must carry through»: workflow.md прямо говорит «merged with the details of this role description» — воркфлоу наследует имя/identity/стиль вызвавшего агента. Раскопки описывали только прямой слой; персонный слой — из дополнений критика.

**Внешние зависимости фазы** (живут в `_bmad/core/`, вне фазы): `bmad-init` (загрузка config для агентов), `bmad-help` (роутер «что дальше»), `bmad-advanced-elicitation` и `bmad-party-mode` — весь фирменный A/P-механизм каждого шага фактически импортируется из core.

**Асимметрия инструментария.** PRD получил полную тройку create+validate+edit; UX — только create, без валидатора, без edit и без data/ CSV-справочников. Качество ux-spec никто машинно (даже псевдо-машинно) не проверяет.

---

## 2. Покрупичный разбор компонентов

Формат каждой крупицы: файл → что названо в самом файле → что узнаваемо без имени (атрибуция и уверенность) → собственные изобретения BMAD → замысел автора → как настроить. Дрейфы — врезками.

### 2.1 `bmad-create-prd` — обязательный стержень фазы

#### Крупица: манифест скилла
- **Файл:** `bmad-create-prd/SKILL.md`
- **Названо в файле:** ничего.
- **Узнаваемо без имени:** формат Claude Code Agent Skills (frontmatter name + trigger-description, Anthropic 2024-2025) — high confidence.
- **Изобретение BMAD:** тонкий шим — SKILL.md содержит только триггер и одну строку «Follow the instructions in ./workflow.md», вся логика снаружи. Один контент пригоден для разных harness'ов.
- **Замысел автора:** отделить точку регистрации от содержимого, чтобы инсталлятор мог переименовывать/переносить воркфлоу без правки логики.
- **Настроить под себя:** триггер-фразы вызова — правка description во frontmatter.

#### Крупица: workflow.md — «конституция исполнения»
- **Файл:** `bmad-create-prd/workflow.md`
- **Названо в файле:** ничего по имени.
- **Узнаваемо без имени:**
  - конечный автомат с чекпоинтами («stepsCompleted array», «SAVE STATE before loading next step») — BPM/workflow-engines, event-sourcing-подобный лог — high;
  - Just-In-Time loading контекста («Only the current step file is in memory») — JIT из Toyota Production System, перенесённый на контекст-окно LLM; в LLM-инженерии progressive disclosure — high;
  - role prompting («Product-focused PM facilitator collaborating with an expert peer») — prompt engineering 2022+ — high;
  - i18n-разделение языка общения и языка артефактов — локализационная практика — medium.
- **Изобретение BMAD:** step-file architecture (каждый шаг — самодостаточный микрофайл); Append-Only Building (документ растёт только дописыванием до step-11); блок «Critical Rules (NO EXCEPTIONS)» с эмодзи-маркерами — анти-оптимизационный намордник («NEVER skip steps or optimize the sequence», «NEVER create mental todo lists from future steps»); stepsCompleted как машинный контракт прогресса в YAML frontmatter самого выходного документа.
- **Замысел автора:** борьба с двумя свойствами LLM — забыванием (длинный контекст → микрофайлы+JIT) и самодеятельностью (срезание пути → запреты смотреть вперёд). Документ-как-state-machine переживает обрывы сессии.
- **Настроить под себя:** `outputFile` во frontmatter; языки/имя/skill level/папки — в `_bmad/bmm/config.yaml`.

> **Дрейф (подтверждён и усилен критиком):** workflow.md требует резолвить `document_output_language` и `user_name` из config — в реальном `config.yaml` (14 строк) этих полей **нет**. Причём их требуют **все четыре** workflow.md фазы (create:35-36, edit:50-55, validate:51-56, ux:24-25), а `{{user_name}}` стоит в обоих шаблонах документов и в greeting обоих агентов. Системный разъезд инсталлятор↔контент.

#### Крупица: prd-purpose.md — доктрина качества
- **Файл:** `bmad-create-prd/data/prd-purpose.md` (читается step-11 и валидатором)
- **Названо в файле:** **SMART Quality Criteria** (Specific/Measurable/Attainable/Relevant/Traceable — это не Doran 1981, а RE-вариант Mannion & Keepence 1995 с заменой Realisable→Relevant); регуляторика по имени: HIPAA, PCI-DSS, SOX, AML/KYC, NIST, Section 508, WCAG 2.1 AA, FedRAMP.
- **Узнаваемо без имени:**
  - traceability chain «Vision → Success Criteria → User Journeys → FRs → (future: User Stories)» — ISO/IEC/IEEE 29148, Gotel & Finkelstein 1994 — high;
  - разделение FR/NFR и запрет implementation leakage («Bad FR: 'System sends JWT via email…'») — IEEE 830 / ISO 29148, what-не-how — high;
  - шаблон NFR «metric + condition + measurement method» — Gilb Planguage / fit criterion Volere — medium;
  - plain-language редактура («'In order to...' → 'To...'», «Maximum information per word. Zero fluff») — Strunk & White + plain language movement — medium.
- **Изобретение BMAD:** dual-audience doctrine — PRD одновременно для людей и для «LLM Downstream Consumption»; «Level 2 headers for all main sections (enables extraction)» — структура документа как API для машинного парсинга; каталог анти-паттернов парами «плохо/хорошо» как few-shot примеры; auto-detect доменных требований через CSV.
- **Замысел автора:** конституция качества. PRD — вершина воронки, питающая всех ИИ-агентов ниже, поэтому плотность и измеримость — машинное требование, не стиль. Текст явно написан как материал для LLM-судьи.
- **Настроить под себя:** правка самого markdown — step-11 и валидатор читают его как источник истины. Но см. врезку про 3 копии в §2.6.

> **Дрейфы:** опечатка «in rhw BMad Method» (`prd-purpose.md:3`); список из 9 Required Sections (`:130-139`) не содержит «Project Classification», хотя step-02c реально аппендит её как Level-2 секцию.

#### Крупица: domain-complexity.csv — справочник доменной сложности
- **Файл:** `bmad-create-prd/data/domain-complexity.csv`
- **Названо в файле:** DO-178C, ISO 26262, IEC 62443, ISA-95, NERC CIP, COPPA, FERPA, ITAR, ASHRAE, BACnet и др. — отраслевые стандарты по строкам доменов.
- **Узнаваемо без имени:** decision table / signal-based classification (колонки domain, signals, complexity, key_concerns, required_knowledge, suggested_workflow, web_searches, special_sections; 13 доменов + `general` default + `gaming` с complexity=redirect) — структурный анализ 1960-х, экспертные системы — high; compliance matrix / regulatory mapping — GRC-практика — high.
- **Изобретение BMAD:** **данные-как-промпт** — колонка web_searches содержит готовые поисковые запросы с плейсхолдером `{date}` («FDA software medical device guidance {date}»): CSV управляет и разговором, и веб-ресёрчем; маршрутизация между воркфлоу зашита в данные (gaming → «REDIRECT TO GAME WORKFLOWS»).
- **Замысел автора:** вынести доменную экспертизу из промпта в данные — новая отрасль = одна строка CSV, а не правка 13 step-файлов; одновременно фиксация знания против галлюцинаций регуляторики.
- **Настроить под себя:** добавить строку — домен с сигналами, комплаенсом, поисками.

#### Крупица: project-types.csv — справочник типов проекта
- **Файл:** `bmad-create-prd/data/project-types.csv`
- **Названо в файле:** OpenAPI, WCAG, PCI (через web_search_triggers и вопросы).
- **Узнаваемо без имени:** situational method engineering / process tailoring (required_sections/skip_sections per тип: api_backend скипает «ux_ui;visual_design;user_journeys», cli_tool — «visual_design;ux_principles;touch_interactions») — Brinkkemper 1996, tailoring в ISO 12207 — medium; вопросные чеклисты per-тип («Endpoints needed?;Authentication method?;…») — checklist-driven discovery — medium.
- **Изобретение BMAD:** колонка innovation_signals — лексические маркеры новизны per тип, питают опциональный step-06; HALT зашит в данные (строка game: «REDIRECT TO USE THE BMad Method Game Module Agent and Workflows - HALT»).
- **Замысел автора:** одна таблица вместо 10 специализированных PRD-шаблонов — единый процесс с матрицей включения/выключения секций.
- **Настроить под себя:** добавить строку — step-07 автоматически превратит её в интервью и секции документа.

#### Крупица: prd-template.md — seed-шаблон
- **Файл:** `bmad-create-prd/templates/prd-template.md`
- **Названо в файле:** ничего.
- **Узнаваемо без имени:** минимальный seed вместо полного скелета — осознанный контраст с классической традицией «заполни PRD-шаблон» (Microsoft/Google templates): документ выращивается диалогом — high.
- **Изобретение BMAD:** frontmatter как единственная «схема» документа — state (stepsCompleted, inputDocuments, workflowType) живёт в самом артефакте; артефакт переносим вместе со своим прогрессом.
- **Замысел автора:** пустой шаблон принуждает к append-only потоку — LLM физически нечего «заполнить всё сразу».
- **Настроить под себя:** титул/брендинг; добавление пустых секций сломает append-only логику.

> **Дрейф:** Required Sections из доктрины в шаблоне не предзаложены — целиком на дисциплине шагов; отклонение шага = отклонение документа без машинной проверки на этапе создания.

#### Крупица: step-01 — инициализация и discovery входных документов
- **Файл:** `steps-c/step-01-init.md`
- **Названо в файле:** ничего.
- **Узнаваемо без имени:** greenfield/brownfield классификация — строительная метафора, в софте с 2000-х — high; resume/checkpoint detection при старте (идемпотентный вход — Airflow/Temporal-паттерн) — high; document discovery по glob-конвенциям + sharded-документы (`*foo*/index.md`) — convention over configuration (Rails) — medium.
- **Изобретение BMAD:** `inputDocuments` + `documentCounts` во frontmatter — машинный реестр загруженного контекста для восстановления сессии; human-confirmation gate на входные данные («Only after this confirmation will you proceed»); блоки SYSTEM SUCCESS/FAILURE METRICS + Master Rule («Skipping steps… constitutes SYSTEM FAILURE») — поведенческая рубрика-самоконтракт в каждом step-файле; меню из одного пункта [C] — HALT обязателен даже там, где выбора нет.
- **Замысел автора:** контекстная гигиена — что загружено, то зафиксировано в frontmatter, чтобы другой LLM-инстанс восстановил ровно тот же контекст. FORBIDDEN-правила бьют по главной болезни LLM — желанию сразу генерить.
- **Настроить под себя:** глобы и папки поиска ({planning_artifacts}, {project_knowledge}, docs/**) в секции 3A.

> **Дрейфы (оба подтверждены критиком построчно):** «Progress: Step 1 of 11» — устаревший счётчик (шагов 13). **Дыра маршрутизации:** continuation срабатывает только если step-12-complete НЕ в stepsCompleted (`:61`); fresh — только если stepsCompleted нет (`:72`). Документ с **завершённым** workflow не попадает ни в одну ветку → обработчик завершения в step-01b §4 недостижим из step-01. Вживую не проверялось (фаза не запускалась).

#### Крупица: step-01b — continuation
- **Файл:** `steps-c/step-01b-continue.md`
- **Названо в файле:** ничего.
- **Узнаваемо без имени:** checkpoint/restart с полным восстановлением контекста («reload what was previously processed», запрет нового discovery) — crash-recovery, write-ahead log → replay — high; маршрутизация по детерминированной lookup-таблице на 13 строк («Get the last element… Look it up… That's the next step!») — transition table конечного автомата — high.
- **Изобретение BMAD:** «FORBIDDEN to discover new input documents during continuation» — защита от дрейфа контекста между сессиями; «FORBIDDEN to modify content completed in previous steps» — append-only инвариант даже при resume.
- **Замысел автора:** единственное место, где переходы заданы машинно-проверяемой таблицей, а не прозой — автор понимает, что resume самая хрупкая точка, и снимает с LLM свободу выбора следующего шага.
- **Настроить под себя:** порядок шагов — правка lookup-таблицы (вместе с COMPLETION NOTE каждого шага — двойная бухгалтерия).

> **Дрейфы:** пример stepsCompleted в §3 (`:90`) — [step-01-init, step-02-discovery, step-03-success] — невозможное состояние (пропущены обязательные 02b/02c), остаток старой 11-шаговой версии. Переходы дублируются в двух местах (таблица здесь + «read fully and follow» в конце каждого шага) — single-source drift риск.

#### Крупица: step-02 — discovery и классификация
- **Файл:** `steps-c/step-02-discovery.md`
- **Названо в файле:** ничего.
- **Узнаваемо без имени:** elicitation через открытые вопросы («What problem does this solve? Who's it for?») — RE-интервью, BABOK — high; классификация по лексическим сигналам с подтверждением человеком — HITL-классификация / member checking — medium; делегирование lookup'а Task-субагенту ради экономии контекста («Do NOT return the entire CSV - only the matching row») + graceful degradation на прямое чтение — Anthropic multi-agent patterns + fault-tolerant design — high.
- **Изобретение BMAD:** **A/P/C меню** — фирменный тройной гейт: [A] Advanced Elicitation, [P] Party Mode, [C] Continue, после A/P обязательный возврат в меню; classification (projectType/domain/complexity/projectContext) сохраняется YAML-блоком в frontmatter — контракт для шагов 5/6/7; промпт субагента зашит прямо в step-файл (инструкция в инструкции).
- **Замысел автора:** классификация — маршрутизатор всего остального воркфлоу; ошибка каскадирует на 11 шагов вперёд, поэтому «понять и классифицировать» отделено от «генерить» и требует подтверждения юзером.
- **Настроить под себя:** плагины A/P (скиллы в `_bmad/core/`), набор типов/доменов — строки CSV.

> **Дрейфы (4):** промпт↔данные — lookup просит вернуть «domain, complexity, typical_concerns, compliance_requirements», а в domain-complexity.csv колонок typical_concerns/compliance_requirements **нет** (реальные: key_concerns, required_knowledge, …) — подтверждено `step-02-discovery.md:102`; chicken-and-egg — §2 ищет строку по `{{detectedProjectType}}` ДО §3, где этот тип только определяется; счётчик «Step 2 of 13» против «of 11» в соседних файлах; меню пронумеровано как «### N.» вместо номера.

#### Крупица: step-02b — vision discovery
- **Файл:** `steps-c/step-02b-vision.md`
- **Названо в файле:** ничего.
- **Узнаваемо без имени:** value proposition / positioning интервью («one sentence to explain why someone should use this over anything else») — Ries & Trout 1981, Osterwalder 2014, Dunford 2019 — medium; углубление от симптома к корневой потребности — JTBD/5 Whys по духу, не названы — medium; «Why now» — Sequoia pitch-deck канон — low; active listening / reflective validation («Here's what I'm hearing… Does this capture it?») — Rogers/Farson — medium.
- **Изобретение BMAD:** жёсткое разделение фаз elicit/generate — «FORBIDDEN to append anything to the document in this step», шаг чисто разговорный; вместо именованного фреймворка — собственный квартет: User delight / Differentiation moment / Core insight / Value proposition.
- **Замысел автора:** бывший один шаг разрезан на 2/2b/2c (видно по нумерации) — сначала понять, потом отдельно синтезировать. Анти-галлюцинационная мера: LLM не должен сочинять видение из ничего.
- **Настроить под себя:** вопросные батареи в секциях 2-3 (реплики фасилитатора в кавычках).

> **Дрейф-маркер:** счётчик «Step 2b of 13» корректен — в отличие от «of 11» у соседей: след того, что 02b/02c добавлены позже без ренумерации остальных.

#### Крупица: step-02c — Executive Summary
- **Файл:** `steps-c/step-02c-executive-summary.md`
- **Названо в файле:** ничего.
- **Узнаваемо без имени:** executive summary как секция №1 — PRD-традиция Долины (линия Horowitz «Good PM/Bad PM» 1996; Horowitz не назван) — medium; draft → review → approve перед записью («FORBIDDEN to append content without user approval via 'C'») — editorial workflow / HITL approval — high.
- **Изобретение BMAD:** шаблон аппенда с плейсхолдерами и легендой ({vision_alignment_content} и т.п. с указанием «Drawn from step 2b differentiator discovery») — контракт данных между шагами; explicit «первая запись sets the quality bar».
- **Замысел автора:** первый генеративный шаг отделён от discovery — на руках уже подтверждённые инсайты, остаётся плотная редактура, и она калибрует стиль всего документа.
- **Настроить под себя:** markdown-скелет в блоке APPEND TO DOCUMENT.

> **Дрейф:** аппендит «## Project Classification», которой нет в Required Sections доктрины.

#### Крупица: step-03 — критерии успеха + первичный scope
- **Файл:** `steps-c/step-03-success.md`
- **Названо в файле:** **MVP** (Robinson 2001, популяризировал Ries 2011) — три уровня scope: MVP / Growth / Vision.
- **Узнаваемо без имени:** триада User/Business/Technical success — вместо OKR, KPI или HEART (ни один не назван), по духу balanced scorecard — medium; «aha! moment» как метрика активации — growth hacking культура (Facebook «7 friends in 10 days») — medium; челлендж расплывчатых метрик («'10,000 users' → 'What kind of users? Doing what?'») — Gilb + GQM по духу — medium; горизонты 3/12 месяцев — OKR-каденции — low.
- **Изобретение BMAD:** «Smart Scope Negotiation» — scope через линзу успеха ДО перечисления фич (аналог Three Horizons, не назван); доменная адаптация метрик прямо в шаге (Consumer/B2B/Regulated); принудительные compliance-минимумы в MVP для сложных доменов.
- **Замысел автора:** метрики ПЕРЕД журнеями и фичами — успех определяет требования, а не наоборот (outcome over output); двойной аппенд закладывает раннюю версию scope, которую step-08 детализирует.
- **Настроить под себя:** категории успеха и доменные адаптации в секциях 2-5.

> **Дрейфы:** «Step 3 of 11» (устаревший); файл — **другое поколение шаблона** step-файла (нет STEP GOAL/Role Reinforcement/COMPLETION NOTE; SUCCESS METRICS/FAILURE MODES вместо «сиренных» блоков) — два поколения в одном воркфлоу; перекрытие со step-08 (оба пишут scope-секции, дедуп отложен на step-11).

#### Крупица: step-04 — user journeys
- **Файл:** `steps-c/step-04-journeys.md`
- **Названо в файле:** **user personas** (метод назван; Cooper 1999 не атрибутирован).
- **Узнаваемо без имени:** драматическая арка журнея (Opening Scene → Rising Action → Climax → Resolution) — Freytag 1863, storytelling for UX (Quesenbery & Brooks 2010) — high; journey mapping с эмоциональной кривой — service design (Shostack 1984 → CJM) — high; покрытие акторов за пределами primary (admins, support, API consumers) — use-case driven approach (Jacobson, OOSE 1992) — medium; персона-шаблон Name/Situation/Goal/Obstacle/Solution — гибрид Cooper + сторителлинг-формулы, JTBD-дух без имени — medium.
- **Изобретение BMAD:** аксиома «No journey = no functional requirements = product doesn't exist» — журней объявлен единственным источником FR; секция «Journey Requirements Summary» — машинный мост в step-09; минимальная матрица покрытия (happy path + edge case + Admin/Ops + Support + API consumer).
- **Замысел автора:** нарративные журнеи выбраны вместо user story mapping (Patton отсутствует) и формальных use cases — рассказ с эмоциональной аркой вынуждает увидеть провалы и edge cases, которые сухой список фич скрывает; edge-case журней = генератор негативных требований.
- **Настроить под себя:** секция JOURNEY TYPES TO ENSURE.

> **Дрейф:** «Step 4 of 11», шаблон «старого поколения».

#### Крупица: step-05 — доменные требования (опциональный)
- **Файл:** `steps-c/step-05-domain.md`
- **Названо в файле:** HIPAA, PCI-DSS, GDPR, SOX, ISO, NIST — как примеры в вопросах.
- **Узнаваемо без имени:** risk-based tailoring (скип шага при низкой сложности) — risk-based testing / ISO 12207 tailoring — medium; risk register (риск + митигирование) — PMBOK — high; вопрос о слепых зонах («what typically gets overlooked?») — premortem-дух (Klein) — low.
- **Изобретение BMAD:** условный гейт по данным классификации (frontmatter `classification.complexity` ветвит шаг); меню оверрайда — [C] Skip / [D] Do domain exploration anyway — человек может перебить машинное решение.
- **Замысел автора:** страховка от каскадного пропуска комплаенса («Missing these requirements… creating expensive rework»), но не налог на простые проекты.
- **Настроить под себя:** markdown-скелет §4 (Compliance & Regulatory / Technical Constraints / Integration / Risk Mitigations).

> **Дрейфы:** тот же промпт↔данные разрыв, что в step-02 (запрашиваются несуществующие колонки CSV); перегрузка буквы C — в skip-меню C = «пропустить», в финальном C = «сохранить и продолжить».

#### Крупица: step-06 — innovation discovery (опциональный)
- **Файл:** `steps-c/step-06-innovation.md`
- **Названо в файле:** ничего (термин «innovation theater» использован — это Steve Blank, HBR 2019, автор не назван).
- **Узнаваемо без имени:** анти-«innovation theater» гейт — high; валидация гипотез новизны + fallback («How do we validate it works? What's the fallback?») — Lean Startup / hypothesis-driven development — medium; конкурентный контекст через targeted research по CSV web_search_triggers — market research — medium.
- **Изобретение BMAD:** двухуровневый детектор новизны (общие лексические маркеры + per-тип innovation_signals из CSV); **легитимация отсутствия инноваций** («many successful products are excellent executions of existing concepts») — шаг честно скипается; отдельное меню для случая «инноваций нет».
- **Замысел автора:** защита от двух симметричных провалов LLM — пропустить настоящую новизну (архитектура не заложит валидацию рискованной части) и выдумать новизну ради красивой секции.
- **Настроить под себя:** innovation_signals в project-types.csv.

> **Дрейфы:** «Step 6 of 11»; §1 велит «Load ../data/project-types.csv completely» — противоречит контекст-экономии остальных шагов («Do NOT return the entire CSV»).

#### Крупица: step-07 — project-type deep dive
- **Файл:** `steps-c/step-07-project-type.md`
- **Названо в файле:** ничего.
- **Узнаваемо без имени:** configuration-driven интервью (questionnaire из decision table: «For each question in key_questions from CSV: Ask the user naturally») — Volere-вопросники + situational method engineering — medium; платформенно-специфические маппинги ('tenant_model' → Multi-tenancy approach, 'rbac_matrix' → Permission structure) — доменные чеклисты архитектуры — low.
- **Изобретение BMAD:** «Template Variable Strategy» — известные имена секций CSV маппятся на готовые шаблоны контента, неизвестные валятся в общий project_type_requirements — расширяемость без правки шага; skip_sections экономят время юзера.
- **Замысел автора:** ядро идеи «один воркфлоу — все типы продуктов»: строка CSV превращается в персонализированное интервью, шаговая логика инвариантна.
- **Настроить под себя:** key_questions/required_sections/skip_sections в CSV; маппинги — §4 шага.

> **Дрейф:** «Step 7 of 11».

#### Крупица: step-08 — scoping, MVP и фазовый роадмап
- **Файл:** `steps-c/step-08-scoping.md`
- **Названо в файле:** **lean MVP** («EMPHASIZE lean MVP thinking») и **validated learning** — дословные термины Eric Ries без атрибуции.
- **Узнаваемо без имени:** Must-Have / Nice-to-Have дихотомия — MoSCoW (Clegg 1994, DSDM), упрощён до двух корзин, имя не названо — high; «Can this be manual initially?» — Concierge MVP / Wizard of Oz — medium; riskiest assumption probing — RAT (Higham 2017) / assumption mapping (Bland 2019) — medium; Phase 1 (MVP) / Phase 2 (Growth) / Phase 3 (Vision) — Now/Next/Later (Bastow) / Three Horizons, оба не названы — medium; риски Technical/Market/Resource — классическая таксономия, близко к Cagan но не совпадает — low.
- **Изобретение BMAD:** типология MVP-философий «problem-solving / experience / platform / revenue MVP» — собственная четвёрка, в литературе такого канона нет; scope-creep challenge как скриптованная реплика («Could this wait until after launch?»).
- **Замысел автора:** второй проход по scope — теперь с полным документом на руках: MVP-граница режется по подтверждённым журнеям и доменным требованиям, а не по ощущениям; риски — последний фильтр против переоптимизма.
- **Настроить под себя:** Content Structure §6.

> **Дрейфы (подтверждены построчно):** «Step 8 of 11»; **битый текст §4** — список «Core user value delivery…» висит без заголовка «Phase 1 (MVP)» (`:84-86`), осиротевшая непарная кавычка (`:101`) — следы порезанной редактуры; терминологический дрейф — Phase 3 = «Vision» в §4 (`:82`) но «Expansion» в аппенде (`:95,153`), а step-03 называл уровень «Vision».

#### Крупица: step-09 — синтез функциональных требований
- **Файл:** `steps-c/step-09-functional.md`
- **Названо в файле:** ничего.
- **Узнаваемо без имени:** design-free requirements («WHAT capability exists? NOT HOW it's implemented») — ISO 29148/IEEE 830, Davis 1993 — high; последовательные ID (FR1..FRn) — requirements management, традиция DOORS — high; синтаксис «[Actor] can [capability]» — сжатая user story Connextra (~2001) без so-that; НЕ EARS — medium; группировка по capability areas, не по слоям технологии («'User Management', not 'Authentication System'») — capability mapping / affinity grouping — medium; self-review чеклист Completeness/Altitude/Quality — Fagan inspections / checklist-based reading — medium.
- **Изобретение BMAD:** **«THE CAPABILITY CONTRACT»** — FR-список объявлен юридически-обязывающим интерфейсом: «UX designers will ONLY design what's listed here… If a capability is missing from FRs, it will NOT exist in the final product»; эвристика «5 different ways» (FR хорош, если реализуем пятью способами) — операционализированный тест уровня абстракции для LLM; числовой бюджет «20-50 FRs»; скриптованное предупреждение «This FR list is now binding». Всё подтверждено построчно (`:98, :14/36/211, :125`).
- **Замысел автора:** ключевой шаг цепочки — FR это единственный канал, через который видение доходит до ИИ-разработчиков; контракт сделан «страшным» (binding, SYSTEM FAILURE), потому что downstream-агенты не увидят разговора — только текст FR.
- **Настроить под себя:** §3-4 — бюджеты (5-8 областей, 20-50 FR), синтаксис строки.

> **Дрейф:** «Step 9 of 11»; в §7 осиротевшая строка с непарной кавычкой.

#### Крупица: step-10 — нефункциональные требования
- **Файл:** `steps-c/step-10-nonfunctional.md`
- **Названо в файле:** GDPR, HIPAA, PCI-DSS, WCAG, Section 508 — в вопросах.
- **Узнаваемо без имени:** каталог категорий атрибутов качества (Performance/Security/Scalability/Accessibility/Integration/Reliability) — упрощённое подмножество ISO 25010 / FURPS+, ни одна модель не названа — medium; квантификация vague→testable («'fast' → 'within 2 seconds'») — Gilb / Volere fit criteria — medium; минимализм против gold plating («We only document NFRs that matter for THIS product») — YAGNI-дух + lean documentation — medium.
- **Изобретение BMAD:** релевантность-гейт перед каждой категорией (Quick Assessment Questions — категория пишется только при попадании); explicit include-критерии в «NFR CATEGORY GUIDANCE» («Include Security When: Handling sensitive user data…»).
- **Замысел автора:** NFR — главный источник раздувания PRD; дефолт — НЕ-написание категории, а каждая включённая обязана быть измеримой, потому что downstream-архитектор превратит её в решения буквально.
- **Настроить под себя:** §2-3 + CATEGORY GUIDANCE.

> **Дрейф:** «Step 10 of 12» — **третье** поколение счётчика (не 11 и не 13).

#### Крупица: step-11 — полировка
- **Файл:** `steps-c/step-11-polish.md`
- **Названо в файле:** ничего.
- **Узнаваемо без имени:** финальный substantive edit («Review for flow and coherence… PRESERVE user's voice») — издательская практика — high; coverage-аудит против исходников (§2b: «for each brainstorming idea, check if it landed in any PRD section… Identify dropped ideas») — RE-верификация полноты — medium; осознание template bias («structured template has an implicit bias toward concrete/structural ideas. Soft ideas… frequently get silently dropped») — близко к критике формализации (Goguen 1994), фактически авторское наблюдение — low.
- **Изобретение BMAD:** **единственный шаг, нарушающий append-only** — «replace the entire document content with the polished version» — компенсация стоимости append-only сборки (дубли, швы); обязательная перезагрузка доктрины перед полировкой («Read ../data/prd-purpose.md… Internalize the philosophy») — само-калибровка LLM-редактора; «Brainstorming Reconciliation» §2b — возврат потерянных «мягких» идей (tone/philosophy/feel) с явным списком юзеру.
- **Замысел автора:** автор знает побочные эффекты собственной архитектуры (9 аппендов = дубли; структурный шаблон молча выкидывает нешаблонное) и ставит компенсатор с Must-Preserve списком как страховкой от пере-редактуры.
- **Настроить под себя:** списки Must Preserve / Can Consolidate в §4.

> **Дрейфы:** §2b стилистически выбивается (узкие примеры «coaching approach ideas») — похоже на поздний точечный патч, возможно локальная кастомизация инсталляции; заголовок «APPEND TO DOCUMENT» противоречит содержимому «replace the entire document content» (`:185-187`) — копипаста шаблона шага.

#### Крупица: step-12 — завершение
- **Файл:** `steps-c/step-12-complete.md`
- **Названо в файле:** ничего.
- **Узнаваемо без имени:** phase-gate handoff с независимой валидацией (Option 1: bmad-check-implementation-readiness «Before starting technical architecture») — Stage-Gate (Cooper 1986) / Definition of Done — medium; living document («update it also as needed») — agile-канон — medium.
- **Изобретение BMAD:** запись `workflow_status['prd'] = '{outputFile}'`; валидация предлагается, но не принудительна («Option 2: Skip for Now»); роутинг дальше через bmad-help.
- **Замысел автора:** терминальный шаг «продаёт» следующий шаг; валидация вынесена в отдельный воркфлоу с другой персоной — maker-checker.
- **Настроить под себя:** §3-4 — предлагаемые следующие воркфлоу.

> **Дрейф №1:** сам шаг должен добавить себя в stepsCompleted (на этом построена логика завершённости step-01/01b), но в EXECUTION PROTOCOLS этого требования нет.
>
> **Дрейф №2 — исправление ошибки раскопок (критик):** раскопки назвали workflow_status «реестром, по которому bmad-help и следующие фазы знают, что PRD готов». Это **неверно**: grep по всему `_bmad/` даёт ровно 2 вхождения `workflow_status`, оба — записи (`step-12-complete.md:55`, ux `step-14-complete.md:79`), **читателей ноль**. `core/bmad-help/SKILL.md:50-53` детектит завершённость иначе — поиском выходных файлов по паттернам из module-help.csv + fuzzy-match. workflow_status — висячая запись в несуществующий механизм: писатель без читателя, сам по себе drift-finding, который раскопки приняли за рабочую фичу.

### 2.2 `bmad-validate-prd` — автономный инспектор

Покрыт раскопками через сравнение с легаси-копией + крупицы V-0…V-11. Шаги после V-11 (финальный отчёт/вердикт) в моих исходных данных отсутствуют — JSON оборван; по ним честно: не раскопано.

#### Крупица V-0: SKILL.md + workflow.md
- **Файлы:** `bmad-validate-prd/SKILL.md`, `workflow.md`
- **Названо в файле:** **Just-In-Time Loading** (термин из Lean/TPS, Тайити Оно; переосмыслен как менеджмент контекст-окна).
- **Узнаваемо без имени:** role prompting («Your Role: Validation Architect and Quality Assurance Specialist… merged with the details of this role description») — high; FSM с персистентным state — medium; append-only журнал — medium.
- **Изобретение BMAD:** те же микрофайлы + Critical Rules с эмодзи-маркерами + halt-at-menu контракт + двуязычный контракт; **собственная персона валидатора, отличная от персоны создателя**.
- **Замысел автора:** те же два отказа LLM (переполнение контекста, самовольная оптимизация); SKILL.md — трёхстрочный диспетчер.
- **Настроить под себя:** языки/пути в config.yaml; персона — строка «Your Role» в workflow.md.

#### Крупица V-1: discovery
- **Файл:** `steps-v/step-v-01-discovery.md`
- **Названо в файле:** ничего.
- **Узнаваемо без имени:** inspection planning (сбор артефактов + init defect log ДО проверки) — фазы Planning/Overview у Fagan 1976 — medium; convention over configuration (глобы `*prd*.md`, sharded `*prd*/*.md`) — medium; data provenance входных документов (extract `inputDocuments` из frontmatter PRD) — data lineage / W3C PROV — medium.
- **Изобретение BMAD:** отдельный validation report со своим state (validationStepsCompleted, validationStatus: IN_PROGRESS); inputDocuments как машиночитаемая цепочка происхождения; меню A/P/C; SYSTEM SUCCESS/FAILURE блок.
- **Замысел автора:** «fresh context validation» — валидатор стартует в пустой сессии и всё восстанавливает с диска; «FORBIDDEN to perform any validation checks» — одна забота на шаг; HALT даёт человеку довнести документы.
- **Настроить под себя:** глобы §2; YAML-шаблон frontmatter отчёта §7.

> **Дрейфы:** frontmatter заявляет «ONLY variables used in this step», но §7 использует `{validationReportPath}`, который нигде не объявлен и не определён — **имя файла отчёта не специфицировано**; шов с edit-workflow — step-e-01 ищет отчёт глобом `validation-report-*.md`, а конвенция имени здесь не задана: отчёт может быть создан под именем, которое edit не найдёт.

#### Крупица V-2: format detection
- **Файл:** `steps-v/step-v-02-format-detection.md`
- **Названо в файле:** ничего.
- **Узнаваемо без имени:** inspection entry criteria (проверка пригодности артефакта до инспекции, эскалация при несоответствии) — Fagan; Gilb & Graham 1993 — medium; структурный фингерпринтинг + контролируемый словарь синонимов заголовков («Executive Summary (or variations: ## Overview, ## Introduction)») — IR / библиотечное дело — medium; детерминированная рубрика по порогу («BMAD Standard: 5-6 core sections… Variant: 3-4… Non-Standard: <3») — high.
- **Изобретение BMAD:** канон «6 BMAD PRD core sections»; условный HALT — Standard/Variant идут дальше «Without delay», Non-Standard требует решения человека.
- **Замысел автора:** шаг-маршрутизатор: гонять полный конвейер по чужому формату без согласия бессмысленно — следующие чеки ищут конкретные секции; формат меряется дёшево (подсчёт заголовков).
- **Настроить под себя:** список 6 секций и синонимы §2; пороги §3.

> **Дрейфы:** prd-purpose.md декларирует **9** Required Sections, формат-детекция проверяет **6** — два разных определения «стандартного» PRD внутри одного семейства; frontmatter-плейсхолдеры `{prd_file_path}`/`{validation_report_path}` никто не резолвит — значения существуют только в памяти LLM.

#### Крупица V-2B: parity check (опциональная ветка; в раскопках отсутствовала, добавлена критиком + есть в легаси-сравнении)
- **Файл:** `steps-v/step-v-02b-parity-check.md`
- **Названо в файле:** **Gap Analysis** (классика стратегического консалтинга 1960-70-х).
- **Узнаваемо без имени:** T-shirt оценка усилий (Minimal/Moderate/Significant) — agile relative estimation — medium; cost of conformance (информировать о цене приведения к стандарту до решения) — Juran/Crosby, упрощённо — low.
- **Изобретение BMAD:** понятие «BMAD PRD parity» — степень соответствия чужого документа канону 6 секций; тройное меню выхода [C]/[E]/[S], «FORBIDDEN to proceed without user selection».
- **Замысел автора:** не отвергать legacy-документы молча и не валидировать их молча (шквал ложных Critical) — конвертировать «ваш документ не наш» в управленческое решение человека с оценкой эффорта.
- **Настроить под себя:** шкала эффорта §2, шаблон отчёта §3.

> **Дрейфы:** чеклист «3 вопроса на секцию» дублируется почти дословно в `step-e-01b` (edit-workflow) — два независимых источника одного чеклиста; **канон-дрейф «6 core sections» ≠ «9 Required Sections»** (см. V-2).

#### Крупица V-3: information density
- **Файл:** `steps-v/step-v-03-density-validation.md`
- **Названо в файле:** **Graceful Degradation** (fault-tolerant computing 1970-х / progressive enhancement).
- **Узнаваемо без имени:** plain-language lint по словарю запрещённых фраз («'Due to the fact that' (use 'because')») — Strunk & White, линтеры write-good/proselint, Plain Writing Act 2010 — high; severity levels с числовыми порогами (Critical >10 / Warning 5-10 / Pass <5) — таксономия статического анализа (lint, Johnson 1978) — high; вынос проверки в изолированный субпроцесс (Task tool) — context isolation / sub-agent fan-out — high.
- **Изобретение BMAD:** «Information Density» как формализованный критерий с измеримым порогом; паттерн «Attempt Sub-Process → Graceful Degradation», повторяющийся во всех v-03…v-12; безостановочная секция конвейера («FORBIDDEN to pause or request user input», «Without delay»).
- **Замысел автора:** превратить стилистическое требование в подобие линтера — словари, счётчики, line numbers, пороги; субпроцесс — чтобы не засорять основной контекст полным PRD; деградация — чтобы не ломаться без Task tool.
- **Настроить под себя:** словари filler/wordy фраз §1-2 (дублируются с prd-purpose.md); пороги §3.

> **Дрейфы (boilerplate-противоречия):** в автономный шаг скопированы правила «NEVER generate content without user input» и «YOU ARE A FACILITATOR, not a content generator» — а шаг обязан работать без пользователя и сам пишет отчёт; правило «When loading next step with 'C'» — в шаге, где меню нет вообще.

#### Крупица V-4: brief coverage
- **Файл:** `steps-v/step-v-04-brief-coverage-validation.md`
- **Названо в файле:** ничего.
- **Узнаваемо без имени:** backward traceability к upstream-документу (Fully/Partially Covered / Not Found / Intentionally Excluded) — IEEE 29148 bidirectional traceability, V-model — high; категория «Intentionally Excluded» — requirements disposition / non-defect classification — medium; явный N/A в отчёте при отсутствии брифа — audit practice (ISO 19011) — medium.
- **Изобретение BMAD:** фиксированная 7-элементная карта покрытия брифа (Vision/Users/Problem/Features/Goals/Differentiators/Constraints); severity гэпов по смысловой важности (Critical = «Core vision, primary users, main features»).
- **Замысел автора:** закрыть классическую дыру конвейера документов — PRD пишется по брифу, но содержимое брифа теряется по дороге; уважение к умышленным исключениям, различение «проверено, чисто» и «нечем проверять».
- **Настроить под себя:** состав контента брифа §2 (субпроцесс-промпт) и §3 (деградация).

> **Дрейф:** frontmatter объявляет `productBrief: '{product_brief_path}'`, тело плейсхолдер ни разу не использует — мёртвая переменная.

#### Крупица V-5: measurability
- **Файл:** `steps-v/step-v-05-measurability-validation.md`
- **Названо в файле:** ничего.
- **Узнаваемо без имени:** verifiability требований («'response time < 200ms', not 'fast response'») — IEEE 830 §4.3.6 / ISO 29148 — high; **weak-word analysis** (словари «easy, fast, simple, intuitive…» и «multiple, several, some, many…») — NASA ARM tool (Wilson et al., NASA SEL 1997) — high; шаблон «[Actor] can [capability]» — Connextra / actor-goal (Cockburn), родственник EARS — medium; NFR по схеме критерий-метрика-метод — Gilb Planguage / ISO 25010 — medium.
- **Изобретение BMAD:** единый числовой порог severity на сумму FR+NFR нарушений (те же >10/5-10/<5, что в v-03 — унификация шкалы); раннее деление leakage на «capability-relevant vs leakage».
- **Замысел автора:** сердце RE-части — verifiability закодирована как четыре механических анти-паттерна, потому что LLM-потребители ниже (architecture, stories, тесты) физически не могут работать с неизмеримым требованием; line numbers обязательны — finding должен быть actionable для edit-workflow.
- **Настроить под себя:** словари weak words §2; эталоны формата §1-2 (канон ещё и в prd-purpose.md).

> **Дрейф:** проверка «No implementation details» целиком дублирует шаг v-07 — одно нарушение посчитается дважды в двух severity-шкалах (v-05: Critical >10; v-07: Critical >5).

#### Крупица V-6: traceability
- **Файл:** `steps-v/step-v-06-traceability-validation.md`
- **Названо в файле:** **Traceability Matrix** (RTM — стандартный артефакт RE; Gotel & Finkelstein, ICRE 1994; IEEE 29148).
- **Узнаваемо без имени:** orphan requirement detection («FRs not traceable to any user journey or business objective») — RTM-практика, DOORS — high; goal-oriented цепочка Vision → Success Criteria → Journeys → FRs — KAOS/i*, популярная форма Impact Mapping (Adzic 2012) — medium; severity по типу дефекта (orphan = автоматический Critical), не по количеству — инспекционные стандарты — medium.
- **Изобретение BMAD:** двунаправленная проверка — не только FR без journey, но и journey без FR, и success criterion без journey; Scope→FR alignment как отдельное звено.
- **Замысел автора:** реализация тезиса доктрины «This chain ensures we build the right thing» — разрыв цепи внутри PRD умножится дальше; orphan = либо потерянная цель, либо контрабандный скоуп.
- **Настроить под себя:** состав звеньев цепочки §1-2.

> **Дрейф (системный):** для api_backend project-types.csv предписывает skip_sections=…user_journeys — у валидного API-PRD звено «User Journeys → FRs» обречено на Gaps: v-06 не знает об исключениях v-09 (шаги не обмениваются контекстом классификации).

#### Крупица V-7: implementation leakage
- **Файл:** `steps-v/step-v-07-implementation-leakage-validation.md`
- **Названо в файле:** **Separation of Concerns** (E.W. Dijkstra, 1974).
- **Узнаваемо без имени:** design independence («Requirements specify WHAT, not HOW») — IEEE 830, Davis 1993 — high; denylist-скан с allowlist-исключениями («'API consumers can access data via REST endpoints' — API/REST is capability; 'React components fetch data using Redux' — leakage») — lint allow/deny паттерн — medium.
- **Изобретение BMAD:** категоризированный словарь технологий по 7 корзинам (Frontend/Backend/DB/Cloud/Infra/Libraries/Data Formats) как лексический детектор; **более строгая шкала** — Critical уже при >5 (vs >10 у v-03/v-05): утечка реализации опаснее многословия.
- **Замысел автора:** защита границы PRD↔Architecture — если PM зашьёт «React + PostgreSQL» в требования, LLM-архитектор примет это за решённое и потеряет пространство выбора; различение технологии-как-способности от технологии-как-решения судит LLM, словарь только наводит.
- **Настроить под себя:** словарь технологий §2 под свой стек; порог §4.

> **Дрейф:** дублирование с v-05 — одно нарушение в двух разделах отчёта с разными порогами.

#### Крупица V-8: domain compliance
- **Файл:** `steps-v/step-v-08-domain-compliance-validation.md`
- **Названо в файле:** HIPAA / FDA Regulatory Pathway; SOC2 / PCI-DSS / GDPR Compliance Matrix; WCAG 2.1 AA / Section 508.
- **Узнаваемо без имени:** risk-based audit scoping (глубина проверки от регуляторного класса: low → skip, high → validate special sections) — ISO 19011 / ISA 315 — medium; decision table CSV как внешний источник правил — high; compliance matrix как форма отчёта (Met/Partial/Missing) — GRC — high.
- **Изобретение BMAD:** `classification.domain` в frontmatter PRD как переключатель глубины (auto-detect на create, enforce на validate); fail-soft default — нет classification → «Treat as general (low complexity)», воркфлоу не падает на неразмеченных PRD.
- **Замысел автора:** shift-left регуляторных чеков («Missing these requirements… expensive rework»); CSV выбран, чтобы добавлять домены без правки шагов — но автор «не удержался» и продублировал часть таблицы хардкодом в промпте, что и разъехалось.
- **Настроить под себя:** строки CSV (главный безопасный вход); `classification.domain` per-документ.

> **Дрейфы (3):** сломана нумерация секций (### 1, ### 2, снова ### 2, ### 3, затем сразу ### 5, 6, 7 — дубль «2», пропавший «4»); хардкод §2 относит EdTech к high complexity, CSV даёт `edtech,medium` — **промпт и данные противоречат, двойной источник истины**; CSV-значение complexity=redirect (gaming) не обрабатывается логикой шага, знающей только low/medium/high.

#### Крупица V-9: project-type compliance
- **Файл:** `steps-v/step-v-09-project-type-validation.md`
- **Названо в файле:** ничего.
- **Узнаваемо без имени:** tailoring/profiles стандарта документации, включая **запрещённые** секции («Required sections MUST be present. Skip sections MUST NOT be present», «API PRDs don't need UX sections») — tailoring clause ISO 29148/12207, MIL-STD-498 — medium; negative checks (проверка отсутствия) — closed-world schema / prohibited items checklist — medium; CSV как источник правил — high.
- **Изобретение BMAD:** валидатор наказывает за **лишние** секции (раздутый PRD для CLI с visual design), не только за отсутствующие; silent default — нет projectType → «Assume web_app (most common) and note in findings»; Compliance Score в процентах.
- **Замысел автора:** борьба с шаблонным мышлением LLM-генераторов, тащащих UX-секции в backend-документ; PRD «по фигуре» типа проекта.
- **Настроить под себя:** строки CSV; `classification.projectType` per-документ.

> **Дрейфы (4, самый разъехавшийся шаг фазы):** дубль нумерации (два «### 4»); хардкод-список типов в §2/§4 (data_pipeline, ml_system, library_sdk, infrastructure, desktop_app) — **четырёх из них нет в CSV**, зато в CSV есть saas_b2b, developer_tool, cli_tool, iot_embedded, blockchain_web3, game, не упомянутые в §4; required-секции §4 для web_app не совпадают с CSV; **системное противоречие трёх шагов** — CSV для api_backend ставит user_journeys в skip, при этом v-02 считает User Journeys одной из 6 core sections, а v-06 требует цепочку через journeys: валидный API-PRD получит противоречивые вердикты от трёх шагов одного конвейера.

#### Крупица V-10: SMART validation
- **Файл:** `steps-v/step-v-10-smart-validation.md`
- **Названо в файле:** **SMART** (Doran 1981; важно — BMAD заменяет каноническое T=Time-bound на **Traceable**, Achievable→Attainable: осознанная мутация под requirements engineering, линия Mannion & Keepence 1995).
- **Узнаваемо без имени:** anchored rating scale (поведенческие якоря на баллы 1/3/5: «5: Clear, unambiguous… 1: Vague») — BARS (Smith & Kendall 1963), педагогические рубрики — medium; quality gate по **доле** дефектных единиц (Critical если >30% flagged) — acceptance sampling (AQL) — low; actionable feedback (каждому флагу — конкретное предложение исправления) — code review practice — medium.
- **Изобретение BMAD:** замена T → Traceable состыковывает рубрику с цепочкой v-06 (Traceable=5 значит «clearly traces to user journey or business objective»); **флаг при любом критерии <3, а не по среднему** — худший балл решает, защита от усреднения дефекта.
- **Замысел автора:** после бинарных чеков — градуированная оценка каждого FR: отчёт получает таблицу-рейтинг, по которой edit-workflow чинит в порядке убывания вреда; у capability-требования нет дедлайна, но обязан быть источник — потому Time-bound честно выброшен.
- **Настроить под себя:** якоря 1/3/5 и условие «score < 3» §2-3; пороги доли §4.

> **Дрейф:** Measurable и Traceable из рубрики дублируют выделенные шаги v-05 и v-06 — **третье** место отчёта, где то же требование оценивается по тому же признаку с третьей шкалой.

#### Крупица V-11: holistic quality (частично — исходный JSON обрывается здесь)
- **Файл:** `steps-v/step-v-11-holistic-quality-validation.md`
- **Узнаваемо без имени:** Perspective-Based Reading — чтение документа с позиций разных стейкхолдеров («Executive-friendly… Developer clarity… Designer clarity… Stakeholder decision-making» + отдельный набор LLM-перспектив) — PBR, Basili/Shull et al., NASA — атрибуция в раскопках есть, confidence не зафиксирован (обрыв данных).
- **Остальные поля крупицы (изобретения, замысел, настройка) и шаги V-12+ (предположительно completeness-чек и финальный отчёт):** не раскопано — раскопки оборваны, утверждать не буду.

### 2.3 `bmad-edit-prd` — дельта-редактирование (данные только от критика, мелко)

- **Поток (5 шагов):** `step-e-01-discovery` (детект формата BMAD/legacy + автопоиск validation report как гайда) → ветка `step-e-01b-legacy-conversion` (gap-анализ legacy PRD, оценка усилий конверсии) → `step-e-02-review` (план изменений, approve человеком) → `step-e-03-edit` → `step-e-04-complete`.
- **Заметный приём:** `step-e-03-edit.md:72-95` — правки **через Task-субагент per-секция** с graceful degradation. Тот же паттерн, что lookup в create/step-02, но впервые для **записи**, не чтения.
- **Замысел (реконструкция):** редактирование через план-→-approve-→-исполнение — человек утверждает дельту до того, как LLM трогает документ; validation report служит машинным входом «что чинить».
- **Названо/узнаваемо/настройка по шагам:** не раскопано.

> **Дрейф — критичная находка:** все 5 step-e файлов ссылаются на несуществующий путь `{project-root}/_bmad/bmm-skills/2-plan-workflows/create-prd/...` (frontmatter `step-e-01:3`, `e-01b:4`, `e-02:5`, `e-03:4`, `e-04:4`; последний целит в `create-prd/steps-v/step-v-01-discovery.md`). Каталога `_bmad/bmm-skills/` **нет** (проверено ls). Следствия: (а) edit-prd не может загрузить prd-purpose.md и validation-воркфлоу — битые ссылки инсталлятора; (б) нюанс к вердикту «create-prd/ можно сносить»: edit-prd *намеревается* использовать именно легаси-раскладку, но путь и так битый — удаление ничего дополнительно не ломает; фикс = перенацелить 5 файлов на `bmad-create-prd/data/` и `bmad-validate-prd/steps-v/`.

### 2.4 `bmad-create-ux-design` — 14 шагов (данные только от критика, мелко)

- **Файл:** `bmad-create-ux-design/workflow.md` — та же step-file конституция (укороченная), output `{planning_artifacts}/ux-design-specification.md`.
- **Шаги:** discovery → core-experience → emotional-response → inspiration → design-system → defining-experience → visual-foundation → design-directions → user-journeys → component-strategy → ux-patterns → responsive-accessibility → complete.
- **Уникальность в фазе:** единственный воркфлоу с **не-markdown артефактами** — `step-08-visual-foundation.md` генерит интерактивный `ux-color-themes.html`, `step-09-design-directions.md:70-74` — `ux-design-directions.html` с «6-8 full-screen mockup variations». LLM как генератор визуальных HTML-прототипов внутри planning-фазы — паттерн, которого нет в PRD-ветке.
- **Покрупичный разбор шагов (названо/узнаваемо/изобретения):** не раскопано.
- **Асимметрия:** нет валидатора, нет edit-воркфлоу, нет data/ CSV-справочников — качество ux-spec не проверяется даже псевдомашинно.

> **Дрейфы (новые, от критика):** `steps/step-01-init.md:60` использует переменную `{product_knowledge}` — в config.yaml её нет (есть `project_knowledge`; create-prd/step-01:79 использует правильную); `step-01-init.md:67` — глоб PRD подписан как «Research Documents (`*prd*.md`)», перепутанная метка; `step-14-complete.md` — нумерация секций «### 3.» → «### 5.», 4 пропущена.

### 2.5 Агент-персоны — точка входа фазы (данные только от критика)

- **`bmad-agent-pm/SKILL.md`** — персона **«John», Product Manager**: identity/communication style («Asks 'WHY?' relentlessly like a detective»); принципы — **Jobs-to-be-Done и opportunity scoring названы по имени** (примечательно: JTBD, который в step-02b раскопки отметили как «дух без имени», на уровне персоны атрибутирован явно). Меню capabilities = маршрутизатор: CP/VP/EP внутри фазы, CE и IR — фаза 3, CC — anytime. Активация: «Load config via bmad-init skill» → greet `{user_name}` → STOP and WAIT. Анти-галлюцинация: «DO NOT invent capabilities on the fly».
- **`bmad-agent-pm/bmad-skill-manifest.yaml`** — `type: agent`, displayName/icon/module — машинный реестр агентов (формат в раскопках не упоминался).
- **`bmad-agent-ux-designer/SKILL.md`** — персона **«Sally», UX Designer**, единственная capability CU. Правило персистенции: «you must not break character until the user dismisses this persona… when the user calls a skill, this persona must carry through» — объясняет фразу «merged with the details of this role description» в каждом workflow.md.
- **Покрупичный разбор (изобретения/замысел/настройка):** не раскопано; реконструкция замысла — персона как «человеческое лицо» поверх воркфлоу-машинерии + анти-галлюцинационный замок на меню.

### 2.6 `create-prd/` — легаси-каталог (вердикт раскопок, проверен)

- **Что это:** НЕ другой воркфлоу и не полный дубль — **осиротевшая копия валидатора**. Upstream BMAD держал create+validate в одной папке `create-prd`; инсталлятор v6.2.2 развёл их в `bmad-create-prd` и `bmad-validate-prd`, но не удалил исходник.
- **Доказательства:** (1) data/ байт-в-байт идентичны bmad-create-prd/data и bmad-validate-prd/data (diff exit 0); (2) каждый steps-v файл отличается от живого только наличием name/description/standalone во frontmatter (в bmad-версии вынесено в SKILL.md); (3) SKILL.md отсутствует → как скилл не регистрируется → мёртвый код; (4) внутри workflow-validate-prd.md `name: validate-prd`, не create-prd.
- **Безопасное удаление:** можно сносить — живые копии в bmad-validate-prd/ (проверено diff'ом), с поправкой из §2.3: битые ссылки edit-prd целили сюда, но они и так не работают.

> **Дрейф (главный системный):** **тройное дублирование данных** — prd-purpose.md, domain-complexity.csv, project-types.csv существуют в 3 экземплярах (bmad-create-prd/data, bmad-validate-prd/data, create-prd/data). Правка доктрины в одном месте **не доедет до валидатора**: создатель и судья будут жить по разным конституциям. Плюс путаница навигации (имя папки create-prd против name: validate-prd внутри).

### 2.7 `module-help.csv` и реестры инсталляции (от критика)

- `_bmad/bmm/module-help.csv` строки 16-19 — единственная машинная карта фазы: bmad-create-prd required=true; validate after=create; edit after=validate; ux-design after=create, optional. Мелкий дрейф: у VP и EP колонка description пуста.
- `_bmad/_config/` — skill-manifest.csv, agent-manifest.csv, files-manifest.csv, manifest.yaml (v6.2.2 от 2026-04-05). По files-manifest можно доказать легаси-статус `create-prd/` (есть ли он там) — **раскопки этого не проверяли**, фиксирую как открытый вопрос.

---

## 3. Карта детерминизма фазы

Главный факт: **в фазе ноль скриптов, exit-кодов и машинных валидаторов** — исполнитель 100% шагов это LLM, читающий markdown. Всё «машинное» ниже — псевдодетерминизм: правила сформулированы как механика (таблицы, пороги, словари), но исполняются дисциплиной модели.

| Решение | Кто решает | Механизм |
|---|---|---|
| Переход к следующему шагу create (13 раз на 1 PRD) | **человек-гейт** | меню A/P/C, «ALWAYS halt and wait», продвижение только по 'C' |
| Запись секции в документ | **человек-гейт** | draft → review → approve, «FORBIDDEN to append without 'C'» |
| Куда продолжить после обрыва сессии | **псевдо-машина (LLM по таблице)** | FSM lookup-таблица step-01b — единственное место с машинно-проверяемыми переходами |
| Что считать прогрессом | **машинный контракт, без enforcement** | stepsCompleted в frontmatter; двухусловный выход «человек нажал C + state записан» |
| Скип step-05 (domain) | **данные + человек-override** | classification.complexity==low из frontmatter → skip; меню [C]/[D] |
| Скип step-06 (innovation) | **данные + LLM-детектор + человек** | innovation_signals из CSV + лексические маркеры; «инноваций нет» — легитимный исход |
| Редирект gaming/game | **данные** | complexity=redirect / «HALT» зашиты в строки CSV |
| Классификация проекта | **LLM + подтверждение человека** | сигналы из CSV, «Does this sound right to you?» — HITL, ошибка каскадирует на 11 шагов |
| CSV-lookup | **субагент с контрактом** | «Return ONLY the matching row as YAML» + graceful degradation на прямое чтение |
| Качество FR на create | **LLM судит сам себя** | self-validation чеклист step-09 (Completeness/Altitude/Quality) — без enforcement |
| Целостность документа | **инвариант на дисциплине** | append-only (2c-10), единственное санкционированное переписывание — step-11 с Must-Preserve |
| Запускать ли валидацию вообще | **человек** | step-12: Option 1 validate / Option 2 «Skip for Now» — настоящая проверка опциональна |
| Вердикты валидатора (v-03…v-12) | **автономный LLM-судья** | «FORBIDDEN to pause or request user input» — человека внутри конвейера НЕТ |
| Severity находок | **псевдо-машина** | числовые пороги (>10/5-10/<5; >5 для leakage; >30% для SMART; orphan=Critical безусловно) — но подсчёт делает LLM |
| Формат PRD (Standard/Variant/Non-Standard) | **псевдо-машина** | детерминированная рубрика по счётчику заголовков |
| Судьба нестандартного PRD | **человек** | условный HALT + parity-check меню [C]/[E]/[S] |
| План правок в edit-prd | **человек** | step-e-02 approve-гейт; исполнение — субагенты per-секция |
| «Фаза 2 завершена» для роутера | **машина по файлам** | bmad-help ищет выходные файлы по паттернам module-help.csv (НЕ workflow_status — тот мёртв) |

**Как сместился ползунок доверия относительно Phase 1:**

1. **Появилась обязательность.** В Phase 1 всё optional; здесь module-help.csv впервые ставит required=true (create-prd) и машинные after-зависимости.
2. **Появился автономный участок.** Phase 1 — сплошные человеко-гейты; здесь валидатор — первый конвейер, где человеку явно **запрещено** вмешиваться («FORBIDDEN to pause») — LLM-as-judge с рубриками. Зато вход в этот конвейер (запускать или нет) отдан человеку.
3. **Псевдомеханизация выросла.** Транзитные таблицы FSM, числовые пороги severity, словари weak words, проценты — формы детерминизма позаимствованы у линтеров и инспекций, но enforcement по-прежнему нулевой: ни одна «механика» не выполняется кодом.
4. **Разделение creator/validator.** Новое относительно Phase 1: проверка вынесена в отдельный воркфлоу с другой персоной и fresh context — анти-самопроверка на уровне архитектуры, а не промпта.
5. **Цена псевдомеханики видна в дрейфах:** 3 поколения счётчика шагов, промпт↔CSV разъезды, 6-vs-9 канонов секций, тройные копии доктрины, мёртвый workflow_status — «механика на прозе» дрейфует ровно потому, что её никто машинно не проверяет.

---

## 4. Соответствие канону

Фаза густо заимствует, но почти ничего не атрибутирует. Разделяю честно: «названо в файле» ≠ «атрибутирован источник» (SMART назван, Doran — нет; lean/validated learning названы словами Ries — сам Ries не упомянут; JTBD назван только на уровне персоны PM).

| Канон | Что взято | Что выкинуто | Что переосмыслено под solo+LLM |
|---|---|---|---|
| **SMART** (Doran 1981 / RE-вариант Mannion & Keepence 1995) | пять букв как рубрика оценки FR, названо по имени | каноническое **T=Time-bound** (и Achievable) | T→**Traceable**, A→Attainable — рубрика состыкована с traceability-цепочкой v-06; флаг по худшему баллу, не по среднему |
| **Lean Startup / MVP** (Ries 2011, Robinson 2001) | MVP, validated learning (дословно), concierge/manual-first, riskiest assumption | весь цикл build-measure-learn как процесс, пивоты | MVP — переговорная рамка scope (3 горизонта) внутри одного интервью; собственная четвёрка «problem-solving/experience/platform/revenue MVP», которой в литературе нет |
| **MoSCoW** (Clegg 1994, DSDM) | Must-have/Nice-to-have анализ | имя метода, корзины Should/Won't | упрощён до бинарной линзы «Without this, does the product fail?» — удобно для скриптованной реплики LLM |
| **User stories** (Connextra/XP ~2001) | синтаксис «[Actor] can [capability]» | часть «so that» (зачем), карточка-разговор-подтверждение | сжат до FR-строки с ID; обоснование вынесено в traceability к журнеям, а не в текст требования |
| **Personas / journey mapping** (Cooper 1999; service design CJM) | персоны названы по имени; журней с эмоциональной кривой | инструментальный CJM (swimlanes, touchpoints) | журней = драматическая арка (Opening Scene→Climax→Resolution) — нарратив как способ заставить LLM и человека увидеть edge cases; журней объявлен **единственным** источником FR |
| **JTBD** (Christensen/Ulwick) | назван по имени в персоне PM; «дух» в вопросах 02b («deeper need», «desperately want to achieve») | формализм jobs/outcomes/forces | растворён в вопросные батареи фасилитатора |
| **IEEE 830 / ISO 29148** (requirements engineering) | verifiability, design-free requirements (what-не-how), traceability, FR-ID | сам стандарт нигде не назван; формальные атрибуты требований (priority, rationale, source per-FR) | трассируемость = цепочка секций одного markdown-документа; проверка — LLM-судья по рубрикам, не RTM-инструмент |
| **NASA ARM weak words** (Wilson 1997) | словари субъективных прилагательных и расплывчатых квантификаторов | инструмент (это был реальный анализатор кода) | словарь вшит в промпт, «линтер» исполняет LLM |
| **Fagan inspections / PBR** (1976; Basili/Shull) | фазность (planning → entry criteria → проверка), perspective-based reading в v-11, severity-таксономии | командные роли (moderator/reader/scribe), метрики дефектов, повторная инспекция | инспекторы = один LLM в свежем контексте с другой персоной; «перспективы» включают набор LLM-стейкхолдеров (downstream-агенты как аудитория) |
| **Stage-Gate** (Cooper 1986) | гейт после PRD (validate → readiness check перед фазой 3) | комитет гейта, kill-критерии | гейт опционален («Skip for Now»), решает один человек |
| **Decision tables** (1960-е) | CSV-таблицы домен/тип → правила | движок правил | **данные-как-промпт**: строки CSV управляют интервью, секциями, веб-поисками и даже HALT/redirect |
| **JIT / Lean (TPS)** | термин Just-In-Time назван | производственный контекст | переосмыслен как менеджмент контекст-окна LLM (грузить только текущий шаг) |
| **Gilb Planguage / Volere fit criteria** | шаблон NFR «metric+condition+measurement method», квантификация vague→testable | Scale/Meter/Target формализм, имена | шаблон-строка + few-shot примеры плохо/хорошо |
| **Three Horizons / Now-Next-Later** | трёхфазный роадмап MVP/Growth/Vision | имена, временные горизонты H1/H2/H3 | фазы привязаны к scope-переговорам, не к портфелю |
| **Innovation theater** (Blank 2019) | термин использован как FAILURE MODE | автор | симметричная защита: не выдумывать новизну И не пропускать настоящую; явное разрешение «инноваций нет» |
| **Greenfield/brownfield** | классификация на step-01 | — | управляет тем, что грузится в контекст (существующие доки) |

Чего из канона Phase 2 демонстративно **нет**: Scrum/спринты (придут в фазе эпиков), user story mapping (Patton), EARS-синтаксис, OKR/HEART по имени, формальные use cases. TDD/BDD к этой фазе не относятся — раскопки молчат, и это ожидаемо.

---

## 5. Карта настройки фазы

Ручки в порядке убывания безопасности. Про переживание обновлений честно: критик зафиксировал существование `_bmad/_config/files-manifest.csv` (manifest.yaml v6.2.2 от 2026-04-05), но **раскопки не проверяли**, хеширует ли инсталлятор vendored-файлы и перетирает ли правки при апдейте — пометки «переживёт ли обновление» ниже это гипотеза, основанная на устройстве, не проверка.

| Ручка | Файл | Что даёт | Риск/переживание обновления |
|---|---|---|---|
| Конфиг проекта | `_bmad/bmm/config.yaml` | языки, имя, skill level, пути артефактов; communication_language уже Russian | per-project файл — самая безопасная ручка; **сюда же стоит добавить отсутствующие `document_output_language` и `user_name`** (фикс дрейфа, который требуют все 4 workflow.md) |
| Типы проектов | `data/project-types.csv` | новая строка = новый тип со своим интервью (key_questions), секциями (required/skip), сигналами новизны; step-07 подхватит сам | задумано как главный тюнинг-вход; НО **3 копии файла** + хардкоды в v-09 — править надо синхронно, иначе создатель и валидатор разъедутся |
| Домены | `data/domain-complexity.csv` | новая отрасль: сигналы, комплаенс, готовые web-запросы; управляет skip step-05 | те же 3 копии + хардкод EdTech в v-08 противоречит CSV уже сейчас |
| Доктрина качества | `data/prd-purpose.md` | анти-паттерны, SMART-критерии, Required Sections — источник истины для step-11 и валидатора | **тройная копия** — самая опасная правка фазы; плюс уже существующий разъезд 9-vs-6 секций с v-02 |
| Seed-шаблон | `templates/prd-template.md` | титул, стартовый frontmatter | добавление пустых секций сломает append-only |
| Путь выходного PRD | `workflow.md` frontmatter `outputFile` | куда пишется prd.md | vendored-файл — правка может перетереться обновлением |
| Step-файлы create | `steps-c/*.md` | реплики фасилитатора, вопросные батареи, Content Structure, бюджеты (20-50 FR, 5-8 областей, минимум журнеев), Must Preserve | прецедент локального патча уже есть — step-11 §2b Brainstorming Reconciliation похож на точечную кастомизацию инсталляции |
| Пороги и словари валидатора | `steps-v/step-v-03/05/07/10…` | severity-пороги (>10/5-10/<5, >5, >30%), словари filler/weak words/технологий, якоря рубрик 1/3/5 | словари частично дублируют prd-purpose.md — ещё одна пара для синхронизации |
| Канон формата | `steps-v/step-v-02-format-detection.md` | список «6 core sections» и синонимы, пороги классификации | при правке свести с 9 секциями доктрины — сейчас два канона |
| Плагины меню A/P | `_bmad/core/bmad-advanced-elicitation`, `bmad-party-mode` | заменить механизм рефлексии/мультиперсонной дискуссии для ВСЕХ шагов фазы разом | живут вне фазы — обновляются отдельно от неё |
| Персоны | `bmad-agent-pm/SKILL.md`, `bmad-agent-ux-designer/SKILL.md` + `bmad-skill-manifest.yaml` | identity, стиль, меню capabilities | персона «протекает» во все воркфлоу (carry through) — правка меняет тон всей фазы |
| Персона валидатора | `bmad-validate-prd/workflow.md` строка «Your Role» | характер судьи | — |
| Триггеры скиллов | `*/SKILL.md` description | фразы вызова | — |
| **Обязательный фикс** | 5 файлов `bmad-edit-prd/steps-e/*` frontmatter | перенацелить битые `_bmad/bmm-skills/...create-prd/...` на `bmad-create-prd/data/` и `bmad-validate-prd/steps-v/` | без этого edit-prd слеп: не видит доктрину и валидатор |
| **Гигиена** | каталог `create-prd/` | снести (живые копии проверены diff'ом) | заодно убирает третью копию data/ |

---

## 6. Уроки для своего пайплайна

Семь приёмов именно этой фазы, переносимых в собственный оркестратор (с поправками на наш принцип «код не доверяет LLM»).

1. **Данные-как-промпт (CSV-таблицы решений) — но с contract-test'ом промпт↔данные.** Идея блестящая: новая отрасль/тип продукта = строка CSV, и она управляет интервью, секциями, веб-поисками и даже HALT'ами. Провал BMAD — в той же фазе 4+ разъезда «промпт просит колонки, которых в CSV нет» и хардкоды, противоречащие таблице (v-08 EdTech, v-09 типы). Перенос: правила в данных + автоматический тест «каждая колонка, упомянутая в промптах, существует в CSV; каждый enum из CSV обработан логикой» (наш класс single-source drift — ровно об этом).

2. **Creator/validator с разными персонами и fresh context.** Создатель PRD и его судья — разные воркфлоу, разные роли, чистая сессия у судьи: анти-самопроверка на уровне архитектуры. Слабость BMAD — валидация **опциональна** («Skip for Now») и сама исполняется LLM. Перенос: разделение оставить, опциональность убрать (gate в коде, как наш exit-2-гейт), вердикты привязать к проверяемым фактам (line numbers — у BMAD это уже требуется, правильно).

3. **State живёт в самом артефакте + FSM-таблица для resume.** stepsCompleted в frontmatter делает документ переносимым вместе с прогрессом, а единственная машинно-читаемая lookup-таблица переходов стоит ровно в самой хрупкой точке — возобновлении после обрыва. Перенос: state в артефакте удобен; но таблицу переходов должен исполнять скрипт, не LLM — и тогда дыра типа «завершённый документ не попадает ни в одну ветку step-01» ловится тестом на полноту покрытия состояний.

4. **Append-only сборка + один санкционированный rewrite с Must-Preserve.** Документ только дописывается → LLM не может «переписать всё и потерять половину»; цена (дубли, швы) честно компенсируется единственным шагом полировки со списком неприкосновенного. Отдельно ценен step-11 §2b: автор знает, что структурный шаблон **систематически** выкидывает «мягкие» идеи (tone, philosophy, feel), и ставит сверку с исходным brainstorming. Перенос: append-only как инвариант воркера + diff-проверка polish-шага кодом («Must-Preserve строки присутствуют после rewrite»).

5. **Гейты «разрешение не делать».** Skip домена при низком риске, легитимация «инноваций нет» («many successful products are excellent executions of existing concepts»), категория «Intentionally Excluded» в coverage-чеке, наказание за **лишние** секции (excluded sections в v-09). Это системная защита от главной болезни LLM — заполнять каждую секцию шаблона контентом ради контента. Перенос: в каждом чеклисте нашего пайплайна явный легитимный исход «N/A с обоснованием», отличимый от «пропущено».

6. **Контракт для слепого потребителя («CAPABILITY CONTRACT»).** Step-09 проговаривает то, что обычно неявно: downstream-агенты не увидят разговора — только текст FR, поэтому «if a capability is missing from FRs, it will NOT exist in the final product», плюс операционализированный тест абстракции («реализуемо 5 разными способами?») и числовой бюджет (20-50 FR). Перенос: каждый межфазный артефакт писать как API для агента без контекста, с явным «binding»-предупреждением человеку и бюджетами размера.

7. **Писатель без читателя = мёртвый контракт (анти-урок workflow_status).** Step-12 пишет `workflow_status['prd']`, читателей в кодовой базе ноль — bmad-help определяет готовность иначе (по файлам). Раскопки приняли это за рабочую фичу — то есть мёртвый шов неотличим от живого без grep'а потребителей. Перенос: для каждой записи state — тест «кто это читает»; для каждого читателя — «кто это пишет» (наш урок producer≠consumer, пойманный уже трижды). Туда же: три поколения счётчика шагов «of 11 / of 12 / of 13» и двойная бухгалтерия переходов — любой факт, повторённый в N местах прозой, разъедется; единственный писатель + генерация остальных мест из него.