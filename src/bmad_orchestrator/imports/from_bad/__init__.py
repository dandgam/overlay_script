"""Cherry-picked patterns from stephenleo/bmad-autonomous-development (BAD).

Source: https://github.com/stephenleo/bmad-autonomous-development
License: MIT (BAD v1.2.0, April 2026)
Attribution: Marie Stephen Leo (sole author)

Адаптировано 5 паттернов (spec §20.4):
- activity_hook        — install PostToolUse hook + jq filter для structured logging
- gh_client            — gh CLI с curl fallback для sandboxes
- watchdog_fsm         — STALE detection state machine ([K]/[R]/[S]/[A])
- merge_gate_prompt    — squash-merge system prompt template
- dag_planner_prompt   — DAG topology system prompt template
"""

__all__ = [
    "activity_hook",
    "dag_planner_prompt",
    "gh_client",
    "merge_gate_prompt",
    "watchdog_fsm",
]
