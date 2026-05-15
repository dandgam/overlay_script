---
name: worker-dispatcher
description: Spawn `claude -p` subprocess in worktree, set cost cap and tool harness, monitor heartbeat. Activates per ready DAG node (from dag-planner output).
---

# worker-dispatcher skill

## Когда активируется

- dag-planner вернул `ready_now` список
- Slot в `SubprocessPool` свободен (Semaphore release)

## Tools

- `create_worktree(story_id, branch)` — `/home/server/<proj>-wt-N`
- `sync_skill_patches(worktree)` — pull latest bmad-auto-dev patches
- `spawn_worker(worktree, model, budget_cap_usd)` — `claude -p /bmad-auto-dev <story_id>`

## Лимиты

- `budget_cap_usd = story_halt_usd` (default $50) — passed как `--max-budget` to claude CLI
- `max_turns` — explicit limit для anti-loop (research §18.3 anti-pattern #3)
- Worker model = `cfg.models.dev` (default Sonnet 4.6)

## Heartbeat / liveness

- Worker emit'ит JSONL events ≥ 1 раз / 60s
- Если silence >300s → call `is_alive(pid)` (3-signal check)
- Если alive → log + ждём (handoff §5.2 + feedback_dont_kill_if_alive)
- Если dead → mark FAILED + cleanup

## Failure modes

- Cold start failure (API key, model unavailable) → fallback model + retry 1×, then escalate
- Spawn timeout → cleanup worktree + escalate
