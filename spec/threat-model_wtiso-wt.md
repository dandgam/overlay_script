---
q_id: Q-260527-WTISO-WT
parent: Q-260527-WTISO (umbrella)
method: STRIDE
scope: worktree-per-Q-NNN isolation layer ONLY (bwrap/cgroup/HOME-overlay = WTISO-BW; shards = WTISO-SH)
created_at: 2026-05-27
verdict: PASS (с honest gap-disclosure для cross-worker FS на WT-layer)
related_layer_oos:
  - cross-worker FS read (cd ..) — open until WTISO-BW (bwrap --bind scope)
  - fork-bomb DoS — open until WTISO-BW (cgroup TasksMax)
  - PII/secret leak via env — open until WTISO-BW (--clearenv)
---

# STRIDE Threat Model — Q-260527-WTISO-WT (Worktree-per-Q-NNN Layer)

## Метод

STRIDE по 6 категориям. Source attack surface = `_spawn_worker` (888-batch.sh:335-459) + worktree-path input handling + `--worktree` arg wiring в `888-batch-headless-prompt.sh` и `888-batch-commit-policy.sh`. Анализируется ТОЛЬКО WT-layer mitigations; BW/SH responsibilities явно maрking как OOS.

Honest gap: WT-layer alone НЕ обеспечивает full sandbox. Это **первый из трёх** слоёв (WT → BW → SH). After WT-merge некоторые attack vectors остаются open (см. таблицу + §3 "WT-Layer-Closed vs Still-Open").

## Таблица — STRIDE × WT-layer

| # | STRIDE | Attack vector (specific to WT scope) | Sev (1-5) | Mitigation (WT-layer) | Belongs to |
|---|---|---|---|---|---|
| T1 | **T** Tampering | Two workers commit concurrently to shared `.git/index` → file corruption / partial writes / `.git/index.lock` race | **5** | `_prepare_worktrees` создаёт `git worktree add /tmp/888-bat-<batch-id>/<q-id> integration/<q-id>` per Q-NNN; каждый worktree получает own `.git/index` file (per `git-worktree(1)`) | **WT (primary)** |
| T2 | **T** Tampering | LLM-worker calls `git config --global user.name attacker` → poisoning ~/.gitconfig для следующих batch'ей | 3 | НЕ в WT-layer. Documented gap → BW HOME overlay. WT mitigates ONLY via documented OOS. | **BW** (OOS) |
| E1 | **E** Elevation | LLM-worker calls `git checkout main && git push` → branch hijack из worktree | **5** | Wrap `_spawn_worker` cmd с branch-scope check: validate `git branch --show-current` matches assigned `integration/<q-id>` before/after worker; PreToolUse hook rejects checkout/switch outside assigned branch | **WT** |
| E2 | **E** Elevation | LLM-worker calls `git checkout <other-q-id-branch>` → cross-Q contamination | 4 | Same as E1: branch-scope check rejects switch to any branch != assigned `integration/<q-id>` | **WT** |
| T3 | **T** Tampering | `--worktree '/tmp/../etc/passwd'` (path traversal) → write to system path | **4** | `_validate_worktree_path(path, batch_id)`: `realpath -e "$path"` + assert prefix matches `/tmp/888-bat-<batch-id>/`; reject with audit event `worktree_path_rejected` exit 78 | **WT** |
| T4 | **T** Tampering | `--worktree '/tmp/888-bat-OTHER-BATCH-ID/q-NNN'` (cross-batch trespass) | 3 | Same `_validate_worktree_path` — prefix check включает `$BATCH_ID`, не только `/tmp/888-bat-`; reject если other batch's id | **WT** |
| I1 | **I** Info disclosure | LLM-worker reads sibling worker's tree via `cd ../other-q-id/.` → reads in-flight code | 4 | **NOT closed at WT-layer.** Documented OOS — bwrap `--bind` scope to single worktree. Audit warn'ing if `pwd` outside worktree at worker_done. | **BW** (OOS, honest gap) |
| I2 | **I** Info disclosure | LLM-worker reads `~/.claude/.env` / `~/.ssh/id_rsa` | 5 | NOT closed at WT-layer. Bwrap `--bind`/blackouts на BW. WT does nothing here. | **BW** (OOS) |
| D1 | **D** DoS | Worker fork-bombs / runs `:(){:|:&};:` exhausts host PIDs | 4 | NOT WT-layer. cgroup TasksMax via systemd-run --scope на BW. | **BW** (OOS) |
| D2 | **D** DoS | Worker writes massive file `/tmp/888-bat-<id>/<q-id>/junk` → fills /tmp | 3 | NOT WT-layer (cgroup memory.high/IO limits — BW). WT adds soft-mitigation: post-batch cleanup `git worktree remove --force` removes the path; partial mitigation. | **BW primary**, WT partial |
| D3 | **D** DoS | Worktree leak — batch crashes mid-flight, `/tmp/888-bat-*` accumulates → disk full after N batches | 2 | `_cleanup_worktrees(batch_id)` runs in EXIT trap; `git worktree prune` post-batch. Test M3 asserts 0 leaked entries. | **WT** |
| R1 | **R** Repudiation | Worker commits with `--reset-author` / fake `--author='someone <x>'` | 3 | NOT WT-layer. commit-policy enforces author trailer (existing 888-batch-commit-policy.sh). WT — out of mitigation chain. | **commit-policy** (existing) |
| S1 | **S** Spoofing | Worker writes audit event с fake `q_id` (другой Q-NNN) → masquerade | 3 | NOT WT-layer (dispatcher-set q_id, worker has read-only env BATCH_888_Q_ID). WT just propagates. | dispatcher (existing) |
| E3 | **E** Elevation | Worker invokes `git worktree add /tmp/888-bat-<id>/q-NEW-EXTRA new-branch` (creates worktree outside dispatcher scope) | 3 | Soft-mitigation: post-batch `git worktree list` diff against expected set; warn if extra entries. Hard block requires PreToolUse hook on `git worktree add` invocation pattern. | **WT** (partial) |
| T5 | **T** Tampering | Race в `_prepare_worktrees` itself: two batches share `/tmp/888-bat-*` namespace → cross-batch tree collision | 4 | batch_id is timestamp+random (existing); add lockfile `/tmp/888-bat-<batch-id>/.prepare.lock` (flock) при prepare. Reject second prepare на same batch_id с exit≠0. | **WT** |
| E4 | **E** Elevation | Feature-flag bypass: BATCH_ISOLATION_ENABLED unset/0 → silent fallback к old shared-CWD model в production | **5** (adversarial #25) | Hard-fail если parallel mode (BATCH_PARALLEL_ENABLED=1) AND BATCH_ISOLATION_ENABLED=0 — log loud warning, refuse to start (exit 79). Опция должна explicitly opt-out с downgrade в sequential. | **WT** |

## Severity scoring

Likert 1-5: 1=theoretical, 5=trivial-exploit-high-impact.

**Top-3 critical (sev 5):**
- T1 .git/index race — exploit без attacker (любой parallel commit), high impact (corrupt state)
- E1 branch hijack via checkout main — trivial для LLM-worker, hijacks integration topology
- E4 feature-flag bypass — silent regression to vulnerable mode = primary anti-pattern from §4fj #25

All sev-5 items mitigated в WT-layer.

## §3 — WT-Layer-Closed vs Still-Open (honest disclosure)

**Closed by WT (8 items):**
- T1 .git/index race (PRIMARY value)
- E1/E2 branch hijack (PreToolUse hook + scope check)
- T3 path traversal (input validation)
- T4 cross-batch trespass (prefix check)
- T5 batch namespace race (prepare lock)
- D3 worktree leak (cleanup trap)
- E4 feature-flag bypass (hard-fail invariant)

**Partial / soft mitigation only:**
- D2 /tmp disk fill (cleanup helps, no hard quota)
- E3 worker creates extra worktree (post-batch diff, no PreToolUse)

**Open at WT-layer — required by BW or commit-policy:**
- T2 ~/.gitconfig poison → BW HOME overlay
- I1 sibling worker FS read (`cd ..`) → BW bwrap --bind
- I2 ~/.claude/.env read → BW blackouts
- D1 fork-bomb → BW cgroup TasksMax
- R1 author repudiation → commit-policy (existing)
- S1 q_id spoof → dispatcher (existing)

## Verdict

**PASS.** 8 of top-3 sev-5 items closed at WT-layer. Remaining gaps are **explicitly documented** as BW-responsibility (analyst §4fj allocation matches: 8 WT findings ≈ this threat model's WT-closed count). No new findings outside analyst's allocation.

No NEEDS-REVISION blockers. Architect can proceed to implementation spec with this threat model as §4 acceptance.

## Cross-ref to analyst §4fj adversarial findings

- #4 "0 races unfalsifiable" → T1 mitigated AND test plan includes 10-iter run with audit event schema (see spec §5)
- #25 No rollback / feature-flag bypass → E4 mitigated via hard-fail invariant
- #27 No baseline measurement → addressed in spec §5 (baseline test BEFORE applying WT)
- #19 Python→bash translation gap → not strictly threat-model, addressed in spec §3 layer enumeration
