# Spec — Auto-elicitation policy engine

> **Фаза ADLC:** Phase 3 (Test & Release) — закрывает gap «8 policy YAML без LLM-driven логики поверх»
> **Patterns used:** P2 Routing (static rules + dynamic LLM-judge для unknown topics)
> **Status:** Draft, awaiting user approval

---

## 1. Проблема

Worker во время dev-story встречает неоднозначность (например: «какой crypto использовать — Argon2 или bcrypt?», «куда положить тест?», «какое имя переменной?»). Сейчас:

- `elicitation-router` skill wired в `agent/skills/__init__.py:56` на event `WORKER_ELICITATION` ✅
- Example policy YAML с rules-based matching существует (`examples/elicitation-policy.example.yaml`) ✅
- **GAP:** реальной работающей логики «rule match → action» в коде **нет**
- **GAP:** если topic не в правилах → всё эскалируется на человека (escalation_rate высокий)

Цель: 80% решений автоматом (low/medium risk), 20% эскалация (security/PII/destructive).

---

## 2. Архитектура — P2 Routing двухтировая

```
WORKER_ELICITATION event
    ↓
[Tier 0] Hard override check (security/crypto/PII keywords)
    ↓ no match
[Tier 1] Static rule match по policy YAML (topics + file_patterns)
    ↓ no match
[Tier 2] LLM-judge (Haiku) — classify question → (risk, suggested_action)
    ↓
Decision → respond_to_elicitation OR escalate_to_human
    ↓
Window tracking: recent_auto_resolves_per_story > 5 → force escalate
```

**Почему two-tier:**
- Tier 1 (rules) — бесплатно, детерминированно, audit-friendly
- Tier 2 (LLM) — для unknown topics, ~$0.0001 за classify (Haiku)
- Hard override — защита от LLM-bypass (security вопросы НИКОГДА не auto)

---

## 3. Модули

| Файл | Что внутри | Строк ~ |
|---|---|---|
| `src/bmad_orchestrator/elicitation/__init__.py` | re-export `ElicitationEngine` | 5 |
| `src/bmad_orchestrator/elicitation/policy.py` | pydantic `ElicitationPolicy`, `Rule`, `Defaults` + YAML loader | 120 |
| `src/bmad_orchestrator/elicitation/engine.py` | `ElicitationEngine.decide(event) → Decision` | 180 |
| `src/bmad_orchestrator/elicitation/llm_judge.py` | Haiku-based classifier (Tier 2) | 90 |
| `tests/test_elicitation_policy.py` | YAML load / validate / hard overrides | 100 |
| `tests/test_elicitation_engine.py` | Tier 0/1/2 routing + window cap | 180 |
| `tests/test_elicitation_judge.py` | LLM-judge mock-based contracts | 80 |
| **Wiring** | в `agent/run.py` — на `WORKER_ELICITATION` event вызвать `engine.decide()` | 30 (правка) |

Итого: ~785 строк (4 новых модуля + 3 test файла + 1 правка).

---

## 4. Pydantic schemas

```python
class Rule(BaseModel):
    id: str
    match: RuleMatch                          # topics, file_patterns, keywords
    risk: Literal["low", "medium", "high"]
    action: Literal["auto_resolve", "escalate", "conditional"]
    default_answer: str | None = None         # для auto_resolve
    reason: str

class Defaults(BaseModel):
    unknown_topic_action: Literal["escalate", "judge"] = "judge"
    max_auto_resolve_per_story: int = 5
    hard_escalate_keywords: list[str] = [     # Tier 0 override
        "crypto", "encryption", "kdf", "pii",
        "personal data", "152-фз", "gdpr",
        "drop table", "delete from", "rm -rf",
    ]

class ElicitationPolicy(BaseModel):
    version: int
    defaults: Defaults
    rules: list[Rule]

class Decision(BaseModel):
    action: Literal["auto_resolve", "escalate"]
    answer: str | None
    reason: str
    tier: Literal[0, 1, 2]                    # которым ярусом принято
    risk: Literal["low", "medium", "high"]
    rule_id: str | None                       # если Tier 1
```

---

## 5. Success criteria (unit-tests pass)

| Тест | Критерий |
|---|---|
| `test_hard_override_security` | ANY question containing `crypto`/`pii`/`gdpr`/etc → action=escalate, tier=0 |
| `test_rule_match_topics` | Question с `topics=[crypto, kdf]` → matches `sec-crypto-choice` rule → escalate |
| `test_rule_match_default_answer` | Low-risk rule с `default_answer` → action=auto_resolve, answer передан |
| `test_unknown_topic_fallback_judge` | No rule match → LLM-judge called (mocked) → routed по judge output |
| `test_unknown_topic_fallback_escalate` | No rule match AND `unknown_topic_action=escalate` → no LLM call, force escalate |
| `test_window_cap_force_escalate` | После 5 auto_resolve на одну story → 6-й вопрос → force escalate (tier=window) |
| `test_judge_low_risk` | LLM mock returns `risk=low` → auto_resolve с judge-suggested answer |
| `test_judge_high_risk_override` | LLM mock returns `risk=high` → escalate даже если topic не в hard list |
| `test_yaml_load_example_policy` | `examples/elicitation-policy.example.yaml` парсится без ошибок |
| `test_engine_emits_correct_event` | engine.decide() возвращает `Decision` с правильным `tier` |
| **Coverage** | ≥85% по новому модулю (pytest-cov) |

---

## 6. Что **НЕ** делает (out of scope)

- ❌ Не зовёт настоящий Haiku API — `llm_judge.py` имеет интерфейс, но конкретный вызов через `anthropic.AsyncAnthropic` приходит в Phase 4 (тогда же когда Supervisor LLM-loop). Сейчас — заглушка с pluggable callback (мокируется в тестах)
- ❌ Не правит существующий `examples/elicitation-policy.example.yaml` — переиспользует as-is
- ❌ Не добавляет новые policy YAML — работает поверх existing
- ❌ Не меняет event schema — `WORKER_ELICITATION` payload остаётся прежним
- ❌ Не real-pilot — только unit-тесты (Step B отложен)

---

## 7. Phase gate (когда инициатива закрыта)

- ✅ `pytest tests/test_elicitation_*.py` — все зелёные
- ✅ `pytest` всего проекта — без регрессий (текущий baseline 1475 PASS → target 1475+12)
- ✅ `ruff check src/bmad_orchestrator/elicitation/` — clean
- ✅ `mypy src/bmad_orchestrator/elicitation/` — clean
- ✅ `methodology-virgil.md` обновлён: gap отмечен ✅, queue переупорядочена
- ✅ Один commit: `feat(elicitation): auto-elicitation policy engine (P2 Routing)`

---

## 8. Зависимости и связки

- Использует existing: `agent/skills/elicitation-router/SKILL.md` (skill metadata)
- Использует existing: `examples/elicitation-policy.example.yaml` (как default policy)
- Использует existing: `agent/tools/control.py::respond_to_elicitation` + `escalate_to_human`
- Разблокирует: **Supervisor LLM-loop + Terminal TUI** (Phase 4 #9) — supervisor нужен auto-elicitation готовый

---

## 9. Открытые вопросы (нужны от пользователя ДО старта)

1. **Tier 2 (LLM-judge) — оставить заглушку или сразу реальный Haiku?**
   - Заглушка → быстрее (1 сессия), real Haiku позже когда нужен
   - Real Haiku → +1 dependency (`anthropic` async client), +20 строк, +cost test infra
   - **Рекомендация:** заглушка с pluggable callback. Real Haiku включим в момент Supervisor.

2. **Default policy путь — где грузить policy YAML?**
   - Опция A: hardcoded `examples/elicitation-policy.example.yaml`
   - Опция B: configurable `--elicitation-policy PATH` через CLI флаг
   - Опция C: per-target-project в `<project-root>/.bmad/elicitation-policy.yaml`
   - **Рекомендация:** B (CLI флаг) с fallback на A (default из examples/). Project-agnostic, как договорились.

3. **Window cap на auto_resolve — где хранить counter?**
   - In-memory dict `{story_id: count}` — теряется при рестарте
   - SQLite `state.db` — persistent, добавляет таблицу
   - **Рекомендация:** in-memory (counter reset на рестарт = OK; единичный auto-resolve лишний — не критично).

---

## 10. References

- Universal methodology: `~/.claude/skills/888/REFERENCE.md`
- Project methodology: `spec/methodology-virgil.md` §4 Phase 3, §5 queue item «Auto-elicitation»
- Existing skill: `src/bmad_orchestrator/agent/skills/elicitation-router/SKILL.md`
- Example policy: `examples/elicitation-policy.example.yaml`
- Pattern decision tree: REFERENCE.md §5 → P2 Routing
- Template applied: `~/.claude/skills/888/templates/routing-decision-tree.md` §4B (Dynamic routing via LLM-judge)

---

**Last updated:** 2026-05-18
**Status:** Draft v1 — awaiting user approval before implementation
