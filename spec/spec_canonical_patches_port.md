# Spec — Canonical BMad patches port (C/N/Q/R/S/W/X/H from Odyssey runner)

**Дата:** 2026-05-18
**Версия:** 0.1
**Базовая ветка:** `main` (post-merge 352a6ab — embed_phase45_fixes)
**Backup branch:** `backup/canonical_patches_port-pre-2026-05-18`
**Integration branch:** `integration/canonical_patches_port`
**Auto merge:** false

---

## 1. Контекст

Odyssey-агент handoff 2026-05-17 (`/home/server/odyssey/spec/handoffs/handoff-bmad-phase4-gaps-2026-05-17.md`) обнаружил 22 active patches A-W в `bmad-auto-dev-runner.sh`. Наш bmad-orchestrator покрывает 5 из 22 + 1 wrong default. До real pilot — нужны 7 critical + 1 default fix.

**Reference impl:** `~/.claude/skills/bmad-auto-dev/scripts/bmad-auto-dev-runner.sh` (1100+ строк bash, production-tested in Odyssey Wave 1a). Каждый patch там implemented inline с line markers `# Patch X 2026-05-15:`.

## 2. Принципы

- **Port, не design** — каждая session начинается с `grep -nE "Patch <ID>" ~/.claude/skills/bmad-auto-dev/scripts/bmad-auto-dev-runner.sh`, читает bash impl + customize.toml triggers, port'ит семантику к Python subscriber/gate.
- **Tests own design** — bash их code не покрывает unit tests; мы пишем regression tests с нуля.
- **Architecture: subscribers, not bash stages** — port'им логику к `code_review_subscriber` / `worker_spawn` / новых subscribers, не tries to mirror Stage 1-8 numbering.
- **Customize.toml тоже portable** — triggers (epic ∈ {3,4,5,7,9,10}, keywords, frontmatter flags) идут в `skills/policy/*.yaml` для tunability.

## 3. Stack / Constraints

- Python 3.11+, без новых deps
- Сохранить 1034 PASS + добавить ~120 tests → ~1154 PASS
- ruff + mypy --strict зелёные
- Deny-list freeze: sandbox/worker_spawn (security-critical functions), но **caller-side helpers** в worker_spawn разрешены

## 4. Session Plan

6 sessions, surface=`backend-python`, code-only. Каждая session порт'ит 1-2 patches.

### P1 — Patch H (timeout) + Patch C (smart deletion check) (CHECKPOINT)

- **Patch H**: timeout 86400s → 1800s в `worker_spawn.BMAD_WORKER_TIMEOUT_SEC` default. Reference: lines 91-127 of runner.sh.
- **Patch C**: new subscriber `deletion_safety_subscriber` — на WORKER_COMPLETED scan diff for migration/secret deletions, halt + HUMAN_QUERY. Reference: runner.sh `# Smart deletion` logic + `migration_patterns` from customize.toml.
- 20 tests
- Target: 1054 PASS

### P2 — Patch N (build check guard) (CHECKPOINT)

- New subscriber `build_check_subscriber` — после WORKER_COMPLETED но ДО code-review spawn → run `pytest tests/` / `cargo check` / project-specific lint в worktree. Reject если build broken → skip $15 Opus review. Reference: runner.sh Stage 5.5 (lines ~589-695 nearby).
- Project-specific commands в `skills/policy/build-check.yaml`
- 20 tests
- Target: 1074 PASS

### P3 — Patch Q (diff size) + Patch R (commit completeness) + Patch S (Stage 5 completeness) (CHECKPOINT)

- **Patch Q**: extend `code_review_subscriber` — pre-merge diff measurement. Reject if recovery commit diff > 500 lines untracked-by-File-List. Reference: runner.sh Patch Q (lines ~821+).
- **Patch R**: extend `merge_to_integration_subscriber` — auto-stage uncommitted после auto-fix recovery. Reference: runner.sh `# Patch R` (lines ~821-848).
- **Patch S**: new subscriber `stage5_commit_completeness_subscriber` — на WORKER_COMPLETED detect uncommitted, auto-stage+commit before passing to review. Reference: runner.sh lines ~566-580.
- 30 tests
- Target: 1104 PASS

### P4 — Patch W (scope by File List) (CHECKPOINT)

- Extend `merge_to_integration_subscriber` + `Patch R` recovery — allow-list = NEW+UPDATE files from `_bmad/stories/<id>.md::File List` ∪ `sprint-status.yaml` ∪ `deferred-work.md`. Reject anything outside. Reference: Odyssey `skill_improvement_patch_W_candidate.md`.
- 15 tests
- Target: 1119 PASS

### P5 — Patch X (security-review conditional) (CHECKPOINT)

- New subscriber `security_review_subscriber` — conditional invoke `claude -p /bmad-security-review` (4-hunter parallel: Injection / Auth Bypass / Crypto / Data Leak).
- Triggers (from `skills/policy/security-review.yaml`):
  - frontmatter `security_critical: true`
  - epic ∈ {3, 4, 5, 7, 9, 10} (configurable)
  - keyword match in story spec/diff: auth, jwt, rls, dpa, crypto, billing, pii, audit, hmac, argon, session
- Verdicts: APPROVE / MERGE-WITH-FIXES / BLOCK → halt+HUMAN_QUERY
- 25 tests
- Target: 1144 PASS

### P6 — Integration + e2e + docs (FINAL)

- E2E test: synthetic story → spawn worker → 7 new subscribers run → all gates pass → merge
- `docs/canonical-patches-architecture.md` — mapping our subscribers ↔ Odyssey Patch IDs, port methodology, customize.toml → policy.yaml mapping
- ~10 final integration tests
- Target: 1154 PASS
- Manual merge

## 5. Acceptance — initiative level

После P6 merge:
- ✅ Coverage 13/22 patches (от 5/22)
- ✅ Wrong default H fixed (30 min vs 24h)
- ✅ Production safety net для pilot (deletion / build / commits / scope / security)
- ✅ Customize.toml triggers ported в `skills/policy/*.yaml`
- ✅ ~1154 PASS, ruff/mypy clean
- ✅ Docs опубликованы с port methodology

**После — pilot ready с full canonical safety net.**

---

**End of spec v0.1.**
