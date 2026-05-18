"""Initiative #3A — project registry + scan/doctor/init/resume CLI tests.

Three groups:

* :mod:`runtime.project_registry` — yaml round-trip, layout detection, scan,
  doctor, resume hint, validation errors.
* CLI commands ``init``, ``scan``, ``doctor``, ``resume`` driven via
  ``typer.testing.CliRunner``.
* Env override ``BMAD_PROJECTS_REGISTRY`` honored over ``orchestrator_home``.

Fixtures use ``tmp_path`` for an isolated orchestrator_home + per-test
registry path; ``monkeypatch`` swaps ``ORCHESTRATOR_HOME`` env vars so the
CLI command resolves to the temp registry instead of the real one.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from bmad_orchestrator.cli.main import app
from bmad_orchestrator.runtime.project_registry import (
    REGISTRY_ENV_VAR,
    DoctorReport,
    ProjectEntry,
    ProjectRegistryError,
    ProjectsRegistry,
    detect_bmad_layout,
    doctor,
    load_registry,
    register_project,
    registry_path,
    resume_hint,
    save_registry,
    scan_registry,
    slug_from_path,
)

# ── layout detector fixtures ────────────────────────────────────────────────


def _make_bmm_v6(root: Path) -> Path:
    p = root / "antares-like"
    (p / "_bmad" / "bmm").mkdir(parents=True)
    (p / "_bmad" / "bmm" / "config.yaml").write_text("project_name: antares\n", encoding="utf-8")
    return p


def _make_odyssey_hybrid(root: Path) -> Path:
    p = root / "odyssey-like"
    (p / "_bmad" / "planning-artifacts").mkdir(parents=True)
    (p / "_bmad" / "config.toml").write_text(
        '[modules.bmm]\nproject_name = "odyssey"\n', encoding="utf-8"
    )
    return p


def _make_empty_bmad(root: Path) -> Path:
    p = root / "weird"
    (p / "_bmad").mkdir(parents=True)
    return p


def _make_not_bmad(root: Path) -> Path:
    p = root / "plain"
    p.mkdir()
    return p


# ── layout detector ─────────────────────────────────────────────────────────


def test_detect_bmm_v6_layout(tmp_path: Path) -> None:
    p = _make_bmm_v6(tmp_path)
    assert detect_bmad_layout(p) == "bmm-v6"


def test_detect_odyssey_hybrid_layout(tmp_path: Path) -> None:
    p = _make_odyssey_hybrid(tmp_path)
    assert detect_bmad_layout(p) == "odyssey-hybrid"


def test_detect_unknown_layout_with_empty_bmad(tmp_path: Path) -> None:
    p = _make_empty_bmad(tmp_path)
    assert detect_bmad_layout(p) == "unknown"


def test_detect_not_bmad_layout(tmp_path: Path) -> None:
    p = _make_not_bmad(tmp_path)
    assert detect_bmad_layout(p) == "not-bmad"


# ── slug derivation ─────────────────────────────────────────────────────────


def test_slug_from_path_lowercases_and_sanitises(tmp_path: Path) -> None:
    p = tmp_path / "MyProject"
    p.mkdir()
    assert slug_from_path(p) == "myproject"


def test_slug_from_path_replaces_invalid_chars(tmp_path: Path) -> None:
    p = tmp_path / "foo.bar baz"
    p.mkdir()
    s = slug_from_path(p)
    # invalid chars replaced with '-'; should match registry slug regex
    assert "." not in s
    assert " " not in s


def test_slug_from_path_truncates_to_64_chars(tmp_path: Path) -> None:
    long_name = "x" * 200
    p = tmp_path / long_name
    p.mkdir()
    assert len(slug_from_path(p)) == 64


# ── registry round-trip ────────────────────────────────────────────────────


def test_registry_empty_load_on_missing_file(tmp_path: Path) -> None:
    reg = load_registry(tmp_path / "missing.yaml")
    assert reg.projects == {}


def test_registry_save_then_load_round_trip(tmp_path: Path) -> None:
    proj = _make_bmm_v6(tmp_path)
    reg = ProjectsRegistry(
        projects={
            "antares": ProjectEntry(path=proj, bmad_layout="bmm-v6"),
        }
    )
    path = tmp_path / "config" / "projects.yaml"
    out = save_registry(reg, path)
    assert out == path
    assert path.exists()
    reloaded = load_registry(path)
    assert reloaded.projects["antares"].path == proj
    assert reloaded.projects["antares"].bmad_layout == "bmm-v6"


def test_registry_rejects_invalid_slug(tmp_path: Path) -> None:
    proj = _make_bmm_v6(tmp_path)
    with pytest.raises(ValueError):
        ProjectsRegistry(
            projects={"Bad Slug!": ProjectEntry(path=proj, bmad_layout="bmm-v6")}
        )


def test_registry_rejects_relative_path(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        ProjectEntry(path=Path("relative/path"), bmad_layout="bmm-v6")


def test_registry_rejects_extra_fields(tmp_path: Path) -> None:
    proj = _make_bmm_v6(tmp_path)
    with pytest.raises(ValueError):
        ProjectEntry(path=proj, bmad_layout="bmm-v6", weird_extra="x")  # type: ignore[call-arg]


def test_registry_load_malformed_yaml_raises(tmp_path: Path) -> None:
    p = tmp_path / "broken.yaml"
    p.write_text("not: : valid: yaml", encoding="utf-8")
    with pytest.raises(ProjectRegistryError):
        load_registry(p)


def test_registry_load_non_mapping_raises(tmp_path: Path) -> None:
    p = tmp_path / "list.yaml"
    p.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(ProjectRegistryError):
        load_registry(p)


def test_registry_upsert_returns_new_instance(tmp_path: Path) -> None:
    proj = _make_bmm_v6(tmp_path)
    reg = ProjectsRegistry()
    new = reg.upsert("antares", ProjectEntry(path=proj, bmad_layout="bmm-v6"))
    assert "antares" not in reg.projects  # original unchanged
    assert "antares" in new.projects


def test_registry_upsert_rejects_bad_slug(tmp_path: Path) -> None:
    proj = _make_bmm_v6(tmp_path)
    reg = ProjectsRegistry()
    with pytest.raises(ProjectRegistryError):
        reg.upsert("Bad!", ProjectEntry(path=proj, bmad_layout="bmm-v6"))


# ── registry_path resolution ───────────────────────────────────────────────


def test_registry_path_default_location(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(REGISTRY_ENV_VAR, raising=False)
    p = registry_path(orchestrator_home=tmp_path)
    assert p == tmp_path / "config" / "projects.yaml"


def test_registry_path_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    override = tmp_path / "elsewhere" / "registry.yaml"
    monkeypatch.setenv(REGISTRY_ENV_VAR, str(override))
    p = registry_path(orchestrator_home=tmp_path)
    assert p == override.resolve()


# ── register_project ───────────────────────────────────────────────────────


def test_register_project_auto_detects_layout(tmp_path: Path) -> None:
    proj = _make_odyssey_hybrid(tmp_path)
    reg = ProjectsRegistry()
    new_reg, slug, entry = register_project(reg, proj)
    assert slug == "odyssey-like"
    assert entry.bmad_layout == "odyssey-hybrid"
    assert entry.path == proj.resolve()
    assert new_reg.projects[slug] == entry


def test_register_project_honors_explicit_slug(tmp_path: Path) -> None:
    proj = _make_bmm_v6(tmp_path)
    reg = ProjectsRegistry()
    _, slug, _ = register_project(reg, proj, slug="custom-name")
    assert slug == "custom-name"


def test_register_project_rejects_missing_path(tmp_path: Path) -> None:
    reg = ProjectsRegistry()
    with pytest.raises(ProjectRegistryError):
        register_project(reg, tmp_path / "does-not-exist")


def test_register_project_rejects_bad_slug(tmp_path: Path) -> None:
    proj = _make_bmm_v6(tmp_path)
    reg = ProjectsRegistry()
    with pytest.raises(ProjectRegistryError):
        register_project(reg, proj, slug="Bad Slug!")


def test_register_project_overwrites_existing_entry(tmp_path: Path) -> None:
    proj1 = _make_bmm_v6(tmp_path)
    proj2 = _make_odyssey_hybrid(tmp_path)
    reg = ProjectsRegistry()
    reg, _, _ = register_project(reg, proj1, slug="proj")
    reg, _, _ = register_project(reg, proj2, slug="proj")
    assert reg.projects["proj"].path == proj2.resolve()
    assert reg.projects["proj"].bmad_layout == "odyssey-hybrid"


# ── scan ───────────────────────────────────────────────────────────────────


def test_scan_empty_registry_returns_no_rows() -> None:
    rows = scan_registry(ProjectsRegistry())
    assert rows == []


def test_scan_reports_ok_for_matching_layout(tmp_path: Path) -> None:
    proj = _make_bmm_v6(tmp_path)
    reg = ProjectsRegistry(
        projects={"antares": ProjectEntry(path=proj, bmad_layout="bmm-v6")}
    )
    rows = scan_registry(reg)
    assert len(rows) == 1
    assert rows[0].slug == "antares"
    assert rows[0].status == "ok"


def test_scan_reports_missing_for_absent_path(tmp_path: Path) -> None:
    fake = tmp_path / "ghost"
    # entry refers to a path that never existed
    reg = ProjectsRegistry(
        projects={"ghost": ProjectEntry(path=fake, bmad_layout="bmm-v6")}
    )
    rows = scan_registry(reg)
    assert rows[0].status == "missing"


def test_scan_reports_stale_when_layout_diverged(tmp_path: Path) -> None:
    proj = _make_bmm_v6(tmp_path)
    # entry claims odyssey-hybrid but on-disk is bmm-v6
    reg = ProjectsRegistry(
        projects={"antares": ProjectEntry(path=proj, bmad_layout="odyssey-hybrid")}
    )
    rows = scan_registry(reg)
    assert rows[0].status == "stale"
    assert "bmm-v6" in rows[0].detail


def test_scan_sorts_rows_by_slug(tmp_path: Path) -> None:
    proj_a = _make_bmm_v6(tmp_path)
    proj_b = tmp_path / "b"
    proj_b.mkdir()
    reg = ProjectsRegistry(
        projects={
            "zebra": ProjectEntry(path=proj_a, bmad_layout="bmm-v6"),
            "alpha": ProjectEntry(path=proj_b, bmad_layout="not-bmad"),
        }
    )
    rows = scan_registry(reg)
    assert [r.slug for r in rows] == ["alpha", "zebra"]


# ── doctor ─────────────────────────────────────────────────────────────────


def test_doctor_unknown_project_reports_unregistered() -> None:
    report = doctor(ProjectsRegistry(), "ghost")
    assert isinstance(report, DoctorReport)
    assert not report.healthy
    assert report.checks[0].name == "registered"
    assert not report.checks[0].ok


def test_doctor_missing_path_short_circuits(tmp_path: Path) -> None:
    reg = ProjectsRegistry(
        projects={"ghost": ProjectEntry(path=tmp_path / "gone", bmad_layout="bmm-v6")}
    )
    report = doctor(reg, "ghost")
    assert not report.healthy
    names = [c.name for c in report.checks]
    assert "path_exists" in names
    # short-circuits before later checks
    assert "bmad_output_dir" not in names


def test_doctor_healthy_when_layout_matches(tmp_path: Path) -> None:
    proj = _make_odyssey_hybrid(tmp_path)
    (proj / "_bmad-output").mkdir()
    reg = ProjectsRegistry(
        projects={"odyssey": ProjectEntry(path=proj, bmad_layout="odyssey-hybrid")}
    )
    report = doctor(reg, "odyssey")
    names = {c.name for c in report.checks}
    assert "layout_matches" in names
    assert "bmad_output_dir" in names


def test_doctor_flags_layout_drift(tmp_path: Path) -> None:
    proj = _make_bmm_v6(tmp_path)
    (proj / "_bmad-output").mkdir()
    reg = ProjectsRegistry(
        projects={"antares": ProjectEntry(path=proj, bmad_layout="odyssey-hybrid")}
    )
    report = doctor(reg, "antares")
    layout_check = next(c for c in report.checks if c.name == "layout_matches")
    assert not layout_check.ok
    assert "bmm-v6" in layout_check.detail


def test_doctor_reads_sprint_status_when_present(tmp_path: Path) -> None:
    proj = _make_bmm_v6(tmp_path)
    out = proj / "_bmad-output"
    out.mkdir()
    (out / "sprint-status.md").write_text("# sprint\n", encoding="utf-8")
    reg = ProjectsRegistry(
        projects={"antares": ProjectEntry(path=proj, bmad_layout="bmm-v6")}
    )
    report = doctor(reg, "antares")
    sprint_check = next(c for c in report.checks if c.name == "sprint_status_readable")
    assert sprint_check.ok


def test_doctor_notes_missing_sprint_status(tmp_path: Path) -> None:
    proj = _make_bmm_v6(tmp_path)
    (proj / "_bmad-output").mkdir()
    reg = ProjectsRegistry(
        projects={"antares": ProjectEntry(path=proj, bmad_layout="bmm-v6")}
    )
    report = doctor(reg, "antares")
    assert any(c.name == "sprint_status_present" and not c.ok for c in report.checks)


# ── resume hint ────────────────────────────────────────────────────────────


def test_resume_hint_unknown_project_returns_comment() -> None:
    hint = resume_hint(ProjectsRegistry(), "ghost")
    assert hint.startswith("# unknown project")


def test_resume_hint_for_known_project(tmp_path: Path) -> None:
    proj = _make_bmm_v6(tmp_path)
    reg = ProjectsRegistry(
        projects={"antares": ProjectEntry(path=proj, bmad_layout="bmm-v6")}
    )
    hint = resume_hint(reg, "antares")
    assert "ORCHESTRATOR_TARGET_PROJECT" in hint
    assert str(proj) in hint
    assert "--project antares" in hint


# ── CLI ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CliRunner:
    """Run CLI with an isolated registry pinned via env override."""
    reg_path = tmp_path / "config" / "projects.yaml"
    monkeypatch.setenv(REGISTRY_ENV_VAR, str(reg_path))
    # Pin orchestrator_home too so other settings don't bleed into pytest cwd.
    monkeypatch.setenv("ORCHESTRATOR_ORCHESTRATOR_HOME", str(tmp_path))
    return CliRunner()


def test_cli_init_registers_project(cli: CliRunner, tmp_path: Path) -> None:
    proj = _make_odyssey_hybrid(tmp_path)
    result = cli.invoke(app, ["init", str(proj)])
    assert result.exit_code == 0, result.stdout
    assert "registered" in result.stdout
    assert "odyssey-hybrid" in result.stdout


def test_cli_init_then_scan_lists_project(cli: CliRunner, tmp_path: Path) -> None:
    proj = _make_bmm_v6(tmp_path)
    r1 = cli.invoke(app, ["init", str(proj)])
    assert r1.exit_code == 0
    r2 = cli.invoke(app, ["scan"])
    assert r2.exit_code == 0
    assert "antares-like" in r2.stdout


def test_cli_init_with_explicit_slug(cli: CliRunner, tmp_path: Path) -> None:
    proj = _make_bmm_v6(tmp_path)
    result = cli.invoke(app, ["init", str(proj), "--slug", "custom"])
    assert result.exit_code == 0
    assert "custom" in result.stdout


def test_cli_init_rejects_missing_path(cli: CliRunner, tmp_path: Path) -> None:
    result = cli.invoke(app, ["init", str(tmp_path / "gone")])
    assert result.exit_code != 0


def test_cli_scan_empty_registry(cli: CliRunner) -> None:
    result = cli.invoke(app, ["scan"])
    assert result.exit_code == 0
    assert "empty" in result.stdout


def test_cli_doctor_unknown_project_exits_nonzero(cli: CliRunner) -> None:
    result = cli.invoke(app, ["doctor", "ghost"])
    assert result.exit_code != 0


def test_cli_doctor_healthy_project_exits_zero(cli: CliRunner, tmp_path: Path) -> None:
    proj = _make_bmm_v6(tmp_path)
    (proj / "_bmad-output").mkdir()
    (proj / "_bmad-output" / "sprint-status.md").write_text("# s\n", encoding="utf-8")
    cli.invoke(app, ["init", str(proj), "--slug", "antares"])
    result = cli.invoke(app, ["doctor", "antares"])
    assert result.exit_code == 0


def test_cli_resume_prints_hint(cli: CliRunner, tmp_path: Path) -> None:
    proj = _make_bmm_v6(tmp_path)
    cli.invoke(app, ["init", str(proj), "--slug", "antares"])
    result = cli.invoke(app, ["resume-project", "antares"])
    assert result.exit_code == 0
    assert "ORCHESTRATOR_TARGET_PROJECT" in result.stdout


def test_cli_resume_unknown_project_prints_comment(cli: CliRunner) -> None:
    result = cli.invoke(app, ["resume-project", "ghost"])
    assert result.exit_code == 0
    assert "unknown project" in result.stdout


# ── CLI persistence ───────────────────────────────────────────────────────


def test_cli_init_persists_yaml(cli: CliRunner, tmp_path: Path) -> None:
    proj = _make_bmm_v6(tmp_path)
    cli.invoke(app, ["init", str(proj), "--slug", "antares"])
    reg_path = Path(os.environ[REGISTRY_ENV_VAR])
    assert reg_path.exists()
    payload = yaml.safe_load(reg_path.read_text(encoding="utf-8"))
    assert "antares" in payload["projects"]
    assert payload["projects"]["antares"]["bmad_layout"] == "bmm-v6"
