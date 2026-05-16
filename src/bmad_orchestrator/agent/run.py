"""Main agent entry point.

См. spec §2 диаграмма + §4 события.

Flow:
1. Build ClaudeAgentOptions: tools (22, all defer_loading=True), hooks, beta_headers
2. Start ClaudeSDKClient as async context manager
3. Event loop: ждём events (worker_done, halt, elicitation, chat_message, ...)
4. На каждом event → query(event_summary) → agent thinks + calls tools → loop
"""

from __future__ import annotations

import asyncio

import structlog

from bmad_orchestrator.config import load_settings

log = structlog.get_logger(__name__)


async def run_orchestrator(
    project: str,
    wave: str,
    max_parallel: int = 2,
) -> None:
    """Main orchestrator loop.

    TODO:
    1. Init ClaudeSDKClient with all 22 tools + 10 skills
    2. Init event queue (asyncio.Queue)
    3. Spawn event sources: file watchers, JSONL stream parsers, telegram bot, scheduler
    4. Main loop: await queue.get() → agent.query(event) → process
    """
    settings = load_settings()
    log.info(
        "orchestrator_starting",
        project=project,
        wave=wave,
        max_parallel=max_parallel,
        target=str(settings.target_project),
    )
    # TODO: implement
    await asyncio.sleep(0)
    log.info("orchestrator_exited")


def main() -> None:
    """CLI shim."""
    import sys

    if len(sys.argv) < 3:
        print("Usage: python -m bmad_orchestrator.agent.run <project> <wave>")
        sys.exit(2)
    asyncio.run(run_orchestrator(sys.argv[1], sys.argv[2]))


if __name__ == "__main__":
    main()
