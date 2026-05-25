"""Tests for intent-classifier.py — 888 Storm §6.1."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest_storm import STORM_DIR


class TestClassifyFunction:
    def test_empty_prompt_returns_empty(self):
        from intent_classifier import classify  # noqa: F401 (dash in filename)
        result = classify("")
        assert result["triggers"] == []
        assert result["context_inject"] == ""
        assert result["block"] is False

    def test_whitespace_prompt_returns_empty(self):
        from intent_classifier import classify
        result = classify("   \n\t  ")
        assert result["triggers"] == []

    def test_t1_feature_intent(self):
        from intent_classifier import classify
        result = classify("хочу внедрить multi-LLM routing")
        assert "T1" in result["triggers"]
        assert "T1_feature_intent.md" in result["scenarios"]
        assert result["block"] is False

    def test_t1_english(self):
        from intent_classifier import classify
        result = classify("let's add a new feature for payment processing")
        assert "T1" in result["triggers"]

    def test_t2_agent_create_russian(self):
        from intent_classifier import classify
        result = classify("создам нового агента для анализа данных")
        assert "T2" in result["triggers"]

    def test_t2_agent_create_english(self):
        from intent_classifier import classify
        result = classify("build agent for customer support")
        assert "T2" in result["triggers"]

    def test_t3_agent_edit(self):
        from intent_classifier import classify
        result = classify("изменю агента-анализатора")
        assert "T3" in result["triggers"]

    def test_t4_comparator(self):
        from intent_classifier import classify
        result = classify("сравни LangChain vs LlamaIndex")
        assert "T4" in result["triggers"]

    def test_t4_english_compare(self):
        from intent_classifier import classify
        result = classify("compare GPT-4 vs Claude for code tasks")
        assert "T4" in result["triggers"]

    def test_t5_improvement(self):
        from intent_classifier import classify
        result = classify("хочу улучшить performance оркестратора")
        assert "T5" in result["triggers"]

    def test_t5_refactor(self):
        from intent_classifier import classify
        result = classify("let me refactor the sandbox module")
        assert "T5" in result["triggers"]

    def test_t6_doubt(self):
        from intent_classifier import classify
        result = classify("не уверен в правильности подхода")
        assert "T6" in result["triggers"]

    def test_t7_bug_fix(self):
        from intent_classifier import classify
        result = classify("fix(virgil-sandbox): exception not caught")
        assert "T7" in result["triggers"]

    def test_t8_merge(self):
        from intent_classifier import classify
        result = classify("git merge feature/new-llm into main")
        assert "T8" in result["triggers"]

    def test_t9_stuck(self):
        from intent_classifier import classify
        result = classify("топчусь на месте, всё время одно и то же")
        assert "T9" in result["triggers"]

    def test_t10_destructive(self):
        from intent_classifier import classify
        result = classify("rm -rf /tmp/worktrees")
        assert "T10" in result["triggers"]

    def test_t11_regression_keyword(self):
        from intent_classifier import classify
        result = classify("опять та же regression в тестах")
        assert "T11" in result["triggers"]

    def test_multiple_triggers(self):
        from intent_classifier import classify
        # This should hit T2 (agent create) and possibly T1 (feature)
        result = classify("создам нового агента и добавим новую фичу")
        assert len(result["triggers"]) >= 2

    def test_context_inject_populated(self):
        from intent_classifier import classify
        result = classify("хочу внедрить новый агент")
        assert len(result["context_inject"]) > 0

    def test_no_false_positive_neutral(self):
        from intent_classifier import classify
        result = classify("покажи мне git log за последние 5 коммитов")
        assert result["triggers"] == []

    def test_result_structure(self):
        from intent_classifier import classify
        result = classify("build feature X")
        assert "triggers" in result
        assert "scenarios" in result
        assert "context_inject" in result
        assert "block" in result
        assert isinstance(result["triggers"], list)
        assert isinstance(result["scenarios"], list)


class TestCLI:
    def test_cli_t1_detection(self):
        """CLI should return JSON with T1 trigger."""
        script = STORM_DIR / "intent-classifier.py"
        result = subprocess.run(
            [sys.executable, str(script), "хочу внедрить multi-LLM"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "T1" in data["triggers"]

    def test_cli_no_args_exits_1(self):
        script = STORM_DIR / "intent-classifier.py"
        result = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 1

    def test_cli_neutral_prompt(self):
        script = STORM_DIR / "intent-classifier.py"
        result = subprocess.run(
            [sys.executable, str(script), "show me the git log"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["triggers"] == []
