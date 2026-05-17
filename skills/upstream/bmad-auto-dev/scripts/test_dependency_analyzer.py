#!/usr/bin/env python3
"""Unit tests for dependency_analyzer.py (stdlib unittest, no pytest dep)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from textwrap import dedent

sys.path.insert(0, str(Path(__file__).parent))
import dependency_analyzer as da  # noqa: E402


def _tmp(text: str, suffix: str) -> Path:
    f = tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False, encoding="utf-8")
    f.write(dedent(text))
    f.flush()
    return Path(f.name)


def epics(text: str) -> Path:
    return _tmp(text, ".md")


def status(text: str) -> Path:
    return _tmp(text, ".yaml")


class TestKeyToStoryId(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(da.key_to_story_id("1-1-rust-workspace-scaffold"), "1.1")

    def test_subletter(self):
        self.assertEqual(da.key_to_story_id("1-8a-postgres-rls-guc"), "1.8a")
        self.assertEqual(da.key_to_story_id("2-4b-consent"), "2.4b")

    def test_story_prefix(self):
        self.assertEqual(da.key_to_story_id("story-0-0-legal-consultation"), "0.0")

    def test_epic_skipped(self):
        self.assertIsNone(da.key_to_story_id("epic-1"))
        self.assertIsNone(da.key_to_story_id("epic-13-retrospective"))

    def test_multi_digit(self):
        self.assertEqual(da.key_to_story_id("10-23-claim-flow"), "10.23")
        self.assertEqual(da.key_to_story_id("11-15-consent-revocation-cascade"), "11.15")


class TestParseEpics(unittest.TestCase):
    def test_no_deps_means_empty(self):
        path = epics(
            """
            ##### Story 1.1: First
            **Wave:** 0a (Spike per ARC-40)
            """
        )
        result = da.parse_epics(path)
        self.assertIn("1.1", result)
        self.assertEqual(result["1.1"]["deps"], [])
        self.assertEqual(result["1.1"]["wave"], "0a")

    def test_deps_none(self):
        path = epics(
            """
            #### Story 2.3: Cookie banner
            **Dependencies:** None
            **Wave:** 1a
            """
        )
        result = da.parse_epics(path)
        self.assertEqual(result["2.3"]["deps"], [])

    def test_deps_multi(self):
        path = epics(
            """
            #### Story 3.3: Onboarding
            **Dependencies:** Story 2.0, Story 2.11, Story 3.1, Story 3.2
            **Wave:** 1a
            """
        )
        self.assertEqual(
            da.parse_epics(path)["3.3"]["deps"],
            ["2.0", "2.11", "3.1", "3.2"],
        )

    def test_subletter_story_id(self):
        path = epics(
            """
            ##### Story 1.8a: PostgreSQL RLS GUC migration
            **Dependencies:** Story 1.1
            **Wave:** 0b
            """
        )
        self.assertEqual(da.parse_epics(path)["1.8a"]["deps"], ["1.1"])


class TestSimpleDeps(unittest.TestCase):
    def test_first_story_returned_when_none_done(self):
        e = epics(
            """
            ##### Story 1.1: First
            **Wave:** 0a

            ##### Story 1.2: Second
            **Dependencies:** Story 1.1
            **Wave:** 0a
            """
        )
        s = status(
            """
            development_status:
              1-1-first: backlog
              1-2-second: backlog
            """
        )
        result = da.next_ready(da.parse_epics(e), da.parse_sprint_status(s))
        self.assertEqual(result["story_id"], "1.1")
        self.assertEqual(result["wave"], "0a")
        self.assertTrue(result["deps_met"])

    def test_second_story_after_first_done(self):
        e = epics(
            """
            ##### Story 1.1: First
            **Wave:** 0a
            ##### Story 1.2: Second
            **Dependencies:** Story 1.1
            **Wave:** 0a
            """
        )
        s = status(
            """
            development_status:
              1-1-first: done
              1-2-second: backlog
            """
        )
        result = da.next_ready(da.parse_epics(e), da.parse_sprint_status(s))
        self.assertEqual(result["story_id"], "1.2")


class TestTransitiveDeps(unittest.TestCase):
    def test_chain_returns_first_unmet(self):
        e = epics(
            """
            ##### Story 1.1: A
            **Wave:** 0a
            ##### Story 1.2: B
            **Dependencies:** Story 1.1
            **Wave:** 0a
            ##### Story 1.3: C
            **Dependencies:** Story 1.2
            **Wave:** 0a
            """
        )
        s = status(
            """
            development_status:
              1-1-a: done
              1-2-b: backlog
              1-3-c: backlog
            """
        )
        result = da.next_ready(da.parse_epics(e), da.parse_sprint_status(s))
        self.assertEqual(result["story_id"], "1.2")


class TestDeferred(unittest.TestCase):
    def test_inline_deferred_skipped(self):
        e = epics(
            """
            #### Story 0.0: Legal
            **Wave:** Pre-0a
            ##### Story 1.1: First
            **Wave:** 0a
            """
        )
        s = status(
            """
            development_status:
              story-0-0-legal: backlog  # ⏸ DEFERRED post-Mesai
              1-1-first: backlog
            """
        )
        statuses = da.parse_sprint_status(s)
        self.assertTrue(statuses["0.0"]["deferred"])
        self.assertFalse(statuses["1.1"]["deferred"])
        result = da.next_ready(da.parse_epics(e), statuses)
        self.assertEqual(result["story_id"], "1.1")

    def test_section_deferred_propagates(self):
        s = status(
            """
            development_status:
              13-0-booking: backlog
              # DEFERRED → Nikii Wave 4
              13-1-calendar-resources: backlog
              13-2-booking-model: backlog
            """
        )
        statuses = da.parse_sprint_status(s)
        self.assertFalse(statuses["13.0"]["deferred"])
        self.assertTrue(statuses["13.1"]["deferred"])
        self.assertTrue(statuses["13.2"]["deferred"])

    def test_deferred_dep_does_not_block(self):
        e = epics(
            """
            #### Story 0.0: Legal
            **Wave:** Pre-0a
            ##### Story 1.1: First
            **Dependencies:** Story 0.0
            **Wave:** 0a
            """
        )
        s = status(
            """
            development_status:
              story-0-0-legal: backlog  # ⏸ DEFERRED
              1-1-first: backlog
            """
        )
        result = da.next_ready(da.parse_epics(e), da.parse_sprint_status(s))
        self.assertEqual(result["story_id"], "1.1")

    def test_section_break_resets_deferred(self):
        s = status(
            """
            development_status:
              # ============================================================
              # Epic 12 section header — Stories 12.99 DEFERRED mention
              # ============================================================
              12-1-foo: backlog
              13-1-bar: backlog
            """
        )
        statuses = da.parse_sprint_status(s)
        self.assertFalse(statuses["12.1"]["deferred"])
        self.assertFalse(statuses["13.1"]["deferred"])


class TestMissingRefs(unittest.TestCase):
    def test_validate_warns_on_unknown(self):
        e = epics(
            """
            ##### Story 1.1: A
            **Dependencies:** Story 9.99
            **Wave:** 0a
            """
        )
        s = status(
            """
            development_status:
              1-1-a: backlog
            """
        )
        warnings = da.validate(da.parse_epics(e), da.parse_sprint_status(s))
        self.assertTrue(any("9.99" in w for w in warnings))

    def test_unknown_dep_blocks_story(self):
        e = epics(
            """
            ##### Story 1.1: A
            **Dependencies:** Story 9.99
            **Wave:** 0a
            """
        )
        s = status(
            """
            development_status:
              1-1-a: backlog
            """
        )
        result = da.next_ready(da.parse_epics(e), da.parse_sprint_status(s))
        # 1.1 has unmet dep on unknown 9.99 → no candidate → all_done sentinel
        self.assertTrue(result.get("all_done"))


class TestAllDone(unittest.TestCase):
    def test_all_done(self):
        e = epics(
            """
            ##### Story 1.1: A
            **Wave:** 0a
            """
        )
        s = status(
            """
            development_status:
              1-1-a: done
            """
        )
        result = da.next_ready(da.parse_epics(e), da.parse_sprint_status(s))
        self.assertTrue(result.get("all_done"))


class TestWaveFilter(unittest.TestCase):
    def test_wave_filter(self):
        e = epics(
            """
            ##### Story 1.1: A
            **Wave:** 0a
            ##### Story 1.2: B
            **Wave:** 0b
            ##### Story 2.1: C
            **Wave:** 1a
            """
        )
        s = status(
            """
            development_status:
              1-1-a: done
              1-2-b: backlog
              2-1-c: backlog
            """
        )
        stories = da.parse_epics(e)
        statuses = da.parse_sprint_status(s)
        self.assertEqual(da.next_ready(stories, statuses)["story_id"], "1.2")
        self.assertEqual(da.next_ready(stories, statuses, "1a")["story_id"], "2.1")


if __name__ == "__main__":
    unittest.main()
