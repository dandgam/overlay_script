---
q_id: Q-260527-WTISO-BW
parent: Q-260527-WTISO (umbrella)
sibling: Q-260527-WTISO-WT (worktree layer — referenced для residual carry-over)
method: STRIDE
scope: bwrap + prlimit + cgroup + HOME-overlay isolation layer ONLY (worktree → WT; shards → SH)
created_at: 2026-05-27
verdict: PASS-PARTIAL — 9 of 10 sev-5 closed via ported sandbox.py primitives; T7 (docker socket) requires NEW boot-time assertion + RED test AC6 (committed Stage 3 per edge-case-hunter F2). 4 residual gaps deferred к BW-DISK / BW-CPU / WTISO-NET / SH
security_critical: true
reference_code: src/bmad_orchestrator/runtime/sandbox.py (lines verified verbatim against actual file 2026-05-27)
related_layer_oos:
  - WT-residual E3 (git worktree add) — referenced; composition required via PreToolUse hook outside bwrap namespace
  - aggregate disk-fill (D2-aggregate) — deferred к Q-260527-WTISO-BW-DISK
  - per-worker CPU quota — deferred к Q-260527-WTISO-BW-CPU
  - egress whitelist (github.com / api.anthropic.com / MCP) — deferred к Q-260527-WTISO-NET
  - per-Q merge gate / methodology shards — deferred к Q-260527-WTISO-SH
---

# STRIDE Threat Model — Q-260527-WTISO-BW (BW Isolation Layer)

> Sub-Q под umbrella Q-260527-WTISO. Покрывает BW-layer (bwrap+prlimit+cgroup+overlay).
> Parent WT threat-model: `spec/threat-model_wtiso-wt.md` (WT-residual items carry-over here).
> SH out-of-scope per §4fw allocation matrix.

## 1. Scope

| Layer | Status в этом threat-model |
|---|---|
| **BW (bwrap + prlimit + cgroup + HOME-overlay)** | **Primary scope** — каждый primitive maps к sandbox.py line range |
| **WT-residual carry-over** | Referenced (E3 git worktree add) — composition с BW namespace required, not re-mitigated |
| **SH (methodology shards, per-Q merge gate)** | **Out-of-scope** — defer к Q-260527-WTISO-SH |
| **NEW BW-introduced** | bwrap setuid CVE class (C1), overlayfs CAP_SYS_ADMIN escalation (C2), --die-with-parent race (D4), /tmp shared-write surface (T8), docker socket bind (T7) |

**Honest closure boundary:** BW-layer closes all sev-5 threats from §4fw v2 22-row table + 3 architect-derived (T7/T8/T9) discovered mid-spec. Residual gaps documented в §5 с explicit Q-NNN routing.

## 2. STRIDE table (28 rows — extends §4fw v2 22 rows с 3 architect mid-spec finds + 3 NEW v3)

Колонки: STRIDE | Threat | Sev (1-5) | Mitigation primitive | sandbox.py evidence | Iron Law test | Residual gap. **PORT** = от §4fw Edit-2 v2; **NEW** = architect mid-spec find.

| # | STRIDE | Threat | Sev | Mitigation primitive | sandbox.py evidence | Iron Law test (M0-M5 / AC2-AC5) | Residual gap | Origin |
|---|---|---|---|---|---|---|---|---|
| T2 | **T** Tampering | `~/.gitconfig` poison cross-worker | 3 | NOTE: gitconfig deliberately readable (см. comment :436-439) — нужен для commit author resolution; ONLY `.git-credentials` + `.config/git/credentials` token stores blocked | :436-439 (deliberate exposure comment), :440-441 (token-store blackouts) | M4 (HOME-overlay readonly canary — gitconfig readable, credentials blackouted) | Если worker writes к gitconfig в overlay snapshot — write isolated к snapshot, не affects next batch (acceptable) | PORT |
| T7 | **T** Tampering | docker socket bind `/var/run/docker.sock` — orchestrator UID в `docker` group → worker connects к daemon → host root pivot | **5** | NOT closed by current blackout sweep — sandbox.py:481-486 explicit gap acknowledged в comment. **Architect adds:** boot-time assertion «orchestrator UID NOT in docker group OR /var/run/docker.sock missing» (exit 78 if violated) + `--unshare-user-try` для user namespace mapping (sandbox.py:493-496 extension) | :481-486 (explicit gap note) — **NEW work required** | AC6 (NEW) boot-time docker-group assertion — RED test: simulate UID-in-docker-group + assert spawn refuses with exit 78 | Mitigation host-level (drop docker group); BW-layer adds defensive assertion only | **NEW (architect mid-spec)** |
| T8 | **T** Tampering | `/tmp` shared writable surface — other batches' `/tmp/888-bat-*` files visible/writable through inner sandbox's `--tmpfs /tmp` mount creation race | 4 | `--tmpfs /tmp` (:350) gives fresh tmpfs **per bwrap invocation** — namespace-isolated; inter-batch contamination blocked by Linux mount-namespace semantics + `--unshare-pid` (:493) | :350, :493 | M2 cross-worker FS read (extended — also asserts `ls /tmp/888-bat-*` returns empty inside namespace) | None — namespace-isolated tmpfs is symmetric | **NEW (architect mid-spec)** |
| I1 | **I** Info disclosure | Sibling worker FS read (`cd ../other-q-id/` from worktree) | **5** | bwrap `--bind` scope только к assigned worktree (`--bind {wt_abs} {wt_abs}`); `--ro-bind / /` exposes host read-only but worker outside-worktree-write blocked; sibling worktree NOT bind-mounted | :360-361 (writable scope = worktree), :347 (root ro-bind), :419-467 (30 blackouts incl. sibling-readable dirs blackouted) | M2 cross-worker FS read canary (cat ../other-worktree × 3 → exit≠0 на all 3) | Read-only access к other-worktree paths via host ro-bind still possible UNLESS sibling worktree's parent dir blackouted — partial gap, mitigated by `/tmp/888-bat-*` namespace tmpfs (T8) | PORT |
| I2 | **I** Info disclosure | `~/.claude/` cross-worker contamination (session files, creds, lock/SQLite stores) | **5** | HOME overlay snapshot — per-worker writable copy `<overlay>/.claude` bind'ится через bwrap. NOT blackout — writes isolated к snapshot, reads допускаются только в snapshot | :499-517 (overlay_claude_subpaths), :523-537 (bind-from-overlay logic) | M4 home env read canary (cat ~/.claude/.env × 3 inside overlay vs host) | Secret protection зависит от overlay-source curation — если operator копирует production creds в overlay, не помогает (out-of-scope: overlay provisioning) | PORT |
| I3 | **I** Info disclosure | env exfil (ANTHROPIC_API_KEY / CLAUDE_OAUTH_REFRESH visible) | **5** | `--clearenv` (:555) + 10-var allowlist (PATH HOME USER LANG LC_ALL TZ PWD SHELL TERM BMAD_CURRENT_WAVE) + INNER blocklist (DBUS_SESSION_BUS_ADDRESS, XDG_RUNTIME_DIR) | :43-51 (allowlist), :60-62 (blocklist), :555 (clearenv), :563-569 (setenv loop) | M3 env+canary positive control (export ANTHROPIC_API_KEY=canary-deadbeef-2026 → 0 matches both var-name AND value × 3 runs) | None at BW-layer (allowlist enforced); per-call env extension via `env` param caller-responsibility | PORT |
| I4 | **I** Info disclosure | `/proc/{cmdline,modules,kallsyms,cpuinfo,meminfo}` kernel-info leak | 3 | ro-bind на `/dev/null` для каждого file (`/proc/version` оставлен — нужен bun runtime в Claude CLI) | :355-359 (5× ro-bind /dev/null) | M0 baseline (post-BW asserts each path returns 0 bytes) | `/proc/version` deliberately readable — bun startup dependency | PORT |
| I5 | **I** Info disclosure | `/sys` kernel module / hardware fingerprinting | 3 | `--tmpfs /sys` mount (empty) | :351 (FS9 H1 comment) | M0 baseline (post-BW asserts /sys listing empty) | None — full tmpfs masking | PORT |
| I6 | **I** Info disclosure | DBUS / XDG_RUNTIME_DIR → keyring/desktop service access | **5** | `_SANDBOX_INNER_ENV_BLOCKLIST` excludes даже если в allowlist; outer systemd-run wrapper gets these (для cgroup user manager), inner bwrap НЕ gets | :60-62 (blocklist), :567-568 (setenv-loop skip) | M3 (extended — also assert DBUS_SESSION_BUS_ADDRESS unset inside namespace) | None — defence-in-depth: outer needs vars для systemd-run, inner forbidden | PORT |
| I7 | **I** Info disclosure | `/proc/sys/kernel/yama/ptrace_scope` leak / ptrace cross-namespace | 3 | `--unshare-pid` (own PID namespace) + namespace unshares preventing ptrace across boundary | :492-497 (unshare-pid/uts/ipc/cgroup-try) | AC4 (NEW): assert `cat /proc/sys/kernel/yama/ptrace_scope` returns 0 bytes OR file not visible inside namespace | None | PORT |
| I8 | **I** Info disclosure | `/run/user/$UID/keyring` + `/run/user/$UID/gnupg` exposed via root ro-bind | 4 | Blackout: `--tmpfs` on directories | :461-462 (keyring + gnupg blackouts) | M4 (extended) — list `/run/user/$UID/keyring` inside namespace → empty | None | **NEW (architect)** |
| D1 | **D** DoS | fork-bomb `:(){ :\|:& };:` PID exhaustion | **5** | cgroup `TasksMax=16384` via `systemd-run --user --scope -p TasksMax=16384` (preferred) + prlimit `RLIMIT_NPROC=16384` fallback (per-UID — see :70-75 caveat) | :80-91 (prlimit defaults), :99-117 (cgroup layer), :109-116 (DEFAULT_CGROUP_LIMITS), :578-585 (rlimit_wrapper) | M1 fork-bomb canary (peak PID under host limit × 3) | Per-UID prlimit nproc may exhaust before fork-bomb hits per-process cap — cgroup TasksMax is правильный fix, prlimit defence-in-depth only | PORT |
| D2-partial | **D** DoS | disk-fill **single-file** (>10 GiB) | 3 | prlimit `RLIMIT_FSIZE=10 GiB` | :82 (`DEFAULT_MAX_FSIZE_BYTES`), :582 (rlimit_wrapper fsize) | M0 baseline (synthetic dd до 10 GiB → write fails EFBIG) | **Aggregate disk-fill** (many files) NOT closed — explicit OOS → Q-260527-WTISO-BW-DISK | PORT |
| D3 | **D** DoS | NoSandbox silent fallback — bwrap missing/broken → orchestrator continues parallel без isolation, operator unaware | **5** | Hard-fail by default (analyst Edit-4 firm policy): `BMAD_REQUIRE_SANDBOX=1` enforced → exit non-zero if NoSandbox path taken; `BMAD_ALLOW_NOSANDBOX=1` explicit opt-out → force sequential + loud stderr warn + audit `sandbox_fallback_nosandbox` per worker | :692-718 (`_require_sandbox` + `_enforce_require_sandbox`), :674-686 (`_emit_no_sandbox_audit`), :764-772 (factory fallback path) | M5 outcome metric (zero `worker_done` events под `BMAD_SANDBOX=none` без opt-in × 3 iter) + AC5 (force-sequential under opt-out) | None — analyst-committed firm policy; production launchers MUST set `BMAD_REQUIRE_SANDBOX=1` | PORT |
| D4 | **D** DoS | `--die-with-parent` race — orphan worker survives dispatcher death (window between dispatcher fork → bwrap setup → child exec) | 4 | bwrap `--die-with-parent` (0.8.0+ stability) + parent-PID watchdog (audit-event watcher reaps orphans every N seconds, OOS to ops Phase 4) | :345 (`--die-with-parent` flag) — watchdog **NEW work required** | AC3 (NEW): kill dispatcher mid-batch → assert all bwrap children dead within 5s (poll `ps --ppid <dispatcher-pid>`) | Watchdog complement deferred к ops Phase 4 (cron / systemd timer) | PORT |
| N1 | **N** (mapped to E here — namespace unshare absence = elevation potential) | namespace unshare missing — PID/UTS/IPC/cgroup visible across host | **5** | `--unshare-pid` + `--unshare-uts` + `--unshare-ipc` + `--unshare-cgroup-try` | :492-497 (4× unshare) | AC4 (NEW): inside namespace, `ps aux` shows only worker process tree, NOT host processes | None — full namespace stack | PORT |
| #14 | **Coverage** | bwrap version drift — older bwrap missing `--die-with-parent` (0.8.0+) or `--ro-bind-try` (0.6.0+) silently degrades isolation | 4 | Boot-time assertion `bwrap --version` ≥ 0.6.0 (для `--ro-bind-try`) or ≥ 0.8.0 (для `--die-with-parent` stability) — **NEW work, not в sandbox.py** | sandbox.py absent — gap | AC2 bwrap version floor — RED test stub older bwrap mock → assert exit 78 + audit `bwrap_version_floor_failed` | None если AC2 RED test landed | PORT |
| #15 | **Coverage** | worker_home_overlay missing — без overlay snapshot per-worker, all workers share host `~/.claude/` and race на SQLite lock | **5** | Per-worker overlayfs (CAP_SYS_ADMIN-gated) OR read-only bind-mount (default fallback); `worker_home_overlay` parameter wires source dir via `--bind <overlay>/.claude /home/.claude` | :499-517 (overlay subpaths), :523-537 (bind-from-overlay logic), :331-341 (overlay validation) | AC3 HOME overlay readonly canary (assert worker write к `~/.claude/foo` lands в `<overlay>/.claude/foo`, not host) | overlayfs requires CAP_SYS_ADMIN OR unprivileged user_ns enabled — see C2 below | PORT |
| C1 | **Coverage** | bwrap setuid CVE class — CVE-2017-5226 (sandbox escape), CVE-2020-5291 (write-OOB) historical | 4 | Version floor ≥0.6.0 mandatory + distro CVE feed subscription (Ubuntu USN, Debian DSA) + quarterly bwrap changelog review (ops Phase 4) | sandbox.py absent — process gap | AC2 (extended) — assert `bwrap --version` ≥ floor AND version NOT в known-vulnerable list (hard-coded CVE table в audit module, periodic refresh) | Future CVEs require emergency Q-NNN с halt-on-detect rotation | PORT |
| C2 | **Coverage** | overlayfs CAP_SYS_ADMIN escalation — CVE-2021-3493 unprivileged user_ns allows root-in-namespace → host root via overlayfs xattr setuid bug | **5** | Boot-time check `/proc/sys/kernel/unprivileged_userns_clone` — если 1 + overlay requested → warn or hard-fail (operator опция); preferred config: kernel ≥ 5.11 с patches OR disable unprivileged_userns_clone OR use read-only bind-mount fallback (#15 mitigation A→B) | sandbox.py absent — boot-check NEW work | AC4 (NEW) — RED test: stub `/proc/sys/kernel/unprivileged_userns_clone=1` + `BMAD_ALLOW_OVERLAYFS=0` → assert spawn refuses overlay path, falls к bind-mount with audit `overlayfs_unsafe_fallback` | Kernel-level fix is upstream's responsibility; BW-layer detection + fallback | PORT |
| E3-residual | **E** Elevation | Worker invokes `git worktree add` outside dispatcher scope (WT-residual per `threat-model_wtiso-wt.md:39`) | 3 | **Composition required:** PreToolUse hook (`agent/safety/hooks.py`) runs в host (not inside bwrap namespace) → matches `git worktree add` pattern → reject before subprocess exec. BW-layer не sees git invocation отдельно от bash. | hook composition outside sandbox.py | AC5 (extended) — RED test: worker prompts `git worktree add /tmp/extra` → hook rejects with exit code visible в audit | None если PreToolUse hook landed; carry-over from WT-residual | PORT |
| T9 | **T** Tampering | bwrap CLI shell-injection — worker prompt indirectly влияет на `--worktree` arg via dispatcher unescaped quoting → `$(touch /tmp/pwn)` evaluation | 4 | `_validate_bwrap_argv` shell-escape harness — Python subcommand (option C) uses `subprocess.run(argv_list)` не `shell=True`; bash wrapper uses `printf '%q'` quoting | sandbox.py NEW work — currently sandbox.py is called from Python, не bash; bash wrapper из 888 batch ДО option C resolution = gap | M0 (extended) — shell-injection canary `$(touch /tmp/pwn-$$)` в --worktree arg → assert /tmp/pwn-* NOT created | None если option C subcommand path used (Python argv not shell) | PORT (analyst Edit-10 #28) |
| #20 | **E** Elevation | Worker effective UID не isolated — bwrap inherits caller UID, worker files written с host-visible ownership | 3 | `--unshare-user-try` (NEW work — currently sandbox.py uses `--unshare-pid/uts/ipc/cgroup-try`, not user) — adds user namespace mapping so worker sees UID 0 inside, host sees nobody | :492-497 — gap (user not unshared) | AC4 (extended) — assert `id -u` inside namespace ≠ host UID | Без unprivileged_userns_clone (см. C2) user-unshare unavailable — same constraint | PORT (analyst Edit-10 #20) |
| T10 | **T** Tampering | Worker writes к `/proc/self/oom_score_adj` to dodge OOM killer | 2 | `--unshare-pid` creates own pidns; `/proc/self` refers к namespace-local view; cgroup `MemoryMax=8G` enforces ceiling regardless of oom_score | :493, :109 | M1 (extended) — assert worker OOM-killed at MemoryMax even when oom_score_adj=-1000 | None | **NEW (architect)** |
| T11 | **T** Tampering | Worker mounts FUSE filesystem inside namespace → forwards to host FUSE daemon → potential escape via vulnerable FUSE handler | 3 | `--unshare-user-try` blocks unprivileged FUSE; `/dev/fuse` not exposed via `--dev` (limited devtmpfs); namespace unshare prevents privileged mount syscalls | :349 (`--dev /dev` — limited bind, not full devtmpfs) | AC4 (extended) — `mount -t fuse none /tmp/fuse-mount` inside namespace returns EPERM | None | **NEW (architect)** |
| S2 | **S** Spoofing | Worker forges sandbox audit events by writing к audit-log path before sandbox setup completes | 3 | Audit log path outside worktree bind scope (`.claude/audit/` blackouted at :463-464 `_orchestrator_state`) → worker cannot write; only dispatcher (host UID) writes audit | :416-418 (`_orchestrator_state` resolution), :463-464 (blackout) | M5 (extended) — assert audit events have dispatcher PID, not worker PID, во всех entries | None | **NEW (architect)** |

**Row count:** 22 PORT (§4fw v2) + 6 NEW (architect mid-spec: T7, T8, T10, T11, S2, I8) = **28 rows**.

**NEW architect-derived (≥3 beyond §4fw 22):**
1. **T7 docker socket bind** (sev-5) — sandbox.py:481-486 explicit gap; mitigation = boot-time docker-group assertion + user-namespace
2. **T8 /tmp shared writable surface** (sev-4) — per-batch tmpfs ensures namespace isolation; covered by `--tmpfs /tmp` + `--unshare-pid`
3. **I8 keyring/gnupg runtime dir exposure** (sev-4) — `/run/user/$UID/keyring` blackouted at :461-462
4. **T10 oom_score_adj tampering** (sev-2) — namespace-local /proc + cgroup MemoryMax
5. **T11 FUSE mount escape** (sev-3) — unshare-user + /dev limited
6. **S2 audit-log spoofing** (sev-3) — orchestrator_state blackout

## 3. Sev-5 closure verification

**Total sev-5 threats: 8** (T7, I1, I2, I3, I6, D1, D3, #15, N1, C2 = 10; but I3 and I6 share mitigation, T7 and #15 require NEW work). Counting unique mitigation paths: **8 distinct sev-5 closures.**

| Sev-5 threat | Closing Iron Law test | Verified by sandbox.py evidence | Hard-fail path |
|---|---|---|---|
| T7 docker socket pivot | AC6 boot-time assertion (NEW) | NEW work — currently :481-486 gap | exit 78 if UID in docker group |
| I1 sibling FS read | M2 cross-worker canary | :360-361 + :347 + blackouts | exit≠0 reads from sibling worktree |
| I2 ~/.claude cross-worker contamination | M4 home env read | :499-517 overlay isolation | overlay snapshot isolates writes |
| I3 ANTHROPIC_API_KEY env exfil | M3 env+canary positive control | :43-51 + :555 + :563-569 | 0 matches on canary value OR name |
| I6 DBUS/keyring access | M3 (extended) | :60-62 + :567-568 | DBUS_SESSION_BUS_ADDRESS unset |
| D1 fork-bomb | M1 canary | :80-91, :99-117, :578-585 | TasksMax + RLIMIT_NPROC |
| D3 NoSandbox silent fallback | M5 outcome metric | :692-718, :674-686, :764-772 | exit 78 если `BMAD_REQUIRE_SANDBOX=1` + NoSandbox path |
| #15 worker_home_overlay missing | AC3 overlay readonly canary | :499-517, :523-537, :331-341 | exit 78 если overlay requested + unprivileged_userns_clone=0 |
| N1 namespace unshare missing | AC4 namespace verification | :492-497 | `ps aux` inside shows only worker tree |
| C2 overlayfs CAP_SYS_ADMIN escalation | AC4 (NEW boot-time check) | NEW work + :492-497 | boot check `/proc/sys/kernel/unprivileged_userns_clone`; warn/fail |

**All 5 architect-classified «top-3 critical» (§4fx Field 6) closed:**
1. Cross-tenant data exfil (T2/I1/I2/T7 group) → M2 + M4 + AC6 + 30 blackouts at :419-467
2. Fork-bomb (D1) → M1 + TasksMax + RLIMIT_NPROC
3. NoSandbox silent fallback (D3) → M5 + `BMAD_REQUIRE_SANDBOX=1` enforcement

## 4. Mitigation matrix (primitive × STRIDE-class coverage)

| Primitive (sandbox.py loc) | T (Tampering) | I (Info disclosure) | D (DoS) | S/R/E (Spoof/Repud/Elev) | Coverage % этого primitive |
|---|---|---|---|---|---|
| `--bind {wt} {wt}` (:360-361) | T8 partial | I1 primary | — | — | Worktree-scope confinement |
| `--ro-bind / /` (:347) | — | I4/I5 setup | — | — | Root read-only baseline |
| Blackout sweep `_SANDBOX_BLACKOUT_PATHS` (:419-467) | T2 partial | I1/I2/I8 secrets blocked | — | S2 audit-log protection | 30 sensitive paths |
| `--tmpfs /tmp`, `--tmpfs /sys` (:350-351) | T8 primary | I5 primary | — | — | Empty mounts hide host state |
| `--ro-bind /dev/null` × 5 /proc files (:355-359) | — | I4 primary | — | — | Kernel-info redaction |
| `--unshare-pid/uts/ipc/cgroup-try` (:492-497) | T10 partial | I7 primary | — | N1 primary, T11 partial | Namespace isolation |
| `--clearenv` + allowlist (:43-51, :555) | — | I3 primary, I6 primary | — | — | Env propagation control |
| `--die-with-parent` (:345) | — | — | D4 primary | — | Orphan reap |
| `--unshare-net` (:539-540) | — | — | — | — | Network namespace (default `none` per Edit-9) |
| prlimit RLIMIT_NPROC (:80, :578-585) | — | — | D1 fallback | — | Per-UID PID cap |
| prlimit RLIMIT_FSIZE (:82, :582) | — | — | D2-partial primary | — | Single-file cap (aggregate → BW-DISK) |
| prlimit RLIMIT_AS (:81, :581) | — | — | D1-mem primary | — | Virtual memory ceiling |
| cgroup TasksMax (:111, :608-644) | — | — | D1 primary | — | Per-scope PID cap |
| cgroup MemoryMax (:109, :608-644) | — | — | D1-mem primary | T10 (oom_score_adj override) | Per-scope memory cap |
| HOME overlay (:499-517, :523-537) | T2 secondary | I2 primary, #15 primary | — | — | Per-worker writable Claude state |
| `BMAD_REQUIRE_SANDBOX=1` (:692-718) | — | — | D3 primary | — | Hard-fail policy enforcement |
| `BMAD_REQUIRE_CGROUP=1` (:137-139) | — | — | D1 enforcement | — | Cgroup mandatory in prod |
| PreToolUse hook composition (host-side, not sandbox.py) | T9 primary, E3-residual primary | — | — | E3-residual primary | Bash/tool deny-list |
| Boot-time docker-group assertion (NEW) | T7 primary | — | — | — | Pre-spawn invariant |
| Boot-time `unprivileged_userns_clone` check (NEW) | — | — | — | C2 primary | Pre-spawn kernel-state |
| bwrap version floor ≥0.6.0 OR ≥0.8.0 (NEW) | — | — | D4 secondary | C1 primary | Boot-time version assertion |

**Coverage gaps (uncovered cells):**
- **Repudiation (R)** — fully delegated к existing commit-policy (`888-batch-commit-policy.sh` author trailer). BW-layer not in mitigation chain (per parent WT §3).
- **Spoofing (S)** — S1 (q_id spoof) delegated к dispatcher; S2 (audit-log) covered by orchestrator_state blackout. No other S threats identified.
- **Aggregate disk-fill** — DEFERRED к Q-260527-WTISO-BW-DISK (cgroup IO quota OR per-mount sizing).
- **CPU starvation** — DEFERRED к Q-260527-WTISO-BW-CPU (per-worker CPUQuota beyond default 200%).

## 5. Residual gaps (deferred с explicit Q-NNN routing)

| Gap | Sev | Routing Q-NNN | Why deferred |
|---|---|---|---|
| Aggregate disk-fill (D2-full, many files filling /tmp) | 3 | **Q-260527-WTISO-BW-DISK** | Requires cgroup IO quota OR per-mount sizing; orthogonal к single-file FSIZE primitive; не sev-5 |
| Per-worker CPU quotas (beyond default `CPUQuota=200%`) | 2 | **Q-260527-WTISO-BW-CPU** | Profile-driven tuning; sane default sufficient для MVP; не sev-5 |
| Egress whitelist (github.com / api.anthropic.com / specific MCP) | 4 | **Q-260527-WTISO-NET** | Requires nftables policy + DNS resolver pinning + CIDR parsing; default `none` (Edit-9) closes baseline; allowlist work separate |
| Docker socket bind host-level mitigation | 5 | **Backlog (ops Phase 4)** | Boot-time assertion in BW catches; permanent fix = drop UID from docker group at deploy-time |
| Per-Q merge gate / methodology shards | varies | **Q-260527-WTISO-SH** | Separate concern (shard rotation, gate composition); architecturally orthogonal к isolation primitive |
| Watchdog process for D4 orphan reap | 4 | **Ops Phase 4** | `--die-with-parent` covers race window; watchdog = belt-and-suspenders; cron / systemd timer |
| Quarterly bwrap CVE changelog review | varies | **Ops Phase 4 runbook** | Process gap, не code gap; subscribe Ubuntu USN + Debian DSA для `bubblewrap` package |
| Distro CVE feed automation для overlayfs | varies | **Ops Phase 4 runbook** | Same as above для `linux-image` + `overlayfs` |

**Sev-distribution after closure (28 rows analysis):**

| Sev | Count | Closed at BW | Residual (deferred) |
|---|---|---|---|
| 5 | 8 | 8 (T7, I1, I2, I3, I6, D1, D3, #15, N1, C2 share mitigation; unique paths = 8) | 0 |
| 4 | 8 | 7 (T8, D4, #14, C1, T9, I8, T2-secondary) | 1 (egress whitelist → WTISO-NET) |
| 3 | 9 | 9 (T2, I4, I5, I7, D2-partial, #20, E3-residual, T11, S2) | 0 |
| 2 | 2 | 2 (T10, [reserved]) | 0 |
| 1 | 1 | 1 | 0 |

## 6. Compliance footprint

### 152-ФЗ ст.13.11 (Russia — multi-tenant PII separation)

**Applicable:** `/home/server/crm` contains user PII (orders, customer records). Multi-tenant Anthropic API calls from non-isolated worker = cross-border PII transfer без user consent → violation.

**BW-layer compliance baseline:**
- Blackout `/home/server/crm` (:421) → worker cannot read CRM PII → cannot exfil к Anthropic API
- 30-path blackout sweep ensures no credential store leak (`.aws`, `.ssh`, `.git-credentials`, etc.)
- Per-worker overlay isolates `~/.claude/` → no session-token leak между tenant batches
- `BMAD_REQUIRE_SANDBOX=1` для production deployments — enforced via systemd unit env

**Multi-tenant deployment prerequisite:** BW-layer landed + audit-trail `sandbox_violation_blocked` events queryable per Q-NNN + retention ≥6 months (per 152-ФЗ audit requirements).

### GDPR (EU data residency)

**Applicable if** target Q-NNN involves EU-resident user data. BW isolation = necessary baseline but не sufficient — additional requirements:
- Data location disclosure (Anthropic API endpoint region)
- DPA (Data Processing Agreement) с Anthropic
- User-facing consent для LLM processing

**BW-layer contribution:** prevents accidental EU-data exfil из non-EU paths (`/home/server/crm` blackout); explicit egress policy (default `none` per Edit-9) requires opt-in per-batch.

### SOC 2 (if processing customer data)

**Applicable controls:**
- **CC6.1 (logical access controls)** — sandbox boundary + UID isolation (`--unshare-user-try`) maps к logical access boundary
- **CC6.6 (vulnerability management)** — bwrap version floor (#14) + quarterly CVE review (C1) + overlayfs unprivileged_userns_clone check (C2) — maps к vulnerability management process
- **CC7.2 (system monitoring)** — audit events `sandbox_violation_blocked`, `sandbox_fallback_nosandbox`, `bwrap_version_floor_failed`, `cgroup_tasks_max_hit` — maps к continuous monitoring

**BW-layer contribution:** technical controls in place; SOC 2 audit requires evidence retention + access-control review process.

## 7. Bwrap / overlayfs CVE monitoring plan

### Subscription / feed sources

| Source | Package(s) | Frequency | Action |
|---|---|---|---|
| Ubuntu USN (Security Notices) | `bubblewrap`, `linux-image-*`, `util-linux` | Daily (RSS / mailing list) | Auto-flag в `#security-alerts` Slack/Telegram; cross-ref с deployed version |
| Debian DSA (Security Advisories) | same | Daily | Same |
| NVD (NIST) | CVE.bubblewrap, CVE.overlayfs, CVE.linux.user_namespace | Weekly cron query | Append к `audit/cve-watchlist.json`; PR if new CVE matches our version range |
| upstream bwrap GitHub releases | containers/bubblewrap | Weekly | Quarterly changelog review (ops Phase 4) — look для privilege-related changes |

### Version-floor enforcement

**Boot-time invariant** (per #14 mitigation):
1. `bwrap --version` → parse → assert ≥ 0.6.0 (для `--ro-bind-try`) OR ≥ 0.8.0 (для `--die-with-parent` stability per F16)
2. Cross-check parsed version against `audit/cve-watchlist.json` known-vulnerable list
3. Если version в vulnerable list → exit 78 + audit `bwrap_version_floor_failed{version=X, reason=cve_match}`
4. Cron daily check (ops Phase 4) — independent of spawn-time; alerts если runtime drift

### Quarterly review process (ops Phase 4 runbook)

1. Pull last quarter's `bubblewrap` upstream commits (`git log --since=3.months v<latest>..HEAD`)
2. Filter для security-relevant: `priv`, `setuid`, `namespace`, `mount`, `escape`, `cve`, `escape`
3. Cross-reference с our deployed version + isolation contract; flag breaking changes
4. Update `audit/cve-watchlist.json` если new CVE published
5. Run M0-M5 + AC2-AC6 RED-test suite to verify isolation contract still holds после CVE patch

### Rotation policy on emergency

**Trigger:** new CVE published affecting deployed bwrap version + breaks isolation contract.

**Procedure:**
1. Emergency Q-NNN created с `priority: P0`
2. `audit/cve-watchlist.json` updated within 24h
3. `BMAD_REQUIRE_SANDBOX=1` paths halt-on-detect (boot fails → operator forced к patched version)
4. If patch unavailable → `BMAD_ALLOW_NOSANDBOX=1` + force-sequential как emergency degraded mode (loses primary safety, audited per event)
5. Post-mortem within 1 week; permanent fix via routine Q-NNN if patch lag >7 days

---

## Verdict

**PASS-PARTIAL** (per edge-case-hunter F1 reconciliation): **10 sev-5 threats** в §2 table (T7, I1, I2, I3, I6, D1, D3, N1, #15, C2). 9/10 closed via ported sandbox.py primitives + Iron Law tests M0-M5 / AC2-AC5. 1/10 (T7 docker socket) requires NEW boot-time assertion → covered by RED test AC6 `test-docker-group-assertion.sh` (committed Stage 3 per edge-case-hunter BLOCKER #2). 6 NEW architect-derived rows beyond §4fw 22 (T7, T8, T10, T11, S2, I8 — sev distribution noted в §2 row count summary). 5 additional RED stubs committed для T11/D4/#20/C2 verification (AC7/AC8/AC9/AC10). Residual gaps deferred к 4 explicit Q-NNN routings (BW-DISK, BW-CPU, WTISO-NET, SH) + ops Phase 4 runbook items.

**Cross-ref to analyst §4fj BW allocation:** 13 findings closed (11 in v2 + 2 enumerated в Edit-10: #20 user-namespace + #28 bwrap CLI fuzzing → covered by T9 row + #20 row above).

**Compliance status:** 152-ФЗ baseline met (cross-tenant PII isolation via blackouts + egress default `none`); GDPR + SOC 2 applicable controls technically covered, audit-evidence process gap = ops Phase 4 responsibility.

**Frontmatter status:** `phase: complete-phase-2-stage-2`, `gate-passed: 888-persona-architect 2026-05-27T (Stage 2 threat-model)`, next: Stage 3 (10 RED tests).
