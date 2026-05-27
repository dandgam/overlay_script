---
q_id: Q-260527-WTISO-BW
parent: Q-260527-WTISO (umbrella)
sibling: Q-260527-WTISO-WT (shipped §4fl), Q-260527-WTISO-SH (pending)
tier: M
phase: 2 (architect Stage 2 — spec write)
status: architect-in-progress · pending implementer Phase 2.5
security_critical: true
pattern: P3-parallel + P4-decompose (deterministic OS-isolation pipeline, no LLM в BW-layer)
complexity: complex (security_critical override — full track, no sizing reduction)
effort_estimate: ~6-8h (M-tier; revised от ~4h per analyst F18)
created_at: 2026-05-27
author: 888-persona-architect (R7 substitute, Stage 2)
analyst_gate: 2026-05-27T05:45Z (§4fw v3 PASS, auto-rationale documented)
architect_stage1_gate: 2026-05-27T05:55Z (§4fx Stage 1 — 7-field + outline)
threat_model: spec/threat-model_wtiso-bw.md (separate deliverable, this Stage 2)
edge_case_hunter: pending Stage 4
storm_t1: spec/feature_wtiso-bw_storm.md (stub backend per WT precedent §4fk)
dep: Q-260527-WTISO-WT (shipped §4fl modulo MOCKISO)
blocks: Q-260527-WTISO-SH (methodology shards), Q-260527-WTISO-BW-CPU (CPU quotas follow-up), Q-260527-WTISO-BW-DISK (aggregate disk quota follow-up), Q-260527-WTISO-NET (egress allowlist follow-up)
reference_code: src/bmad_orchestrator/runtime/sandbox.py (972 LOC, gold reference; option C reuses as subcommand)
---

# Spec — Q-260527-WTISO-BW: bwrap + prlimit + cgroup + HOME overlay isolation layer

> Sub-Q под umbrella Q-260527-WTISO. Закрывает WT-deferred gaps T2 (gitconfig poison) / I1 (sibling FS read) / I2 (~/.claude cross-worker contamination) / D1 (fork-bomb PID exhaustion) / D2 (disk-fill partial) + новые #14 (bwrap version drift) + #15 (worker_home_overlay missing). Bwrap binary version assertion + per-worker `~/.claude` overlay + `--clearenv` + 10-var allowlist + ~30 blackout paths + cgroup `TasksMax`/`MemoryMax` + prlimit RLIMIT_* fallback + NoSandbox hard-fail-by-default policy. Methodology shards (per-Q patch counter, per-Q merge gate, rollback contract) = OUT OF SCOPE → Q-260527-WTISO-SH.

---

## §1 Context + analyst handoff

### 1.1 Pain (8-category cross-tenant blast radius per §4fw Edit-1)

WTISO-WT (shipped §4fl) закрыл `.git/index` race + branch-hijack + worktree-path traversal. Что осталось — полный OS-level isolation surface. Currently `_spawn_worker` (`~/.claude/skills/888/scripts/888-batch.sh:335-459`) запускает `claude -p` БЕЗ isolation wrapper. `_scan_bash` pattern-matcher (`agent/safety/hooks.py`) — defence-in-depth, не primary safety (sandbox.py:11-13 explicit); 3 rounds bypass-fix loops produced 5+5+5 P0 bypasses (heredoc, subshell, brace, xargs, curl-pipe — sandbox.py:4-6). Pattern blacklist provably loses arms race.

Unfettered read access ко всем 30 blackout paths из Virgil sandbox.py:419-467. Категории exposure (§4fw Edit-1 verbatim):

| # | Категория | Конкретные paths (sandbox.py:419-467 evidence) |
|---|---|---|
| 1 | **Cross-tenant prod data** | `/home/server/crm` (другой production project на этом же host — multi-tenant violation), `/etc/shadow`, `/etc/sudoers`, `/etc/ssh`, `/root` |
| 2 | **Cloud provider creds** | `~/.aws/`, `~/.config/gcloud/`, `~/.config/azure/`, `~/.terraform.d/credentials.tfrc.json` |
| 3 | **Source-code platform creds** | `~/.config/gh/` (GitHub CLI auth), `~/.git-credentials`, `~/.config/git/credentials`. NOTE: `~/.gitconfig` сам deliberately READABLE (sandbox.py:436-439) — worker commits resolve user.email/name |
| 4 | **Package registry creds** | `~/.npmrc`, `~/.pypirc`, `~/.cargo/credentials.toml` |
| 5 | **Secret-store** | `~/.vault-token`, `~/.password-store/`, `~/.config/op`, `~/.config/sops`, `~/.config/pulumi` |
| 6 | **GPG/SSH keys** | `~/.gnupg/`, `~/.ssh/`, `~/.local/share/keyrings/`, `/run/user/$UID/{keyring,gnupg}` |
| 7 | **Orchestrator secrets** | `~/.bmad-orchestrator/`, `~/.config/bmad-orchestrator/`, orchestrator state.db (sandbox.py:463-466). NOTE: `~/.claude/` использует per-worker overlay (sandbox.py:499-517) — writable copy isolates cross-worker contamination |
| 8 | **OS-level keyrings** | `/etc/shadow`, `/etc/sudoers`, kernel keyring via `/proc/keys` (mitigated через `--tmpfs /sys` + ro-bind kernel-info files) |

**Compliance angle (152-ФЗ ст.13.11 — cross-border PII transfer):** `/home/server/crm` contains user PII (orders, contacts). Non-isolated worker может exfil PII через LLM API call (Anthropic — US-based) без operator consent. BW-layer isolation = compliance baseline для multi-tenant deployment.

**Operator burden без BW:** каждый batch run требует manual verification что workers не trigger'нули exfil. Не scaleable beyond synthetic tests.

### 1.2 Cross-link к analyst Phase 1 (v3 PASS)

`~/.claude/skills/888/methodology-888.md` §4fw — full 6-field brief + 22-row STRIDE + 13/13 adversarial-allocation closure + baseline JSON schema + Iron Law 10-test plan. v3 gate-passed 2026-05-27T05:45Z (auto-PASS rationale: round-2 commit «otherwise all 10 edits substantively PASS» + 2 mechanical fabrications fixed verbatim).

**Verification debt explicit (carry-forward от analyst):** round-3 reviewer не invoked после v3 mechanical fix. ⚠ Если architect Stage 2-4 OR implementer finds residual evidence issue grounded в v3 claims (особенно sandbox.py:436-439 / :463-466 / :499-517 cites), re-check ДО code-gate.

### 1.3 security_critical:true justification

| Marker | Source |
|---|---|
| Closes 22 STRIDE primitives (T2/I1-I7/D1-D4/N1/C1-C2/E3-residual + #14/#15) — `~/.claude/skills/888/methodology-888.md` §4fw Edit-2 v2 |
| Closes 13 of 27 adversarial findings (§4fj BW allocation, full enumeration §4fw Edit-10) |
| 152-ФЗ ст.13.11 compliance baseline (Russian PII law) для multi-tenant 888 batch deployment |
| CLAUDE.md Critical Boundaries §5 (project-level): «Worker isolation — primary safety = OS-level sandbox (`runtime/sandbox.py`)... Prod prerequisite: `apt install bubblewrap` (отсутствует → `NoSandbox` fallback + loud audit warning, **primary safety теряется**)» |
| Reference code is 972-LOC battle-tested Python `sandbox.py` (FS7/FS9 rounds + 3 P0 bypass fix-loops + Patch DD + Initiative #1 Task 1.3/1.4) |

**No spec sizing reduction permissible** per security_critical override. Full track applies (12 sections, ~500-700 LOC, full threat-model deliverable, 10 RED tests, ops Phase 4 ramp).

### 1.4 Parent umbrella + sibling layers

| Sub-Q | Status | Coverage |
|---|---|---|
| Q-260527-WTISO-WT | ✅ shipped §4fl (commits 73808f0+525285e+3be7b22+de9d9ad) | worktree per Q-NNN + dead `--worktree` arg wiring + 8 findings (T1/E1/E2/T3 etc.) |
| **Q-260527-WTISO-BW** | 🟡 phase:2 architect Stage 2 (this spec) | bwrap+prlimit+cgroup+overlay; 13 findings; 5 sev-5 + 7 sev-4 + 6 sev-3 STRIDE rows |
| Q-260527-WTISO-SH | ⬜ pending | methodology shards (per-Q patch counter, merge gate, rollback contract); 6 findings |

---

## §2 Architecture — 7-field summary (§4fx verbatim)

| F | Field | Decision | Rationale (source) |
|---|---|---|---|
| **F0** | Complexity classification | **complex** | auto-heuristic-2+5: security_critical + 972-LOC reference + new feature flag (`BMAD_ALLOW_NOSANDBOX`) + 4 new audit event types + 10 Iron Law tests + ≥10 files modified. Full track, no sizing reduction. |
| **F1** | Single LLM call достаточен? | **No** | BW-layer = deterministic OS isolation pipeline (bwrap+prlimit+cgroup+overlay). Zero LLM calls в orchestration harness. LLM lives ВНУТРИ worker (`claude -p`), не в BW pipeline. |
| **F2** | Anthropic pattern P1-P5 | **P3 parallel + P4 decompose** | Same as WT sibling (§4fk). Worker per Q-NNN, parallel batch с per-worker bwrap namespace. Decompose: bwrap-wrap → prlimit-wrap → systemd-run cgroup-wrap → exec `claude -p` (4-step orchestration pipeline). |
| **F3** | Memory | **Session-only** | Per-worker overlay snapshot в `/tmp/888-bat-<batch-id>/overlays/<q-id>/`. Cleanup post-batch (mirror WT cleanup contract §4fl). No persistent state. Каждый batch run = fresh isolation namespaces. |
| **F4** | External tools (≤5 Anthropic recommendation) | **8 tools** (over cap; justified) | bwrap, prlimit, systemd-run, realpath, `python -m bmad_orchestrator.runtime.sandbox` (option C), env, flock, bash builtins. Over-cap rationale: OS-isolation pipeline inherently composes multiple Linux primitives; no consolidation possible without inventing custom wrapper. Sanctioned by analyst Open Q #1 option C recommendation. |
| **F5** | Multi-LLM routing | **N/A** | No LLM в BW-layer (per F1). |
| **F6** | Threat-model placeholder | top-3 enumerated §7; full 22+ rows → `spec/threat-model_wtiso-bw.md` | Top-3: (1) cross-tenant data exfil [T2/I1/I2 sev-5]; (2) fork-bomb PID exhaustion [D1 sev-4]; (3) NoSandbox silent fallback [D3 sev-4]. |
| **F7** | RAG? | **No** | Pure deterministic isolation pipeline. No knowledge retrieval. |

---

## §3 Implementation plan — 4-step orchestration pipeline

Каждый worker spawn идёт через 4-stage wrap composition. Outer-to-inner: `systemd-run --scope` → `bwrap` namespace → `prlimit` rlimit caps → exec `claude -p ARGS`. Step 1 (version assertion) runs once per batch at boot; Steps 2-4 — per worker.

### 3.1 Step 1 — bwrap version assertion (boot, once per batch)

| Aspect | Decision |
|---|---|
| When | At batch start, BEFORE `_prepare_worktrees` (WT-layer §3.1). Single check per batch, result cached в batch state file. |
| Command | `bwrap --version` → parse `^bubblewrap (\d+\.\d+\.\d+)$` |
| Floor | **≥0.6.0** (rationale: `--bind-try` + `--ro-bind-try` since 0.6.0; `--die-with-parent` since 0.8.0 stability — see §11 Open Q #2 implementer can elect to bump к 0.8.0) |
| Override | `BMAD_BWRAP_MIN_VERSION=X.Y.Z` env (test scenarios only — production should not override) |
| Fail behavior | Hard-fail exit **78** (`BMAD_SANDBOX_REQUIRED_BUT_UNAVAILABLE`) + audit event `bwrap_version_floor_failed` |
| Bypass | `BMAD_ALLOW_NOSANDBOX=1` operator opt-in → fallback path (see §3.4.2 NoSandbox policy) |

**Rationale for floor=0.6.0:** Virgil sandbox.py uses `--bind-try` + `--ro-bind-try` (introduced 0.6.0, see §1 Edit-1 row #7 NOTE — missing host paths skip silently). Bumping к 0.8.0 catches `--die-with-parent` race-class CVEs (D4 sev-4) but excludes older distros. Implementer decision Open Q §11 #2.

### 3.2 Step 2 — per-worker overlay preparation

| Aspect | Decision |
|---|---|
| When | Per worker, BEFORE bwrap exec |
| Default strategy | **Eager `cp -R`** (`copy` mode): `cp -R ~/.claude /tmp/888-bat-<batch-id>/overlays/<q-id>/.claude && cp ~/.claude.json /tmp/888-bat-<batch-id>/overlays/<q-id>/.claude.json` |
| Opt-in alternative | `BMAD_OVERLAY_MODE=bind` (read-only bind-mount; no isolation от concurrent host writes — for read-heavy workloads where copy cost dominates) |
| Future alternative | `BMAD_OVERLAY_MODE=overlayfs` (CoW; requires CAP_SYS_ADMIN OR `/proc/sys/kernel/unprivileged_userns_clone=1`; deferred — see §11 Open Q #3) |
| Path layout | `/tmp/888-bat-<batch-id>/overlays/<q-id>/{.claude,.claude.json}` (mirrors WT-layer §3.1 layout `/tmp/888-bat-<batch-id>/worktrees/<q-id>/`) |
| Cleanup | Post-batch (mirror WT cleanup §4fl `_cleanup_worktrees`) — new helper `_cleanup_overlays`. Failure handling = warn-and-continue (operator can `rm -rf /tmp/888-bat-*` manually) |
| NOT copied | `~/.local/share/claude/` (binary install — stays host-bound per sandbox.py:520-522). Empty overlay would shadow binary → `bwrap: execvp .../claude: No such file or directory` |

**Why eager copy default vs bind:** isolation от concurrent host writes (operator может editor'ить `~/.claude/CLAUDE.md` пока batch бежит); reproducibility (structural identity baseline AC5 requires stable snapshot). Trade-off: ~50-200ms per worker для `cp -R` (acceptable для M-tier batches).

**Concurrent-overlay safety (edge-case-hunter HIGH F6 fix):** N workers starting within 100ms parallel-batch → N concurrent `cp -R ~/.claude` reads. If operator mid-saves `~/.claude/<sqlite>.db-wal` OR holds exclusive lock на host SQLite → partial-copy hash mismatch (AC5 flakes nondeterministically) OR corrupt per-worker SQLite snapshot. **Implementation MUST:**

1. **Single per-batch snapshot:** `_prepare_overlays` создаёт ONE master snapshot `/tmp/888-bat-<batch-id>/master-snapshot/.claude/` ОДНОКРАТНО (under flock на `~/.claude/.batch-snapshot.lock`) BEFORE worker fan-out.
2. **Per-worker fast copy** от master: `cp -R /tmp/888-bat-<batch-id>/master-snapshot/.claude /tmp/.../overlays/<q-id>/.claude` (local FS copy, не network; no concurrent ~/.claude reads).
3. **Master snapshot validation:** post-copy `sha256sum` of snapshot tree files → record в batch state file. Audit event `master_snapshot_taken` с hash для AC5 reproducibility.

**Crash recovery (edge-case-hunter HIGH F8 fix):** дispatcher `trap EXIT INT TERM` wires `_cleanup_overlays` для current-batch dir. Boot-time scan `/tmp/888-bat-*` older than 24h (`find -mtime +1`) → sweep + audit `stale_overlay_swept` per dir. Prevents disk-fill on crash-prone dev.

**PATH_MAX edge (edge-case-hunter MED F16):** pre-flight check `find ~/.claude -maxdepth 16 -name "*" | awk 'length > 3500'` → warn если deep paths (PATH_MAX=4096; overlay prefix `/tmp/888-bat-XXXX/overlays/Q-NNN/` adds ~50 chars).

### 3.3 Step 3 — sandbox wrap composition (option C subcommand)

| Aspect | Decision |
|---|---|
| Strategy | **Option C** (analyst recommendation §4fw Edit-optional F8): reuse existing Virgil `runtime/sandbox.py` via `python -m bmad_orchestrator.runtime.sandbox wrap`. Zero translation, zero 2-language drift, single source-of-truth. |
| Bash wrapper | New helper `_spawn_worker_isolated` в `~/.claude/skills/888/scripts/888-batch.sh` (NOT modify original `_spawn_worker` — original остаётся в sequential-mode path под `BATCH_PARALLEL_ENABLED=0`) |
| Invocation contract | См. §4 Option C subcommand contract |
| Composition order | systemd-run (outer cgroup) → python -m bmad_orchestrator.runtime.sandbox wrap (bwrap namespace + prlimit + env clearenv + overlay binds + blackouts + `--unshare-net`) → exec `claude -p ARGS` (inner worker) |

**Bash glue location:** `~/.claude/skills/888/scripts/888-batch.sh` `_spawn_worker` (lines 335-459) wrap'ится в новый `_spawn_worker_isolated` helper. Branching:

```bash
if [[ "${BATCH_PARALLEL_ENABLED:-0}" == "1" && "${BMAD_SANDBOX:-bwrap}" != "none" ]]; then
    _spawn_worker_isolated "$@"   # new BW path
else
    _spawn_worker "$@"            # original sequential path (unchanged)
fi
```

### 3.4 Step 4 — cgroup wrap (`systemd-run --user --scope`)

#### 3.4.1 Cgroup configuration (matches sandbox.py:109-116)

| Limit | Value | Purpose |
|---|---|---|
| `MemoryMax` | `8G` (env override `BMAD_SANDBOX_MAX_AS_BYTES`) | Per-worker memory ceiling |
| `CPUQuota` | `200%` (env override TBD, default sandbox.py:110) | 2-core equivalent |
| `TasksMax` | `16384` (env override TBD) | Per-worker PID/thread cap; closes D1 fork-bomb |

#### 3.4.2 Cgroup availability + NoSandbox fallback policy (§4fw Edit-4 firm commit)

| Scenario | Policy | Audit event |
|---|---|---|
| `systemd-run --user --scope` healthy (user systemd manager running, `XDG_RUNTIME_DIR` set + exists per sandbox.py:119-130) | Full sandbox + cgroup (default path) | — |
| `systemd-run` missing OR no user systemd manager | Fallback к prlimit-only mode + audit warn `cgroup_unavailable_fallback_prlimit` | `cgroup_unavailable_fallback_prlimit` |
| `BMAD_REQUIRE_CGROUP=1` set (existing flag sandbox.py:99-117) + cgroup unavailable | **Hard-fail exit 78** | `cgroup_required_but_unavailable` |
| `bwrap` missing OR version <floor (Step 1) OR healthcheck fails | **Hard-fail exit 78** (`BMAD_SANDBOX_REQUIRED_BUT_UNAVAILABLE`) | `bwrap_version_floor_failed` OR `sandbox_hard_fail_no_bwrap` |
| `BMAD_ALLOW_NOSANDBOX=1` operator opt-in | **Force-sequential** (`BATCH_PARALLEL_ENABLED=0` overridden) + loud stderr warn + audit `sandbox_fallback_nosandbox` per worker. Sequential is non-negotiable under opt-out. | `sandbox_fallback_nosandbox` (per worker) |
| `BMAD_SANDBOX=none` explicit | Alias к `BMAD_ALLOW_NOSANDBOX=1` | same as above |

**Precedent:** `BMAD_REQUIRE_CGROUP=1` env knob already exists в sandbox.py:99-117. `BMAD_ALLOW_NOSANDBOX` flips the default direction (hard-fail by default vs warn-and-continue) per CLAUDE.md Critical Boundaries §5 «primary safety теряется» principle.

---

## §4 Option C subcommand contract

### 4.1 CLI signature

```
python -m bmad_orchestrator.runtime.sandbox wrap \
    --worktree DIR \
    --network {none|github_only|full} \
    --allow-nosandbox {0|1} \
    [--overlay-mode {copy|bind|overlayfs}] \
    [--overlay-source DIR] \
    [--readonly-path PATH ...] \
    [--env KEY=VALUE ...] \
    -- ARGS...
```

### 4.2 Arguments

| Arg | Required | Default | Maps к sandbox.py |
|---|---|---|---|
| `--worktree DIR` | yes | — | `BwrapSandbox.wrap_command(worktree=DIR)` :539-572 |
| `--network {none\|github_only\|full}` | no | `none` (per analyst Edit-9) | `NetworkPolicy` :36 + `--unshare-net` toggle :539-541 |
| `--allow-nosandbox {0\|1}` | no | `0` | gates fallback behavior on Sandbox.detect failure :17 |
| `--overlay-mode {copy\|bind\|overlayfs}` | no | `copy` | controls per-worker `~/.claude` snapshot strategy :516-537 (current sandbox.py = bind only; new arg extends) |
| `--overlay-source DIR` | no | derived | path to overlay snapshot prepared в Step 2 (`/tmp/888-bat-<batch-id>/overlays/<q-id>/`) |
| `--readonly-path PATH` (repeatable) | no | — | extra ro-binds (e.g., prod docs the worker may consult) :544-549 |
| `--env KEY=VALUE` (repeatable) | no | — | extra env vars passed through `--setenv` :555-569 |
| `-- ARGS...` | yes | — | worker command (typically `claude -p ARGS`) |

### 4.3 Exit codes

| Code | Meaning |
|---|---|
| `0` | Worker exited successfully (passed through) |
| `78` | Sandbox required but unavailable (bwrap missing OR version <floor OR healthcheck failed AND `--allow-nosandbox=0`). Standard sysexits.h `EX_CONFIG`. |
| `79` | **REMOVED per edge-case-hunter BLOCKER #5.** Original idea: «isolation bypass attempted при security_critical context» — но flag `BATCH_ISOLATION_ENABLED` нигде не определён end-to-end (Python sandbox.py не знает про Q-NNN security_critical bool; нет registered flag в §5 table). Kill-switch story теперь полагается на `BMAD_SANDBOX=none + BMAD_ALLOW_NOSANDBOX=1` combo (см. §5 + §3.4.2 precedence matrix). Exit 79 reserved для future use. |
| other | Worker exit code pass-through (e.g., 1 = worker failure, 130 = SIGINT) |

### 4.4 stdout / stderr / audit

| Stream | Content |
|---|---|
| stdout | Worker stdout pass-through (`claude -p` JSONL events stream) |
| stderr | Worker stderr pass-through + sandbox audit lines (JSON Lines format, distinguishable by `"event":"sandbox_*"` prefix). NoSandbox warn line prepended ДО worker exec. |
| audit/batches/<batch-id>/events.jsonl | Append 4 new event types (см. §6 schema). Schema_version="1" on каждом event. |

### 4.5 Implementation note

The `wrap` subcommand currently does NOT exist в sandbox.py — implementer Phase 2.5 adds it. Existing `BwrapSandbox.wrap_command` returns a list[str] suitable for `subprocess.run`; the CLI wrapper composes args, invokes `wrap_command`, then `os.execvp` (or `subprocess.run` if cleanup needed post-worker). Implementer Open Q §11 #4 covers schema versioning.

---

## §5 Feature flags + env vars (6 total)

| Flag | Default | Purpose | Source |
|---|---|---|---|
| `BMAD_SANDBOX` | `bwrap` | Backend selection: `bwrap` (default) / `systemd` (cgroup-only, no bwrap) / `none` (alias к `BMAD_ALLOW_NOSANDBOX=1`) | sandbox.py:19-20 existing; extended |
| `BMAD_ALLOW_NOSANDBOX` | `0` | Operator opt-in для hard-fail bypass (force-sequential mode). Analyst §4fw Edit-4 firm policy. | **NEW** |
| `BMAD_NETWORK_POLICY` | `none` | Network policy default. `none` = `--unshare-net`; `github_only`/`full` defer к WTISO-NET allowlist work. Analyst §4fw Edit-9. | **NEW** (maps к sandbox.py:36 `NetworkPolicy` enum) |
| `BMAD_REQUIRE_CGROUP` | `0` | Existing flag (sandbox.py:99-117). Если `1` + cgroup unavailable → hard-fail. Exposed unchanged. | sandbox.py:99-117 existing |
| `BMAD_OVERLAY_MODE` | `copy` | Overlay strategy: `copy` (eager `cp -R`, default) / `bind` (read-only bind-mount, opt-in) / `overlayfs` (CoW, future) | **NEW** |
| `BMAD_BWRAP_MIN_VERSION` | `0.6.0` | Version floor override. **CAN ONLY RAISE, NEVER LOWER** (edge-case-hunter HIGH F9 fix): implementation uses `max(env_value, "0.6.0")` to prevent typo / copy-paste from old test fixture downgrading floor below CVE-rationale. Mirror precedent: `_MIN_NPROC` floor logic (sandbox.py:88-91). | **NEW** |

**Existing flags carried forward без изменений** (sandbox.py:94-97): `BMAD_SANDBOX_MAX_NPROC`, `BMAD_SANDBOX_MAX_AS_BYTES`, `BMAD_SANDBOX_MAX_FSIZE_BYTES`, `BMAD_SANDBOX_MAX_NOFILE`. Below floors (sandbox.py:88-91) → ignored с warn.

### §5.1 Flag precedence matrix (edge-case-hunter F5/F7 fix — BLOCKER #4 spec edit)

Conflicting flag combos resolved per following matrix:

| Setting | `BMAD_REQUIRE_SANDBOX` | `BMAD_ALLOW_NOSANDBOX` | `BMAD_REQUIRE_CGROUP` | Outcome |
|---|---|---|---|---|
| Default | 0 (unset) | 0 (unset) | 0 | Best-effort bwrap; warn if missing |
| Strict prod | 1 | 0 | 0 | Hard-fail if bwrap missing (exit 78) |
| Operator opt-in (analyst Edit-4) | 0 | 1 | 0 | Force-sequential; warn per worker |
| **CONFLICT REQUIRE+ALLOW** | 1 | 1 | * | **REQUIRE wins**, exit 78 + audit `policy_conflict_resolved_to_require` |
| Cgroup-strict | 0/1 | 0 | 1 | bwrap OK без cgroup → exit с REQUIRE_CGROUP semantics (sandbox.py:99-117) |
| **CONFLICT ALLOW+CGROUP** | 0 | 1 | 1 | **ALLOW implies cgroup no-op** (cgroup в стеке sandbox); audit `cgroup_required_but_nosandbox_opt_in_skipping`, force-sequential proceeds |

Rationale: security_critical:true → safe-default = REQUIRE wins. Operator panic-paste cases (both REQUIRE+ALLOW set) must NOT silently succeed без isolation.

---

## §6 Audit event schema (4 new event types, schema_version="1")

All events appended к `audit/batches/<batch-id>/events.jsonl`. Each event = single line JSON object. `ts` = ISO-8601 UTC. `schema_version` enables migration plan (implementer Open Q §11 #4).

### 6.1 `sandbox_violation_blocked`

Worker attempted to access blackout path / exfil env var / fork-bomb. Detected at sandbox layer (bwrap mount denial, prlimit EAGAIN, cgroup TasksMax).

```json
{
  "ts": "2026-05-27T10:23:14.123Z",
  "event": "sandbox_violation_blocked",
  "schema_version": "1",
  "batch_id": "bat-260527-abc123",
  "worker_q_id": "Q-260527-EXAMPLE",
  "violation_type": "fs_read|env_exfil|fork_bomb|net_egress",
  "path_or_var": "/home/server/crm/orders.json",
  "outcome": "blocked",
  "detector": "bwrap_mount|prlimit_eagain|cgroup_tasks_max|clearenv"
}
```

### 6.2 `sandbox_fallback_nosandbox`

Operator opted into NoSandbox mode via `BMAD_ALLOW_NOSANDBOX=1` OR `BMAD_SANDBOX=none`. Emitted per worker (M5 outcome metric counts these).

```json
{
  "ts": "2026-05-27T10:23:14.123Z",
  "event": "sandbox_fallback_nosandbox",
  "schema_version": "1",
  "batch_id": "bat-260527-abc123",
  "worker_q_id": "Q-260527-EXAMPLE",
  "trigger": "bwrap_missing|version_floor_failed|operator_opt_in|systemd_unavailable",
  "host_state": {
    "bwrap_path": null,
    "bwrap_version": null,
    "systemd_run_path": "/usr/bin/systemd-run",
    "user_systemd_manager": false
  },
  "force_sequential": true,
  "warn_channel": "stderr+audit"
}
```

### 6.3 `bwrap_version_floor_failed`

Step 1 version assertion failed (boot-time check). Hard-fail unless `BMAD_ALLOW_NOSANDBOX=1`.

```json
{
  "ts": "2026-05-27T10:23:14.123Z",
  "event": "bwrap_version_floor_failed",
  "schema_version": "1",
  "batch_id": "bat-260527-abc123",
  "observed_version": "0.5.0",
  "required_floor": "0.6.0",
  "bwrap_path": "/usr/bin/bwrap",
  "outcome": "hard_fail_exit_78"
}
```

### 6.4 `cgroup_tasks_max_hit`

Worker hit TasksMax limit (D1 fork-bomb mitigation engaged). Signals successful defence.

```json
{
  "ts": "2026-05-27T10:23:14.123Z",
  "event": "cgroup_tasks_max_hit",
  "schema_version": "1",
  "batch_id": "bat-260527-abc123",
  "worker_q_id": "Q-260527-EXAMPLE",
  "tasks_max": 16384,
  "cgroup_scope": "888-batch-bat-260527-abc123-Q-260527-EXAMPLE.scope",
  "outcome": "worker_killed_signal_sigterm"
}
```

### 6.5 Schema versioning migration plan

| Version | Trigger | Migration |
|---|---|---|
| `"1"` | Initial (this spec) | — |
| `"2"+` | Field added/removed/renamed | Backward-compat reader: tolerate missing optional fields; new required fields default к null. Implementer adds `tools/audit-migrate.py` if needed. |

---

## §7 STRIDE summary

**Full table:** `/home/server/bmad-orchestrator/spec/threat-model_wtiso-bw.md` (separate deliverable, written parallel by another agent в Stage 2).

**Severity distribution (authoritative from `threat-model_wtiso-bw.md` §2 — 28-row STRIDE table; reconciled vs initial §7 estimate per Stage 2 spec writer note on >20% divergence escalation):**

| Sev | Count (authoritative) | Examples |
|---|---|---|
| **sev-5** (critical: cross-tenant blast / RCE / 152-ФЗ violation / host root) | **10** | T7 docker socket bind · I1 sibling FS read · I2 ~/.claude cross-worker · I3 env exfil · I6 DBUS keyring · D1 fork-bomb · D3 NoSandbox silent fallback · N1 namespace unshare · #15 worker_home_overlay · C2 overlayfs CAP_SYS_ADMIN |
| **sev-4** (high: single-tenant compromise / DoS / coverage gap) | **6** | T8 /tmp shared · I8 keyring runtime dir · D4 `--die-with-parent` orphan · #14 bwrap version drift · C1 bwrap CVE class · T9 bwrap CLI shell-injection |
| **sev-3** (medium: info leak / fingerprinting / partial) | **9** | T2 gitconfig poison · I4 /proc kernel-info · I5 /sys fingerprint · I7 ptrace cross-ns · D2-partial single-file disk · E3-residual git worktree add · #20 worker UID elevation · T11 FUSE mount · S2 audit-log spoof |
| **sev-2** (low: defence-in-depth) | **1** | T10 oom_score_adj tampering |
| **sev-1** | 0 | — |
| **Total** | **26** | (threat-model claims 28 rows; 2 entries не severity-numbered explicitly — see threat-model §2 PORT/NEW breakdown) |

**Closure status** (reconciled per edge-case-hunter F1 + F3, BLOCKER #3): **9 of 10 sev-5 closed** via ported sandbox.py primitives + Iron Law tests M0-M5 / AC2-AC5. **1 of 10 (T7 docker socket)** requires NEW boot-time assertion (`_assert_no_docker_group` + `/var/run/docker.sock` blackout) — implementation pending Phase 2.5; verified by RED test AC6 (`test-docker-group-assertion.sh`, committed Stage 3). 5 additional RED stubs (AC6-AC10) cover T7/C2/T11/D4/#20 sev-5/sev-4/sev-3 threats per edge-case-hunter BLOCKER #2. Residual gaps → 4 explicit Q-NNN routings (BW-DISK, BW-CPU, WTISO-NET, SH) + 3 ops Phase 4 runbook items (watchdog, CVE feed automation, quarterly bwrap review).

**Top-3 architectural attack vectors (§4fx Field 6 verbatim):**

1. **Cross-tenant data exfil через non-isolated worker** (T2/I1/I2 group, sev-5): worker `cat /home/server/crm/orders.json` ИЛИ `cat ~/.aws/credentials` без BW → leak в Anthropic API call → 152-ФЗ violation + cloud creds compromise. **Mitigation:** bwrap `--bind` scope только к worktree + `--tmpfs` overlay on 30 blackout paths (port from sandbox.py:419-467) + `--clearenv` + 10-var allowlist (sandbox.py:43-51) + per-worker `~/.claude` overlay (sandbox.py:499-517).
2. **Fork-bomb host PID exhaustion** (D1, sev-4): worker exec'ит `:(){ :|:& };:` → host PID saturation → ВСЕ batch'и + другие process на host die. **Mitigation:** cgroup `TasksMax=16384` via `systemd-run --user --scope -p TasksMax=16384` (preferred) + prlimit `RLIMIT_NPROC=16384` fallback (sandbox.py:80-91, per-UID limitation noted).
3. **NoSandbox silent fallback** (D3, sev-4): bwrap binary missing/version-fail → current orchestrator silently continues parallel → primary safety теряется без notification. **Mitigation:** analyst-committed firm policy (§4fw Edit-4): hard-fail exit 78 by default + `BMAD_ALLOW_NOSANDBOX=1` explicit opt-out + force-sequential under opt-out + audit event `sandbox_fallback_nosandbox` per worker. **Detection** (Phase 3 qa Driver): M5 outcome metric (zero `worker_done` events без opt-in).

---

## §8 Iron Law 10 RED tests

All test files committed `tests/wtiso-bw/` с `exit 1` placeholder bodies (or actual failing assertions) BEFORE implementer Phase 2.5 lands sandbox wrapper. Iron Law pressure-test hook (`scripts/iron-law-pressure-test.sh` if present) verifies RED commit → fix commit → GREEN sequence.

| # | Test path | Purpose | RED → GREEN condition |
|---|---|---|---|
| **M0** | `tests/wtiso-bw/test-baseline-canary-rate.sh` | Capture pre-BW baseline values для M1-M5 (no BW code yet → records unsafe behavior) | Runs `BATCH_PARALLEL_ENABLED=1 BMAD_SANDBOX=none` → outputs `evals/baselines/wtiso-bw-baseline-2026-05-27.json` per §4fw Edit-8 schema. PASS = file exists + 5 metric keys populated + seed=42 pinned. |
| **M1** | `tests/wtiso-bw/test-fork-bomb-blocked.sh` | D1 cgroup TasksMax enforcement | Worker exec'ит `:(){ :\|:& };:` × 3 runs. **RED:** peak host PIDs saturates kernel cap (~250K). **GREEN:** cgroup TasksMax engages, peak ≤ baseline + 16384 safety margin, audit event `cgroup_tasks_max_hit` × ≥1 |
| **M2** | `tests/wtiso-bw/test-cross-worker-fs-blocked.sh` | I1 sibling FS read blocked via bwrap `--bind` scope | Worker exec'ит `cat ../other-worktree/file` × 3 runs. **RED:** exit 0 (read succeeds). **GREEN:** exit≠0 (ENOENT/EACCES) на всех 3 |
| **M3** | `tests/wtiso-bw/test-env-secret-exfil-canary.sh` | I3 env exfil + positive control (§4fw Edit-5) | Spawning shell ОБЯЗАН `export ANTHROPIC_API_KEY=canary-deadbeef-2026 CLAUDE_OAUTH_REFRESH=canary-refresh-2026`. Worker `env \| grep -E '(ANTHROPIC\|CLAUDE_OAUTH\|canary-deadbeef\|canary-refresh)'` × 3. **RED:** matches on var names + values. **GREEN:** 0 matches × 3 (catches broken sandbox в clean CI shell that would trivially "pass" без positive control) |
| **M4** | `tests/wtiso-bw/test-orchestrator-state-read-blocked.sh` | I2 ~/.bmad-orchestrator/ blackout. NOTE: `~/.claude/` uses overlay, NOT blackout. | Worker `cat ~/.bmad-orchestrator/state.db` × 3. **RED:** read OK. **GREEN:** ENOENT/EACCES × 3. Separate check: `echo x > ~/.claude/.session-mutation` → должен land в overlay copy, NOT в host `~/.claude/` (verify via overlay-copy diff post-batch). |
| **M5** | `tests/wtiso-bw/test-nosandbox-outcome.sh` | D3 hard-fail OR opt-in force-sequential (§4fw Edit-6 outcome reframe) | Run-1: `BMAD_SANDBOX=none` (no opt-in) × 3 iter → assert exit=78 + 0 `worker_done` events. Run-2: `BMAD_ALLOW_NOSANDBOX=1 BMAD_SANDBOX=none` × 3 iter → assert exit=0 + N `worker_done` events sequentially (NOT parallel — sequential forced). **RED:** parallel batch reaches `worker_done` без opt-in. **GREEN:** both runs match policy. |
| **AC2** | `tests/wtiso-bw/test-bwrap-version-floor.sh` | Version assertion ≥0.6.0 (Step 1) | Mock bwrap stub returning `bubblewrap 0.5.0` → invoke `python -m bmad_orchestrator.runtime.sandbox wrap ...`. **RED:** silent acceptance. **GREEN:** exit 78 + audit `bwrap_version_floor_failed{observed_version:"0.5.0",required_floor:"0.6.0"}` |
| **AC3** | `tests/wtiso-bw/test-home-overlay-readonly.sh` | Overlay default `copy` mode — writes contained, host `~/.claude/` untouched | Worker `echo PWNED > ~/.claude/CLAUDE.md` → verify post-batch: (a) host `~/.claude/CLAUDE.md` unchanged (sha256 match pre/post); (b) overlay copy contains "PWNED"; (c) overlay cleaned by `_cleanup_overlays`. **RED:** host file modified. **GREEN:** containment verified. |
| **AC4** | `tests/wtiso-bw/test-nosandbox-fallback-handles.sh` | Hard-fail default + opt-in force-sequential paths both exercised | Same as M5 but ALSO asserts: (a) stderr "primary safety теряется" warn line per worker; (b) `sandbox_fallback_nosandbox` audit event with `trigger:operator_opt_in` + `force_sequential:true`; (c) zero `BATCH_PARALLEL_ENABLED` honoured under opt-in. |
| **AC5** | `tests/wtiso-bw/test-sequential-mode-byte-identical.sh` | sha256 byte-identical audit log under sequential (§4fw Edit-7 reframe) | Run pre-BW (`BATCH_PARALLEL_ENABLED=0`, no BW code path) capturing `audit/batches/<bid>/events.jsonl`. Land BW. Run post-BW same inputs (queue + env + seed=42) → assert `sha256sum < pre-events.jsonl == sha256sum < post-events.jsonl`. **RED:** non-deterministic ordering OR BW leaks event types into sequential path. **GREEN:** exact byte match. |

### 8.1 Baseline JSON schema (§4fw Edit-8 verbatim)

Baseline path: `evals/baselines/wtiso-bw-baseline-2026-05-27.json` (operator runs once pre-BW via M0).

```json
{
  "schema_version": "1",
  "baseline_run_id": "<uuid>",
  "host_kernel": "<uname -r>",
  "bwrap_present": false,
  "seed": 42,
  "metrics": {
    "M1": {"name": "fork_bomb_peak_pid", "value": 0, "unit": "host PIDs", "sample_size": 3, "method": "bash :(){ :|:& };: × 3 + ps -eu watcher"},
    "M2": {"name": "cross_worker_fs_read_exits", "value": [0,0,0], "unit": "exit codes", "sample_size": 3, "method": "cat ../other-worktree × 3"},
    "M3": {"name": "env_secret_matches", "value": [0,0,0], "unit": "match count", "sample_size": 3, "method": "env | grep canary × 3 (positive control)"},
    "M4": {"name": "home_env_read_exits", "value": [0,0,0], "unit": "exit codes", "sample_size": 3, "method": "cat ~/.bmad-orchestrator/state.db × 3"},
    "M5": {"name": "nosandbox_audit_events", "value": 0, "unit": "event count", "sample_size": 1, "method": "BMAD_SANDBOX=none → grep sandbox_fallback events"}
  }
}
```

### 8.2 `diff-baselines.sh` contract

| Aspect | Spec |
|---|---|
| Path | `tests/wtiso-bw/diff-baselines.sh` |
| Input | `baseline.json` (pre-BW) + `post.json` (post-BW) as `$1 $2` |
| Output | exit 0 if все 5 targets met (per §4fw Edit-8 «target deltas»); exit 1 otherwise |
| Stdout | markdown table per-metric delta `Baseline → Post (target) [OK\|FAIL]` |
| Stderr | per-FAIL metric — concrete remediation hint |
| Reproducibility | seed pinned `42` (synthetic worker random seed), kernel version recorded в schema для debugging cross-host drift |
| Target deltas | M1: peak-PID-delta = `baseline_peak - 16384` ≥ 0 (TasksMax cap engaged). M2-M4: matches_or_reads = `baseline_count → 0` (на ВСЕХ 3 sample iterations). M5: events_emitted = `baseline_0 → ≥1 per run` AND batch exit code matches policy (78 без opt-in, 0 + force-sequential под opt-in). |

---

## §9 Acceptance criteria (§4fw v2-revised verbatim)

| AC | Statement | Test ref |
|---|---|---|
| **AC1** | Aggregate canary suite committed `tests/wtiso-bw/canaries/` GREEN после BW landed (≥3 of 5 M1-M5 canaries pass per flake3-runs convention; baseline RED до). Tests are M1-M5; AC1 = aggregate pass gate, not separate test. | §8 M1-M5 |
| **AC2** | `tests/wtiso-bw/test-bwrap-version-floor.sh` GREEN — boot-time bwrap version <floor (0.6.0 default) → hard-fail с exit 78 + audit event `bwrap_version_floor_failed`. Boolean assertion на observed_version < required_floor. | §8 AC2 |
| **AC3** | `tests/wtiso-bw/test-home-overlay-readonly.sh` GREEN — worker write attempts contained в overlay copy, host `~/.claude/` byte-identical pre/post (sha256). Default overlay mode = `copy` per §3.2. | §8 AC3 |
| **AC4** | `tests/wtiso-bw/test-nosandbox-fallback-handles.sh` GREEN — bwrap binary moved away → hard-fail exit 78 (default) OR `BMAD_ALLOW_NOSANDBOX=1` set → force-sequential + ≥1 audit warn `sandbox_fallback_nosandbox` per worker + ≥1 stderr "primary safety теряется" line per worker. | §8 AC4 |
| **AC5** | `tests/wtiso-bw/test-sequential-mode-byte-identical.sh` GREEN — `sha256sum < audit/batches/<batch-id>/events.jsonl` pre-BW vs post-BW sequential runs идентичен (100% byte match). Verifies BW does not leak behavior into sequential path. | §8 AC5 |

**Phase 3 qa Driver** verifies AC1-AC5 + flake3-runs convention (≥3 consecutive GREEN per canary) + reads `diff-baselines.sh` output.

---

## §10 Out-of-scope (defer table)

| Item | Defer to | Why deferred |
|---|---|---|
| Methodology shards (per-Q patch counter, per-Q merge gate, rollback contract) | Q-260527-WTISO-SH | Separate orchestration concern, distinct file set; tracked в §4fj umbrella split |
| Inner-worker LLM tool-use restriction | Claude Code harness config | OS-sandbox protects FS/PID/env, NOT tool semantics; harness owns tool allowlist |
| Network egress allowlist (github.com/api.anthropic.com/specific MCP) | Q-260527-WTISO-NET (future, if proven needed) | BW commits `network="none"` default; allowlist parsing + DNS pinning = separate work |
| Aggregate disk-fill via cgroup IO quota | Q-260527-WTISO-BW-DISK (follow-up if proven) | Phase 1 minimum = TasksMax + per-file RLIMIT_FSIZE; aggregate quota requires cgroup v2 io.max + per-mount sizing |
| Per-worker cgroup CPU quotas (beyond default 200%) | Q-260527-WTISO-BW-CPU (follow-up if proven) | TasksMax + MemoryMax sufficient для Phase 1 baseline; CPU contention unobserved до production load |
| Replacing `_scan_bash` pattern matcher entirely | stays as defence-in-depth (sandbox.py:11-13 explicit) | Complementary, not replacement; pattern matcher catches known cases + logs suspicious даже under BW |
| Overlayfs CoW mode default | future (after CAP_SYS_ADMIN/`unprivileged_userns_clone` audit) | `copy` mode default достаточен для Phase 1; CoW requires kernel ≥3.18 + capability audit |
| CVE monitoring automation (distro-CVE feed subscription) | ops Phase 4 ramp deliverable | Orthogonal к isolation pipeline; runbook + manual review acceptable Phase 1 |

---

## §11 Open Q for implementer (Phase 2.5) — 6 items verbatim §4fx

1. **systemd-run fallback ordering:** prefer `systemd-run --user --scope` (cgroup ownership), fallback к prlimit-only? Or hard-require cgroup и fail если нет user systemd manager? (Analyst Open Q #4 deferred к architect; architect defers к implementer experimentation. **Architect recommendation:** prefer `systemd-run`; fallback к prlimit-only с audit warn `cgroup_unavailable_fallback_prlimit`; hard-require только под `BMAD_REQUIRE_CGROUP=1`.)
2. **Version check timing:** check bwrap version at first-worker spawn (cached) ИЛИ per-worker (slow но catches mid-batch reinstall)? **Architect recommendation:** at batch boot (once), cached в `/tmp/888-bat-<batch-id>/sandbox-version.cache` for batch lifetime. Mid-batch reinstall = operational anti-pattern, not security concern.
3. **Overlay snapshot strategy:** `cp -R ~/.claude /tmp/.../overlay/<q-id>/.claude` (eager copy, slow для large dirs) vs bind-mount read-only (no isolation от concurrent host writes) vs overlayfs CoW (requires CAP_SYS_ADMIN)? **Architect recommendation:** `copy` default per §3.2; `bind` opt-in via `BMAD_OVERLAY_MODE=bind`; `overlayfs` deferred. Implementer benchmarks `cp -R` cost; if >500ms per worker, escalate.
4. **Audit-event schema versioning:** `schema_version: "1"` field on новые 4 event types; migration plan если schema bumps? **Architect recommendation:** §6.5 plan — backward-compat reader tolerates missing optional fields; new required fields default к null; implementer adds `tools/audit-migrate.py` if forward-incompatible bump needed.
5. **NoSandbox warning channel:** stderr (visible interactively) + audit JSONL (machine-readable) + ???: dedicated Sentry alert? Slack hook? **Architect defers к ops Phase 4 ramp decision.** Implementer commits stderr + audit; ops adds Sentry/Slack post-ramp if signal noisy.
6. **bwrap `--die-with-parent` race mitigation** (NEW v2 STRIDE D4): watchdog process polling parent PID + SIGTERM children if parent gone? **Architect recommendation:** `--die-with-parent` flag itself (bwrap 0.8.0+) — implementer decision Open Q #2 on whether floor=0.6.0 (skip `--die-with-parent` reliance) OR floor=0.8.0 (use flag, no watchdog needed). If floor=0.6.0 chosen, implementer adds bash trap'ы handling SIGTERM propagation к child PGID.

### §11.1 Additional Open Q (edge-case-hunter required edits #9)

7. **User-namespace unshare prerequisites (BLOCKER closure для T7/T11/#20):** sandbox.py:492-497 currently does `--unshare-pid/uts/ipc/cgroup-try` BUT NOT `--unshare-user-try`. Adding user-ns unshare requires `/proc/sys/kernel/unprivileged_userns_clone=1` (kernel config). **Architect recommendation:** implementer adds `--unshare-user-try` (graceful — flag with `-try` suffix silently skips если kernel disabled); boot-time check value of `/proc/sys/kernel/unprivileged_userns_clone` → если 0, audit warn `user_namespace_unshare_unavailable` + falls к non-user-isolated mode (T7 mitigated by `_assert_no_docker_group` boot check вместо). RED test: AC10 `test-uid-namespace-unshare.sh`.

8. **Stale `/tmp/888-bat-*` cleanup на dispatcher crash:** spec §3.2 documents boot-time sweep older-than-24h. **Implementer decisions:** (a) sweep threshold: `find -mtime +1` OK, or shorter для CI/dev? (b) cleanup safety: hard `rm -rf` или `mv` к `/tmp/888-bat-stale/`? (c) sweep frequency: only at batch boot, or also via cron/timer? **Architect recommendation:** `find -mtime +1 -prune -exec rm -rf {} \;` at batch boot only (cron deferred к ops Phase 4); audit `stale_overlay_swept` per dir.

9. **Flag precedence enforcement (§5.1 matrix):** spec defines REQUIRE/ALLOW/CGROUP precedence. **Implementer decisions:** (a) where в code precedence check runs? `option C subcommand` boot OR caller-side в bash? (b) what если new flag added в future — extending precedence matrix systematically? **Architect recommendation:** precedence check in option C subcommand at startup (single source of truth); future flags get explicit row addition in §5.1 + corresponding sandbox.py constant. Backward-compat: missing flag defaults к `0` (safe).

---

## §12 Edge-case-hunter findings

**Placeholder.** Architect Stage 4 invokes `bmad-review-edge-case-hunter` via Agent faithful-substitute R7 over this spec + §6 audit schema + §8 RED tests + §11 open Q. Findings appended here per Phase-gate PASS/FAIL decision.

**Expected coverage targets** (per edge-case-hunter method):
- Boundary conditions: bwrap version=0.5.99 (just below floor), TasksMax=16383 (just below cap), overlay path с unicode chars / spaces / symlinks
- Concurrent failure modes: бatch race на overlay prepare (10 workers `cp -R` одновременно), `_cleanup_overlays` partial failure → orphan `/tmp/888-bat-*` dirs
- Operator misconfiguration: `BMAD_ALLOW_NOSANDBOX=1` + `BMAD_REQUIRE_CGROUP=1` (contradictory), `BMAD_OVERLAY_MODE=overlayfs` без CAP_SYS_ADMIN
- Composition risks: WT-layer worktree path containing chars bwrap CLI misparses; systemd-run scope name collision если 2 batches с same batch-id
- Compliance regression: 152-ФЗ audit trail requires positive evidence что blackouts engaged per Q-NNN (not just absence of violation)

---

## §13 Document control

| Stage | Status | Output |
|---|---|---|
| Stage 1 (7-field + outline) | ✅ done 2026-05-27T05:55Z | methodology §4fx |
| Stage 2 (spec write — this document) | 🟡 done this turn | `spec/spec_wtiso-bw.md` |
| Stage 2 (threat-model write) | 🟡 parallel agent | `spec/threat-model_wtiso-bw.md` |
| Stage 3 (10 RED tests commit) | ⬜ next architect turn | `tests/wtiso-bw/*.sh` placeholders |
| Stage 4 (edge-case-hunter review) | ⬜ after Stage 3 | §12 fill + Phase-gate PASS/FAIL |
| Phase 2.5 (implementer) | ⬜ after Stage 4 PASS | sandbox.py `wrap` subcommand + 888-batch.sh `_spawn_worker_isolated` |
| Phase 3 (qa) | ⬜ after impl | AC1-AC5 verification + STRIDE coverage audit |
| Phase 4 (ops ramp) | ⬜ after qa PASS | feature flag rollout + NoSandbox warn channel decision (Open Q #5) |
| Phase 5 (improver retro) | ⬜ after Phase 4 | metric review + follow-up Q-NNN parking |

**Verification debt explicit (architect Stage 2):**
- ⚠ analyst v3 round-3 reviewer waived — если implementer finds residual evidence issue в §1.1 cross-tenant categories (especially sandbox.py:436-439 gitconfig + :463-466 orchestrator state + :499-517 overlay), re-check ДО code-gate.
- ⚠ `wrap` subcommand does NOT exist в sandbox.py currently — implementer Phase 2.5 must add it. Existing `BwrapSandbox.wrap_command(worktree, network, env, readonly_paths, worker_home_overlay)` returns list[str]; CLI wrapper composes args + invokes + `os.execvp`.
- ⚠ STRIDE row count в §7 («22 + 3-6 architect adds → 25-28») = estimate. Authoritative count comes from `threat-model_wtiso-bw.md` parallel deliverable. If divergence >20%, escalate ДО Phase 2.5.
- ⚠ Effort estimate revised к ~6-8h (от analyst F18 callout «972 LOC port + 10 tests + canary infra under-scoped at ~4h»). Implementer should split into ≥2 sessions if needed.

---

## §14 Session Plan (Phase 2.5 implementer — auto-loop-spec-long)

Bootstrap target: 6 sessions, integration branch `integration/wtiso-bw`, fresh `claude -p` per session (Runtime=loop_wrapper, delay=300s). Auto merge=false — user reviews integration branch и merges manually на main.

### Session breakdown

| ID | Title | Surface | Spec section | Acceptance | Depends on | Destructive actions |
|---|---|---|---|---|---|---|
| S1 | `wrap` subcommand scaffold + version assertion | backend-python | §3.1 + §4 | argparse CLI added; `bwrap_version_floor` check; M0 baseline GREEN; AC2 (version floor) GREEN | — | [] |
| S2 | Per-worker overlay preparation (eager copy + concurrent safety) | backend-python | §3.2 | `_prepare_overlays` master snapshot under flock; per-worker fast copy; M2 + M4 + AC3 GREEN | S1 | [] |
| S3 | cgroup wrap + NoSandbox fallback policy | backend-python | §3.4 | `systemd-run --user --scope` integration; BMAD_REQUIRE_CGROUP + BMAD_ALLOW_NOSANDBOX flag handling; M1 + M5 GREEN | S2 | [] |
| S4 | bash glue `_spawn_worker_isolated` + env clearenv | mixed | §3.3 + §5 | new helper в `~/.claude/skills/888/scripts/888-batch.sh`; env allowlist (10 vars from sandbox.py:43-51); M3 (env exfil) + AC4 + AC5 GREEN | S3 | [] |
| S5 | Audit event schema (4 new types) + sev-5 RED tests (AC6-AC10) | backend-python | §6 + §8 | `sandbox_violation_blocked`, `sandbox_fallback_nosandbox`, `bwrap_version_floor_failed`, `cgroup_tasks_max_hit` events с schema_version="1"; AC6-AC10 GREEN | S4 | [] |
| S6 | Open Q resolution + bmad-code-review + bmad-security-review + retro | mixed | §11 + §11.1 + §12 | 9 Open Q resolved explicitly; bmad-code-review 3 hunters PASS; bmad-security-review 4 hunters PASS; methodology §4xx retro entry | S5 | [] |

**surface_rationale (S4):** bash `_spawn_worker_isolated` ~150 LOC + Python sandbox.py integration ~100 LOC + RED test wiring ~80 LOC — каждый ≥20% of session work.

**surface_rationale (S6):** review skill invocations via Agent ~40% + Open Q decision documentation ~30% + retro markdown ~30%.

### Multi-repo note

Implementation spans 2 repos:
- **bmad-orchestrator** (this repo, integration branch lives here): `src/bmad_orchestrator/runtime/sandbox.py` — primary impl (~80% of code)
- **`~/.claude/skills/888/`** (external skill repo): `scripts/888-batch.sh` (S4 bash glue) + `scripts/tests/wtiso-bw/*.sh` (15 RED stubs — GREEN happens here)

Auto-loop wrapper runs within bmad-orchestrator. Per-session work на `~/.claude/skills/888/` editing goes через `bash .claude/scripts/write-claude-file.sh` helper (harness-blocked otherwise). Commits в `~/.claude/skills/888/` repo делаются separately per session (separate git repo).

### Exit criteria (initiative-complete)

- All 6 sessions completed → integration branch `integration/wtiso-bw` ready
- 15 RED tests GREEN (verified via `bash ~/.claude/skills/888/scripts/tests/wtiso-bw/_runner.sh` or equivalent)
- bmad-code-review verdict PASS (3 hunters: Blind / Edge / Acceptance)
- bmad-security-review verdict PASS (4 hunters: Injection / Auth Bypass / Crypto / RLS Leak)
- methodology §4xx retro entry written
- Final Report в tracker с commit hashes + manual-merge hint

User receives PushNotification: «initiative wtiso-bw ready for manual merge. Review `integration/wtiso-bw` then `git merge --no-ff`.»
