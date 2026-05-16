# Production Launcher — bmad-orchestrator Wave 1a Pilot

> How to launch the orchestrator under `systemd-user` for a real Wave 1a pilot
> run, what to check before flipping the switch, and how to recover if it goes
> wrong. Covers Odyssey (`/home/server/odyssey-ux/`) but the unit applies to
> any project with `_bmad-output/planning-artifacts/`.

Compatible with: orchestrator commit `4e9cc2e` and later (post-W4).

---

## 1. Systemd unit (user instance)

Drop the unit into `~/.config/systemd/user/bmad-orchestrator@.service`. The
template parameter `%i` is the wave (e.g. `1a`); per-wave instances stay
isolated.

```ini
[Unit]
Description=bmad-orchestrator pilot — Wave %i
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=%h/bmad-orchestrator
EnvironmentFile=%h/bmad-orchestrator/.env.pilot
Environment="BMAD_REQUIRE_SANDBOX=1"
Environment="BMAD_REQUIRE_DB_BRIDGE=1"
Environment="ORCHESTRATOR_TARGET_PROJECT=%h/odyssey-ux"
Environment="PYTHONUNBUFFERED=1"
ExecStart=%h/bmad-orchestrator/venv/bin/python -m bmad_orchestrator.cli run \
    --project odyssey \
    --wave %i \
    --max-parallel 2 \
    --max-stories 2 \
    --max-spend-usd 5.0 \
    --real
Restart=no
StandardOutput=journal
StandardError=journal
SyslogIdentifier=bmad-orchestrator-%i

# Resource isolation — pin the orchestrator + its worker subprocesses to one
# cgroup slice so OOMs / runaway spawns are scoped.
Slice=bmad-orchestrator.slice
TasksMax=4096
MemoryMax=8G
LimitNOFILE=65536

[Install]
WantedBy=default.target
```

`Restart=no` is intentional. The first pilot must surface failures loudly —
`Restart=on-failure` masks misconfiguration. After the pilot's first 10 stories
land cleanly, switch to `Restart=on-failure` + a `RestartSec=60` cooldown.

Enable + start:

```bash
loginctl enable-linger "$(whoami)"        # one-time, survives logout
systemctl --user daemon-reload
systemctl --user enable --now bmad-orchestrator@1a.service
systemctl --user status bmad-orchestrator@1a.service
journalctl --user -u bmad-orchestrator@1a.service -f
```

Stop:

```bash
systemctl --user stop bmad-orchestrator@1a.service
```

`stop` triggers a graceful shutdown via SIGTERM. The orchestrator drains
in-flight worker handles before exiting; you'll see `wave_boundary_reached`
or `orchestrator_drained` in the journal before the unit shows `inactive`.

---

## 2. `.env.pilot` template

Place at `~/bmad-orchestrator/.env.pilot`, chmod 600.

```bash
# Anthropic API access — never commit this file.
ANTHROPIC_API_KEY="sk-ant-...."

# Optional: override budget per-day cap (defaults from config.py: $30/day).
# BMAD_DAILY_LIMIT_USD="20.0"

# StateDB path — same DB for bot + agent so the bridge can route human-query
# events across processes (FS8 NH1).
BMAD_STATEDB_PATH="%h/bmad-orchestrator/.state/pilot.db"

# Shared session id (FS8 NH1) — set by the launcher script below, NOT by hand.
# BMAD_ORCHESTRATOR_SESSION_ID=...

# Telegram bot (optional — leave empty in dry-run pilot).
TELEGRAM_BOT_TOKEN=""
TELEGRAM_CHAT_ID_WHITELIST="123456789,987654321"
TELEGRAM_ESCALATION_CHAT_ID="123456789"

# Wave 1a Odyssey-specific overrides.
ORCHESTRATOR_LOCALE=ru
```

`BMAD_REQUIRE_SANDBOX=1` and `BMAD_REQUIRE_DB_BRIDGE=1` are set in the unit,
not here — they belong with the deployment, not the secrets.

---

## 3. Pre-deployment checklist

Run through this list once **per host** before the first pilot launch. Each
line is a hard prerequisite; the orchestrator either refuses to start or
silently downgrades safety if any are missing.

- [ ] **`bubblewrap` installed.** `which bwrap` returns a path. Without it,
      `detect_sandbox()` falls back to `NoSandbox` and `BMAD_REQUIRE_SANDBOX=1`
      raises before any worker is spawned (intentional — primary safety
      can't be silently lost).
      Install: `sudo apt install bubblewrap util-linux`.
- [ ] **`util-linux` ≥ 2.36** (for `prlimit`). `prlimit --version` reports
      ≥ 2.36. Older versions silently ignore `-p`.
- [ ] **`linger` enabled** for the runtime user. `loginctl show-user $(whoami) | grep Linger`
      shows `Linger=yes`. Without it, the systemd timer/unit dies on logout.
- [ ] **StateDB initialised.** `ls -la ~/bmad-orchestrator/.state/pilot.db`
      shows a non-empty file. If missing:
      ```bash
      venv/bin/python -m bmad_orchestrator.cli init-statedb \
          --path ~/bmad-orchestrator/.state/pilot.db
      ```
- [ ] **Git identity configured.** `git config --global user.email`
      and `git config --global user.name` are both set. Without them,
      `merge_to_integration_subscriber` raises `GitCommandError`
      (`--signoff` requires identity) and escalates as `merge_conflict`.
- [ ] **Target project clean.** `cd ~/odyssey-ux && git status -s` is empty.
      Worker worktrees branch off the project's current `main`, so dirty
      state propagates into every story.
- [ ] **No stale worktrees.** `ls ~/odyssey-ux/.worktrees/` is empty (or absent).
      A previous crashed pilot leaves stale worktree dirs that
      `merge_to_integration_subscriber` cannot clean up safely.
- [ ] **Sprint status freshly generated.**
      `ls ~/odyssey-ux/_bmad-output/planning-artifacts/sprint-status.yaml`.
      Stale sprint-status causes DagPlanner to spawn already-done stories
      (rework, wasted spend).
- [ ] **`ANTHROPIC_API_KEY` valid.**
      ```bash
      curl -s -H "x-api-key: $ANTHROPIC_API_KEY" \
        -H "anthropic-version: 2023-06-01" \
        https://api.anthropic.com/v1/models | jq '.data[0].id'
      ```
      Returns a model id (e.g. `"claude-opus-4-7"`). 401 / 403 → renew key.
- [ ] **Day-cap floor sanity-checked.** `BMAD_DAILY_LIMIT_USD` (or the
      default in `config.py`) is **at least** `2 × max_spend_usd` from the
      CLI — the pilot is allowed to retry once before tripping the hard cap.
- [ ] **Telegram bot reachable** (only if `TELEGRAM_BOT_TOKEN` is set).
      `python -m bmad_orchestrator.cli ping-bot` returns `bot_alive: true`.
      Whitelist check (`TELEGRAM_CHAT_ID_WHITELIST`) lists your `chat_id`.
- [ ] **Tests green on the pilot commit.** `venv/bin/pytest tests/ -q`
      returns `0 failed` on the very commit you're about to deploy. The
      systemd unit pins `WorkingDirectory` to whatever is checked out —
      stale code on disk is a common foot-gun.

---

## 4. Recovery runbook

Three recovery scenarios, in increasing severity. Pick by symptom.

### 4.1 «Orchestrator висит — нужно остановить»

The wave is making progress but you need to halt (e.g. spend looks higher than
expected, you spotted a wrong story in the queue).

1. `systemctl --user stop bmad-orchestrator@1a.service` — sends SIGTERM.
   The orchestrator finishes the **current in-flight worker batch** (so the
   running code-review for any story completes), emits
   `wave_boundary_reached`, then exits cleanly.
2. Verify: `journalctl --user -u bmad-orchestrator@1a.service -n 50`
   should end with `orchestrator_drained` or `wave_boundary_reached`.
3. If the unit is `failed` instead of `inactive` — see §4.3.

Do NOT `kill -9` the unit. SIGKILL leaves worktrees orphaned + the StateDB's
`agent_session` row in `running` state, which blocks the next pilot from
acquiring the same session id (FS8 NH1).

### 4.2 «Budget halt — pilot вышел по дневному cap'у»

Symptom: journal has `BUDGET_THRESHOLD_HIT scope=day level=halt`. Unit exits
cleanly. Next-day plan:

1. **Confirm spend** —
   `venv/bin/python -m bmad_orchestrator.cli budget-status --wave 1a`.
   Shows attributed spend per scope (`intent_router`, `worker:<story>`).
   Sanity-check that the breakdown matches your expectation.
2. **Inspect uncommitted stories** — for each story still listed
   `ready-for-dev` (not yet `dev-done` / merged), decide whether it gets
   resumed or abandoned. Resume = no action needed (DAG re-picks it on next
   launch). Abandon = `bmad-cli mark-story --status abandoned <id>`.
3. **Wait for daily reset OR raise the cap.** Default reset is UTC midnight.
   To raise the cap mid-day: edit `BMAD_DAILY_LIMIT_USD` in `.env.pilot`
   AND emit a fresh manual confirmation (the orchestrator refuses to honour
   a cap change for an already-halted day unless the env was rotated).
4. **Re-launch:** `systemctl --user start bmad-orchestrator@1a.service`.
   The shared session id (env-supplied) ensures budget accounting picks up
   where it left off — workers from prior crashes won't double-count.

### 4.3 «Pilot упал — нужно поднять очередь stories после ребута»

Symptom: unit is `failed`, last journal line is a stack trace or `Killed`.
Possible causes: OOM, sandbox failure, git merge conflict on integration
branch, bubblewrap missing post-update.

1. **Read the failure.**
   `journalctl --user -u bmad-orchestrator@1a.service -n 200`. Look for
   `RuntimeError`, `GitCommandError`, `BMAD_REQUIRE_SANDBOX raised`, or
   `OOMKilled`.

2. **Quarantine half-finished work.**

   ```bash
   cd ~/odyssey-ux
   git status -s
   ls .worktrees/
   ```

   For each leftover worktree:

   ```bash
   # If the worktree dir is healthy (has commits on its feature/<story> branch):
   git worktree remove .worktrees/<story-dir> --force
   # If the worktree dir is corrupt:
   rm -rf .worktrees/<story-dir>
   git worktree prune
   git branch -D feature/<story>   # ONLY if commits are already merged or abandoned
   ```

3. **Reset the StateDB session for this wave.**

   ```bash
   sqlite3 ~/bmad-orchestrator/.state/pilot.db <<'SQL'
   UPDATE agent_session
     SET status = 'crashed', ended_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
     WHERE project = 'odyssey' AND wave = '1a' AND status = 'running';
   SELECT * FROM agent_session ORDER BY id DESC LIMIT 5;
   SQL
   ```

   The orchestrator's `_resolve_session` (FS8 NH1) will then create a fresh
   session row on next launch instead of trying to reuse the crashed one.

4. **Re-run the pilot.**
   `systemctl --user start bmad-orchestrator@1a.service` and tail the journal.

5. **If failure repeats with the same error after a restart** — do NOT keep
   restarting. Switch the unit to `--max-stories 1` and reproduce on a
   single story to narrow the blast radius. File the failure in
   `_bmad/_memory/incidents/` with the journal excerpt + `sprint-status.yaml`
   snapshot at the time of failure.

### 4.4 Emergency rollback — «main отравлен, нужно откатить»

The orchestrator does NOT merge into `main`; only the human does. If you've
manually merged a polluted `integration/1a` into `main`:

```bash
cd ~/odyssey-ux
git reflog | head -10                 # find the SHA before the merge
git reset --hard <sha-before-merge>   # destructive — ONLY if you're sure
```

This is a destructive action. Run it ONLY when:
- You confirmed the bad commit is the most-recent on `main`.
- No teammate has pulled the polluted `main` yet.
- You've snapshotted the current state to a backup branch first:
  `git branch backup/main-pre-rollback-$(date +%Y%m%d) main`.

The orchestrator's `backup/wave_1a_pilot_wiring-pre-2026-05-16` branch is
NOT involved here — that's the orchestrator codebase, not the target
project. Target project rollback is the operator's responsibility and lives
outside this skill's scope.

---

## 5. Operating envelope (Wave 1a defaults)

| Parameter | Default | When to change |
|---|---|---|
| `--max-parallel` | 2 | Increase only after 10+ stories succeed sequentially. Per-host nproc + memory ceiling. |
| `--max-stories` | 2 (pilot) | First pilot: 2. Hardening run: 5. Production: 50. |
| `--max-spend-usd` | 5.0 (pilot) | Pilot cap = 2× expected per-story cost. |
| `BMAD_DAILY_LIMIT_USD` | 30.0 | Should be ≥ 2× `--max-spend-usd`. |
| `WorkerCostTracker.story_alarm_usd` | from `BudgetConfig` | Read-only — adaptive reserve self-tunes from `_recent_story_costs`. |

If the first 2-story pilot lands cleanly: bump `--max-stories` to 5 and
re-run. If 5 lands: re-evaluate (cgroup migration? multi-key failover?)
before going to 10.

---

## 6. What's intentionally NOT in this doc

- **Multi-project queue** (Odyssey + CRM-hotfix in parallel) — backlog item
  4 (`project_backlog_post_mvp.md`). Single-project per unit instance for
  Wave 1a.
- **`nftables` network whitelist** for `github_only` outbound — deferred to
  Wave 1b.
- **Cgroup-based `TasksMax`** for sandbox isolation — currently the unit
  uses `Slice=bmad-orchestrator.slice` + `TasksMax=4096` as a coarse upper
  bound. Per-worker cgroup migration is on the backlog and is re-evaluated
  after the first 10 stories produce EAGAIN signal (or don't).
- **Telegram voice / TTS** — backlog item 6, post-MVP.
- **Auto-merge to `main`** — by design. Wave 1a uses `auto_merge=false`;
  the human reviews `integration/<wave>` before merging.
