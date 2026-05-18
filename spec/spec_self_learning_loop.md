# Spec — Self-learning consolidation loop

> **Фаза ADLC:** Phase 5 (Monitor & Improve) — item #11 → reframed («memory wiring» закрыто retroactively; новый scope = consolidation loop поверх готовой памяти)
> **Patterns used:** P5 Evaluator-Optimizer (главный — оценить retrospectives, улучшить policies итеративно) + P1 Chaining (extract → propose → gate → apply)
> **Объём:** 2-3 сессии (M-scale initiative)
> **Status:** Draft v1 — awaiting user approval

---

## 1. Зачем это

Virgil **накапливает уроки** (`agent/memory/levels.py` — tactical/strategic/architectural), но не использует их автономно. Сейчас:

| Что есть | Что отсутствует |
|---|---|
| ✅ Memory infrastructure (vendor-agnostic) | ❌ Автономный pattern extractor (LLM читает lessons → выделяет правила) |
| ✅ `Proposal` data class + `build_proposals(retro_id)` | ❌ Auto-apply path для low-risk (сейчас всё через Telegram кнопки) |
| ✅ `proactive-improver` skill (3 triggers wired) | ❌ Periodic consolidation cron (MONTHLY_REVIEW_SCHEDULED — event есть, emitter отсутствует) |
| ✅ `apply_proposals_batch` + `rollback_policy` | ❌ Orchestration: extract → gate → apply → measure → record |
| ✅ Vendor-agnostic file storage | ❌ Vendor-agnostic LLM extraction layer (pluggable ExtractorProtocol) |

**Self-learning loop** = автономный цикл:

```
Накопились lessons (per-story, per-wave)
    ↓
[Trigger] wave_boundary | monthly cron | manual CLI
    ↓
[Extract] LLM читает raw lessons → выделяет patterns (≥3 повторений → правило)
    ↓
[Propose] pattern → Proposal (использует existing build_proposals)
    ↓
[Gate] Risk classification: low → auto-apply, medium → escalate, high → PR/manual
    ↓
[Apply] apply_proposals_batch с rollback на регрессии
    ↓
[Measure] следующий wave — сравниваем metrics до/после → если regression > threshold → rollback
    ↓
[Record] applied/rejected → semantic memory (cross-wave-learnings.md)
```

**Цель:** Virgil **тише и лучше** от wave к wave без вмешательства человека для low-risk правок.

**Аналогия:** доктор-практик. Каждый день видит пациентов (stories), вечером записывает заметки (lessons), раз в месяц перечитывает все заметки и обновляет свой «протокол лечения» (policy YAML). Если новый протокол даёт хуже результат — возвращается к старому (rollback).

---

## 2. Что уже есть (re-use)

| Компонент | Где | Статус |
|---|---|---|
| `agent/memory/proposals.py::Proposal` + `build_proposals(retro_id)` | существует | ✅ |
| `agent/memory/levels.py::TacticalLesson / StrategicLesson / ArchitecturalLesson` | существует | ✅ |
| `agent/memory/gates.py::can_promote_wave / is_retro_done` | существует | ✅ |
| `runtime/lesson_parser.py::parse_lessons_dir / apply_proposals_batch / rollback_policy / _atomic_yaml_write` | существует | ✅ |
| `proactive-improver` skill — wired на `WAVE_BOUNDARY_REACHED` / `EPIC_BOUNDARY_REACHED` / `PHASE4_COMPLETE` / `MONTHLY_REVIEW_SCHEDULED` | существует | ✅ |
| `EventType.MONTHLY_REVIEW_SCHEDULED` event | существует | ✅ |
| `LLMJudgeProtocol` (auto-elicitation/supervisor pattern) | существует | ✅ (re-use паттерна) |

**Не строим заново:** `Proposal` schema, lesson parsing, policy apply mechanics, rollback, skill triggers.

**Расширяем:** `build_proposals()` сейчас читает **structured `## Policy proposal` markdown blocks** из lessons. Self-learning добавляет **LLM-extraction path** для случаев когда lesson — это просто свободный текст без structured blocks.

**Новое:**
- `self_learning/` модуль (consolidator + extractor + apply + loop + metrics)
- Periodic emitter для `MONTHLY_REVIEW_SCHEDULED` (cron-style scheduler)
- CLI `bmad-orchestrator self-learning {run, status, rollback}` команды
- Policy auto-tune thresholds (separate YAML — `config/self-learning.yaml`)

---

## 3. Архитектура

### 3.1 Decision pipeline (P5 Evaluator-Optimizer)

```
[Trigger] WAVE_BOUNDARY_REACHED | MONTHLY_REVIEW_SCHEDULED | manual CLI
    ↓
[Step 1] Collect raw lessons
    LessonParser → list[Lesson]  (existing parse_lessons_dir + raw markdown reader)
    ↓
[Step 2] Structured parse first
    parse_lessons_dir() → list[LessonProposal]  (existing — finds ## Policy proposal blocks)
    ↓
[Step 3] LLM-extraction fallback
    For lessons WITHOUT structured proposals → ExtractorProtocol.extract(lesson) → list[Pattern]
    Pattern requires: ≥ min_pattern_occurrences (default 3) repetitions across history
    ↓
[Step 4] Convert patterns → Proposals
    Pattern → Proposal via existing build_proposals contract (extend if needed)
    ↓
[Step 5] Risk gate (auto-classification)
    risk=low (policy delta < N lines, no compliance tag) → auto-apply queue
    risk=medium → escalate via HUMAN_QUERY с suggested change
    risk=high (code change, security policy, compliance) → manual review only
    ↓
[Step 6] Apply low-risk in batch
    apply_proposals_batch(low_risk_proposals)  — existing
    ↓
[Step 7] Measure (next wave)
    Compare metrics: escalation_rate, pass_rate, cost_per_story
    Regression > threshold → auto-rollback last batch
    ↓
[Step 8] Record outcome
    Append to <orchestrator_home>/.claude/memory/cross-wave-learnings.md
    + audit row в control.events.jsonl (event_type=self_learning_decision)
```

### 3.2 Структура модулей

```
src/bmad_orchestrator/self_learning/
├── __init__.py                # public API
├── config.py                  # SelfLearningConfig (pydantic) + YAML loader
├── extractor.py               # ExtractorProtocol + StubExtractor (vendor-agnostic)
├── consolidator.py            # Main orchestration: collect → extract → propose
├── risk_classifier.py         # Risk gate (low/medium/high)
├── apply.py                   # Auto-apply + regression guard + rollback
├── metrics.py                 # Track applied proposals + their effect
└── audit.py                   # JSONL audit log writer

src/bmad_orchestrator/runtime/
├── self_learning_subscriber.py  # bus glue (subscribes to 4 trigger events)
└── monthly_scheduler.py         # cron-emit MONTHLY_REVIEW_SCHEDULED раз в месяц

config/self-learning.yaml      # default policy

src/bmad_orchestrator/cli/main.py
└── new `self-learning` typer subcommand group (run / status / rollback)

tests/
├── test_self_learning_config.py
├── test_self_learning_extractor.py
├── test_self_learning_consolidator.py
├── test_self_learning_risk_classifier.py
├── test_self_learning_apply.py
├── test_self_learning_metrics.py
└── test_self_learning_subscriber.py
```

Итого: **8 новых модулей в `self_learning/`** + 2 runtime + 1 config YAML + CLI subcommands + 7 test файлов. Оценка: **~1500 строк** (включая тесты).

### 3.3 Pydantic schemas

```python
class SelfLearningDefaults(BaseModel):
    min_pattern_occurrences: int = 3
    auto_apply_max_risk: Literal["low"] = "low"
    measure_window_waves: int = 2
    regression_threshold_pct: float = 5.0    # >5% degradation → rollback
    extractor_model: str = "claude-sonnet-4-6"

class SelfLearningConfig(BaseModel):
    version: int = Field(ge=1)
    enabled: bool = True
    defaults: SelfLearningDefaults
    excluded_policy_files: list[str] = Field(default_factory=list)  # e.g. security-review.yaml
    excluded_compliance_tags: list[str] = ["152-ФЗ", "187-ФЗ"]  # never auto-touch
```

### 3.4 ExtractorProtocol (vendor-agnostic, pluggable)

```python
class ExtractorProtocol(Protocol):
    """LLM-based pattern extractor — reads raw lesson markdown, returns proposals."""
    async def extract(self, lessons: list[Lesson]) -> list[ProposedPattern]: ...

class StubExtractor:
    """Default — returns empty list. Real Sonnet implementation behind future flag."""
    async def extract(self, lessons): return []
```

**Vendor-agnostic note:** Тот же шаблон что в `auto-elicitation/llm_judge.py` и `supervisor/llm_judge.py`. При миграции на OpenAI/Gemini — replace `StubExtractor` implementation, всё остальное (storage, apply, rollback) работает as-is.

---

## 4. Что Self-learning loop НЕ делает (out of scope)

| Не делает | Почему |
|---|---|
| Не правит код агента | Code-level changes = manual PR (high risk) |
| Не трогает security-review.yaml | Compliance/security policy = manual review only |
| Не делает auto-apply для medium/high risk | Escalation needed; этот gate **не configurable** |
| Не работает без `proactive-improver` skill | Skill — source of truth для что proposable |
| Не заменяет manual `bmad-orchestrator policy-apply` CLI | Manual path остаётся для review-required cases |
| Не пишет в БД (только JSONL + YAML) | DB — отдельная инициатива |
| Не делает RL training / fine-tuning | Это «обучение правилам», не «обучение модели» |
| Не работает на не-Anthropic моделях | Storage/logic — vendor-agnostic; LLM extraction — за pluggable интерфейсом, swap ~50 строк |

---

## 5. Success criteria (Phase 5 gate)

| Criterion | Threshold |
|---|---|
| Trigger emit'ит на каждом из 4 event'ов (wave / epic / phase4 / monthly) | ✅ integration test |
| Extraction latency (Tier 1 LLM call) p95 | ≤ 30 сек на 50 lessons |
| Auto-apply only low-risk | 100% (hard gate, не configurable) |
| Regression rollback на degradation > 5% | ✅ test с synthetic regression |
| Excluded policy files никогда не трогаются | 100% (hard gate) |
| Audit log записывает 100% decisions | 100% |
| Unit tests | ≥ 30, coverage ≥ 85% по `self_learning/` |
| Full pytest suite без регрессий | 1588+ PASS |
| Mypy + ruff | clean |
| End-to-end mock test: 5 lessons → 1 proposal → auto-apply → rollback symulation | ✅ |

---

## 6. План реализации (2-3 сессии)

### Session 1 — Foundation + Extractor (M1)
- `self_learning/config.py` — pydantic schemas + YAML loader
- `self_learning/extractor.py` — Protocol + StubExtractor
- `self_learning/audit.py` — JSONL writer
- `config/self-learning.yaml` — default config с conservative thresholds
- Tests: `test_config.py` + `test_extractor.py` + `test_audit.py` (~15 tests)

**Phase gate session 1:** config loads, stub extractor returns empty list, audit writes. No orchestration yet.

### Session 2 — Consolidator + Risk gate + Apply (M2)
- `self_learning/consolidator.py` — main orchestration (collect → extract → propose)
- `self_learning/risk_classifier.py` — pattern → risk via simple rules + LLM-judge fallback
- `self_learning/apply.py` — auto-apply with regression guard + rollback (wraps existing `apply_proposals_batch` + `rollback_policy`)
- `self_learning/metrics.py` — wave-over-wave metric comparison
- Tests: `test_consolidator.py` + `test_risk_classifier.py` + `test_apply.py` + `test_metrics.py` (~20 tests)

**Phase gate session 2:** end-to-end mock — 5 synthetic lessons → 1 proposal → auto-apply → simulated regression → rollback. All in-memory.

### Session 3 — Bus integration + CLI + Methodology (M3)
- `runtime/self_learning_subscriber.py` — bus subscriber for 4 trigger events
- `runtime/monthly_scheduler.py` — cron-style emit для MONTHLY_REVIEW_SCHEDULED (использует asyncio scheduler — каждый 1-го числа в 10:00)
- `cli/main.py` — `self-learning {run, status, rollback}` subcommands
- Wiring в `agent/run.py`
- Tests: `test_subscriber.py` + `test_monthly_scheduler.py` (~10 tests)
- Methodology update + commit + retrospective memory

**Phase gate session 3:** end-to-end через bus — synthetic WAVE_BOUNDARY → consolidator runs → policy YAML mutates → audit row written. Methodology v12.

---

## 7. Открытые вопросы (нужны ответы ПЕРЕД стартом)

### Q1. Auto-apply scope: только `policy` proposals или включить `config`?
- (a) **Только policy** — `skills/policy/*.yaml` (наш узкий контракт)
- (b) Policy + config — расширяет на `<orchestrator>/_config/*.yaml`
- **Рекомендация:** (a) **Только policy.** Config-level changes требуют ребута оркестратора; auto-apply небезопасно. После 3+ месяцев успеха можно расширить.

### Q2. Regression measurement: 1 wave или N wave window?
- (a) **1 wave** — quick rollback, но noisy (1 wave может быть случайно плохой)
- (b) **N=2 wave window** — стабильнее, но медленнее реагирует
- **Рекомендация:** (b) **N=2 waves.** Менее false-positive rollbacks. Configurable через `measure_window_waves`.

### Q3. Excluded policies — какие НЕ трогать?
- (a) Только `security-review.yaml` (минимум)
- (b) `security-review.yaml` + `code-review-gates.compliance_tags` + `deletion-safety.yaml`
- (c) Всё что содержит `compliance_tags` поле
- **Рекомендация:** (b) **Конкретный allowlist.** Хардкодим в default config; пользователь может расширить через YAML.

### Q4. StubExtractor сейчас или сразу LLM call?
- (a) **Stub** (как делали для elicitation/supervisor) — extract() returns [], всю механику тестируем без токенов
- (b) Real Sonnet call за флагом
- **Рекомендация:** (a) **Stub сначала.** Real Sonnet включим вместе с supervisor real-mode в одну сессию.

### Q5. Monthly scheduler — где живёт?
- (a) Внутри основного оркестратора (asyncio Task с time.sleep'ом до 1 числа)
- (b) Отдельный CLI команда `bmad-orchestrator self-learning cron-emit` — вызывается из systemd timer / crontab
- (c) Опционально (a) для dev, (b) для prod
- **Рекомендация:** (c) **Both paths**. По умолчанию embedded scheduler (просто работает). Production может переехать на systemd timer если нужна изоляция.

### Q6. Rollback strategy на regression — auto или escalate?
- (a) **Auto-rollback** при regression > threshold (без вопросов)
- (b) **Escalate** — нотифицирует, ждёт подтверждения
- **Рекомендация:** (a) **Auto-rollback.** Self-learning loop должен быть «safe-revert by default»; если человек хочет manual confirm — отдельный mode. Rollback дешевле чем regression в проде.

### Q7. Audit log — отдельный файл или inline в control.events.jsonl?
- (a) `<orchestrator_home>/.claude/memory/self-learning.jsonl` (отдельный)
- (b) Inline в `control.events.jsonl` с `event_type=self_learning_decision` (как supervisor)
- **Рекомендация:** (b) **Inline.** Один audit pipeline, supervisor уже сделал прецедент.

---

## 8. Зависимости

**Pre-requisites (всё done):**
- ✅ Memory infrastructure (Phase 2 retroactive close)
- ✅ Proposal + build_proposals (`agent/memory/proposals.py`)
- ✅ lesson_parser (apply_proposals_batch, rollback_policy)
- ✅ `proactive-improver` skill wired
- ✅ Auto-elicitation engine (pattern for pluggable LLM)
- ✅ Supervisor LLM-loop (pattern for autonomous bus subscribers)

**Unlocks:**
- Phase 5 fully active (`MONTHLY_REVIEW_SCHEDULED` cron starts working)
- Vision step 6 fully active (self-learning loop)
- Production pilot (Phase 4 #10) — может запускаться knowing system auto-improves

---

## 9. Риски + mitigation

| Риск | Likelihood | Mitigation |
|---|---|---|
| Auto-apply ломает работающую policy → регрессии | Medium | Regression guard + auto-rollback + N-wave window + excluded files allowlist |
| Extractor галлюцинирует patterns где их нет | Medium | min_pattern_occurrences ≥ 3 + StubExtractor по умолчанию пока не доверяем LLM |
| Cascade: rollback вызывает другой rollback | Low | Rollback одного wave не triggers self-learning (skip event если source=self_learning) |
| Compliance tags случайно auto-applied | Low | Hard exclusion list — non-overrideable в default config |
| Monthly scheduler пропускает emit на restart'ах | Medium | Persist last_emit timestamp в state.db (или sqlite-WAL); check на startup |
| Toonkens cost от Extractor LLM call раз в месяц | Low | Cap N=50 lessons per extract; budget guard следит |

---

## 10. References

- Universal methodology: `~/.claude/skills/build-agent/REFERENCE.md`
- Patterns используемые: P5 Evaluator-Optimizer (main) + P1 Chaining (extract→propose→gate→apply)
- Template applied: `templates/memory-engineering.md` §4 (consolidation: episodic → semantic)
- Project methodology: `spec/methodology-virgil.md` Phase 5 + vision step 6
- Existing module: `agent/memory/proposals.py` (Proposal + build_proposals)
- Existing module: `runtime/lesson_parser.py` (parse_lessons_dir + apply + rollback)
- Existing skill: `agent/skills/proactive-improver/SKILL.md` (3 triggers wired)
- Sister specs: `spec/spec_auto_elicitation_engine.md` (pluggable LLM pattern) + `spec/spec_supervisor_llm_loop.md` (autonomous bus loop pattern)

---

## 11. Что нужно от user'а ПЕРЕД стартом Session 1

7 решений:
1. ✅/❌ — Q1: только policy proposals (не config) для MVP
2. ✅/❌ — Q2: N=2 waves regression window
3. ✅/❌ — Q3: allowlist excluded (security-review + compliance tags + deletion-safety)
4. ✅/❌ — Q4: StubExtractor сейчас, real Sonnet вместе с supervisor real-mode позже
5. ✅/❌ — Q5: both paths (embedded scheduler + CLI команда)
6. ✅/❌ — Q6: auto-rollback на regression > 5% (без подтверждения)
7. ✅/❌ — Q7: audit inline в control.events.jsonl

Plus high-level: **делаем subagent'ом или сам в этой сессии?**
- (a) Subagent (изолированно, я мерджу обратно) — параллельный путь, требует verify
- (b) Сам в этой сессии — последовательно, дольше но проще координировать
- **Рекомендация:** (a) **Subagent с briefing'ом из этого spec'а** — мы уже обсудили tradeoffs. Я финально верифицирую и мержу.

---

**Last updated:** 2026-05-18
**Status:** Draft v1 — awaiting user review
