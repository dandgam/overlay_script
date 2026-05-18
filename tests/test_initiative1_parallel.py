"""Initiative #1 part A — parallel CLI flag + file-conflict pre-check.

Coverage:
  * Task 1.1 — ``--parallel`` CLI preset validation (1/3/5/10 accepted,
    other ints rejected via ``typer.BadParameter``; --parallel wins over
    --max-parallel when both supplied).
  * Task 1.2 — ``agent/file_conflict.py`` find_conflicts + split_batch
    edge cases (empty, single, non-overlap, overlap, multi-overlap,
    touches_shared coverage, max_parallel cap, max_parallel<=0).
"""

from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner

from bmad_orchestrator.agent.file_conflict import find_conflicts, split_batch
from bmad_orchestrator.cli.main import PARALLEL_PRESETS, app

# ── Task 1.1 — CLI --parallel preset validation ──────────────────────────────


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def test_parallel_presets_match_spec() -> None:
    assert PARALLEL_PRESETS == (1, 3, 5, 10), \
        "spec_parallelism_initiatives Task 1.1 freezes presets at 1/3/5/10; " \
        "adding values requires a matching sandbox preset (Task 1.3)."


@pytest.mark.parametrize("bad_value", ["0", "2", "4", "11", "-1", "100"])
def test_parallel_rejects_non_preset_values(
    runner: CliRunner, bad_value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invalid preset → typer.BadParameter, no orchestrator run."""
    called: dict[str, Any] = {}

    def _fail_if_called(**_kwargs: Any) -> None:  # pragma: no cover
        called["yes"] = True

    monkeypatch.setattr(
        "bmad_orchestrator.cli.main.run_orchestrator", _fail_if_called
    )
    result = runner.invoke(
        app,
        [
            "run",
            "--project", "antares",
            "--wave", "smoke",
            "--parallel", bad_value,
            "--mock",
        ],
    )
    assert result.exit_code != 0, result.output
    assert "--parallel" in result.output
    assert "1, 3, 5, 10" in result.output
    assert "yes" not in called, "orchestrator must not run on bad --parallel"


@pytest.mark.parametrize("good_value", ["1", "3", "5", "10"])
def test_parallel_accepts_preset_values(
    runner: CliRunner, good_value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Valid preset → orchestrator invoked with max_parallel = preset."""
    captured: dict[str, Any] = {}

    async def _capture(**kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr("bmad_orchestrator.cli.main.run_orchestrator", _capture)
    result = runner.invoke(
        app,
        [
            "run",
            "--project", "antares",
            "--wave", "smoke",
            "--parallel", good_value,
            "--mock",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured.get("max_parallel") == int(good_value)


def test_parallel_overrides_max_parallel(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When both --parallel and --max-parallel given, --parallel wins."""
    captured: dict[str, Any] = {}

    async def _capture(**kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr("bmad_orchestrator.cli.main.run_orchestrator", _capture)
    result = runner.invoke(
        app,
        [
            "run",
            "--project", "antares",
            "--wave", "smoke",
            "--max-parallel", "7",
            "--parallel", "5",
            "--mock",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured.get("max_parallel") == 5


def test_max_parallel_still_works_without_parallel(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Backwards compat: --max-parallel alone keeps working."""
    captured: dict[str, Any] = {}

    async def _capture(**kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr("bmad_orchestrator.cli.main.run_orchestrator", _capture)
    result = runner.invoke(
        app,
        [
            "run",
            "--project", "antares",
            "--wave", "smoke",
            "--max-parallel", "7",
            "--mock",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured.get("max_parallel") == 7


# ── Task 1.2 — find_conflicts ────────────────────────────────────────────────


def _story(sid: str, files: list[str], shared: list[str] | None = None) -> dict[str, Any]:
    s: dict[str, Any] = {"id": sid, "touches_files": files}
    if shared is not None:
        s["touches_shared"] = shared
    return s


def test_find_conflicts_empty() -> None:
    assert find_conflicts([]) == []


def test_find_conflicts_single_story() -> None:
    assert find_conflicts([_story("a", ["src/x.py"])]) == []


def test_find_conflicts_disjoint() -> None:
    stories = [
        _story("a", ["src/x.py"]),
        _story("b", ["src/y.py"]),
        _story("c", ["src/z.py"]),
    ]
    assert find_conflicts(stories) == []


def test_find_conflicts_single_overlap() -> None:
    stories = [
        _story("a", ["src/x.py", "src/a.py"]),
        _story("b", ["src/x.py", "src/b.py"]),
    ]
    conflicts = find_conflicts(stories)
    assert conflicts == [("a", "b", "src/x.py")]


def test_find_conflicts_multi_overlap_stable_order() -> None:
    """Multiple shared files between two stories → one tuple per file, sorted by file."""
    stories = [
        _story("a", ["src/x.py", "src/z.py"]),
        _story("b", ["src/x.py", "src/z.py"]),
    ]
    conflicts = find_conflicts(stories)
    assert conflicts == [("a", "b", "src/x.py"), ("a", "b", "src/z.py")]


def test_find_conflicts_touches_shared_treated_like_files() -> None:
    """touches_shared is also a merge-conflict risk and must be detected."""
    stories = [
        _story("a", ["src/x.py"], shared=["config/global.yaml"]),
        _story("b", ["src/y.py"], shared=["config/global.yaml"]),
    ]
    conflicts = find_conflicts(stories)
    assert conflicts == [("a", "b", "config/global.yaml")]


def test_find_conflicts_first_owner_wins() -> None:
    """Third story conflicting with first AND second reports against FIRST owner only."""
    stories = [
        _story("a", ["src/x.py"]),
        _story("b", ["src/y.py"]),
        _story("c", ["src/x.py", "src/y.py"]),
    ]
    conflicts = find_conflicts(stories)
    assert conflicts == [("a", "c", "src/x.py"), ("b", "c", "src/y.py")]


def test_find_conflicts_raises_on_idless_story() -> None:
    """Review finding H-6 — missing id is upstream bug, must surface loudly."""
    stories = [{"touches_files": ["src/x.py"]}, _story("b", ["src/x.py"])]
    with pytest.raises(ValueError, match="story missing id"):
        find_conflicts(stories)


# ── Task 1.2 — split_batch ───────────────────────────────────────────────────


def test_split_batch_empty() -> None:
    parallel, deferred = split_batch([], 3)
    assert parallel == []
    assert deferred == []


def test_split_batch_no_conflicts_under_cap() -> None:
    stories = [_story(c, [f"src/{c}.py"]) for c in "abc"]
    parallel, deferred = split_batch(stories, 3)
    assert [s["id"] for s in parallel] == ["a", "b", "c"]
    assert deferred == []


def test_split_batch_cap_pushes_extras_to_deferred() -> None:
    stories = [_story(c, [f"src/{c}.py"]) for c in "abcde"]
    parallel, deferred = split_batch(stories, 3)
    assert [s["id"] for s in parallel] == ["a", "b", "c"]
    assert [s["id"] for s in deferred] == ["d", "e"]


def test_split_batch_conflict_pushes_to_deferred() -> None:
    """When two stories touch the same file, only first goes to parallel."""
    stories = [
        _story("a", ["src/x.py"]),
        _story("b", ["src/x.py"]),
        _story("c", ["src/y.py"]),
    ]
    parallel, deferred = split_batch(stories, 3)
    assert [s["id"] for s in parallel] == ["a", "c"]
    assert [s["id"] for s in deferred] == ["b"]


def test_split_batch_max_parallel_zero_defers_everything() -> None:
    stories = [_story(c, [f"src/{c}.py"]) for c in "ab"]
    parallel, deferred = split_batch(stories, 0)
    assert parallel == []
    assert [s["id"] for s in deferred] == ["a", "b"]


def test_split_batch_negative_max_parallel_defers_everything() -> None:
    stories = [_story("a", ["src/x.py"])]
    parallel, deferred = split_batch(stories, -1)
    assert parallel == []
    assert [s["id"] for s in deferred] == ["a"]


def test_split_batch_shared_claim_blocks_subsequent() -> None:
    """touches_shared claim by first story also blocks subsequent peers."""
    stories = [
        _story("a", ["src/x.py"], shared=["config/global.yaml"]),
        _story("b", ["src/y.py"], shared=["config/global.yaml"]),
    ]
    parallel, deferred = split_batch(stories, 5)
    assert [s["id"] for s in parallel] == ["a"]
    assert [s["id"] for s in deferred] == ["b"]


def test_split_batch_does_not_mutate_input() -> None:
    """split_batch must not modify the supplied story dicts."""
    stories = [_story("a", ["src/x.py"]), _story("b", ["src/x.py"])]
    snapshot = [{k: v for k, v in s.items()} for s in stories]
    split_batch(stories, 3)
    assert stories == snapshot
