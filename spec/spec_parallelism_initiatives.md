# Spec: Parallelism Initiatives (Phase 0 + Initiatives #1-#3)

**Origin:** user direction после first pilot success 2026-05-18
**Branch:** integration/parallelism_initiatives (NEW, не main, не canonical_patches_port)
**Sessions estimated:** Phase 0 = 1 · Initiative #1 = 1-2 · Initiative #2 = 3-4 · Initiative #3 = 4-6 · Total ~9-13

## Vision link

Эти инициативы реализуют **Vision step 1 → step 2** (см. `memory/project_vision_master_bmad_builder.md`):
- step 1 ✅ — embedded phase 4+5 wrapper (валидирован pilot 2026-05-18)
- step 2 = scale-out: параллелизм + multi-project

## Order of execution

| Phase / Initiative | Why first | Blocks |
|---|---|---|
| **Phase 0** — pilot follow-ups (zombie cleanup, post-worker validation, cost honesty, second pilot) | Гигиена перед сложными initiative'ами + второй pilot валидирует Stage 6 (была in-progress) | Initiative #1 |
| **Initiative #1** — Story parallelism MVP | Простое, foundation для #2 и #3 | #2, #3 |
| **Initiative #2** — Story split + intra-story parallelism | Build на worker pool из #1 | — |
| **Initiative #3** — Multi-project queue | Capstone; нужен #1 + project-agnostic CLI | — |

---

## Phase 0 — Pilot follow-ups (1 session)

**Цель:** закрыть gaps найденные в pilot v1-v7 + второй полный pilot.

### Task 0.1 — Zombie orchestrator cleanup

**Файл:** `src/bmad_orchestrator/agent/run.py` (`_run_real_pilot` finally)
**Симптом:** 5 zombie orchestrator процессов копились из неудачных run'ов; убийство одного через `pkill` убило и активный pilot.
**Fix:** в начале `_run_real_pilot` сделать pre-cleanup:
```python
import subprocess
subprocess.run(
    ["pkill", "-9", "-f", f"bmad-orchestrator run --project {project}"],
    check=False,
)
# wait briefly, then proceed
```
Также — `finally:` блок в pilot для kill child processes (claude -p, bwrap) если orchestrator crash'ит.

**Test:** запустить pilot, kill orchestrator mid-stage, рестартануть → проверить что child процессы умерли.

### Task 0.2 — Post-worker validation gate

**Файл:** `src/bmad_orchestrator/agent/run.py` (после `_tail_and_emit_completion`)
**Симптом:** `real_pilot_done halted=False rounds=1 stories=1` хотя worker `total_usd=0.00 input_tokens=0` и **0 commits** на feature branch. Pilot считался успехом.
**Fix:** после worker'а:
```python
async def _validate_worker_actually_worked(handle):
    """exit_code=0 + zero commits = silent failure."""
    if handle.exit_code != 0:
        return  # already handled by halt-on-fail
    commits = await _count_commits_since_base(handle.worktree, base_ref)
    if commits == 0:
        await bus.emit(EventType.WORKER_SILENT_FAILURE, story_id=handle.story_id, ...)
        # treat as halt-on-fail equivalent
```

**Test:** mock worker returning exit=0 with no commits → orchestrator emits SILENT_FAILURE event + halts.

### Task 0.3 — Cost tracker honesty в subscription mode

**Файл:** `src/bmad_orchestrator/runtime/worker_spawn.py` или `cost_tracker.py`
**Симптом:** `worker_cost_final total_usd=0.00` ВСЕГДА в subscription mode (нет API key для cost calculation). Метрика misleading.
**Fix variant A:** detect subscription mode → emit `cost_tracking_unavailable_subscription` instead of zero values.
**Fix variant B:** парсить `claude -p` stream для `usage:` blocks (если есть) → реальные tokens.

Recommend **A** (simple, honest). B можно отдельной session.

**Test:** spawn в subscription mode → event = `cost_tracking_unavailable` not `cost_final 0.00`.

### Task 0.4 — Pre-spawn worktree freshness check

**Файл:** `src/bmad_orchestrator/agent/run.py` (`_ensure_git_worktree`)
**Симптом:** pre-existing файлы в worktree (от commit `d2d2592` Antares pre-flight) пересеклись с dev-story output. Worker молодец заметил, но это были «лишние циклы».
**Fix:** при создании worktree чек — `git status -s` → если непустой, warn в audit log «pre-existing untracked files может конфликтовать с story output».

### Task 0.5 — Second pilot Antares (Story 1.2 docker-compose-dev-stack)

После 0.1-0.4 запустить **полный** pilot включая Stage 6 (без моего kill'а). Цель: end-to-end validation pipeline на втором story.

**Команда:**
```bash
ORCHESTRATOR_TARGET_PROJECT=/home/server/Antares \
BMAD_REQUIRE_SANDBOX=1 BMAD_DISABLE_BUDGET=1 \
bmad-orchestrator run --project antares --wave smoke --real \
    --story 1-2-docker-compose-dev-stack \
    --max-stories 1 --max-spend-usd 999999
```

**Success criteria:** sprint-status 1.2=done, Antares master имеет docker-compose.yml + dev-stack files, merge clean.

### Phase 0 commit checkpoint

После 0.1-0.5 — commit на `integration/parallelism_initiatives` ветке:
```
chore(pilot-followups): zombie cleanup, post-worker validation, cost honesty, freshness check
```

---

## Initiative #1 — Story parallelism MVP (1-2 sessions)

**Цель:** запустить N stories одновременно из одной wave. CLI flag + presets.

Memory: `project_backlog_parallelism_presets.md`

### Task 1.1 — Presets + CLI flag

**Файл:** `src/bmad_orchestrator/cli.py` + `config.py`
```python
@app.command()
def run(
    parallel: int = typer.Option(1, "--parallel", help="N workers in parallel (1/3/5/10 presets)"),
    ...
):
    if parallel not in (1, 3, 5, 10):
        raise typer.BadParameter("--parallel must be one of: 1, 3, 5, 10")
```

Передать в `_run_real_pilot` как `max_parallel` (уже принимает).

### Task 1.2 — File-conflict pre-check

**Файл:** новый `src/bmad_orchestrator/agent/file_conflict.py`
**Задача:** перед спавном batch'а проверить что stories не пишут в один файл (через `touches_files` frontmatter в story.md).
```python
def conflict_check(stories: list[Story]) -> list[tuple[str, str, str]]:
    """Return list of (story_a, story_b, conflicting_file) tuples."""
    seen: dict[str, str] = {}  # file → first story
    conflicts = []
    for s in stories:
        for f in s.touches_files:
            if f in seen:
                conflicts.append((seen[f], s.id, f))
            else:
                seen[f] = s.id
    return conflicts
```

Если conflicts non-empty → orchestrator splits batch (одну группу в parallel, конфликтующая — sequential after).

### Task 1.3 — Resource limits per worker (cgroup)

**Файл:** `src/bmad_orchestrator/runtime/sandbox.py`
**Задача:** ограничить RAM/CPU per claude-sonnet worker (3 параллельных = 24GB RAM иначе). Использовать `systemd-run --scope -p MemoryMax=8G -p CPUQuota=200%`.

Связано с memory `project_backlog_sandbox_cgroup_migration`.

### Task 1.4 — Shared Claude state isolation

**Файл:** `src/bmad_orchestrator/runtime/worker_spawn.py`
**Симптом:** все workers binding `~/.claude/` → конкурентная запись token rotation/history → corruption.
**Fix:** per-worker `HOME` overlay в bwrap:
```
--bind /tmp/worker-N-home /home/server
```
Скопировать `~/.claude/` снимок в `/tmp/worker-N-home/.claude/` перед спавном.

### Task 1.5 — Validation pilot

Antares Story 1.2 + 1.3 + 1.5 параллельно (1.4 depends on 1.3, sequential).
Команда: `bmad-orchestrator run --parallel 3 ...`

**Success criteria:** все 3 done в один wall-clock (~30 мин), не последовательно (~90 мин); no file conflicts; sprint-status все done.

---

## Initiative #2 — Story split + intra-story parallelism (3-4 sessions)

**Цель:** большая story (>5k tokens или >10 файлов) → DAG planner авто-разбивает на sub-stories → параллелит внутри.

Memory: `project_backlog_auto_split_parallelism.md`

### Task 2.1 — should_split heuristic

**Файл:** `src/bmad_orchestrator/agent/skills/dag-planner/` (новый python helper)
```python
def should_split(story: Story) -> bool:
    return (
        story.estimated_tokens > 5000
        or len(story.touches_files) > 10
        or story.has_independent_subtasks()  # markers в spec
    )
```

### Task 2.2 — LLM-based decomposition

**Skill:** новый `story-splitter` (уже есть в `agent/skills/` per pilot inspection)
Spawn Opus 4.7 с prompt: «Разбей эту story на 2-5 sub-stories с явными deps. Output JSON: `[{id, title, deps_on}]`».

### Task 2.3 — Sub-story execution

Использовать Initiative #1 worker pool. Каждая sub-story → отдельный worktree?  
**Decision needed:** sub-stories share parent worktree (faster, но conflict risk) ИЛИ свои worktrees (safer, slower setup).

Recommend: **shared parent worktree, sequential sub-stories внутри**. Параллелизм — на уровне parent stories из Initiative #1, не sub-stories. KISS.

Если позже нужна intra-parallelism → отдельная session.

### Task 2.4 — Sub-stories merge back

После всех sub-stories done → squash-merge их commits в один parent commit на feature/<parent-id> branch. Затем normal pipeline продолжает (Stage 6 code-review на объединённый код).

### Task 2.5 — Validation pilot

Antares Story 3.1 (Nextcloud Docker template, ~7 sub-tasks per epics.md) → auto-split → измерить speedup.

---

## Initiative #3 — Multi-project queue (4-6 sessions)

**Цель:** оркестратор работает над Antares + Odyssey + third project одновременно.

Memory: `project_backlog_post_mvp.md::multi-project-queue`

### Task 3.1 — Project registry

**Файл:** `~/.bmad-orchestrator/projects.yaml` (или `<orchestrator>/config/projects.yaml`)
```yaml
projects:
  antares:
    path: /home/server/Antares
    bmad_layout: bmm-v6
    sandbox_overrides: { ... }
  odyssey:
    path: /home/server/odyssey-ux
    bmad_layout: odyssey-hybrid
    sandbox_overrides: { ... }
```

### Task 3.2 — Project-agnostic CLI (scan/doctor/init/resume)

Из memory `project_backlog_orchestrator_project_agnostic`. Чтобы добавить новый проект:
```bash
bmad-orchestrator init /path/to/project   # detects layout, generates config
bmad-orchestrator doctor antares          # health check
bmad-orchestrator scan                    # list known projects + status
```

### Task 3.3 — Multi-project execution

**Файл:** новый `src/bmad_orchestrator/multi_run.py`
```python
async def run_multi(projects: list[str], total_parallel: int = 10):
    # Split N workers across projects (e.g. 5/5 or 3/4/3)
    # Spawn per-project _run_real_pilot async
    # Shared budget guard
```

CLI:
```bash
bmad-orchestrator multi --projects antares,odyssey --parallel 10
```

### Task 3.4 — Per-project state isolation

- Memory `.claude/memory/` per project (already via worktree paths)
- Sprint-status updates atomic per project
- Cross-project budget allocation (один daily cap → split)

### Task 3.5 — Validation pilot

Одна wave Antares + одна wave Odyssey параллельно (5 workers / 5 workers). Проверить: no state corruption, no skill cross-pollination, both waves complete.

---

## Cross-cutting concerns

### Testing strategy

- Каждый task → pytest coverage
- Pilot validation для каждой initiative обязателен (smoke test недостаточен)
- Маркер `@pytest.mark.slow` для real-mode integration tests (run только locally / nightly CI)

### Memory updates

После каждой завершённой initiative:
- Update `MEMORY.md` индекс
- Создать `project_milestone_initiative_<N>_complete.md`
- Если есть новые feedback rules → отдельный `feedback_*.md`

### Branch strategy

- Все работы — на `integration/parallelism_initiatives` (NEW branch off `integration/canonical_patches_port`)
- НЕ merge'ить в main без human OK (per CLAUDE.md)
- После каждой initiative — code-review pass перед следующей

### Safety gates

- Никогда `--no-verify`
- Никогда `git push --force` без explicit user request
- При sandbox cgroup migration (1.3) — protect prod CRM mounts (`/home/server/crm/` НЕ должен оказаться в worker'е bind list)

---

## Phase 4 — Mandatory final gate (1-2 sessions)

После завершения всех 3 initiatives (после Initiative #3 validation pilot) — обязательный security/quality gate:

### Task 4.1 — Full code review (Opus 4.7)

Spawn `claude -p` Opus с инструкцией: «прочти весь diff `integration/parallelism_initiatives` vs `integration/canonical_patches_port`, найди bugs / security issues / regressions / архитектурные косяки. Report verdict: PASS / NEEDS-FIX / BLOCKED + list of findings с severity».

Параллельно spawn'ить `code-auditor` subagent — отдельный второй reviewer для cross-check.

### Task 4.2 — Auto-fix found findings

Для каждого finding severity ∈ {P0, P1, High}:
- Spawn `claude -p` с задачей «fix this specific finding: <description>. Edit only the affected file/function. Run tests. Commit «fix(<scope>): address review finding <id>»».
- Auto-accept fix если: tests pass + ruff PASS + mypy PASS.
- Если auto-fix не прошёл — записать в `Blockers / Pauses` с типом `autofix_failed` + finding details.

### Task 4.3 — Re-review after fixes

После всех auto-fix коммитов — повторный review pass. Если новый verdict = PASS → mark initiative complete. Если NEEDS-FIX → ещё один auto-fix round (max 2 retry total). Если BLOCKED после 2 retry → halt, escalate user.

### Task 4.4 — Security audit

Отдельный pass через `security-auditor` subagent. Проверка:
- SQL injection / command injection в новых helpers
- Sandbox bypass risks (особенно после cgroup migration + per-worker HOME)
- Secrets leak в logs (cost tracker, multi-project audit logs)
- Race conditions в parallel worker pool

Auto-fix принципы те же что в 4.2.

### Task 4.5 — Final report + tests sweep

- Run `pytest tests/ -q` → expect 1162+ PASS (new tests added)
- Run `ruff check src/` → expect 0 errors
- Run `mypy src/` → expect 0 errors  
- Update memory: `project_milestone_parallelism_initiatives_complete.md` + lessons learned
- Generate Final Report в tracker с metrics, commits list, merge hint

**Success criteria для всего spec'а:** Phase 0 + Initiative #1-#3 + Phase 4 all complete; integration branch ready for manual merge to main с verdict PASS от двух reviewer'ов.

---

## 10. Session Plan

Sessions для `/auto-loop-spec-long` bootstrap (delay 180s между сессиями, runtime=loop_wrapper, auto_merge=false).

- **S1** — Phase 0 (pilot followups all 5 tasks + second pilot validation Antares Story 1.2). Surface: backend-python. Acceptance: zombie cleanup wired, post-worker validation, cost honesty, pre-spawn freshness, sprint-status 1.2=done.
- **S2** — Initiative #1 part A (CLI parallel flag + presets + file-conflict pre-check). Surface: backend-python. Acceptance: `--parallel 3` спавнит 3 worker'а параллельно, conflict detector splits batch правильно.
- **S3** — Initiative #1 part B (cgroup limits per worker + per-worker HOME isolation + parallel validation pilot 1.3+1.5). Surface: infra-with-recovery (sandbox changes). Acceptance: 2 stories реально параллелятся, ~30 мин wall-clock vs ~60 sequential.
- **S4** — Initiative #2 part A (should_split heuristic + story-splitter skill scaffold + LLM decomposition). Surface: backend-python. Acceptance: tests на should_split heuristic + sample decomposition output validates.
- **S5** — Initiative #2 part B (sub-story execution через worker pool + squash-merge sub-stories back). Surface: backend-python. Acceptance: integration test с mock sub-stories.
- **S6** — Initiative #2 part C (validation pilot Antares Story 3.1 — Nextcloud Docker template, auto-split → speedup measure). Surface: backend-python. Acceptance: 3.1 split на 3+ sub-stories, speedup ≥2× vs sequential baseline.
- **S7** — Initiative #3 part A (project registry yaml + CLI scan/doctor/init/resume). Surface: backend-python. Acceptance: `bmad-orchestrator init /path` создаёт config, `scan` показывает оба проекта.
- **S8** — Initiative #3 part B (multi-project execution + per-project state isolation). Surface: backend-python. Acceptance: tests на shared budget guard + isolated sprint-status updates.
- **S9** — Initiative #3 part C (validation pilot — Antares wave + Odyssey wave параллельно, проверка cross-pollution). Surface: backend-python. Acceptance: оба waves complete, sprint-status каждого корректен, no state leak.
- **S10** — Phase 4 part A (mandatory full code review Opus + code-auditor cross-check). Surface: backend-python (review surface). Acceptance: оба reviewer'а выдали findings list с severity tags.
- **S11** — Phase 4 part B (auto-fix P0/P1/High findings + re-review + security-auditor pass + final report). Surface: backend-python. Acceptance: verdict PASS от обоих reviewer'ов после ≤2 retry; 1162+ tests PASS; ruff+mypy 0; Final Report written.

**Total: 11 sessions · estimated wall-clock 9-15h (с paus'ами 180s)**

## Next session bootstrap

После bootstrap'а — у пользователя одна команда:
```bash
tmux new -d -s autoloop-parallelism_initiatives bash .claude/scripts/auto-loop-parallelism_initiatives.sh
tmux new -d -s watchdog-parallelism_initiatives bash .claude/scripts/watchdog-parallelism_initiatives.sh
```

Если что-то непонятно — read `md/retrospective_first_pilot_2026-05-18.md` для контекста почему мы это делаем.
