# Spec — Batch composition + sizing pipelines v1

> **Umbrella:** Q-260526-BATCH · **Sub-Q:** SIZE / STHP / TBAT / DCAP / ISOL
> **Brief:** `~/.claude/skills/888/methodology-888.md` §4de (analyst Phase 1)
> **Storm T1:** `~/.claude/skills/888/spec/feature_batch_composition_v1_storm.md` (ADRs 001-005)
> **Phase 2 architect output. 2026-05-26.**

---

## §1 Goal

Прекратить гонять каждый мелкий/средний фикс через все 5 фаз ADLC. Композировать S/M Q-NNN по теме в один батч (1 спека · 1 сессия · 1 review). XL — full-cycle как сейчас. Соблюдать build-discipline R1-R7.

**Success (DELTA от auto-measured baseline):**
- ≥70% S/M fixes via batch-pipeline за 14 дней (min sample N≥15)
- avg time-to-close S/M < 1 сессия
- derivative-explosion rate < 1.5×
- storm coverage в headless `claude -p` ≥ 95%

---

## §2 Closed-set enumeration — type × pipeline (R5 mandate)

**Final taxonomy (refined from analyst draft 6×4 → architect 6×4 + override matrix):**

| Type ↓ / Pipeline → | skip-cycle | mini-cycle | batch-cycle | full-cycle |
|---|---|---|---|---|
| **XS** typo/comment/README line/1 manifest field | ✅ default | — | — | — |
| **S** 1-3 script lines / 1 test / known pattern | ✅ if Iron Law whitelisted | ✅ default | ✅ if в теме (N≥2) | — |
| **M** 1-3 files / 1 feature / no new dep | — | ✅ default | ✅ if в теме (N≥2) | escalate if security_critical |
| **L** 3-10 files / multi-handler / new pattern | — | — | — | ✅ default |
| **XL** >10 files / новая subsystem | — | — | — | ✅ default + PHSP split |
| **security_critical** override (auth/crypto/SQL/file paths/canary/locks/PII) | — | — | — | ✅ + bmad-security-review 4 hunters |

**Pipeline definitions:**

- **skip-cycle** = implementer alone + Iron Law self-check (no architect, no qa, no improver)
- **mini-cycle** = architect (light, ≤4 fields) → implementer → improver (light retro). 3 personas, no qa, no ops
- **batch-cycle** = composer → analyst (light brief per batch) → architect (per batch spec) → implementer (loop over N items) → qa (1 review of whole batch) → improver (1 retro). 5 personas, but N≥2 items
- **full-cycle** = current standard. analyst → architect → implementer → qa → ops → improver. 6 personas, 1 item

**Decision rules:**

1. Structural classification (script) FIRST: `size-initiative.sh --structural` → returns hint (XS/S/M/L/XL) based on file path count estimate + scope keywords
2. Intent classification (LLM-judge) SECOND: only if structural returned tie (e.g., between S/M) OR scope contains intent-only signals
3. security_critical = structural override (auto-detected from file paths: `crypto/` `auth/` `migrations/` `*Secret*` `*Token*` etc.) → ALWAYS full-cycle + 4 hunters
4. XL = structural override (file count >10) → ALWAYS full-cycle + PHSP split into multi-session
5. Manual override: `CLAUDE_PIPELINE=skip|mini|batch|full|force-full` env var (deny if security_critical detected)

**Override matrix (architect addresses review P1#2):**

| Trigger | Override action |
|---|---|
| security_critical detected | force full-cycle + 4 hunters (deny manual downgrade) |
| XL detected | force full-cycle + PHSP (deny manual downgrade) |
| User explicit `CLAUDE_PIPELINE=force-full` | apply (audit log entry) |
| User explicit `CLAUDE_PIPELINE=skip` on M+ | deny + LLM-judge explains why |
| Storm T7 fired (≥5 fix commits / 14d) | force mini-cycle minimum (no skip) |

---

## §3 4-level reaction gates (R7 mandate per gate)

R7 = `try-fix → soft-warn → LLM-judge → hard-halt`. Architect Field deliverable: table per gate.

| Gate | try-fix | soft-warn | LLM-judge | hard-halt |
|---|---|---|---|---|
| **SIZE missing classification** | Default L-tier | STDERR warn «sizing inferred=L» | Architect override via prompt | Iron Law violation if XL claimed but >10 files |
| **DCAP cap-breach** | Auto-suggest batch-parent | STDERR warn «parent X has 3 open children» | Composer LLM-judge: are these the same theme? | Block new Skill if parent has ≥5 + composer rejected |
| **TBAT theme-misgroup** | Lowest-score pair dropped | Show user proposed batch with cohesion scores | LLM re-scores after user feedback | Block spec-gen if avg cohesion <0.5 |
| **ISOL wrong-context** | Default current session | Warn «task usually needs fresh `claude -p`» | LLM-judge: does current context have >70% relevant tokens? | Block if context >85% + task is L+ |
| **STHP storm bypass detected** | Re-fire hook | Warn user storm artifact stale | LLM-judge: does artifact match scope? | Block Edit/Write if no artifact + scope not whitelisted |

---

## §4 Script designs (5 scripts on 4 families per R6)

R6 mandate: 1 script per family, not per situation. Families:

### Family 1 — Sizing (1 script)

**`scripts/size-initiative.sh <Q-NNN-or-scope-text>`**
- Args: `--structural` (script-only) | `--intent` (LLM-judge) | `--full` (both)
- Output: `XS|S|M|L|XL` + `security_critical:true|false` + recommended pipeline + confidence score
- Reads: methodology section for Q-NNN, git log if available, scope text from arg
- Writes: `audit/sizing-decisions.jsonl` (append-only per R3)
- Integration: triage §1.5 cascade calls this; Entry menu §0.5 displays result

### Family 2 — Composition (1 script + composer)

**`scripts/compose-batch.sh --theme <X> [--auto|--manual]`**
- Discovers candidate Q-NNN: open S/M from queue with parent overlap OR LLM-judge cohesion
- Sub-tool: `compose-judge-pair.py <q-id-1> <q-id-2>` returns score 0-1 (R1-safe: forma output, single LLM call per pair)
- Clusters pairs above threshold (default 0.7) into batches
- Outputs: `audit/batches/<batch-id>.json` + draft `spec/spec_batch_<theme>_<date>.md`
- User confirm gate before spec finalization

### Family 3 — Derivative cap (1 script + 1 hook)

**`scripts/derivative-cap.sh <parent-q> [--check|--register]`**
- `--check` returns count of open children
- `--register <new-q>` increments count (flock-protected per ADR-005 → R3)
- Cap default = 3 (architect: pull histogram from queue-history.jsonl to validate; may bump to p75)

**`~/.claude/hooks/derivative-cap-PostToolUse-Skill.sh`**
- Triggers on Skill invocation
- Extracts parent Q-NNN from handoff payload
- Calls `derivative-cap.sh --check` → block if ≥cap + composer not invoked
- R4 mandate: critical rule in hook, not prompt

### Family 4 — Session isolation (1 script)

**`scripts/session-isolation.sh <task> [--decide|--apply]`**
- Reads `config/session-isolation.yaml` (closed-set rules)
- `--decide` returns `current|fresh-claude-p|new-worktree`
- `--apply fresh-claude-p` spawns headless `claude -p` with task payload
- Integration: auto-handoff §2 step 7-bis consults this before invoking next persona

### Family 5 — Baseline measurement (1 script, supports Field 6 metric)

**`scripts/measure-batch-baseline.sh [--commit|--dry-run]`**
- Reads queue-history.jsonl + git log --since=30d
- Classifies historical Q-NNN by tier × pipeline (using current SIZE rules)
- Outputs JSON to `evals/baselines/batch_pipeline_2026-05-26.json`
- Idempotent on same inputs (Q-260520-LLM dev best practice #4)

---

## §5 Threat-model (architect Field 6 placeholder + DCAP-specific from review)

**Top-5 attack vectors (architect-level):**

1. **Prompt injection via theme name** → composer LLM-judge accepts «security audit batch» containing actual rm-rf payloads. Mitigation: theme name is user-text, NEVER fed to executor LLM; only matched against allowlist for composer purposes.
2. **DCAP race condition** → 2 concurrent Skill invocations both pass cap=3 check, both increment, total = 5. Mitigation: flock на `audit/derivative-count.json` per parent (~10ms hold); hook returns exit=2 on lock contention.
3. **DCAP bypass via direct Edit** → user manually edits methodology to add children without going through Skill. Mitigation: NOT a real bypass — direct edit doesn't trigger downstream persona work; cap counts ACTIVE persona invocations only.
4. **Secret exfil through spec_batch_*.md generation** → composer LLM sees Q-NNN containing PII / secrets in description → writes them to spec file. Mitigation: PII redaction layer in compose-judge-pair.py (uses existing `Pii::redact()` equivalent from CLAUDE.md security defaults).
5. **Privilege escalation through composer** → batch spec wraps L-tier task disguised as S → bypasses qa. Mitigation: SIZE re-runs ON EACH ITEM individually in batch-cycle; if any item evaluates as M+ → batch escalates to mini-cycle minimum.

**DCAP threat (review P1#5 specific):**

- **Threat**: retroactive vs grandfather for existing open Q-NNN
- **Decision**: grandfather — existing open Q-NNN ignored for cap counting; only new Q-NNN created post-landing counted. Document in DCAP README.

---

## §6 Iron Law RED test plan (architect mandate from analyst handoff §4de #6)

Per-script RED tests (implementer must write each as failing test BEFORE implementation):

| # | Script | RED test scenario |
|---|---|---|
| T-SIZE-1 | size-initiative.sh | `--structural` on Q-NNN with 5 files in `crypto/` returns `security_critical=true` even if user-claimed XS |
| T-COMP-1 | compose-batch.sh | 5 unrelated Q-NNN with theme="misc" returns batch with avg cohesion <0.5 → exit non-zero |
| T-COMP-2 | compose-judge-pair.py | Returns score, NOT free-form text (R1-safe constraint) |
| T-DCAP-1 | derivative-cap.sh | --check on parent with 3 open children returns exit=2 (cap breach) |
| T-DCAP-2 | derivative-cap PostToolUse hook | Blocks Skill invocation if cap breached + composer not invoked |
| T-ISOL-1 | session-isolation.sh | Returns `fresh-claude-p` for L-tier task when current context >70% |
| T-MEAS-1 | measure-batch-baseline.sh | Same inputs → same JSON output (idempotent) |
| T-STHP-1 | hooks in claude -p | All 11 trigger IDs fire correctly in headless mode (audit script) |

**Implementer Phase 2.5 RED-GREEN sequence:**
1. Write all 8 RED tests → commit (must FAIL)
2. Implement scripts one by one → tests turn GREEN incrementally → commit per script

---

## §7 Migration plan (review P2 — §7.7 + §7-ter absorbed)

| Old | New | Migration |
|---|---|---|
| `§7.7 phase-skip whitelist` (static allowlist) | `size-initiative.sh` SIZE classifier (XS tier maps to skip-cycle) | XS rules from §7.7 → loaded as SIZE config; §7.7 marked deprecated 2026-06-15, removed 2026-07-01 |
| `§7-ter BATCH chained handoff` (sequential Q-NNN runner) | `compose-batch.sh` (theme aggregation) + existing `888-batch.sh run` | `888-batch.sh` gains new sub-command `compose`; `run` continues as today; both interop |
| `scripts/classify-diff.sh` (post-factum) | `size-initiative.sh --structural` (pre + post) | `classify-diff.sh` becomes internal helper called by `size-initiative.sh`; existing direct callers continue working until 2026-07-01 |
| `Q-260524-AUTO auto-handoff` (current) | extends с tier-aware confirm gate | After landing — auto-handoff проверяет sizing tier; XS/S/M passes confirm gate without prompt |

**Back-compat window:** 6 weeks (2026-05-26 → 2026-07-01). All deprecation warnings emit STDERR. Removal triggers if 0 active callers detected.

---

## §8 Phase 2.5 implementer handoff payload

**Order of implementation (per ADR-005 + review P0):**

1. **measure-batch-baseline.sh** + commit `evals/baselines/batch_pipeline_2026-05-26.json` (foundation for metric)
2. **STHP audit** (separate Q-260526-STHP — verify hooks in claude -p) — BLOCKING for next steps if fails
3. **size-initiative.sh + classifier** (Q-260526-SIZE) + integration в triage §1.5
4. **derivative-cap.sh + hook** (Q-260526-DCAP)
5. **compose-batch.sh + compose-judge-pair.py** (Q-260526-TBAT)
6. **session-isolation.sh** (Q-260526-ISOL)
7. End-to-end smoke test on dogfood: route Q-260526-TBAT itself through new batch-pipeline

**Iron Law sequence per script:** write RED test → commit (FAIL) → implement → commit (GREEN). NO «green from birth».

**Spec deliverables done by architect (this file):** §1-7 + RED test list + threat-model + migration plan. Implementer details script implementations, qa designs eval suite, ops handles rollout flag.

---

## §9 Cross-impact verified

| Existing | Status |
|---|---|
| Q-260524-AUTO (auto-handoff) | extends via tier-aware confirm gate; interface = handoff payload `tier:` field |
| Q-260525-PHSP (auto-split heavy) | orthogonal; XL → PHSP, S/M → batch |
| Q-260524-CTXM (context budget) | hooked via session-isolation.sh consulting CTXM `current_context_pct` |
| Q-260524-PARA (parallel waves) | composable; batch within wave + N waves parallel |
| Storm framework Layer 1-3 | depends on STHP audit; if hooks broken in headless → architecture changes to inline middleware |

---

## §10 Status

- **Phase 2 architect:** DRAFT (Storm T1 ✅ filled · this spec ✅ §1-9) — pending review-gate
- **Next:** `bmad-review-edge-case-hunter` via Agent (R7 faithful-substitute) on this spec
- **Then:** handoff to Phase 2.5 implementer
- **Section in methodology:** §4df (to be created with frontmatter handoff-pending qa **OR** implementer per Phase 2.5 routing flag)

**Open work documented in handoff for Phase 2.5 implementer to actually write:** all 8 RED tests + 5 scripts + 1 hook + config YAML + measure-baseline JSON output.

**Architect did NOT write code (per §4 invariant).** Only design + spec + threat-model + test list.
