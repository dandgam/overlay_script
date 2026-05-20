# Spec — Supervisor LLM-loop + TUI integration

> **Фаза ADLC:** Phase 4 (Deploy) — item #9 в priority queue
> **Patterns used:** P4 Orchestrator-Workers (Supervisor=meta-orchestrator) + P2 Routing (классификация event типа) + P5 Evaluator-Optimizer (Supervisor оценивает свои предыдущие решения)
> **Объём:** 3-4 сессии (M-scale initiative)
> **Status:** ✅ DONE (all 4 sessions complete, M4 AnthropicJudge live)

---

## 1. Зачем это

Сейчас Virgil не полностью автономен — на 5 типах событий он останавливается и ждёт оператора в Telegram/чате:

| Event | Что блокирует |
|---|---|
| `HUMAN_QUERY` | вопрос от worker'а / gate trip / compliance escalation — без ответа дальше не идём |
| `WORKER_HALT_FILE` | worker записал halt-reason — нужно решение «продолжать / убить / новая попытка» |
| `BUDGET_THRESHOLD_HIT` | дневной/wave-бюджет превышен — пауза до подтверждения |
| `WORKER_SILENT_FAILURE` | exit_code=0 но 0 commits — нужна диагностика |
| `COMPLIANCE_SWEEP_NEEDED` | каждые N stories — нужен audit pass |

Без оператора эти события **зависают навсегда** — auto-loop pipeline ломается.

**Supervisor LLM-loop** — это **отдельный LLM-цикл** который:
1. Подписан на эти 5 типов событий
2. Решает: **auto-act / wait / escalate-real-human / abort**
3. Если auto-act → выполняет через существующие tools (`respond_to_elicitation`, `pause_worker`, `escalate_to_human` и т.д.)
4. Если escalate → нотификация в Telegram + TUI banner
5. Если abort → останавливает оркестратор cleanly

Параллельно — **TUI dashboard** (уже есть `cli/tui.py`) показывает текущее состояние Supervisor: какие events в очереди, какие решения принимались последние N минут, текущий budget consumption, активные workers.

**Аналогия:** супервайзер фабрики. Раньше каждый раз когда станок «пик-пик» — звали человека. Теперь супервайзер на этаже сам решает: подкрутить / позвать инженера / остановить смену.

---

## 2. Что уже есть (re-use, не строим заново)

| Компонент | Где | Статус |
|---|---|---|
| Event bus + 24 EventType'а | `runtime/event_loop.py` | ✅ |
| HUMAN_QUERY subscriber (intent-router dispatch) | `agent/run.py:2115` | ✅ ручная диспетчеризация |
| TUI dashboard (`run_live` + `render_once`) | `cli/tui.py` | ✅ readonly snapshot |
| Auto-elicitation engine (Tier 0/1/2 для WORKER_ELICITATION) | `elicitation/` | ✅ |
| `escalate_to_human`, `respond_to_elicitation`, `pause_worker`, `resume_worker` tools | `agent/tools/control.py` | ✅ |
| BudgetGuard с `record_story_cost` + alarms | `agent/safety/budget_guard.py` | ✅ |
| intent-router skill (free-text → tool routing) | `agent/skills/intent-router/SKILL.md` | ✅ |
| Anthropic SDK client с prompt caching | существует в worker pipeline | ✅ |

**Не строим заново:** event bus, TUI rendering, individual control tools, intent-router NL parsing.

**Расширяем:**
- HUMAN_QUERY handler — добавляем LLM-pre-step (decide auto vs escalate) перед текущей диспетчеризацией
- TUI — добавляем секцию «Supervisor activity» (последние N решений + текущий статус LLM-loop)

**Новое:**
- LLM-loop сам (decision engine для 5 event types)
- Policy YAML для Supervisor решений
- Tools для Supervisor-level действий (`pause_pipeline`, `abort_pipeline`, `acknowledge_halt`)

---

## 3. Архитектура

### 3.1 Decision pipeline (P2 Routing + P4 Orchestrator)

```
Event (одного из 5 типов) на bus
    ↓
[Tier 0] Hard rules (policy YAML)
    ├── matches → auto-act / escalate (детерминированно)
    └── no match
[Tier 1] LLM-judge (Sonnet 4.6)
    ├── confidence ≥ 0.85 → auto-act
    ├── confidence ∈ [0.6, 0.85) → escalate + suggested action для оператора
    └── confidence < 0.6 → escalate без suggestion (uncertain)
[Tier 2] Real-human escalation
    ├── Telegram notify
    └── TUI banner + sound
    ↓
Action emit (через существующие control tools)
    ↓
Audit log (control.events.jsonl)
```

### 3.2 Структура модулей

```
src/bmad_orchestrator/supervisor/
├── __init__.py            # public API (re-exports)
├── policy.py              # SupervisorPolicy (pydantic) + YAML loader
├── llm_judge.py           # Anthropic SDK call (Sonnet) — classify event
├── engine.py              # SupervisorEngine: decide(event) → SupervisorDecision
├── actions.py             # Action executor: maps Decision → tool calls
└── audit.py               # JSONL audit log writer для всех решений

src/bmad_orchestrator/runtime/
└── supervisor_subscriber.py   # bus subscriber, glue в event loop

config/supervisor-policy.yaml  # default policy (override-able через --supervisor-policy)

src/bmad_orchestrator/cli/
└── tui.py                 # +1 секция «Supervisor activity» (модифицируется)

tests/
├── test_supervisor_policy.py     # YAML load / validate
├── test_supervisor_engine.py     # Tier 0/1/2 decisions (mock judge)
├── test_supervisor_actions.py    # action executor (mock bus)
├── test_supervisor_subscriber.py # bus integration
└── test_supervisor_audit.py      # audit log format
```

Итого: **6 новых модулей** в `supervisor/` + 1 subscriber + 1 policy YAML + 5 test файлов + правка `tui.py`. Оценка: ~1400 строк.

### 3.3 SupervisorDecision schema

```python
class SupervisorDecision(BaseModel):
    action: Literal[
        "auto_respond",       # выполнить response через tool
        "pause_workers",      # SIGSTOP всех активных
        "abort_pipeline",     # stop_orchestrator() + cleanup
        "escalate_human",     # notify + wait
        "no_op",              # event не требует Supervisor (другой subscriber обработает)
    ]
    tool_calls: list[ToolCall]    # для auto_respond — что вызвать
    confidence: float             # 0.0..1.0 (Tier 1 LLM-judge)
    reason: str                   # человекочитаемое объяснение
    tier: Literal[0, 1, 2]        # каким ярусом принято
    rule_id: str | None           # если Tier 0
    escalation_text: str | None   # для Tier 2 — что показать оператору
```

### 3.4 Policy YAML формат

```yaml
version: 1

defaults:
  llm_judge_model: claude-sonnet-4-6
  confidence_auto_threshold: 0.85
  confidence_escalate_floor: 0.6
  max_actions_per_minute: 10           # rate limit на Supervisor решения
  max_consecutive_escalations: 3       # после 3 подряд → abort pipeline (signal noise)

hard_rules:
  # Tier 0 — деterministic
  - id: budget-hard-cap
    when: { event: BUDGET_THRESHOLD_HIT, ratio_gte: 1.0 }
    action: pause_workers
    reason: "Hard budget cap hit — pause до подтверждения оператором"

  - id: compliance-sweep
    when: { event: COMPLIANCE_SWEEP_NEEDED }
    action: auto_respond
    tool_calls: [{ name: trigger_compliance_sweep }]
    reason: "Routine sweep — выполнить автоматом"

  - id: silent-failure
    when: { event: WORKER_SILENT_FAILURE }
    action: escalate_human
    reason: "Silent failure требует diagnostic (нет automated path)"

llm_judge_prompts:
  # Tier 1 — system prompt для Sonnet
  system: |
    You are a pipeline supervisor for a BMad orchestrator.
    Classify each event into: auto_respond | pause_workers | abort_pipeline | escalate_human | no_op.
    Output JSON: {action, confidence, reason, tool_calls?}
    ...
```

---

## 4. Что Supervisor НЕ делает (out of scope)

| Не делает | Почему |
|---|---|
| Не пишет код | Это работа worker'ов; Supervisor — meta-уровень |
| Не правит target проект | Только control plane (events + tools) |
| Не учится сам (RL) | Self-learning loop — отдельная инициатива Phase 5 |
| Не handle worker_completed (success path) | Уже есть code_review_subscriber и др. |
| Не заменяет manual override через CLI | Оператор всегда может `bmad-orchestrator stop` |
| Не работает на не-Anthropic моделях | Multi-LLM — отдельная миграция (см. methodology §6) |
| Не пишет в БД | Audit идёт в JSONL; БД — отдельная инициатива |

---

## 5. Success criteria (Phase 4 gate)

| Criterion | Threshold |
|---|---|
| Все 5 event типов имеют hard-rule **или** LLM-judge path | 100% |
| Supervisor decision latency p95 (Tier 1 LLM call) | ≤ 5 секунд |
| Audit log записывает 100% решений (Tier 0/1/2) | 100% |
| Rate limit `max_actions_per_minute` enforced | ✅ test |
| Circuit breaker `max_consecutive_escalations` enforced | ✅ test |
| TUI показывает последние N=10 Supervisor решений в real-time | ✅ manual + integration test |
| Unit tests | ≥ 30, coverage ≥ 85% по `supervisor/` |
| Full pytest suite | без регрессий (1538+ PASS) |
| Mypy + ruff | clean |
| Mock-mode integration test: 5 событий → 5 решений → audit consistent | ✅ |

---

## 6. План реализации (3-4 сессии)

### Session 1 — Foundation (M1) ✅ DONE
- `supervisor/policy.py` — pydantic schemas (SupervisorPolicy, HardRule, JudgeConfig, SupervisorDecision)
- `supervisor/audit.py` — JSONL writer
- `config/supervisor-policy.yaml` — default policy (3 hard rules для известных deterministic кейсов)
- Tests: `test_supervisor_policy.py` + `test_supervisor_audit.py` (~15 tests)

**Phase gate session 1:** policy loads, audit writes, hard rules validate. No bus wiring yet.

### Session 2 — Decision engine (M2) ✅ DONE
- `supervisor/llm_judge.py` — LLMJudgeProtocol + StubJudge (real call behind M4 flag)
- `supervisor/engine.py` — `SupervisorEngine.decide(event) → SupervisorDecision`
  - Tier 0 (hard rules) → Tier 1 (LLM judge) → Tier 2 (escalate)
  - Rate limiter (token bucket)
  - Circuit breaker (consecutive escalations counter)
- Tests: `test_supervisor_engine.py` с mock judge (~15 tests)

**Phase gate session 2:** decisions сделаны in-process через mock judge.

### Session 3 — Bus integration + Actions (M3) ✅ DONE
- `supervisor/actions.py` — executor: Decision.tool_calls → bus emit / tool invocations
- `runtime/supervisor_subscriber.py` — bus subscriber для 5+1 event types (+ WORKER_STUCK_TIMEOUT NEW-33.3)
- Wiring в `agent/run.py` (новый subscriber after existing chain)
- Tests: `test_supervisor_actions.py` + `test_supervisor_subscriber.py` (~10 tests)

**Phase gate session 3:** end-to-end mock pipeline — 5 событий → 5 решений → corresponding tool calls / events на bus.

### Session 4 — Real LLM Judge (M4) ✅ DONE
- `supervisor/judges/` пакет — multi-LLM ready architecture
  - `anthropic_judge.py` — `AnthropicJudge`: AsyncAnthropic client, prompt caching, JSON+repair parse, JudgeError on all failures
  - `__init__.py` — re-export + extension guide for future providers
- `JudgeConfig.provider` field in `policy.py` — enum for future multi-LLM routing
- `_supervisor_judge_factory` in `agent/run.py` — wired: `BMAD_SUPERVISOR_LLM=anthropic ANTHROPIC_API_KEY=<key>` → real AnthropicJudge; no-key → StubJudge + warning
- Tests: `test_supervisor_anthropic_judge.py` (23 tests) + 2 subscriber wiring tests. Tests 2206→2231. ruff/mypy clean.

**Phase gate session 4:** `BMAD_SUPERVISOR_LLM=anthropic ANTHROPIC_API_KEY=<key>` activates real Sonnet judge end-to-end.

---

## 7. Открытые вопросы (нужны ответы ПЕРЕД стартом)

### Q1. Real Anthropic call или stub-первая?
**Опции:**
- (a) Сразу wire real Sonnet call (через subscription `claude -p` или API key если появится)
- (b) Stub с pluggable callback (как мы делали с auto-elicitation LLM-judge)

**Рекомендация:** (b) **Stub с pluggable callback.** Real Anthropic подключим в Session 4 за флагом `--supervisor-llm`. Это позволяет всю архитектуру проверить без реальных трат токенов, потом включить когда готовы.

### Q2. Где живёт Supervisor process?
**Опции:**
- (a) Внутри `_run_real_pilot` — отдельный asyncio Task в том же процессе
- (b) Отдельный subprocess (как worker'ы)

**Рекомендация:** (a) **Same process, asyncio Task.** Supervisor нужен low-latency доступ к bus. Subprocess добавил бы IPC overhead без выигрыша.

### Q3. Rate limit — глобальный или per-event-type?
**Опции:**
- (a) Глобальный `max_actions_per_minute=10`
- (b) Per-type budgets (e.g. budget events — 5/min, halt events — 3/min)

**Рекомендация:** (a) **Глобальный** для MVP. Per-type усложнит без понятной потребности.

### Q4. Persist Supervisor state между restart'ами?
**Опции:**
- (a) In-memory (потеря rate-limit / circuit-breaker counter на restart)
- (b) SQLite + WAL

**Рекомендация:** (a) **In-memory.** Counter reset на restart — приемлемо (oрkестратор всё равно перезапускается редко, и event poll cycle короткий).

### Q5. Поведение если LLM judge не отвечает (timeout, network error)?
**Опции:**
- (a) Fail-safe → escalate human
- (b) Fail-open → no_op (другой subscriber обработает)

**Рекомендация:** (a) **Fail-safe escalate.** Безопаснее перевалить решение на человека чем let оrkестратор слепо continue.

### Q6. Где сохраняется audit log?
**Опции:**
- (a) `<orchestrator_home>/.claude/memory/supervisor/decisions.jsonl`
- (b) `<target_project>/_bmad-output/runs/supervisor.events.jsonl`
- (c) Inline в общий `control.events.jsonl`

**Рекомендация:** (c) **Inline в `control.events.jsonl`** с `event_type=supervisor_decision`. Один audit pipeline, не плодим файлов.

---

## 8. Зависимости

**Pre-requisites (всё done):**
- ✅ Event bus + 24 EventType
- ✅ Auto-elicitation engine (Phase 3)
- ✅ TUI baseline
- ✅ Control tools
- ✅ BudgetGuard
- ✅ intent-router skill

**Unlocks:**
- Phase 4 #10 (Production pilot на target проекте) — реально автономный run возможен только с Supervisor
- Vision step 3 (continuous autonomy без оператора)
- Cleaner Phase 5 (Self-learning consolidation — поверх supervisor decisions можно собирать patterns)

---

## 9. Риски + mitigation

| Риск | Likelihood | Mitigation |
|---|---|---|
| LLM judge даёт false-positive auto_respond → выполняется неверный tool | Medium | Confidence threshold 0.85 + circuit breaker + hard-rule overrides для destructive ops |
| Rate limit слишком restrictive → реальные события игнорируются | Low | Конфигурируемо через YAML; default conservative; alerting на rate-limit-hit metric |
| Supervisor зацикливается (его decision → новый event → новое decision) | Medium | Циклы detect'ятся через rate limiter + `event.payload.source != "supervisor"` filter |
| Anthropic API недоступен → весь pipeline зависает | High | Fail-safe escalate + timeout 5s + offline mode = только hard rules |
| TUI rendering пожирает CPU из-за частых updates | Low | Throttle: max 1 render per 500ms |

---

## 10a. Multi-LLM Extension Guide

To add a new provider (e.g. Gemini, OpenAI, Yandex, Ollama):

1. **Create** `src/bmad_orchestrator/supervisor/judges/<provider>_judge.py`.
2. **Implement** `LLMJudgeProtocol` — a single `async def classify(self, input_: JudgeInput) -> JudgeVerdict` method.
   - Raise `JudgeError` on any transport / parse failure (engine automatically falls back to Tier 2 escalate_human).
   - Add prompt caching (ephemeral TTL) on the system block if the provider supports it.
3. **Re-export** from `supervisor/judges/__init__.py`:
   ```python
   from bmad_orchestrator.supervisor.judges.<provider>_judge import <Provider>Judge
   __all__ = [..., "<Provider>Judge"]
   ```
4. **Add provider to `JudgeConfig.provider`** Literal in `supervisor/policy.py`.
5. **Wire** in `agent/run.py:_supervisor_judge_factory` — add a branch for the new provider name.
6. **Add tests** in `tests/test_supervisor_<provider>_judge.py` (minimum 8 tests matching the AnthropicJudge test contract).

No changes needed to `engine.py`, `policy.py` (besides the Literal), or `supervisor_subscriber.py`.

---

## 10. References

- Universal methodology: `~/.claude/skills/888/REFERENCE.md`
- Patterns используемые:
  - P4 Orchestrator-Workers (Supervisor = meta-orchestrator поверх существующего)
  - P2 Routing (классификация event'а в один из 5 action types)
  - P5 Evaluator-Optimizer (опционально — Supervisor оценивает свои предыдущие decisions через retrospective)
- Project methodology: `spec/methodology-virgil.md` §4 Phase 4 item #9
- Template applied: `~/.claude/skills/888/templates/routing-decision-tree.md` (Tier 0/1/2 паттерн)
- Related skill: `agent/skills/intent-router/SKILL.md` — Supervisor дополняет, не заменяет
- Existing infra: `cli/tui.py`, `elicitation/`, `agent/tools/control.py`

---

## 11. Что нужно от user'а ПЕРЕД стартом Session 1

1. ✅/❌ — согласие с архитектурой Tier 0/1/2 (раздел 3.1)
2. ✅/❌ — Q1: stub-first или real-Anthropic-first
3. ✅/❌ — Q2: same-process asyncio Task
4. ✅/❌ — Q3: global rate limit
5. ✅/❌ — Q4: in-memory state
6. ✅/❌ — Q5: fail-safe → escalate
7. ✅/❌ — Q6: audit inline в `control.events.jsonl`

Если по всем 7 рекомендациям согласие — стартуем Session 1.

---

**Last updated:** 2026-05-18
**Status:** Draft v1 — awaiting user review
