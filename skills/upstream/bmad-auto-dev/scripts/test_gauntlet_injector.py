#!/usr/bin/env python3
"""Unit tests for gauntlet_injector.py — stdlib-only (unittest)."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gauntlet_injector as gi  # noqa: E402


REAL_TEMPLATES_PATH = (
    Path(__file__).resolve().parent.parent / "templates" / "gauntlet-prompts.md"
)
REAL_CUSTOMIZE_PATH = Path(__file__).resolve().parent.parent / "customize.toml"


# --------------- Mode detection ---------------


class TestExtractEpicId(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(gi.extract_epic_id("1.3"), "epic-1")

    def test_two_digit_epic(self):
        self.assertEqual(gi.extract_epic_id("13.2"), "epic-13")

    def test_letter_suffix(self):
        self.assertEqual(gi.extract_epic_id("1.8a"), "epic-1")

    def test_no_dot(self):
        self.assertEqual(gi.extract_epic_id("foo"), "epic-foo")


class TestSelectGauntletMode(unittest.TestCase):
    def setUp(self):
        self.deep_epics = ["epic-4", "epic-5", "epic-7", "epic-10"]
        self.deep_tags = ["compliance", "auth", "pii", "rls"]

    def test_epic_4_iam_deep(self):
        self.assertEqual(
            gi.select_gauntlet_mode("epic-4", "routine CRUD", self.deep_epics, self.deep_tags),
            "deep",
        )

    def test_epic_5_billing_deep(self):
        self.assertEqual(
            gi.select_gauntlet_mode("epic-5", "any text", self.deep_epics, self.deep_tags),
            "deep",
        )

    def test_epic_7_compliance_deep(self):
        self.assertEqual(
            gi.select_gauntlet_mode("epic-7", "any text", self.deep_epics, self.deep_tags),
            "deep",
        )

    def test_epic_10_ai_gateway_deep(self):
        self.assertEqual(
            gi.select_gauntlet_mode("epic-10", "any text", self.deep_epics, self.deep_tags),
            "deep",
        )

    def test_epic_17_design_quick(self):
        """Epic 17 (Design System) — routine UX, should be quick."""
        self.assertEqual(
            gi.select_gauntlet_mode(
                "epic-17",
                "Adjust button hover color per UX spec.",
                self.deep_epics,
                self.deep_tags,
            ),
            "quick",
        )

    def test_epic_1_quick_default(self):
        self.assertEqual(
            gi.select_gauntlet_mode(
                "epic-1", "scaffold workspace", self.deep_epics, self.deep_tags
            ),
            "quick",
        )

    def test_tag_overrides_epic(self):
        """Epic 1 + auth tag in text → deep."""
        self.assertEqual(
            gi.select_gauntlet_mode(
                "epic-1",
                "Add auth middleware for the new route.",
                self.deep_epics,
                self.deep_tags,
            ),
            "deep",
        )

    def test_tag_case_insensitive(self):
        self.assertEqual(
            gi.select_gauntlet_mode(
                "epic-2", "involves PII redaction", self.deep_epics, self.deep_tags
            ),
            "deep",
        )

    def test_tag_inside_word_still_matches(self):
        """Substring match by design — 'authentication' contains 'auth'."""
        self.assertEqual(
            gi.select_gauntlet_mode(
                "epic-1", "authentication flow", self.deep_epics, self.deep_tags
            ),
            "deep",
        )

    def test_epic_case_insensitive(self):
        self.assertEqual(
            gi.select_gauntlet_mode(
                "EPIC-4", "anything", self.deep_epics, self.deep_tags
            ),
            "deep",
        )


# --------------- Template parsing ---------------


class TestParseTemplates(unittest.TestCase):
    def test_real_file_has_five_templates(self):
        templates = gi.parse_templates(REAL_TEMPLATES_PATH)
        self.assertEqual(len(templates), 5)

    def test_template_numbers_sequential(self):
        templates = gi.parse_templates(REAL_TEMPLATES_PATH)
        nums = [t["num"] for t in templates]
        self.assertEqual(nums, [1, 2, 3, 4, 5])

    def test_template_names(self):
        templates = gi.parse_templates(REAL_TEMPLATES_PATH)
        names = [t["name"] for t in templates]
        self.assertEqual(
            names,
            [
                "Failure Mode Analysis",
                "Edge Case Hunter",
                "Pre-mortem",
                "Devil's Advocate",
                "Security Red Team",
            ],
        )

    def test_template_bodies_contain_placeholders(self):
        templates = gi.parse_templates(REAL_TEMPLATES_PATH)
        for t in templates:
            self.assertIn("{story_id}", t["body"])
            self.assertIn("{story_text}", t["body"])
            self.assertIn("{word_budget}", t["body"])


# --------------- Config loader ---------------


class TestLoadConfig(unittest.TestCase):
    def test_real_customize_loads(self):
        config = gi.load_config(REAL_CUSTOMIZE_PATH)
        self.assertIn("epic-4", config["deep_epics"])
        self.assertIn("epic-10", config["deep_epics"])
        self.assertIn("auth", config["deep_tags"])

    def test_missing_file_uses_defaults(self):
        config = gi.load_config(Path("/nonexistent/customize.toml"))
        self.assertEqual(config["deep_epics"], gi.DEFAULT_DEEP_EPICS)
        self.assertEqual(config["deep_tags"], gi.DEFAULT_DEEP_TAGS)


# --------------- Prompt building ---------------


class TestBuildPrompts(unittest.TestCase):
    def setUp(self):
        self.templates = gi.parse_templates(REAL_TEMPLATES_PATH)
        self.story_text = "##### Story 1.3: Health endpoint\n- AC1: returns 200"

    def test_deep_mode_returns_five_prompts(self):
        prompts = gi.build_prompts("1.3", self.story_text, "deep", self.templates)
        self.assertEqual(len(prompts), 5)
        names = [n for n, _ in prompts]
        self.assertEqual(
            names,
            [
                "Failure Mode Analysis",
                "Edge Case Hunter",
                "Pre-mortem",
                "Devil's Advocate",
                "Security Red Team",
            ],
        )

    def test_quick_mode_returns_single_combined_prompt(self):
        prompts = gi.build_prompts("1.3", self.story_text, "quick", self.templates)
        self.assertEqual(len(prompts), 1)
        self.assertEqual(prompts[0][0], "combined")

    def test_quick_prompt_contains_all_five_lens_names(self):
        prompts = gi.build_prompts("1.3", self.story_text, "quick", self.templates)
        body = prompts[0][1]
        for name in [
            "Failure Mode Analysis",
            "Edge Case Hunter",
            "Pre-mortem",
            "Devil's Advocate",
            "Security Red Team",
        ]:
            self.assertIn(name, body)

    def test_deep_uses_300_word_budget(self):
        prompts = gi.build_prompts("1.3", self.story_text, "deep", self.templates)
        for _name, body in prompts:
            self.assertIn("~300 words", body)

    def test_quick_uses_200_word_budget(self):
        prompts = gi.build_prompts("1.3", self.story_text, "quick", self.templates)
        self.assertIn("~200 words", prompts[0][1])

    def test_story_id_substituted(self):
        prompts = gi.build_prompts("13.2a", self.story_text, "deep", self.templates)
        self.assertIn("13.2a", prompts[0][1])
        self.assertNotIn("{story_id}", prompts[0][1])

    def test_story_text_substituted_deep(self):
        prompts = gi.build_prompts("1.3", self.story_text, "deep", self.templates)
        self.assertIn("Health endpoint", prompts[0][1])
        self.assertNotIn("{story_text}", prompts[0][1])

    def test_invalid_mode_raises(self):
        with self.assertRaises(ValueError):
            gi.build_prompts("1.3", self.story_text, "fancy", self.templates)

    def test_wrong_template_count_raises(self):
        with self.assertRaises(ValueError):
            gi.build_prompts("1.3", self.story_text, "deep", self.templates[:3])


# --------------- Merge output ---------------


class TestMergeEnriched(unittest.TestCase):
    def test_concat_with_separators(self):
        story = "##### Story 1.3: X\n- AC1: ok"
        sections = [
            ("Failure", "## Failure section\nbody"),
            ("Edge", "## Edge section\nbody"),
        ]
        merged = gi.merge_enriched(story, sections)
        self.assertIn("Story 1.3", merged)
        self.assertIn("## Failure section", merged)
        self.assertIn("## Edge section", merged)
        self.assertEqual(merged.count("---"), 2)

    def test_trailing_newline(self):
        merged = gi.merge_enriched("body", [("a", "body")])
        self.assertTrue(merged.endswith("\n"))


# --------------- Story extraction ---------------


SAMPLE_EPICS = """\
# Epics

## Epic 1

#### Story 1.1: Setup
**Wave:** 0a
**Dependencies:** None
Body of 1.1

#### Story 1.2: Next
**Wave:** 0a
Body of 1.2

## Epic 2

##### Story 2.1: Another
**Wave:** 1a
Body of 2.1
"""


class TestExtractStoryText(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkstemp(suffix=".md")[1])
        self.tmp.write_text(SAMPLE_EPICS, encoding="utf-8")

    def tearDown(self):
        self.tmp.unlink(missing_ok=True)

    def test_extract_first_story(self):
        text = gi.extract_story_text(self.tmp, "1.1")
        self.assertIn("Story 1.1", text)
        self.assertIn("Body of 1.1", text)
        self.assertNotIn("Story 1.2", text)

    def test_extract_middle_story(self):
        text = gi.extract_story_text(self.tmp, "1.2")
        self.assertIn("Body of 1.2", text)
        self.assertNotIn("Body of 1.1", text)

    def test_extract_last_story(self):
        text = gi.extract_story_text(self.tmp, "2.1")
        self.assertIn("Body of 2.1", text)

    def test_missing_raises(self):
        with self.assertRaises(KeyError):
            gi.extract_story_text(self.tmp, "9.9")


# --------------- CLI smoke (subprocess, --print-mode) ---------------


class TestCliSmokeMode(unittest.TestCase):
    """Black-box: run the script as a subprocess with --print-mode (no claude call)."""

    SCRIPT = Path(__file__).resolve().parent / "gauntlet_injector.py"

    def _run(self, args: list[str]) -> dict:
        proc = subprocess.run(
            [sys.executable, str(self.SCRIPT), *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        return json.loads(proc.stdout.strip())

    def test_print_mode_real_story_routes_correctly(self):
        """Story 1.1 mentions `crates/auth` and `crates/rls` in its scaffold body
        → substring tag-match deliberately routes to `deep`. End-to-end smoke
        of epic_id extraction + tag scan + JSON emit."""
        out = self._run(
            [
                "--story",
                "1.1",
                "--epics",
                "_bmad/planning-artifacts/epics.md",
                "--print-mode",
            ]
        )
        self.assertEqual(out["story_id"], "1.1")
        self.assertEqual(out["epic_id"], "epic-1")
        self.assertEqual(out["mode"], "deep")

    def test_manual_override_deep(self):
        out = self._run(
            [
                "--story",
                "1.1",
                "--mode",
                "deep",
                "--epics",
                "_bmad/planning-artifacts/epics.md",
                "--print-mode",
            ]
        )
        self.assertEqual(out["mode"], "deep")

    def test_manual_override_quick(self):
        out = self._run(
            [
                "--story",
                "1.1",
                "--mode",
                "quick",
                "--epics",
                "_bmad/planning-artifacts/epics.md",
                "--print-mode",
            ]
        )
        self.assertEqual(out["mode"], "quick")


if __name__ == "__main__":
    unittest.main(verbosity=2)
