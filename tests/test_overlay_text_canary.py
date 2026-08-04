"""Hermetic tests for the brain-methods text canary (Этап 3, СРЕЗ 1).

Layered bottom-up, mirroring the build order:
  1. parser golden-test  — csv_parse / step_parse on the pinned 6.8.0 golden pair
                           (a lying parser yields a false canary, so it is proven first)
  2. invariants          — INV-BRAIN-COLS / INV-COUNT-RECON / INV-PHANTOM-CALL
  3. golden-gate + meta-proof — sick→RED, healthy→GREEN; 4th column → FAIL → PASS;
                           CSV with/without trailing newline → both 61/10.

The golden fixtures (tools/golden/brain/6.8.0) are the REAL pair: pinned upstream
6.8.0 step files (sick: "36/7", phantom columns) vs the odyssey fork (healthy:
61/10, 3 columns). Same brain-methods.csv for both (it never drifted; the step
files describing it did).
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "overlay_sync", Path(__file__).resolve().parents[1] / "tools" / "overlay_sync.py"
)
assert _SPEC and _SPEC.loader  # сужает тип до ModuleSpec ДО использования (mypy arg-type)
osync = importlib.util.module_from_spec(_SPEC)
sys.modules["overlay_sync"] = osync
_SPEC.loader.exec_module(osync)

GOLDEN = Path(__file__).resolve().parents[1] / "tools" / "golden" / "brain" / "6.8.0"


def _write(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


# === 1. PARSER GOLDEN-TEST (before invariants) =============================


def test_csv_parse_golden_facts() -> None:
    facts = osync.csv_parse(GOLDEN / "brain-methods.csv")
    assert facts.columns == ("category", "technique_name", "description")
    assert facts.techniques == 61
    assert facts.categories == 10
    assert len(facts.technique_names) == 61
    # exact surface forms matter for INV-PHANTOM-CALL (no fuzzy matching)
    assert "SCAMPER Method" in facts.technique_names
    assert "SCAMPER" not in facts.technique_names
    assert "Nature's Solutions" in facts.technique_names  # apostrophe survives csv.reader


def test_csv_parse_robust_to_trailing_newline(tmp_path: Path) -> None:
    raw = (GOLDEN / "brain-methods.csv").read_text(encoding="utf-8")
    no_nl = _write(tmp_path / "no.csv", raw.rstrip("\n"))
    with_nl = _write(tmp_path / "yes.csv", raw.rstrip("\n") + "\n")
    a, b = osync.csv_parse(no_nl), osync.csv_parse(with_nl)
    assert (a.techniques, a.categories) == (61, 10)
    assert (b.techniques, b.categories) == (61, 10)


def test_csv_parse_quoted_comma_not_split(tmp_path: Path) -> None:
    csv_text = (
        "category,technique_name,description\n"
        'Creative,Devil\'s Advocate,"Challenge, then refine the idea"\n'
    )
    facts = osync.csv_parse(_write(tmp_path / "q.csv", csv_text))
    assert facts.techniques == 1
    assert facts.columns == ("category", "technique_name", "description")
    assert "Devil's Advocate" in facts.technique_names


def test_step_parse_healthy_cuts_emdash_prose() -> None:
    s = osync.step_parse(GOLDEN / "healthy" / "step-02a-user-selected.md")
    # the fork Parse line is "...description — these are the ONLY 3 columns…":
    # the prose after the em-dash must NOT become a phantom column.
    assert s.parse_columns == ("category", "technique_name", "description")
    assert (61, 10) in s.declared_counts
    assert s.called_techniques  # the "Includes:" lists were captured


def test_step_parse_sick_keeps_phantom_columns() -> None:
    s = osync.step_parse(GOLDEN / "sick" / "step-02a-user-selected.md")
    assert s.parse_columns == (
        "category",
        "technique_name",
        "description",
        "facilitation_prompts",
        "best_for",
        "energy_level",
        "typical_duration",
    )
    assert (36, 7) in s.declared_counts


# === 2. INVARIANTS =========================================================


def test_invariants_fire_on_sick_silent_on_healthy() -> None:
    csv_path = GOLDEN / "brain-methods.csv"
    sick = sorted((GOLDEN / "sick").glob("step-02*.md"))
    healthy = sorted((GOLDEN / "healthy").glob("step-02*.md"))
    sick_findings = osync.run_text_invariants(csv_path, sick, "golden-sick")
    healthy_findings = osync.run_text_invariants(csv_path, healthy, "golden-healthy")
    # sick must trip all three invariants; healthy must be silent.
    invs = {f.inv for f in sick_findings}
    assert invs == {"INV-BRAIN-COLS", "INV-COUNT-RECON", "INV-PHANTOM-CALL"}
    assert healthy_findings == []


def test_phantom_call_uses_curated_alias_not_fuzzy() -> None:
    facts = osync.csv_parse(GOLDEN / "brain-methods.csv")
    # "SCAMPER" is a curated alias of the real "SCAMPER Method" -> accepted.
    assert osync._call_is_known("SCAMPER", facts)
    # "Pirate Code" is a real upstream phantom; the CSV has "Pirate Code Brainstorm".
    # Fuzzy/prefix matching would wrongly accept it -> we must NOT.
    assert not osync._call_is_known("Pirate Code", facts)
    assert osync._call_is_known("Pirate Code Brainstorm", facts)


# === 3. GOLDEN GATE + META-PROOF ===========================================


def test_golden_gate_passes_on_pinned_pair() -> None:
    ok, reason = osync.golden_gate("6.8.0")
    assert ok, reason


def test_golden_gate_refuses_unknown_version() -> None:
    ok, reason = osync.golden_gate("9.9.9")
    assert not ok
    assert "no golden fixture" in reason


def _clone_golden(tmp_path: Path) -> Path:
    root = tmp_path / "golden"
    shutil.copytree(GOLDEN, root / "6.8.0")
    return root


def test_meta_proof_fourth_column_makes_healthy_fail_then_pass(tmp_path: Path) -> None:
    """Inject a 4th (phantom) column into the HEALTHY Parse line -> INV-BRAIN-COLS
    must FAIL; revert -> PASS. Proves the invariant actually discriminates."""
    root = _clone_golden(tmp_path)
    step = root / "6.8.0" / "healthy" / "step-02a-user-selected.md"
    original = step.read_text(encoding="utf-8")

    mutated = original.replace(
        "- Parse: category, technique_name, description —",
        "- Parse: category, technique_name, description, phantom_col —",
    )
    assert mutated != original  # the anchor was found
    step.write_text(mutated, encoding="utf-8")
    ok, reason = osync.golden_gate("6.8.0", golden_root=root)
    assert not ok and "false-red" in reason  # healthy golden now fires

    step.write_text(original, encoding="utf-8")
    ok, reason = osync.golden_gate("6.8.0", golden_root=root)
    assert ok, reason  # reverted -> trusted again


def test_meta_proof_silent_canary_is_caught(tmp_path: Path) -> None:
    """If the canary goes silent on the SICK sample (false-green), the gate must
    refuse — replace the sick steps with healthy copies and expect a refusal."""
    root = _clone_golden(tmp_path)
    sick_dir = root / "6.8.0" / "sick"
    for f in sick_dir.glob("step-02*.md"):
        f.unlink()
    for f in (root / "6.8.0" / "healthy").glob("step-02*.md"):
        shutil.copy(f, sick_dir / f.name)
    ok, reason = osync.golden_gate("6.8.0", golden_root=root)
    assert not ok and "false-green" in reason


def test_golden_gate_version_pin_mismatch(tmp_path: Path) -> None:
    root = _clone_golden(tmp_path)
    (root / "6.8.0" / "VERSION").write_text("6.7.0\n", encoding="utf-8")
    ok, reason = osync.golden_gate("6.8.0", golden_root=root)
    assert not ok and "VERSION pin mismatch" in reason


# === 4. FEATURE FLAG (off by default) ======================================


def test_text_canary_flag_off_by_default() -> None:
    args = osync.build_parser().parse_args(["check"])
    assert osync._text_canary_enabled(args) is False


def test_text_canary_flag_via_cli() -> None:
    args = osync.build_parser().parse_args(["--text-canary", "check"])
    assert osync._text_canary_enabled(args) is True


def test_text_canary_flag_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BMAD_OVERLAY_TEXT_CANARY", "on")
    args = osync.build_parser().parse_args(["check"])
    assert osync._text_canary_enabled(args) is True


# === 5. HARDENING (from adversarial review) ================================


def test_count_recon_tolerates_phrasing_drift(tmp_path: Path) -> None:
    """INV-COUNT-RECON must catch a count lie regardless of connector/adjective/noun
    phrasing — coupling to the literal 'across N categor' template was a false-green."""
    facts = osync.csv_parse(GOLDEN / "brain-methods.csv")  # 61/10
    for phrasing in (
        "- CSV contains 36 techniques in 7 categories",
        "- 36 methods spanning 7 categories",
        "- 36 techniques across 7 distinct categories",
    ):
        step = osync.step_parse(_write(tmp_path / "s.md", phrasing + "\n"))
        assert step.declared_counts == ((36, 7),), phrasing
        assert osync.inv_count_recon(step, facts, "t"), f"missed lie: {phrasing}"
    truthful = osync.step_parse(_write(tmp_path / "ok.md", "- 61 techniques in 10 categories\n"))
    assert osync.inv_count_recon(truthful, facts, "t") == []


def test_count_recon_ignores_per_category_lines(tmp_path: Path) -> None:
    # "[2] Deep (8 techniques)" has a technique count but no category count -> not a
    # declaration; it must not manufacture a phantom (8, ?) finding.
    step = osync.step_parse(_write(tmp_path / "s.md", "**[2] Deep** (8 techniques)\n"))
    assert step.declared_counts == ()


def test_brain_cols_catches_non_snake_phantom(tmp_path: Path) -> None:
    facts = osync.csv_parse(GOLDEN / "brain-methods.csv")
    for phantom in ("energyLevel", "energy-level", "prompts2"):
        line = f"- Parse: category, technique_name, description, {phantom}\n"
        step = osync.step_parse(_write(tmp_path / "s.md", line))
        assert phantom in step.parse_columns, phantom
        assert osync.inv_brain_cols(step, facts, "t"), f"missed phantom: {phantom}"


def test_extract_columns_union_across_all_parse_lines(tmp_path: Path) -> None:
    text = (
        "- Parse: category, technique_name, description\n"
        "intervening text\n"
        "- Parse: category, technique_name, description, sneaky_col\n"
    )
    step = osync.step_parse(_write(tmp_path / "s.md", text))
    assert "sneaky_col" in step.parse_columns  # a later lying Parse line is not hidden


def test_phantom_call_curly_apostrophe_not_false_red() -> None:
    facts = osync.csv_parse(GOLDEN / "brain-methods.csv")
    assert "Nature's Solutions" in facts.technique_names
    assert osync._call_is_known("Nature’s Solutions", facts)  # curly apostrophe folded


def test_includes_anchored_to_bullet_not_prose(tmp_path: Path) -> None:
    facts = osync.csv_parse(GOLDEN / "brain-methods.csv")
    prose = "This phase Includes: thinking, talking, and reviewing with the user.\n"
    step = osync.step_parse(_write(tmp_path / "s.md", prose))
    assert step.called_techniques == ()
    assert osync.inv_phantom_call(step, facts, "t") == []


def test_golden_gate_requires_every_invariant_on_sick(monkeypatch: pytest.MonkeyPatch) -> None:
    """Per-invariant vacuity guard: if one invariant silently breaks, the gate must
    refuse — not pass on the strength of the other two (findings #9/#10)."""
    monkeypatch.setattr(osync, "inv_phantom_call", lambda step, facts, project: [])
    ok, reason = osync.golden_gate("6.8.0")
    assert not ok
    assert "INV-PHANTOM-CALL" in reason and "missing" in reason


def test_meta_proof_count_recon_mutation(tmp_path: Path) -> None:
    """Gate-level meta-proof for INV-COUNT-RECON (mirrors the 4th-column proof)."""
    root = _clone_golden(tmp_path)
    step = root / "6.8.0" / "healthy" / "step-02a-user-selected.md"
    original = step.read_text(encoding="utf-8")
    step.write_text(original + "\n- Now 61 techniques across 9 categories\n", encoding="utf-8")
    ok, reason = osync.golden_gate("6.8.0", golden_root=root)
    assert not ok and "false-red" in reason
    step.write_text(original, encoding="utf-8")
    assert osync.golden_gate("6.8.0", golden_root=root)[0]


def test_meta_proof_phantom_call_mutation(tmp_path: Path) -> None:
    """Gate-level meta-proof for INV-PHANTOM-CALL."""
    root = _clone_golden(tmp_path)
    step = root / "6.8.0" / "healthy" / "step-02a-user-selected.md"
    original = step.read_text(encoding="utf-8")
    step.write_text(
        original + "\n- Includes: Ghost Technique That Does Not Exist\n", encoding="utf-8"
    )
    ok, reason = osync.golden_gate("6.8.0", golden_root=root)
    assert not ok and "false-red" in reason
    step.write_text(original, encoding="utf-8")
    assert osync.golden_gate("6.8.0", golden_root=root)[0]


# === 6. FOLLOW-UPS #4 (numbered call source) + #6 (CSV schema guard) ========


def test_numbered_example_phantom_now_caught(tmp_path: Path) -> None:
    """#4: a phantom referenced ONLY as a numbered example (not in Includes:) is
    now caught — closing the call-source blind spot."""
    facts = osync.csv_parse(GOLDEN / "brain-methods.csv")
    step = osync.step_parse(_write(tmp_path / "s.md", '"**1. Ghost Method**"\n'))
    assert "Ghost Method" in step.called_techniques
    assert osync.inv_phantom_call(step, facts, "t")
    ok = osync.step_parse(_write(tmp_path / "ok.md", "**2. Six Thinking Hats**\n"))
    assert osync.inv_phantom_call(ok, facts, "t") == []


def test_numbered_checklist_headings_not_false_red(tmp_path: Path) -> None:
    """#4 guard: 02b reuses 'N. **bold**' for checklist headings / placeholders;
    those must NOT be harvested as techniques (would false-red on healthy steps)."""
    facts = osync.csv_parse(GOLDEN / "brain-methods.csv")
    text = (
        "1. **Goal Analysis:** assess the goal\n"
        "2. **[Technique 1]:** placeholder\n"
        "3. **Energy/Tone Assessment:** mood\n"
    )
    step = osync.step_parse(_write(tmp_path / "s.md", text))
    assert step.called_techniques == ()
    assert osync.inv_phantom_call(step, facts, "t") == []


def test_csv_schema_drift_caught(tmp_path: Path) -> None:
    """#6: a renamed CSV header breaks the ground truth — INV-CSV-SCHEMA fires once
    and per-step reconciliation is skipped (no false-phantom spray)."""
    bad = _write(tmp_path / "b.csv", "cat,name,desc\nCreative,SCAMPER Method,x\n")
    step = _write(tmp_path / "s.md", "- Includes: Ghost Technique\n")
    findings = osync.run_text_invariants(bad, [step], "t")
    assert len(findings) == 1
    assert findings[0].inv == "INV-CSV-SCHEMA"


def test_golden_gate_refuses_on_renamed_csv(tmp_path: Path) -> None:
    """#6 at gate level: a drifted CSV schema makes golden_gate refuse to trust."""
    root = _clone_golden(tmp_path)
    csv_path = root / "6.8.0" / "brain-methods.csv"
    lines = csv_path.read_text(encoding="utf-8").splitlines(keepends=True)
    lines[0] = "cat,name,desc\n"
    csv_path.write_text("".join(lines), encoding="utf-8")
    ok, _ = osync.golden_gate("6.8.0", golden_root=root)
    assert not ok
