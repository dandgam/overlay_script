#!/usr/bin/env python3
"""Unit tests for batch_gate.py — boundary detection in BMad auto-dev pipeline."""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from batch_gate import (  # noqa: E402
    DEFAULT_CONFIG_VALUES,
    check_boundary,
    is_gate_story,
    load_config,
    parse_epics_extended,
    read_state,
)

EPICS_FIXTURE = """\
## Epic 1 — Foundation

#### Story 1.1: Rust workspace scaffold

**As a** dev, **I want** a workspace, **so that** I can build.

**Acceptance Criteria:**
- Given a fresh checkout
- Then `cargo check` passes

**Wave:** 0a
**Type:** standard
**Dependencies:** None

---

#### Story 1.2: pnpm frontend scaffold

**Wave:** 0a
**Dependencies:** Story 1.1

---

##### Story 1.7: 🚦 Rollback gate review (≤1.8× estimate)

**Acceptance Criteria:**
- ratio ≤ 1.8 → GO

**Wave:** 0a (Spike per ARC-40)
**Type:** 🚦 GATE story
**Dependencies:** Story 1.2

---

#### Story 1.8: First Wave 0b story

**Wave:** 0b
**Type:** standard
**Dependencies:** Story 1.7

---

#### Story 2.1: Some story with no Type field

**Wave:** 0a
**Dependencies:** None
"""

# Real sprint-status.yaml uses inline `key: value` (not nested mapping).
STATUS_FIXTURE = """\
development_status:
  epic-1: in-progress
  1-1-rust-workspace-scaffold: done
  1-2-pnpm-frontend-scaffold: done
  1-7-rollback-gate-review: backlog
  1-8-first-wave-0b-story: backlog
  2-1-some-story-with-no-type-field: backlog
"""

CONFIG_FIXTURE = """\
[batch]
size = 10
wave_boundary = true
gate_stories = true
gate_story_ids = ["3.5"]
"""


class _Fixture(unittest.TestCase):
    """Base — creates temp epics.md/status.yaml/customize.toml/state.json."""

    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

        self.epics = self.root / "epics.md"
        self.status = self.root / "status.yaml"
        self.config = self.root / "customize.toml"
        self.state = self.root / "state.json"

        self.epics.write_text(EPICS_FIXTURE, encoding="utf-8")
        self.status.write_text(STATUS_FIXTURE, encoding="utf-8")
        self.config.write_text(CONFIG_FIXTURE, encoding="utf-8")


class TestLoadConfig(_Fixture):
    def test_missing_file_returns_defaults(self) -> None:
        cfg = load_config(self.root / "nope.toml")
        self.assertEqual(cfg, DEFAULT_CONFIG_VALUES)

    def test_full_config_loaded(self) -> None:
        cfg = load_config(self.config)
        self.assertEqual(cfg["batch_size"], 10)
        self.assertTrue(cfg["wave_boundary"])
        self.assertTrue(cfg["gate_stories"])
        self.assertEqual(cfg["gate_story_ids"], ["3.5"])

    def test_partial_config_falls_back_to_defaults(self) -> None:
        partial = self.root / "partial.toml"
        partial.write_text("[batch]\nsize = 5\n", encoding="utf-8")
        cfg = load_config(partial)
        self.assertEqual(cfg["batch_size"], 5)
        self.assertEqual(cfg["wave_boundary"], DEFAULT_CONFIG_VALUES["wave_boundary"])
        self.assertEqual(cfg["gate_story_ids"], [])

    def test_invalid_size_ignored(self) -> None:
        bad = self.root / "bad.toml"
        bad.write_text("[batch]\nsize = -5\n", encoding="utf-8")
        cfg = load_config(bad)
        self.assertEqual(cfg["batch_size"], DEFAULT_CONFIG_VALUES["batch_size"])

    def test_malformed_toml_returns_defaults(self) -> None:
        bad = self.root / "bad.toml"
        bad.write_text("[[[ not valid toml", encoding="utf-8")
        cfg = load_config(bad)
        self.assertEqual(cfg, DEFAULT_CONFIG_VALUES)


class TestParseEpicsExtended(_Fixture):
    def test_extracts_title_wave_type_deps(self) -> None:
        stories = parse_epics_extended(self.epics)
        self.assertIn("1.7", stories)
        s17 = stories["1.7"]
        self.assertEqual(s17["title"], "🚦 Rollback gate review (≤1.8× estimate)")
        self.assertEqual(s17["wave"], "0a")
        self.assertEqual(s17["type"], "🚦 GATE story")
        self.assertEqual(s17["deps"], ["1.2"])

    def test_missing_type_field_is_none(self) -> None:
        stories = parse_epics_extended(self.epics)
        self.assertIsNone(stories["2.1"]["type"])

    def test_order_preserved(self) -> None:
        stories = parse_epics_extended(self.epics)
        ordered_ids = [sid for sid, _ in sorted(stories.items(), key=lambda x: x[1]["order"])]
        self.assertEqual(ordered_ids[:3], ["1.1", "1.2", "1.7"])

    def test_deps_none_parsed_as_empty(self) -> None:
        stories = parse_epics_extended(self.epics)
        self.assertEqual(stories["1.1"]["deps"], [])


class TestIsGateStory(_Fixture):
    def setUp(self) -> None:
        super().setUp()
        self.stories = parse_epics_extended(self.epics)

    def test_type_marker_detects_gate(self) -> None:
        is_gate, detail = is_gate_story("1.7", self.stories, [])
        self.assertTrue(is_gate)
        self.assertEqual(detail, "type_marker")

    def test_config_list_overrides(self) -> None:
        is_gate, detail = is_gate_story("1.1", self.stories, ["1.1"])
        self.assertTrue(is_gate)
        self.assertEqual(detail, "config_list")

    def test_title_keyword_when_no_type_field(self) -> None:
        custom_epics = self.root / "custom.md"
        custom_epics.write_text(
            "#### Story 9.9: rollback gate review fallback\n\n**Wave:** 0z\n",
            encoding="utf-8",
        )
        stories = parse_epics_extended(custom_epics)
        is_gate, detail = is_gate_story("9.9", stories, [])
        self.assertTrue(is_gate)
        self.assertTrue(detail.startswith("title_kw="))

    def test_non_gate_returns_false(self) -> None:
        is_gate, detail = is_gate_story("1.1", self.stories, [])
        self.assertFalse(is_gate)
        self.assertEqual(detail, "")

    def test_unknown_story_returns_false(self) -> None:
        is_gate, detail = is_gate_story("99.99", self.stories, [])
        self.assertFalse(is_gate)
        self.assertEqual(detail, "unknown_story")


class TestReadState(_Fixture):
    def test_missing_file_returns_empty(self) -> None:
        st = read_state(self.root / "nope.json")
        self.assertEqual(st, {"stories": [], "last_story_id": None})

    def test_valid_state(self) -> None:
        self.state.write_text(
            json.dumps({"stories": ["1.1", "1.2"], "last_story_id": "1.2"}),
            encoding="utf-8",
        )
        st = read_state(self.state)
        self.assertEqual(st["stories"], ["1.1", "1.2"])
        self.assertEqual(st["last_story_id"], "1.2")

    def test_malformed_json_returns_empty(self) -> None:
        self.state.write_text("{not valid", encoding="utf-8")
        st = read_state(self.state)
        self.assertEqual(st, {"stories": [], "last_story_id": None})

    def test_non_dict_json_returns_empty(self) -> None:
        self.state.write_text("[]", encoding="utf-8")
        st = read_state(self.state)
        self.assertEqual(st, {"stories": [], "last_story_id": None})

    def test_non_list_stories_field_coerced(self) -> None:
        self.state.write_text(
            json.dumps({"stories": "1.1", "last_story_id": "1.1"}),
            encoding="utf-8",
        )
        st = read_state(self.state)
        self.assertEqual(st["stories"], [])
        self.assertEqual(st["last_story_id"], "1.1")


def _write_status(root: Path, **overrides: str) -> Path:
    """Build a status YAML with default 'backlog' for unset stories and overrides."""
    defaults = {
        "1-1-rust-workspace-scaffold": "done",
        "1-2-pnpm-frontend-scaffold": "done",
        "1-7-rollback-gate-review": "backlog",
        "1-8-first-wave-0b-story": "backlog",
        "2-1-some-story-with-no-type-field": "backlog",
    }
    defaults.update(overrides)
    body = "development_status:\n  epic-1: in-progress\n"
    for k, v in defaults.items():
        body += f"  {k}: {v}\n"
    path = root / f"status_{len(overrides)}.yaml"
    path.write_text(body, encoding="utf-8")
    return path


class TestCheckBoundary(_Fixture):
    def setUp(self) -> None:
        super().setUp()
        self.stories = parse_epics_extended(self.epics)
        from dependency_analyzer import parse_sprint_status
        self.statuses = parse_sprint_status(self.status)
        self.cfg = load_config(self.config)

    def test_empty_state_no_state(self) -> None:
        res = check_boundary(
            {"stories": [], "last_story_id": None}, self.cfg, self.stories, self.statuses
        )
        self.assertFalse(res["boundary"])
        self.assertEqual(res["reason"], "no_state")

    def test_batch_size_exact_triggers(self) -> None:
        state = {"stories": [f"x{i}" for i in range(10)], "last_story_id": "1.2"}
        res = check_boundary(state, self.cfg, self.stories, self.statuses)
        self.assertTrue(res["boundary"])
        self.assertEqual(res["reason"], "batch_size_reached")
        self.assertEqual(res["size"], 10)
        self.assertEqual(res["cap"], 10)

    def test_batch_size_over_triggers(self) -> None:
        state = {"stories": [f"x{i}" for i in range(15)], "last_story_id": "1.2"}
        res = check_boundary(state, self.cfg, self.stories, self.statuses)
        self.assertTrue(res["boundary"])
        self.assertEqual(res["reason"], "batch_size_reached")

    def test_gate_story_triggers(self) -> None:
        state = {"stories": ["1.1", "1.7"], "last_story_id": "1.7"}
        res = check_boundary(state, self.cfg, self.stories, self.statuses)
        self.assertTrue(res["boundary"])
        self.assertEqual(res["reason"], "gate_story")
        self.assertEqual(res["story_id"], "1.7")
        self.assertEqual(res["detail"], "type_marker")

    def test_gate_disabled_falls_through_to_wave_transition(self) -> None:
        cfg = {**self.cfg, "gate_stories": False}
        from dependency_analyzer import parse_sprint_status
        custom_status = _write_status(self.root, **{"1-7-rollback-gate-review": "done"})
        statuses = parse_sprint_status(custom_status)
        state = {"stories": ["1.1", "1.7"], "last_story_id": "1.7"}
        res = check_boundary(state, cfg, self.stories, statuses)
        # last=1.7 (wave 0a), next_ready iter: 1.1 done, 1.2 done, 1.7 done, 1.8 deps=[1.7] done → 1.8 wave 0b
        self.assertTrue(res["boundary"])
        self.assertEqual(res["reason"], "wave_transition")
        self.assertEqual(res["from"], "0a")
        self.assertEqual(res["to"], "0b")

    def test_wave_transition_triggers_when_not_gate(self) -> None:
        from dependency_analyzer import parse_sprint_status
        custom_status = _write_status(self.root, **{"1-7-rollback-gate-review": "done"})
        statuses = parse_sprint_status(custom_status)
        state = {"stories": ["1.1", "1.2", "1.7"], "last_story_id": "1.2"}
        # 1.2 not a gate (no type marker, no title kw, not in config list).
        res = check_boundary(state, self.cfg, self.stories, statuses)
        self.assertTrue(res["boundary"])
        self.assertEqual(res["reason"], "wave_transition")
        self.assertEqual(res["from"], "0a")
        self.assertEqual(res["to"], "0b")

    def test_wave_disabled_returns_continue(self) -> None:
        cfg = {**self.cfg, "wave_boundary": False, "gate_stories": False}
        state = {"stories": ["1.1", "1.2"], "last_story_id": "1.2"}
        res = check_boundary(state, cfg, self.stories, self.statuses)
        self.assertFalse(res["boundary"])
        self.assertEqual(res["reason"], "continue")

    def test_same_wave_returns_continue(self) -> None:
        # last=1.1 (wave 0a), next_ready iter: 1.1 done, 1.2 done, 1.7 backlog deps=[1.2] done → 1.7 wave 0a.
        state = {"stories": ["1.1"], "last_story_id": "1.1"}
        res = check_boundary(state, self.cfg, self.stories, self.statuses)
        self.assertFalse(res["boundary"])
        self.assertEqual(res["reason"], "continue")

    def test_priority_cap_over_gate(self) -> None:
        state = {"stories": [f"x{i}" for i in range(10)], "last_story_id": "1.7"}
        res = check_boundary(state, self.cfg, self.stories, self.statuses)
        self.assertEqual(res["reason"], "batch_size_reached")

    def test_priority_gate_over_wave(self) -> None:
        from dependency_analyzer import parse_sprint_status
        custom_status = _write_status(self.root, **{"1-7-rollback-gate-review": "done"})
        statuses = parse_sprint_status(custom_status)
        state = {"stories": ["1.1", "1.7"], "last_story_id": "1.7"}
        # last=1.7 is gate AND next=1.8 (different wave) — gate wins by priority.
        res = check_boundary(state, self.cfg, self.stories, statuses)
        self.assertEqual(res["reason"], "gate_story")

    def test_all_done_no_wave_transition(self) -> None:
        from dependency_analyzer import parse_sprint_status
        custom_status = _write_status(
            self.root,
            **{
                "1-7-rollback-gate-review": "done",
                "1-8-first-wave-0b-story": "done",
                "2-1-some-story-with-no-type-field": "done",
            },
        )
        statuses = parse_sprint_status(custom_status)
        state = {"stories": ["1.1", "1.2"], "last_story_id": "1.2"}
        res = check_boundary(state, self.cfg, self.stories, statuses)
        self.assertFalse(res["boundary"])
        self.assertEqual(res["reason"], "continue")

    def test_config_list_gate_match(self) -> None:
        cfg = {**self.cfg, "gate_story_ids": ["1.2"]}
        state = {"stories": ["1.1", "1.2"], "last_story_id": "1.2"}
        res = check_boundary(state, cfg, self.stories, self.statuses)
        self.assertTrue(res["boundary"])
        self.assertEqual(res["reason"], "gate_story")
        self.assertEqual(res["detail"], "config_list")


class TestCliSmoke(_Fixture):
    SCRIPT = SCRIPT_DIR / "batch_gate.py"

    def _run(self, *args: str) -> dict:
        result = subprocess.run(
            [sys.executable, str(self.SCRIPT), "--check", *args],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=f"stderr={result.stderr}")
        return json.loads(result.stdout.strip())

    def test_cli_no_state_returns_no_state(self) -> None:
        res = self._run(
            "--epics", str(self.epics),
            "--status", str(self.status),
            "--state-file", str(self.state),
            "--config", str(self.config),
        )
        self.assertEqual(res["reason"], "no_state")

    def test_cli_with_overrides_batch_count(self) -> None:
        res = self._run(
            "--epics", str(self.epics),
            "--status", str(self.status),
            "--state-file", str(self.state),
            "--config", str(self.config),
            "--last-story", "1.2",
            "--batch-count", "10",
        )
        self.assertEqual(res["reason"], "batch_size_reached")

    def test_cli_with_gate_story_override(self) -> None:
        res = self._run(
            "--epics", str(self.epics),
            "--status", str(self.status),
            "--state-file", str(self.state),
            "--config", str(self.config),
            "--last-story", "1.7",
            "--batch-count", "2",
        )
        self.assertEqual(res["reason"], "gate_story")
        self.assertEqual(res["story_id"], "1.7")


if __name__ == "__main__":
    unittest.main()
