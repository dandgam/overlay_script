"""Unit tests for `cli/snapshot.py` helpers + `_build_snapshot` integration.

Covers the four sources `_build_snapshot` reads (state.db / sprint-status.yaml /
events.jsonl / live processes) with isolated tmp_path fixtures. Each test seeds
one source minimally and asserts the snapshot reflects it without touching the
others.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from bmad_orchestrator.cli.snapshot import (
    SessionRow,
    SprintProgress,
    derive_agent_thinking,
    derive_budget_level,
    read_active_workers,
    read_events_tail,
    read_latest_session,
    read_session_budget,
    read_sprint_progress,
)
from bmad_orchestrator.config import Settings
from bmad_orchestrator.state.db import SCHEMA_SQL

# ── fixtures ────────────────────────────────────────────────────────────────


def _seed_state_db(
    db_path: Path,
    *,
    sessions: list[dict] | None = None,
    budgets: list[dict] | None = None,
) -> None:
    """Create state.db with schema and optional rows."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
        for sess in sessions or []:
            conn.execute(
                """
                INSERT INTO agent_session
                  (id, target_project, wave, max_parallel, status, started_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    sess["id"],
                    sess["target_project"],
                    sess["wave"],
                    sess.get("max_parallel", 2),
                    sess["status"],
                    sess["started_at"],
                ),
            )
        for b in budgets or []:
            conn.execute(
                """
                INSERT INTO budget_tracker
                  (session_id, scope, scope_target_id,
                   spent_usd, spent_tokens,
                   alarm_threshold, halt_threshold,
                   breached_alarm, breached_halt, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, '2026-05-19T00:00:00+00:00')
                """,
                (
                    b["session_id"],
                    b["scope"],
                    b.get("scope_target_id", "default"),
                    b["spent_usd"],
                    b.get("spent_tokens", 0),
                    b.get("alarm_threshold", 100.0),
                    b.get("halt_threshold", 200.0),
                ),
            )
        conn.commit()


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    """Empty BMad-shaped project root."""
    root = tmp_path / "fake-proj"
    (root / "_bmad-output" / "runs").mkdir(parents=True)
    return root


@pytest.fixture
def settings(project_root: Path, tmp_path: Path) -> Settings:
    """Settings instance pointed at the tmp project, isolated state.db."""
    return Settings(
        target_project=project_root,
        state_db=tmp_path / "state.db",
        orchestrator_home=tmp_path / "orch",
    )


def _write_sprint_status(root: Path, payload: dict) -> Path:
    """Write sprint-status.yaml in the first probed (upstream BMad) layout."""
    import yaml

    path = root / "_bmad" / "implementation-artifacts" / "sprint-status.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return path


def _write_events(
    root: Path, wave: str, worktree: str, events: list[dict]
) -> Path:
    """Append `events` to `<runs>/<wave>/<worktree>.events.jsonl`."""
    wave_dir = root / "_bmad-output" / "runs" / wave
    wave_dir.mkdir(parents=True, exist_ok=True)
    path = wave_dir / f"{worktree}.events.jsonl"
    with path.open("a", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    return path


# ── derive_budget_level ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "spent,cap,expected",
    [
        (0.0, 100.0, "ok"),
        (50.0, 100.0, "ok"),
        (79.99, 100.0, "ok"),
        (80.0, 100.0, "alarm"),
        (99.0, 100.0, "alarm"),
        (100.0, 100.0, "halt"),
        (250.0, 100.0, "halt"),
        (5.0, 0.0, "ok"),  # zero cap → degrade
    ],
)
def test_derive_budget_level(spent: float, cap: float, expected: str) -> None:
    assert derive_budget_level(spent, cap) == expected


# ── read_latest_session ─────────────────────────────────────────────────────


def test_read_latest_session_missing_file_returns_none(tmp_path: Path) -> None:
    assert read_latest_session(tmp_path / "missing.db") is None


def test_read_latest_session_no_active_returns_none(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    _seed_state_db(
        db,
        sessions=[
            {
                "id": 1, "target_project": "antares", "wave": "1a",
                "status": "stopped", "started_at": "2026-05-19T00:00:00+00:00",
            }
        ],
    )
    assert read_latest_session(db) is None


def test_read_latest_session_returns_running_row(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    _seed_state_db(
        db,
        sessions=[
            {
                "id": 1, "target_project": "antares", "wave": "1a",
                "status": "stopped", "started_at": "2026-05-18T00:00:00+00:00",
            },
            {
                "id": 2, "target_project": "antares", "wave": "1a",
                "status": "running", "started_at": "2026-05-19T00:00:00+00:00",
            },
        ],
    )
    row = read_latest_session(db)
    assert isinstance(row, SessionRow)
    assert row.id == 2
    assert row.status == "running"
    assert row.target_project == "antares"
    assert row.wave == "1a"


def test_read_latest_session_paused_is_active(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    _seed_state_db(
        db,
        sessions=[
            {
                "id": 1, "target_project": "x", "wave": "1a",
                "status": "paused", "started_at": "2026-05-19T00:00:00+00:00",
            }
        ],
    )
    row = read_latest_session(db)
    assert row is not None
    assert row.status == "paused"


# ── read_session_budget ─────────────────────────────────────────────────────


def test_read_session_budget_missing_db_returns_zero(tmp_path: Path) -> None:
    assert read_session_budget(tmp_path / "missing.db", 1) == 0.0


def test_read_session_budget_empty_returns_zero(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    _seed_state_db(db)
    assert read_session_budget(db, 1) == 0.0


def test_read_session_budget_sums_day_scope(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    _seed_state_db(
        db,
        sessions=[
            {
                "id": 1, "target_project": "x", "wave": "1a",
                "status": "running", "started_at": "2026-05-19T00:00:00+00:00",
            }
        ],
        budgets=[
            {"session_id": 1, "scope": "day", "spent_usd": 12.5,
             "alarm_threshold": 100.0, "halt_threshold": 200.0},
            # story/batch rows must be ignored (they double-count the same spend)
            {"session_id": 1, "scope": "story", "scope_target_id": "1.1",
             "spent_usd": 12.5, "alarm_threshold": 30.0, "halt_threshold": 60.0},
            {"session_id": 1, "scope": "batch", "scope_target_id": "1",
             "spent_usd": 12.5, "alarm_threshold": 200.0, "halt_threshold": 400.0},
        ],
    )
    assert read_session_budget(db, 1) == pytest.approx(12.5)


# ── read_sprint_progress ────────────────────────────────────────────────────


def test_read_sprint_progress_missing_yaml(settings: Settings) -> None:
    out = read_sprint_progress(settings)
    assert out == SprintProgress()


def test_read_sprint_progress_counts_stories(
    settings: Settings, project_root: Path
) -> None:
    _write_sprint_status(
        project_root,
        {
            "epics": {
                "1": {
                    "status": "in-progress",
                    "stories": {
                        "1.1": "done",
                        "1.2": "done",
                        "1.3": "ready-for-dev",
                        "1.4": "backlog",
                    },
                },
                "2": {
                    "status": "backlog",
                    "stories": {"2.1": "ready-for-dev"},
                },
            }
        },
    )
    out = read_sprint_progress(settings)
    assert out.total == 5
    assert out.done == 2
    assert "1.3" in out.ready_next and "2.1" in out.ready_next
    assert "1.1" in out.done_recent and "1.2" in out.done_recent


def test_read_sprint_progress_broken_yaml_degrades(
    settings: Settings, project_root: Path
) -> None:
    path = project_root / "_bmad" / "implementation-artifacts" / "sprint-status.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("epics: [: broken\n", encoding="utf-8")
    out = read_sprint_progress(settings)
    assert out == SprintProgress()


# ── read_active_workers ─────────────────────────────────────────────────────


def test_read_active_workers_missing_wave_dir(settings: Settings) -> None:
    assert read_active_workers(settings, "ghost-wave") == []


def test_read_active_workers_spawned_only_is_active(
    settings: Settings, project_root: Path
) -> None:
    _write_events(
        project_root, "1a", "wt-1",
        [
            {"event_type": "worker_spawned", "worktree": "wt-1",
             "story_id": "1.3", "pid": 0},
            {"event_type": "stdout_line", "text": "hello"},
        ],
    )
    workers = read_active_workers(settings, "1a")
    assert len(workers) == 1
    assert workers[0]["story_id"] == "1.3"
    assert workers[0]["worktree"] == "wt-1"
    assert workers[0]["state"] == "active"


def test_read_active_workers_completed_drops_out(
    settings: Settings, project_root: Path
) -> None:
    _write_events(
        project_root, "1a", "wt-1",
        [
            {"event_type": "worker_spawned", "worktree": "wt-1",
             "story_id": "1.3", "pid": 0},
            {"event_type": "worker_completed", "worktree": "wt-1",
             "story_id": "1.3", "status": "success"},
        ],
    )
    assert read_active_workers(settings, "1a") == []


def test_read_active_workers_halt_file_drops_out(
    settings: Settings, project_root: Path
) -> None:
    _write_events(
        project_root, "1a", "wt-1",
        [
            {"event_type": "worker_spawned", "worktree": "wt-1",
             "story_id": "1.3", "pid": 0},
            {"event_type": "worker_halt_file", "worktree": "wt-1",
             "story_id": "1.3"},
        ],
    )
    assert read_active_workers(settings, "1a") == []


def test_read_active_workers_corrupted_line_skipped(
    settings: Settings, project_root: Path
) -> None:
    path = project_root / "_bmad-output" / "runs" / "1a" / "wt-1.events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"event_type": "worker_spawned", "story_id": "1.3", "pid": 0}\n'
        "not json at all\n",
        encoding="utf-8",
    )
    workers = read_active_workers(settings, "1a")
    assert len(workers) == 1
    assert workers[0]["story_id"] == "1.3"


def test_read_active_workers_dead_pid_marks_halted(
    settings: Settings, project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "bmad_orchestrator.cli.snapshot.is_pid_alive", lambda pid: False
    )
    _write_events(
        project_root, "1a", "wt-1",
        [{"event_type": "worker_spawned", "story_id": "1.3", "pid": 99999}],
    )
    workers = read_active_workers(settings, "1a")
    assert workers[0]["state"] == "halted"


# ── read_events_tail ────────────────────────────────────────────────────────


def test_read_events_tail_missing_wave(settings: Settings) -> None:
    assert read_events_tail(settings, "ghost") == []


def test_read_events_tail_returns_formatted_lines(
    settings: Settings, project_root: Path
) -> None:
    _write_events(
        project_root, "1a", "wt-1",
        [
            {"event_type": "worker_spawned", "story_id": "1.3"},
            {"event_type": "stdout_line", "text": "compiling..."},
            {"event_type": "worker_completed", "story_id": "1.3"},
        ],
    )
    lines = read_events_tail(settings, "1a", limit=10)
    assert any("worker_spawned 1.3" in line for line in lines)
    assert any("compiling..." in line for line in lines)
    # Each line is prefixed with the worktree
    assert all(line.startswith("[wt-1]") for line in lines)


# ── derive_agent_thinking ───────────────────────────────────────────────────


def test_derive_agent_thinking_running_with_workers() -> None:
    text = derive_agent_thinking("running", [{"story_id": "1.1"}])
    assert "1" in text
    assert "worker" in text.lower()


def test_derive_agent_thinking_running_without_workers() -> None:
    assert derive_agent_thinking("running", []) == "пилотирую wave"


def test_derive_agent_thinking_paused() -> None:
    assert "пауз" in derive_agent_thinking("paused", []).lower()


def test_derive_agent_thinking_idle() -> None:
    text = derive_agent_thinking("idle", [])
    # i18n fallback returns either translated string or the key itself
    assert text and text != "running"


# ── _build_snapshot integration ─────────────────────────────────────────────


@pytest.fixture
def isolate_env(
    monkeypatch: pytest.MonkeyPatch, project_root: Path, tmp_path: Path
) -> Iterator[None]:
    """Bind env vars so `load_settings()` inside `_build_snapshot` sees our fixtures."""
    monkeypatch.setenv("ORCHESTRATOR_TARGET_PROJECT", str(project_root))
    monkeypatch.setenv("ORCHESTRATOR_STATE_DB", str(tmp_path / "state.db"))
    monkeypatch.setenv("ORCHESTRATOR_ORCHESTRATOR_HOME", str(tmp_path / "orch"))
    monkeypatch.delenv("BMAD_PROJECTS_REGISTRY", raising=False)
    yield


def test_build_snapshot_empty_everything_yields_idle(
    isolate_env: None,
) -> None:
    from bmad_orchestrator.cli.main import _build_snapshot

    snap = _build_snapshot()
    assert snap.status == "idle"
    assert snap.progress_done == 0
    assert snap.progress_total == 0
    assert snap.workers == []
    assert snap.events_tail == []


def test_build_snapshot_reads_all_sources(
    isolate_env: None, project_root: Path, tmp_path: Path
) -> None:
    from bmad_orchestrator.cli.main import _build_snapshot

    _seed_state_db(
        tmp_path / "state.db",
        sessions=[
            {
                "id": 1, "target_project": project_root.name,
                "wave": "1a", "status": "running",
                "started_at": "2026-05-19T00:00:00+00:00",
            }
        ],
        budgets=[
            {"session_id": 1, "scope": "day", "spent_usd": 42.0,
             "alarm_threshold": 400.0, "halt_threshold": 500.0},
        ],
    )
    _write_sprint_status(
        project_root,
        {
            "epics": {
                "1": {
                    "status": "in-progress",
                    "stories": {"1.1": "done", "1.2": "ready-for-dev"},
                }
            }
        },
    )
    _write_events(
        project_root, "1a", "wt-1",
        [{"event_type": "worker_spawned", "story_id": "1.2", "pid": 0}],
    )

    snap = _build_snapshot()
    assert snap.status == "running"
    assert snap.wave == "1a"
    assert snap.project == project_root.name
    assert snap.progress_done == 1
    assert snap.progress_total == 2
    assert snap.budget_spent_usd == pytest.approx(42.0)
    assert snap.budget_level == "ok"
    assert len(snap.workers) == 1
    assert snap.workers[0]["story_id"] == "1.2"


def test_build_snapshot_override_args_win_over_session(
    isolate_env: None, project_root: Path, tmp_path: Path
) -> None:
    from bmad_orchestrator.cli.main import _build_snapshot

    _seed_state_db(
        tmp_path / "state.db",
        sessions=[
            {
                "id": 1, "target_project": project_root.name,
                "wave": "1a", "status": "running",
                "started_at": "2026-05-19T00:00:00+00:00",
            }
        ],
    )
    snap = _build_snapshot(project="custom", wave="custom-wave")
    assert snap.project == "custom"
    assert snap.wave == "custom-wave"
