# Initiative Tracker — Parallelism Initiatives (Phase 0 + #1 + #2 + #3 + Phase 4 review)

## Metadata
- **Spec:** spec/spec_parallelism_initiatives.md
- **Parent specs:** spec/spec_canonical_patches_port.md
- **Integration branch:** integration/parallelism_initiatives
- **Created:** 2026-05-18
- **Bootstrap completed:** 2026-05-18 by auto-loop-spec-long manual bootstrap
- **Scope frozen:** 2026-05-18
- **Runtime:** loop_wrapper
- **Delay seconds:** 180
- **Auto merge:** false

## Scope Freeze
### In scope
- Phase 0: zombie cleanup, post-worker validation, cost honesty, pre-spawn freshness, second pilot Antares 1.2
- Initiative #1: Story parallelism MVP (CLI flag, conflict pre-check, cgroup limits, per-worker HOME, validation pilot)
- Initiative #2: Story split + intra-story sequencing (should_split, LLM decomposition, sub-story execution, squash-merge)
- Initiative #3: Multi-project queue (registry, scan/doctor/init/resume CLI, multi-execution, per-project isolation)
- Phase 4: mandatory final review (Opus code-review + code-auditor cross-check + auto-fix P0/P1/High + security-auditor + final report)

### Out of scope (explicit)
- Vision step 6 (reflexion-learner — orchestrator self-modifying skills). Deferred to separate spec.
- Multi-LLM provider routing (Anthropic API key + fallback). Deferred.
- TTS notifications. Deferred.

### Deferred to follow-up initiative
- Intra-story PARALLEL sub-execution (текущий S5 = sequential sub-stories внутри parent worktree). Parallel intra-story = отдельный spec.
- Self-modifying skills loop (после parallelism + multi-project готовы).

## Sessions

### Pending

- **id:** S3
  **title:** Initiative #1B — cgroup limits + per-worker HOME isolation + parallel validation pilot
  **surface:** infra-with-recovery
  **spec_section:** Initiative #1 Task 1.3-1.5
  **depends_on:** [S2]
  **acceptance:**
    - cgroup `MemoryMax=8G CPUQuota=200%` applied per worker
    - Per-worker HOME isolated (no shared ~/.claude/ writes)
    - Antares stories 1.3+1.5 параллельно ~30 мин wall-clock
  **safety_gates:**
    - L1: sandbox modifications need rollback script; L2: validation pilot must merge cleanly
  **checkpoint:** true
  **estimated_retries_allowed:** 3

- **id:** S4
  **title:** Initiative #2A — should_split heuristic + story-splitter skill scaffold + LLM decomposition
  **surface:** backend-python
  **spec_section:** Initiative #2 Task 2.1-2.2
  **depends_on:** [S3]
  **acceptance:**
    - Tests на should_split heuristic edge cases
    - Sample decomposition output validates JSON schema
  **safety_gates:**
    - L1: no destructive; L2: ruff+mypy+tests
  **checkpoint:** false
  **estimated_retries_allowed:** 3

- **id:** S5
  **title:** Initiative #2B — sub-story execution + squash-merge back to parent
  **surface:** backend-python
  **spec_section:** Initiative #2 Task 2.3-2.4
  **depends_on:** [S4]
  **acceptance:**
    - Integration test с mock sub-stories
    - Squash-merge produces single parent commit
  **safety_gates:**
    - L1/L2/L3 standard
  **checkpoint:** false
  **estimated_retries_allowed:** 3

- **id:** S6
  **title:** Initiative #2C — validation pilot Antares Story 3.1 (Nextcloud Docker template) auto-split
  **surface:** backend-python
  **spec_section:** Initiative #2 Task 2.5
  **depends_on:** [S5]
  **acceptance:**
    - Story 3.1 split на ≥3 sub-stories
    - Speedup ≥2× vs sequential baseline
    - sprint-status 3.1=done
  **safety_gates:**
    - L1/L2/L3 standard
  **checkpoint:** true
  **estimated_retries_allowed:** 3

- **id:** S7
  **title:** Initiative #3A — project registry yaml + CLI scan/doctor/init/resume
  **surface:** backend-python
  **spec_section:** Initiative #3 Task 3.1-3.2
  **depends_on:** [S6]
  **acceptance:**
    - `bmad-orchestrator init /path` создаёт config
    - `scan` показывает все known projects
  **safety_gates:**
    - L1/L2/L3 standard
  **checkpoint:** false
  **estimated_retries_allowed:** 3

- **id:** S8
  **title:** Initiative #3B — multi-project execution + per-project state isolation
  **surface:** backend-python
  **spec_section:** Initiative #3 Task 3.3-3.4
  **depends_on:** [S7]
  **acceptance:**
    - Tests: shared budget guard + isolated sprint-status updates
    - No memory cross-pollination
  **safety_gates:**
    - L1: prod CRM mounts NEVER in worker bind list
    - L2/L3 standard
  **checkpoint:** false
  **estimated_retries_allowed:** 3

- **id:** S9
  **title:** Initiative #3C — validation pilot (Antares wave + Odyssey wave parallel)
  **surface:** backend-python
  **spec_section:** Initiative #3 Task 3.5
  **depends_on:** [S8]
  **acceptance:**
    - Оба waves complete
    - sprint-status каждого проекта корректен
    - No state leak в memory/.claude/
  **safety_gates:**
    - L1/L2/L3 standard
  **checkpoint:** true
  **estimated_retries_allowed:** 3

- **id:** S10
  **title:** Phase 4A — mandatory full code review (Opus + code-auditor cross-check)
  **surface:** backend-python
  **spec_section:** Phase 4 Task 4.1
  **depends_on:** [S9]
  **acceptance:**
    - Оба reviewer'а спавнятся, выдают findings list с severity tags
    - Report written в `.claude/checkpoints/parallelism_initiatives-review-S10.md`
  **safety_gates:**
    - L1/L2/L3 standard (read-only review)
  **checkpoint:** false
  **estimated_retries_allowed:** 3

- **id:** S11
  **title:** Phase 4B — auto-fix P0/P1/High findings + re-review + security-auditor + final report
  **surface:** backend-python
  **spec_section:** Phase 4 Task 4.2-4.5
  **depends_on:** [S10]
  **acceptance:**
    - Auto-fix всех P0/P1/High findings (max 2 retry); each fix commit "fix(<scope>): address review finding"
    - Re-review verdict PASS от обоих reviewer'ов
    - security-auditor pass: no новых vulnerabilities
    - pytest 1162+ PASS, ruff 0 errors, mypy 0 errors
    - Final Report written в tracker с merge hint
  **safety_gates:**
    - L1: never `--no-verify`, never force-push
    - L2/L3 standard
  **checkpoint:** true
  **estimated_retries_allowed:** 3

### Current

- **id:** S2
  **title:** Initiative #1A — CLI --parallel flag + presets + file-conflict pre-check
  **surface:** backend-python
  **spec_section:** Initiative #1 Task 1.1-1.2
  **depends_on:** [S1]
  **acceptance:**
    - `--parallel 3` спавнит 3 worker'а параллельно
    - File-conflict detector splits batch при перекрытии touches_files
    - Tests pass
  **safety_gates:**
    - L1: no destructive ops; L2: ruff+mypy must pass; L3: 1184+ tests
  **checkpoint:** false
  **estimated_retries_allowed:** 3
  **started:** (pending first wake on S2)
  **workflow:** workflows/backend-python.md
  **retry_count:** 0
  **worker_branches:** []

### Completed

- **id:** S1
  **title:** Phase 0 — pilot followups (zombie cleanup, post-worker validation, cost honesty, pre-spawn freshness)
  **completed:** 2026-05-18 UTC
  **commit:** 9897124
  **files_changed:** 5+ (zombie cleanup, silent-failure detector, cost tracker, freshness check, 22 new tests)
  **tests_passed:** 1184/1184 PASS (was 1162); ruff PASS; mypy PASS
  **decisions_made:**
    - Cost tracker subscription mode → emit `cost_tracking_unavailable` instead of `$0.00` (honest reporting)
    - Zombie cleanup pre-pilot: `pkill -9 -f "bmad-orchestrator run --project <name>"` with same-project narrowing
    - Silent-failure detection: exit_code=0 + zero commits → SILENT_FAILURE event + halt-equivalent
    - Pre-spawn freshness: warn on dirty worktree (untracked files that might conflict with dev-story output)
  **deferred_items:**
    - Task 0.5 (Antares Story 1.2 pilot) skipped — see Decisions Log entry "Skip 0.5 — story file unavailable + redundant with S2-S11 natural pipeline exercise". Pipeline validated by pilot v7 Story 1.1 (Stage 1-5 confirmed) + 22 new unit tests covering 0.1-0.4 surfaces.

## Safety Gates Triggered

(empty)

## Blockers / Pauses

- **[2026-05-18 UTC] pilot_validation_pending — Antares Story 1.2 real pilot requires manual user run**
  Reason: Acceptance 0.5 requires `sprint-status 1.2=done` after a ~30-min real pilot. Running the pilot nested inside this headless `claude -p` wake collides with the new `_kill_stale_orchestrators` logic (S1's own 0.1 fix), which SIGKILLs any matching `bmad-orchestrator run --project antares` it sees — including a pilot spawned by the autoloop itself. The cleanest separation is to defer the runtime validation to a regular shell session under user supervision.

  **User action required (one of):**

  Option A — supervised tmux run (recommended):
  ```bash
  tmux new -s antares-1.2
  cd /home/server/bmad-orchestrator
  ORCHESTRATOR_TARGET_PROJECT=/home/server/Antares \
  BMAD_REQUIRE_SANDBOX=1 BMAD_DISABLE_BUDGET=1 \
  venv/bin/bmad-orchestrator run --project antares --wave smoke --real \
      --story 1-2-docker-compose-dev-stack \
      --max-stories 1 --max-spend-usd 999999
  # detach: Ctrl-b d ; reattach: tmux a -t antares-1.2
  # ~30 min; check sprint-status when done:
  grep -E '1[._-]2' /home/server/Antares/_bmad-output/sprint-status.md
  ```

  Option B — skip 0.5 (accept the Phase 0 code fixes without runtime validation): mark this blocker `resolved_skipped` and let autoloop promote S1 → Completed. Risk: zombie cleanup + silent-failure detection are wired but not re-validated end-to-end against a real worker. Code is covered by 22 new unit tests + full 1184-test suite.

  Option C — re-run pilot later (after another initiative session): same effect as A but unblocks autoloop now via `resolved_deferred`.

  **resolution:** resolved_skipped 2026-05-18 — Option B per S1 worker proposal. Rationale: Story 1.2 file does not exist in Antares (only 1.1 + 4.8 prepared); creating it would require its own Stage 4 run, defeating the "second pilot" purpose. Pipeline already validated by pilot v7 Story 1.1 (Stage 1-5 confirmed end-to-end) + 22 new unit tests covering 0.1-0.4 code surfaces. Natural pipeline exercise resumes in S2-S11. Autoloop resumed on S2.

## Decisions Log

- **date:** 2026-05-18T05:00 UTC
  **session:** bootstrap
  **decision:** Single spec covering Phase 0 + 3 initiatives + Phase 4 (vs 4 separate specs or pipeline)
  **rationale:** user preference — one command, autonomous chain. Per-initiative isolation traded for simplicity. Checkpoint sessions (S3, S6, S9, S11) provide intermediate review surfaces.
  **impact:** all 11 sessions on one integration branch; final manual merge only after Phase 4 PASS.

- **date:** 2026-05-18T05:00 UTC
  **session:** bootstrap
  **decision:** Runtime=loop_wrapper (fresh context per session), Delay=180s, Auto merge=false (manual merge after Phase 4)
  **rationale:** 11 sessions too long for shared context (`/auto-loop-spec-short`); 180s pause keeps wall-clock manageable while allowing zombie cleanup; manual merge per `/auto-loop-spec-long` default for long initiatives.
  **impact:** user runs one tmux command, walks away ~10-15h; reviews integration branch + manually merges to main at end.

- **date:** 2026-05-18T05:00 UTC
  **session:** bootstrap
  **decision:** Phase 4 = mandatory + auto-fix P0/P1/High + 2 retry max
  **rationale:** user explicitly requested code review + auditor + auto-acceptance of fixes. Severity threshold = High prevents auto-fix on noisy nitpicks. 2 retry cap prevents infinite loop on stubborn findings.
  **impact:** initiative cannot complete without PASS verdict from both reviewers; S11 may halt + escalate user if 2 retry exhausted.

- **date:** 2026-05-18 UTC
  **session:** S1
  **decision:** Defer 0.5 pilot validation to manual user run via PENDING Blockers entry
  **rationale:** S1's own 0.1 zombie cleanup (`_kill_stale_orchestrators` matches `bmad-orchestrator run --project antares`) would SIGKILL a pilot spawned nested from inside the autoloop's `claude -p` wake. Either disable the cleanup for self-spawned pilots (extra complexity, breaks the spec) or run the pilot outside the autoloop (cleaner). Chose the latter.
  **impact:** Autoloop wrapper stops (PENDING resolution). User resumes by running pilot in tmux (Option A) or marking blocker resolved_skipped/deferred (Options B/C).

- **date:** 2026-05-18 UTC
  **session:** human-resolved
  **decision:** Skip 0.5 — Story 1.2 file unavailable + redundant with S2-S11 natural pipeline exercise
  **rationale:** Attempted pilot 1.2 from supervised shell session failed with `RuntimeError: --story '1-2-docker-compose-dev-stack' not found in target project's stories dir`. Antares только имеет 1.1 + 4.8 story files. Создавать 1.2 файл через Stage 4 чтобы потом запустить полный pilot на нём = busy work, не valid validation. Pipeline END-TO-END уже validated в pilot v7 Story 1.1 (Stage 1-5 confirmed, manual close demonstrated integrity). Phase 0 code fixes 0.1-0.4 покрыты 22 новыми unit-тестами + полным 1184-test suite. Natural validation pipeline exercise resumes в S2-S11 (каждая сессия задействует pipeline).
  **impact:** Blocker marked resolved_skipped → autoloop resumes на S2 без human-in-loop. Если в S2-S11 pipeline reveal'ит регрессию в Phase 0 surfaces — будет caught там, не до.

## Journal

```
[2026-05-18 05:00 UTC] bootstrap: tracker + integration/parallelism_initiatives + backup/parallelism_initiatives-pre-2026-05-18 + launcher + watchdog created. 11 sessions planned. S1=Current. delay=180s, runtime=loop_wrapper, auto_merge=false.
[2026-05-18 UTC] S1 code work done: 0.1 zombie cleanup + 0.2 silent-failure + 0.3 cost honesty + 0.4 freshness check committed as 9897124 on integration/parallelism_initiatives. 22 new tests in tests/test_phase0_pilot_followups.py; 1184/1184 PASS, ruff 0, mypy 0 new. 0.5 pilot validation deferred — see Blockers/Pauses (PENDING).
[2026-05-18 UTC] human-resolved: 0.5 PENDING blocker resolved_skipped (Option B). Story 1.2 file unavailable in Antares; redundant with S2-S11 natural pipeline exercise. S1 → Completed, S2 promoted → Current. Autoloop resumes.
```

## Final Report (populated on last session completion)

(empty — pending S11 completion)
