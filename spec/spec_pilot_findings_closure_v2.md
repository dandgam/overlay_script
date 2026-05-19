# spec_pilot_findings_closure_v2 — Закрытие новых pilot findings (Antares 1a real)

> **Owner:** user + Virgil orchestrator
> **Created:** 2026-05-19
> **Goal:** Закрыть 4 новых P1/P2 находки из первого реального production pilot (Antares Epic 1a, post-`f2ff857`). Разблокировать повторный прогон того же пилота на чистой конфигурации.
> **Source:** `spec/methodology-virgil.md` §5 — секция «Backlog — НОВЫЕ pilot findings (Antares 1a real 2026-05-19, post-closure)».
> **Predecessor:** `spec_pilot_findings_closure.md` (P1/P2/P3 первой волны, merged `f2ff857`).
> **Scope decision:** все 4 finding'а (2× P1 + 2× P2). Без R3-R5 deferred research findings и без backlog cleanup — отдельные инициативы.

---

## 0. Context

### Inventory at start

- Branch: `main` @ `22d5026`
- Tests: **~1945 PASS** (точное число — `pytest --collect-only -q | tail -1`)
- Pilot 1a verdict: 3/3 stories halted одинаково на runner Stage 7. Story 1.3 содержит реальную autofix-работу (+1286/-37 LOC, 22 файла) на `feature/1.3` — **не merged**, ждёт фикса NEW-2.
- Phase 4 #10 production pilot: ⬜ blocked by NEW-1 + NEW-2 (NEW-2 — главный блокер).

### Что вошло в epic

| # | Tier | Item | Estimate | Source |
|---|---|---|---|---|
| 1 | P1 | `--project <slug>` flag не побеждает `ORCHESTRATOR_TARGET_PROJECT` env | ~0.5 сессии | NEW-1 |
| 2 | P1 | runner Stage 7 cleanup ломается на reused worktree | ~1-1.5 сессии | NEW-2 |
| 3 | P2 | S1 `normalize_story_id` не матчит kebab+slug composite | ~0.3 сессии | NEW-3 |
| 4 | P2 | `worker_completed status=success` при runner exit 1 (outer/inner race) | ~0.3 сессии | NEW-4 |

**Total:** ~2-2.5 сессии. Параллелить нельзя (все 4 трогают runtime/runner; merge-order конфликтов слишком много).

### Hard rules

- Branch isolation: вся работа в `integration/pilot-findings-closure-v2`. **Никогда** напрямую в `main`.
- Auto-merge **disabled** — финальный merge в main делает user руками.
- `--no-verify`, `--amend` после hook reject, `--force` — запрещены.
- Tests delta cap: ≥ +15 tests (см. per-item) → target ≥1960 PASS.
- mypy/ruff clean каждый commit.
- 1 item = 1 commit (внутри одной session допустимо 2 commit'а если найден refactor).

---

## 1. P1 — критический путь к повторному pilot'у

### #1 (NEW-1) `--project` flag должен побеждать env var

**Зачем.** `virgil run --project antares` создаёт worktrees в `/home/server/odyssey/.worktrees/wt-1.X` несмотря на `config/projects.yaml::antares.path=/home/server/Antares`. Pydantic-settings связывает `Settings.target_project` с `ORCHESTRATOR_TARGET_PROJECT` env var (config.py:113-118). `_resolve_settings_for_project` (cli/main.py:393-413) пытается override через registry, но:
- Если registry entry отсутствует → graceful degrade возвращает оригинальные settings (odyssey).
- Если entry есть, но downstream код где-то ещё читает `os.environ["ORCHESTRATOR_TARGET_PROJECT"]` напрямую → override теряется.

**Что добавляем.**

- **Audit pass:** `rg "ORCHESTRATOR_TARGET_PROJECT" src/` → найти все callsites, заменить прямые `os.environ.get(...)` на чтение из переданного `Settings.target_project`. Допустимое исключение: `worker_spawn._build_worker_env` (там env инжектится в subprocess — это by design).
- **Registry-miss fail-loud:** в `_resolve_settings_for_project`:
  - Если `project_slug` передан, но в registry entry нет → **raise `ProjectNotFoundError`** (не silent fallback). Сообщение: `"Project '{slug}' not found in {registry_path}. Run 'virgil project add' or check config/projects.yaml."`.
  - Если flag `--project` явно дан и не матчит current settings → log на INFO уровне «overriding ORCHESTRATOR_TARGET_PROJECT=… with --project=… → resolved path …».
- **CLI precedence rule (комментарий + тест):** документировать порядок resolution: `--project` flag > `config/projects.yaml` lookup > `ORCHESTRATOR_TARGET_PROJECT` env > default. Зафиксировать как docstring `_resolve_settings_for_project`.

**Tests.** `tests/test_project_flag_overrides_env.py`:
- 3 unit на `_resolve_settings_for_project`: flag + entry в registry, flag без entry (raises), flag совпадает с settings.target_project.name (no-op).
- 2 integration: `virgil run --project antares` с `ORCHESTRATOR_TARGET_PROJECT=/home/server/odyssey` env → resolved path = `/home/server/Antares` (verified через captured Settings).
- 1 regression: `virgil snap` / `virgil eval` / другие subcommands с `--project` → тот же effective target.
- **Target:** +6 tests.

**Acceptance.**
- `ORCHESTRATOR_TARGET_PROJECT=/home/server/odyssey virgil run --project antares --wave 1a --real --dry-run` → лог содержит `target_project=/home/server/Antares`, **никакой** worktree в odyssey не создан.
- Команда падает с понятным сообщением если `--project foo` не зарегистрирован (вместо silent run в odyssey).

---

### #2 (NEW-2) runner Stage 7 cleanup на reused worktree

**Зачем.** На Antares 1a 3/3 stories halted одинаково: `error: cannot delete branch 'feature/1.X' used by worktree at '/home/server/Antares/.worktrees/wt-1.X'`. `bmad-auto-dev-runner.sh` Stage 7 cleanup делает `git branch -D feature/<id>` на ветке, checked out в worktree → git отказывает → runner exit 1 → outer `claude -p` exit 0 → orchestrator пишет `worker_silent_failure`. **Story 1.3 fully прошла Stage 4-6 + autofix iter H1/H2/H3/M2-M11**, 2 коммита (+1286/-37 LOC) на `feature/1.3` — потеряны (verdict event не эмитился, integration ветка не создана).

**Что добавляем (комбинация 3-х слоёв защиты).**

**Слой A — Runner-side graceful Stage 7 (главный fix):**
- `agent/skills/bmad-auto-dev/scripts/bmad-auto-dev-runner.sh` Stage 7:
  - Перед `git branch -D feature/<id>` → проверить `git worktree list --porcelain | grep "branch refs/heads/feature/<id>"`.
  - Если branch checked out в worktree → **skip cleanup**, log `stage7_skipped reason=used_by_worktree branch=feature/<id> worktree=<path>`.
  - Если есть unmerged коммиты с `base_sha` → emit synthetic `claude_event verdict=approve commits=N` в worker JSONL **перед** exit (даёт orchestrator-side `merge_to_integration_subscriber` шанс отработать).
  - Только при clean Stage 7 (no worktree conflict, no commits) → `git branch -D` без error.

**Слой B — Orchestrator-side detector (defence-in-depth):**
- `runtime/worker_silent_failure.py` (или где живёт detector): добавить pattern `cannot delete branch .* used by worktree` в `stdout_lines` regex.
- При match → emit `RUNNER_CLEANUP_FAILED_REUSED_WORKTREE` event (новый EventType #30) + проверить через `git log --oneline base_sha..feature/<id>` есть ли коммиты:
  - Если есть → emit synthetic `CODE_REVIEW_VERDICT verdict=approve source=runner_cleanup_recovery commits=N` → запускает merge.
  - Если нет → treat as halt (как раньше).

**Слой C — Pre-spawn worktree refresh (optional, в backlog если время есть):**
- `spawn_worker`: если `<worktree>/feature/<id>` уже существует с `HEAD == base_sha` → emit `verdict=approve` без spawn'а (story already done).
- Иначе → `git worktree remove --force <wt>` перед fresh spawn (managed через `--force-new-worktree` flag, default true для real-mode).

**Tests.**
- `tests/test_runner_stage7_graceful.py` (5 unit): Stage 7 skip когда worktree держит ветку, synthetic verdict при unmerged commits, clean cleanup без conflict, log format, env override `BMAD_RUNNER_SKIP_STAGE7=1`.
- `tests/test_worker_silent_failure_stage7_pattern.py` (4 unit): regex match, synthetic verdict emission, no-commits halt path, integration с event bus.
- `tests/test_stage7_e2e_recovery.py` (2 integration): full pilot mock с reused worktree → integration ветка создана несмотря на runner exit 1.
- **Target:** +11 tests.

**Acceptance.**
- Antares Story 1.3 повторный pilot (worktree уже существует от прошлой попытки) → runner Stage 7 skip, verdict=approve emit, `integration/wave-1a` ветка создана с merged commit.
- `events.jsonl` содержит `RUNNER_CLEANUP_FAILED_REUSED_WORKTREE` если runner всё-таки упал (детект работает).
- `worker_silent_failure` НЕ эмитится когда есть реальная работа на feature branch.

---

## 2. P2 — quality of life

### #3 (NEW-3) `normalize_story_id` для kebab+slug composite

**Зачем.** Log от Antares 1a: `pilot_mark_done_unresolved reason='no matching sprint-status key in any epic block' spawned_id=1.3`. Sprint-status имеет ключ `1-3-fastapi-app-lifespan-health`. `resolve_sprint_status_key` (S1 закрытия v1, commit `7edc9bb`) не покрывает composite key с descriptive suffix — split logic делает только prefix-exact match.

**Что добавляем.**
- `agent/story_id.py::resolve_sprint_status_key`: расширить fuzzy match алгоритм.
  - Существующая логика: dotted (`1.3`) → kebab (`1-3`) → exact lookup.
  - Новая ветка: если exact kebab не найден → split каждый sprint-status key на `^(\d+-\d+)-(.+)$`, match только на dotted-prefix part.
  - Tie-breaking: если несколько keys матчат `1-3-*` → log warning + взять первый (по lexicographic order).

**Tests.** `tests/test_story_id_kebab_slug_composite.py`:
- 3 unit: composite single match, multiple matches (tie-break log), no match fallback.
- 1 integration: full `_run_real_pilot_body` mock с kebab+slug sprint-status → mark-done срабатывает.
- **Target:** +4 tests.

**Acceptance.**
- `resolve_sprint_status_key("1.3", ["1-3-fastapi-app-lifespan-health", "1-4-startup-checks"])` → `"1-3-fastapi-app-lifespan-health"`.
- Pilot с composite keys: sprint-status корректно обновляется → resume не повторяет done-stories.

---

### #4 (NEW-4) `worker_completed` использует inner exit code

**Зачем.** Race: outer `claude -p` exit 0 (dutifully reported inner exit), inner `bmad-auto-dev-runner.sh` exit 1. `worker_spawn._tail_and_emit_completion` смотрит на outer exit → пишет `worker_completed status=success` при настоящем failure. False positives портят analytics, маскируют halts.

**Что добавляем.**
- `runtime/worker_spawn.py::_tail_and_emit_completion`:
  - Перед формированием `worker_completed` payload — scan последние ~50 stdout lines на regex `^Exit code: (\d+)$` или `^❯ Exit code: (\d+)$` (формат `claude -p` обёртки).
  - Если найден inner exit ≠ 0 → override `status="failure"` + добавить `inner_exit_code=N` в payload.
  - Outer exit сохраняем как `outer_exit_code` для diff debugging.
- Регистрация: bash-side runner уже логирует `Exit code: $?` в Stage 7 — фикс на orchestrator-side parsing, без runner-side изменений.

**Tests.** `tests/test_worker_completed_inner_exit.py`:
- 3 unit: inner exit 0 + outer 0 (success), inner exit 1 + outer 0 (failure), no exit code line (current behaviour preserved).
- 1 integration: full subprocess mock с inner/outer mismatch → JSONL содержит `status=failure`.
- **Target:** +4 tests.

**Acceptance.**
- Antares 1a-style mismatch: outer=0, inner=1 → `worker_completed status=failure inner_exit_code=1 outer_exit_code=0`.
- Никаких регрессий на success path (inner=0 → status=success).

---

## 3. Session plan

| Session | Items | Estimated time | Tests delta | Commit count |
|---|---|---|---|---|
| S1 | #1 NEW-1 (P1 project flag) + #3 NEW-3 (P2 resolver) | ~1 сессия | +10 | 2 |
| S2 | #2 NEW-2 Слой A + B (runner-side + detector) | ~1-1.5 сессии | +9 | 2 |
| S3 | #2 NEW-2 Слой C (pre-spawn refresh, optional) + #4 NEW-4 (exit code race) + finalize | ~0.5-1 сессии | +6 | 2 |

**Total estimate:** 2.5-3 сессии. Tests delta: ≥ +25.

**Finalize check (end of S3):**
- `pytest -q` → ≥1960 PASS, mypy/ruff clean.
- `git log integration/pilot-findings-closure-v2 --oneline` → 6 commits (или 5 если #2 слой C deferred).
- Manual smoke: `ORCHESTRATOR_TARGET_PROJECT=/home/server/odyssey virgil run --project antares --wave 1a --real --story 1.3 --dry-run` → лог показывает antares path (не odyssey).
- Tracker `.bmad-runs/pilot_findings_closure_v2/` с per-session updates + final report.

---

## 4. Out of scope (для следующей инициативы)

- **R3 per-turn token snapshot** (research backlog, P2).
- **R4 stale worktree GC** (P2, частично пересекается с Слоем C #2).
- **R5 fail-closed cleanup policy** (P3, guard для R4).
- **`pilot_findings_closure_v2_validation`** — повторный полный прогон Antares Epic 1 после merge этой инициативы. Отдельная mini-session.
- **Phase 5 items** — observability dashboard, TTS notifications, Vision steps 3-7.

---

## 5. References

- Methodology: `spec/methodology-virgil.md` §5 «Backlog — НОВЫЕ pilot findings»
- Predecessor: `spec/spec_pilot_findings_closure.md` (S1..S8, merged `f2ff857`)
- Memory:
  - `project_backlog_target_resolution_bug` (NEW-1)
  - `project_backlog_runner_reused_worktree_cleanup` (NEW-2)
  - `project_backlog_resolver_kebab_slug` (NEW-3)
  - `project_backlog_exit_code_race` (NEW-4)
- Pilot artifact: `/home/server/Antares/.worktrees/wt-1.3` (feature/1.3 +1286 LOC, ждёт recovery от NEW-2 fix)
- Code anchors:
  - `src/bmad_orchestrator/cli/main.py:393-413` (`_resolve_settings_for_project`)
  - `src/bmad_orchestrator/config.py:113-118` (env binding)
  - `agent/skills/bmad-auto-dev/scripts/bmad-auto-dev-runner.sh` Stage 7
  - `src/bmad_orchestrator/runtime/worker_spawn.py` (`_tail_and_emit_completion`)
  - `src/bmad_orchestrator/agent/story_id.py` (`resolve_sprint_status_key`)
