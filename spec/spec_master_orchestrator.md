# Spec — Master Orchestrator (bmad-orchestrator)

**Версия:** 0.1.0 (draft)
**Дата:** 2026-05-15
**Автор:** AABIT
**Статус:** scaffold готов, реализация — после pilot на Odyssey Wave 1a

---

## 1. Executive Summary

`bmad-orchestrator` — отдельный Python-инструмент, который автоматизирует BMad **Phase 4 (Implementation)** для greenfield-проектов с `_bmad/` структурой. Он читает уже подготовленные эпики и stories, строит граф зависимостей, запускает параллельных Claude-worker'ов в изолированных `git worktree`-копиях, прогоняет каждую story через цикл `create-story → dev-story → code-review`, мержит готовое в integration-ветку через safety gates, и эскалирует пользователю только то, что не может решить policy engine.

**Целевой эффект:** для Odyssey Wave 1a (101 story по 6 эпикам) переход от sequential «один story за час» к parallel «3-5 stories одновременно» = ~3-5× ускорение реальной разработки. **При сохранении качества** за счёт mandatory code-review gate перед каждым merge.

---

## 2. Goals & Non-Goals

### Goals

1. **Автоматизировать BMad Phase 4** end-to-end: от чтения `epics.md` / `stories/*.md` до merged code в integration-branch.
2. **Параллелизация по DAG**: stories без общих shared-files и общих ancestor-зависимостей запускаются одновременно (limit = N, по умолчанию 3).
3. **Worktree-изоляция**: каждый параллельный worker работает в `git worktree`, никакого пересечения рабочих копий.
4. **Elicitation policy engine**: 80% неоднозначностей решаются автоматически по правилам, 20% эскалируются человеку через Telegram/email.
5. **Mandatory gates**: code-review (всегда) + security-review (для security-critical эпиков) перед merge.
6. **Human checkpoints**: каждые 10 stories или на Wave-границе — pause + notification.
7. **Cost budget hard-cap**: остановка при превышении дневного лимита токенов ($/день).
8. **Reusable**: работает с любым проектом, имеющим `_bmad/planning-artifacts/epics.md` + `stories/*.md` + `_bmad/_config/`.

### Non-Goals

- **НЕ автоматизирует Phase 1-3** (Analysis / Planning / Solutioning) — те остаются guided workflow с человеком.
- **НЕ заменяет** `bmad-auto-dev` skill — оркестратор **вызывает** его как имплементационную единицу, а сверху добавляет parallelism + DAG + gating.
- **НЕ деплоит** код в прод — останавливается на merge в `integration/<wave>` ветку. Финальный merge в `main` + deploy — отдельный шаг с human approval.
- **НЕ работает** с brownfield-проектами без `_bmad/` — нужна минимальная структура (см. §5).
- **НЕ делает** code-generation сам — это работа Claude Agent SDK worker'ов. Оркестратор — только control plane.

---

## 3. Build Plan (3 фазы)

Принцип: **не строить orchestrator вслепую**. Сначала собрать данные на pilot run, потом проектировать на их основе.

### Phase 1 — Pilot Run (без оркестратора)

**Цель:** baseline-данные о реальном поведении `bmad-auto-dev` на Odyssey.

- Target: Odyssey Epic 1 (Platform Shell) — ~15-20 stories
- Запуск: `/auto-loop-pipeline` со списком из `bmad-auto-dev`-вызовов на каждую story
- Собираемая телеметрия:
  - Время на story (median, p90, max)
  - Какие elicitation-вопросы возникали (полный лог)
  - Какие файлы трогает каждая story (для shared-files mutex анализа)
  - Где `bmad-code-review` отклонял и почему
  - Сколько токенов на story (Opus vs Sonnet split)

**Выход:** `phase-1-pilot-report.md` с реальными числами.

### Phase 2 — Policy Harvest

**Цель:** превратить pilot-логи в `elicitation-policy.yaml` для проекта.

- Pass 1: автоматический — собрать все elicitation events в категории, сгруппировать одинаковые
- Pass 2: ручной (with Claude) — для каждой категории решить: auto_resolve / conditional / escalate, прописать default
- Output: `_bmad/_config/orchestrator-policy.yaml` в target-проекте

### Phase 3 — MVP Orchestrator

**Минимальная версия** (без параллелизации):

- DAG planner — читает stories, строит граф, выдаёт топологический порядок
- Sequential worker — один worker, один story за раз, через Claude Agent SDK
- Merge gate — `bmad-code-review` через subprocess, halt-on-fail
- Cost budget — простой counter, halt при overrun
- CLI: `bmad-orchestrator run --project X --wave Y --sequential`

**Pilot:** Odyssey Wave 1a, Epic 2 (любой не Epic 1).
Если 8/10 stories пройдут без human intervention — MVP считается успешным.

### Phase 4 — Parallel Worker Pool

- N параллельных worker'ов в worktrees
- Mutex на shared files
- Async merge queue (sequential merge, parallel implementation)
- Watchdog с liveness-check (НЕ kill alive, см. §9)

**Pilot:** Wave 1b — production-grade.

### Phase 5 — Production hardening

- Telegram bot integration
- Web dashboard (FastAPI + htmx) для monitoring
- Multi-project queue (несколько target-проектов в один день)
- Retrospective integration (Phase 5 BMad — `/bmad-retrospective`)

---

## 4. Architecture

### 4.1 High-level component diagram

```
                                ┌─────────────────────────┐
                                │  bmad-orchestrator CLI  │
                                │  (typer + rich)         │
                                └────────────┬────────────┘
                                             │
                                             ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                         Master Orchestrator                              │
│                    (Anthropic SDK, Opus 4.7)                             │
│                                                                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐ │
│  │ DAG Planner  │→ │ Scheduler    │→ │ Worker Pool  │→ │ Merge Gate   │ │
│  │              │  │ (priority +  │  │ (N parallel) │  │ (review +    │ │
│  │ networkx     │  │  mutex)      │  │              │  │  security)   │ │
│  └──────────────┘  └──────────────┘  └──────────────┘  └──────────────┘ │
│         │                  │                 │                 │         │
│         └──────────────────┴─────────────────┴─────────────────┘         │
│                            │                                             │
│                            ▼                                             │
│                  ┌─────────────────────┐                                 │
│                  │ Elicitation Policy  │                                 │
│                  │ Engine              │                                 │
│                  │ (YAML rules + LLM)  │                                 │
│                  └─────────────────────┘                                 │
└──────────────────────────────────────────────────────────────────────────┘
                                             │
                                             ▼
                            ┌────────────────────────────────┐
                            │  Worker (per story)            │
                            │  Claude Agent SDK, Sonnet 4.6  │
                            │                                │
                            │  worktree: target/.worktrees/  │
                            │           story-{epic.story}/  │
                            │                                │
                            │  runs: /bmad-auto-dev story    │
                            └────────────────────────────────┘
                                             │
                                             ▼
                            ┌────────────────────────────────┐
                            │  Notification channels         │
                            │  - Telegram (escalation)       │
                            │  - Email (daily summary)       │
                            │  - structlog → file + stdout   │
                            └────────────────────────────────┘
```

### 4.2 Components

| Компонент | Технология | Назначение | Модель |
|---|---|---|---|
| **CLI** | typer + rich | Entry point, args parsing, progress display | — |
| **Master Orchestrator** | Anthropic SDK | Главный цикл, координация | Opus 4.7 |
| **DAG Planner** | networkx + Anthropic SDK | Строит граф зависимостей stories | Opus 4.7 (одноразовый вызов на старте) |
| **Scheduler** | Pure Python | Выбирает следующий батч stories (DAG roots + mutex check) | — |
| **Worker Pool** | asyncio + subprocess | Запускает N параллельных Claude Agent SDK worker'ов | — |
| **Worker** | Claude Agent SDK (Python) | Исполняет одну story через `bmad-auto-dev` | Sonnet 4.6 |
| **Merge Gate** | subprocess + Anthropic SDK | `bmad-code-review` + (опц.) `bmad-security-review` | Opus 4.7 для adversarial review |
| **Elicitation Policy Engine** | pydantic + YAML + Anthropic SDK | Решает auto/escalate | Haiku 4.5 для matching, Opus для тонких решений |
| **Notification** | python-telegram-bot + smtplib | Уведомления пользователю | — |
| **State Store** | SQLite (через aiosqlite) | Persistence: статусы stories, audit log, cost counter | — |

### 4.3 Data flow (per story)

```
1. Scheduler выбирает story X из DAG (mutex OK, deps satisfied)
   │
   ▼
2. Создание worktree:
   git -C <target> worktree add .worktrees/story-X.Y -b feat/story-X.Y integration/<wave>
   │
   ▼
3. Spawn worker (Claude Agent SDK):
   - System prompt: ссылка на story file + project CLAUDE.md + policy YAML
   - Tools: standard Claude Code harness (Read/Edit/Bash/Grep/Glob/Task)
   - Initial message: "/bmad-auto-dev story-X.Y"
   │
   ▼
4. Worker работает:
   - Если elicitation event → callback на orchestrator → policy engine → auto/escalate
   - Если auto_resolve → return decision, worker продолжает
   - Если escalate → worker pause, orchestrator → Telegram → wait → resume
   │
   ▼
5. Worker завершил (commit'нул в feat/story-X.Y)
   │
   ▼
6. Merge Gate:
   a) `bmad-code-review` (subprocess `claude -p`) на diff
   b) Если story в security-critical эпике → `bmad-security-review`
   c) Если оба PASS → merge feat/story-X.Y → integration/<wave>
   d) Если FAIL → worker resume с feedback, max 2 retries
   │
   ▼
7. Worktree cleanup:
   git worktree remove .worktrees/story-X.Y
   git branch -d feat/story-X.Y
   │
   ▼
8. State update: story X marked done, DAG.successors(X) checked for readiness
   │
   ▼
9. Если done % 10 == 0 или wave boundary → Human Checkpoint
```

---

## 5. Data Contracts

### 5.1 Story metadata (input — из target's `_bmad/planning-artifacts/stories/*.md`)

Story file должна иметь YAML frontmatter:

```yaml
---
id: "1.2"                     # epic.story
epic_id: "1"
title: "Tenant signup flow"
estimated_tokens: 50000       # для cost budget
estimated_minutes: 25
risk: medium                  # low | medium | high
depends_on: ["1.1"]           # other story IDs
touches_files:                # для shared-files mutex
  - "src/auth/signup.rs"
  - "migrations/0042_tenants.sql"
touches_shared:               # explicit shared-resource locks
  - "db_schema"               # серилизация на миграциях
security_critical: false
requires_human: false         # для Story 0.0 типа юр.консультаций
---

# Story 1.2 — Tenant signup flow

[story body — acceptance criteria, technical notes, ...]
```

### 5.2 Pydantic models (internal)

```python
# src/bmad_orchestrator/models.py

from pydantic import BaseModel, Field
from typing import Literal
from enum import Enum

class Risk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

class StoryStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"            # deps satisfied
    IN_PROGRESS = "in_progress"
    REVIEW = "review"          # merge gate
    BLOCKED = "blocked"        # waiting on escalation
    MERGED = "merged"
    FAILED = "failed"          # halt-on-fail triggered

class Story(BaseModel):
    id: str                    # "1.2"
    epic_id: str
    title: str
    file_path: str
    estimated_tokens: int = 50000
    estimated_minutes: int = 25
    risk: Risk = Risk.MEDIUM
    depends_on: list[str] = Field(default_factory=list)
    touches_files: list[str] = Field(default_factory=list)
    touches_shared: list[str] = Field(default_factory=list)
    security_critical: bool = False
    requires_human: bool = False
    status: StoryStatus = StoryStatus.PENDING

class ElicitationEvent(BaseModel):
    story_id: str
    worker_pid: int
    question: str
    context: str               # surrounding code / situation
    topics: list[str]          # for policy matching
    proposed_answer: str | None = None  # what worker would do without policy

class ElicitationResolution(BaseModel):
    action: Literal["auto_resolve", "escalate"]
    answer: str | None = None
    matched_rule_id: str | None = None
    reason: str

class MergeGateResult(BaseModel):
    story_id: str
    code_review_status: Literal["pass", "fail"]
    security_review_status: Literal["pass", "fail", "skipped"]
    findings: list[str] = Field(default_factory=list)
    can_merge: bool
```

---

## 6. Runtime Flow

### 6.1 State machine — per story

```
                  ┌──────────┐
                  │ PENDING  │
                  └────┬─────┘
                       │ deps satisfied
                       ▼
                  ┌──────────┐
                  │  READY   │
                  └────┬─────┘
                       │ scheduler picks + mutex OK
                       ▼
                  ┌──────────────┐
                  │ IN_PROGRESS  │◄────────┐
                  └────┬─────────┘         │
                       │                   │ resume from escalation
        ┌──────────────┴──────┐            │
        │                     │            │
        ▼                     ▼            │
  ┌──────────┐         ┌──────────┐        │
  │ REVIEW   │         │ BLOCKED  │────────┘
  └────┬─────┘         └──────────┘
       │ gate result
   ┌───┴────────┐
   ▼            ▼
┌────────┐  ┌────────┐
│ MERGED │  │ FAILED │
└────────┘  └────────┘
```

### 6.2 Master loop (pseudocode)

```python
async def master_loop(project: ProjectConfig):
    stories = load_stories(project)
    dag = DAGPlanner(opus_client).build(stories)
    state = StateStore(project.state_db_path)
    pool = WorkerPool(max_parallel=project.max_parallel)
    policy = PolicyEngine(project.policy_yaml_path, haiku_client, opus_client)
    budget = CostBudget(daily_limit_usd=project.daily_budget_usd)

    while not dag.all_terminal():
        # 1. Pick ready batch
        ready = dag.ready_stories(state, mutex_check=True)
        if not ready and pool.idle():
            await asyncio.sleep(5)  # idle — wait for in-progress to settle
            continue

        # 2. Schedule до max_parallel
        for story in ready[: pool.available_slots()]:
            if budget.exceeded():
                await escalate_user("Daily budget exceeded — halt")
                return
            await pool.spawn(story, policy, on_elicitation=policy.resolve)

        # 3. Reap completed
        for completed in pool.poll_done():
            gate_result = await merge_gate.review(completed)
            if gate_result.can_merge:
                await merge_gate.merge(completed)
                state.mark(completed.id, MERGED)
                dag.mark_done(completed.id)
            else:
                await pool.respawn_with_feedback(completed, gate_result.findings)

        # 4. Human checkpoint
        if state.merged_count() % 10 == 0 and state.merged_count() > 0:
            await checkpoint_user(state.summary())

        # 5. Halt-on-fail
        if state.failed_count() >= project.halt_threshold:
            await escalate_user(f"{state.failed_count()} failures — halt")
            return
```

---

## 7. SDK Choices

### 7.1 Anthropic SDK vs Claude Agent SDK

Используем **обе** — для разных задач:

| Компонент | SDK | Почему |
|---|---|---|
| Master Orchestrator | Anthropic SDK (raw) | Нужен полный контроль над сообщениями, нет нужды в tool harness |
| DAG Planner | Anthropic SDK (raw) | Одноразовый вызов, output = JSON, нет I/O |
| Elicitation Engine | Anthropic SDK (raw) | Single-shot decision, без tools |
| Merge Gate (LLM-side) | Anthropic SDK (raw) | Adversarial review через streaming |
| **Worker** | **Claude Agent SDK** | Нужен полный Claude Code harness (Read/Edit/Bash/Grep/Task), плюс skills support |

### 7.2 Prompt caching (обязательно)

Все Anthropic SDK вызовы — с **prompt caching enabled** (cache TTL 5 минут):

- **Cacheable prefix:** project context (CLAUDE.md target + epics.md + architecture.md). Размер ~20-50K токенов. Один раз загружается, потом cache_read на всех stories той же сессии.
- **Volatile suffix:** конкретная story file + diff + question.
- **Target cache hit rate:** ≥80%. Меньше = bug в шаблонизации.

### 7.3 Worker invocation

Worker не вызывается через Python API напрямую — слишком много overhead'а на запуск Claude Code в-процессе. Вместо этого **subprocess + headless `claude -p`**:

```python
proc = await asyncio.create_subprocess_exec(
    "claude", "-p", f"/bmad-auto-dev {story.id}",
    cwd=worktree_path,
    env={**os.environ, "CLAUDE_NO_INTERACTIVE": "1"},
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE,
)
```

Преимущество: каждый worker — fresh context, не отъедает RAM, легко kill при stuck.
Недостаток: cold start ~5 секунд. Acceptable — story всё равно идёт 15+ минут.

### 7.4 Communication: worker → orchestrator

Через **structured JSONL на stdout** worker'а. Worker emit'ит события вида:

```jsonl
{"event": "started", "story_id": "1.2", "timestamp": "..."}
{"event": "elicitation", "story_id": "1.2", "question": "...", "topics": ["error-handling"]}
{"event": "tool_use", "story_id": "1.2", "tool": "Edit", "file": "src/auth.rs"}
{"event": "completed", "story_id": "1.2", "commit": "abc123", "tokens_used": 47000}
```

Orchestrator парсит stdout line-by-line через `asyncio.StreamReader`, отвечает на elicitation через stdin (или через file-based IPC).

---

## 8. Worktree Management

### 8.1 Layout

```
<target_project>/
  .git/
  src/
  _bmad/
  .worktrees/                  # gitignored в target
    story-1.2/
      .git → ../../../.git/worktrees/story-1.2/
      src/
      ...
    story-1.3/
    story-2.1/
```

### 8.2 Lifecycle

```bash
# Create
git -C <target> worktree add .worktrees/story-1.2 -b feat/story-1.2 integration/wave-1a

# Worker работает там...

# After merge
git -C <target> checkout integration/wave-1a
git -C <target> merge --no-ff feat/story-1.2 -m "feat(epic-1): merge story 1.2"
git -C <target> worktree remove .worktrees/story-1.2
git -C <target> branch -d feat/story-1.2
```

### 8.3 Integration branch policy

- Каждая Wave имеет свою `integration/wave-1a` ветку
- Стартует от `main` в момент Wave kickoff
- Все stories мержатся в неё
- В конце Wave — `git-merge-integration` skill для final merge в `main` (это уже human-driven)

### 8.4 Shared-files mutex

DAG planner маркирует stories как `mutex_locked` если они трогают:

- Один и тот же файл (по `touches_files`)
- Один и тот же shared resource (по `touches_shared`)

Mutex implementation: **в orchestrator memory**, NOT файловые locks. Scheduler просто не запустит две конфликтующие stories параллельно. Когда первая завершит merge — mutex released, вторая становится ready.

---

## 9. Safety Gates

### 9.1 Cost budget

- Hard cap: `daily_budget_usd` (default $50)
- Soft cap: `daily_budget_usd * 0.8` → warning notification
- На превышении hard cap — `escalate_user`, no new spawns, текущие worker'ы дорабатывают
- Counter persisted в SQLite (`state.db`), reset в полночь UTC

### 9.2 Human checkpoints

Обязательные:

- Каждые 10 merged stories
- На Wave boundary (последний story → пауза до user resume)
- На FAILED status (halt + notification)
- На budget warning (80%)
- На security-critical story перед merge (даже если gate PASS)

Опциональные (через CLI flag):

- На каждый merge (`--checkpoint-every-merge`)
- На escalation (`--checkpoint-on-escalation`)

### 9.3 Kill-stuck protocol

Watchdog **НЕ kill alive процессы**:

```python
async def is_alive(pid: int) -> bool:
    """Check via 3 signals — CPU, file I/O, network."""
    proc = psutil.Process(pid)
    cpu_recent = proc.cpu_percent(interval=2.0) > 0.5
    io_recent = any(proc.io_counters() != snapshot.last_io for ...)
    net_recent = any(c.status == "ESTABLISHED" for c in proc.connections())
    return cpu_recent or io_recent or net_recent

async def watchdog(worker):
    while worker.running():
        await asyncio.sleep(60)
        if worker.no_stdout_for(seconds=300):
            if await is_alive(worker.pid):
                logger.info("Silent but alive — let it work", pid=worker.pid)
            else:
                logger.warning("Dead — killing", pid=worker.pid)
                worker.kill()
                state.mark(worker.story_id, FAILED)
```

(Зафиксировано в user memory `feedback_dont_kill_if_alive.md`.)

### 9.4 Three-layer safety (наследуем от `auto-loop-spec`)

1. **Prompt gates** — в system prompt worker'а явные deny: «никогда не push --force в main», «никогда `--no-verify`».
2. **Deny-list** — orchestrator scan'ит JSONL events, kill при detection forbidden tool calls.
3. **Branch isolation** — worker работает в worktree на feature-ветке, физически не может тронуть main.

---

## 10. Observability

### 10.1 Structured logging

`structlog` → JSONL → file + stdout.

```python
log.info("story_started", story_id="1.2", epic="1", worker_pid=42, parallel_count=3)
```

### 10.2 Metrics (collected, не отправляются никуда — пока)

- `stories_total`, `stories_merged`, `stories_failed`, `stories_blocked`
- `tokens_used_total` (Opus / Sonnet / Haiku split)
- `cost_usd_total`
- `merge_gate_pass_rate`
- `elicitation_auto_resolve_rate` (target: ≥80%)
- `worker_avg_minutes_per_story`
- `parallelism_actual` (avg concurrent workers)

### 10.3 Daily summary email

В конце дня — автоматический email с:

- Сколько stories merged / failed / blocked
- Стоимость $
- Top 3 elicitation topics (для policy refinement)
- Stories requiring human attention

---

## 11. Open Questions (с default resolutions)

| Вопрос | Default resolution | Rationale |
|---|---|---|
| Pricing model для cost budget — fixed $/day или token-based? | Token-based с конверсией по published rates | Точнее, проще аудитить |
| Что делать если worker зациклился на elicitation? | Hard cap 3 escalation per story, дальше FAILED | Иначе worker может вечно эскалировать |
| Concurrent code-review для security-critical — параллельно с dev или после? | После dev (sequential) | Security review должен видеть final code, не WIP |
| Где хранить policy YAML — в orchestrator repo или target? | В **target** (`_bmad/_config/orchestrator-policy.yaml`) | Policy специфична для проекта |
| Что если `bmad-auto-dev` skill не установлен в target? | CLI flag `--install-skills` копирует из `~/.claude/skills/` | Прозрачно, idempotent |
| Telegram bot vs email для escalation? | Telegram primary, email fallback | Telegram instant, email reliable |
| Что если pyproject deps конфликтуют у разных target проектов? | Worker запускается через `claude -p` headless, orchestrator deps изолированы в своём venv | No conflict — different processes |
| Multi-project queue — последовательно или параллельно? | Последовательно (один проект за раз) в MVP, параллельно в v2 | KISS |
| Roll-back при merge breakage? | `git revert <merge_commit>` + story marked FAILED + escalate | Reversible, audit-trail сохранён |
| Cost для DAG planner — Opus или Sonnet? | Opus (одноразовый вызов на старте, экономия не критична) | Лучшее качество планирования |

---

## 12. Phase 4 BMad коннект

Оркестратор покрывает BMad Phase 4 «Implementation» skills из catalog:

| BMad skill | Запускается оркестратором | Где |
|---|---|---|
| `bmad-create-story` (CS) | ✅ да | Worker внутри `bmad-auto-dev` |
| `bmad-create-story:validate` (VS) | ✅ да | Перед dev-story в `bmad-auto-dev` |
| `bmad-dev-story` (DS) | ✅ да | Основная работа worker'а |
| `bmad-code-review` (CR) | ✅ да | Merge Gate в orchestrator |
| `bmad-security-review` | ✅ условно | Merge Gate если story security-critical |
| `bmad-qa-generate-e2e-tests` (QA) | ⚠️ опционально (flag) | После merge, если включено |
| `bmad-retrospective` (ER) | ⚠️ опционально | На Wave boundary, human-triggered |
| `bmad-sprint-status` (SS) | ✅ да | Orchestrator CLI `status` command |
| `bmad-correct-course` (CC) | ❌ нет | Human-only |
| `bmad-checkpoint-preview` (CK) | ✅ да | Human checkpoint use case |

---

## 13. Risks & Mitigations

| Риск | Impact | Mitigation |
|---|---|---|
| Token cost runaway | High ($) | Cost budget hard-cap §9.1 |
| Merge conflicts на shared files | Medium | DAG mutex §8.4 |
| Bad code прокрадывается через gates | High (тех долг) | Mandatory `bmad-code-review`, security review для critical |
| Worker зависает | Medium | Watchdog с liveness §9.3 |
| Elicitation policy слишком permissive | High (wrong defaults shipped) | Policy review человеком после Phase 2 harvest |
| Orchestrator падает посреди batch | Medium | State в SQLite, resume на restart |
| Target проект не имеет нужной BMad структуры | Low | CLI validation на старте, fail fast |
| Race condition на git operations | Medium | All git ops через single git-coordinator actor (no concurrent push/merge) |
| Claude API rate limit | Medium | Exponential backoff в Anthropic SDK + spread spawns с delay |
| Story 0.0 (юр.консультация) попадает в pipeline | Medium | `requires_human: true` flag → scheduler skip |

---

## 14. References

- **Anthropic SDK:** https://github.com/anthropics/anthropic-sdk-python
- **Claude Agent SDK:** https://github.com/anthropics/claude-agent-sdk-python
- **Existing skills:**
  - `~/.claude/skills/bmad-auto-dev/SKILL.md` (или в `<project>/.claude/skills/`)
  - `~/.claude/skills/auto-loop-pipeline/SKILL.md`
  - `~/.claude/skills/auto-loop-spec/SKILL.md`
- **Target project artifacts:**
  - `/home/server/odyssey-ux/_bmad/planning-artifacts/epics.md` — 16 эпиков, 133 FR
  - `/home/server/odyssey-ux/_bmad/planning-artifacts/architecture-odyssey-foundation.md`
  - `/home/server/odyssey-ux/CLAUDE.md`
- **User memories:**
  - `feedback_dont_kill_if_alive.md` — liveness check protocol
  - `feedback_default_resolutions.md` — defaults > open Q&A
  - `feedback_audit_default.md` — audit-agent default for design artifacts
  - `project_odyssey_decisions.md` — 6 critical decisions register

---

## 15. Glossary

- **BMad** — Method-driven AI-Agent Development workflow (4 фазы: Analysis → Planning → Solutioning → Implementation).
- **DAG** — Directed Acyclic Graph; здесь — граф зависимостей stories, узел = story, ребро = «B depends on A».
- **Worktree** — `git worktree`-копия репо на той же `.git/`, позволяет работать в нескольких ветках параллельно.
- **Integration branch** — long-lived ветка типа `integration/wave-1a`, куда orchestrator мержит stories, потом единым PR в `main`.
- **Mutex** — explicit lock на shared resource (файл / DB schema / config) — stories с пересекающимися locks не идут параллельно.
- **Elicitation** — момент когда worker должен задать уточняющий вопрос. Policy engine решает: auto / escalate.
- **Merge Gate** — `bmad-code-review` (+ опц. `bmad-security-review`) обязательный перед merge story → integration.
- **Human Checkpoint** — pause + notification пользователю каждые N stories или на boundary.
- **Cost budget** — token-based daily limit с hard cap и soft cap.
- **Anthropic SDK** — официальная Python библиотека для прямых вызовов Claude API.
- **Claude Agent SDK** — обёртка дающая полный Claude Code tool harness (Read/Edit/Bash/...) embedded в Python приложении.
- **Headless `claude -p`** — Claude Code как одноразовый subprocess без UI, output в stdout/JSONL.

---

## 16. Next Actions (для следующей сессии в этом проекте)

1. **Прочитать `.claude/memory/activeContext.md`** (создастся hook'ом при первом старте).
2. **Прочитать этот spec целиком**.
3. **Проверить:** `bmad-auto-dev` skill доступен глобально? Если нет — установить из `/home/server/odyssey-ux/.claude/skills/bmad-auto-dev/`.
4. **Phase 1 (Pilot):** не строить orchestrator пока. Сначала договориться с пользователем когда запускать pilot на Odyssey Epic 1 (требует завершённый `/bmad-create-epics-and-stories` + `/bmad-sprint-planning` в Odyssey).
5. **Если pilot готов:** написать скрипт `scripts/run_pilot.sh` — обёртка над `/auto-loop-pipeline` с инструментированным логированием для harvest.
6. **Создать `src/bmad_orchestrator/models.py`** с Pydantic моделями из §5.
7. **Создать `src/bmad_orchestrator/cli.py`** — typer entry point с командами `status`, `plan`, `run`, `validate-policy`.

---

**End of spec.**
