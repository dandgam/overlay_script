# Handoff: bmad-auto-dev → Orchestrator (Phase 2)

**Дата:** 2026-05-15
**Автор:** Claude Opus 4.7 (1M context) + AABIT
**Назначение:** контекст для следующей сессии где будем создавать agent-оркестратор для полностью автономной + параллельной Phase 4 + Phase 5 в проекте Odyssey

---

## 1. TL;DR — что хотим

**Сейчас (Phase 1):** `bmad-auto-dev` скилл = sequential, 1 story за раз, человек мониторит. Wave 0a complete (7/7), Wave 0b в процессе (1/15 done).

**Хотим (Phase 2):** **orchestrator-agent**, который:
- Параллелит исполнение N stories через git worktree
- Сам делает retrospective на wave/epic boundary без участия человека
- Сам решает formality issues (без halt) согласно `feedback_decide_dont_halt` memory
- Сам валидирует cross-story merge conflicts
- Сам синкает skill patches между worktrees
- Сам делает financial budget guards (>$50 — halt + ask)
- Финальная Phase 5 retrospective когда всё Phase 4 closed → автогенерация Wave 2 Thor PRD draft

**Цель:** ~316 stories (Wave 1 active) → автономный run за 2-4 недели wall-clock вместо 6-12 weeks sequential.

---

## 2. Что уже есть (готовый scaffold)

**Скилл `bmad-auto-dev`** (Phase 1, sequential, текущий):
- Path: `~/.claude/skills/bmad-auto-dev/` + project mirror `<project>/.claude/skills/bmad-auto-dev/`
- 9 stages (Stage 0-8) per story: preflight → select → branch → gauntlet → create-story (Opus) → dev-story (Sonnet) → code-review (Opus) → merge → batch-gate
- 12 landed patches A-L (см. секцию 4 ниже)
- `scripts/bmad-auto-dev-runner.sh` (Bash, ~600 lines)
- `scripts/dependency_analyzer.py`, `gauntlet_injector.py`, `batch_gate.py`
- `customize.toml`: batch.size, gauntlet.deep_tags/epics, retrospective.auto_invoke_wave_boundary

**Существующий orchestrator repo:** `/home/server/bmad-orchestrator/` (commit b2a7228) — Python scaffold + spec. **Проверить состояние** на старте новой сессии (memory `reference_bmad_orchestrator_repo.md`).

---

## 3. Architecture decisions taken

### 3.1 Sequential (Phase 1) vs Parallel (Phase 2)

**Решено в этой сессии:** Phase 1 = sequential, Phase 2 = orchestrator с worktree parallelism.

| | Phase 1 (current) | Phase 2 (target) |
|--|-------------------|-------------------|
| Параллелизм | 1 story at a time | N worktrees параллельно |
| Stage 8 retro | Inline (Patch L) | **Orchestrator spawns в параллельной worktree** |
| Cross-story conflicts | N/A (sequential) | **Conflict detector + serialized merges** |
| Human checkpoint | Каждая story в auto mode, halt только destructive | Только wave boundary + budget guard |
| Skill patches | Live-sync 2 копии (user + project) | **Auto-sync across worktrees** |

### 3.2 Где живёт retrospective

| Уровень | Сколько раз | Когда | Кто запускает в Phase 1 | Кто в Phase 2 |
|---------|:-----------:|-------|--------------------------|---------------|
| **Wave** retrospective | 6 (0a, 0b, 1a, 1b, 1c, 1d) | После каждой wave | Inline runner Stage 8.5 (Patch L) | **Orchestrator** в параллельной worktree |
| **Epic** retrospective | 2 (Epic 1, Epic 7 critical) | После последней story эпика | **Manual** (`/bmad-retrospective`) | **Orchestrator** auto-detects |
| **Phase 5 final** | 1 (ONLY) | После закрытия всех stories Phase 4 | Manual | **Orchestrator** auto-detects + spawn Wave 2 (Thor) PRD draft |

### 3.3 Decision-making autonomy

Память `feedback_decide_dont_halt.md` (создана сегодня):
- **Halt for human approval ТОЛЬКО при:** destructive ops, security/data risk, кардинальный pivot, spend >$50
- **Решать сам + документировать ПРИ:** spec formality (AC text, commit shape), choice между approaches с одинаковым outcome, defense-in-depth defer

Orchestrator должен **унаследовать это правило** — pause только когда реально нужно человеку.

### 3.4 Cost routing per stage (CLAUDE.md cost-routing)

- **Opus 4.7** — Stage 4 create-story (heavy context), Stage 6 code-review (adversarial depth)
- **Sonnet 4.6** — Stage 5 dev-story (implementation per spec)
- **Haiku 4.5** — пока нигде не используется; orchestrator может задействовать для mechanical sub-tasks (rename, format, merge)

---

## 4. Skill patches A-L (что наследуется orchestrator'ом)

| ID | Что | Phase 2 plan |
|----|-----|--------------|
| **A** | `--batch-name` auto-creates integration branch | Keep + extend для worktree-batch-name |
| **B** | Auto-fix retry на review fail (deprecated by I) | Removed |
| **C** | Smart deletion guard (allow .gen/dist/lock files) | Keep |
| **D** (+L1) | Commit dirty review artifacts pre-checkout (whitelist paths) | Keep, applied per-worktree |
| **F** | Git status diagnostics в halt-reason | Keep |
| **G** | Anthropic API auto-retry 30s × 2 | Keep |
| **H** | Hard timeout 1800s ceiling per `claude -p` | **Bump to 2400s** for Wave 0b production stories (post-1.8a learning, 30 min было впритык) |
| **I** | Structured findings auto-apply (≤300 lines budget) | **Bump retry budget to 2-3** for complex stories. Add budget cap escalation when Critical+High >5 findings |
| **J** | Elicitation 5-lens enforcement | Keep. Already v1.1 (scan name+body). |
| **K** | sprint_status_mark_done long-key support | Keep |
| **L1** | Patch D whitelist (Wave 0a hygiene fix) | Keep |
| **L2** | Auto-retrospective on wave_transition | **Migrate to orchestrator** — parallel worktree |

---

## 5. Failure modes orchestrator должен обработать

### 5.1 Carry-over from Phase 1

- **API errors** (Patch G) — transient socket/ECONNRESET → 30s retry
- **claude -p hang** (Patch H) — 30 min timeout + retry 1× → halt-on-fail
- **Stage 4/5/6 non-zero exit** → halt with halt-reason.txt
- **Code-review NEEDS-FIX 20+ findings** (Patch I budget cap) → pause at 5 fixes, escalate to human

### 5.2 NEW в Phase 2 (orchestrator-specific)

- **Worktree conflict on shared file** — два worktree редактируют `sprint-status.yaml`, `deferred-work.md`, общие крейты. Нужно: `flock(2)` + retry queue
- **Cross-story dependency не в spec** — Story X внезапно нужна Story Y которая ещё не done. Нужно: dep-graph re-evaluation + worktree-pause
- **Skill patch sync** — патч skill landed в одном worktree, не виден другим. Нужно: file-watch + auto-pull
- **Budget overrun** — single story >$50 spend → halt + ask. Aggregate batch >$200 → checkpoint.
- **Anthropic ratelimit hit** — несколько worktrees параллельно превышают account TPM/RPM. Нужно: token bucket per-worktree
- **Test flake on integration branch** — merge succeeds in one worktree, breaks integration test from another. Нужно: post-merge integration test gate

### 5.3 Не пытаемся

- **No silent retries beyond Patch G/H limits** — escalate
- **No auto-history-rewrite** — destructive, всегда halt+ask
- **No cross-account spend** — single Anthropic account, не масштабируем horizontally
- **No worktree-level git push to origin** — local-only, merge to main = human checkpoint

---

## 6. State management

### 6.1 Single source of truth

- `sprint-status.yaml` — story status (`backlog | ready-for-dev | in-progress | review | done`). **Serialized writes only** (flock + Python regex per Patch K).
- `_bmad/auto-dev-state/state.json` — per-run state (gitignored). **Per-worktree** in Phase 2.
- `_bmad/auto-dev-state/current-batch.json` — batch progress (gitignored). **Per-worktree.**
- `_bmad/auto-dev-state/halt-reason.txt` — halt indicator (gitignored). **Per-worktree.**
- `_bmad/auto-dev-state/gauntlet/<id>/prompts.json` — per-story (gitignored).

### 6.2 Worktree layout (proposed Phase 2)

```
/home/server/odyssey/             # main worktree, integration branch
/home/server/odyssey-wt-1/         # worktree 1, feature/story-X (in-progress)
/home/server/odyssey-wt-2/         # worktree 2, feature/story-Y
/home/server/odyssey-wt-3/         # worktree 3, feature/story-Z
/home/server/odyssey-retro/        # ephemeral worktree для wave/epic retro
```

Orchestrator выбирает story по dep-graph, spawns worktree, runs skill в нём, мерджит обратно в integration, рекламирует.

### 6.3 Lock files

- `_bmad/implementation-artifacts/sprint-status.yaml.lock` (Patch K) — write-lock на sprint-status
- `_bmad/implementation-artifacts/deferred-work.md.lock` (NEW) — write-lock на defer list (часто пишут review агенты)
- Каждая worktree должна respect эти locks

---

## 7. Cost model

### 7.1 Phase 1 actual (Wave 0a)

- 7 stories × ~$10-15 avg = ~$60-90 total
- Story 1.5 (security-critical, 3 review rounds) = ~$30-40
- Wave 0a wall-clock: ~6 hours (включая human review intervals)

### 7.2 Phase 2 estimated (Wave 0b + 1)

- Wave 0b: 15 stories × ~$15 = $225 (production stories дороже)
- Wave 1a-1d: 316 stories × ~$10 = $3160
- Phase 5 final retro + Wave 2 PRD: $50-100
- **TOTAL Wave 0b → Phase 5:** ~$3500-4000 estimated

### 7.3 Per-worktree budget guard

- Hard cap per story: $30 (alarm), $50 (halt)
- Hard cap per batch: $200 (alarm), $300 (halt)
- Phase 5 final: separate ~$100 budget

Track via `claude` CLI debug logs (per-invocation token count × model price). Aggregate in orchestrator dashboard.

---

## 8. Concrete tasks для orchestrator-agent

### MVP (Phase 2 v1)

1. Read `bmad-auto-dev` SKILL.md + runner.sh — understand 9-stage workflow
2. Scaffold orchestrator at `/home/server/bmad-orchestrator/` (or pivot existing scaffold)
3. Worktree management: `git worktree add`, branch tracking, cleanup
4. Dep-graph: compute next N runnable stories from `sprint-status.yaml`
5. Spawn skill per worktree with appropriate `--batch-name` + `--max 1`
6. Wait for skill exit, parse halt-reason or merge outcome
7. Serialize sprint-status updates via lock
8. Auto-spawn retrospective worktree on wave_boundary detection
9. Budget tracker (per-story + per-batch spend)
10. Human checkpoint UI: print summary + halt at wave end

### v2 enhancements

- ML re-ordering: by deps + by historical complexity (avg hours)
- Conflict prediction: pre-merge dry-run
- Auto-rollback on integration test failure
- Telegram/Slack notifications on halt events (per CLAUDE.md «Visible to others» rules)
- Cost dashboard

### v3 horizon

- Multi-account: spread load across multiple Anthropic API keys
- Cross-project: orchestrator manages Odyssey + CRM hotfix queue
- Self-modifying: orchestrator proposes skill patches via PR

---

## 9. Open questions для следующей сессии

1. **Где жить orchestrator?** Python repo `/home/server/bmad-orchestrator/` уже scaffold'ed (commit b2a7228) — продолжить или pivot? Вариант: переписать как Rust binary в monorepo Odyssey crate `apps/orchestrator/`.

2. **Когда orchestrator-agent v1 первый раз запускается?** Не на Wave 0b (уже идёт sequential). Возможно после Wave 0b complete → начать Wave 1a с orchestrator. ИЛИ — после Wave 0b сделать ретроспективу + полный rewrite.

3. **Какой минимум для MVP?** Параллелизм 2 worktree (= 2x speedup) или сразу 3-5? Цифра 2 проще для validation; цифра 5 близка к Anthropic TPM limit.

4. **Telegram integration для halt notifications?** Удобно для AABIT (single founder, async monitoring) — но требует bot setup + RKN compliance review (PII в halt-reason?).

5. **Phase 5 final retrospective + Wave 2 PRD автогенерация — autonomous OR co-design с человеком?** Phase 5 — стратегическая milestone, нужно ли human review или дать LLM свободу?

---

## 10. Memory files relevant (load в новой сессии)

- `~/.claude/projects/-home-server-odyssey/memory/MEMORY.md` — индекс
- `feedback_decide_dont_halt.md` — autonomous decision rule (NEW today)
- `feedback_dont_kill_if_alive.md` — liveness check before kill
- `reference_bmad_orchestrator_repo.md` — existing scaffold pointer
- `project_wave_0a_progress.md` — Wave 0a outcomes + 8 skill patches landed
- `project_odyssey_decisions.md` — 6 critical project decisions

---

## 11. Quick start checklist для новой сессии

```
1. Read MEMORY.md
2. Read this file (orchestrator-handoff-2026-05-15.md)
3. Read existing scaffold: /home/server/bmad-orchestrator/SPEC.md (if exists)
4. Read current skill: ~/.claude/skills/bmad-auto-dev/SKILL.md
5. Check Wave 0b progress: cat odyssey/_bmad/implementation-artifacts/sprint-status.yaml | grep ' 1-'
6. Decide: continue scaffold OR pivot
7. Build MVP: 2-worktree parallel runner + retrospective auto-spawn
8. Test on Wave 1a first 3 stories (parallel)
9. Measure: wall-clock speedup vs cost
10. Iterate
```

---

**End of handoff. Готов передать в новую сессию.**

**Полный путь:** `/home/server/odyssey/spec/orchestrator-handoff-2026-05-15.md`
