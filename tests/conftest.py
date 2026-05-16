"""Shared pytest fixtures (FS8 §8.3).

Fixtures:

- ``sandbox_available`` — skips the test when ``bwrap`` is not present on
  ``$PATH``. Used by every real-sandbox PoC under ``test_fs7_sandbox.py`` so
  the suite stays green in CI hosts without bubblewrap.
- ``tmp_state_db_session`` — async fixture: creates a temp StateDB on a
  fresh path, initialises schema, opens one session, and yields
  ``(state_db, session_id)``. Tests that need a bound budget guard or
  cross-process bridge instantiate this fixture instead of hand-rolling
  setup boilerplate.
"""

from __future__ import annotations

import shutil
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from bmad_orchestrator.state.db import StateDB


@pytest.fixture
def sandbox_available() -> None:
    """Skip the test if ``bwrap`` is not on ``$PATH``."""
    if shutil.which("bwrap") is None:
        pytest.skip("bwrap not available — FS7/FS8 real-sandbox PoC skipped")


@pytest_asyncio.fixture
async def tmp_state_db_session(tmp_path: Path) -> AsyncIterator[tuple[StateDB, int]]:
    """Yield a freshly initialised StateDB plus one running session id."""
    db = StateDB(db_path=tmp_path / "state.db")
    await db.init()
    session_id = await db.create_session(
        target_project="test-project",
        wave="test-wave",
        max_parallel=2,
    )
    yield db, session_id
