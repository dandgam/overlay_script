"""Activity hook installer — адаптировано из BAD.

Original: https://github.com/stephenleo/bmad-autonomous-development/blob/main/skills/bad/scripts/setup-activity-hook.py
License: MIT — Marie Stephen Leo

Что делает:
- Установка PostToolUse hook в .claude/settings.local.json
- Пишет одну JSONL строку на каждый tool call в `~/.claude/projects/<encoded>/bad-logs/<agent>/`
- jq filter: [now|todate, tool_name, first input value]

Зачем нам:
- Powering watchdog_fsm.py stale detection (читает mtime лог-файла)
- Cross-reference с replay timeline (§3 cap #13)
"""

from __future__ import annotations

# jq filter из BAD (load-bearing format)
ACTIVITY_LOG_JQ_FILTER = (
    '[now|todate, .tool_name, '
    '(.tool_input | to_entries | map(.value | tostring) | first // "")] '
    '| join(" | ")'
)


def install_activity_hook(settings_path: str, agent_slug: str) -> None:
    """TODO: implement.

    Modify .claude/settings.local.json:
      "hooks": {
        "PostToolUse": [
          {"matcher": "*", "hooks": [{
              "type": "command",
              "command": f"jq -r '{ACTIVITY_LOG_JQ_FILTER}' >> ~/.claude/projects/<enc>/bad-logs/{agent_slug}/activity.log"
          }]}
        ]
      }
    """
    raise NotImplementedError("TODO: port from BAD")
