# Phase 4A — Mandatory Code Review (S10)

**Date:** 2026-05-18 UTC
**Branch:** integration/parallelism_initiatives
**Diff scope:** main..HEAD — 67 files, +14553 / -353 LOC
**Reviewers:**
- `code-reviewer` subagent (Opus 4.7)
- `code-auditor` subagent (Opus 4.7)
**Method:** read + reason. pytest/ruff/mypy NOT re-run (tracker pins 1386/1386 PASS, ruff 0, mypy 6 untyped-closure notes).

---

## Consolidated verdict — **FAIL** (both reviewers)

Block manual merge to main until S11 (Phase 4B) auto-fixes all P0/P1/High findings + re-review passes.

### Why FAIL even with P0=0

- The headline Init #3 deliverable (shared aggregate budget across multi-project waves) is **structurally inert in production** — `_subprocess_runner` never feeds spend back to `SharedSpendTracker`, so the cap never trips. Tests pass because stubs do call `tracker.add()`; production code does not. Both reviewers found this independently.
- Three operational hazards in the real `_subprocess_runner` (no timeout, unbounded stdout PIPE, env leak) make `multi --real` unsafe to run unattended even once the budget gap is fixed.
- One git-history corruption hazard (auto-split fallback inherits a dirty branch) can poison the integration history in a way the merge-gate may rubber-stamp.

---

## Aggregated severity stats

| Severity | code-reviewer | code-auditor | combined unique |
|---|---|---|---|
| P0 | 0 | 0 | 0 |
| P1 | 4 | 4 | 6 (2 strong-overlap) |
| High | 4 | 4 | 6 (2 strong-overlap) |
| Medium | 6 | 6 | 11 (1 overlap) |
| Low | 3 | 6 | 8 (1 overlap) |

---

## P1 — must fix in S11 before manual merge

### P1-A. SharedSpendTracker never receives production spend (both reviewers)
- **file** — `src/bmad_orchestrator/cli/main.py:565-608` (`_subprocess_runner`)
- **category** — correctness / safety-invariant
- **summary** — Real runner returns `ProjectRunResult(spent_usd=0.0)` and never calls `await tracker.add(...)`; only test stubs do, so shared budget halt is inert in production.
- **detail** — Shared `BudgetGuard` + `SharedSpendTracker` (S8) is the entire mechanism that fulfills Init #3 §3.3 "shared budget guard halts second project at aggregate cap". With this gap, a 10-project plan with $50 shared cap allows up to $500 real spend before any guard fires; only the per-child `--max-spend-usd = daily/N` local soft caps apply.
- **fix_hint** — Have child write `spend.json` next to sprint-status (more robust than stdout-parsing since stdout is currently discarded); parent reads on `proc.communicate()` completion → `await tracker.add(spent)` → populate `ProjectRunResult.spent_usd`. Add regression test where stub child reports real spend and asserts second project halts.

### P1-B. `_subprocess_runner` has no timeout (auditor P1-2)
- **file** — `src/bmad_orchestrator/cli/main.py:593-600`
- **category** — resource-leak / liveness
- **summary** — `await proc.communicate()` has no timeout; a hung `claude -p` child (auth prompt, network deadlock, runaway sub-agent loop) parks the entire multi-run forever.
- **detail** — `asyncio.gather` waits for all `_wrap` coroutines; one hung child blocks the entire `MultiProjectOutcome`. Already-paid spend is invisible. Child's internal per-worker timeouts don't help if the child orchestrator itself deadlocks.
- **fix_hint** — `await asyncio.wait_for(proc.communicate(), timeout=plan.per_project_timeout_sec)` (default ~4h). On `TimeoutError`: `proc.terminate()` → 30s grace → `proc.kill()` → return `ProjectRunResult(completed=False, error="timeout after Ns")`. Add `per_project_timeout_sec` to `MultiProjectPlan` with a configurable ceiling.

### P1-C. `_subprocess_runner` stdout PIPE causes OOM on long waves (reviewer #4 + auditor P1-3)
- **file** — `src/bmad_orchestrator/cli/main.py:597-600`
- **category** — resource-leak / observability
- **summary** — `stdout=PIPE` + `proc.communicate()` buffers entire child stdout in parent RAM (then discards into `_`). A 4h wave producing megabytes of structured logs × N projects can OOM the orchestrator host before completion. Operator also loses all per-story progress telemetry.
- **fix_hint** — Either `stdout=DEVNULL` (parent doesn't read it), or stream line-by-line to `<orchestrator_home>/_logs/multi-<wave>/<slug>.log` and tee a summary to the parent's event bus. Cap stderr too (`await proc.stderr.read(64_000)` truncate — only first 400 bytes surface anyway).

### P1-D. Subprocess runner leaks full `os.environ` to child workers (reviewer P1#1)
- **file** — `src/bmad_orchestrator/cli/main.py:581`
- **category** — security / safety-invariant
- **summary** — `env = dict(os.environ)` propagates orchestrator-internal env (`BMAD_DISABLE_BUDGET`, `BMAD_PROJECTS_REGISTRY`, `BMAD_AUTO_SPLIT`, `BMAD_REQUIRE_CGROUP`, `ANTHROPIC_API_KEY`, host `HOME`) verbatim to every child.
- **detail** — Test fixture that flipped `BMAD_DISABLE_BUDGET=1` in parent shell, or operator who set `BMAD_PROJECTS_REGISTRY=/tmp/test.yaml`, silently propagates to every per-project child — disabling the very gates `multi` was added to enforce. Sandbox module already maintains `_SANDBOX_DEFAULT_ENV_ALLOWLIST`; subprocess path should mirror that discipline.
- **fix_hint** — Build child env from allow-list (`PATH`, `HOME`, `USER`, `LANG`, `LC_ALL`, `TZ`, `TERM`, `SHELL`, `ANTHROPIC_API_KEY` only if forwarded), then explicitly set `ORCHESTRATOR_TARGET_PROJECT` + any plan flags. Add test that asserts `BMAD_DISABLE_BUDGET` from parent never reaches child.

### P1-E. `validate_project_isolation` only enforced in `run_multi`; `register_project` + single-project `run` bypass it (reviewer P1#3)
- **file** — `src/bmad_orchestrator/runtime/project_registry.py:174` + `src/bmad_orchestrator/runtime/multi_run.py:259`
- **category** — safety-invariant
- **summary** — L1 forbidden-path gate (prod CRM mount) is enforced inside `run_multi` only. `bmad-orchestrator init /home/server/crm` and `bmad-orchestrator run --project <slug-pointing-at-crm>` both succeed today.
- **detail** — Spec §Safety gates §1.3 says prod CRM must never appear in worker bind list. Today: (a) operator can register `/home/server/crm` into registry via `init`, (b) single-project `run` does not call `validate_project_isolation` (grep confirms). Defence-in-depth promise broken — gate sits at wrong layer.
- **fix_hint** — Call `validate_project_isolation([slot_from(entry.path)])` at top of `register_project` (best — reject at insertion) AND at top of real single-project pilot in `agent/run.py` so an already-poisoned registry from prior version still cannot spawn a worker.

### P1-F. Auto-split fallback inherits a dirty branch (auditor P1-4)
- **file** — `src/bmad_orchestrator/agent/run.py:1037-1083`
- **category** — state-corruption (git history)
- **summary** — When `auto_split_and_execute` raises mid-flight after committing K of N sub-stories, broad `except Exception` falls through to legacy `runtime_spawn_worker(... branch=branch_name ...)`. Legacy worker spawns on branch already containing partial sub-story commits → frankencommit history.
- **detail** — `auto_split_and_execute` runs `execute_sub_stories` which commits each successful sub-story to `branch_name`. If sub-story 3 of 4 fails or squash fails, branch holds 2-3 commits. Exception bubbles to `run.py:1051`, `auto_split_outcome` is set to `None`, control falls into legacy spawn block (no `git reset --hard <base_sha>`, no branch reset, no worktree cleanup). Legacy worker writes its own commits on top — merge-gate sees mixed history no reviewer expects.
- **fix_hint** — In `except` block, before falling through, run `git -C <worktree> reset --hard <base_sha>` to restore branch tip to original DAG batch base. Or safer — skip legacy fallback entirely and emit `WORKER_COMPLETED` with `status="failed"` so planner re-batches parent story next round.

---

## High — fix in S11 (auto-fix included per spec)

### H-1. Sandbox `--ro-bind / /` exposes all host data for read (auditor)
- **file** — `src/bmad_orchestrator/runtime/sandbox.py:290`
- **category** — sandbox-escape / defence-in-depth
- **summary** — Worker bwrap config binds entire host root read-only. Compromised worker can read `/home/server/crm/`, `/etc/shadow`, other users' homes, `/home/server/.claude*` auth tokens — anything host UID can read.
- **detail** — `FORBIDDEN_PROJECT_PATHS` only prevents prod CRM appearing as WRITE bind. Read-everywhere mount means worker can `cat /home/server/crm/.env` and exfil customer DB creds. New auto-split machinery allows decomposer prompts to influence sub-story content; a poisoned story description could ask sub-worker to "read host crm/.env for context".
- **fix_hint** — Short-term: add `--ro-bind /dev/null /home/server/crm` (and other sensitive host paths) as explicit blackouts in `wrap_command`. Long-term: switch from `--ro-bind / /` to explicit per-allowed-root binds skipping sensitive paths.

### H-2. Auth-token snapshots persist in `/tmp` after crash (auditor)
- **file** — `src/bmad_orchestrator/runtime/worker_spawn.py:150-210` + `213-229`
- **category** — secret-leak / disk hygiene
- **summary** — `_create_isolated_home` copies `~/.claude.json` (live OAuth tokens) into `/tmp/bmad-worker-<label>-XXXX/`. SIGKILL/OOM on parent skips `_cleanup_isolated_home`; snapshot sits in /tmp until reboot.
- **fix_hint** — Add startup cleanup pass in `run.py::_run_real_pilot` that rms any `/tmp/bmad-worker-*` directories older than 1h owned by current UID.

### H-3. `_kill_stale_orchestrators` substring-match kills wrong-project siblings (auditor)
- **file** — `src/bmad_orchestrator/agent/run.py:700`
- **category** — destructive-action-misfire
- **summary** — `pgrep -f "bmad-orchestrator run --project odyssey"` also matches `odyssey-staging` and `odyssey-prod`. Staging cleanup SIGKILLs production.
- **detail** — With new Init #3 multi-project registry, slugs like `odyssey` and `odyssey-staging` will coexist on one host. Exclude list only covers `os.getpid()` + `os.getppid()` — siblings are fair game.
- **fix_hint** — After `pgrep -f`, read each matched pid's `/proc/<pid>/cmdline`, split on `\0`, assert `--project` is followed *exactly* by `project` (not prefix). Or switch to project-rooted lockfile (`<orchestrator_home>/runs/<slug>.pid`) with `fcntl.flock`.

### H-4. `validate_decomposition` does not check `touches_files` disjointness (reviewer H#5 + auditor H-4)
- **file** — `src/bmad_orchestrator/runtime/story_splitter.py:149-201`
- **category** — correctness / merge-conflict
- **summary** — DECOMPOSITION_PROMPT promises disjoint File Lists per sub-story; validator never reads `touches_files` at all. Misbehaving LLM returns two sub-stories both touching `src/foo.py` → passes validation → squash-merge produces conflict.
- **detail** — Whole point of structured splitting (vs simple chunking) is to guarantee disjoint write sets. `file_conflict.split_batch` pre-check operates on batch-level stories, not on intra-decomposition sub-stories.
- **fix_hint** — In `validate_decomposition`, for each sub-story extract `touches_files`, compute pairwise intersection, raise `DecompositionError(f"sub_stories {a!r} and {b!r} both touch {file}")` on overlap. Require union to be subset of parent's declared File List.

### H-5. `validate_decomposition` does not enforce AC ≤ 5 cap stated in prompt (reviewer H#6)
- **file** — `src/bmad_orchestrator/runtime/story_splitter.py:149`
- **category** — correctness
- **summary** — Prompt says "AC per sub-story <= 5" but validator never reads `ac`. Sub-story with 12 ACs defeats splitting purpose (still too large for worker context window).
- **fix_hint** — Add `ac = item.get("ac", []); if not isinstance(ac, list) or len(ac) > 5: raise DecompositionError(...)` next to existing per-item checks.

### H-6. `find_conflicts` silently skips stories without `id` (reviewer H#8)
- **file** — `src/bmad_orchestrator/agent/file_conflict.py:53`
- **category** — correctness / fail-loud-violation
- **summary** — `if not sid: continue` swallows malformed input — story with no id (or empty id) dropped from conflict analysis with zero log.
- **detail** — A planner emitting `{"id": null, "touches_files": [...]}` leads runtime to think batch is conflict-free and spawn N writers on same file. Should be fail-loud, not graceful degradation.
- **fix_hint** — Raise `ValueError(f"story missing id: {s!r}")` instead of silent continue.

---

## Medium — batch into separate follow-up (not S11 auto-fix scope per spec — "High and above")

### M-1. Reviewer M#1 — `split_batch` lets stories with empty `touches_files` over-fill parallel slots
- file: `src/bmad_orchestrator/agent/file_conflict.py:65`
- summary: empty touches_files treated as "claims nothing" but usually means metadata missing → two such stories spawn in parallel and may collide.
- fix_hint: require non-empty or treat as "claims everything → defer all but one".

### M-2. Reviewer M#2 — `detect_bmad_layout` order-dependent (bmm-v6 wins over odyssey-hybrid)
- file: `src/bmad_orchestrator/runtime/project_registry.py:148`
- summary: project with both layout markers silently classified as bmm-v6; migration projects get wrong layout → reads stale planning artifacts.
- fix_hint: raise `ProjectRegistryError("ambiguous layout: both bmm-v6 and odyssey-hybrid markers present, pin explicitly")`.

### M-3. Reviewer M#3 — `memory_path` slug validation weaker than registry regex
- file: `src/bmad_orchestrator/runtime/project_memory.py:85`
- summary: registry enforces `^[a-z0-9][a-z0-9_-]{0,63}$`; memory_path only rejects `/ \ . ..`. Programmatic `ProjectMemory(project_slug="My Project!")` slips through.
- fix_hint: extract `_SLUG_RE` to shared `runtime/_slug.py`, apply in both places.

### M-4. Reviewer M#4 — `_subprocess_runner` splits daily cap evenly, ignoring shared tracker
- file: `src/bmad_orchestrator/cli/main.py:589`
- summary: static $25/$25 split halts expensive project at $25 even though shared cap $50 is fine. Defeats "shared budget" premise.
- fix_hint: pass full `daily_max_spend_usd` to each child and rely on shared tracker (after P1-A fixed); per-child cap stays only as small safety stop.

### M-5. Reviewer M#5 — prlimit still primary defence; cgroup migration not complete
- file: `src/bmad_orchestrator/runtime/sandbox.py:45`
- summary: per-UID `RLIMIT_NPROC` brittle on busy hosts; `BMAD_REQUIRE_CGROUP` is opt-in. If systemd-run unavailable, silent fallback to broken prlimit.
- fix_hint: either complete migration (cgroup primary, prlimit removed) or document wave-1a pilot requires `BMAD_REQUIRE_CGROUP=1` and refuse start without it.

### M-6. Reviewer M#6 — Auto-split synthetic `WORKER_COMPLETED` emits `jsonl=""`
- file: `src/bmad_orchestrator/agent/run.py:1068`
- summary: downstream subscribers (code-review, security-review) consume `jsonl` as path; empty string breaks any `Path(jsonl).read_text()`.
- fix_hint: audit all `WORKER_COMPLETED` subscribers; add explicit `auto_split=True` branch OR synthesise combined JSONL from sub-story runs.

### M-7. Auditor M-1 — SIGINT does not cleanly tear down `_subprocess_runner` children
- file: `src/bmad_orchestrator/cli/main.py:646-651`
- fix_hint: wrap `asyncio.run` in `try/except KeyboardInterrupt` that `proc.terminate()`s active children with timeout.

### M-8. Auditor M-2 — `BMAD_PROJECTS_REGISTRY` env override has no path-traversal guard
- file: `src/bmad_orchestrator/runtime/project_registry.py:97-100`
- fix_hint: assert resolved path `is_relative_to(Path.home())` or allowlist.

### M-9. Auditor M-3 — `path.with_suffix(suffix + ".tmp")` edge case
- file: `src/bmad_orchestrator/runtime/project_registry.py:128`
- fix_hint: `tmp = path.parent / (path.name + ".tmp")` — clearer and handles `.hidden.yaml` style filenames.

### M-10. Auditor M-4 — `run_multi` pre-flight has TOCTOU window
- file: `src/bmad_orchestrator/runtime/multi_run.py:266-283`
- fix_hint: each runner calls `await tracker.check_only()` at start; refuse spawn if level=="halt". Cheap, idempotent.

### M-11. Auditor M-5 — `auto_split_outcome.succeeded` swallows partial-failure semantics
- file: `src/bmad_orchestrator/agent/run.py:1058-1083`
- fix_hint: emit `EventType.AUTO_SPLIT_PARTIAL` with list of sub-stories that did land, so policy engine can decide to keep them.

### M-12. Auditor M-6 — `make_bus_bridge` task lifecycle in `auto_split.py`
- file: `src/bmad_orchestrator/runtime/auto_split.py`
- summary: `pending: set[asyncio.Task]` strong-ref pattern fixes RUF006 but set never drained; long wave with thousands of bus events accumulates completed-but-referenced Tasks.
- fix_hint: in task `done_callback`, `pending.discard(task)`.

### High — also reviewer flagged (test-quality)
### H-7 (reviewer #7). No test exercises symlink resolution in `validate_project_isolation`
- file: `tests/test_initiative3b_multi_run.py:174`
- fix_hint: add `test_symlink_into_forbidden_rejected` using `tmp_path / "mirror"` + `symlink_to("/home/server/crm")`; skip if path doesn't exist.

---

## Low / Info — defer to backlog

- Reviewer L#1 — `auto_split.auto_split_and_execute` uses `Any` for spawn_fn/wait_fn (fix: import `SpawnFn`/`WaitFn`).
- Reviewer L#2 — `DECOMPOSITION_PROMPT` mentions `splittable: false` but `evaluate_split` never reads it (fix: gate-first in evaluate_split).
- Reviewer L#3 — `_subprocess_runner` discards return distinction for SIGKILL/OOM vs graceful failure (fix: surface `signal_name` in `error` when `returncode < 0`).
- Auditor L-1 — `runtime/event_loop.py:213` `with suppress(asyncio.CancelledError, BaseException)` too broad (pre-existing).
- Auditor L-2 — `runtime/project_memory.py` `fcntl.flock + os.replace` correct for single-host; not for NFS-shared (doc only).
- Auditor L-3 — `FORBIDDEN_PROJECT_PATHS` hardcoded to one path; should be loadable from `<orchestrator_home>/config/forbidden_paths.yaml`.
- Auditor L-4 — sandbox `/proc/version` intentionally exposed for bun runtime; worth docstring why.
- Auditor L-5 — Auto-split `MIN_SUBS=2` rejects 1-sub decompositions with `DecompositionError`; consider treating `len == 1` as no-op (run as legacy) not error.
- Auditor L-6 — `cli/main.py` daily cap division uses `max(len(plan.projects), 1)` but `__post_init__` already rejects empty.

---

## Architectural notes (not auto-fix in S11; worth raising)

1. **`_subprocess_runner` is an architectural smell** — spawning a full Python interpreter + bmad-orchestrator import per project just to share a budget guard is heavyweight. In-process `gather` over `run_real_pilot(project=slot)` eliminates P1-A entirely. Current design exists because per-project state was easier to isolate via separate processes, but `ORCHESTRATOR_TARGET_PROJECT` env passthrough proves you can isolate inside one process too.
2. **Auto-split + multi-project + per-worker HOME overlay = three concurrent isolation mechanisms** with no integration test exercising all three together. 1386 tests but each initiative has its own test file with stub injection. Recommend end-to-end test where multi-run drives N projects, each auto-splits a parent into K sub-stories, each in per-worker HOME overlay + cgroup scope.
3. **`set_decomposer(None)` default in `auto_split.py` is global mutable** — not thread-local. Two pytest sessions in parallel would see each other's decomposer. Tests appear sequential so this doesn't manifest; future `pytest-xdist` migration would surface it.
4. **Documentation reality check** — tracker S8 `decisions_made` claims "shared budget guard halts second project at aggregate cap". Technically true ONLY IF runner feeds spend back. With P1-A unfixed, that claim is false in production. Update tracker S8 entry to flag production gap, or fix P1-A before merging.

---

## S11 (Phase 4B) auto-fix scope

Per spec §Phase 4 Task 4.2-4.5 + tracker S11 acceptance:
- **Must fix:** all P0/P1/High (12 findings: 6 P1 + 6 High + H-7 test-quality)
- **Max 2 retry per finding**
- **Each fix commit:** `fix(<scope>): address review finding <id>`
- **Re-review:** spawn both reviewers again, verdict must be PASS
- **security-auditor pass:** no new vulnerabilities
- **pytest 1386+ PASS, ruff 0, mypy 0 new errors**
- **Final Report written in tracker with merge hint**

If 2-retry exhausted on any finding → STOP loop + escalate user per spec.

---

## Raw agent outputs

- **code-reviewer agent id:** `a6303fe4d5fb8c8a2` (53,886 tokens / 411s / 50 tool uses)
- **code-auditor agent id:** `a862b3f41a03e9a90` (50,064 tokens / 511s / 53 tool uses)
- Both verdicts: **FAIL**.
- Strong agreement on 2 P1 findings (shared budget never enforced; stdout PIPE OOM) + 1 High (validate_decomposition disjointness gap) — high confidence those are real.
