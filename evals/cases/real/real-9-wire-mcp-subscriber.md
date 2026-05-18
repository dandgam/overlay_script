# Story real-9: Wire MCP_NOT_READY orchestrator-side subscriber

**Epic:** orchestrator-runtime
**Status:** ready-for-dev
**Level:** hard
**Tags:** runtime, subscriber, security-critical

## Acceptance Criteria

1. New subscriber `runtime/mcp_not_ready_subscriber.py` listens for `MCP_NOT_READY` events
2. On event: emits `STORY_HALTED` with `reason="mcp_not_ready: <tool-list>"` for the story_id in payload
3. Idempotent: receiving the same `(story_id, missing_tools)` pair twice emits exactly one `STORY_HALTED`
4. Subscriber wired into `agent/run.py` bus bootstrap alongside existing subscribers
5. Per-story frontmatter override (`requires_mcp: [...]`) merged with `Settings.required_mcp_tools` at dispatch time and surfaced through spawn_worker
6. 6 unit tests: subscriber handler (3) + frontmatter merge (2) + bus integration (1)
7. mypy + ruff clean

## Tasks

- [ ] AC1: Create subscriber module with handler + idempotency cache (per-run dict keyed by story_id)
- [ ] AC2: Hook frontmatter `requires_mcp` parsing in dispatcher (where story md is already parsed)
- [ ] AC3: Pass merged tool list through to `spawn_worker(required_mcp_tools=...)`
- [ ] AC4: Add subscriber to bus bootstrap in `agent/run.py`
- [ ] AC5: 6 tests + bus integration assertion
- [ ] AC6: Bump EventType inventory test if STORY_HALTED count changes (no new event type — just usage)

## File List

- `src/bmad_orchestrator/runtime/mcp_not_ready_subscriber.py`
- `src/bmad_orchestrator/agent/run.py`
- `src/bmad_orchestrator/runtime/worker_spawn.py` (param forwarding, no new logic)
- `src/bmad_orchestrator/agent/dispatch.py` (or wherever stories are parsed)
- `tests/test_mcp_not_ready_subscriber.py`
- `tests/test_frontmatter_requires_mcp.py`

## Dev Notes

Multi-file cross-layer wiring (subscriber + dispatcher + spawn_worker integration).
Expected 2-3 iterations (idempotency edge cases, frontmatter merge ordering).
Security-critical because a missed MCP gate could let a worker run without
authenticated tooling and silently produce wrong code.
Compliance: per-spec safety gate from spec_pilot_findings_closure §#5 deferred item.
