"""Probe: does our system_prompt path actually achieve prompt caching?

Goal:
  Verify that build_system_prompt → blocks_to_string → ClaudeAgentOptions
  chain results in non-zero cache_read_input_tokens on the SECOND call,
  meaning the Anthropic CLI auto-caches our long system prompt.

Methodology:
  1. Build the production system_prompt via the same code path orchestrator uses.
  2. Call Claude SDK twice with same system_prompt + same user message.
  3. Inspect ResultMessage.usage on each call.
  4. Verdict: cache_creation on call 1 > 0 AND cache_read on call 2 > 0.

Usage:
  cd /home/server/bmad-orchestrator
  .venv/bin/python scripts/probe_prompt_caching.py

Exit codes:
  0 — caching works (cache_read > 0 on call 2)
  1 — caching broken (cache_read == 0 on call 2)
  2 — auth / network / SDK error
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ResultMessage,
    query,
)

from bmad_orchestrator.agent.system_prompt import (
    blocks_to_string,
    build_system_prompt,
)


async def _make_call(label: str, sys_prompt: str) -> dict | None:
    """Single SDK call; returns usage dict from final ResultMessage."""
    print(f"\n── {label} ───────────────────────────────")
    opts = ClaudeAgentOptions(system_prompt=sys_prompt)
    final_usage: dict | None = None
    msg_count = 0
    try:
        async for msg in query(
            prompt="Reply with EXACTLY one word: OK", options=opts
        ):
            msg_count += 1
            if isinstance(msg, ResultMessage):
                usage = getattr(msg, "usage", None)
                if usage is not None:
                    if hasattr(usage, "__dict__"):
                        final_usage = dict(usage.__dict__)
                    elif isinstance(usage, dict):
                        final_usage = dict(usage)
                    else:
                        final_usage = {"raw": str(usage)}
    except Exception as exc:  # noqa: BLE001 — probe must catch any SDK error
        print(f"  ERROR: {type(exc).__name__}: {exc}")
        return None

    print(f"  messages: {msg_count}")
    if final_usage:
        print(f"  usage: {json.dumps(final_usage, indent=2, default=str)}")
    else:
        print("  usage: <no ResultMessage with usage seen>")
    return final_usage


async def main() -> int:
    project_root = Path("/home/server/bmad-orchestrator")
    blocks = build_system_prompt(project_root=project_root, wave="1a")
    sys_prompt = blocks_to_string(blocks)

    chars = len(sys_prompt)
    est_tokens = chars // 4
    print(f"System prompt: {chars:,} chars (~{est_tokens:,} tokens)")
    print(f"Block count: {len(blocks)} (cache_control on first 4 of 5)")
    has_cache_control = sum(1 for b in blocks if "cache_control" in b)
    print(f"Blocks with cache_control: {has_cache_control}/{len(blocks)}")
    print(
        "Note: blocks_to_string() drops cache_control markers; we test "
        "Anthropic CLI auto-caching behavior on the str prefix."
    )

    usage1 = await _make_call("Call 1 (cold — should be cache_creation)", sys_prompt)
    if usage1 is None:
        print("\n[FAIL] Call 1 errored — probe inconclusive")
        return 2

    print("  (sleeping 2s to let cache settle)")
    await asyncio.sleep(2)

    usage2 = await _make_call("Call 2 (warm — should be cache_read)", sys_prompt)
    if usage2 is None:
        print("\n[FAIL] Call 2 errored — probe inconclusive")
        return 2

    # Verdict
    cc1 = usage1.get("cache_creation_input_tokens", 0) or 0
    cr1 = usage1.get("cache_read_input_tokens", 0) or 0
    cc2 = usage2.get("cache_creation_input_tokens", 0) or 0
    cr2 = usage2.get("cache_read_input_tokens", 0) or 0

    print("\n══ VERDICT ════════════════════════════════════")
    print(f"  Call 1: cache_creation={cc1:>6,}  cache_read={cr1:>6,}")
    print(f"  Call 2: cache_creation={cc2:>6,}  cache_read={cr2:>6,}")

    if cr2 > 0:
        ratio = cr2 / (cr2 + (usage2.get("input_tokens", 0) or 0))
        print(f"  ✅ CACHING WORKS — cache_read on call 2 = {cr2:,} tokens")
        print(f"     hit ratio call 2 = {ratio:.0%}")
        return 0
    if cc1 == 0 and cr1 == 0 and cc2 == 0 and cr2 == 0:
        print("  ⚠️ NO CACHE STATS — usage payload doesn't include cache_* fields")
        print("     (subscription CLI may strip them, or SDK aggregates differently)")
        return 2
    print("  ❌ CACHING BROKEN — cache_read on call 2 = 0")
    print("     blocks_to_string + claude CLI subprocess does NOT auto-cache")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
