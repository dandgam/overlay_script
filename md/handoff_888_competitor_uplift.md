# Handoff: Virgil ← Automator Uplift — для агента 888

**Дата:** 2026-05-21
**Автор:** Claude (Opus 4.7) после deep-audit сессии с user'ом
**Получатель:** 888 dispatcher (Phase 5 Improver или SELF_EXISTING Virgil mode)
**Контекст:** Анализ конкурента `bmad-code-org/bmad-automator` vs наш Virgil (`/home/server/bmad-orchestrator/`)
**Цель:** Сделать Virgil умнее + дешевле по токенам + меньше багов, перенося лучшие паттерны automator'а

---

## 0. TL;DR — что нужно сделать в первую очередь

Три категории работы, в порядке P0 → P2:

| Приоритет | Что | Эффект | Время |
|---|---|---|---|
| **P0** | Token economy: log pre-filter + slim SKILL.md + cascade judges Haiku→Opus | Токены −40-50% | 2-3 недели |
| **P0** | Dual-source verdict (sprint-status + story-file) | Закрывает NEW-9/21/26 архитектурно | 3-4 сессии |
| **P0** | Trim dead code: bot/TTS, auto_split, AnthropicJudge | −3000 LOC, чище | 1-2 недели |
| **P0** | Decompose `agent/run.py` (5226 строк → 6-7 файлов) | maintainability, рефактор без удаления | 1 неделя |
| **P1** | Verifier contracts (deterministic вместо LLM) + complexity scoring | −40% review tokens | 6-10 сессий |
| **P1** | Policy snapshot hash + marker heartbeat | Reproducibility + recovery | 2-3 сессии |
| **P1** | Validate / Edit / Resume CLI modes (first-class) | Operator UX, debug speed | 5-6 сессий |
| **P1** | Bundled adversarial review skill (zero-critical gate) | Quality gap closed | 8-10 сессий |
| **P2** | Step-file refactor agent/run.py → markdown workflow | Сдвиг к automator парадигме | 12-15 сессий |
| **P2** | Bot/TTS deferred — оставить за feature-flag, не deploy | Don't pay for unused | вынос за flag |

**Полная экономия по итогу всех P0+P1:**
- LOC: 35 548 → ~30 000 (−15%)
- Tokens per story: ~400-500k → ~170k (≈ automator уровень) **при сохранении** 4-judge intelligence + DAG + sandbox
- Bug surface: −5 архитектурных классов багов (NEW-9/21/26 типа) не появятся by design

---

## 1. Контекст — что сравнивалось

### Конкурент

`https://github.com/bmad-code-org/bmad-automator` — Python-плагин для Claude Code, делает BMad Phase 4 (Implementation). Установка через `npx bmad-story-automator`. Уже в production npm.

**Размер:** ~10k LOC Python + ~6.8k LOC markdown инструкций (workflow.md + 14 step-*.md + 30+ data/*.md).

**Подход:** logic-in-markdown, thin Python helpers, LLM ведёт процесс читая markdown.

### Наш Virgil

`/home/server/bmad-orchestrator/` — standalone Python пакет, делает то же самое + DAG-параллелизм + worktree+bwrap sandbox + supervisor judges + self-learning.

**Размер:** ~35k LOC Python + 43k LOC тестов.

**Подход:** logic-in-Python, state machine с event subscribers, LLM как tool из машины.

### Verified facts (НЕ перепроверять)

Эти выводы получены в нашей сессии через runtime probe / grep / code-auditor agent:

1. ✅ **Prompt caching работает идеально** — verified probe в `scripts/probe_prompt_caching.py`:
   - Call 1: cache_creation = 44 553 tokens
   - Call 2: cache_read = 44 553 tokens (100% hit ratio)
   - `ephemeral_1h` extended TTL применяется (не дефолт 5 мин)
   - **НЕ нужна** spec на caching fix — было false alarm

2. ✅ **Virgil пишет в BMad-canonical файлы корректно**:
   - `_bmad/implementation-artifacts/sprint-status.yaml` ← через `agent/tools/_common.py:188 write_sprint_status_yaml`
   - Story files `_bmad-output/implementation-artifacts/*.md` с `Status: done` frontmatter
   - **НЕ нужна** spec на format compatibility — было false alarm

3. ⚠️ **Doctor check имеет косметический warning bug** в `runtime/project_registry.py:413`:
   ```python
   detail="no sprint-status.md (first run?)"
   ```
   Ищет дополнительный human-report .md, не canonical .yaml. Сообщение вводит в заблуждение.
   **Fix:** 15 минут, изменить message на «no human-readable progress report yet».

4. ⚠️ **No Anthropic API key** — Virgil использует subscription Claude через `claude -p` CLI:
   - `supervisor/judges/anthropic_judge.py` (251 LOC) — мёртвый код, никогда не вызывался
   - feedback memory: «multi-LLM возвращается когда будет API ключ»
   - automator subscription-only by design — `claude --dangerously-skip-permissions` + `codex exec`

5. ✅ **Tmux, npm, node, claude CLI** — все доступны на сервере:
   - tmux 3.4
   - node v22.22.2
   - claude 2.1.143 (Claude Code)

6. ✅ **Прошлые истории Virgil'ом записаны корректно** — Wave 1a 3/3 на Antares через sprint-status.yaml + story-files + git commits. Backfill НЕ нужен.

### Format conventions (BMad canonical)

Обе системы работают с этой структурой target проекта:

```
<target>/
├── _bmad/
│   ├── bmm/config.yaml                                       # project config
│   └── implementation-artifacts/sprint-status.yaml           # ← CANONICAL state
└── _bmad-output/
    ├── planning-artifacts/epics.md                           # ← ## Epic N / ### Story N.M
    └── implementation-artifacts/<story-prefix>-*.md          # ← story files
```

Story-files в обоих случаях содержат frontmatter с `Status: drafted|review|done`, секции `Acceptance Criteria` + `Dev Agent Record` + `File List`.

---

## 2. Already-written specs (7 штук в `spec/`)

Все 7 спек уже написаны мной в сессии. Они **не прошли formal review**. 888 должен прогнать их через persona-qa и edge-case-hunter ПЕРЕД запуском `/auto-loop-spec-*`.

**Queue file:** `spec/_queue_competitor_uplift.txt`

| # | Spec | Размер | Priority | Зависимости | Закрывает |
|---|---|---|---|---|---|
| 1 | `spec/spec_competitor_quickwins.md` | SHORT (3с) | **P0** | — | feedback_llm_worker_overthinks, NEW-11/12 |
| 2 | `spec/spec_dual_source_verdict.md` | SHORT-MED (3-4с) | **P0** | #1 | NEW-9, NEW-21, NEW-26 архитектурно |
| 3 | `spec/spec_policy_snapshot_marker.md` | SHORT-MED (2-3с) | P1 | #1 | reproducibility, recovery |
| 4 | `spec/spec_operator_first_class_modes.md` | MED-LONG (5-6с) | P1 | #3 | debug speed, replay UX |
| 5 | `spec/spec_verifier_contracts.md` | LONG (8-10с) | P1 | #2, #3 | гибкость, complexity scoring |
| 6 | `spec/spec_adversarial_review_bundled.md` | LONG (8-10с) | P1 | #5, #2, #1 | quality gap |
| 7 | `spec/spec_step_file_runtime_architecture.md` | LONG (12-15с) | P2 | ВСЕ | maintainability + перенос в markdown-парадигму |

Каждая спека имеет:
- Executive Summary
- Goals / Non-Goals
- Изменения по файлам (с конкретными путями)
- Acceptance Criteria
- Test Plan (новые тесты + регрессионные)
- Rollout (feature flag)
- Risks
- Effort estimate
- Dependencies

### Краткий smell test каждой спеки

| Spec | Главная фича | Самая ценная часть |
|---|---|---|
| competitor_quickwins | log_pre_filter, slim SKILL.md, cli_contract_check | log pre-filter → −80% на парсинге |
| dual_source_verdict | resolve_verdict + git_reality_check | three-source fallback → закрывает 3 NEW-XX класса |
| policy_snapshot_marker | snapshot policy hash + marker heartbeat | reproducibility — debug пилотов на порядок быстрее |
| operator_first_class_modes | validate/edit/resume CLI с TUI меню | оператор debug 30мин → 2мин |
| verifier_contracts | runtime/verifier_registry.py + policy JSON | замена inline phase4_subscribers на pluggable verifiers |
| adversarial_review_bundled | virgil-adversarial-review skill в skills_repo/ | zero-critical gate + git reality cross-check + auto-fix loop |
| step_file_runtime_architecture | agent/run.py → agent/runner.py ≤500 + steps-*/ | парадигмальный сдвиг к automator |

---

## 3. NEW recommendations — после bloat audit

Это **дополнительно к 7 спекам**, найдено code-auditor агентом при анализе bloat (35k vs 10k):

### 3.1 P0 — Trim dead code (~3000 LOC)

| # | Файл/папка | LOC | Причина удаления |
|---|---|---:|---|
| T1 | `src/bmad_orchestrator/bot/` (целиком) | ~1700 | Telegram бот + TTS (4 провайдера); в memory `project_backlog_post_mvp` помечено как post-MVP; никогда не запускался в prod |
| T2 | `src/bmad_orchestrator/runtime/auto_split.py` + `story_splitter.py` + `sub_story_executor.py` | 1075 | Memory: «не даёт speedup, только меньше LOC-halt». Кандидат на removal или вынос за flag |
| T3 | `src/bmad_orchestrator/supervisor/judges/anthropic_judge.py` | 251 | Subscription-only, API key никогда не появлялся, dead path в pilot'ах |
| T4 | `src/bmad_orchestrator/runtime/multi_run.py` | 343 | Post-MVP backlog item, тащит surface в core |

**Спека:** написать `spec/spec_trim_dead_code_p0.md` объединяющую T1-T4 (1 неделя работы).

### 3.2 P0 — Decompose god-module

`src/bmad_orchestrator/agent/run.py` — **5226 строк**, 78 функций. Разбить на:

1. `agent/run.py` ≤300 строк (entry point)
2. `agent/pilot_body.py` (real + mock pilot)
3. `agent/worktree_lifecycle.py` (ensure/reset/dirty/reuse)
4. `agent/intent_router.py` (Anthropic dispatch)
5. `agent/cost_emit.py` (cost_tracker integration)
6. `agent/subscribers_wiring.py` (15+ event subscribers)
7. `agent/review_gate_config.py`
8. `agent/completion_tail.py` (`_tail_and_emit_completion` ~400 строк)

**Эффект:** maintainability + снимает блокер для будущих фич. Никакого функционального изменения.

**Спека:** `spec/spec_decompose_agent_run.md` (1 неделя, 100% рефактор по существующим тестам как parity-проверка).

### 3.3 P1 — Cascade judges (Haiku→Opus)

Сейчас review гонит 4 LLM-судьи параллельно через Opus/Sonnet:
- `supervisor/judges/anthropic_judge.py` (dead)
- `supervisor/judges/claude_p_judge.py` (active)
- security_review судья
- supervisor engine верifier

**Паттерн cascade:**
```python
# supervisor/judges/cascade_judge.py (новый)
class CascadeJudge:
    def __init__(self, fast: HaikuJudge, slow: OpusJudge, threshold: float = 0.8):
        ...
    async def classify(self, input_):
        fast_result = await self.fast.classify(input_)
        if fast_result.confidence >= self.threshold:
            return fast_result  # ~85% случаев — Haiku справится
        return await self.slow.classify(input_)  # эскалация на Opus только при uncertain
```

**Эффект:** −60% токенов на review, intelligence не теряется.

**Спека:** `spec/spec_cascade_judges.md` (5-7 сессий).

### 3.4 P1 — Haiku для boring LLM calls

Парсинг логов, классификация событий, normalize story keys — это **simple tasks**, не нужен Opus или Sonnet.

| Файл | Текущая модель | Должна быть |
|---|---|---|
| `runtime/log_filter.py` (после spec_competitor_quickwins) | Sonnet | **Haiku 4.5** |
| `elicitation/llm_judge.py` | Sonnet | Haiku |
| `runtime/intent_router.py` | Opus | Haiku (для simple classification) |

**Эффект:** −85% на тех вызовах.

**Спека:** не отдельная — добавить в `spec_competitor_quickwins.md` как extension.

### 3.5 P1 — Унификация watchdog'ов

3 отдельных детектора отказа worker'а:
- `runtime/worker_silent_failure.py` (237 LOC)
- `runtime/worker_cancellation.py` (236 LOC)
- `runtime/stuck_watchdog.py` (311 LOC)

**Унифицировать в один** `runtime/worker_watchdog.py` с 3 проверками. −500 LOC, проще debug.

**Спека:** `spec/spec_unify_watchdogs.md` (1 неделя).

### 3.6 P1 — Сократить hooks.py (защитный layer)

`agent/safety/hooks.py` — 709 строк deny-list patterns. По CLAUDE.md проекта:
> «Primary safety = bwrap, hooks = defence-in-depth, НЕ наращивать patterns»

Сократить до ~350 строк, оставить только supplemental к bwrap. **−350 LOC.**

**Спека:** `spec/spec_trim_defensive_hooks.md` (3-5 дней).

### 3.7 P1 — Перенос CLI логики из `cli/main.py`

`cli/main.py` — 1858 строк (у automator 166). Логика multi-project / snapshot / settings-resolution должна жить в `runtime/cli_support/`. CLI = тонкий dispatcher.

**Спека:** `spec/spec_trim_cli_main.md` (1 неделя, чистый рефакторинг).

### 3.8 P2 — Изолировать self_learning за feature flag

`self_learning/` — 869 LOC. В CLAUDE.md статус «Layer C deferred». Не удалять, но вынести за `BMAD_SELF_LEARNING=1` flag, не платить за integration overhead в core.

**Спека:** `spec/spec_isolate_self_learning.md` (3-5 дней).

### 3.9 P2 — Replay.py → eval/

`runtime/replay.py` (~280 LOC) — концептуально это eval инструмент, не runtime. Перенести в `eval/replay.py`.

**Спека:** включить в `spec_isolate_self_learning.md` как secondary.

### 3.10 Cosmetic fix — Doctor warning message

`runtime/project_registry.py:413`:
```python
# WAS:
detail="no sprint-status.md (first run?)"
# SHOULD BE:
detail="no human-readable progress report yet"
```

15 минут работы. Не нужна отдельная спека — quickfix.

---

## 4. Полный приоритизированный roadmap

### Phase 0 — Quickfixes (1 день)

| # | Действие | Effort |
|---|---|---|
| 0.1 | Doctor warning message fix (`project_registry.py:413`) | 15 мин |
| 0.2 | README: добавить ссылки на competitor-uplift specs | 30 мин |
| 0.3 | Commit `scripts/probe_prompt_caching.py` (валидация cache на будущее) | 15 мин |

### Phase 1 — Token economy (2-3 недели)

Цель: с ~400-500k tokens/story до ≈220k (≈ automator).

| # | Spec | Размер | Эффект |
|---|---|---|---|
| 1.1 | `spec_competitor_quickwins.md` | 3 сессии | −30-50% per story |
| 1.2 | `spec_cascade_judges.md` (NEW) | 5-7 сессий | −60% на review |
| 1.3 | Haiku-routing для парсинга (расширение 1.1) | 1 сессия | −85% на тех вызовах |

### Phase 2 — Reliability + Trim (3-4 недели)

| # | Spec | Размер | Эффект |
|---|---|---|---|
| 2.1 | `spec_dual_source_verdict.md` | 3-4 сессии | Закрывает NEW-9/21/26 архитектурно |
| 2.2 | `spec_trim_dead_code_p0.md` (NEW: T1-T4) | 1 неделя | −3000 LOC |
| 2.3 | `spec_decompose_agent_run.md` (NEW) | 1 неделя | Maintainability |
| 2.4 | `spec_unify_watchdogs.md` (NEW) | 1 неделя | −500 LOC, проще debug |

### Phase 3 — Operator UX + Reproducibility (2-3 недели)

| # | Spec | Размер | Эффект |
|---|---|---|---|
| 3.1 | `spec_policy_snapshot_marker.md` | 2-3 сессии | Reproducibility + crash recovery |
| 3.2 | `spec_operator_first_class_modes.md` | 5-6 сессий | Debug 30мин → 2мин |
| 3.3 | `spec_trim_cli_main.md` (NEW) | 1 неделя | CLI чистый |

### Phase 4 — Architecture deepening (2-3 месяца)

| # | Spec | Размер | Эффект |
|---|---|---|---|
| 4.1 | `spec_verifier_contracts.md` | 8-10 сессий | Pluggable verifiers, complexity scoring |
| 4.2 | `spec_adversarial_review_bundled.md` | 8-10 сессий | Quality gap closed |
| 4.3 | `spec_trim_defensive_hooks.md` (NEW) | 3-5 дней | −350 LOC |

### Phase 5 — Paradigm shift (опционально, 2-3 месяца)

| # | Spec | Размер | Эффект |
|---|---|---|---|
| 5.1 | `spec_step_file_runtime_architecture.md` | 12-15 сессий | Перенос logic в markdown |
| 5.2 | `spec_isolate_self_learning.md` (NEW) | 3-5 дней | Self-learning за flag |

---

## 5. Эффект всего плана — измеримое

### По LOC

| Слой | До | После P0+P1+P2 | После всех фаз |
|---|---:|---:|---:|
| Production code | 35 548 | ~32 000 | ~28 000 |
| Tests | 43 366 | ~40 000 | ~36 000 |
| **Итого** | **78 914** | **~72 000** | **~64 000** |

### По токенам на 1 story

| Этап | До | После Phase 1 | После Phase 4 |
|---|---:|---:|---:|
| Worker spawn + dev | 200k | 80k | 60k |
| Review (4 judges) | 120k | 50k | 30k |
| Log parsing | 30k | 6k | 4k |
| Retry / autofix | 30k | 15k | 10k |
| Прочее | 20k | 15k | 10k |
| **Итого** | **~400k** | **~165k** | **~115k** |

≈ automator's ~220k уже на Phase 1. Лучше automator'а на Phase 4.

### По багам

| Класс багов | Закрывается | Через |
|---|---|---|
| NEW-9 (exit code перекрывает verdict) | by design | dual_source_verdict |
| NEW-21 (runner не пишет jsonl) | by design | dual_source_verdict |
| NEW-26 (review skill интерактивен → verdict=error) | by design | dual_source_verdict + bundled review |
| NEW-11/12 (ruff/pre-commit регрессии) | by design | cli_contract_check |
| NEW-13 (security_review error → circuit breaker) | смягчается | verifier_contracts |
| Любая convention drift в будущем | by contract | policy_snapshot_hash |

---

## 6. ЧТО НЕ ДЕЛАТЬ — анти-рекомендации

| Действие | Почему НЕ делать |
|---|---|
| **Fork automator + port** | 4-6 месяцев работы. Переоткрытие 40+ NEW-багов которые мы залатали. Их runtime — Claude Code plugin + tmux, наш — standalone Python. Несовместимо на уровне platform. |
| **Spec_prompt_caching_fix.md** | Caching verified working (см. §1.4). Не нужна. |
| **Spec_bmad_format_compatibility.md** | False alarm. Virgil пишет в .yaml корректно. Не нужна. |
| **Backfill истории прошлых пилотов** | Они уже корректно записаны. Не нужно ничего восстанавливать. |
| **Multi-LLM сейчас** | Pilot'ы не закрыты, single-LLM не стабилизирован. Сначала MVP, потом multi-LLM. Backlog: `project_backlog_post_mvp`. |
| **Удалить Codex support до его внедрения** | Codex backlog item. Можно отложить как часть multi-LLM. |
| **Пытаться полный run-comparison на одном target** | Они incompatible на уровне sprint-status location internals. Принцип работы уже понят из §1 + чтения workflow.md. |

---

## 7. Где смотреть конкурента (для архитекта)

### Локально клонировано
```
/tmp/bmad-automator/    # git clone из github
```

### Ключевые файлы конкурента

| Файл | Что почитать |
|---|---|
| `/tmp/bmad-automator/README.md` | Quickstart + философия |
| `/tmp/bmad-automator/docs/how-it-works.md` | Архитектурная модель + mode routing |
| `/tmp/bmad-automator/docs/story-execution.md` | Per-story lifecycle + review loop |
| `/tmp/bmad-automator/docs/review-workflow.md` | Bundled review skill с git-reality |
| `/tmp/bmad-automator/docs/state-and-resume.md` | State doc + marker + resume/validate/edit |
| `/tmp/bmad-automator/docs/agents-and-monitoring.md` | Codex vs Claude + monitor states |
| `/tmp/bmad-automator/skills/bmad-story-automator/workflow.md` | Эталонный пример: 172 строк markdown орчестрирует процесс |
| `/tmp/bmad-automator/skills/bmad-story-automator/data/orchestration-policy.json` | Эталон policy JSON для §4.1 verifier_contracts |
| `/tmp/bmad-automator/skills/bmad-story-automator/data/complexity-rules.json` | Эталон complexity scoring rules |
| `/tmp/bmad-automator/skills/bmad-story-automator/src/story_automator/core/success_verifiers.py` | Эталон verifier registry (создать аналог в `verifier_registry.py`) |
| `/tmp/bmad-automator/skills/bmad-story-automator/cli.py` | 166 LOC — эталон тонкого CLI |

### Synthetic test target

`/tmp/bmad-cmp/` — синтетический BMad проект (3 stories) для безопасных тестов. Уже установлен automator. 888 может использовать для re-validation новых фич без trips прод-проекты.

---

## 8. Inputs для 888 dispatcher

### Mode

**SELF_EXISTING Virgil** (Virgil уже знаком 888 через `~/.claude/skills/888/methodology-virgil.md`).

### Что 888 должен сделать

1. **Phase 5 Improve / Retro mode:** прочитать этот документ как retro-input, обновить queue в `methodology-virgil.md`
2. Создать Q-IDs для всех items:
   - 7 уже-готовых спек
   - 5 NEW spec'ов из §3 (trim_dead_code_p0, decompose_agent_run, cascade_judges, unify_watchdogs, trim_defensive_hooks, trim_cli_main, isolate_self_learning, doctor_message_fix)
   - = 14 Q-IDs всего
3. Назначить complexity verdict каждой (simple / medium / complex):
   - Quickfixes (Phase 0) — simple
   - Spec _quickwins, _dual_source, _decompose, _watchdogs, _trim_hooks — medium
   - Spec _verifier_contracts, _adversarial_review, _step_file — complex
4. Прогнать 7 existing specs через `bmad-review-edge-case-hunter` (теперь доступен — see verified facts §1.5)
5. Phase-gate каждой спеки перед запуском `/auto-loop-spec-*`
6. После завершения каждой Phase — обновить gap-analysis в methodology-virgil.md

### Что 888 НЕ должен делать

- Не пытаться имплементировать сам (это не его роль — 888 dispatcher)
- Не открывать BMB для написания кода (BMB строит agents/skills, не правит Python пакеты — несовместимо с нашими спеками)
- Не пересматривать verified facts §1 — там опровергнутые гипотезы
- Не запускать полный pilot через automator (15-20k tokens, не нужно)

### Имплементация — где

Phase 4 Implementation НЕ через 888 / BMB. Через:
- `/auto-loop-spec-short spec/spec_X.md` для SHORT/MEDIUM спек
- `/auto-loop-spec-long spec/spec_X.md` для LONG спек
- Или прямая Claude Code сессия + `/featurenew` для одиночных задач

888 management слой над этим (planning + review + retro), Virgil или прямой Claude — execution слой.

---

## 9. Памятка про архитектурные принципы конкурента

Эти принципы можно использовать как guidelines при ревью наших новых спек:

### P1 — Logic в markdown, не в Python (для повторяющихся flow'ов)
```
Bad:  if step == "create": run_create() elif step == "dev": run_dev() ...
Good: workflow.md → step-01-create.md → step-02-dev.md → ... (Claude reads)
```

### P2 — CLI helper = thin shell, не business logic
```
Bad:  bmad-orchestrator run --project X --wave Y (1858 LOC handler)
Good: parse-epic --file X → JSON ; sprint-status get Y → JSON (LLM решает что дальше)
```

### P3 — Three-tier source-of-truth, формализованный в коде
```
Bad:  if jsonl.verdict == "approve": done = True
Good: resolve_verdict(sources=[jsonl, story_file, sprint_status]) — fallback chain
```

### P4 — Verifier по контракту, не inline Python
```
Bad:  in phase4_subscribers.py 50-line if/elif chain
Good: VERIFIERS["review_completion"](contract={"sourceOrder":...}) — pluggable
```

### P5 — Policy snapshot на каждом run
```
Bad:  config меняется в полпрогона, никто не замечает
Good: snapshot_policy() → hash; на resume verify hash; mismatch → halt
```

### P6 — Marker file с heartbeat
```
Bad:  process died → recovery через грепы events.jsonl
Good: marker.json с pid + heartbeat; TTL-based stale detection
```

### P7 — Validate / Edit / Resume как first-class modes
```
Bad:  manual SQL для debug, replay.py для recovery
Good: virgil validate, virgil edit, virgil resume с TUI menu
```

---

## 10. Final checklist для 888

При обработке этого handoff'а 888 должен:

- [ ] Прочитать §0 TL;DR + §1 Verified facts (не перепроверять)
- [ ] Прочитать §2 existing specs (7 штук в `spec/`)
- [ ] Прочитать §3 new recommendations (8 новых спек требуются)
- [ ] Прочитать §4 полный roadmap (Phase 0 → 5)
- [ ] Прочитать §6 анти-рекомендации (что НЕ делать)
- [ ] Создать 14 Q-IDs в `~/.claude/skills/888/methodology-virgil.md` (queue section)
- [ ] Назначить complexity verdict каждому Q
- [ ] Запустить `bmad-review-edge-case-hunter` параллельно на 7 существующих спек
- [ ] Phase-gate новые 8 спек через persona-architect (если 888 решит, что их надо писать)
- [ ] Обновить gap-analysis в methodology-virgil.md

---

## 11. Контактные точки

- **Synthetic target для тестов:** `/tmp/bmad-cmp/`
- **Cloned competitor:** `/tmp/bmad-automator/`
- **Probe script (caching validation):** `scripts/probe_prompt_caching.py`
- **Spec queue file:** `spec/_queue_competitor_uplift.txt`
- **Этот документ:** `md/handoff_888_competitor_uplift.md`

---

## 12. Termin-словарь (для понимания документа)

- **automator** — bmad-code-org/bmad-automator, конкурент-эталон (~10k LOC).
- **Virgil** — наш bmad-orchestrator (~35k LOC), user-facing name для пакета.
- **BMad canonical** — sprint-status.yaml + story-files; стандартный формат BMad-проекта.
- **Verifier** — функция-проверка успешности шага; возвращает verdict + reason.
- **Cascade judge** — паттерн «дешёвая модель (Haiku) сначала, дорогая (Opus) только при low confidence».
- **Policy snapshot** — заморозка конфига прогона в JSON с hash; защищает от drift.
- **Marker file** — JSON-флаг активного run с heartbeat.
- **Step-file architecture** — workflow.md + steps-*.md как Claude-instructions, thin Python runner.
- **Dual-source verdict** — verdict проверяется по 2-3 источникам (jsonl → story-file → sprint-status) с fallback.
- **Git reality cross-check** — сравнение story.FileList с `git diff --name-only`; ловит hallucinated файлы.
- **Reactive accretion** — наращивание patch'ей без рефактора → bloat.
- **God-module** — один файл с десятками несвязанных функций (наш `agent/run.py` 5226 строк).
- **Defensive duplication** — две независимые проверки одного и того же (hooks vs sandbox deny-list).
- **Built-for-futures** — код для функций которые могут понадобиться, но не используются (TTS, multi-LLM, AnthropicJudge).
- **Trim** — целевое удаление мёртвого кода без потери функционала.

---

**Конец handoff документа.**

888, твой ход.
