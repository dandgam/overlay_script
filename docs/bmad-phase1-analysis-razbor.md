# BMAD Phase 1 (Analysis) изнутри: как метод превращает LLM в дисциплинированного аналитика

Это разбор первой фазы BMAD METHOD v6 (версия из материала — 6.2.2) — фазы Analysis. Если Phase 4 (которую вы уже разбирали) — это «фабрика кода» с воркерами и гейтами, то Phase 1 — это «комната переговоров»: здесь LLM не пишет код, а помогает человеку *думать* — брейнштормить, исследовать рынок и превращать сырую идею в документ, с которого начнётся весь проект. Самое интересное в Phase 1 — не «что» она делает, а «как» её промпты удерживают LLM от двух главных грехов: выдумывания фактов и работы вместо человека.

Все пути ниже — из реальной инсталляции: `/home/server/Downloads/crm/_bmad/` (модули `core/` и `bmm/`, рабочие воркфлоу фазы в `bmm/1-analysis/`).

---

## 1. Карта Phase 1: из чего состоит

### 1.1 Главный факт: вся фаза — опциональна

В `module-help.csv` (декларативная карта всего метода, об этом ниже) **все 5 скиллов Phase 1 имеют `required=false`**. Первый обязательный шаг метода — `bmad-create-prd`, и он уже в Phase 2 (Planning). То есть BMAD говорит: «можешь пропустить весь анализ и сразу писать PRD — но если идея сырая, вот тебе инструменты её доварить». Правило из `bmad-help`: «A phase with no required items is entirely optional».

### 1.2 Таблица компонентов

| Компонент | Где лежит | Что делает | Обязателен? | Место в порядке |
|---|---|---|---|---|
| **bmad-agent-analyst («Mary»)** | `bmm/1-analysis/bmad-agent-analyst/` | Входная точка фазы: агент-персона Business Analyst с меню из 6 кодов, маршрутизирует во все скиллы фазы | нет | Точка входа |
| **bmad-brainstorming** | `_bmad/core/bmad-brainstorming/` | Интерактивная брейншторм-сессия: 61 техника, цель 100+ идей, документ-результат | нет | Обычно первый шаг (manifest брифа: `after: brainstorming`) |
| **research/** — 3 воркфлоу | `bmm/1-analysis/research/` | bmad-market-research (клиенты/конкуренты), bmad-domain-research (отрасль/регуляторика), bmad-technical-research (технологии/архитектура) | нет | Параллельно/после брейншторма, до брифа |
| **bmad-product-brief** | `bmm/1-analysis/bmad-product-brief/` | Главный выход фазы: executive brief 1-2 страницы + LLM-дистиллят для PRD | нет (но `is-required: true` в собственном манифесте как capability) | Финал фазы: `after: [brainstorming, perform-research]`, `before: [create-prd]` |
| **bmad-document-project** | `bmm/1-analysis/bmad-document-project/` | Brownfield-документация существующего кода: сканы 3 уровней, resume-механика | нет; в `module-help.csv` его phase = «anytime» | Вне строгого порядка |
| **bmad-agent-tech-writer («Paige»)** | `bmm/1-analysis/bmad-agent-tech-writer/` | Агент-персона технического писателя: документы, Mermaid-диаграммы, валидация доков | нет | Сервисный агент |

Под фазой лежит инфраструктура из `core/`, без которой она не работает:

| Инфраструктура | Где | Роль |
|---|---|---|
| **bmad-init** | `core/bmad-init/` (SKILL.md + `scripts/bmad_init.py`) | Единственный скилл с реальным Python-кодом: загружает/создаёт конфиг, отдаёт переменные `{user_name}`, `{planning_artifacts}` и т.д. |
| **config.yaml** ×2 | `core/config.yaml`, `bmm/config.yaml` | Реальные значения: project_name: crm, communication_language: Russian, planning_artifacts: `{project-root}/_bmad/planning-artifacts`, project_knowledge: `{project-root}/docs` |
| **module-help.csv + bmad-help** | `bmm/module-help.csv`, `core/bmad-help/` | Декларативный DAG (граф зависимостей шагов) всего метода: фазы, after/before, required, outputs |
| **bmad-advanced-elicitation** | `core/bmad-advanced-elicitation/` | 50 методов углубления/критики в 11 категориях; вызывается из брейншторма опцией [A] |
| **bmad-distillator** | `core/bmad-distillator/` | Универсальный «сжиматель» документов для LLM (lossless compression, not summarization) |

### 1.3 Порядок (как его задаёт сам метод)

Порядок зашит не в код, а в **данные** — манифесты и `module-help.csv`:

```
Mary (меню) ──► brainstorming ─┐
              ──► research ×3  ─┼──► product-brief ──► [Phase 2] create-prd (первый required)
              ──► document-project (anytime) ─┘
```

`bmad-manifest.json` продукт-брифа: `after: ["brainstorming, perform-research"]`, `before: ["create-prd"]`. Любопытный баг данных: `after` — это массив из **одной** строки с запятой внутри, а не из двух элементов. Работает, потому что читает это LLM, а не парсер — но это типичный пример «данных, которые никто не валидирует» (мы к этому вернёмся в уроках).

---

## 2. Сквозные принципы: как BMad управляет LLM

Это самая ценная часть для переноса в свои пайплайны. Каждый принцип — с цитатой из файлов и переводом на бытовой язык.

### 2.1 Шаги в отдельных файлах (micro-file architecture)

**Как устроено.** `workflow.md` брейншторма только задаёт роль и пути, а дальше: «Read fully and follow: ./steps/step-01-session-setup.md». Каждый шаг — самодостаточный файл со своими правилами, и заглядывать вперёд запрещено: «Don't preload technique information or look ahead to execution steps!», «Don't assume knowledge from other steps».

**Зачем.** LLM, получив весь сценарий сразу, начинает «срезать путь»: пропускает вопросы, сливает шаги, заранее формирует вывод. Дозирование инструкций по одному файлу делает срезание физически невозможным — модель просто не знает, что будет дальше. Бытовая аналогия: квест-комната, где конверт с заданием №2 выдают только после выполнения №1.

### 2.2 Фасилитатор, не генератор

**Как устроено.** В step-01 брейншторма капсом: «🛑 NEVER generate content without user input — YOU ARE A FACILITATOR, not a content generator». В product-brief: «The user is the domain expert... Work together as equals». Нюанс: в step-03 агенту разрешено со-творчество («Let me build on your idea: [Extend concept with your own creative contribution]») — но только **в ответ** на вклад пользователя, никогда вместо него.

**Зачем.** Фаза анализа существует ради извлечения знаний из головы человека. Если LLM сам нагенерит «идеи» и «требования», на выходе будет правдоподобный мусор, на котором потом построят PRD и код. Принцип: агент держит микрофон, а не поёт сам.

### 2.3 Чекпойнты: жёсткие HALT и мягкие гейты

**Как устроено.** Два режима. Жёсткий — в брейншторме и research: «HALT — wait for user selection before proceeding», «🚫 FORBIDDEN to load next step until C is selected» — каждое меню `[C] Continue` останавливает воркфлоу намертво (10+ HALT-точек в брейншторме). Мягкий — в product-brief: «Anything else you'd like to add, or shall we move on?» — пауза-предложение, а не блокировка; в SKILL.md прямо объяснено зачем: этот вопрос «consistently draws out additional context users didn't know they had».

**Зачем.** Жёсткие гейты — там, где LLM склонен убегать вперёд (генерация контента). Мягкие — там, где важен темп разговора. Это как шлагбаум против «лежачего полицейского»: оба замедляют, но по-разному.

### 2.4 Elicitation-меню (выбор пользователя как механика)

**Как устроено.** Каждая развилка — нумерованное меню: «[1] User-Selected Techniques [2] AI-Recommended [3] Random [4] Progressive Flow» (step-01 брейншторма); «[C] Continue / [Modify] / [Details] / [Back]»; финал техники — «[K] Keep exploring / [T] Try different / [A] Go deeper / [B] Break / [C] Move to organization». В `bmad-advanced-elicitation` выбор метода буквой/цифрой — контракт встраивания: режим INTEGRATION улучшает только что сгенерированную секцию и возвращает enhanced-версию по выбору пользователем 'x'.

**Зачем.** Меню превращает расплывчатое «что дальше?» в конечный набор кнопок: пользователю не нужно формулировать, LLM не нужно угадывать. Плюс каждое меню — это и есть точка HALT.

### 2.5 Анти-галлюцинация через принудительный сорсинг

**Как устроено.** Три разных механизма под одну цель:
- **Данные только из файла:** техники брейншторма — только из `brain-methods.csv`; «❌ Loading techniques from memory instead of CSV» — прямо названный failure mode.
- **Факты только из веба:** research-воркфлоу начинаются с «⛔ Web search required. If unavailable, abort and tell the user», а каждый рабочий шаг несёт «🛑 NEVER generate content without web search verification» + «Always cite URLs» + «_Source: [URL]_» под каждой секцией. Failure mode: «Relying solely on training data without web verification».
- **Запрет фабрикации в выводах:** шаблон брифа — «Be honest — if the moat is execution speed, say so. Don't fabricate technical moats»; skeptic-reviewer целево ищет «Where does the brief assert things without evidence?».

**Зачем.** Заметьте: это не запреты постфактум («не выдумывай»), а конструкция, где факту просто неоткуда взяться, кроме как из источника. Как в суде: не «обещай говорить правду», а «приложи документ».

### 2.6 Состояние на диске, не в контексте

**Как устроено.** Прогресс каждой сессии живёт в YAML-frontmatter (служебная шапка в начале файла) выходного документа: `stepsCompleted: [1] → [1,2] → [1,2,3,4]`, `techniques_used`, `selected_approach`, в финале `workflow_completed: true`. Документ строится append-only (только дописывание). Продолжение сессии (`step-01b-continue.md`) восстанавливается из файла, а не из памяти модели. У document-project — отдельный state-файл `{project_knowledge}/project-scan-report.json` с resume-меню и автоархивацией состояния старше 24 часов в `.archive/`.

**Зачем.** Контекст LLM теряется при обрыве сессии, диск — нет. Любой новый процесс может открыть файл и продолжить с точного места.

### 2.7 Шаблоны как контракты

**Как устроено.** Каждый воркфлоу стартует с копирования шаблона: `cp template.md → output file` (брейншторм), «exact copy» `research.template.md` (research). `brief-template.md` задаёт 8 секций (Executive Summary → Problem → Solution → What Makes This Different → Who This Serves → Success Criteria → Scope → Vision), но с оговоркой: «The product determines the structure, not the template» + таблица адаптаций (B2B → секция Buyer vs User, regulated → Compliance, и т.д.). А каждый step-файл несёт двойной чеклист: SUCCESS METRICS (✅ что должно случиться) и FAILURE MODES (❌ что считается провалом) — LLM получает и позитивный, и негативный контур поведения.

**Зачем.** Шаблон фиксирует *форму* результата заранее, а пара ✅/❌ работает как должностная инструкция с описанием не только обязанностей, но и нарушений.

### 2.8 Субагенты-рецензенты: adversarial review ДО человека

**Как устроено.** В product-brief черновик до показа пользователю проходит 3 линзы: **Skeptic Reviewer** (субагент: «What's missing? What assumptions are untested?»), **Opportunity Reviewer** (субагент: «What's the bigger story? What would an investor want to hear more about?») и **Contextual Reviewer** — главный агент сам выбирает третью линзу под продукт: «the lens that addresses the SINGLE BIGGEST RISK that the skeptic and opportunity reviewers won't naturally catch» (healthtech → regulatory, marketplace → chicken-and-egg, дефолт → go-to-market). Дальше триаж: «Apply non-controversial improvements directly... Flag substantive suggestions that need user input» — косметика правится автономно, стратегия — только через человека.

**Зачем.** Это редколлегия перед публикацией: текст вычитывают критик и оптимист, и читателю показывают уже исправленную версию плюс короткий список спорных вопросов — а не весь сырой выхлоп рецензентов.

### 2.9 Строгие выходные контракты субагентов

**Как устроено.** Все 4 субагента product-brief заканчиваются одной формулой: «Return ONLY the following JSON object. No preamble, no commentary. Maximum 8 bullets per section» (у ревьюеров — max 5 items, «Focus on the 2-3 most impactful opportunities», «lead with the most impactful issues»).

**Зачем.** LLM любит болтать; числовые лимиты + «только JSON» дают предсказуемый, парсимый и приоритизированный результат. Headless-выход всего воркфлоу — тоже контракт: `{status, brief, distillate, confidence: high|medium|low, open_questions: []}` — с самооценкой уверенности и явным списком нерешённого вместо молчаливой уверенности.

### 2.10 Токен-дисциплина (контекст — дефицитный ресурс)

**Как устроено.** Везде, последовательно:
- При детекте старых сессий — «ТОЛЬКО имена файлов; DO NOT read any file contents — wastes context».
- CSV с техниками грузится только в момент показа.
- Artifact-analyzer: sharded-документы (большой документ, нарезанный на части с index.md) — сначала index, потом только релевантные части; документы >50 страниц — TOC + executive summary + заголовки, с пометкой «skimmed vs read fully»; «Ignore documents that aren't relevant... Don't waste tokens».
- document-project: архитектура **write-as-you-go** — каждый документ пишется на диск сразу, а детальные находки «purged from context after writing (only summaries kept)».

**Зачем.** Контекст — как оперативная память: всё, что можно не держать в голове, сбрасывается на диск или не читается вовсе.

### 2.11 Graceful degradation (деградация без остановки)

**Как устроено.** В product-brief — отдельные одноимённые секции: «Never block the workflow because a subagent feature is unavailable». Fallback прописан заранее: вместо субагентов — прочитать inline 1-2 самых релевантных документа (не все!), сделать несколько таргетных поисков; в ревью — три прохода самому, последовательно: «Apply each lens deliberately — don't blend them into one generic review».

**Зачем.** Запасной план — часть спецификации, а не импровизация в момент сбоя. Причём fallback тоже ограничен («limit context impact in degraded mode»), чтобы деградация не съела контекст.

### 2.12 Анти-bias стек против врождённых слабостей LLM

**Как устроено.** В step-03 брейншторма — четыре независимых механизма против банальности:
1. **ANTI-BIAS DOMAIN PIVOT** — каждые 10 идей принудительный поворот в ортогональный домен (UX → Business → Physics → Social Impact) — против semantic clustering (LLM кучкует похожие идеи).
2. **THOUGHT BEFORE INK (CoT)** — перед каждой идеей внутреннее рассуждение: «What domain haven't we explored yet? What would make this idea surprising or uncomfortable for the user?».
3. **SIMULATED TEMPERATURE** — «🌡️ Act as if your creativity is set to 0.85 - take wilder leaps and suggest "provocative" concepts» — промпт-эмуляция высокой температуры сэмплинга (параметр случайности генерации).
4. Поле **Novelty** в формате идеи — каждая идея обязана объяснить, чем она отличается от очевидного.

**Зачем.** Это компенсаторы: разработчики BMAD знают типовые сбои LLM-генерации и ставят против каждого свой механизм.

### 2.13 Языковой контракт

**Как устроено.** Каждый файл дублирует: «YOU MUST ALWAYS SPEAK OUTPUT In your Agent communication style with the communication_language». При этом язык диалога (`communication_language` — в этом проекте Russian) и язык документов (`document_output_language`) — две разные переменные конфига.

**Зачем.** Разговаривать можно по-русски, а артефакты писать по-английски (или наоборот) — и это решение конфига, а не настроения модели.

---

## 3. Пошаговый разбор компонентов

### 3.1 bmad-brainstorming — брейншторм-фасилитатор

**Суть:** LLM ведёт человека через проверенные техники креативности, удерживая в «генеративном режиме» как можно дольше (цель — 100+ идей: «the magic happens in ideas 50-100»), и фиксирует всё в append-only документ. Знаменитая цитата воркфлоу: «The best brainstorming sessions feel slightly uncomfortable».

| Шаг | Файл | Что происходит | Роль человека |
|---|---|---|---|
| 0. Активация | `SKILL.md` | 6 строк: триггер («help me brainstorm / ideate») + «Follow the instructions in ./workflow.md» | Произносит триггер |
| 1. Инициализация | `workflow.md` | Роль фасилитатора, Critical Mindset («Resist the urge to organize or conclude»), Anti-Bias Protocol, Quantity Goal 100+, загрузка `core/config.yaml`, вычисление пути выходного файла | Нет (всё общение далее — на communication_language) |
| 2. Шаблон | `template.md` | Скелет документа: frontmatter-состояние (stepsCompleted, session_topic, techniques_used, ideas_generated) + заголовок; копируется `cp` в выходной файл | Нет |
| 3. Setup | `steps/step-01-session-setup.md` | Листинг старых сессий (только имена!), меню Continue/New; fresh: 2 discovery-вопроса → Session Analysis → подтверждение → меню 4 путей выбора техник + HALT | Отвечает «о чём штормим» и «какой результат нужен»; подтверждает; выбирает путь 1-4 |
| 3b. Продолжение | `steps/step-01b-continue.md` | «CONTINUATION FACILITATOR»: читает документ, статус-сводка из frontmatter, резюме и возврат на нужный шаг; «🚫 FORBIDDEN repeating completed work» | Выбирает Review/New/Extend/Resume |
| 4a. Путь 1 | `steps/step-02a-user-selected.md` | Роль «TECHNIQUE LIBRARIAN, not a recommender»: показывает категории и карточки техник из CSV нейтрально, «FORBIDDEN making recommendations or steering choices» | Сам выбирает категорию и техники |
| 4b. Путь 2 | `steps/step-02b-ai-recommended.md` | Роль «TECHNIQUE MATCHMAKER»: 4-мерный анализ (Goal / Complexity / Energy-Tone / Time) → последовательность техник, каждая с «Why this fits»; «FORBIDDEN generic recommendations without context analysis» | Подтверждает / [Modify] заменяет техники |
| 4c. Путь 3 | `steps/step-02c-random-selection.md` | Роль «SERENDIPITY FACILITATOR»: «intelligent random» — 3 техники из разных категорий, без конфликтов; «FORBIDDEN steering random selections» | [C] / [Shuffle] — новая случайная комбинация |
| 4d. Путь 4 | `steps/step-02d-progressive-flow.md` | Роль «CREATIVE JOURNEY GUIDE»: 4-фазный маршрут от дивергентного к конвергентному мышлению (Exploration → Pattern Recognition → Development → Action Planning) с Journey Map; «FORBIDDEN jumping ahead to later phases» | [C] / [Customize] фаз и тайминга |
| 5. Исполнение (ядро) | `steps/step-03-technique-execution.md` | Живой коучинг: один элемент техники за раз (SCAMPER — по одной букве), три анти-bias механизма, фиксация идей по IDEA FORMAT TEMPLATE, Energy Checkpoint каждые 4-5 обменов, escape-фраза «next technique» в любой момент, финальное меню [K/T/A/B/C] | Генерит идеи; агент достраивает только в ответ; решает когда хватит — «DEFAULT IS TO KEEP EXPLORING» |
| 6. Организация | `steps/step-04-idea-organization.md` | Роль «IDEA SYNTHESIZER»: кластеризация в 3+ темы, приоритизация по Impact/Feasibility/Innovation/Alignment, action-планы (Next Steps / Resources / Timeline / Success Indicators / Obstacles); «FORBIDDEN workflow completion without action planning» | Сам называет приоритеты; агент даёт только фреймворк |
| 7. Данные | `brain-methods.csv` | 61 техника в 10 категориях, 3 колонки: category, technique_name, description | Не видит напрямую — видит карточки |

**Условия конвергенции** (когда можно переходить к организации): пользователь явно попросил, ИЛИ 45+ минут И 100+ идей, ИЛИ энергия явно иссякла. «AI initiating conclusion without user explicitly requesting it» — failure mode.

**Методы и фреймворки (что это, по одной строке):**
- **SCAMPER** — 7 линз модификации идеи: Substitute (заменить) / Combine / Adapt / Modify / Put to other uses / Eliminate / Reverse.
- **Six Thinking Hats** — обсуждение с 6 «шляп»-перспектив по очереди: факты, эмоции, выгоды, риски, креатив, процесс — чтобы перспективы не конфликтовали.
- **Five Whys** — спрашивать «почему это случилось?» 5 раз подряд, пока не докопаешься до корневой причины.
- **Mind Mapping** — ветвление идей от центрального понятия, как дерево.
- **Question Storming** — сначала генерить только вопросы, без ответов — чтобы решать правильную проблему.
- **Reverse Brainstorming / Anti-Solution** — «как сделать проблему хуже?» — из анти-ответов вычитываются решения.
- **First Principles Thinking** — забыть «как принято» и пересобрать решение от базовых истин.
- **Analogical Thinking** — «на что это похоже?» — перенос рабочих паттернов из других областей.
- **Resource Constraints** — экстремальные лимиты («only $1, no technology, one hour») как катализатор изобретательности.
- Экзотика для расширения охвата: **biomimetic** (как решила бы природа — «3.8 billion years of evolutionary wisdom»), **quantum** (держать все варианты «в суперпозиции» до коллапса в оптимум), **cultural** (мифы, ритуалы, фьюжн культурных подходов), **theatrical** (Alien Anthropologist — взгляд инопланетянина на привычное), **wild** (Zombie Apocalypse Planning — выживание стрипует идею до ядра ценности), **introspective_delight** (Future Self Interview — совет от 80-летнего себя).
- **Дивергентное → конвергентное мышление** — классическая модель: сначала максимум вариантов без оценки, потом систематическое сужение к действию; зашита и в путь 4, и в пару step-03/step-04.

**Найденный drift (расхождение промптов и данных)** — поучительный: step-файлы заявляют «36+ techniques across 7 categories», в CSV реально 61 техника и 10 категорий (biomimetic/quantum/cultural в меню step-02a вообще не показаны); промпты требуют парсить колонки `facilitation_prompts, best_for, energy_level, typical_duration` — которых в CSV **нет** (только 3 колонки). Duration/Energy в карточках агент вынужден сочинять — единственная точка узаконенной импровизации. Вывод: данные и промпты эволюционировали порознь, и никакой тест это не ловит.

---

### 3.2 bmad-product-brief — главный выход фазы

**Суть:** превращает сырую идею в executive brief на 1-2 страницы + опциональный «LLM distillate» (токен-эффективный пакет деталей для PRD). Роль: «product-focused Business Analyst and peer collaborator»; пользователь — доменный эксперт. Три режима: **guided** (диалог, дефолт), **yolo** (`--yolo` / «just draft it»: сначала черновик, потом доработка), **autonomous/headless** (`--autonomous`/`-A`: вообще без человека, JSON на выходе).

| Шаг | Файл | Что происходит | Роль человека |
|---|---|---|---|
| Stage 0 | `SKILL.md` | Детект режима (guided/yolo/autonomous), загрузка `bmm/config.yaml`, резолв 5 переменных, приветствие по имени («dream builder energy») | Получает приветствие (в autonomous — ничего) |
| Stage 1 — Understand Intent | `SKILL.md` | Понять ЗАЧЕМ и О ЧЁМ бриф **до любого чтения файлов**: детект типа (product / internal tool / research — для некоммерческого фокус смещается на «stakeholder value and adoption path»); развод нескольких идей по отдельным сессиям; update-режим (прочитать старый бриф полностью, спросить «What's changed?»); brain dump — «capture everything» | Выговаривается; на каждой паузе — «Anything else?» |
| Stage 2 — Contextual Discovery | `prompts/contextual-discovery.md` | Параллельный fan-out (одновременный запуск) 2 субагентов с передачей intent summary; затем synthesis: merge с рассказом пользователя, identify gaps, note surprises (что из ресёрча противоречит предположениям) | Guided: слушает резюме находок, soft gate; yolo/headless: ничего |
| Stage 2a | `agents/artifact-analyzer.md` | «Research analyst»: сканирует `{planning_artifacts}` и `{project_knowledge}` по паттернам имён (*brainstorm*, *research*, *brief*...), sharded/large-стратегия чтения, «Read all relevant documents in parallel»; извлекает в т.ч. rejected ideas («rejected ideas are valuable — they prevent re-proposing») | Нет |
| Stage 2b | `agents/web-researcher.md` | «Market research analyst»: 3-5 таргетных поисков по шаблонам («[problem domain] solutions comparison», «[industry] market trends [current year]»...), «Synthesize findings — don't just list links. Extract the signal» | Нет |
| Stage 3 — Guided Elicitation | `prompts/guided-elicitation.md` | ТОЛЬКО guided: умные вопросы по гэпам, «mental checklist, not a script»; 4 топик-области (Vision & Problem / Users & Value / Market & Differentiation / Success & Scope); микроцикл The Flow; анти-перфекционизм: «You don't need perfection — you need enough to draft well» | Отвечает; уверенные ответы → проактивное предложение драфтить раньше |
| Stage 4.1 — Draft | `prompts/draft-and-review.md` + `resources/brief-template.md` | Черновик по 8-секционному шаблону, «Lead with the problem — make the reader feel the pain», 1-2 страницы («If it's longer... that's what the distillate is for»); файл с frontmatter `status: draft` + `inputs:` (provenance — список использованных входов) | Нет — черновик идёт на ревью ДО показа |
| Stage 4.2 — Review fan-out | `draft-and-review.md` + `agents/skeptic-reviewer.md` + `agents/opportunity-reviewer.md` | 3 линзы: Skeptic (гэпы/допущения/риски), Opportunity («value the brief is leaving on the table»), Contextual (доменная, под SINGLE BIGGEST RISK, inline) | Нет |
| Stage 4.3 — Triage | `draft-and-review.md` | Группировка по темам, дедуп; бесспорное — правится автономно; стратегическое — флагуется человеку | Нет |
| Stage 4.4 — Present | `draft-and-review.md` | Показ драфта + только субстантивных находок: «my review panel surfaced some things worth considering...»; итерации «as long as the user wants» | Решает стратегические вопросы; «happy with this?» |
| Stage 5.1 — Polish | `prompts/finalize.md` | frontmatter `status: complete`, форматирование, проверка читабельности standalone | Нет |
| Stage 5.2 — Distillate | `finalize.md` | Оффер дистиллята с **обязательными 2-3 конкретными примерами** перехваченного overflow (доказательство, что capture реально был); 7 групп: rejected ideas, requirements hints, technical context, user scenarios, competitive intelligence, open questions, scope signals; headless — создаётся всегда (кроме пустого — «note... instead of creating an empty file») | Соглашается/нет |
| Stage 5.3 — Completion | `finalize.md` | Пути к файлам + handoff: «tell your assistant 'create a PRD' and point it to these files»; headless: JSON `{status, brief, distillate, confidence, open_questions}` | Может запросить ещё правок (цикл назад в draft-and-review) |

**Методы и фреймворки:**
- **Intent-first discovery** — «DO NOT read document files yet»; rationale дословно: «without knowing what the brief is about, scanning documents is noise, not signal» — сначала цель, потом данные.
- **Subagent fan-out** — тяжёлое чтение и веб-поиск выносятся в параллельные субагенты с компактным JSON-возвратом — основное окно не засоряется.
- **Capture-don't-interrupt** — детали сверх брифа (requirements, платформы, сроки) фиксируются молча («Good detail, I'll capture that»), не перебивая поток — потом выгружаются в дистиллят.
- **Двухартефактная модель** — бриф 1-2 стр. для людей + дистиллят для downstream-LLM: одна беседа, две аудитории.
- **The Flow** — 4-шаговый микроцикл элиситации: (1) «Lead with what you know» — «Based on your input and my research, it sounds like [X]. Is that right?» (гипотеза на верификацию, не допрос) → (2) gap question → (3) reflect and confirm → (4) soft gate.
- **Multi-lens adversarial review** — см. принцип 2.8; плюс доменная таблица линз (healthtech→regulatory, devtools→DX friction, marketplace→chicken-and-egg, enterprise→procurement, default→go-to-market — «Almost always valuable, frequently missed»).
- **Классические PM-вопросы** в чеклисте: **aha moment** (момент, когда пользователь понимает «это оно»), **unfair advantage / defensible moat** (преимущество, которое конкуренту трудно скопировать), **why now** (почему именно сейчас время), **MVP scope** (минимальная версия с реальной ценностью), **2-3-летняя vision**.
- **YAML frontmatter lifecycle** — `status: draft → complete` + `inputs:` — машиночитаемый статус и происхождение данных.

---

### 3.3 research/ — три исследовательских воркфлоу

**Суть:** клоны одного micro-file паттерна (SKILL.md → workflow.md → template → 6 step-файлов), различаются предметом. Жёсткий prerequisite — единственный в Phase 1 обязательный внешний инструмент: «⛔ Web search required. If unavailable, abort and tell the user». Выход: `{planning_artifacts}/research/<type>-{{topic}}-research-{{date}}.md`.

**Как выбирать:** рынок/клиенты/конкуренты для бизнес-решения → **market**; отрасль целиком, включая регуляторику и value chain → **domain**; сравнение технологий и архитектура → **technical**.

Общий паттерн рабочего шага: роль-аналитик → 3-4 веб-поиска (часто параллельных, «UTILIZE SUBPROCESSES AND SUBAGENTS») → агрегация с confidence levels (уровень уверенности в данных) → запись секций в документ с «_Source: [URL]_» → `[C] Continue` + HALT.

**MARKET (клиенты и конкуренты):**

| Шаг | Файл | Что происходит | Роль человека |
|---|---|---|---|
| Вход | `SKILL.md` + `workflow.md` | Роль «market research facilitator working with an expert partner», discovery темы | «What topic... do you want to research?» + Core Topic / Goals / Scope |
| 1. Init | `steps/step-01-init.md` | ТОЛЬКО скоуп: «🛑 NEVER generate research content in init step», «🔍 NO WEB RESEARCH in init»; scope-документ сразу в файл; единственный init с веткой [Modify] | Подтверждает скоуп: сегменты, география, цель |
| 2. Customer Behavior | `step-02-customer-behavior.md` | «CUSTOMER BEHAVIOR ANALYST»: 4 параллельных поиска (behavior patterns / demographics / psychographic profiles / drivers) → секции: сегментация, психографика, 3 профиля сегментов, драйверы | [C] |
| 3. Pain Points | `step-03-customer-pain-points.md` | «CUSTOMER NEEDS ANALYST»: frustrations / unmet needs / barriers to adoption (цена, доверие, удобство) / satisfaction gaps / приоритизация High-Med-Low + Opportunity Mapping | [C] |
| 4. Decisions & Journey | `step-04-customer-decisions.md` | «CUSTOMER DECISION ANALYST»: процесс решения, критерии, journey по 5 стадиям (Awareness → Consideration → Decision → Purchase → Post-Purchase), touchpoints, инфлюенсеры | [C] |
| 5. Competitive | `step-05-competitive-analysis.md` | «COMPETITIVE ANALYST»: игроки, доли, позиционирование, «[SWOT analysis with source citations]», угрозы и возможности. Отличие: контент пишется только ПОСЛЕ [C] | [C] |
| 6. Completion | `step-06-research-completion.md` | «MARKET RESEARCH STRATEGIST»: +2 поиска (entry strategies, risk frameworks) → ПОЛНЫЙ документ: Executive Summary, TOC, Market Size/CAGR, рекомендации, Go-to-Market, риски, roadmap+KPI, прогноз 1-2/3-5/5+ лет, Methodology (полный список запросов!), Appendices | Финальное [C] |

**DOMAIN (отрасль):** тот же каркас; шаги: scope confirmation → **Industry Analysis** (размер, CAGR, зрелость, барьеры — лексика Five Forces без упоминания Porter) → **Competitive Landscape** (Cost Leadership / Differentiation / Focus — генерические стратегии Портера по содержанию, имя не названо; экосистемы, M&A) → **Regulatory Focus** — уникальный шаг, которого нет у двух других: роль «REGULATORY ANALYST», 3 последовательных поиска, GDPR/CCPA названы явно, особый SOURCE VERIFICATION-блок («Always cite regulatory agency websites», «Note effective dates») → **Technical Trends** (AI/ML, automation, disruption) → **Synthesis** (Cross-Domain Synthesis: «Market-Technology Convergence», «Regulatory-Strategic Alignment»).

**TECHNICAL (технологии):** scope confirmation → **Technology Stack Analysis** (языки, фреймворки, БД: SQL/NoSQL/In-Memory, облака: AWS/Azure/GCP, Docker/Kubernetes, Serverless) → **Integration Patterns** — самый насыщенный именованными паттернами шаг → **Architectural Patterns** (роль «SYSTEMS ARCHITECT»; 3 generic-поиска даже без {{research_topic}} в запросе) → **Implementation Research** («IMPLEMENTATION ENGINEER»: adoption strategies, CI/CD, DevOps, команда, стоимость) → **Synthesis** (роль «TECHNICAL RESEARCH STRATEGIST»; детали этого шага в моём материале обрезаны — по общей архитектуре он зеркалит synthesis market/domain, но утверждать конкретные секции не могу).

**Словарик упомянутых терминов (по 1 строке):**
- **CAGR** — среднегодовой темп роста рынка в процентах.
- **SWOT** — таблица «сильные/слабые стороны, возможности, угрозы».
- **Customer journey** — путь клиента от «узнал» до «купил и пользуется», по стадиям.
- **Value chain** — цепочка создания ценности: кто что добавляет от сырья до клиента.
- **REST / GraphQL / gRPC** — три стиля API (способа общения программ): простые адреса-запросы / гибкие запросы «дай ровно эти поля» / быстрые бинарные вызовы.
- **Webhook** — «обратный звонок»: сервис сам стучится к вам при событии.
- **API Gateway** — единая входная дверь ко множеству внутренних сервисов.
- **Service Mesh** — прослойка, управляющая общением микросервисов между собой.
- **Circuit Breaker** — «предохранитель»: при сбоях сервиса перестаём его дёргать, чтобы не уронить всё.
- **Saga** — способ провести длинную операцию через несколько сервисов с откатами при сбое.
- **Pub-Sub** — «подписка на новости»: отправитель публикует событие, получатели подписаны.
- **Event Sourcing** — хранить не текущее состояние, а журнал всех событий, из которого оно восстанавливается.
- **CQRS** — разделение «писать данные» и «читать данные» на разные пути.
- **OAuth 2.0 / JWT / Mutual TLS** — стандарты «кто ты такой»: делегированный доступ / подписанный пропуск-токен / взаимная проверка сертификатов.
- **SOLID** — 5 классических принципов чистого дизайна кода.
- **Hexagonal / Clean architecture** — стиль архитектуры: бизнес-логика в центре, всё внешнее (БД, UI) — сменные адаптеры.
- **ADR (architectural decision record)** — короткий документ «какое архитектурное решение приняли и почему».
- **CI/CD** — автоматическая сборка-проверка-выкладка кода при каждом изменении.
- **Infrastructure as Code** — серверы и настройки описаны файлами в репозитории, а не руками.
- **GDPR / CCPA** — европейский и калифорнийский законы о персональных данных.

**Найденные баги research-файлов** (важно для уроков): market step-06 велит ставить `stepsCompleted: [1,2,3,4]` вместо [1..6]; domain step-06 ссылается «regulatory from step-03, trends from step-04» (реально 04 и 05), а competitive (step-03) в списке синтеза вообще пропущен, финальный frontmatter [1,2,3,4,5] вместо [1..6]; опечатка «Evoluton Patterns» в market step-04; асимметрия записи: в шагах 2-4 «WRITE CONTENT IMMEDIATELY», а в шаге 5 запись до [C] — failure mode. LLM такие баги обычно «прощает» — но это везение, а не дизайн.

---

### 3.4 bmad-document-project — документирование существующего кода (brownfield)

**Суть:** крупнейший воркфлоу для случая «проект уже есть, но не описан». Лежит в `1-analysis/`, но по `module-help.csv` его phase = «anytime». Состав: `workflow.md`, `instructions.md`, `checklist.md`, `documentation-requirements.csv`, `templates/` (5 файлов), `workflows/` (4 файла).

| Механика | Файл | Что происходит | Роль человека |
|---|---|---|---|
| Роутер + resume | `instructions.md` | State-файл `{project_knowledge}/project-scan-report.json`; если есть — меню Resume / Start fresh / Cancel; state старше 24 часов автоархивируется в `.archive/`; при resume — CONDITIONAL CSV LOADING: грузится только строка кэшированного project_type | Выбирает Resume/Fresh/Cancel |
| Полный скан | `workflows/full-scan-workflow.md` | Режимы initial_scan / full_rescan; **3 уровня скана** (full-scan-instructions.md:117-128): **quick** — паттерны/конфиги/манифесты, исходники НЕ читаются; **deep** — чтение критических директорий по типу проекта; **exhaustive** — все исходники кроме node_modules/dist/build | Материал не уточняет, как именно выбирается уровень — честно: не знаю |
| Точечный скан | `workflows/deep-dive-workflow.md` | Глубокая документация одного места, всегда scan_level=exhaustive | — |
| Матрица «что сканировать» | `documentation-requirements.csv` | **11 типов проектов × 24 колонки** паттернов (web, mobile, backend, cli...): key_file_patterns, auth_security_patterns, schema_migration_patterns, monorepo_workspace_patterns и т.д. — детерминированная таблица вместо импровизации LLM | Нет |
| Анти-переполнение контекста | `checklist.md` | Архитектура **write-as-you-go**: каждый документ пишется на диск сразу, детальные находки «purged from context after writing (only summaries kept)», батчинг по подпапкам | Нет |
| Шаблоны | `templates/` | 5 шаблонов, включая `project-scan-report-schema.json` — формальная JSON-схема state-файла | Нет |

Пошаговой раскадровки диалога для этого воркфлоу в моём материале нет — выше всё, что подтверждено файлами.

---

### 3.5 Агенты-персоны: Mary и Paige

Это вторая механика BMAD помимо воркфлоу: **агент = персона + меню маршрутизации**.

**bmad-agent-analyst — «Mary»** (`bmm/1-analysis/bmad-agent-analyst/`, SKILL.md + `bmad-skill-manifest.yaml`):

| Аспект | Что в файлах |
|---|---|
| Персона | Business Analyst; communication style «treasure hunter»; в principles — Porter's Five Forces, SWOT, root cause analysis |
| Активация (3 шага) | (1) загрузка конфига через скилл bmad-init; (2) поиск `**/project-context.md` как foundational reference; (3) показ меню + «STOP and WAIT for user input» |
| Меню (6 кодов) | BP→bmad-brainstorming, MR→bmad-market-research, DR→bmad-domain-research, TR→bmad-technical-research, CB→bmad-product-brief-preview, DP→bmad-document-project |
| Анти-выдумывание | «DO NOT invent capabilities on the fly» — меню конечно |
| Persona-контракт | «must not break character until the user dismisses this persona» — персона переносится сквозь вызовы вложенных скиллов |

Найденный drift: CB указывает на `bmad-product-brief-preview`, реальная директория — `bmad-product-brief`, а её манифест говорит `replaces-skill: bmad-create-product-brief` — **три имени одного скилла** в разных местах. Плюс minor: brainstorming имеет menu-code **BSP** в core/module-help.csv, но **BP** в меню Mary и bmm/module-help.csv.

**bmad-agent-tech-writer — «Paige»** (`bmm/1-analysis/bmad-agent-tech-writer/`):

| Аспект | Что в файлах |
|---|---|
| Персона | Technical Documentation Specialist; стандарты: CommonMark, DITA, OpenAPI, Mermaid (текстовый язык диаграмм) |
| 5 capabilities | DP → скилл bmad-document-project; **WD/MG/VD/EC → локальные prompt-файлы** write-document.md / mermaid-gen.md / validate-doc.md / explain-concept.md («prompts are always in the same folder as this skill») |
| Память | По module-help.csv есть capability US (Update Standards) с persistent-памятью в `_bmad/_memory/tech-writer-sidecar` — агент накапливает стандарты письма между сессиями |

Важная деталь: у Paige **две разные механики диспетчеризации** — вызов внешнего скилла (DP) и исполнение локального промпт-файла (WD/MG/VD/EC). То есть «capability» в BMAD — это не обязательно отдельный скилл, иногда это просто файл-инструкция рядом.

---

### 3.6 bmad-advanced-elicitation — углубитель чужих результатов

`core/bmad-advanced-elicitation/` (SKILL.md + methods.csv). Разбор брейншторма упоминает его как опцию [A] «Go deeper» — на деле это самостоятельный core-механизм:

- **50 методов в 11 категориях** (methods.csv: num, category, method_name, description, output_pattern): collaboration 10, advanced 6, core 6, creative 6, risk 5, technical 5, competitive 3, research 3, learning 2, philosophical 2, retrospective 2. Среди известных по триггерам скилла: socratic (вопросы Сократа до сути), first principles, pre-mortem (вообразить провал заранее и найти причины), red team (атаковать собственное решение).
- **Режим INTEGRATION** — контракт встраивания в чужие воркфлоу: при вызове из другого процесса улучшает только что сгенерированную секцию и возвращает enhanced-версию по выбору пользователем 'x'. Это «плагин-критик», который любой воркфлоу может позвать, не зная его внутренностей.
- Frontmatter ссылается на `agent_party: {project-root}/_bmad/_config/agent-manifest.csv` — механика «партии агентов» для методов с несколькими персонами (деталей в материале нет).

---

### 3.7 Инфраструктура, на которой всё стоит

**bmad-init** (`core/bmad-init/`) — конфиг-механика всего метода и **единственный скилл вокруг Phase 1 с реальным Python-кодом** (плюс distillator); всё остальное — чистые промпты:
- **fast path:** `bmad_init.py load --module X` → JSON с переменными;
- **init path:** `check` → статусы no_project / core_missing / module_missing → диалог по вопросам из module.yaml → `resolve-defaults` → `write`.
- `resources/core-module.yaml` определяет канонические вопросы: user_name (default «BMad»), communication_language, document_output_language, output_folder (default `_bmad-output`).
- Это закрывает дыру: в реальных config.yaml проекта **нет** `user_name` и `document_output_language` — их источник именно bmad-init с дефолтами.

**config.yaml** — реальные значения в этой инсталляции: BMAD Version 6.2.2 (файлы генерируются установщиком); bmm: project_name: crm, user_skill_level: intermediate, planning_artifacts: `{project-root}/_bmad/planning-artifacts`, project_knowledge: `{project-root}/docs`, communication_language: Russian, output_folder: `_bmad`.

**module-help.csv + bmad-help** — карта метода:
- CSV-формат `module,skill,...,phase,after,before,required,output-location,outputs` — **декларативный DAG всего метода**: 1-analysis → 2-planning → 3-solutioning → 4-implementation. Порядок фаз — это строки таблицы, а не код.
- Цепочка required=true: bmad-create-prd → create-architecture → create-epics-and-stories → check-implementation-readiness → sprint-planning → create-story → dev-story. Всё в Phase 1 — required=false.
- **bmad-help** — навигатор: читает собранный манифест `{project-root}/_bmad/_config/bmad-help.csv` и детектит завершённость фаз **по наличию артефактов** — сверяет `outputs`-паттерны с файлами в resolved `output-location`: «Artifacts ... reveal which steps are possibly completed». Прогресс определяется не галочками в памяти, а реальными файлами на диске.

**bmad-distillator** (`core/bmad-distillator/`) — обобщение идеи «дистиллята» из product-brief в универсальный механизм:
- Девиз: «lossless compression, not summarization» — сжатие без потерь смысла, а не пересказ.
- 4 стадии: Analyze → Compress → Verify & Output → Round-Trip Validate.
- Флаг `--validate` запускает агента round-trip-reconstructor: восстановить оригинал из дистиллята и сравнить — **проверка lossless-свойства экспериментом**, а не обещанием.
- Параметры downstream_consumer (для кого сжимаем) и token_budget (semantic splitting при превышении бюджета). Состав: SKILL.md, agents/distillate-compressor.md + round-trip-reconstructor.md, resources/ (3 файла), scripts/analyze_sources.py.

---

## 4. Чему это учит: переносимые уроки построения LLM-методов

**Урок 1. Дозируй инструкции файлами — не давай LLM весь сценарий сразу.**
Где в BMad: micro-file архитектура, «FORBIDDEN to load next step until C is selected», «Don't look ahead».
У себя: режьте многошаговый промпт на файлы-шаги и подгружайте следующий только после завершения текущего. Это единственный надёжный способ запретить модели «срезать» процесс.

**Урок 2. Состояние — на диске, в машиночитаемой шапке артефакта.**
Где в BMad: frontmatter `stepsCompleted`, append-only документы, step-01b-continue, project-scan-report.json с 24-часовой архивацией.
У себя: каждый долгий процесс должен уметь умереть и возродиться из файла. Кладите состояние прямо в выходной документ (frontmatter) — тогда артефакт и чекпойнт — одно и то же.

**Урок 3. Анти-галлюцинация — это сорсинг по конструкции, а не запрет в промпте.**
Где в BMad: техники только из CSV (брать «из памяти» — failure mode), research только с web search (нет инструмента — abort), бриф из 3 источников, «Don't fabricate technical moats», skeptic ищет утверждения без evidence.
У себя: для каждого типа факта определите единственный разрешённый источник (файл / поиск / слова пользователя) и сделайте отсутствие источника стоп-условием.

**Урок 4. Давай LLM оба контура: что такое успех И что такое провал.**
Где в BMad: каждый step-файл = MANDATORY RULES → TASK → SUCCESS METRICS (✅) → FAILURE MODES (❌).
У себя: к каждому промпту-этапу добавляйте явный список «что считается провалом» — негативные примеры дисциплинируют модель сильнее позитивных.

**Урок 5. Субагенты — с жёстким контрактом выхода и лимитами.**
Где в BMad: «Return ONLY the following JSON object. No preamble, no commentary. Maximum 8 bullets per section».
У себя: любой вспомогательный LLM-вызов должен возвращать только структуру (JSON) с числовыми лимитами и требованием приоритизации — иначе получите эссе вместо данных.

**Урок 6. Adversarial review до человека + триаж «косметика/стратегия».**
Где в BMad: Skeptic + Opportunity + Contextual линза до показа драфта; бесспорное правится автономно, стратегическое — только через пользователя.
У себя: между «LLM сгенерил» и «человек увидел» вставьте 2-3 ортогональные линзы критики, причём одну выбирайте под главный доменный риск конкретной задачи. Человеку показывайте только вопросы, требующие его решения.

**Урок 7. Один разговор — два артефакта для двух аудиторий.**
Где в BMad: executive brief (1-2 стр., для людей) + LLM distillate (плотные буллеты, для следующей фазы); capture-don't-interrupt собирает overflow молча.
У себя: если результат сессии будет читать и человек, и следующий LLM-этап — делайте два выхода. Не заставляйте человека читать «токен-эффективное», а LLM — «красивое».

**Урок 8. Graceful degradation прописывается заранее, с собственными лимитами.**
Где в BMad: «Never block the workflow because a subagent feature is unavailable» + конкретный fallback (1-2 дока inline, линзы последовательно и «don't blend them»).
У себя: для каждой опциональной возможности (субагенты, поиск, MCP) опишите план Б прямо в промпте — и ограничьте его аппетиты, чтобы деградация не съела контекст.

**Урок 9. Поведение — в данные (CSV/YAML), а не в текст промпта; но данные без контракт-теста дрейфуют.**
Где в BMad: brain-methods.csv (61 техника), documentation-requirements.csv (11×24), module-help.csv (DAG метода), methods.csv (50 методов) — против них найденные дрейфы: «36+ techniques across 7 categories» при реальных 61/10, несуществующие колонки CSV, BSP/BP, три имени product-brief, баги stepsCompleted.
У себя: выносите перечни и маршруты в таблицы — это правильно. Но добавьте то, чего у BMAD нет: автоматическую проверку, что промпты и данные согласованы (счёт строк, имена колонок, ссылки на файлы). LLM «прощает» рассинхрон — и тем прячет его от вас.

**Урок 10. Прогресс детектируй по артефактам на диске, а не по памяти/логам.**
Где в BMad: bmad-help определяет завершённость фаз сверкой `outputs`-паттернов с реальными файлами в output-location.
У себя: «сделано» = «файл существует и соответствует паттерну». Это единственный источник правды, который переживает любой обрыв сессии.

**Урок 11 (бонус). Сужай роль агента под режим через FORBIDDEN.**
Где в BMad: один и тот же выбор техник — четыре роли: librarian (FORBIDDEN рекомендовать), matchmaker (FORBIDDEN рекомендации без анализа), serendipity facilitator (FORBIDDEN подкручивать случайность), journey guide (FORBIDDEN перескакивать фазы).
У себя: вместо одной универсальной роли — несколько узких, каждая со своим запретом, закрывающим её главный соблазн.

---

## 5. Связь Phase 1 → Phase 2: что является входом следующей фазы

**Артефакты-выходы Phase 1** (все — в `{planning_artifacts}`, в этой инсталляции `_bmad/planning-artifacts/`, плюс docs):

| Артефакт | Производит | Потребляет в Phase 2 |
|---|---|---|
| `product-brief-{project_name}.md` | bmad-product-brief | bmad-create-prd — главный вход |
| `product-brief-{project_name}-distillate.md` | bmad-product-brief (finalize Step 2) | create-prd: «Token-efficient context for downstream PRD creation» — прямо в frontmatter дистиллята |
| `research/market|domain|technical-{{topic}}-research-{{date}}.md` | research ×3 | create-prd + architecture (domain step-05: «Use the domain research to inform other workflows (PRD, architecture, etc.)») |
| `brainstorming/brainstorming-session-{date}-{time}.md` | bmad-brainstorming | питает product brief; дальше — через бриф |
| Документация проекта | bmad-document-project | project_knowledge для всех downstream-фаз |

**Связь не декларативная, а подтверждённая потреблением** — в файлах Phase 2 есть прямые ссылки:
- `2-plan-workflows/bmad-create-prd/steps-c/step-02-discovery.md:45` — «Input documents already loaded are in memory (product briefs, research, brainstorming, project docs)»;
- `step-03-success.md:44` — анализ брифа на success criteria;
- `step-04-journeys.md:42,173` — персоны берутся из брифа, и есть FAILURE MODE «Ignoring existing personas from product briefs» — то есть Phase 2 **наказуема** за игнорирование выходов Phase 1.

**Механика handoff'а** — три слоя:
1. **Манифест:** `before: [create-prd]` у product-brief — декларативная связь в DAG.
2. **Финальное сообщение:** «Recommended next step: ...tell your assistant 'create a PRD' and point it to these files» — handoff человеческими словами.
3. **Headless-контракт:** JSON `{status, brief, distillate, confidence, open_questions}` — handoff для оркестратора, с явной самооценкой уверенности и списком нерешённых вопросов, которые Phase 2 должна добрать.

**И главный гейт:** Phase 1 целиком required=false, а `bmad-create-prd` — первый required=true шаг метода. То есть граница фаз — это не церемония, а смена режима: всё, что до PRD — «помоги мне додумать» (опционально, диалогово), всё, что после — обязательная цепочка артефактов (PRD → architecture → epics/stories → readiness-check → sprint → story → dev), которая и приводит к знакомой вам Phase 4.

---

## Термины

- **DAG** — граф шагов со стрелками «что после чего», без циклов.
- **Frontmatter** — служебная YAML-шапка в начале markdown-файла (метаданные документа).
- **Headless** — режим работы без участия человека, результат — машиночитаемый JSON.
- **Fan-out** — параллельный запуск нескольких субагентов/задач с последующей сборкой результатов.
- **Субагент** — отдельный вспомогательный LLM-вызов с собственным узким заданием и контрактом выхода.
- **Elicitation** — структурированное «вытягивание» знаний из человека вопросами.
- **HALT** — жёсткая точка остановки воркфлоу до явного выбора пользователя.
- **Append-only** — документ, в который можно только дописывать, не переписывая прошлое.
- **Brownfield** — работа с уже существующим проектом (в отличие от greenfield — с нуля).
- **Distillate** — сжатая без потерь смысла версия документа для потребления следующим LLM-этапом.
- **Graceful degradation** — заранее прописанный план Б при недоступности инструмента, без остановки процесса.
- **Drift** — расхождение между промптами и данными/реальностью, накопившееся со временем.