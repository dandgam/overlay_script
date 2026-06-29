# Свод исследований: Virgil · 888 · ядро (CORE) — 2026-06-03

> Произведён research-recap workflow'ом (5 веерных читателей + 1 синтез, 6 агентов, ~464k токенов).
> Источник = ранее написанные research-файлы (брифы, 7 выводов adversarial-агентов, специи Virgil, ECC-ресёрч, память). Новых экспериментов не запускалось — это свод готового.

## Общая картина (overview)

За ~3 недели исследование разбилось на 3 связанных вопроса: (1) что такое Virgil и работает ли он; (2) делать ли своё (888) или хватит BMAD; (3) извлекать ли общее **ядро (CORE)**, объединяющее 888 и Virgil. Итог — одна согласованная стратегия:
- **Virgil** работает на пилотах, но **на паузе** (пилоты прошли, в проде сам не крутится);
- **888** — делать как **OVERLAY (надстройка) над BMAD** (брать документы BMAD, держать только свой enforcement-код, не доверяющий ИИ), и **«ров правды» (живую канарейку) строить ПЕРВЫМ**;
- общее **ядро (CORE)** — явно **отложено** (сначала добить 888, но писать всё параметризованным по project-root, чтобы будущее извлечение было дешёвым).

**Несущая находка adversarial-разбора:** тезис «enforcement и есть наш ров» сегодня верен лишь наполовину — **процессный гейт жив, а ров-правды (канарейка, реально запускающая фичу) НЕ построен и пустышка на 100% реальных задач.**

---

## 1. Virgil

**Что:** автономный Python control-plane, автоматизирует только BMad Phase 4 (имплементацию). Читает planning-артефакты любого target-проекта, строит DAG stories по shared-файлам, спавнит N параллельных `claude -p` воркеров в изолированных git-worktree, каждый гонит create-story → Gauntlet → dev-story → code-review, мержит только через review-gate в `integration/<wave>` (никогда не в main). User-facing имя = **Virgil**, пакет = `bmad-orchestrator`.

**Ключевые находки:**
- Архитектура = Anthropic «Orchestrator-Workers»: central planner → DAG by shared-files → N worktree-воркеров (default 3, target 4-8). ~35.5k LOC код + ~43.4k LOC тесты, ~50 runtime-модулей.
- Безопасность 3-слойная, **PRIMARY = ОС-песочница bwrap** (network=none, host FS read-only, пишется только worktree, `--clearenv` → ключи не текут). Сканер `_scan_bash` — defence-in-depth, НЕ primary (чёрный список bash-текста неисчерпаем — 3 цикла фиксов, каждый дал 5-6 новых обходов). Prod-prerequisite: `apt install bubblewrap`, иначе NoSandbox + громкий warning.
- Жёсткие границы: никогда не пишет в main target напрямую; никогда `--no-verify`/`--force`/`reset --hard` без human approval; дневной token hard-cap; обязательный bmad-code-review перед каждым merge; human-checkpoint каждые 10 stories или на Wave-границе.
- Пилоты ПРОШЛИ (итеративно): 1-й сквозной prod-успех = прогон #4 (Antares Epic 1, merge `48febc0` — ветка integration/1a создана, story 1.3 смержена автономно). 6+ replay-прогонов дали NEW-1..NEW-22+, отсюда инициативы pilot_findings_closure v1→v7. Главный блокер исторически = NEW-7 (integration-ветка не создавалась); NEW-21 = review-runner падал на read-only HOME.
- **Virgil vs bmad-automator = комплементарны, УЛУЧШАТЬ не мигрировать.** Virgil сильнее по физ-безопасности+масштабу (worktree N-pool + DAG, bwrap, cost hard-cap, 43k LOC тестов). Automator сильнее по зрелости self-running одного потока: adaptive retry + PLATEAU-detection, типизированные per-stage verifier contracts, 6-state crash-taxonomy, complexity-score routing, multi-LLM fallback, 4 operator-режима.
- 4 реальных gap'а взять у automator: **A1** adaptive retry+plateau (P0, лечит ручной retry / NEW-9); **A2** типизированные stage-verifier contracts (P0, заменяют хрупкий regex-по-последним-5-строкам в >20 местах); **A4** crash-state taxonomy (P0, уменьшает NEW-26); **A3** complexity-score 2.0 (P1, блокер — переносится ли plateau/complexity на ПАРАЛЛЕЛЬНЫЙ DAG vs sequential automator).

**Решения:** улучшать Virgil (не мигрировать); взять 4 паттерна поверх существующего event-bus+DAG; держать Virgil на HOLD (сначала 888); новые примитивы — project-root-параметризованные.

**Статус:** пилоты проходят, но **в проде сам не крутится**. ADLC Phase 1/2/3 — DONE; Phase 4 Deploy + Phase 5 Monitor — NOT STARTED. Метрики (pass_rate≥85%, escalation≤20%, review_p95≤2, cache_hit≥50%) — baseline-pending. Фактически запаркован за 888.

---

## 2. 888

**Что:** operator-agent builder/enforcer — система, которая **строит и дисциплинирует** ИИ-агентов. Большой вопрос «BMAD уже работает — делать ли своё?» решён через multi-agent workflow (2 Explore + web-research по BMAD/BMB/automator/TEA + 9-агентный advocate/adversary review). **Ответ: делать своё, но как OVERLAY, не форк.** Дифференциатор = код, не доверяющий ИИ (PreToolUse code-gate возвращает exit 2 и физически отклоняет Edit в цикле, до коммита — CI так не умеет, он post-commit).

**Ключевые находки:**
- Центральный тезис: BMAD/BMB/automator/TEA = LLM-КОЛЛАБОРАТОРЫ — направляют и выдают ДОКУМЕНТЫ (даже TEA «Release Gate» = go/no-go документ), полагаются на то, что LLM/человек ПОСЛЕДУЕТ процессу. Структурный дифференциатор 888 = код, не доверяющий LLM (in-loop exit-2 Edit-блок + storm-decider + forced-T9 + канарейка, реально ЗАПУСКАЮЩАЯ фичу).
- **ADVERSARIAL OVERTURN (holds=false), «key nuance» плана:** ров расщепляется на **PROCESS-moat** (code-gate, storm, T9, drift-audit, WIDENET — жив, CI-незаменим для in-loop pre-edit denial, но доказывает лишь «ты подумал/прокрутил/запустил», НЕ «фича работает», и ~60-70% CI-воспроизводим) vs **TRUTH-moat** (criteria→canary + contract-tests — реально невоспроизводимый дифференциатор, но НЕ построен, no-op на 100% боевых Q). Честно: «живой process-gate + непостроенный truth-gate»; устойчивый ров = ОБЕЩАНИЕ, не обладание.
- **КОРЕНЬ «снежного кома инициатив» (canary-gap):** фичи проходят весь пайплайн 888, но падают вживую, потому что гейты проверяют ФОРМУ (структура/наличие/ревью-прошло), а не реальное ПОВЕДЕНИЕ; прогоны шли на stub/mock backend; criteria оставались прозой, не компилировались в исполняемые тесты. Доказательства: BATCH_ID_SHA (unit GREEN, упал вживую), CHAINENTRY (13/13 в temp, пойман только 1-м live-батчем), TBE2 (D1-D6 все найдены live-прогонами). «Отложенная канарейка = будущий баг = будущая инициатива» = двигатель приплода.
- **ADOPT-vs-KEEP** (единый sha256-pinned vendoring-путь = `.bmad-version` + `vendor-update.sh`): ADOPT у BMAD — Epic→Story формат нарезки (BMM), Gherkin/criteria формат (TEA), BMB `.agent.yaml`, bmad-retrospective, methods.csv (уже vendored), automator loop-form как чек-лист. KEEP/BUILD-OWN (ров) — PreToolUse code-gate exit-2-в-цикле, real-execution канарейка, storm-decider + forced-T9, slug_scope резолвер + contract-tests (R11/R12), WIDENET + drift-audit, bwrap-sandbox + worktree + DAG file-mutex. Правило: РОВНО один vendoring-путь — второй был бы наш же single-source-drift на уровне систем.
- **Design 1 (оживить канарейку: ОДИН источник criteria → canary + acceptance + contract) = holds=false, 3 дыры:** H1 SLICER-MISMATCH (парсер якорит h3 `### q:`, ровно 1 шт, а реальные criteria — h2 `## NNN. Q-`, 224-253 шт → копия скана даёт пусто на каждом Q = тот самый класс drift); H2 WRONG-FILE SEAM (canary-писатель кормится per-Q SPEC-доком, не methodology); H3 META-CHECK не подключён (red→green→break→red был только прозой). Фиксы: ОДИН общий h2-slicer для всех парсеров; каноническая локация criteria, доказанная реальным батчем с непустым `.canary.json`; `criteria_test.sh` HARD-FAIL ДО qa-гейта canary-must-pass.
- **Design 2 (Epic→Story→Task над плоским Q) = holds=false, 4 дыры, но read-слой ЗДОРОВ:** H4 drift-audit blindspot (нет registry-строки для оси Q↔story); H5 write-seam (`do_init` positional 7/8-arg → arity-change + lockstep caller = producer↔consumer 8×); H6 partial-failure (story-DONE не определён при Q3-of-5 fail); H7 флаг на неверной стороне (ключи пишутся независимо от флага).
- **Storm-brain Wave 0 SHIPPED 3/3** (`e3bfb87`+): Kusok1 vendored methods single-source (16/16), Kusok2 trigger→methods single YAML + verify_artifact, Kusok3 канарейка дискриминации 10/10 + анти-тавтология (сломал brain→RED→restore→GREEN), флаг ВКЛ. Найден МЁРТВЫЙ флаг в Kusok2 (env-only) → починен на config/.env. Весь план В0→В6 завершён flags-off/canary-armed, затем включён по одному флагу в real-pilot (В1 canary `af61cc9`, В5 decider, В6 T9). 7-й producer↔consumer шов (storm-pending) всплыл live, обобщён через R12; slug↔scope drift консолидирован в ПЕРВЫЙ закрытый drift-класс.
- **Enforcement-gap audit (2026-06-03, 29-subagent workflow):** process-moat почти заряжен (10 флагов ON), но СТРУКТУРНЫЙ, не СЕМАНТИЧЕСКИЙ — НЕТ хука, гоняющего тест изменённого файла в момент правки (все 110 contract-тестов — только на ручном run-all-gates; на edit-time = 0). Конкретный latent-баг: `run-all-gates.sh` метёт только `${ROOT}/scripts/*_test.sh` и ПРОПУСКАЕТ `storm/*_test.sh` → drift-контракты гниют молча перед merge. Также: code-gate.sh срабатывает лишь на Edit|Write → запись через Bash (`cat >`, `sed -i`, `tee`, `python open()`) обходит весь storm/decider/T7/T11. 2 P0-фикса: PostToolUse changed-file→sibling-test router + Stop-hook канарейка на «done». **(Это ровно то, что я закрыл сегодня в Stage 0a.)**
- **ECC («старший брат 888», 63 subagents):** ECC даёт 888 две вещи, которых нет — авто-цикл непрерывного обучения + eval-методология (pass@k/pass^k). Но `block-no-verify.js` ECC (547-строчный shell-парсер) = ровно тот pattern-scan, что 888 сознательно отверг (cite как «Exhibit A» за sandbox>scanner). ECC НЕ валидирует семантическую правду сгенерированных «инстинктов» = «корень приплода», который 888 уже опознал → любой adopted learning-loop ТРЕБУЕТ канарейку/contract-тест на каждый инстинкт.
- Манифест 888 уже кодирует философию рва: A1 «скрипты для форм, LLM для смыслов», A3 «хуки=гарантии, промпты=вероятность», A4 детерминизм на границе исполнения, B10 «enforcement surface = attack surface». Overlay-стратегия с ним согласована.

**Решения:** своё как OVERLAY не форк (adopt через 1 vendoring-путь, держать только enforcement); **truth-moat ПЕРВЫМ**, перед ним Stage 0 «стоп-кровь»; Epic→Story = adopt-not-build (parent-pointer overlay, плоский Q = enforceable leaf, параллельно truth-moat); все 3 дизайна = holds=false пока их МЕТА-тесты (red→green→break→red) не ИСПОЛНЕНЫ; канарейка = жёсткий non-deferrable in-cycle гейт (R10) на МЕХАНИЗМЕ; чинить ядро руками волнами ДО self-run; LLM строго внутри method-filling, «нужен ли storm» = детерминированная trigger×size таблица.

**Статус:** стратегия принята owner'ом (2026-06-03): Overlay-not-fork + truth-moat-first. План написан, НЕ внедрён (design stage). SHIPPED: storm-brain Wave 0 (флаг ON), весь В0→В6 по флагам, vendoring Kusok 1, ПЕРВЫЙ закрытый drift-класс (slug↔scope). **+ СЕГОДНЯ: Stage 0a/0b/0c (стоп-кровь) — 4 коммита `cb3553d`→`f3c6345`.** НЕ построено: truth-moat/канарейка (no-op на 100% Q); BMM-sharding не vendored; 3 дизайна fixed-on-paper.

---

## 3. ядро / CORE

**Что:** вопрос «делать ли общее ядро, объединяющее 888 и Virgil?». **Ответ: ОТЛОЖИТЬ (defer), не извлекать сейчас** — директива owner'а/брифа, не просто рекомендация.

**Ключевые находки:**
- **ВЫВОД = DEFER.** Плана секция (wondrous-petting-oasis.md, строки 57-58) дословно: «FUTURE (НЕ сейчас) — CORE+wrappers. Не извлекать общее ядро и не сливать 888+Virgil сейчас (директива брифа).»
- Стратегическая рамка: ни «одна система на всё» (bloat), ни «две раздельные системы» (рассинхрон пайплайнов = наш же single-source-drift на уровне систем) не верны. Лучшее = CORE (один пайплайн) + тонкие обёртки (888 для скиллов, Virgil для SaaS) — но **сначала добить 888, ПОТОМ извлекать ядро.**
- Единственная дисциплина на период отсрочки: «build-now ради cheap-extract-later» — писать ВСЕ новые примитивы Stage 0/1/2 параметризованными по project-root (как Virgil), без хардкода 888-путей.
- Design 2 подтверждает (строка 99): «Virgil DagPlanner = переиспользование КОНЦЕПЦИЙ, не кода (нет общего lib; реальный reuse = будущая CORE-экстракция)».
- ECC и enforcement-gap-audit — это ADOPT/harden-888 анализы, НЕ build-shared-core. Enforcement-gap усиливает «сначала добить 888».

**Решения:** не извлекать CORE / не сливать сейчас; добить 888 первым; единственное правило — project-root-параметризация; сегодня 888 и Virgil переиспользуют КОНЦЕПЦИИ, не код.

**Статус:** решено отложить в FUTURE; документировано в wondrous-petting-oasis.md; работа по извлечению не начата. Единственный авторитетный источник по вопросу = этот план (grep подтверждает — единственный hit «CORE+wrappers»).

---

## Сквозные выводы (cross-cutting)

1. **BUILD-OWN-VS-BMAD (headline):** делать своё, но OVERLAY не форк. Adopt контент BMAD через 1 sha256-pinned vendoring-путь; держать только enforcement-слой. Owner-confirmed 2026-06-03.
2. **PROCESS-MOAT vs TRUTH-MOAT (несущая, holds=false):** process-moat жив и CI-незаменим для in-loop pre-edit denial, но доказывает лишь «подумал/прокрутил/запустил», ~60-70% CI-воспроизводим. Truth-moat (канарейка из criteria) = реально невоспроизводимый дифференциатор, но НЕ построен (no-op на 100% Q). Поэтому truth-moat строится первым.
3. **CANARY-GAP = корень снежного кома:** фичи проходят все ADLC-фазы, но падают live, т.к. гейты проверяют ФОРМУ не ПОВЕДЕНИЕ, прогоны на заглушках, criteria — проза. Отложенная канарейка = двигатель приплода инициатив. Фикс = жёсткая non-deferrable in-cycle канарейка (R10) на МЕХАНИЗМЕ.
4. **SINGLE-SOURCE-DRIFT = повторяющийся класс отказа** (8×: имя артефакта E5, slug↔scope, storm-pending, jq-policy): один факт в ≥2 местах → копии расходятся → баг на стыке. Лечение = резолвер (writer=reader) + contract-тест на каждый join (R11/R12). Это же — аргумент против второго vendoring-пути и против раскола 888/Virgil на 2 рассинхронных пайплайна. slug↔scope = первый полностью закрытый класс.
5. **ENFORCEMENT СТРУКТУРНЫЙ, НЕ СЕМАНТИЧЕСКИЙ:** гейты проверяют «артефакт на месте / порядок персон», почти никогда «работает ли правка / прошёл ли её тест» — нет хука, гоняющего тест изменённого файла на edit-time. Конкретный latent-баг (WIDENET-glob пропускает storm/*_test.sh) → стал Stage 0a.
6. **ЧИНИТЬ ЯДРО РУКАМИ, НЕ SELF-RUN:** сломанный мозг не починить сломанным мозгом, канарейка на плохой фикстуре врёт → enforcement-ядро (storm-brain, тормоза) чинится волнами, 1-й платный пилот — внутри worktree, 888 не отпускают в self-run пока мета-чеки не исполнены.
7. **FACTORY-VS-PRODUCT риск:** 3 недели строим «инфраструктуру, строящую инфраструктуру» — circular/self-breeding; фабрика могла стать целью, а реальный target-проект не двигается. Явно отмечено как причина добить 888 и доказать truth-moat.

---

## Открытые вопросы (open)

- **TRUTH-MOAT НЕ ПОСТРОЕН:** компилятор criteria→canary спроектирован, не реализован; канарейка no-op на 100% Q. Все 3 дизайна = holds=false пока red→green→break→red не ИСПОЛНЕНЫ.
- **888 НЕ SELF-RUNNING:** enforcement в основном флаг-ON, но СТРУКТУРНЫЙ не СЕМАНТИЧЕСКИЙ; 2 P0-gap'а (PostToolUse changed-file→own-test router; Stop-hook канарейка на «done») + WIDENET-glob фикс. Покрытие предложенного router'а честно <10% storm, ~34% scripts.
- **ФЛАГИ FAIL-OPEN:** ~36k LOC enforcement в одном fail-open флаге от спячки (config/.env: 8+ =off); найдены мёртвые флаги. Нужны жёсткие flip-or-kill даты.
- **BMM-SHARDING НЕ VENDORED:** vendored только methods + retro; формат Epic→Story для Design 2 не вытянут, vendorability проверена чтением не прогоном.
- **VIRGIL ЗАПАРКОВАН MID-MATURITY:** Phase 4/5 не начаты; метрики baseline-pending от реального prod-прогона; 4 automator-adoption'а в backlog (A3 блокирован переносом plateau на параллельный DAG).
- **CORE EXTRACTION ОТЛОЖЕН (не отменён):** нет условия старта кроме «добить 888»; единственный safeguard — «параметризуй по project-root».
- **UPSTREAM CONTRIBUTION ОТЛОЖЕН:** advocate предлагал контрибнуть enforcement-слой как bmad-модуль; owner принял overlay, upstreaming отложил.
- **СЕМАНТИЧЕСКАЯ ПРАВДА ФИКСТУР НЕ ИЗМЕРЕНА:** даже подключённая канарейка на структурно-валидной-но-семантически-фейковой фикстуре пройдёт (vacuous verify не ловится авто); eval-quality gap (0/5) и любой adopted ECC learning-loop требуют per-instinct канарейку, которой нет.

---

## Индекс файлов-источников (все файлы свода)

### A. Стратегия 888 + план
- `/home/server/.claude/skills/888/mainplan.md` — бриф: глубокий анализ + план развития 888 + meta-вопрос «делать ли своё»
- `/home/server/.claude/plans/wondrous-petting-oasis.md` — **главный план** (overlay + truth-moat, roadmap Stage 0→3, секция CORE+wrappers=FUTURE)
- `/home/server/.claude/skills/888/brief-gaps-ACTUALIZED-2026-06-01.md` — актуализированный список gap'ов
- `/home/server/.claude/skills/888/manifesto-888.md` — манифест (A1/A3/A4/B10 — философия рва)

### B. 7 выводов adversarial-воркфлоу (что СОЗДАЛИ план)
- `/home/server/.claude/plans/wondrous-petting-oasis-agent-af6fd928212499c19.md`
- `/home/server/.claude/plans/wondrous-petting-oasis-agent-a32cbd577080f6766.md`
- `/home/server/.claude/plans/wondrous-petting-oasis-agent-ab3d04ce4be4dee1f.md`
- `/home/server/.claude/plans/wondrous-petting-oasis-agent-a8cf483bd6d7e658a.md`
- `/home/server/.claude/plans/wondrous-petting-oasis-agent-a41c01a9f40465d6c.md`
- `/home/server/.claude/plans/wondrous-petting-oasis-agent-ac734f4e613d1c9a0.md`
- `/home/server/.claude/plans/wondrous-petting-oasis-agent-a06d41d185a611817.md`

### C. storm / канарейка
- `/home/server/.claude/skills/888/brief-storm-diagnostics-888.md`
- `/home/server/.claude/skills/888/INVESTIGATION-BRIEF-storm-boundaries.md`
- `/home/server/.claude/skills/888/RESPONSE-storm-decider-design-888.md`
- `/home/server/.claude/skills/888/PLAN-storm-canary-waves-888.md`
- `/home/server/.claude/projects/-home-server-bmad-orchestrator/memory/feedback_canary_gap_root_of_breeding.md` — инсайт «канарейка-гап = корень приплода»

### D. Virgil
- `/home/server/bmad-orchestrator/spec/methodology-virgil.md`
- `/home/server/bmad-orchestrator/spec/spec_master_orchestrator.md`
- `/home/server/bmad-orchestrator/spec/spec_orchestrator_agent.md`
- `/home/server/bmad-orchestrator/spec/research_compare_virgil_vs_automator_2026-05-25.md` — сравнение Virgil↔automator

### E. ядро/CORE + аудиты
- `/home/server/bmad-orchestrator/md/ecc-research-for-888-2026-06-02.md` — ECC («старший брат»), learning-loop + eval
- `/home/server/bmad-orchestrator/md/enforcement-gap-audit-2026-06-03.md` — аудит дыр enforcement (29 subagents)

### F. Сырой вывод workflow'а (этого свода)
- `/tmp/claude-1000/-home-server-bmad-orchestrator/ad8c67d3-c556-4b6d-9389-ea371bc30be5/tasks/w43ltzslu.output` — полный JSON research-map (temp; этот .md = его читаемая версия)
