# Embedded skills + self-learning — architecture

> bmad-orchestrator's canonical phase 4+5 BMad skills live inside the
> orchestrator repo (`skills/upstream/`). Every spawned worker receives the
> same canonical version overlaid into its worktree at
> `.claude/skills/`. Self-learning runs on top: live tuning of code-review
> thresholds, per-project memory priming `BudgetGuard`, and retrospective
> lessons that flow back into the policy YAMLs after operator review.
>
> Spec: `spec/spec_embed_phase45_with_selflearning.md`.
> Vision: `~/.claude/projects/-home-server-bmad-orchestrator/memory/project_vision_master_bmad_builder.md`
> (step 2 of 7).

## Design rationale

Phase 4 (Implementation) and phase 5 (Adjacent — code-review, retrospective)
were previously consumed from per-project `.claude/skills/` directories, so
every target project shipped a private copy of `bmad-auto-dev`,
`bmad-code-review`, etc. That had three failure modes:

1. **Drift.** Two projects in the same org could pin different SKILL.md
   bodies. A bug fixed in Odyssey did not land in CRM.
2. **Project bootstrap cost.** A new target project required hand-copying
   ≥14 skill directories from a known-good source.
3. **No upgrade path.** Pulling upstream changes from BMad maintainers
   required N manual copies + N hand-merges per customisation.

The fix is to make the orchestrator the single source of truth for
phase 4+5 skills. Workers receive a **copy** of those skills in their
own worktree on spawn, not a symlink — the worker process operates inside
its sandbox and the symlink target would leak out. The overlay machinery
in `runtime/embedded_skills.py` also applies per-skill customisations
(`skills/customize/<name>.customize.toml`) so org-specific tweaks live
beside the upstream copy.

The 14 canonical skills are listed in
`src/bmad_orchestrator/skills_repo.py::EMBEDDED_SKILL_NAMES`. The full
set covers story creation, advanced elicitation, dev-story execution,
checkpoint preview, code-review, retrospective, and the three adversarial
review variants.

## Component layout

```
skills/
├── upstream/                 ← canonical SKILL.md bodies (read-only)
│   ├── bmad-auto-dev/
│   ├── bmad-code-review/
│   └── ...
├── customize/                ← per-skill TOML overlays (operator-edited)
│   ├── bmad-auto-dev.customize.toml
│   └── ...
├── policy/                   ← live policy YAMLs (live-tuned + lesson-applied)
│   ├── code-review-gates.yaml
│   ├── cost-tuning.yaml
│   └── retry-policy.yaml
├── lessons/                  ← retrospective output (markdown)
│   └── <project>/wave-<N>.md
└── patches/                  ← upstream-drift patches reapplied on skill-update
```

`_config/projects/<slug>/` (under `orchestrator_home`) holds per-project
runtime state:

- `memory.yaml` — rolling windows + aggregates persisted across orchestrator
  process restarts.
- `policy-proposals.yaml` — operator review artefact written by
  `bmad-orchestrator policy-apply` before any policy YAML is touched.

## Upgrade flow

When BMad maintainers ship an upstream change:

1. `bmad-orchestrator skill-update <source>` pulls the new `SKILL.md`
   files into `skills/upstream/`.
2. Patches in `skills/patches/<name>.patch` are reapplied against the
   refreshed upstream; conflicts produce a report instead of a hard fail
   so the operator can decide whether to refresh the patch or drop it.
3. Customisations in `skills/customize/` are unaffected — they overlay
   the (possibly new) upstream body at worker-spawn time.
4. `_config/projects/<slug>/memory.yaml` is **not** touched by an upgrade.

The next worker spawn picks up the refreshed skill set automatically;
no per-project bootstrap is required.

## Worker spawn — overlay path

`runtime/worker_spawn.spawn_worker(..., embedded_skills_root=...,
allowed_worktree_root=...)` calls
`runtime/embedded_skills.apply_embedded_skills` to copy the upstream
skills + customizations into the worker's worktree at
`.claude/skills/<name>/`. The operation emits an
`embedded_skills_applied` event into the worker's JSONL so the audit
trail records which skill set the worker ran with.

The overlay is gated on path-traversal + symlink-escape (`SymlinkEscapeError`,
`WorktreeOutOfRootError`) so a poisoned `customize.toml` cannot escape
the worker sandbox.

## Self-learning — four layers

| Layer | What it does | Where it lives |
|---|---|---|
| L1 — baseline policy | Operator-edited defaults | `skills/policy/*.yaml` |
| L2 — live tuning | Adaptive thresholds via rolling stats | `runtime/live_tuning.py` |
| L3 — per-project memory | Prime BudgetGuard windows on boot, persist across restarts | `runtime/project_memory.py` |
| L4 — lesson proposals | Retrospective markdown → policy proposals → operator-approved apply | `runtime/lesson_parser.py` |

L5 (reflexion — auto-PR on own skills) is intentionally **out of
scope** for this initiative; see step 6 of the master roadmap.

### L2 — Live tuning of code-review gates

`agent.run.code_review_subscriber` feeds each completed story's coverage
samples into `BudgetGuard.record_review_metrics`. Once a metric's
rolling window reaches `MIN_SAMPLES_FOR_TUNING` (5), `evaluate_threshold`
proposes the median as a new threshold. Proposals within 50% movement of
the current value are written via `atomic_write_gates_yaml`; larger
swings emit `HUMAN_QUERY` so the operator approves the drift
explicitly. The bound is `MAX_MOVEMENT_FRACTION_DEFAULT = 0.5`.

### L3 — Per-project memory

On `agent.run.run_orchestrator` boot path, `load_project_memory(slug)`
reads `_config/projects/<slug>/memory.yaml` and `BudgetGuard.prime_from_memory`
restores the rolling windows. Without this priming a restarted
orchestrator would start every wave with empty windows, defeating L2.

The schema is `pydantic` with `extra="forbid"` and an explicit
`schema_version` — forward-incompatible payloads fail loud rather than
silently dropping fields. Migration helpers handle `schema_version=0 → 1`
in `_migrate_payload`.

### L4 — Lessons → policy proposals

After a wave, the retrospective skill emits markdown under
`skills/lessons/<project>/wave-<N>.md`. Each `## Policy proposal:
<policy_file>.<field>` block carries `before:` / `after:` / optional
`rationale:` lines. The parser raises `LessonProposalInvalidError` on
unknown policy file, unknown field, or missing `before` / `after` —
soft warnings would mask hand-edit bugs.

Applying happens via `bmad-orchestrator policy-apply <project>` which:

1. Parses every `*.md` under the lessons dir (deterministic sort).
2. Persists the proposals to
   `_config/projects/<project>/policy-proposals.yaml` BEFORE writing
   anything so the operator has a review artefact even if `apply` later
   fails.
3. Prompts per proposal (or auto-applies with `--auto-apply`).
4. Validates against the pydantic schema before any disk write — an
   out-of-range value cannot half-corrupt the policy YAML.
5. Emits a `policy_proposal_applied` audit row carrying the full
   before/after payload + rationale so the operator can reconstruct
   the prior state without parsing the lesson.

## Manual smoke instructions

Run one synthetic story with embedded skills enabled, to confirm a real
target project picks up the orchestrator's canonical skill set:

```bash
bmad-orchestrator run \
  --project /home/server/odyssey \
  --wave 1a \
  --real \
  --max-stories 1 \
  --story <story-id>
```

Verify the worker's worktree carries the canonical skill set:

```bash
ls /home/server/odyssey-wt-*/.claude/skills/
# expect 14 skill directories matching skills/upstream/
```

After the wave completes, drive a retrospective + apply lessons:

```bash
# retrospective writes skills/lessons/odyssey/wave-1a.md
bmad-orchestrator retro --wave 1a

# review proposals (interactive); use --auto-apply for CI / scripted runs
bmad-orchestrator policy-apply odyssey
```

Inspect the audit log for the resulting policy edits:

```bash
grep policy_proposal_applied $(bmad-orchestrator logs --audit-path)
```

## Operational safety

- **Atomic writes.** Every YAML write under `skills/policy/`,
  `_config/projects/<slug>/memory.yaml` and `policy-proposals.yaml`
  goes through the same tempfile + fsync + `os.replace` pattern. A
  mid-write crash never leaves a half-written file in place of the
  previous good one.
- **Schema-validated writes.** Pydantic `model_validate` gates every
  policy and memory write so a programming bug or hand-edited proposal
  cannot land an unparseable YAML on disk.
- **Audit log carries rollback context.** `policy_proposal_applied` rows
  contain the full `before` value so the operator can revert without
  parsing the original lesson file.
- **Live tuning bounded.** `MAX_MOVEMENT_FRACTION_DEFAULT = 0.5` caps
  per-tuning movement; anything beyond emits `HUMAN_QUERY` instead of
  writing. This prevents adversarial samples from drifting thresholds
  into a permanently broken state.

## Tests

- `tests/test_e1_skills_repo.py` … `tests/test_e8_lesson_parser.py` —
  per-epic acceptance tests for E1-E8.
- `tests/test_e9_integration_e2e.py` — full pipeline + cross-module
  wiring + docs + grep DoD.

## Out of scope

- L5 reflexion (auto-PR on own skills) — step 6 master roadmap.
- Phase 1-3 skills embedding — steps 3-5 master roadmap.
- Skill sync to a project's own `.claude/skills/` directory (deploy
  mode) — step 7.
- Multi-version upstream support (pin different BMad versions per
  project) — follow-up initiative.
