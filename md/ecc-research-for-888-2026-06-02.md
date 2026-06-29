# ECC (affaan-m/ECC) — глубокий ресерч для 888

> Дата: 2026-06-02 · Источник: https://github.com/affaan-m/ECC (клон в `trash/ECC-repo`)
> Метод: 5 параллельных subagent'ов (instincts/hooks/security/skills/philosophy) + ручная верификация 3 ключевых цитат.
> Получатель: 888 (методология-фреймворк в `~/.claude/skills/888/`) + bmad-orchestrator.

## Что такое ECC

Зрелый (10+ мес ежедневного использования) **operator-фреймворк для AI-агентов** под Claude Code / Codex / Cursor / OpenCode / Zed / Gemini. Масштаб: **63 субагента, ~181–249 skills, 79 legacy-command-shims, hook-система (18 типов событий, 8 активны), AgentShield (внешний npm, 102 правила), instinct-система непрерывного обучения**. По сути — «старший брат» 888: та же идея (дисциплина агента через хуки+skills+правила), но multi-harness и на порядок больше по площади.

## Главный вывод (conclusion-first)

ECC даёт 888 в основном **две вещи, которых у 888 нет**, плюс пачку точечных механизмов:
1. **Автоматизированный continuous-learning loop** (observe → mine → re-inject) — то, что 888 делает вручную (§5 findings).
2. **Eval-методологию** (pass@k / pass^k + anti-pattern checklist) — самое слабое место 888 (eval-suite только Step A).

**Но 888 СИЛЬНЕЕ ECC** в: anti-tautology canary, contract-tests на швах, single-source-drift как закрытый класс, OS-sandbox как primary safety, reaction-tiering (5 patches=STOP), research-persistence. Эти слои **не трогать** — у ECC аналогов нет.

⚠️ **Критичный нюанс безопасности:** ECC `block-no-verify.js` (547 строк shell-парсера для отлова `--no-verify`) — это **ровно тот pattern-scan подход, который 888 сознательно отверг** (Critical Boundary #5: «fix в sandbox, не patterns в scanner»). НЕ перенимать. Использовать как **Exhibit A** — реальное доказательство правоты решения 888 (история комментариев в файле = каталог bypass-за-bypass).

---

## Карта переносимых идей (по подсистемам)

### A. Continuous-learning / instinct loop — ФЛАГМАН

| Механизм | Где в ECC | Мапинг на 888 | Ценность · усилие |
|---|---|---|---|
| **Instinct data-model** (атомарный, confidence-scored) | `skills/continuous-learning-v2/SKILL.md:51-79` (`id/trigger/confidence/domain/source/scope` + `## Action` + `## Evidence`) | Структурная замена `methodology-888.md §5 "active findings"` (сейчас free-form prose) | HIGH · ~1 сессия |
| **Hook-based observation capture** (100% детерминизм, не skill) | `hooks/observe.sh:212-256` — PostToolUse пишет JSONL-строку на каждый tool-call + secret-scrub (`:318-333`) | 888 уже на хуках + `events.jsonl` append-only → добавить PostToolUse-аппендер тривиально | HIGH · ~0.5-1 сессия |
| **Background observer** (Haiku майнит ≥3× паттерны → instinct) | `agents/observer-loop.sh:153-193` (`claude --model haiku`, tail last-500 obs, 4 класса паттернов) | 888 уже использует `claude -p` backend; классы паттернов = bounce'ы review-gate / `code_gate_blocked` reasons / storm-методы | HIGH · ~2-3 сессии |
| **SessionStart re-injection** (conf≥0.7, top-6) | `scripts/hooks/session-start.js:30,347-398` (`INSTINCT_CONFIDENCE_THRESHOLD=0.7`, `MAX_INJECTED_INSTINCTS=6`) | Замыкает петлю; 888 уже инжектит Memory Bank на SessionStart — добавить ранжирование+порог+cap | HIGH · ~0.5-1 сессия |
| **STALE-REPLAY GUARD** на инжектируемой памяти | `session-start.js:592-604` («HISTORICAL REFERENCE ONLY … verify against git before action») | 888 гоняет headless auto-loops, ре-эмитит storm/canary → защита от повторного исполнения «Следующего шага» | **HIGH** · ~0.2 сессии (брать первым) |
| **Confidence-as-graduation-gate** (promote ≥2 проекта, conf≥0.8) | `instinct-cli.py:123-124,1254-1325` | Формальный гейт «parked finding → enforced RULES.md правило»: conf≥X, seen≥N сессий | MED · concept |

**Критичная честность:** ECC **НЕ валидирует семантическую истинность** сгенерированного instinct'а. Это **ровно корень «приплода инициатив»**, который 888 уже опознал (`feedback_canary_gap_root_of_breeding`: структурная проверка ≠ семантическая правда). → При переносе loop'а **обязателен canary/contract-test на сгенерированный instinct**, иначе 888 будет авто-плодить непроверенные правила. ECC здесь слабее 888.

### B. Hook-архитектура

| Механизм | Где в ECC | Мапинг на 888 | Ценность · усилие |
|---|---|---|---|
| **Single resolver + per-hook ID** | `scripts/lib/hook-flags.js:53-65` (`isHookEnabled`, `ECC_HOOK_PROFILE`∈{minimal,standard,strict} + `ECC_DISABLED_HOOKS` CSV) | Лечит ручное управление «какие хуки активны»; даёт профили бесплатно | HIGH · LOW-MED |
| **Consolidated ordered dispatcher** (1 entry на event×matcher, «first blocker wins») | `scripts/hooks/bash-hook-dispatcher.js:22-52,123-148` | Лечит «N хуков на одном событии разъезжаются»; порядок = явные данные (массив), не порядок регистрации | HIGH · MED |
| **Single-writer shared libs** (`scripts/lib/`) | `session-bridge.js`, `resolve-formatter.js`, `resolve-ecc-root.js` — любой факт ≥2 хуков → один resolver | **Repo-wide лекарство от single-source-drift** (named класс 888: jq-политика в 10+ хуках) — ECC применяет R11 как default | HIGH · MED |
| **No `.env` → split-brain by construction невозможен** | весь hook-path читает только `process.env` (нет dotenv-загрузчика) | 888 split-brain = флаг в `config/.env` мёртв в интерактиве. Лекарство: читать persisted-флаг через ОДИН resolver, что зовут и batch и интерактив | **HIGH (диагностика)** · LOW |
| **Accumulate-then-act-once** (idempotency) | `post-edit-accumulator.js` (append на каждый edit) + `stop-format-typecheck.js` (1× на Stop, clear-on-read) | Паттерн для t9-counter / streak-файлов: observe(cheap,append) ≠ act(once,budgeted,clear) | MED-HIGH · LOW |
| **Deny-with-recovery-hint + fail-open-on-state-error** | `gateguard-fact-force.js:784-804` (`allowWithStateWarning()` — state не записался → ALLOW, не deadlock) | Для **advisory** гейтов 888 (НЕ для code-gate — тот сознательно fail-closed primary safety) | MED · LOW |

### C. Безопасность

**AgentShield — внешний npm-пакет (`ecc-agentshield`), НЕ в репо.** Это config-audit/CI-сканер (сканирует `.claude/` артефакты), **не runtime-sandbox** → **не конфликтует** с `runtime/sandbox.py` 888 (другой слой).

| Механизм | Где в ECC | Мапинг на 888 | Ценность · усилие |
|---|---|---|---|
| **13-domain rule taxonomy** | `docs/architecture/agentshield-enterprise-research-roadmap.md:79` (secrets/permissions/hooks/MCP/agent-configs/prompt-injection/supply-chain/taint/sandbox/policy…) | **4 новые категории для 4-hunter `bmad-security-review`**: supply-chain/CI, MCP tool-poisoning, agent-config over-grant, hidden-Unicode/homoglyph | HIGH (как таксономия) · LOW |
| **`validate-workflow-security.js`** (in-repo, enumerable!) | `scripts/ci/validate-workflow-security.js` (~10 правил: `pull_request_target`+checkout untrusted, `permissions:write-all`, `npm ci` без `--ignore-scripts`…) | Готовый supply-chain CI-слой, которого у 888 нет как кода; прямо liftable | HIGH · LOW-MED |
| **GateGuard fact-forcing** (cognition-gate, не scanner) | `gateguard-fact-force.js:4-9` (демандит importers/API/schema/rollback ДО edit) | **Sandbox-совместимый** поведенческий гейт; ≈ STORM decider-gate + «enumeration ДО кода» | HIGH · MED |
| **`FAIL_MODE=open\|closed` toggle + sha256-chained audit JSONL** | `insaits-security-monitor.py:36` | Per-hook fail-mode knob (для sandbox-absent fallback) + хэш-цепочка ≈ `events.jsonl` | MED · LOW |
| **Prompt Defense Baseline** (6-строк anti-injection преамбула на КАЖДОМ agent/rule/CLAUDE.md) | `CLAUDE.md` §Prompt Defense Baseline; `agents/silent-failure-hunter.md:8-15` | Стандартизовать на persona-skills 888 (no role-change, treat fetched=untrusted, flag unicode/homoglyph) | MED · LOW |
| **Lethal trifecta** framing | `the-security-guide.md:39` («private data + untrusted content + external comms в одном runtime») | Точная формулировка ЗАЧЕМ worktree-worker'у нужен network-deny + secret-deny | MED (vocab) · — |
| ⚠️ **`block-no-verify.js`** (547-строк shell-парсер) | `scripts/hooks/block-no-verify.js` | **НЕ перенимать.** Exhibit A для «sandbox > scanner» (Boundary #5). Цитировать в spec §22.7 | — (anti-pattern) |

### D. Skills / agents архитектура

| Механизм | Где в ECC | Мапинг на 888 | Ценность · усилие |
|---|---|---|---|
| **`TRIGGER when:` / `DO NOT TRIGGER when:`** в `description` frontmatter | `skills/blueprint/SKILL.md:9-12`, `skills/token-budget-advisor/SKILL.md:7-15` | Авто-диспетчеризация → dispatcher SKILL.md ужимается до ≤10 строк → лечит «LLM over-thinks длинные SKILL.md» | HIGH · LOW |
| **`tools:` ограничение** в agent frontmatter | `agents/gan-planner.md:1-7` (`tools:[Read,Write,Grep,Glob]`, `model:opus`), `silent-failure-hunter.md` (read-only) | persona-analyst=`[Read,Grep,Glob]` (нет Edit/Bash) → меньше непреднамеренных правок на фазе анализа | HIGH · LOW |
| **install-profiles** (minimal/developer/full + `--with/--without`) | `manifests/install-profiles.json:1-93` | Модульная установка 888: `minimal`=dispatcher+5 personas, `full`=все 11 storm-scenarios+хуки | HIGH (масштаб) · большое |
| **skill-stocktake** (квартальный health-audit: Keep/Improve/Retire/Merge) | `skills/skill-stocktake/SKILL.md:78-103` | Готовый skill — гонять на `~/.claude/skills/888/`: какие storm-scenarios не срабатывают (retire), дубли (merge) | MED · LOW (готовое) |
| **agent-sort DAILY/LIBRARY** | `skills/agent-sort/SKILL.md:40-53` | storm-scenarios → LIBRARY (lazy), persona-phases → DAILY (always) | MED · MED |
| **legacy-command-shims** (backward-compat на переименовании) | `legacy-command-shims/commands/tdd.md` (shim = 1 строка «Apply X skill») | Безболезненная миграция при переименовании методов 888 | LOW · минимальное |

### E. Философия / правила / eval

База ECC (`SOUL.md`, `RULES.md`, `AGENTS.md`) — **слабее 888** (generic coding-hygiene vs 888's anti-LLM-patterns). **Не перенимать prose.** Payload — в enforcement-слое:

| Принцип | Где в ECC | Мапинг на 888 | Ценность · усилие |
|---|---|---|---|
| **GateGuard fact-forcing** (блокирующий enumeration-before-code) | `gateguard-fact-force.js:704-711` («List ALL importers · public funcs · schemas · quote instruction → retry») | Блокирующая форма «enumeration ДО кода» + «cross-impact tracing» (сейчас только prose, НЕ enforced хуком) | **HIGH** · MED (инфра decider-gate уже есть) |
| **EDD / eval-harness** (pass@k≥0.90 capability, pass^k=1.00 regression) | `skills/eval-harness/SKILL.md:22-26,255-264`; `skills/agent-eval/SKILL.md:136-141` (≥1 deterministic judge, pin commit, ≥3 trials) | **Прямо лечит слабейшее место 888** (eval Step A→B). pass^3=1.00 = формализация «76g/0-NEW» прогонов | **HIGH** · MED |
| **config-protection** («не ослабляй гейт чтобы пройти гейт») | `config-protection.js:5-8` (блок edit'ов linter/formatter configs; `pyproject.toml` сознательно исключён) | Блок правок quality-gate config'ов 888 (ruff/mypy/contract-test config) в fix-цикле | MED-HIGH · LOW |
| **search-first decision matrix** (Adopt/Extend/Compose/Build) + anti-duplication write-gate | `skills/search-first/SKILL.md`; `gateguard:723` («confirm no existing file serves this purpose, use Glob» ДО Write) | Дополняет research-persistence 888 (которая про *не потерять* research; эта — *сделать* research ДО кода) | MED · LOW |
| **GAN anti-generosity rubric + per-iteration regression log** | `agents/gan-evaluator.md:28-35,152-157` («Fight your generosity… DO penalize AI-slop») | Усиливает «gate-beats-self-review» 888 (но 888 уже СИЛЬНЕЕ через anti-tautology meta-proof) | MED · LOW |
| **WORKING-CONTEXT.md + parity-test на self-reported counts** | `WORKING-CONTEXT.md:88-90`; `tests/ci/agent-yaml-surface.test.js` | Новый инстанс класса single-source-drift: pin счётчики (skills/hooks/scenarios) parity-тестом против диска | MED · LOW |

---

## TOP-8 для 888 (ранжировано, дубли слиты)

1. **Continuous-learning loop** (observe→mine→inject) — автоматизация §5-findings. HIGH · 3-4 сессии. **Старт с STALE-REPLAY GUARD (0.2 сессии) — мгновенная защита headless-loop'ов.** ⚠️ обязателен canary на сгенерированный instinct (иначе авто-приплод).
2. **GateGuard fact-forcing gate** — блокирующий «enumeration ДО кода» (importers+API+schema+quoted-instruction). HIGH · MED. *(независимо отмечен 2 агентами: security + philosophy)*
3. **Single-source-drift структурное лекарство** — `scripts/lib`-resolver как DEFAULT + убить `.env` split-brain (читать флаг через один resolver и в batch, и в интерактиве). HIGH · MED. *(888's named класс — ECC показывает repo-wide дисциплину)*
4. **EDD eval-словарь** — pass@k/pass^k + eval anti-pattern checklist + agent-eval (≥1 deterministic judge, pin commit, ≥3 trials). HIGH · MED. *(чинит слабейшее место — eval Step A→B)*
5. **Hook-консолидация** — single resolver + per-hook IDs + `ECC_HOOK_PROFILE`/`DISABLED` + ordered dispatcher «first blocker wins». HIGH · MED.
6. **Security-таксономия** — 4 новые hunter-категории (supply-chain/CI liftable из `validate-workflow-security.js`, MCP tool-poisoning, agent over-grant, hidden-Unicode). HIGH (как таксономия) · LOW.
7. **TRIGGER/DO-NOT-TRIGGER frontmatter + `tools:` ограничение** — ужать dispatcher, ограничить persona-toolset'ы. HIGH · LOW.
8. **config-protection gate** — «не ослабляй гейт чтобы пройти гейт». MED-HIGH · LOW.

---

## Что НЕ перенимать / где 888 уже сильнее

| Область | 888 | ECC | Вердикт |
|---|---|---|---|
| Anti-tautology | meta-proof break→RED→restore→GREEN | только «fight generosity» prompt | **888 далеко впереди** |
| Contract-tests на швах | real writer→reader на каждом join | 1 parity-test | **888 впереди** |
| Single-source-drift | закрыт как КЛАСС (R11 resolver) | case-by-case | **888 впереди** (но взять repo-wide дисциплину) |
| Sandbox | bwrap OS-level = primary | только хуки, `ECC_GATEGUARD=off` escape | **888 впереди по safety-модели** |
| Reaction-tiering | 5 patches=STOP | «max 3 retrieval cycles» (мягче) | **888 впереди** |
| Prompt-caching как gate | «cache hit <50% = bug» | cost-tracker есть, не gated | **888 впереди** |
| `block-no-verify.js` | sandbox закрывает структурно | 547-строк arms-race парсер | **НЕ брать — anti-pattern (Boundary #5)** |
| SOUL/RULES prose | 10 build-discipline + 10 LLM-dev rules | generic «80% coverage, files<800» | **НЕ брать prose** |
| project-scoping | solo single-project | hash git-remote, cross-project promote | **N/A для 888** |
| `/evolve` clustering | full ADLC 1→5 с review-gates | dumb keyword-clusterer | **888 ADLC сильнее** — взять только «suggest cluster» триггер |

---

## Предлагаемые Q-задачи для backlog 888

1. **«Авто-извлечение уроков из сессий»** (Q-260602-INSTINCT-LOOP) — P1. observe-хук → background-майнер → SessionStart-инжект + **canary на каждый сгенерированный instinct**. Закрывает давнюю ручную §5-боль. Старт с STALE-REPLAY GUARD как изолированный quick-win.
2. **«Гейт-следователь: факты до правки»** (Q-260602-FACT-GATE) — P1. Порт GateGuard на decider-gate инфру 888: блок первого Edit/файл до grep-importers + named-API. Делает «enumeration ДО кода» enforced, а не prose.
3. **«Eval-словарь pass@k»** (Q-260602-EVAL-EDD) — P1. pass@k/pass^k + anti-pattern checklist для eval-suite Step B. Чинит слабейшее место.
4. **«Единый источник: добить .env split-brain»** (Q-260602-FLAG-RESOLVER) — P2, follow-up к закрытому Q-SLUG-SCOPE-CONSOLIDATE. Все flag-reads через один resolver (batch=интерактив). Применить `scripts/lib`-as-default дисциплину.
5. **«+4 категории security-hunter»** (Q-260602-HUNTER-TAX) — P2. supply-chain/CI (lift `validate-workflow-security.js`) + MCP tool-poisoning + agent over-grant + hidden-Unicode.
6. **«Самодокументируемый dispatcher»** (Q-260602-TRIGGER-FM) — P2. TRIGGER/DO-NOT-TRIGGER + `tools:` в persona frontmatter.

Все имена — человекочитаемые (per `feedback_888_human_names`); Q-ID = internal.

---

## Ключевые файлы ECC для follow-up

- `skills/continuous-learning-v2/{SKILL.md, agents/observer.md, agents/observer-loop.sh, agents/session-guardian.sh}`, `hooks/observe.sh`, `scripts/instinct-cli.py`, `scripts/hooks/{session-start.js, session-end.js, pre-compact.js}`
- `scripts/lib/hook-flags.js`, `scripts/hooks/{run-with-flags.js, bash-hook-dispatcher.js, gateguard-fact-force.js, config-protection.js}`
- `skills/{eval-harness,agent-eval,search-first,gan-style-harness,skill-stocktake,agent-sort}/SKILL.md`, `agents/gan-evaluator.md`
- `scripts/ci/validate-workflow-security.js`, `the-security-guide.md`, `docs/architecture/agentshield-enterprise-research-roadmap.md`
- `manifests/install-profiles.json`, `WORKING-CONTEXT.md`
