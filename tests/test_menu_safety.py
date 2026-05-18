"""Unit tests for destructive-command classifier."""

from __future__ import annotations

import pytest

from bmad_orchestrator.cli.menu.safety import DESTRUCTIVE_COMMANDS, is_destructive


class TestDestructiveClassifier:
    @pytest.mark.parametrize(
        "path",
        [
            ("stop",),
            ("policy-rollback",),
            ("skill-update",),
            ("self-learning", "rollback"),
            ("multi",),
            ("run",),
        ],
    )
    def test_known_destructive_paths(self, path: tuple[str, ...]) -> None:
        assert is_destructive(path) is True

    @pytest.mark.parametrize(
        "path",
        [
            ("status",),
            ("dag",),
            ("model", "show"),
            ("budget",),
            ("pause",),
            ("resume",),
            ("scan",),
            ("self-learning", "run"),
            ("self-learning", "status"),
            ("bot", "start"),
        ],
    )
    def test_non_destructive_paths(self, path: tuple[str, ...]) -> None:
        assert is_destructive(path) is False

    def test_empty_tuple_not_destructive(self) -> None:
        assert is_destructive(()) is False

    def test_exactly_six_destructive_entries(self) -> None:
        assert len(DESTRUCTIVE_COMMANDS) == 6

    def test_all_entries_are_tuples(self) -> None:
        for entry in DESTRUCTIVE_COMMANDS:
            assert isinstance(entry, tuple)
            assert all(isinstance(s, str) for s in entry)
