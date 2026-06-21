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
import sys
from pathlib import Path

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
