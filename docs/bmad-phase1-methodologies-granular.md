# Чем руководствовался автор BMAD на каждом шаге Phase 1 — разбор методологий, изобретений и ручек настройки

> База: `$B` = `/home/server/Downloads/crm/_bmad`, инсталляция BMAD METHOD v6.2.2 (по `_config/manifest.yaml`). Всё ниже проверено по файлам на диске; где файлы молчат — так и написано.

---

## 1. Сводная карта: какая фаза несёт какие индустриальные методологии

### 1.1 Таблица по фазам

| Фаза | Названные методологии (с файлами) | Чего там НЕТ (важно для ожиданий) |
|---|---|---|
| **core** (общий банк техник) | SCAMPER, Six Thinking Hats, Five Whys, First Principles, Pre-mortem, Red Team, Socratic Questioning, Mind Mapping, Morphological Analysis, Feynman Technique, Occam's Razor, Trolley Problem, ADR, Chaos Monkey + **LLM-papers**: Tree of Thoughts, Graph of Thoughts, Self-Consistency, Thread of Thought — всё в двух CSV: `$B/core/bmad-brainstorming/brain-methods.csv` и `$B/core/bmad-advanced-elicitation/methods.csv` | Никакой agile-механики; это «библиотека мышления», не процесс |
| **1-analysis** | Porter's Five Forces + SWOT + root cause + competitive intelligence (`bmad-agent-analyst/SKILL.md` и `bmad-skill-manifest.yaml` — **только декларация персоны**), SWOT (`research/bmad-market-research/steps/step-05-competitive-analysis.md` — «[SWOT analysis with source citations]»), KPI (`bmad-technical-research/technical-steps/step-05-implementation-research.md`, `bmad-market-research/steps/step-06-research-completion.md`), Customer journey mapping (`steps/step-04-customer-decisions.md`), CAGR (domain step-02, market step-06), GDPR/CCPA (domain step-04), Domain-driven design + SOLID + Clean/Hexagonal architecture + ADR (`bmad-technical-research/technical-steps/step-04-architectural-patterns.md`), MVP (вопрос в `bmad-product-brief/prompts/guided-elicitation.md`), Lean Canvas-лексика «unfair advantage / moat» (там же), CommonMark/DITA/OpenAPI/Mermaid (`bmad-agent-tech-writer/SKILL.md`) | **Ни одного хита BDD, Given/When/Then, user story, INVEST, acceptance criteria как формата.** Phase 1 — про понимание проблемы/рынка/кодовой базы, не про формат требований |
| **2-plan-workflows** | MVP-scoping (ядро: `bmad-create-prd/steps-c/step-08-scoping.md` — «EMPHASIZE lean MVP thinking»), User Journey Mapping (`steps-c/step-04-journeys.md`), Jobs-to-be-Done (`bmad-agent-pm/SKILL.md`), value proposition (`steps-c/step-02b-vision.md`), Must-Have Analysis (безымянный MoSCoW, step-08), acceptance criteria как будущий артефакт (`data/prd-purpose.md` — «Acceptance criteria → story acceptance tests») | INVEST, RICE, Kano, OKR — отсутствуют |
| **3-solutioning** | **User story формат** («As a/I want/So that» — `bmad-create-epics-and-stories/steps/step-03-create-stories.md`), **BDD** (`bmad-check-implementation-readiness/steps/step-05-epic-quality-review.md:108` — «Given/When/Then Format: Proper BDD structure?»), **Given/When/Then** (`step-03-create-stories.md:150` — «Use Given/When/Then format»), acceptance criteria (`templates/epics-template.md`), Scrum Master как роль (`bmad-check-implementation-readiness/workflow.md`), value proposition per epic, MVP deferred decisions | Vertical slicing как названный метод — нет (есть только проверка «"Setup all models" - not a USER story» в step-05-epic-quality-review.md — это INVEST-дух без INVEST-имени) |
| **4-implementation** | Scrum (`bmad-agent-sm/SKILL.md` — «Certified Scrum Master»), sprint planning/status/correct-course, retrospective + velocity + story points (`bmad-retrospective/workflow.md`), **TDD полным именем** («test-driven development» в `bmad-agent-dev/bmad-skill-manifest.yaml`) + **red-green-refactor** (`bmad-dev-story/workflow.md:263`), BDD-форматированные criteria (`bmad-create-story/workflow.md:225`), Definition of Done (`bmad-dev-story/checklist.md`), root cause (`bmad-quick-dev/step-04-review.md`) | Kanban, WIP-limits, story slicing — нет |

### 1.2 Ключевой честный вывод про BDD/INVEST/slicing

Греп подтверждает: **BDD, Given/When/Then, user stories и acceptance criteria живут исключительно в фазах 2-4** (точки входа: `3-solutioning/bmad-create-epics-and-stories/steps/step-03-create-stories.md`, `3-solutioning/bmad-check-implementation-readiness/steps/step-05-epic-quality-review.md`, `4-implementation/bmad-create-story/workflow.md`, `4-implementation/bmad-quick-dev/spec-template.md`). В Phase 1 — ноль хитов. Phase 1 несёт другое: аналитический консалтинг (Porter/SWOT — декларативно), evidence-based research с цитированием, продуктовый VC-словарь (moat/why-now/aha-moment) и креативный банк Осборна/де Боно. **INVEST отсутствует во всём BMAD целиком** — качество story проверяется самописным чек-листом step-05-epic-quality-review.md, а не INVEST-критериями.

### 1.3 Этого в BMAD нет вообще (ни в каком написании, все 4 фазы)

INVEST · Kanban · vertical slice / story slicing · Lean Startup · Lean Canvas · Business Model Canvas · TAM / SAM / SOM · PESTLE/PESTEL · Design Thinking (как названный фреймворк) · Double Diamond · Working Backwards (Amazon) · PR-FAQ / press release · OKR · North Star Metric · RICE · MoSCoW · Kano · empathy map · story mapping · event storming · A/B testing.

Аббревиатура отсутствует, но фреймворк есть полным именем: TDD («test-driven development» + red-green-refactor), JTBD («Jobs-to-be-Done framework»), DDD («domain-driven design patterns», 1 упоминание в Phase 1 technical research). Слово «lean» (19 хитов) везде — прилагательное «компактный», не Lean-методология.

---

## 2. Покрупичный разбор каждого шага Phase 1

## 2.1 `bmad-brainstorming` (core-скилл, вызывается Mary как capability BP)

### Крупица: точка входа — манифест
**Шаг:** SKILL.md (`$B/core/bmad-brainstorming/SKILL.md`)
- **Названо в файле:** ничего не названо.
- **Узнаваемо без имени:** манифест Anthropic Agent Skills — тонкий frontmatter с триггер-фразами («Use when the user says help me brainstorm or help me ideate»), тело = одна строка «Follow the instructions in ./workflow.md» (спецификация Claude Code Skills, 2024-2025; **high**).
- **Собственное изобретение BMAD:** разделение «манифест ≠ логика» — 6 строк-указатель, вся машинерия грузится лениво.
- **Замысел автора:** скилл должен срабатывать по бытовой фразе, а контекст-тяжёлый workflow подгружаться только после активации.
- **Настроить под себя:** поле `description` в SKILL.md — дописать свои триггеры («мозговой штурм», «покидаем идеи»); заменить ссылку `./workflow.md` — скилл станет обёрткой над любым процессом.

### Крупица: оркестратор
**Шаг:** workflow.md
- **Названо в файле:** «black swan events» (Taleb, 2007 — как ярлык экстремальных сценариев); «semantic clustering (sequential bias)» — терминология исследований LLM-идеации 2023-2024.
- **Узнаваемо без имени:** правила Осборна — deferred judgment + «quantity breeds quality» («Aim for 100+ ideas before any organization»; Alex Osborn, *Applied Imagination*, 1953; **high**) · Extended Effort Principle («the magic happens in ideas 50-100»; Sidney Parnes, Osborn-Parnes CPS; **medium**) · дивергентное мышление («maintain true divergence»; J.P. Guilford, 1950; **high**) · роль content-neutral фасилитатора (IAF/ToP-школа; **medium**) · «творческий дискомфорт как индикатор» (эвристика CPS/IDEO, близко creative abrasion — Leonard, HBR 1997; **low**).
- **Собственное изобретение BMAD:** Anti-Bias Protocol — обязательный пивот домена каждые 10 идей (контрмера именно против LLM-кластеринга, в человеческой фасилитации аналога нет) · micro-file architecture (каждый шаг — самодостаточный файл) · state в YAML-frontmatter выходного документа · техники on-demand из CSV · фиксация пути output-файла «evaluated once at workflow start».
- **Замысел автора:** две явно названные угрозы — LLM рано сваливается в выводы (лечится «keep the user in generative exploration mode as long as possible») и генерит похожие идеи подряд (лечится anti-bias pivot). Квота 100+ — операционализация Осборна в проверяемую метрику.
- **Настроить под себя:** числа квоты (100+ → 30-50 для коротких сессий), интервал пивота («every 10 ideas») и домены пивота (technical→UX→business→black swan → свои оси), `context_file` в frontmatter, паттерн имени файла `brainstorming-session-{{date}}-{{time}}.md`; язык/папка/имя — `$B/core/config.yaml`.

### Крупица: шаблон-носитель состояния
**Шаг:** template.md
- **Названо в файле:** ничего не названо.
- **Узнаваемо без имени:** YAML frontmatter (конвенция Jekyll/static site generators, ~2008, переиспользована как state-store; **high**), Mustache-плейсхолдеры `{{user_name}}` (**high**).
- **Собственное изобретение BMAD:** документ = одновременно артефакт для человека и память workflow для LLM (`stepsCompleted[]`, `techniques_used`, `ideas_generated` — по ним step-01b восстанавливает сессию); пустые поля-болванки как контракт шагов.
- **Замысел автора:** continuation-дизайн — для резюма сессии достаточно шапки, контент можно не читать (экономия токенов). Археологическая оговорка: «**Facilitator:** {{user_name}}» — подпись фасилитатора отдана пользователю, хотя фасилитатор по workflow — LLM; похоже на копипаст-слип.
- **Настроить под себя:** добавить свои секции (## Constraints, ## Stakeholders) и state-поля (например `idea_quota:`) — шаги аппендят поверх скелета.

### Крупица: Step 1 — Session Setup
**Шаг:** steps/step-01-session-setup.md
- **Названо в файле:** «psychological safety» («Maintain psychological safety for creative exploration» — термин Amy Edmondson, 1999).
- **Узнаваемо без имени:** контрактование сессии purpose+outcomes (POST-формула фасилитации/ICF-коучинг; **medium**) · стадия Objective Finding из Osborn-Parnes CPS — фиксация темы и целей ДО выбора техник (**medium**) · playback/активное слушание («Does this accurately capture what you want to achieve?»; Carl Rogers; **medium**).
- **Собственное изобретение BMAD:** «HALT — wait for user selection» — стоп-токен против LLM-привычки отвечать за пользователя · FORBIDDEN-формулы («YOU ARE A FACILITATOR, not a content generator») · «DO NOT read any file contents - only list filenames» при детекте старых сессий (явная токен-экономия) · SUCCESS METRICS / FAILURE MODES как самопроверочный чек-лист внутри промпта · CONTEXT BOUNDARIES (изоляция шага) · меню-роутер 1-4 → файлы step-02a/b/c/d.
- **Замысел автора:** три LLM-болезни — самовольная генерация, потеря состояния, расход контекста. Дубли секций (заголовок «### E», повтор Handle User Selection) выдают итеративное редактирование без чистки.
- **Настроить под себя:** добавить discovery-вопросы (ограничения/время/аудитория); добавить пункт [5] меню + свой step-02e — новый режим выбора техник; переписать приветственный скрипт.

### Крупица: Step 1b — продолжение сессии
**Шаг:** steps/step-01b-continue.md
- **Названо в файле:** ничего не названо.
- **Узнаваемо без имени:** checkpoint/resume из software engineering — чтение frontmatter → роутинг в нужный шаг (workflow-движки; в творческих методологиях аналога нет; **high**) · re-contracting/check-in при возврате (коучинг, многодневные воркшопы; **medium**).
- **Собственное изобретение BMAD:** роль «CONTINUATION FACILITATOR, not a fresh starter» · запрет «repeating completed work or asking same questions» (анти-паттерн LLM-переспрашивания) · поля `session_continued: true` / `continuation_date` — аудит-след возобновлений.
- **Замысел автора:** чат-сессии обрываются, LLM сам не помнит где остановились — диск (frontmatter) объявлен единственным источником правды («RESPECT EXISTING WORKFLOW state»).
- **Настроить под себя:** добавить опцию [4] «Export summary» в Continuation Options; расширить Session Status (показ последних идей — помня о токенах).

### Крупица: Step 2a — пользователь выбирает техники («библиотекарь»)
**Шаг:** steps/step-02a-user-selected.md
- **Названо в файле:** SCAMPER Method (Bob Eberle, 1971, на чек-листе Осборна), Six Thinking Hats (Edward de Bono, 1985) — с готовыми карточками Duration/Energy/Example prompt.
- **Узнаваемо без имени:** метод-карточки как browse-колода (IDEO Method Cards 2003, Thinkpak Михалко 1994; **medium**) · контентная нейтральность («YOU ARE A TECHNIQUE LIBRARIAN, not a recommender»; принцип нейтральности IAF; **medium**).
- **Собственное изобретение BMAD:** запрет «making recommendations or steering choices» · on-demand загрузка CSV (preloading = объявленная FAILURE MODE) · двухступенчатый HALT + [Back]-навигация · фиксация `selected_approach: 'user-selected'` в frontmatter.
- **Замысел автора:** режим для контроля пользователя. **Археология — двойной дрейф данных:** шаг заявляет «36+ Techniques Across 7 Categories», реальный CSV — 61 техника в 10 категориях (biomimetic/quantum/cultural в меню вообще нет); шаг велит парсить колонки `facilitation_prompts, best_for, energy_level, typical_duration`, которых в CSV нет — Duration/Energy в карточках LLM вынуждена фабриковать.
- **Настроить под себя:** синхронизировать счётчики с CSV (61/10, добавить пункты [8]-[10]); добавить недостающие колонки в `brain-methods.csv` — фабрикация исчезнет; править формат карточки (добавить «когда НЕ использовать»).

### Крупица: Step 2b — AI-рекомендация («сваха»)
**Шаг:** steps/step-02b-ai-recommended.md
- **Названо в файле:** ничего не названо.
- **Узнаваемо без имени:** purpose-indexed подбор метода (контингентная таблица «Innovation → creative/wild; Problem Solving → deep/structured»; Gamestorming — Gray/Brown/Macanufo 2010, VanGundy 1988; **medium**) · трёхфазная арка Foundation → Generation → Refinement (диамант Канера 1996, divergent→emergent→convergent; **medium**) · таймбоксинг по доступному времени (<30/30-60/>60 мин; **high**) · подстройка под тон клиента (calibration из коучинга; **low**).
- **Собственное изобретение BMAD:** решающая таблица из 4 измерений (Goal/Complexity/Energy/Time) прямо в промпте — рекомендация сделана воспроизводимой · обязательное «Why this fits» на каждую рекомендацию · баланс «expertise vs user autonomy» (рекомендации + Modify/Details/Back + HALT).
- **Замысел автора:** автор не доверяет LLM «просто порекомендовать» — без рамки выйдет generic-список; матчинг разложен на проверяемые измерения с rationale.
- **Настроить под себя:** дописать строки в Analysis Framework («Compliance/Risk → deep, structured»), поменять пороги таймбоксинга, сократить/расширить число фаз.

### Крупица: Step 2c — случайный выбор («серендипити»)
**Шаг:** steps/step-02c-random-selection.md
- **Названо в файле:** ничего не названо.
- **Узнаваемо без имени:** случайная карта как катализатор + право перетянуть [Shuffle] (Brian Eno & Peter Schmidt, Oblique Strategies, 1975; **medium**) · обоснование пользы случайности = Random Entry де Боно («forces us out of our usual thinking patterns»; Lateral Thinking, 1970; **medium**) · стратифицированная случайность (из разных категорий + совместимость; **low**).
- **Собственное изобретение BMAD:** роль «SERENDIPITY FACILITATOR» + запрет «steering random selections» (LLM не умеет в честный рандом — автор хотя бы запрещает подгонку пост-фактум) · обязательный excitement-фрейминг («Random discovery bonus») · [Shuffle] при сохранении HALT-дисциплины.
- **Замысел автора:** режим против творческой колеи; «intelligent random» — рандом с ограничениями совместимости. Метафора замысла: «Random selection should feel like opening a creative gift».
- **Настроить под себя:** число случайных техник (3 → 2), правило «из разных категорий», лимит решаффлов (чтобы рандом не вырождался в ручной выбор).

### Крупица: Step 2d — прогрессивный поток
**Шаг:** steps/step-02d-progressive-flow.md
- **Названо в файле:** Divergent Thinking, Convergent Thinking (фазы 1 и 3 названы прямо — J.P. Guilford).
- **Узнаваемо без имени:** Double Diamond — расширение→сужение в 4 фазах (UK Design Council, 2005; **medium**) · полный цикл Osborn-Parnes CPS вплоть до Acceptance Finding («implementation strategies... resources, timelines, success metrics»; **medium**) · хвост Design Thinking ideate→implement (**low**) · deferred judgment в фазе 1 (**high**).
- **Собственное изобретение BMAD:** фазовый гейт «FORBIDDEN jumping ahead to later phases» (LLM любит прыгать к решению) · Journey Map с явными «→ Phase Transition» как контрольными точками · Customize-меню (Compact/Extended/Focused) — параметризация без правки файлов.
- **Замысел автора:** единственный режим, где когнитивная пара названа и продана как «как работает естественная креативность»; метка «(Analytical Thinking)» у фазы 2 — авторская вставка-аналог groan zone Канера.
- **Настроить под себя:** состав фаз (слить 2-3, добавить инкубацию), привязки категорий CSV к фазам.

### Крупица: Step 3 — исполнение техник (ядро генерации)
**Шаг:** steps/step-03-technique-execution.md
- **Названо в файле:** CoT — «THOUGHT BEFORE INK (CoT): Before generating each idea, you must internally reason...» (Chain-of-Thought — Wei et al., Google, 2022; применён не к логике, а к новизне); «SIMULATED TEMPERATURE: Act as if your creativity is set to 0.85» (softmax temperature — стандартный параметр LLM API); SCAMPER / Six Thinking Hats как примеры; внутренний скилл `bmad-advanced-elicitation`.
- **Узнаваемо без имени:** «quantity breeds quality» почти дословно («AIM FOR 100+ IDEAS... quantity unlocks quality»; Осборн 1953; **high**) · deferred judgment («Don't filter or edit»; **high**) · «Yes, and...» из импров-театра («Let me build on your idea»; Spolin/Johnstone/IDEO; **medium**) · активное слушание с ветвлением по типу реплики (**medium**) · мониторинг энергии группы (Energy Checkpoint каждые 4-5 обменов, depleted-эвристика «short responses, 'I don't know'»; ToP/Gamestorming; **medium**) · минимальный таймбокс 30-45 минут — инвертированный extended effort (**medium**) · карточка идеи с полем Novelty (idea capture cards; **low**).
- **Собственное изобретение BMAD:** anti-bias domain pivot («Every 10 ideas... consciously pivot to an orthogonal domain: UX → Business → Physics → Social Impact») · simulated temperature — API-ручка пересажена в текст роли (через скилл сэмплированием управлять нельзя) · **асимметричный гейт конвергенции**: AI запрещено инициировать завершение (это объявленная FAILURE MODE), выход только по явной просьбе, 45+ мин И 100+ идей, или depleted · escape-фраза «next technique» с авто-документацией частичного прогресса · двойная документация «what» (идеи) и «how» (facilitation_notes, Energy Flow) · «CSV provides structure, not rigid scripts».
- **Замысел автора:** самый плотный файл — война с главным пороком LLM-брейншторма: преждевременной конвергенцией и среднестатистическими идеями. Замысел проговорён: «This is creative coaching, not technique delivery!».
- **Настроить под себя:** квота и таймбокс (100+/30-45 мин → реалистичные 25-30 идей), «температура» 0.85 → 0.6/0.95, поля IDEA FORMAT TEMPLATE (Feasibility, Owner — попадут в документацию шага 4), частота Energy Checkpoint, домены пивота под свою предметку.

### Крупица: Step 4 — организация и приоритизация (конвергенция)
**Шаг:** steps/step-04-idea-organization.md
- **Названо в файле:** Convergent/Divergent Thinking («FACILITATE CONVERGENT THINKING after divergent exploration» — Guilford).
- **Узнаваемо без имени:** affinity diagramming / KJ-метод — кластеризация в темы с Pattern Insight (Jiro Kawakita, 1960-е; **high**) · Impact/Effort матрица + квадрант quick wins («Easiest Quick Wins: Which ideas could be implemented fastest?»; **high**) · MCDA / weighted scoring по 4 критериям Impact/Feasibility/Innovation/Alignment (**medium**) · топ-N отбор как диалоговый dot-voting (**low**) · action planning по канону CPS Acceptance Finding / SMART («Success Metrics: How will you know it's working?» + Obstacles ≈ WOOP/premortem-lite; **medium**).
- **Собственное изобретение BMAD:** гейт «FORBIDDEN workflow completion without action planning» · финальный HALT [C] Complete + машинное закрытие `session_active: false / workflow_completed: true` · требование индексировать документ тремя разрезами (themes/priorities/techniques) · фиксация предпочтений пользователя «for future reference».
- **Замысел автора:** зеркало step-03 — там конвергенция запрещалась, тут становится обязанностью («YOU ARE AN IDEA SYNTHESIZER»); сессия без action plan = провал.
- **Настроить под себя:** заменить критерии приоритизации (Cost/Risk/Time-to-market), добавить Owner/Deadline/Dependencies в action-план, править markdown-блок финального документа.

### Крупица: библиотека техник — brain-methods.csv (61 техника, 10 категорий)
**Шаг:** brain-methods.csv

Атрибуция по группам (полная по-строчная атрибуция — три слоя компиляции):

| Группа | Техники | Происхождение (атрибуция, confidence) |
|---|---|---|
| **Канон креативности 1950-80-х, имена сохранены** | SCAMPER · Six Thinking Hats · Mind Mapping · Five Whys · Morphological Analysis · Reverse Brainstorming · First Principles · Forced Relationships · Provocation Technique · Random Stimulation · Question Storming · Brain Writing Round Robin · Role Playing · Yes And Building · Analogical Thinking · Assumption Reversal | Eberle 1971 · de Bono 1985 · Buzan 1970-е · Toyota/Ohno · Zwicky 1940-е · CPS-традиция · Аристотель/Маск · Whiting 1958 · de Bono «Po» 1972 · de Bono Random Entry 1970 · Roland 1985/Gregersen 2018 · Rohrbach 6-3-5 1968 · Moreno/Griggs · импров-театр/IDEO · синектика Гордона 1961 · VanGundy (всё **high-medium**) |
| **Структурно-аналитические без имён** | Reversal Inversion · What If Scenarios · Time Shifting · Metaphor Mapping · Cross-Pollination · Concept Blending · Constraint Mapping · Failure Analysis · Decision Tree Mapping · Solution Matrix · Trait Transfer · Resource Constraints | de Bono/Munger «invert» (**high**) · сценарное мышление Kahn/RAND (**medium**) · Michalko time travel (**medium**) · синектика+Lakoff&Johnson (**medium**) · Medici Effect Johansson 2004 (**medium**) · Fauconnier&Turner blending / бисоциация Кёстлера (**high**) · ToC Goldratt (**medium**) · FMEA/post-mortem (**medium**) · decision analysis Raiffa (**high**) · ящик Цвикки/Pugh matrix (**medium**) · attribute listing Crawford 1931 (**medium**) · creativity from constraints Stokes (**medium**) |
| **Психологический слой (introspective_delight)** | Shadow Work Mining · Mythic Frameworks · Inner Child Conference · Values Archaeology · Future Self Interview · Body Wisdom Dialogue · Permission Giving · Emotion Orchestra | Юнг («тень», «collective unconscious» названы в CSV) · Кэмпбелл (мономиф) · Bradshaw/Берн + beginner's mind · values clarification Raths 1966 · Hershfield future self · Gendlin Focusing 1978 · Edmondson+Cameron (**medium-low**; половина — авторские вариации) |
| **Театрально-карнавальный слой (theatrical/wild)** | Time Travel Talk Show · Alien Anthropologist · Dream Fusion Laboratory · Parallel Universe Cafe · Persona Journey · Guerrilla Gardening Ideas · Pirate Code Brainstorm · Zombie Apocalypse Planning · Drunk History Retelling · Anti-Solution · Quantum Superposition · Elemental Forces · Ideation Relay Race · Sensory Exploration · Emergent Thinking · Chaos Engineering | в основном **авторские обёртки известных механик**: остранение Шкловского/«марсианский антрополог» Сакса · Disney Strategy (Dilts 1994)+backcasting · контрфактический worldbuilding · rolestorming+архетипы · remix-культура (Kleon 2012) · MVP-редукция · Feynman/ELI5 (по ТВ-шоу Drunk History) · Worst Possible Idea (дубль Reverse Brainstorming!) · set-based design в квантовой обёртке · чисто авторская стихийная архетипика · Crazy 8s-дух · bodystorming · Theory U «what wants to happen» · Netflix Chaos Monkey 2011 + Taleb antifragility (названы) (**low-medium**) |
| **Biomimetic / Quantum / Cultural** | Nature's Solutions · Ecosystem Thinking · Evolutionary Pressure · Observer Effect · Entanglement Thinking · Superposition Collapse · Indigenous Wisdom · Fusion Cuisine | Benyus Biomimicry 1997 — формула «3.8 billion years» почти дословна (**high**) · Senge/Moore ecosystems (**medium**) · генетические алгоритмы Holland (**medium**) · реальная механика Observer Effect = эффект Хоторна/закон Гудхарта (**medium**) · остальное — авторские квантовые/кулинарные обёртки системного мышления (**low**) |

- **Собственное изобретение BMAD:** плоский 3-колоночный CSV как библиотека (расширение без правки промптов); facilitation-вопросы вшиты в description («by asking...»); категории introspective_delight/theatrical/wild/quantum — расширение репертуара под сольную сессию человек+LLM; карнавальная лексика работает на тот же anti-bias замысел.
- **Замысел автора:** трёхслойная компиляция — канон с именами, психология, авторский карнавал. Дубли (Anti-Solution ≈ Reverse Brainstorming; Quantum Superposition ≈ Superposition Collapse) намекают: список собирался добавлением категорий без дедупликации.
- **Настроить под себя:** дописать строку CSV — техника появится во всех 4 режимах; своя категория + маппинг в step-02b; добавить колонки best_for/energy_level/typical_duration (шаги уже ждут их); почистить дубли.

---

## 2.2 `bmad-product-brief` (BMM Phase 1) — executive brief через 5 стадий с fan-out субагентов

### Крупица: роль и продукт работы
**Шаг:** SKILL.md, Overview
- **Названо в файле:** Business Analyst («Act as a product-focused Business Analyst and peer collaborator» — роль BA формализована IIBA BABOK), executive summary (консалтинговый 1-pager), PRD (классический PM-артефакт, Horowitz 1996).
- **Узнаваемо без имени:** facilitation/peer-stance — «The user is the domain expert... Work together as equals» (школа Канера + BABOK elicitation; **high**) · problem-first нарратив всего конвейера — дух Amazon Working Backwards без имени (**medium**).
- **Собственное изобретение BMAD:** «LLM distillate» — токен-эффективный артефакт-компаньон для следующего LLM-workflow (не для человека) · «Design rationale» прямо в промпте («scanning documents is noise, not signal» без понимания intent) · persona-тон «dream builder energy».
- **Замысел автора:** intent прежде сканирования; не прерывать творческий поток пользователя («capture everything... rather than interrupting their creative flow»); BA-as-peer — чтобы LLM не доминировал.
- **Настроить под себя:** переписать роль (BA-peer → строгий VC-партнёр); длина брифа «1-2 page» (синхронно в SKILL.md, draft-and-review.md, finalize.md, brief-template.md); убрать/сделать обязательным дистиллят.

### Крупица: три режима автономии
**Шаг:** SKILL.md, Activation Mode Detection
- **Названо в файле:** ничего не названо.
- **Узнаваемо без имени:** CLI-флаги как контракт (`--autonomous/-A`, `--yolo`; UNIX-конвенции; **high**) · draft-first review (Anne Lamott «shitty first draft» / Amazon draft-then-review; **medium**) · «YOLO mode» — жаргон AI-coding-инструментов 2024-25 (**high**).
- **Собственное изобретение BMAD:** трёхуровневая шкала автономии guided → yolo → autonomous, детект режима из контекста вызова; переменная `{mode}` протаскивается между prompt-файлами — file-based state machine без кода.
- **Замысел автора:** один скилл для живого диалога и batch-конвейера (manifest: supports-headless) — не плодить два скилла.
- **Настроить под себя:** дефолтный режим; свои триггер-фразы (русские); запретить headless через `bmad-manifest.json`.

### Крупица: загрузка конфига
**Шаг:** SKILL.md, On Activation
- **Названо в файле:** ничего не названо.
- **Узнаваемо без имени:** конфиг отдельно от «кода» (12-Factor App, Heroku 2011; **high**) · i18n-разделение языка общения и языка документа (**medium**).
- **Собственное изобретение BMAD:** «External Skills: bmad-init» — декларация зависимости скилла от скилла (import для промптов); двойной язык как штатная шапка каждого prompt-файла.
- **Настроить под себя:** `communication_language`, `document_output_language`, `planning_artifacts`, `project_knowledge`, `user_name` — `$B/bmm/config.yaml` (в этой инсталляции: Russian, артефакты в `_bmad/planning-artifacts`, знания в `docs/`).

### Крупица: Stage 1 — Understand Intent
**Шаг:** SKILL.md, Stage 1
- **Названо в файле:** ничего не названо.
- **Узнаваемо без имени:** business case адаптация для некоммерческого (BABOK/PRINCE2; **medium**) · one-product-per-brief (один воркшоп — одна тема; ограничение WIP; **medium**) · «anything else?»-паттерн = doorknob question клинического интервьюирования (**medium**) · parking lot («capture them silently for the distillate. Don't redirect»; meeting facilitation; **high**) · brain dump (GTD/discovery-интервью; **high**).
- **Собственное изобретение BMAD:** «DO NOT read document files yet» — чтение отложено до субагентов Stage 2 (защита основного контекста) · update-режим через тот же конвейер (существующий бриф = rich input) · «soft gate» как класс мягкой контрольной точки (контраст с HALT).
- **Замысел автора:** intent-first + неприкосновенность потока; запрет раннего чтения — ещё и токен-дисциплина.
- **Настроить под себя:** типы брифов (internal tool, research); формулировка soft-gate вопроса (встречается также в guided-elicitation.md и draft-and-review.md).

### Крупица: Stage 2 — Contextual Discovery (fan-out)
**Шаг:** prompts/contextual-discovery.md
- **Названо в файле:** Fan-Out («## Subagent Fan-Out» — scatter-gather из распределённых систем; orchestrator-workers из Anthropic «Building Effective Agents», 2024), Graceful Degradation («Never block the workflow because a subagent feature is unavailable» — термин reliability engineering).
- **Узнаваемо без имени:** триангуляция источников пользователь+документы+рынок (UX research; **medium**) · gap analysis перед интервью (**high**) · disconfirming evidence («anything from research that contradicts... the user's assumptions?»; customer development Бланка; **medium**).
- **Собственное изобретение BMAD:** деградация описана в терминах токенов, не аптайма («Read only the most relevant 1-2 documents... limit context impact in degraded mode») · «product intent summary» как контракт-фильтр для воркеров · блок «Stage Complete» с explicit-критерием выхода.
- **Замысел автора:** тяжёлое чтение и веб-поиск — параллельно и в изолированных контекстах; рисёрч должен мочь опровергнуть пользователя, не только украсить бриф.
- **Настроить под себя:** третий субагент (codebase-analyzer для brownfield) — пункт 3 в Launch in parallel + файл в agents/; отключить веб-рисёрч (офлайн-проекты); лимит деградированного чтения.

### Крупица: Stage 3 — Guided Elicitation, подход
**Шаг:** prompts/guided-elicitation.md
- **Названо в файле:** ничего не названо (методологии — см. блоки вопросов ниже).
- **Узнаваемо без имени:** semi-structured interview («mental checklist, not a script»; topic guide качественных исследований; **high**) · reflective paraphrase (шаг 3 flow; Rogers; **high**) · hypothesis-led questioning («Based on your input and my research, it sounds like [X]. Is that right?»; McKinsey hypothesis-driven; **medium**) · follow the energy (**medium**).
- **Собственное изобретение BMAD:** термин «the soft gate» · анти-повтор «don't re-ask what's been covered» · early-exit порог «after fewer than 3-4 exchanges, proactively offer to draft early».
- **Замысел автора:** стадия прямо противопоставлена «rote section-by-section interrogation» — болезни LLM-анкетёров; выход — «enough to draft well», недостающее доберёт ревью-панель Stage 4.
- **Настроить под себя:** порог раннего выхода; критерии «When to Move On»; сделать стадию обязательной в yolo.

### Крупица: Stage 3 — четыре блока вопросов
**Шаг:** prompts/guided-elicitation.md, Topics to Cover
- **Названо в файле:** «aha moment» (PLG-школа, Facebook growth ~2009) · «unfair advantage or defensible moat» (Lean Canvas — Ash Maurya 2010; moat — Buffett/Thiel) · «Why now» (slide питч-дека Sequoia) · «minimum viable version» / MVP (Robinson 2001, Ries 2011).
- **Узнаваемо без имени:** JTBD/switch-интервью («How do people solve this today? What's frustrating...»; Christensen/Moesta; **medium**) · beachhead-сегментация («Who experiences this problem most acutely?»; Moore «Crossing the Chasm»; **medium**) · персоны («different user types»; Cooper 1999; **medium**) · earned secret («What's the insight or angle...»; Thiel; **low**) · explicit out-of-scope (PMBOK charter exclusions; **medium**) · vision horizon 2-3 года (Pichler/Cagan + VC; **medium**).
- **Собственное изобретение BMAD:** сами 4 блока — компрессия Lean Canvas + PLG + VC-питч + project charter в «mental checklist» из 15 вопросов, с указанием использовать данные Stage 2.
- **Замысел автора:** каждый блок зеркалит секцию шаблона (Vision&Problem → The Problem/The Solution и т.д.) — интервью собирает ровно то, что нужно для рендера, ни вопросом больше; лексика VC — потому что бриф = pitch.
- **Настроить под себя:** добавить блок (Compliance для regtech) — синхронно с секцией в brief-template.md; заменить VC-лексику на корпоративную; горизонт 2-3 года → 5 лет.

### Крупица: Stage 4 Step 1 — черновик
**Шаг:** prompts/draft-and-review.md, Step 1
- **Названо в файле:** YAML frontmatter (Jekyll-конвенция).
- **Узнаваемо без имени:** problem-first нарратив («make the reader feel the pain before presenting the solution»; Amazon PR/FAQ + SCQA Минто; **medium**) · executive writing — пирамида Минто (**medium**) · pitch-стиль («this is a pitch, not a hedge»; YC-культура; **high**) · provenance-метаданные (`inputs:`; data lineage; **medium**).
- **Собственное изобретение BMAD:** `status: "draft"` → `"complete"` как машинное состояние конвейера · детерминированное имя `{planning_artifacts}/product-brief-{project_name}.md` — контракт для create-prd · анти-шаблонная директива «adapt structure to fit the product's story».
- **Настроить под себя:** writing principles (добавить «активный залог, без англицизмов»); схему имени файла менять синхронно в draft-and-review.md и finalize.md — иначе ломается контракт с create-prd; дополнительные поля frontmatter.

### Крупица: Stage 4 Step 2 — ревью-панель из трёх линз
**Шаг:** prompts/draft-and-review.md, Step 2 + agents/skeptic-reviewer.md + agents/opportunity-reviewer.md
- **Названо в файле:** network effects / chicken-and-egg (платформенная экономика), go-to-market (дефолтная линза), organizational change management (Kotter/Prosci-домен), developer experience.
- **Узнаваемо без имени:** devil's advocate / red teaming — skeptic с мандатом «What assumptions are untested? What could go wrong?» (**high**) · параллельное мышление разными ролями — три линзы с запретом «don't blend them into one generic review» (de Bono Six Hats / perspective-based reading Basili; **medium**) · risk-based выбор третьей линзы («the SINGLE BIGGEST RISK that the skeptic and opportunity reviewers won't naturally catch»; **medium**) · pre-customer review gate (издательская вычитка; **high**). В skeptic-reviewer.md дополнительно проверяются moat («Are the differentiators actually defensible?») и реалистичность MVP scope; в opportunity-reviewer.md — investor-взгляд («What would an investor want to hear more about?») и flywheel/viral potential (PLG-лексика; **medium**).
- **Собственное изобретение BMAD:** Contextual Reviewer — третья линза НЕ зафиксирована файлом, основной агент конструирует роль под домен («run the review yourself inline») · явный fallback при неясном домене (GTM: «Almost always valuable, frequently missed») · graceful degradation ревью («Perform all three review passes yourself, sequentially») · у обоих фиксированных ревьюеров — жёсткий JSON-контракт возврата («Return ONLY the following JSON object... Maximum 5 items per section»).
- **Замысел автора:** skeptic и opportunity намеренно ортогональны (дыры vs упущенная ценность); ревью идёт ДО пользователя — человек получает усиленный драфт + выжимку спорного, а не сырец.
- **Настроить под себя:** четвёртая постоянная линза (security/privacy — файл в agents/ по образцу skeptic-reviewer.md); каталог доменных линз; дефолт GTM → unit economics; схемы JSON ревьюеров (добавить severity-шкалу).

### Крупица: Stage 4 Steps 3-4 — триаж и презентация
**Шаг:** prompts/draft-and-review.md, Steps 3-4
- **Названо в файле:** ничего не названо.
- **Узнаваемо без имени:** триаж находок с дедупликацией (bug triage + affinity grouping; **high**) · auto-fix vs escalate (copy-edit vs substantive-edit; nit vs blocking в code review; **high**) · design crit подача («my review panel surfaced some things worth considering»; Pixar braintrust-дух; **medium**).
- **Собственное изобретение BMAD:** фильтр эскалации — пользователю только «substantive ones that need user input» (80/20 автономии — фирменный BMAD-паттерн) · дословные реплики-скрипты вместо свободной генерации · композитный критерий Stage Complete (все линзы + удовлетворённость пользователя/режим).
- **Настроить под себя:** граница «non-controversial» (запретить молчаливые правки секции Scope); реплика презентации; лимит итераций.

### Крупица: Stage 5 — Finalize + distillate
**Шаг:** prompts/finalize.md
- **Названо в файле:** ничего не названо.
- **Узнаваемо без имени:** «Rejected ideas — so downstream workflows don't re-propose them» = ADR-практика фиксации отвергнутых альтернатив (Nygard 2011; **high**) · machine-readable exit (headless JSON c status/confidence/open_questions; CI/CD-контракты; **high**) · next-step handoff (stage-gate Купера; **medium**) · open questions register (RAID log; **medium**).
- **Собственное изобретение BMAD:** артефакт типа `llm-distillate` («purpose: Token-efficient context for downstream PRD creation») · принципы письма ПОД LLM-читателя («Each bullet carries enough context to be understood standalone», «Token-conscious... so an LLM reading this later understands WHY») · анти-пустышка правило (не создавать пустой дистиллят) · self-reported `confidence: high|medium|low` для оркестратора · loop-back в draft-and-review.
- **Замысел автора:** «куда деть всё, что не влезло в 1-2 страницы» — overflow со Stage 1 монетизируется для PRD-фазы; группировка «by theme, not by when it was mentioned» превращает хронологию разговора в структуру знания.
- **Настроить под себя:** секции дистиллята (Pricing signals, Legal constraints); сделать дистиллят безусловным; поля headless-JSON (синхронно с парсером вашего оркестратора).

### Крупица: шаблон брифа
**Шаг:** resources/brief-template.md
- **Названо в файле:** ничего не названо (секция «What Makes This Different» содержит VC-лексику «unfair advantage», «moat» — см. атрибуцию выше).
- **Узнаваемо без имени:** 8-секционный 1-pager Executive Summary → Problem → Solution → Differentiation → Users → Success Criteria → Scope → Vision — гибрид Amazon-нарратива и Lean Canvas-полей (**medium**) · «Be honest — if the moat is execution speed, say so. Don't fabricate technical moats» — анти-фабрикация как редакционный принцип.
- **Собственное изобретение BMAD:** шаблон объявлен «flexible guide... The product determines the structure, not the template» + Adaptation Guidelines (B2B → Buyer vs User; marketplace → Network Effects; regulated → Compliance; «If scope is well-defined: Merge Scope and Vision into Roadmap Thinking») — таблица условных мутаций шаблона.
- **Настроить под себя:** структура секций и Adaptation Guidelines; помнить про зеркальность с 4 блоками guided-elicitation.

### Крупица: субагент Artifact Analyzer
**Шаг:** agents/artifact-analyzer.md
- **Названо в файле:** ничего не названо.
- **Узнаваемо без имени:** desk research роль (**high**) · skimming strategy для >50 страниц (ToC-first; SQ3R; **medium**) · глоб-эвристики поиска (`*brainstorm*`, `*research*`; UNIX-конвенции; **high**) · structured output contract («Return ONLY the following JSON... Maximum 8 bullets per section»; LLM JSON mode; **high**).
- **Собственное изобретение BMAD:** конвенция sharded documents (index.md + части — BMAD-механика обхода контекстных лимитов) · tool-call batching в промпте («issue all Read calls in a single message») · `raw_detail_worth_preserving` — субагент знает о дистилляте Stage 5 · rejected ideas как первоклассные данные (схема accepted|rejected|open) · токен-бюджет возврата.
- **Замысел автора:** субагент — расходуемый контекст: горы документов на входе, сжатый JSON на выходе; intent — фильтр («Don't waste tokens»); честность о глубине (skimmed vs read fully).
- **Настроить под себя:** паттерны имён (добавить `*prd*`, русские имена); порог «>50 pages»; схема возврата (синхронно с Synthesis в contextual-discovery.md); бюджет буллетов.

### Крупица: субагент Web Researcher
**Шаг:** agents/web-researcher.md
- **Названо в файле:** pain points (customer development жаргон), «timing_and_opportunity... why now, enabling shifts» (VC Why-Now), competitive_landscape с полем gaps.
- **Узнаваемо без имени:** структура market research отчёта (конкуренты/смежники/размер/sentiment; **high**) · Voice of Customer / review mining (**medium**) · готовые поисковые шаблоны (OSINT-паттерны; **medium**) · «Synthesize findings — don't just list links. Extract the signal» (**high**).
- **Собственное изобретение BMAD:** бюджет «3-5 targeted web searches — quality over quantity» (числовой лимит на инструментальные вызовы); «Maximum 5 bullets per section» + JSON-only; обязательная секция risks_and_considerations (включая regulatory) — рисёрчер обязан вернуть и негатив.
- **Замысел автора:** файл закрывает конкретные секции брифа внешними данными (competitive_landscape → What Makes This Different; timing → Why now; sentiment → The Problem); лимит поисков — защита от рисёрч-спирали в headless.
- **Настроить под себя:** число/шаблоны запросов (добавить `site:` для своего рынка); схема возврата.

---

## 2.3 Research-семейство: `bmad-market-research` / `bmad-domain-research` / `bmad-technical-research`

### Крупица: общий каркас трёх workflow
**Шаг:** workflow.md ×3 + research.template.md ×3
- **Названо в файле:** ничего не названо.
- **Узнаваемо без имени:** роль research facilitator с разделением труда («you bring research methodology and web search capabilities, while your partner brings domain knowledge» — BABOK-распределение процесс/контент; **medium**) · topic guide клиентского брифинга (Core Topic / Goals / Scope — стандарт research brief; **high**).
- **Собственное изобретение BMAD:** жёсткий пререквизит «Web search required. If unavailable, abort» — fail-fast гейт против галлюцинаций · детерминированное имя файла `{planning_artifacts}/research/market-{{topic}}-research-{{date}}.md` · стартовый файл = точная копия research.template.md с state-frontmatter (`stepsCompleted: []`, `web_research_enabled: true`, `source_verification: true`) и якорем `[Research overview and methodology will be appended here]` для финальной замены · переменные `research_type/topic/goals` протаскиваются через все шаги.
- **Замысел автора:** три скилла — один и тот же скелет с разной предметной начинкой; append-only построение документа поверх неизменного шаблона.
- **Настроить под себя:** примеры тем в приветствии; схема имени файла; frontmatter-поля шаблона (research.template.md).

### Крупица: единый «жанр» step-файла (повторяется во всех 16 шагах трёх скиллов)
Каждый шаг построен из одних и тех же блоков: MANDATORY EXECUTION RULES → EXECUTION PROTOCOLS → CONTEXT BOUNDARIES → задача → последовательность → SUCCESS METRICS → FAILURE MODES.
- **Названо в файле:** ничего (механика безымянная).
- **Узнаваемо без имени:** grounding/anti-hallucination дисциплина — «NEVER generate content without web search verification» + FAILURE MODE «Relying solely on training data without web verification» (практика RAG/grounded generation, 2023+; **high**) · оценка достоверности «Apply confidence levels to uncertain data», «Present conflicting information when sources disagree» — родня words of estimative probability разведаналитики (Sherman Kent, CIA) и академической триангуляции (**low-medium**, в файлах школа не названа) · цитирование «Always cite URLs» (**high**).
- **Собственное изобретение BMAD:** негативная роль на каждом шаге («YOU ARE A CUSTOMER BEHAVIOR ANALYST, not content generator») · [C]-гейт с HALT перед каждым переходом · `stepsCompleted: [1, 2, ...]` ратчет в frontmatter · «WRITE CONTENT IMMEDIATELY TO DOCUMENT» (диск, не чат) · «ALWAYS read the complete step file before taking any action» — анти-частичное-чтение · «UTILIZE SUBPROCESSES AND SUBAGENTS... if available» — параллельный fan-out 4 поисков как опция, не зависимость.
- **Замысел автора:** довести LLM-рисёрч до воспроизводимости: каждый шаг — транзакция «поиск → синтез → запись на диск → человеческий гейт», с самопроверкой по SUCCESS/FAILURE-спискам.

### Крупицы: bmad-market-research, шаги 1-6
**step-01-init.md** — Названо: ничего. Узнаваемо: research scoping / подтверждение брифа до полевой работы (стандарт market research; **high**); правило «Init steps confirm understanding and scope, not generate research content» — стадийная дисциплина. Изобретение BMAD: «NO WEB RESEARCH in init»; немедленная запись scope-документа; [C]/[Modify] меню. Настроить: список Scope Clarification Questions (сегменты/география/цель — добавить свои), блок Focus Areas.

**step-02-customer-behavior.md** — Названо: ничего. Узнаваемо: сегментация по демографии/психографике/поведению — классический STP-инструментарий маркетинга (Kotler-школа; в файле не названа; **medium**); психографика (VALS-традиция; **low**). Изобретение: 4 параллельных поисковых запроса прописаны дословно; структура аппенда из 6 фиксированных подсекций с курсивными слотами `_Source: [URL]_`. Настроить: список поисковых запросов и подсекций (например, добавить «B2B-роли в закупке»).

**step-03-customer-pain-points.md** — Названо: ничего. Узнаваемо: Voice of Customer / review mining («customer satisfaction surveys... complaints and reviews»; **medium**) · gap analysis ожидание-реальность («Expectation Gaps») — родня SERVQUAL (Parasuraman et al., 1988; в файле не названа; **low**) · приоритизация болей High/Medium/Low + Opportunity Mapping (**medium**). Изобретение: обязательная секция Emotional Impact Assessment (frustration/loyalty/reputation). Настроить: оси приоритизации болей.

**step-04-customer-decisions.md** — **Названо в файле:** «Customer journey mapping» (UX/сервис-дизайн практика; стадии Awareness → Consideration → Decision → Purchase → Post-Purchase = классическая воронка/consumer decision journey — AIDA-родословная от E. St. Elmo Lewis, McKinsey CDJ 2009; сами имена AIDA/CDJ в файле НЕ названы). Узнаваемо: touchpoint analysis (сервис-дизайн; **medium**) · social proof в Decision Influencers (Чалдини; не назван; **medium**) · friction reduction/CRO-блок (**medium**). Настроить: стадии journey под свой цикл сделки.

**step-05-competitive-analysis.md** — **Названо в файле:** SWOT («### Strengths and Weaknesses / [SWOT analysis with source citations]» — Эндрюс/Гарвардская школа, 1960-е). Узнаваемо: структура competitive intelligence отчёта (key players → market share → positioning → differentiation → threats; SCIP-практика; **medium**). Честно: **Porter's Five Forces в шаге НЕ применяется** — несмотря на декларацию в персоне Mary, нет ни поставщиков, ни покупателей, ни барьеров входа как рамки. Настроить: добавить подсекции под пять сил — простейший способ «дотянуть» шаг до декларации персоны.

**step-06-research-completion.md** — **Названо в файле:** KPI («### Success Metrics and KPIs»), Go-to-Market Strategy, CAGR. Узнаваемо: executive summary + TOC + методология + limitations — каркас профессионального research-репорта (консалтинговый стандарт; **high**) · risk assessment + mitigation (PMBOK-дух; **medium**) · «Methodology Transparency» — академическая норма (**medium**). Изобретение BMAD: финальная замена якоря `[Research overview and methodology will be appended here]` в шапке документа — отложенное введение, написанное после исследования. **Археологическая находка:** в финале шаг велит «Update frontmatter: `stepsCompleted: [1, 2, 3, 4]`» и пишет «All market research steps completed (1-4)» — при том что шагов шесть; скилл явно расширялся с 4 до 6 шагов без правки финального блока (фактический дрейф инструкции и структуры). Настроить: состав 11 секций финального документа; исправить дрейф stepsCompleted при кастомизации.

### Крупицы: bmad-domain-research, шаги 1-6
Каркас идентичен market (тот же init, те же [C]-гейты, тот же fan-out поисков). Отличия по содержанию:

**step-02-domain-analysis.md** — Названо: CAGR («Growth Rate: [CAGR and market growth projections]»). Узнаваемо: industry analysis с market maturity / life cycle stage («Life cycle stage and development phase» — концепция industry life cycle, стратегический менеджмент; имя в файле не названо; **medium**) · value chain намёк («Supply chain and value chain structure» — Porter value chain без имени; **low-medium**).

**step-03-competitive-landscape.md** — Названо: value proposition («Value Proposition Mapping: [Different value propositions across players]»), business models («Primary Business Models: [How competitors make money]» — generic, НЕ Business Model Canvas). Узнаваемо: конкурентная разведка по бизнес-моделям (**medium**).

**step-04-regulatory-focus.md** — Названо: GDPR, CCPA («GDPR, CCPA, and other data protection laws»). Узнаваемо: compliance-скан (regulatory mapping из GRC-практики; **medium**); три отдельных целевых поиска (regulations / standards / privacy).

**step-05-technical-trends.md** — Названо: ничего из методологий (грепом подтверждены только generic «New business models enabled by technology», «Business model evolution»). Узнаваемо: technology scouting / trend watching (**low**, в файле имён нет).

**step-06-research-synthesis.md** — Названо: CAGR. Каркас финального синтеза как у market step-06.

**Честная сводка по domain research:** покрытие industry → competitive → regulatory → technology напоминает PESTLE-разложение (политико-правовое + экономическое + технологическое), но **PESTLE в файлах не назван и social/environmental осей нет** — это моя атрибуция (low), а не авторская.

### Крупицы: bmad-technical-research, шаги 1-6
Каркас тот же. Самый «именной» файл всей research-ветки:

**step-04-architectural-patterns.md** — **Названо в файле:** Domain-driven design patterns (Eric Evans, 2003), SOLID principles (Robert Martin, начало 2000-х), Clean architecture (Martin, 2012), hexagonal architecture (Alistair Cockburn, 2005), microservices/monolithic/serverless (Lewis & Fowler 2014 — имена авторов в файле нет), event-driven/reactive, GraphQL vs REST, «architectural decision records (ADRs)» (Michael Nygard, 2011). Всё — как ярлыки направлений веб-поиска, а не как применяемые процедуры. Узнаваемо: trade-off-центричное мышление архитектора (ATAM-дух; **low**). Настроить: список паттернов под свой стек (заменить «GraphQL vs REST» на свои дилеммы).

**step-02-technical-overview.md / step-03-integration-patterns.md** — по названиям и грепу: обзор технологии и интеграционные паттерны; названных методологий греп не показал (честно: файлы целиком не цитирую).

**step-05-implementation-research.md** — **Названо в файле:** KPI («### Success Metrics and KPIs» — грепом подтверждено).

**step-06-research-synthesis.md** — финальный синтез по общему каркасу.

---

## 2.4 `bmad-document-project` — документирование brownfield-проекта для AI-контекста

### Крупица: манифест + workflow
**Шаг:** SKILL.md + workflow.md
- **Названо в файле:** «brownfield» («Document brownfield projects for AI context» — термин software engineering, заимствован из строительства; в индустрии с 2000-х).
- **Узнаваемо без имени:** docs-as-code (вывод в `{project_knowledge}` markdown-файлами; **medium**).
- **Собственное изобретение BMAD:** формулировка цели «for AI context» — документация адресована не человеку, а LLM-конвейеру (выход = вход для brownfield PRD в Phase 2); двухфайловая точка входа (SKILL = 1 строка, workflow = конфиг + делегирование в instructions.md).
- **Настроить под себя:** `project_knowledge` в `$B/bmm/config.yaml` (в этой инсталляции → `docs/`).

### Крупица: router + resume
**Шаг:** instructions.md
- **Названо в файле:** ничего не названо.
- **Узнаваемо без имени:** checkpoint/restart по state-файлу `project-scan-report.json` с JSON Schema (`templates/project-scan-report-schema.json`; workflow-движки; **high**) · архивная ротация (`.archive/project-scan-report-{{timestamp}}.json`; log rotation; **high**) · staleness TTL («state file age >= 24 hours → Starting fresh scan»; cache invalidation; **medium**).
- **Собственное изобретение BMAD:** XML-подобный DSL `<workflow><step n=...><check if=...><ask>` — управляющие конструкции прямо в markdown-промпте (исполнитель — LLM, не интерпретатор) · «CONDITIONAL CSV LOADING FOR RESUME» — на резюме грузится только строка нужного project_type (токен-экономия) · меню Resume/Fresh/Cancel.
- **Замысел автора:** скан большого репо не влезает в одну сессию — состояние обязано жить на диске, а резюме обязано быть дешёвым.
- **Настроить под себя:** TTL 24 часа; тексты меню.

### Крупица: full scan — уровни глубины и аудит
**Шаг:** workflows/full-scan-instructions.md (12 шагов)
- **Названо в файле:** ничего из методологий; «brownfield PRD» как downstream-потребитель.
- **Узнаваемо без имени:** трёхуровневая глубина скана Quick (2-5 мин, pattern-based) / Deep (10-30 мин, критические директории) / Exhaustive (30-120 мин, все исходники) — tiered scanning как у SAST/инвентаризационных сканеров (**medium**) · table-driven design — вся логика «что сканировать» вынесена в CSV (**high**) · audit trail дисциплина: «Every time you touch the state file, record: step id, human-readable summary (what you actually did), precise timestamp... Vague phrases are unacceptable» (audit logging; **high**).
- **Собственное изобретение BMAD:** объяснение механики детекта пользователю прямо по ходу («How Project Type Detection Works») — самодокументирующийся процесс · per-step записи в `completed_steps` с человекочитаемым summary · финал = `index.md` мастер-индекс + явная команда-handoff «point the PRD workflow to: {{project_knowledge}}/index.md».
- **Замысел автора:** документировать «как есть» с управляемой стоимостью: пользователь выбирает глубину, машина оставляет проверяемый след каждого шага.
- **Настроить под себя:** дефолтный scan level; шаги 4-9 (какие документы генерятся — source tree, dev guide, integration architecture, per-part architecture).

### Крупица: данные детекта
**Шаг:** documentation-requirements.csv (12 строк × 24 колонки)
- **Названо в файле:** ничего.
- **Узнаваемо без имени:** сигнатурная идентификация типа проекта по key_file_patterns (`package.json;tsconfig.json` → web) — как linguist/детект экосистемы в CI (**medium**).
- **Собственное изобретение BMAD:** одна CSV объединяет детект (`project_type_id`, `key_file_patterns`) и требования к документации (`requires_api_scan`, `requires_data_models`, ... `auth_security_patterns`, `schema_migration_patterns`) — «scan guide: WHERE to look and WHAT to document».
- **Настроить под себя:** **главная ручка скилла** — добавить свою строку (например, `bmad-module` или внутренний фреймворк) с паттернами и флагами; поправить critical_directories под свой layout. Шаблоны вывода — `templates/index-template.md`, `project-overview-template.md`, `source-tree-template.md`, `deep-dive-template.md`.

---

## 2.5 Агенты-персоны: Mary и Paige

### Крупица: Mary — Business Analyst
**Шаг:** bmad-agent-analyst/SKILL.md + bmad-skill-manifest.yaml
- **Названо в файле:** Porter's Five Forces, SWOT analysis, root cause analysis, competitive intelligence («draw upon Porter's Five Forces, SWOT analysis, root cause analysis, and competitive intelligence methodologies to uncover what others miss»).
- **Узнаваемо без имени:** elicitation-принципы BABOK («Articulate requirements with absolute precision. Ambiguity is the enemy of good specs»; «Ensure all stakeholder voices are heard» — stakeholder analysis; **medium**) · evidence-based практика («Ground findings in verifiable evidence»; **medium**).
- **Собственное изобретение BMAD:** persona-as-router — агент не делает работу, а маршрутизирует в 6 скиллов через таблицу Capabilities (BP/MR/DR/TR/CB/DP) с «STOP and WAIT for user input» и запретом «DO NOT invent capabilities on the fly» · принудительное удержание персоны («you must not break character until the user dismisses this persona», «when the user calls a skill, this persona must carry through») · «treasure hunter» communication style — эмоциональный тюнинг LLM.
- **Замысел автора и честная оговорка:** Porter/SWOT/root cause здесь — **прайминг персоны, а не процедура**: ни один step-файл Phase 1 не содержит шага «построй матрицу пяти сил» или «заполни SWOT-квадрант» (единственная операционализация SWOT — один слот в market step-05). Автор рассчитывает, что названные фреймворки активируют нужные знания модели «naturally in conversation, without making it feel academic».
- **Настроить под себя:** строка `principles` в manifest + SKILL.md (заменить набор фреймворков на свой — например PESTLE + JTBD); таблица Capabilities (добавить свой скилл строкой); communication style. Учитывать дубль: персона определена и в `bmad-agent-analyst/`, и в `$B/_config/agent-manifest.csv` (для party mode) — менять синхронно.

### Крупица: Paige — Tech Writer
**Шаг:** bmad-agent-tech-writer/SKILL.md (+ промпты write-document.md, mermaid-gen.md, validate-doc.md, explain-concept.md)
- **Названо в файле:** CommonMark (спецификация Markdown, 2014), DITA (OASIS-стандарт структурированной документации), OpenAPI, Mermaid.
- **Узнаваемо без имени:** task-oriented writing («Every technical document helps someone accomplish a task» — минимализм Джона Кэрролла, *The Nurnberg Funnel*, 1990; имя не названо; **medium**) · plain language / экономия слов («every word and phrase serves a purpose» — Strunk & White-дух; **low**) · audience analysis («Understand the intended audience or clarify» — основа technical communication; **high**) · «diagram is worth thousands of words» — visual-first документация (**low**).
- **Собственное изобретение BMAD:** capability-таблица смешанного типа — «Skill or Prompt»: пункты ведут либо в скилл (DP → bmad-document-project), либо в локальный prompt-файл из той же папки.
- **Настроить под себя:** добавить свой prompt-пункт (например «release notes по диффу») — файл рядом + строка в таблице; принципы письма.

---

## 2.6 `bmad-advanced-elicitation` (core) — 50 методов улучшения свежего вывода

### Крупица: механизм скилла
**Шаг:** SKILL.md
- **Названо в файле:** в description как примеры триггеров — «socratic, first principles, pre-mortem, red team» (имена методов из CSV).
- **Узнаваемо без имени:** итеративный self-critique цикл «применить метод → показать улучшение → снова меню» — родня LLM-паттернов Self-Refine (Madaan et al., 2023) и Reflexion (Shinn et al., 2023), в файле работы не названы (**medium**) · контекстный подбор 5 методов из 50 по «content type, complexity, stakeholder needs, risk level, creative potential» — risk-based выбор техники (**medium**).
- **Собственное изобретение BMAD:** меню `1-5 / r (Reshuffle) / a (List All) / x (Proceed)` с обязательным re-offer после каждого метода · **гейт применения с забыванием**: «Ask the user if they would like to apply the changes (y/n)... IF No, discard your memory of the proposed changes» — анти-контаминация документа отвергнутыми правками · INTEGRATION-режим: скилл встраивается в чужой workflow и возвращает улучшенную секцию по 'x' · party mode — «If party mode is active, agents will join in»: персонажи из `$B/_config/agent-manifest.csv` (Mary, John, Winston, Amelia...) играют роли в мульти-персонных методах · колонка `output_pattern` в CSV («thesis → antithesis → synthesis») как гибкий flow-гайд исполнения.
- **Замысел автора:** превратить «а сделай получше» в управляемую процедуру: банк методов отделён от механики, выбор объясним, каждое улучшение проходит человеческий гейт, цикл не завершается сам.
- **Настроить под себя:** methods.csv — добавить/удалить методы (формат: num, category, method_name, description, output_pattern); число предлагаемых методов (5); путь `agent_party` в frontmatter — своя труппа персон.

### Крупица: methods.csv — 50 методов с атрибуцией по категориям

| Категория (кол-во) | Методы | Происхождение (атрибуция, confidence) |
|---|---|---|
| **collaboration (10)** | Stakeholder Round Table · Expert Panel Review · Debate Club Showdown · User Persona Focus Group · Time Traveler Council · Cross-Functional War Room · Mentor and Apprentice · Good Cop Bad Cop · Improv Yes-And · Customer Support Theater | стейкхолдер-воркшопы BABOK (**medium**) · экспертные панели/Delphi-дух, RAND 1950-е (**low**) · диалектика — output_pattern буквально «thesis → antithesis → synthesis» (Гегель/Фихте; **high**) · фокус-группы (Мертон, 1940-е) + персоны Купера (**medium**) · future-self коучинг, Hershfield (**medium**) · трио desirability-feasibility-viability названо в описании — IDEO design thinking lens (**high**) · когнитивное ученичество/обучение-через-преподавание (**medium**) · полицейский троп (**high**) · импров-театр (**high**) · ролевая игра/CS-практика (**medium**) |
| **advanced (6)** | Tree of Thoughts · Graph of Thoughts · Thread of Thought · Self-Consistency Validation · Meta-Prompting Analysis · Reasoning via Planning | **вся категория — LLM-research papers 2022-2024, названы почти по заголовкам статей**: Tree of Thoughts (Yao et al., 2023) · Graph of Thoughts (Besta et al., 2023) · Thread of Thought (Zhou et al., 2023 — в описании даже «essential for RAG systems») · Self-Consistency (Wang et al., 2022) · meta-prompting (Suzgun & Kalai, 2024) · Reasoning via Planning / RAP (Hao et al., 2023). Все **high** |
| **competitive (3)** | Red Team vs Blue Team · Shark Tank Pitch · Code Review Gauntlet | военная/ИБ-практика red teaming (**high**) · ТВ-шоу Shark Tank как формат стресс-теста value proposition (**high**) · софтверная code review культура (**high**) |
| **technical (5)** | Architecture Decision Records · Rubber Duck Debugging Evolved · Algorithm Olympics · Security Audit Personas · Performance Profiler Panel | **ADR назван** (Michael Nygard, 2011) · rubber duck — *The Pragmatic Programmer* (Hunt & Thomas, 1999; **high**) · benchmarking-практика (**medium**) · threat modeling мульти-перспективой (**medium**) · full-stack profiling (**medium**) |
| **creative (6)** | SCAMPER Method · Reverse Engineering · What If Scenarios · Random Input Stimulus · Exquisite Corpse Brainstorm · Genre Mashup | Eberle 1971 (**high**) · backcasting/working backwards-механика (**medium**) · сценарное мышление (**medium**) · de Bono Random Entry (**high**) · **«изысканный труп» сюрреалистов** — круг Андре Бретона, 1920-е, имя сохранено (**high**) · cross-pollination/Medici Effect (**medium**) |
| **research (3)** | Literature Review Personas · Thesis Defense Simulation · Comparative Analysis Matrix | академический peer review (**high**) · защита диссертации (**high**) · MCDA/weighted scoring (**high**) |
| **risk (5)** | Pre-mortem Analysis · Failure Mode Analysis · Challenge from Critical Perspective · Identify Potential Risks · Chaos Monkey Scenarios | **pre-mortem назван** (Gary Klein, HBR 2007) · FMEA — военный стандарт США, 1949 (**high**) · devil's advocate (**high**) · risk identification PMBOK (**medium**) · **Chaos Monkey назван** (Netflix, 2011) |
| **core (6)** | First Principles Analysis · 5 Whys Deep Dive · Socratic Questioning · Critique and Refine · Explain Reasoning · Expand or Contract for Audience | Аристотель/Маск (**high**) · Toyota (**high**) · Сократ (**high**) · редакторский цикл + дух Self-Refine (**medium**) · показ хода рассуждений — CoT-родня (**medium**) · адаптация под аудиторию, tech writing (**medium**) |
| **learning (2)** | Feynman Technique · Active Recall Testing | **Фейнман назван** (популяризация Scott Young и др.; **high**) · retrieval practice — Roediger & Karpicke, 2006 (**medium**) |
| **philosophical (2)** | Occam's Razor Application · Trolley Problem Variations | **Оккам назван** (Уильям Оккам, XIV в.) · **trolley problem назван** (Philippa Foot 1967, Judith Jarvis Thomson) |
| **retrospective (2)** | Hindsight Reflection · Lessons Learned Extraction | prospective hindsight (Mitchell, Russo & Pennington, 1989 — научная основа premortem; **medium**) · lessons learned/AAR — военная и проектная практика (**high**) |

Главная находка таблицы: **CSV — единственное место Phase 1, где индустрия LLM-промптинга процитирована напрямую** (категория advanced = шесть статей 2022-2024 под их собственными названиями), бок о бок с Гегелем, сюрреалистами и Toyota.

---

## 3. Карта настройки (полная)

### 3.1 Все ручки

| Ручка | Файл | Что меняет | Переживёт обновление BMAD? |
|---|---|---|---|
| Язык общения / язык документов, имя, skill level, пути артефактов | `$B/bmm/config.yaml`, `$B/core/config.yaml` | Все скиллы Phase 1 (читают через bmad-init) | **Да по смыслу** — значения генерятся инсталлером из ваших ответов; при апдейте перепроверить, файл помечен «Generated by BMAD installer» |
| Библиотека техник брейншторма | `$B/core/bmad-brainstorming/brain-methods.csv` | Все 4 режима выбора техник; +строка = +техника везде | **Нет** — файл захеширован в `$B/_config/files-manifest.csv`, апдейт модуля перезапишет; держать копию правок в git/патче |
| Банк методов elicitation | `$B/core/bmad-advanced-elicitation/methods.csv` | Меню 1-5/r/a/x во всех скиллах, зовущих elicitation | **Нет** (то же — vendored, hash-tracked) |
| Детект типов проекта и план скана | `$B/bmm/1-analysis/bmad-document-project/documentation-requirements.csv` | Что и где сканирует document-project | **Нет** (hash в files-manifest) |
| Труппа персон для party mode | `$B/_config/agent-manifest.csv` | Кто «приходит» в мульти-персонные методы elicitation; принципы Mary/John/Winston | **Нет**; менять синхронно с SKILL.md соответствующего агента |
| Триггер-фразы автоактивации | `description` в каждом SKILL.md | Когда скилл ловится по бытовой фразе | **Нет** (vendored) |
| Числовые политики брейншторма | `bmad-brainstorming/workflow.md` (квота 100+, пивот каждые 10), `steps/step-03...` (temperature 0.85, 30-45 мин, Energy Checkpoint 4-5), `steps/step-04...` (критерии Impact/Feasibility/Innovation/Alignment) | Поведение сессии | **Нет** |
| Скелеты выходных документов | `bmad-brainstorming/template.md`, `research/*/research.template.md`, `bmad-product-brief/resources/brief-template.md`, `bmad-document-project/templates/*` | Структура артефактов + state-поля frontmatter | **Нет** |
| Блоки вопросов интервью | `bmad-product-brief/prompts/guided-elicitation.md` | Что спрашивают при брифе (4 блока, 15 вопросов) | **Нет**; менять зеркально с brief-template.md |
| Состав ревью-панели | `bmad-product-brief/prompts/draft-and-review.md` + `agents/*.md` | Линзы skeptic/opportunity/contextual, дефолт GTM | **Нет** |
| Бюджеты субагентов | `agents/artifact-analyzer.md` (≤8 буллетов, >50 стр.), `agents/web-researcher.md` (3-5 поисков, ≤5 буллетов) | Токен-стоимость discovery | **Нет** |
| Поисковые запросы research-шагов | `research/*/steps/step-0N-*.md` (дословные «Search the web: ...») | Куда смотрит рисёрч | **Нет** |
| Дистиллят и headless-JSON | `bmad-product-brief/prompts/finalize.md` | Контракт с PRD-фазой и вашим оркестратором | **Нет** |
| Capabilities-меню агентов | `bmad-agent-analyst/SKILL.md`, `bmad-agent-tech-writer/SKILL.md` (+ manifest.yaml рядом) | Что предлагают Mary/Paige | **Нет** |
| Пути вывода | `planning_artifacts`, `project_knowledge` в `$B/bmm/config.yaml` | Куда падают брифы/рисёрчи/доки | **Да** (конфиг) |

### 3.2 Механика обновления — что важно знать

- Инсталлер ведёт `$B/_config/manifest.yaml` (версии модулей) и `$B/_config/files-manifest.csv` — **SHA-256 хеш каждого установленного файла**. То есть любые ваши правки внутри `core/` и `bmm/` детектируемы инсталлером и при обновлении модуля будут перезаписаны/конфликтны.
- **Папки `custom/` в этой инсталляции нет** — официального overlay-механизма для переживания апдейтов в файлах не видно (в файлах этого нет). Практический вывод: (1) надёжно переживают апдейт только `config.yaml`-значения и ваши выходные папки (`planning-artifacts/`, `implementation-artifacts/`, `docs/`); (2) правки CSV/шаблонов/промптов держать как git-коммиты или патч-файлы поверх vendored-дерева и реаплаить после обновления; (3) альтернатива без касания vendored-файлов — собственные скиллы-обёртки вне `_bmad/` (в `.claude/skills/`), которые ссылаются на BMAD-файлы, добавляя свои шаги до/после.
- Самые «дешёвые и живучие» кастомизации по соотношению эффект/риск: `config.yaml` → CSV-данные (одна строка = новая техника/метод/тип проекта) → шаблоны документов → числовые политики в steps → роли/персоны.

---

## 4. Родословная метода: из чего собран Phase 1 и как этим пользоваться

Phase 1 BMAD — это пять школ, сшитых одной дисциплиной. Первый слой — **канон креативности 1950-80-х** (Осборн и CPS, де Боно, Цвикки, Гилфорд, синектика): он живёт в `brain-methods.csv` и квотах/правилах workflow брейншторминга, частично с сохранёнными именами, частично растворённый в формулировках. Второй — **стратегический консалтинг и бизнес-анализ** (Porter, SWOT, BABOK-elicitation, market research-репортинг): характерно, что Porter и root cause существуют только как прайминг персоны Mary, а реальные research-шаги оперируют более простым каркасом «поиск → цитата → confidence level» — декларация богаче операционализации. Третий — **продуктово-венчурная школа 2010-х** (Lean Canvas-лексика moat/unfair advantage, Why-Now Sequoia, aha-moment PLG, MVP, JTBD-вопросы) — она зашита в вопросы product brief, при этом сами фреймворки (Lean Startup, Business Model Canvas, TAM/SAM/SOM) по именам отсутствуют. Четвёртый — **LLM-research 2022-2024**: Chain-of-Thought, semantic clustering, simulated temperature, Tree/Graph of Thoughts, Self-Consistency процитированы напрямую — автор читает arXiv, а не только бизнес-книги. Пятый, и самый ценный для строителя своего пайплайна, — **собственная инженерная дисциплина BMAD**, не имеющая индустриального прототипа: HALT-гейты и FORBIDDEN-запреты, state в frontmatter и JSON-state-файлах, fan-out субагентов с JSON-контрактами и токен-бюджетами, LLM-distillate, graceful degradation в терминах токенов, anti-bias pivot. Практический вывод: содержательные слои (вопросы, техники, фреймворки) — это **заменяемые данные** (CSV, шаблоны, блоки вопросов), которые можно и нужно переписывать под свой домен; а дисциплинарный слой (гейты, state, контракты, бюджеты) — это **несущая конструкция против известных пороков LLM** (преждевременная конвергенция, галлюцинации, потеря состояния, расход контекста), и именно его стоит сохранять при любой кастомизации — об этом же напоминают найденные дрейфы (36+/61 техник в step-02a, `stepsCompleted: [1,2,3,4]` в шестишаговом market research): даже у автора метода данные и инструкции разъезжаются, когда правки не синхронизируют по всей цепочке.