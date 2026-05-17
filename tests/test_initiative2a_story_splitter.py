"""Initiative #2 Task 2.1 + 2.2 — should_split heuristic + decomposition schema.

Three groups:

* evaluate_split / should_split — threshold edge cases (each rule fires at its
  exact boundary, none fires just below).
* classify_layers / count_acceptance_criteria — building blocks.
* validate_decomposition — malformed payload handling per spec §21.7 (LLM
  hallucinations must not corrupt sprint-status).
"""

from __future__ import annotations

import pytest

from bmad_orchestrator.runtime.story_splitter import (
    DECOMPOSITION_PROMPT,
    MAX_SUBS,
    MIN_SUBS,
    SPLIT_AC_THRESHOLD,
    SPLIT_FILE_COUNT_THRESHOLD,
    SPLIT_MINUTES_THRESHOLD,
    SPLIT_TOKEN_THRESHOLD,
    DecompositionError,
    SplitDecision,
    classify_layers,
    count_acceptance_criteria,
    evaluate_split,
    should_split,
    validate_decomposition,
)


def _story(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": "1-1-test",
        "estimated_minutes": 25,
        "estimated_tokens": 500,
        "touches_files": [],
        "touches_shared": [],
    }
    base.update(overrides)
    return base


# ── classify_layers ──────────────────────────────────────────────────────────


def test_classify_layers_empty() -> None:
    assert classify_layers([]) == set()


def test_classify_layers_known_prefixes() -> None:
    layers = classify_layers(
        ["src/foo.py", "frontend/Foo.tsx", "tests/test_x.py", "workers/main.rs"]
    )
    assert layers == {"backend", "frontend", "tests", "workers"}


def test_classify_layers_collapses_js_and_frontend() -> None:
    layers = classify_layers(["js/ui_chat.js", "frontend/lg/App.tsx"])
    assert layers == {"frontend"}


def test_classify_layers_ignores_unknown_prefix() -> None:
    assert classify_layers(["random/path.md", "docs/spec.md"]) == set()


# ── count_acceptance_criteria ───────────────────────────────────────────────


def test_count_ac_missing_text() -> None:
    assert count_acceptance_criteria({}) == 0


def test_count_ac_none_text() -> None:
    assert count_acceptance_criteria({"_acceptance_text": None}) == 0


def test_count_ac_three_bullets_mixed_markers() -> None:
    text = """- one
* two
• three
not a bullet line
"""
    assert count_acceptance_criteria({"_acceptance_text": text}) == 3


def test_count_ac_non_string_text() -> None:
    # Defensive: arbitrary type should be ignored, not crash.
    assert count_acceptance_criteria({"_acceptance_text": ["bad"]}) == 0


# ── evaluate_split / should_split ────────────────────────────────────────────


def test_keep_small_story() -> None:
    result = evaluate_split(_story())
    assert isinstance(result, SplitDecision)
    assert result.decision == "keep"
    assert result.rules_hit == ()
    assert should_split(_story()) is False


def test_ac_below_threshold_keeps() -> None:
    text = "\n".join(f"- AC{i}" for i in range(SPLIT_AC_THRESHOLD - 1))
    assert evaluate_split(_story(_acceptance_text=text)).decision == "keep"


def test_ac_at_threshold_splits() -> None:
    text = "\n".join(f"- AC{i}" for i in range(SPLIT_AC_THRESHOLD))
    result = evaluate_split(_story(_acceptance_text=text))
    assert result.decision == "split"
    assert any("ac>=" in r for r in result.rules_hit)


def test_minutes_below_threshold_keeps() -> None:
    assert (
        evaluate_split(_story(estimated_minutes=SPLIT_MINUTES_THRESHOLD - 1)).decision
        == "keep"
    )


def test_minutes_at_threshold_splits() -> None:
    result = evaluate_split(_story(estimated_minutes=SPLIT_MINUTES_THRESHOLD))
    assert result.decision == "split"
    assert any("minutes>=" in r for r in result.rules_hit)


def test_tokens_below_threshold_keeps() -> None:
    assert (
        evaluate_split(_story(estimated_tokens=SPLIT_TOKEN_THRESHOLD - 1)).decision
        == "keep"
    )


def test_tokens_at_threshold_splits() -> None:
    result = evaluate_split(_story(estimated_tokens=SPLIT_TOKEN_THRESHOLD))
    assert result.decision == "split"
    assert any("tokens>=" in r for r in result.rules_hit)


def test_files_below_threshold_keeps() -> None:
    files = [f"src/mod_{i}.py" for i in range(SPLIT_FILE_COUNT_THRESHOLD - 1)]
    assert evaluate_split(_story(touches_files=files)).decision == "keep"


def test_files_at_threshold_splits_via_file_count_or_layers() -> None:
    files = [f"src/mod_{i}.py" for i in range(SPLIT_FILE_COUNT_THRESHOLD)]
    result = evaluate_split(_story(touches_files=files))
    assert result.decision == "split"
    assert any("files>=" in r for r in result.rules_hit)


def test_layers_below_threshold_keeps() -> None:
    files = ["src/x.py", "tests/test_x.py"]  # 2 layers
    assert evaluate_split(_story(touches_files=files)).decision == "keep"


def test_layers_at_threshold_splits() -> None:
    files = ["src/x.py", "tests/test_x.py", "frontend/X.tsx"]  # 3 layers
    result = evaluate_split(_story(touches_files=files))
    assert result.decision == "split"
    assert any("layers>=" in r for r in result.rules_hit)


def test_touches_shared_counts_toward_layers_and_files() -> None:
    story = _story(
        touches_files=["src/x.py"],
        touches_shared=["frontend/X.tsx", "tests/test_x.py"],
    )
    result = evaluate_split(story)
    assert result.touches_files_count == 3
    assert set(result.layers) == {"backend", "frontend", "tests"}
    assert result.decision == "split"


def test_combo_multiple_rules_hit_listed_in_rationale() -> None:
    text = "\n".join(f"- AC{i}" for i in range(8))
    story = _story(
        _acceptance_text=text,
        estimated_minutes=300,
        estimated_tokens=10_000,
    )
    result = evaluate_split(story)
    assert result.decision == "split"
    assert len(result.rules_hit) >= 3
    assert any("ac>=" in r for r in result.rules_hit)
    assert any("minutes>=" in r for r in result.rules_hit)
    assert any("tokens>=" in r for r in result.rules_hit)


def test_as_dict_round_trip() -> None:
    result = evaluate_split(_story(estimated_minutes=300))
    d = result.as_dict()
    assert d["decision"] == "split"
    assert d["minutes"] == 300
    assert isinstance(d["rationale"], list)
    assert isinstance(d["layers"], list)


def test_negative_or_string_metric_does_not_crash() -> None:
    # Defensive: bmad_format parser sometimes emits string ints.
    result = evaluate_split(_story(estimated_minutes="not-a-number"))  # type: ignore[arg-type]
    # Falls back to 0, no rule hit.
    assert result.decision == "keep"


# ── validate_decomposition ─────────────────────────────────────────────────


def _valid_sub(idx: str = "a", **extra: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": f"1-2-{idx}",
        "title": f"sub {idx}",
        "scope": "one slice",
        "ac": ["AC1"],
        "estimated_minutes": 20,
        "deps_on": [],
        "touches_files": ["src/x.py"],
    }
    base.update(extra)
    return base


def test_validate_decomposition_minimum_set_ok() -> None:
    payload = [_valid_sub("a"), _valid_sub("b")]
    out = validate_decomposition(payload, parent_id="1-2-parent")
    assert len(out) == MIN_SUBS


def test_validate_decomposition_maximum_set_ok() -> None:
    payload = [_valid_sub(chr(ord("a") + i)) for i in range(MAX_SUBS)]
    out = validate_decomposition(payload)
    assert len(out) == MAX_SUBS


def test_validate_decomposition_rejects_non_list() -> None:
    with pytest.raises(DecompositionError, match="expected list"):
        validate_decomposition({"sub_stories": []})  # type: ignore[arg-type]


def test_validate_decomposition_too_few() -> None:
    with pytest.raises(DecompositionError, match="outside"):
        validate_decomposition([_valid_sub("a")])


def test_validate_decomposition_too_many() -> None:
    payload = [_valid_sub(chr(ord("a") + i)) for i in range(MAX_SUBS + 1)]
    with pytest.raises(DecompositionError, match="outside"):
        validate_decomposition(payload)


def test_validate_decomposition_missing_required_key() -> None:
    bad = _valid_sub("a")
    del bad["deps_on"]
    with pytest.raises(DecompositionError, match="missing required key"):
        validate_decomposition([bad, _valid_sub("b")])


def test_validate_decomposition_non_string_id() -> None:
    bad = _valid_sub("a")
    bad["id"] = 42
    with pytest.raises(DecompositionError, match="id must be non-empty"):
        validate_decomposition([bad, _valid_sub("b")])


def test_validate_decomposition_empty_title() -> None:
    bad = _valid_sub("a")
    bad["title"] = ""
    with pytest.raises(DecompositionError, match="title must be non-empty"):
        validate_decomposition([bad, _valid_sub("b")])


def test_validate_decomposition_duplicate_id() -> None:
    with pytest.raises(DecompositionError, match="duplicate"):
        validate_decomposition([_valid_sub("a"), _valid_sub("a")])


def test_validate_decomposition_id_collides_with_parent() -> None:
    sub = _valid_sub("a")
    sub["id"] = "1-2-parent"
    with pytest.raises(DecompositionError, match="collides with parent"):
        validate_decomposition([sub, _valid_sub("b")], parent_id="1-2-parent")


def test_validate_decomposition_deps_wrong_type() -> None:
    bad = _valid_sub("a")
    bad["deps_on"] = "not-a-list"
    with pytest.raises(DecompositionError, match="deps_on must be list"):
        validate_decomposition([bad, _valid_sub("b")])


def test_validate_decomposition_deps_unknown_peer() -> None:
    a = _valid_sub("a", deps_on=["nope"])
    with pytest.raises(DecompositionError, match="unknown peer"):
        validate_decomposition([a, _valid_sub("b")])


def test_validate_decomposition_cycle() -> None:
    a = _valid_sub("a", deps_on=["1-2-b"])
    b = _valid_sub("b", deps_on=["1-2-a"])
    with pytest.raises(DecompositionError, match="cycle"):
        validate_decomposition([a, b])


def test_validate_decomposition_valid_dag() -> None:
    a = _valid_sub("a")
    b = _valid_sub("b", deps_on=["1-2-a"])
    c = _valid_sub("c", deps_on=["1-2-a"])
    out = validate_decomposition([a, b, c])
    assert [s["id"] for s in out] == ["1-2-a", "1-2-b", "1-2-c"]


def test_decomposition_prompt_contains_constraints() -> None:
    assert "2-5" in DECOMPOSITION_PROMPT
    assert "deps_on" in DECOMPOSITION_PROMPT
    assert "JSON" in DECOMPOSITION_PROMPT
