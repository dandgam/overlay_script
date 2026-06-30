# Enforcement-Gap Audit — 888 process-moat (2026-06-03)

> **Что это.** Аудит «где не хватает тестов/верифаеров/хуков» в 888-enforcement (`~/.claude/skills/888/` + `~/.claude/hooks/`).
> Цель — пока 888 чинится **вручную через оператора**, усилить enforcement на самом операторе-агенте.
> Метод: workflow из 29 субагентов (6 карт → 3 оси → 20 кандидатов → дедуп → devil's-advocate refute) + независимое чтение
> `code-gate.sh` / `drift-audit.sh` / `regression-smoke.sh` / `settings.json`.
> **Это анализ, НЕ внедрение.** Список с приоритетами и дискриминаторами — что ставить первым решается отдельно.
>
> **Принципы (для агента-разборщика).** Лечить КЛАСС, не место. У каждой проверки — дискриминатор (red→green→break→red),
> доказывающий что она ловит баг, а не пустышка. Честные лимиты: что проверка реально поймает vs нет.

## Окружение и точки входа (для холодного чтения)

- Хук-конфиг: `~/.claude/settings.json` (events: UserPromptSubmit, SessionStart, PreToolUse, PostToolUse, Stop).
- Первичный edit-gate: `~/.claude/hooks/code-gate.sh` (PreToolUse, matcher=`Edit|Write`).
- Storm/decider/T9: `~/.claude/skills/888/storm/` + `~/.claude/skills/888/scripts/storm-decider.sh`, `hooks/t9-counter.sh`.
- Детектор single-source-drift: `~/.claude/skills/888/scripts/drift-audit.sh` (registry-driven, D1 policy/D2 flag/D3 denylist/D4 link).
- Полный gate-suite: `~/.claude/skills/888/scripts/run-all-gates.sh` (**ручной/batch**, не хук).
- Contract-тесты: 110 шт. `~/.claude/skills/888/**/*_test.sh`.
- Класс-память: `~/.claude/projects/-home-server-bmad-orchestrator/memory/feedback_*.md`, `project_milestone_*.md`.

## Текущие флаги (config/.env, на 2026-06-03)

- **ON:** `RGSU_ENABLED`, `DECIDER_ENFORCE`, `T9_FORCED` (LIMIT=3), `FIXTURE_CANARY`, `REQUIRE_JQ`, `PHASE_CHAIN_ENFORCE`,
  `WIRE_PHASE_VALIDATORS`, `TEMPLATE_GATE`, `HKHK_ENFORCE`, `HANDOFF_SMOKE`, `STORM_LLM_BACKEND=claude-p`.
- **OFF:** `STORM_BRAIN_ENFORCE`, `COST_CAP_ENABLED`, `CONSULT_BOUNCE`, `EVOL_ENABLED`, `PERSONA_AGENT_MODE`.

---

## Вывод (одной строкой)

Process-moat **в основном вооружён** (10 флагов on), но он **структурный, не семантический**: проверяет «есть ли артефакт /
правильный ли порядок персон», почти никогда — «работает ли правка / прошёл ли её собственный тест». Главная дыра: **ни один
хук не гоняет тест изменённого файла в момент правки** — все 110 contract-тестов срабатывают только на ручном `run-all-gates`,
на edit-time = 0.

### Code-grounding (проверено)

- `grep run-all-gates` в `settings.json`+`hooks/` = **0 хитов** (только manual; референсы — `widenet.sh`, `lib/test-buckets.sh`, audit-baseline'ы).
- `regression-smoke.sh` = **фиксированный набор 20 сценариев** (`SCENARIOS[]` :339); `file_path` читается лишь для short-circuit (:87-106), маппинга changed-file→`*_test.sh` нет.
- 110 `*_test.sh` на диске, **0 авто-запускается на правке**.
- 4 Stop-хука — все структурные; `FIXTURE_CANARY=on` читают **0 хуков** из settings.json (только `phase-conductor.sh`/`888-batch.sh`).
- `code-gate.sh` стреляет **только** на инструменте `Edit|Write` — запись файла через Bash (`cat >`, `sed -i`, `tee`, `python -c open()`) обходит весь storm/decider/T7/T11.

---

## Матрица (дедуп 20 кандидатов → 10 точек)

Оси давали один и тот же gap дважды; ниже — дедуплицированные точки.

| #  | Точка / событие                                   | Класс бага                                         | Есть сейчас                                  | Дискр. | Приор. |
|----|---------------------------------------------------|----------------------------------------------------|----------------------------------------------|--------|--------|
| 1  | **PostToolUse**: тест изменённого файла на edit   | single-source-drift, silent-fail                   | нет (fixed smoke ≠ targeted)                 | ✅     | **P0** |
| 2  | **Stop**: acceptance/канарейка на «готово»         | canary-gap, «готово»-на-словах                     | нет (4 хука структурные)                     | ✅     | **P0** |
| 3  | `scope_from_path` ↔ `_check_active` (форма scope) | scope-form drift (Q-260602 **OPEN**)               | нет; drift-audit репортит CLEAN              | ✅*    | P1     |
| 4  | artifact-name: writer `_artifact_path` ↔ readers  | E5 name-drift (бил 5-6×)                            | частично (только T9 vs writer)               | ✅     | P1     |
| 5  | flag-spelling: 42 reader'а ↔ `config/.env`         | dead-flag split-brain                              | feature-flag-validator (toggle, не spelling) | ✅*    | P1     |
| 6  | `auto-loop-verdicts.jsonl` writer↔consumer         | «PASS» stale/forged, no-active-Q fail-open         | существование, не sha-binding                | ✅     | P1     |
| 7  | **PreToolUse Write**: новый файл (syntax)           | new-untested/unsafe surface                        | нет на write-time (только commit S-tier)     | ✅     | P1     |
| 8  | 6 `code_gate_blocked` printf ↔ `_t9_streak`        | event-field drift                                  | b1b5b6 покрывает **только** :132             | ⚠      | P2     |
| 9  | `code-gate:149` STUBN ↔ `storm_status:50`          | G1 stub-mirror рассинхрон                          | per-side тесты, не coupling                  | ⚠      | P2     |
| 10 | dead `prompt-regression quick-diff` (settings:205) | dead-enforcement (ложное покрытие)                 | хук есть, но `exit 2`+`\| \| true`           | ✅     | P2     |

`*` — дискриминатор требует переписки в нетавтологичную форму (детали в карточке).

---

## P0 — кровоточит

### 1. PostToolUse — тест изменённого файла сразу после правки

1. **Точка:** `settings.json:192` PostToolUse(`Edit|Write|MultiEdit|NotebookEdit`); `regression-smoke.sh:87-106` (dispatch) и `:339` (`SCENARIOS[]`).
2. **Чего нет:** хук-роутер `tool_input.file_path` → sibling-тест (`X.py→X_test.sh`, `X.sh→X_test.sh`) → запуск с timeout → FAIL немедленно на экран. НЕ fixed smoke, НЕ на merge.
3. **Класс:** single-source-drift + silent-fail. Сейчас contract-тесты (`slug_scope_test`, `storm_status_test`, `decider-gate §E`) ловят слом — но срабатывают на **нуле** правок, которые их ломают, до ручного `run-all-gates`.
4. **Есть:** нет. `regression-smoke` для файлов под `storm/` делает short-circuit→exit 0 (`:102`). Правка `storm/slug_scope.py` гоняет **0** ассертов `slug_scope_test.sh`.
5. **Приоритет:** P0.
6. **Дискриминатор:** правлю `slug_scope.py` так, чтобы `scopes_for_slug`→`[]` (реверт R11 к самогаданию). Роутер резолвит `slug_scope_test.sh §B` (реальный writer→reader) → FAIL → хук кричит. Restore → green. **Не пустышка:** сегодня та же правка даёт `exit 0` от smoke (short-circuit) + 0 других хуков = green на сломанной правке.

**Честный лимит (workflow его занизил):** при строгом `X→X_test` маппинге sibling-тест есть лишь у **2 из 26** `storm/*.py` и **~40 из 116** `scripts/*.sh` → naive-роутер авто-покрывает **<10% storm / ~34% scripts**. Закрывает именно задокументированные drift-швы (у них тесты есть), но не «всё». Растить покрытие — через точку 7. Тавтологичный тест пройдёт (см. `decider-gate §D` hand-seed).

### 2. Stop — acceptance/канарейка на «готово»

1. **Точка:** `settings.json:261` Stop (4 хука): `stop-hook-enforce-check.sh`, `t9-counter.sh`, `phase-chain-stop.sh`, `audit-trail.sh`.
2. **Чего нет:** Stop-хук, который по `active_task.scopes` (`events.jsonl`) находит тронутые scope → гоняет их contract-тест/канарейку → `decision:block` если RED. «Готово» = реально проверено.
3. **Класс:** canary-gap (отложенная канарейка = будущий баг, `feedback_canary_gap_root_of_breeding`), «готово»-на-словах.
4. **Есть:** нет. Все 4 Stop-хука структурные: `stop-hook-enforce-check` проверяет только дисциплину persona-handoff (был ли вызван dispatcher после persona-Skill), не работу. `phase-chain-stop` спит вне batch (нужны `CLAUDE_888_PHASE_CHAIN`+`TIER`). `FIXTURE_CANARY=on` ни один Stop-хук не читает.
5. **Приоритет:** P0 (единственное событие, стреляющее на реальном interactive Stop; задокументированное «всё молчит» окно).
6. **Дискриминатор:** заканчиваю сессию, в которой переименовал поле в `_check_active.py` (`touched_files→touched_paths`), которое не поймал ни code-gate, ни pytest. Сегодня все 4 Stop → allow. С канарейкой: читает scope→`decider-gate_test §E`→FAIL→Stop BLOCK. Реверт→green. Сломать **другой** scope→хук НЕ блокирует (доказывает scoped, не blanket-`run-all-gates`, который всегда RED).

**Честные лимиты:** (1) роутер scope→test надо **построить** (на диске его нет — `consult-router.sh` это defect→persona, не path→test). (2) Покрытие = карта + строгость теста (scope без теста = без канарейки; тавтологичный тест проходит). (3) Латентность: 1-2 теста, не 110 → нужен timeout + bypass-env (`RGSU_DISABLED`-стиль). Реального claude-p reviewer на Stop не гонять (cost) — только channel/contract-тесты.

**Связанные residual'ы (в это же «молчит»-окно, добавить к разбору):**
- code-gate matcher = `Edit|Write` → запись файла **через Bash** (`cat >`, `sed -i`, `tee`, `python -c open()`) обходит весь storm/decider/T7/T11 (`destructive-guard` ловит только rm/DROP/force, читает только `.command`, не Write-content).
- self-override `/tmp/.888_override_<scope>` (`code-gate.sh:123`) **без бюджета** — audit-trail логирует (degrade, Stop), но злоупотребление повтором не блокируется.

---

## P1 — важно

### 3. scope-form drift — единый нормализатор scope (Q-260602 OPEN)

1. **Точка:** `scope_from_path.py` (по каталогу) vs `_check_active.extract_scopes` (по basename, зовёт `scope_from_path:78`) + bash-fallback `code-gate.sh:64-69` (3-й деривер).
2. **Чего нет:** единый резолвер «scope из любой формы» + table-test `prompt_scope(basename F) == path_scope(fullpath F)`.
3. **Класс:** scope-form value drift (родня slug≠scope, `RULES.md:531`, `drift-audit.sh:27` сам признаёт лимит). **Уже OPEN-баг** (`spec/Q-260602-SCOPE-FORM-DRIFT.md`), не гипотеза: блок code-gate непоследователен — зависит от формы пути.
4. **Есть:** нет. `drift-audit` под этой расходимостью репортит CLEAN (нет registry-строки), `slug_scope_test §B` зелёный (доверяет writer).
5. **Приоритет:** P1.
6. **Дискриминатор (исправленный — НЕ тавтология):** строка `F=hooks/code-gate.sh`: bare `"code-gate.sh"→"code-gate-sh"` vs full `"/home/.../hooks/code-gate.sh"→"hooks"` → **RED сегодня** (проверено исполнением). Сравнивает две входные ФОРМЫ через одну функцию, не две функции. Фикс = резолвер, нормализующий и bare-токены, и full-path к одному scope + D4-строка в `drift-audit.sh`. **Лимит:** ловит только перечисленные строки таблицы; не сливает реализации, если фикс не маршрутизирует обе формы через один резолвер.

### 4. artifact-name reader↔writer contract

1. **Точка:** writer `storm-orchestrator.py:690,989` (`_artifact_path`/`--print-artifact-path`) vs readers `code-gate.sh:181` (taxonomy), `:364/386` (regression), `taxonomy-checker.py:38`.
2. **Чего нет:** тест, выводящий имя из writer (`--print-artifact-path T7 <scope>`) и сравнивающий с тем, что строят readers, для **всех** триггеров. В `run-all-gates` gate-2.
3. **Класс:** E5 name-drift (тот самый `t7_` vs `taxonomy_` deadlock, задокументирован `code-gate.sh:177`; класс горел 5-6×).
4. **Есть:** ЧАСТИЧНО — только T9 vs writer (`t9-counter_test.sh:125`). `e5-taxonomy-deadlock_test.sh` = literal-vs-literal (хардкодит имя, writer не зовёт → rename writer'а не поймает).
5. **Приоритет:** P1 (тяжёлые последствия при срабатывании — re-deadlock; низкая частота — только при правке `_artifact_path`).
6. **Дискриминатор:** добавляю в `_artifact_path` суффикс `_v2` для taxonomy → assert `code-gate TAXONOMY_FILE == _artifact_path('T7',scope)` FAIL; зову `--print-artifact-path`→PASS. **Пин на writer как ground-truth, НЕ на литерал** (иначе lockstep-drift writer+reader проходит). Покрыть T7 + T11 (`:386`/`:364`) + `taxonomy-checker.py:43`.

### 5. flag-name reader-spelling contract

1. **Точка:** 42 файла grep'ают `config/.env`; `feature-flag-validator.sh:59`.
2. **Чего нет:** скан, что каждый reader-grep (`BMAD_888_*`) совпадает по написанию с ключом в `.env`; + обратное направление (каждый ключ имеет ≥1 reader = ловит dead-flag «set, читается нигде»).
3. **Класс:** dead-flag split-brain (флаг живёт в батче, мёртв в интерактиве — бил на B1/storm-pending).
4. **Есть:** `feature-flag-validator` проверяет только что у флага **есть toggle-тест**, не написание reader'а. Не покрывает non-prefixed (`STORM_LLM_BACKEND`, `BATCH_PARALLEL_ENABLED`).
5. **Приоритет:** P1.
6. **Дискриминатор:** опечатка `BMAD_888_DECIDER_ENFROCE` в `code-gate.sh` → скан: ключа нет в `.env` → exit≠0 с file:line. Сегодня feature-flag-validator зелёный + decider тихо fail-open. **Лимит:** не ловит верно-написанный reader с **неверным default-on-empty** (тоже fail-open) и динамически-собранное имя (string concat). Эти — отдельный behavioral toggle-test на ПРОДАКШН-хуке.

### 6. handoff verdict-log trust (sha-binding)

1. **Точка:** `auto-loop-verdicts.jsonl` writers (`auto-loop-driver.sh`, `hooks/enforce-auto-loop-before-handoff.sh`) ↔ consumer; `enforce-subagent-before-handoff.sh` (читает `audit/sub-agent-reviews.jsonl`).
2. **Чего нет:** привязка вердикта к sha **того артефакта, что reviewer реально смотрел** (spec-sha для auto-loop; code-diff-sha для subagent) + хук пересчитывает. Закрыть no-active-Q fail-open.
3. **Класс:** «PASS»-на-словах — stale-вердикт от старого diff / forged-строка пускают handoff; `allow "no-active-q"` (`enforce-auto-loop:151`, `enforce-subagent:126`) = дыра шире forge.
4. **Есть:** только существование PASS-строки, без freshness-to-diff.
5. **Приоритет:** P1.
6. **Дискриминатор:** PASS для Q-X на sha S1 → новая правка (S2) → downstream persona. С binding: пересчёт S2, вердикт только для S1 → mismatch → BLOCK. Forged-строка без sha → требуется поле → BLOCK. Re-run на S2 → ALLOW. **Лимит:** sha-binding бьёт stale-reuse, не wholesale-forge (агент впишет fake-but-matching sha); whitespace-only diff меняет sha (ложный re-verify). auto-loop reviewer смотрит SPEC-файл (не working-tree diff) → ключ = spec-sha, не git-diff-sha.

### 7. PreToolUse Write — структурный гейт на новый файл (syntax-leg)

1. **Точка:** `settings.json:114` PreToolUse(`Edit|Write`); `challenge_mode.sh:25` (скип `.claude`), `config_protection.sh` (basename-only), `code-gate.sh:120` (блок только на ≥2-й правке scope).
2. **Чего нет:** на новый `*.sh`/`*.py` под enforced-путями (`scripts/`, `storm/`, `hooks/`, `templates/`) — `bash -n` / `python -m py_compile` (ast.parse) по `tool_input.content` **до создания**.
3. **Класс:** new-untested/unsafe surface — правка самой safety-машинерии = наименее проверяемое действие.
4. **Есть:** нет на write-time. `test-coverage-enforcer.sh` (require sibling-test) — только commit-time, только `scripts/*.sh`, только existence (`:133` выводит имя, не запускает). `s_tier_gate` `bash -n` — только S-tier commit, скипает `.claude`/SKILL.md/templates.
5. **Приоритет:** P1 (syntax-leg). Test-requirement-leg — **drop** (уже покрыт на commit `test-coverage-enforcer`, иначе double-friction).
6. **Дискриминатор:** новый `scripts/foo.sh` с несбалансированным `if` → хук `bash -n`→exit≠0→блок Write. Сегодня все 3 PreToolUse-гейта exit 0 (challenge скипает `.claude`, config_protection basename-miss, code-gate first-touch-pass) — битый файл создаётся без возражений до S-tier commit. **Доказательство дыры:** новый `foo.sh` с телом `rm -rf /` создаётся сегодня с **нулём** объекций (`destructive-guard` matcher=Bash, читает только `.command`, не видит Write-content). **Лимит:** `bash -n`/`py_compile` ловит синтаксис, не логику (`rm -rf /` синтаксически валиден). Нужен audited escape-hatch.

---

## P2 — потом

### 8. printf event-schema contract (остальные 5 emit-сайтов)

- **Точка:** 6 `code_gate_blocked` printf (`code-gate.sh:132,153,199,231,293,397`) ↔ consumer `_t9_streak.py:66`.
- **Чего нет / класс:** field-name parity для **всех 6** сайтов; producer≠consumer на логе.
- **Есть:** ⚠ `b1b5b6-integration_test.sh §2→§3` (реальный code-gate→реальный t9-counter, в gate-2) **уже покрывает :132** (`decider_storm_pending`). Дискриминатор «переименуй :132» **уже RED** под армед-тестом → finding частично-избыточен.
- **Residual (P2):** rename `"scope"` на **:231/:293/:397** в одиночку b1b5b6 НЕ ловит (§2 гоняет только :132); + non-block события (`storm_completed` slug, `manual_override`). **Лимит:** реальный block нужен temp-HOME harness (~14 jq) — **тяжело для PostToolUse-на-каждый-edit**, место — в test-tier, не inline-хук.

### 9. G1 stub-mirror equality

- **Точка:** `code-gate.sh:149` (`STUBN>=SECN`) ↔ `storm_status.py:50` (`STUBN<SECN`).
- **Чего нет / класс:** coupling-тест, что две копии stub-детектора согласны; рассинхрон зеркала.
- **Есть:** ⚠ per-side покрыто (`decider-gate_test:112` ловит off-by-one в code-gate; `storm_status_test:51` — симметрию).
- **Residual (P2):** ввести **второй** маркер-строку (`"TODO: fill"`) только в одну копию → обе per-side зелёные, coupling-тест RED. Это единственное, что текущие тесты пропускают. **Лимит:** ловит только над enumerated fixture-set.

### 10. dead `prompt-regression quick-diff` (hygiene)

- **Точка:** `settings.json:205` зовёт `prompt-regression.sh quick-diff`.
- **Класс:** dead-enforcement — ложное чувство post-edit покрытия.
- **Есть/факт:** `prompt-regression.sh:46` принимает только `capture|compare`, `quick-diff`→`exit 2`, проглочен `|| true`. **Гарантированный no-op** (проверено эмпирически). Bonus: `weekly-audit.sh:68` зовёт `compare` без `--persona`→тоже `exit 2` (dead).
- **Дискриминатор:** инструментируй marker-файл на реальном compare → PostToolUse Edit → marker НЕ пишется. **Рекомендация:** не воскрешать (сравнивает mock-выводы персон, не файл) — переиспользовать слот под роутер точки 1.

---

## PostToolUse — отдельная оценка (особый фокус задачи)

**Реально ли технически?** Да. PostToolUse уже получает `tool_input.file_path` (`regression-smoke.sh:82` его парсит). Роутер тривиален: `file_path`→sibling `*_test.sh`→если есть, запустить.

**Что запускать:** ровно **один** sibling-тест тронутого файла (не все 110, не fixed smoke). Карта: `X.py→X_test.sh`, `X.sh→X_test.sh`; `lib/test-buckets.sh` уже умеет резолвить тесты.

**Замедлит?** Нет, если: (1) per-test timeout 10-15s; (2) short-circuit если sibling-теста нет; (3) bootstrap-immune пути (`settings.json`, self) как у smoke. Сейчас smoke бюджетит 90s на 20 сценариев — один targeted тест дешевле.

**Тонкость (важно).** PostToolUse стреляет **после** применения правки — отменить Edit не может. Но `decision:block` / stderr заставляют исправить тут же, до следующего шага. Выигрыш = слом ловится на edit-time, не на merge/WIDENET.

**Честный лимит:** покрытие = только файлы с sibling-тестом (<10% storm, ~34% scripts) → растить через точку 7 (require-test на новый файл). Тавтологичный тест пройдёт. Поэтому точка 1 закрывает **drift-швы**, не «всё».

---

## Devil's advocate — проверено, НЕ дыра (3 кандидата отброшено)

| Кандидат                              | Почему отброшен                                                                                                                                                                                                                                  |
|---------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `code_gate_blocked` real-printf contract | **Уже покрыт** `b1b5b6-integration_test.sh §2→§3` (реальный emit→реальный consumer, в gate-2). Дискриминатор разворачивает существующий армед-тест в RED — не новая дыра. (Residual по 5 другим сайтам вынесен в точку 8.)                          |
| drift-audit registry-completeness meta   | **Too-costly + тавтология.** Машиночитаемого списка закрытых классов нет (только проза R11/R12/R13). Мета-тест требует **второй** руками-поддерживаемый список = ровно то, что R13 уже мандатит. То же забывание пропустит обе строки → тест зелёный при наличии бага. Все 5 классов уже зарегистрированы. |
| UserPromptSubmit injection-scan          | **Threat-model ошибка.** Prompt как текст инертен — вред требует downstream tool-call, а те уже вооружены (`parry_scan` exec-verbs exit 2, code-gate, destructive-guard, sandbox). Сканить операторский ввод = false-positive'ы при ~0 ценности. Если реформулировать — сканить **untrusted** контент (WebFetch/file-read results), не промпт. |

---

## Что ставить первым (рекомендация)

1. **Точка 1 (PostToolUse changed-file→own-test)** — P0, наибольший ROI, переиспользует слот точки 10, маппинг через готовый `lib/test-buckets.sh`. Закрывает корень «contract-тесты не стреляют на правке».
2. **Точка 2 (Stop канарейка)** использует тот же роутер scope→test → строить **вместе** с точкой 1.
3. **Точка 3 (scope-form)** — единственный **уже-OPEN** баг в списке; маленький резолвер + table-test.

Точки 4-7 — regression-prevention для классов, что горели; после 1-3. Точки 8-10 — hygiene/residual.

---

## Приложение — источники

- Workflow run: `wf_918d8e91-552`, 29 субагентов, 6 карт / 3 оси / 20 кандидатов → 17 survivors + 3 dropped.
- Прочитано напрямую: `code-gate.sh` (412 строк), `drift-audit.sh` (256), `regression-smoke.sh` (471), `inline-first-check.sh`,
  `stop-hook-enforce-check.sh`, `settings.json`, `spec/Q-260602-SCOPE-FORM-DRIFT.md`.
- Все file:line ссылки — на момент 2026-06-03; перед внедрением переразрешить (методичка большая, строки дрейфуют).
