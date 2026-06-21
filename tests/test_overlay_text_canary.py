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
osync = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
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
        "category", "technique_name", "description",
        "facilitation_prompts", "best_for", "energy_level", "typical_duration",
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
