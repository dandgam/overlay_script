# spec_pilot_findings_closure — Закрытие pilot findings + Phase 3 gate

> **Owner:** user + Virgil orchestrator
> **Created:** 2026-05-19
> **Goal:** Закрыть все P1/P2/P3 находки из Antares smoke runs 1-7 + R1/R2 research findings + Step B real eval cases. После закрытия Phase 3 gate готов, можно запускать #10 production pilot.
> **Source:** `spec/methodology-virgil.md` §5 priority queue — секции «Backlog — pilot findings» (8 items) + «Backlog — research findings» (R1/R2) + Phase 3 IN PROGRESS (Step B).
> **Scope decision:** B (P1+P2+P3 + Step B). См. диалог 2026-05-19 в `/888`.

---

## 0. Context

### Inventory at start

- Branch: `main` @ `a6f96b2`
- Tests: **1860 PASS**, mypy/ruff clean
- Event types: 29
- Subscribers: 12+
- Embedded skills: 16+
- Phase 3 status: 🟡 IN PROGRESS (Step A done, Step B остался)
- Phase 4 hardening: ✅ 7/7 closed (commits `85ee1ae` → `b7649fc`)
- Phase 4 #10 production pilot: ⬜ blocked by pilot findings

### Что вошло в epic

10 items, грубо ранжированы по критическому пути для разблокировки prod pilot:

| # | Tier | Item | Где зафиксировано |
|---|---|---|---|
| 1 | P1 | mark-done ID-format mismatch (dotted vs kebab) | pilot findings |
| 2 | P1 | bmad-auto-dev → orchestrator verdict event disconnect | pilot findings |
| 3 | P1 | Sonnet autofix LOC-300 cap → auto-escalate на Opus + auto-split trigger | pilot findings |
| 4 | P1 | R1 — AbortController per worker | research findings |
| 5 | P1 | R2 — MCP server readiness polling | research findings |
| 6 | P2 | subscription-mode budget auto-detect | pilot findings |
| 7 | P2 | pre-flight halt-state check до спавна | pilot findings |
| 8 | P2 | subprocess timeout adaptive / configurable per story | pilot findings |
| 9 | P3 | `real_pilot_done stories=N` misleading counter | pilot findings |
| 10 | Phase 3 | Step B — 10+ real BMad stories в eval suite (`evals/cases/`) | Phase 3 §B |

**Hard rules.**
- Branch isolation: вся работа в `integration/pilot-findings-closure`. **Никогда** напрямую в `main`.
- Auto-merge **disabled** (`Auto merge: false` в tracker) — user merge'ит финал руками.
- Tests delta caps: каждый item даёт +N tests (см. ниже). Финальный target: ≥1950 PASS.
- `--no-verify`, `--amend`, `--force` — запрещены.

---

## 1. P1 critical — критический путь к prod pilot

### #1 mark-done ID-format mismatch

**Зачем.** `_run_real_pilot_body` помечает истории `done` циклом `for sid in spawned: epic_block["stories"][sid] = "done"`. `spawned` содержит ID `1.3` (dotted). Ключи sprint-status — кебаб `1-3-fastapi-app-lifespan-health`. Match не срабатывает → sprint-status НЕ обновляется → resume повторно прогонит уже сделанные истории.

**Что добавляем.**
- `runtime/pilot.py` (или где живёт `_run_real_pilot_body`) — импортировать `normalize_story_id` из существующего id-utils модуля. Если такой утилиты нет — создать `agent/story_id.py` с `normalize_story_id(raw: str, sprint_keys: list[str]) -> str` (lookup точное совпадение → kebab variant → fallback raw).
- Замена логики mark-done: для каждого `sid` в `spawned` найти соответствующий ключ через `normalize_story_id`, обновить `epic_block["stories"][resolved_key] = "done"`.
- Дополнительно: `mark_done_log` в JSONL с парой `{spawned_id, resolved_key}` для debug.

**Tests.** `tests/test_pilot_mark_done_normalization.py`:
- 4 unit: dotted→kebab, kebab→kebab, missing→fallback raw + warning, case-insensitive
- 2 integration: full `_run_real_pilot_body` mock с обоими форматами spawned ids
- 1 regression: resume после прерванного pilot НЕ повторяет done-stories
- **Target:** +7 tests

**Acceptance.**
- Pilot с `spawned=["1.3"]` и sprint-status keys `["1-3-fastapi-app-lifespan-health"]` после finalize: sprint-status[`1-3-fastapi-app-lifespan-health`] == `"done"`
- Resume сразу после первого pilot run: ready_next list не содержит уже done-stories

---

### #2 bmad-auto-dev → orchestrator verdict event disconnect

**Зачем.** Воркеры `claude -p /bmad-auto-dev` делают code-review/autofix внутри runner'а (Stage 6), но **НЕ эмитят** `claude_event verdict=approve/reject` в worker JSONL. Orch-level subscribers (`code_review_subscriber` → `security_review_subscriber` → `merge_to_integration_subscriber`) реагируют именно на этот event. Без него **ни одна история не мержится в `integration`**. Без этого все 5 hardening-gates (Patch S/N/C/E5/W4/Q/X) — мёртвый код.

**Что добавляем.** Два варианта, выбрать в session start через research:

**Вариант A — runner emits event (preferred):**
- `agent/skills/bmad-auto-dev/scripts/bmad-auto-dev-runner.sh` Stage 6 (post-review)→ после code-review/autofix цикла append в worker JSONL:
  ```json
  {"event":"claude_event","payload":{"event":"verdict","verdict":"approve|reject","reason":"...","review_iteration":N,"findings_count":M}}
  ```
- Если runner — Python (а не bash), то `agent/skills/bmad-auto-dev/runner.py` `_finalize_stage6` пишет тот же event.

**Вариант B — orchestrator reads runner state (fallback):**
- `runtime/code_review_subscriber.py` — если за 60s после `WORKER_FINISHED` event'а `verdict` не пришёл, фоллбек на чтение `<worktree>/_bmad/auto-dev-state/reviews/<story>-stage6-*.log` (grep `Verdict: approve|reject`).
- Эмитит синтетический `verdict` event с источником `payload.source=runner_log_fallback`.

**Tests.** `tests/test_verdict_event_emission.py`:
- 3 unit (Variant A): runner emit verdict event with metadata fields
- 3 unit (Variant B): fallback reader parses stage6 log, emits synthetic event
- 2 integration: end-to-end `WORKER_FINISHED` → `verdict` → `merge_to_integration` triggered
- 1 regression: на Antares 1.4 fixture (saved JSONL) → merge gate тратит верный verdict
- **Target:** +9 tests

**Acceptance.**
- Mock pilot run на 1 story → `events.jsonl` содержит `verdict=approve` event ПОСЛЕ `WORKER_FINISHED`
- `integration/<wave>` ветка создаётся (commit count ≥1) после approve
- На reject → `request_changes` event с правильной причиной

---

### #3 Sonnet autofix LOC-300 cap → auto-escalate + auto-split trigger

**Зачем.** bmad-auto-dev runner откатит autofix Sonnet'а если diff > 300 LOC → halt → требует ручной override. На security-критичных эпиках бьёт постоянно (7-15 findings × 50-150 LOC fix = легко >300). 2 рычага:

**Что добавляем.**
- **Рычаг 1: Routing policy.** `skills/policy/autofix-routing.yaml`:
  ```yaml
  autofix_model_rules:
    - if: story.tags contains "security-critical"
      then: opus
    - if: review_iteration >= 2
      then: opus
    - default: sonnet
  ```
- `runtime/autofix_routing.py` — `pick_autofix_model(story, review_iteration) -> Literal["sonnet","opus"]`. Wire в `agent/skills/bmad-auto-dev/runner` перед Stage 6 autofix invocation.
- **Рычаг 2: Auto-split trigger.** В `runtime/decomposer_subscriber.py` (или новый, если ещё нет hook'а на `WORKER_HALT_FILE` с reason `loc_cap_exceeded`):
  - Subscribe на `WORKER_HALT_FILE` event с payload `halt_reason="loc_cap_exceeded"`.
  - Триггерит `bmad-orchestrator auto-split --story <id>` через existing decomposer (commit `87a172c`).
  - Эмитит `STORY_AUTO_SPLIT` event (новый, +1 → 30 total event types).
- Connection к **#2**: routing rule использует `verdict` event для определения `review_iteration` — без #2 не работает (depends on #2).

**Tests.** `tests/test_autofix_routing.py`:
- 4 unit: routing rules (security-tag → opus, iter≥2 → opus, default → sonnet, override через ENV)
- 3 unit: auto-split trigger на halt event
- 2 integration: full loop (sonnet attempt → halt LOC cap → split → 2 sub-stories created)
- **Target:** +9 tests
- **Depends on:** #2 (verdict event)

**Acceptance.**
- Story с `tags=[security-critical]` → autofix вызывается с `--model claude-opus-4-7`
- Halt event с `loc_cap_exceeded` → новый STORY_AUTO_SPLIT event + 2 child stories в sprint-status
- Sonnet остаётся default'ом для regular stories

---

### #4 R1 — AbortController per worker

**Зачем.** Coordinator может kill stuck worker мгновенно вместо опоры на SIGTERM + 1800s timeout. Ref: leaked claude-code `tools/AgentTool/runAgent.ts:520`. Идея — паттерн, не код.

**Что добавляем.**
- `runtime/worker_spawn.py` — каждому worker присвоить `worker_id → CancellationToken` (asyncio.Event или threading.Event, в зависимости от текущей модели).
- `runtime/worker_supervisor.py` (новый или расширение `supervisor/`) — метод `cancel_worker(worker_id, reason)` ставит token + SIGTERM как fallback.
- Wire в `supervisor` policy: новое action `cancel_worker` (рядом с `pause` / `abort_wave` / `escalate`).
- Bus event `WORKER_CANCELLED` (+1 → 30 или 31 в зависимости от порядка merge с #3).

**Tests.** `tests/test_worker_cancellation.py`:
- 3 unit: token mechanics (set/check/timeout)
- 2 unit: cancel_worker idempotent + handles already-dead worker
- 2 integration: supervisor decision → cancel_worker → WORKER_CANCELLED event
- 1 regression: stuck worker (sleep 9999) killed в <5s
- **Target:** +8 tests

**Acceptance.**
- Worker stuck in `time.sleep(9999)` killed mock-supervisor решением в <5s wall clock
- `WORKER_CANCELLED` event содержит `reason` и `cancelled_by` (supervisor / user / timeout)

---

### #5 R2 — MCP server readiness polling

**Зачем.** Перед spawn worker'а проверять что required MCP tools не просто connected, но **authenticated** (30s max, 500ms interval). Защита от падения «tool not found» через 5 мин. Ref: leaked `tools/AgentTool/AgentTool.tsx:371`.

**Что добавляем.**
- `runtime/mcp_readiness.py` (новый): `poll_mcp_ready(required_tools: list[str], timeout_s=30, interval_ms=500) -> ReadinessResult`.
  - Опрос: `claude mcp list --json` (или эквивалент CLI) каждый 500ms.
  - Проверка `authenticated: true` для каждого required tool.
  - Возвращает `(ok: bool, missing: list[str], elapsed_ms: int)`.
- `runtime/worker_spawn.py:_build_worker_env` (или `_spawn_worker`) — перед Popen вызывает `poll_mcp_ready`. Если `not ok` → emit `MCP_NOT_READY` event (+1 type) + halt before spawn с понятной ошибкой.
- Config: `Settings.required_mcp_tools: list[str] = []` (default — пусто, opt-in per project). Per-story override через story frontmatter `requires_mcp: [analyzer, postgres-mcp]`.

**Tests.** `tests/test_mcp_readiness.py`:
- 4 unit: polling (immediate ready, slow auth, never ready timeout, partial set)
- 2 unit: parse mcp list output (mock JSON)
- 2 integration: worker_spawn aborts if MCP not ready, succeeds if ready
- **Target:** +8 tests

**Acceptance.**
- Story requires `analyzer` MCP, MCP not connected → worker НЕ спавнится, halt-reason `mcp_not_ready: analyzer`
- Polling timeout 30s соблюдается (test использует sleep mock на 35s → timeout)

---

## 2. P2 — устранить до prod pilot

### #6 subscription-mode budget auto-detect

**Зачем.** `cost_tracking_unavailable reason=subscription_mode` детектится, но $-гейты (cap/daily/story-alarm) халтят на синтетических оценках. Workaround `BMAD_DISABLE_BUDGET=1` ставится руками.

**Что добавляем.**
- `runtime/cost_tracker.py` (или где `cost_tracking_unavailable` событится) — при детекте subscription mode авто-set `Settings._budget_disabled = True` + emit `BUDGET_AUTO_DISABLED` event (+1 type) + log WARN.
- Audit: `events.jsonl` записывает причину auto-disable для post-mortem.

**Tests.** `tests/test_budget_subscription_autodetect.py`:
- 3 unit: trigger на subscription_mode → flag set, idempotent, no double-trigger
- 2 integration: budget gates skipped после auto-disable
- **Target:** +5 tests

**Acceptance.**
- Run в subscription auth mode → первый детект сразу выставляет `_budget_disabled=True`
- `BMAD_DISABLE_BUDGET=1` руками больше не нужен (но override remains supported для tests)

---

### #7 Pre-flight halt-state check до спавна

**Зачем.** Runner отказывается работать если в worktree есть `_bmad/auto-dev-state/halt-reason.txt`. Сейчас оркестратор спавнит всех воркеров → они молча падают на Stage 0.

**Что добавляем.**
- `runtime/worker_spawn.py:_spawn_worker` — перед `subprocess.Popen` проверить `<worktree>/_bmad/auto-dev-state/halt-reason.txt`. Если есть:
  - Default: emit `WORKER_HALT_PRESPAWN` event + skip spawn + понятная ошибка в logs
  - С флагом `--resume`: auto-clear halt-reason.txt + спавн как обычно
  - С флагом `--auto-clear-halt`: то же что `--resume` но per-story scope
- CLI `bmad-orchestrator run --resume` уже есть (предполагается)? Если нет — добавить.
- Аналогично Patch BB orphan pre-flight.

**Tests.** `tests/test_prespawn_halt_check.py`:
- 3 unit: halt file detected → skip spawn, no file → spawn, --resume clears file
- 2 integration: full pilot run, halt presents → graceful skip всех worker'ов одной story (другие проходят)
- **Target:** +5 tests

**Acceptance.**
- Pilot run на worktree с halt-reason.txt → ни один worker не запущен → exit code != 0 → понятная ошибка
- `--resume` flag clear halt + spawn proceeds

---

### #8 Subprocess timeout adaptive / configurable

**Зачем.** `timeout --kill-after=10s 1800 claude -p` (30 мин) killed mid-flight на тяжёлых stories (12+ AC, security-critical). В Antares 1.3 и 1.5 получили `subprocess_timeout` именно так.

**Что добавляем.**
- Default timeout поднять до 3600s (60 мин) в `agent/skills/bmad-auto-dev/scripts/bmad-auto-dev-runner.sh`.
- ENV override: `BMAD_RUNNER_CLAUDE_TIMEOUT_SEC`. В bash: `TIMEOUT_SEC=${BMAD_RUNNER_CLAUDE_TIMEOUT_SEC:-3600}`.
- Adaptive (опционально, sub-task): функция `pick_timeout_sec(story_frontmatter) -> int`:
  - 0-3 AC → 1800
  - 4-8 AC → 3600
  - 9+ AC или security-critical → 5400
- Sync с orchestrator-level `BMAD_WORKER_TIMEOUT_SEC` — runner cap ≤ orchestrator cap.

**Tests.** `tests/test_subprocess_timeout_adaptive.py`:
- 3 unit: pick_timeout по AC count + tags
- 2 unit: ENV override honoured
- 2 unit: runner cap не превышает orchestrator cap (clamp)
- **Target:** +7 tests

**Acceptance.**
- Story 12 AC → timeout 5400s
- `BMAD_RUNNER_CLAUDE_TIMEOUT_SEC=7200` + orchestrator cap 24h → effective 7200
- Default new behaviour: 3600s вместо 1800s

---

## 3. P3 — cleanup

### #9 `real_pilot_done stories=N` misleading counter

**Зачем.** Счётчик считает заспавненные истории, не успешные. Все упавшие worker'ы всё равно дают `stories=3`.

**Что добавляем.**
- `runtime/pilot.py` finalize logic — разделить `spawned: list[str]` и `succeeded: list[str]` (последний фильтр по verdict=approve).
- Log: `real_pilot_done spawned=X succeeded=Y failed=Z`.
- Sprint-status update только для `succeeded` (см. #1 — синергия).

**Tests.** `tests/test_pilot_done_counter.py`:
- 3 unit: counter math (all success, mixed, all fail)
- 1 integration: log format check
- **Target:** +4 tests

**Acceptance.**
- 3 spawned, 1 reject → log `spawned=3 succeeded=2 failed=1`

---

## 4. Phase 3 closure

### #10 Step B — Real BMad stories в eval suite

**Зачем.** Phase 3 IN PROGRESS — Step A (5 synthetic mock cases) closed; Step B = реальные stories из любого target BMad-проекта (не привязано к конкретному).

**Что добавляем.**
- `evals/cases/real/` — 10+ YAML кейсов (3 easy + 4 medium + 3 hard) на основе stories из доступного target проекта.
- Каждый case: spec dump + expected verdict + acceptance criteria + estimated cost/latency baseline.
- CLI `bmad-orchestrator eval run --mode real --project-root <path> --cases-dir evals/cases/real/`.
- Baseline run: запустить `eval run --mode real --repeat 1` на всех 10 кейсах, зафиксировать pass_rate / median_latency_s / median_cost_usd в `evals/baselines/phase3-step-b-baseline.json`.

**Tests.** `tests/test_eval_real_mode.py`:
- 3 unit: YAML schema validation, project_root resolution
- 2 unit: case discovery, filter by tags
- 1 integration: dry-run mock real-mode eval
- **Target:** +6 tests

**Acceptance.**
- Baseline зафиксирован в `evals/baselines/phase3-step-b-baseline.json` с ≥10 cases
- `bmad-orchestrator eval run --mode real --project-root .` отрабатывает с reasonable timeout
- Phase 3 в methodology-virgil.md помечен ✅ DONE

---

## 5. Session breakdown (предварительный)

Финальное разбиение — за `/auto-loop-spec-long` bootstrap. Грубо:

| S | Items | Зачем вместе |
|---|---|---|
| S1 | #1 mark-done + #9 counter | оба касаются `_run_real_pilot_body` finalize, общие тесты |
| S2 | #2 verdict event | один большой item, depends-on others |
| S3 | #3 autofix routing + #8 timeout | оба касаются `bmad-auto-dev-runner.sh`, общая area |
| S4 | #4 AbortController | supervisor area, изолирован |
| S5 | #5 MCP readiness | worker_spawn area, изолирован |
| S6 | #6 budget auto-detect + #7 halt pre-flight | оба «pre-spawn safety», лёгкие |
| S7 | #10 Step B real cases (часть 1 — 5 easy/medium кейсов + harness) | большой |
| S8 | #10 Step B real cases (часть 2 — 5 medium/hard + baseline + docs) | финал |

Decomposer и tracker в bootstrap могут реструктурировать (например, S2 разбить на 2 если verdict event окажется heavier).

---

## 6. Acceptance — epic level

После закрытия всех 10 items:

- ✅ Tests: ≥**1950 PASS** (delta +90 от baseline 1860), mypy/ruff clean
- ✅ Event types: 33-34 (+4-5 новых: `WORKER_STATE_PERSISTED` уже есть; новые — `STORY_AUTO_SPLIT`, `WORKER_CANCELLED`, `MCP_NOT_READY`, `BUDGET_AUTO_DISABLED`, `WORKER_HALT_PRESPAWN`)
- ✅ Branch `integration/pilot-findings-closure` собран, диффы по item'ам разделены commit'ами
- ✅ methodology-virgil.md §5 обновлён: все pilot findings P1/P2/P3 + R1/R2 + Phase 3 Step B помечены ✅ DONE, Phase 3 gate **закрыт**
- ✅ Final Report tracker: pass_rate ≥80% на eval baseline, no regressions
- ⏭ Unblocks: #10 production pilot на любом target BMad-проекте

---

## 7. References

- `spec/methodology-virgil.md` §5 — priority queue, source of all items
- `spec/spec_phase4_hardening.md` — стиль / structure / Tier 1/2 model
- `spec/spec_master_orchestrator.md` — overall architecture
- pilot run logs (Antares 1-7) — source of truth для P1/P2/P3 evidence
- leaked claude-code source (`/home/server/crm/claude-code-main/`) — паттерны для R1/R2

---

**Last updated:** 2026-05-19 (v1.0 — initial)
**Status:** READY for `/auto-loop-spec-long` bootstrap
