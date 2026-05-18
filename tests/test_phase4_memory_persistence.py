"""Phase 4 hardening #2 — PreCompact + SessionStart memory persistence.

Spec: spec_phase4_hardening §1.2.

Coverage (9 tests):

Unit — MemoryPersistor dump/load (4 tests):
  * dump/load round-trip: state written and read back identically.
  * stale file rejection: file older than worker_started_at → None.
  * corruption handling: broken JSON → None.
  * missing file → None.

Unit — trigger_precompact_dump model-swap hook (3 tests):
  * dump_state is called when trigger_precompact_dump is invoked.
  * JSONL event worker_state_persisted is emitted when jsonl_path provided.
  * No JSONL event when jsonl_path=None (silent persist).

Unit — SessionStart block embeds load_state output (2 tests):
  * 'Resumed from:' line present in block when resumed_state provided.
  * 'Resumed from:' line absent when resumed_state=None.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path

from bmad_orchestrator.agent.memory.levels import MemoryPersistor
from bmad_orchestrator.agent.safety.session_start import build_session_start_block
from bmad_orchestrator.runtime.worker_spawn import trigger_precompact_dump

# ────────────────────────────────────────────────────────────────────────────
# A. Unit — MemoryPersistor dump/load (4 tests)
# ────────────────────────────────────────────────────────────────────────────


def test_memory_persistor_dump_load_roundtrip(tmp_path: Path) -> None:
    """dump_state followed by load_state returns the same dict."""
    persistor = MemoryPersistor()
    state = {
        "current_story_id": "epic-1-story-2",
        "retry_count": 1,
        "last_event_seq": 42,
        "active_skill": "bmad-dev-story",
        "scope_drift_warnings": 0,
        "worker_started_at": "2026-05-19T10:00:00+00:00",
    }
    persistor.dump_state(tmp_path, state)
    loaded = persistor.load_state(tmp_path)
    assert loaded == state


def test_memory_persistor_stale_file_rejected(tmp_path: Path) -> None:
    """load_state returns None when the snapshot file is older than worker_started_at."""
    persistor = MemoryPersistor()
    # Write state with an old timestamp.
    state = {
        "current_story_id": "story-old",
        "retry_count": 0,
        "last_event_seq": 0,
        "active_skill": "bmad-auto-dev",
        "scope_drift_warnings": 0,
        "worker_started_at": "2026-05-01T00:00:00+00:00",
    }
    snap_path = persistor.dump_state(tmp_path, state)

    # Set file mtime to a past timestamp (1 hour ago).
    past_mtime = time.time() - 3600
    import os
    os.utime(snap_path, (past_mtime, past_mtime))

    # Worker started "now" — file is from the past, so stale.
    worker_started_at = datetime.now(UTC).isoformat()
    loaded = persistor.load_state(tmp_path, worker_started_at=worker_started_at)
    assert loaded is None, "Expected None for stale file"


def test_memory_persistor_corrupted_json_returns_none(tmp_path: Path) -> None:
    """load_state returns None when the snapshot file contains invalid JSON."""
    persistor = MemoryPersistor()
    snap_path = tmp_path / MemoryPersistor.PRECOMPACT_SUBPATH
    snap_path.parent.mkdir(parents=True, exist_ok=True)
    snap_path.write_text("not valid json {{{{", encoding="utf-8")

    loaded = persistor.load_state(tmp_path)
    assert loaded is None, "Expected None for corrupted JSON"


def test_memory_persistor_missing_file_returns_none(tmp_path: Path) -> None:
    """load_state returns None when the snapshot file does not exist."""
    persistor = MemoryPersistor()
    loaded = persistor.load_state(tmp_path)
    assert loaded is None, "Expected None when file is missing"


# ────────────────────────────────────────────────────────────────────────────
# B. Unit — trigger_precompact_dump model-swap hook (3 tests)
# ────────────────────────────────────────────────────────────────────────────


def test_trigger_precompact_dump_creates_snapshot(tmp_path: Path) -> None:
    """trigger_precompact_dump writes a readable snapshot to the worktree."""
    snap_path = trigger_precompact_dump(
        worktree=str(tmp_path),
        story_id="story-model-swap",
        retry_count=2,
        active_skill="bmad-dev-story",
    )
    assert snap_path.exists(), "Expected snapshot file to be created"
    data = json.loads(snap_path.read_text(encoding="utf-8"))
    assert data["current_story_id"] == "story-model-swap"
    assert data["retry_count"] == 2
    assert data["active_skill"] == "bmad-dev-story"


def test_trigger_precompact_dump_emits_jsonl_event(tmp_path: Path) -> None:
    """trigger_precompact_dump emits worker_state_persisted to JSONL when path given."""
    jsonl_file = tmp_path / "events.jsonl"
    jsonl_file.touch()

    trigger_precompact_dump(
        worktree=str(tmp_path),
        story_id="story-emit-test",
        retry_count=1,
        jsonl_path=jsonl_file,
    )

    lines = jsonl_file.read_text(encoding="utf-8").strip().splitlines()
    assert lines, "Expected at least one JSONL line"
    event = json.loads(lines[-1])
    assert event["event_type"] == "worker_state_persisted"
    assert event["story_id"] == "story-emit-test"
    assert event["retry_count"] == 1


def test_trigger_precompact_dump_silent_when_no_jsonl(tmp_path: Path) -> None:
    """trigger_precompact_dump does not create any JSONL file when jsonl_path=None."""
    trigger_precompact_dump(
        worktree=str(tmp_path),
        story_id="story-silent",
        jsonl_path=None,
    )
    # Snapshot must exist (state was persisted)
    snap_path = tmp_path / MemoryPersistor.PRECOMPACT_SUBPATH
    assert snap_path.exists()

    # No stray JSONL files in the worktree root.
    jsonl_files = list(tmp_path.glob("*.jsonl"))
    assert not jsonl_files, f"Expected no JSONL files, got: {jsonl_files}"


# ────────────────────────────────────────────────────────────────────────────
# C. Unit — SessionStart block embeds load_state output (2 tests)
# ────────────────────────────────────────────────────────────────────────────


def test_session_start_block_embeds_resumed_state() -> None:
    """When resumed_state is provided, block contains 'Resumed from:' line."""
    state = {
        "current_story_id": "story-resume-test",
        "retry_count": 2,
        "scope_drift_warnings": 1,
        "active_skill": "bmad-dev-story",
        "worker_started_at": "2026-05-19T08:00:00+00:00",
    }
    block = build_session_start_block(
        "bmad-dev-story", "story-resume-test", resumed_state=state
    )
    assert "Resumed from:" in block, f"Expected 'Resumed from:' in block; got: {block[:300]}"
    assert "retry=2" in block
    assert "scope_drift=1" in block


def test_session_start_block_no_resumed_state_no_resumed_from() -> None:
    """When resumed_state=None, block does NOT contain 'Resumed from:' line."""
    block = build_session_start_block(
        "bmad-dev-story", "story-fresh", resumed_state=None
    )
    assert "Resumed from:" not in block, (
        "Did not expect 'Resumed from:' when no state; got block: "
        + block[:300]
    )
