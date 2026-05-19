"""NEW-24 — review workers must spawn with API network egress.

pilot run #7 / replay 1.5 (2026-05-20): every review spawn (code-review,
security-review, merge-gate spec + quality) wrote::

    API Error: Unable to connect to API (ConnectionRefused)
    worker_completed exit_code=1 status=failure

→ ``verdict=error`` on every stage, masked only by the runner-log fallback.

Root cause: the review spawns passed ``sandbox_network="none"`` (``--unshare-net``).
The review worker runs an inner ``claude -p`` reviewer that makes LLM API calls —
reviewing is an LLM operation and needs egress, same as the dev worker.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from bmad_orchestrator.agent import run


class _FakeHandle:
    jsonl_path = Path("/tmp/fake.events.jsonl")


_SPAWNERS = [
    "_spawn_code_review_worker",
    "_spawn_security_review_worker",
    "_spawn_merge_gate_spec_worker",
    "_spawn_merge_gate_quality_worker",
]


@pytest.mark.parametrize("spawner_name", _SPAWNERS)
@pytest.mark.asyncio
async def test_review_spawn_uses_full_network(spawner_name: str) -> None:
    """Each review spawn must request ``sandbox_network="full"`` — never "none"."""
    spawner = getattr(run, spawner_name)
    fake = AsyncMock(return_value=_FakeHandle())

    with patch.object(run, "runtime_spawn_worker", fake):
        await spawner(worktree="/tmp/wt-1.5", story_id="1.5", wave="1a")

    assert fake.await_count == 1
    kwargs = fake.await_args.kwargs
    assert kwargs["sandbox_network"] == "full", (
        f"{spawner_name} spawned with "
        f"sandbox_network={kwargs.get('sandbox_network')!r} — reviewer needs "
        f"API egress (ConnectionRefused regression)"
    )
