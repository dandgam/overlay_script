"""E4 acceptance tests — skill upgrade pipeline + CLI subcommands.

См. spec/spec_embed_phase45_with_selflearning.md §4 E4.

Coverage:
- read_bmad_version: ok / missing / invalid YAML / missing required keys.
- compute_diff: no-change vs added/removed/modified.
- apply_patches_check + apply_patches: clean / conflict semantics.
- write_conflict_report: markdown structure for human consumption.
- update_skills: dry-run vs --apply, customize/policy/lessons preservation,
  .bmad-version rewrite, conflict rollback.
- skill_status: snapshot shape.
- Integration CLI: skill-update --apply followed by skill-status round trip
  against a mock BMad upgrade.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from bmad_orchestrator.cli.main import app
from bmad_orchestrator.runtime.skill_update import (
    BmadVersionInvalidError,
    BmadVersionNotFoundError,
    PatchConflictError,
    PatchResult,
    SourceMissingError,
    apply_patches,
    apply_patches_check,
    compute_diff,
    read_bmad_version,
    skill_status,
    update_skills,
    write_conflict_report,
)

CANONICAL_VERSION: dict[str, object] = {
    "source_path": "/tmp/synthetic-source",
    "source_repo": "odyssey (canonical BMad reference)",
    "source_git_rev": "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
    "source_git_date": "2026-05-17",
    "copied_at": "2026-05-17T12:33:47Z",
    "copied_by": "e4 fixture",
    "skills_count": 2,
    "skills": ["bmad-auto-dev", "bmad-dev-story"],
}


def _build_skills_root(
    tmp_path: Path,
    *,
    upstream_files: dict[str, str] | None = None,
    customize: dict[str, str] | None = None,
    policy: dict[str, str] | None = None,
    lessons: dict[str, str] | None = None,
    patches: dict[str, str] | None = None,
    version: dict[str, object] | None = None,
    name: str = "skills",
) -> Path:
    """Construct a synthetic ``skills/`` tree with the four data dirs + upstream."""
    root = tmp_path / name
    upstream = root / "upstream"
    upstream.mkdir(parents=True)
    for rel, body in (upstream_files or {"bmad-auto-dev/SKILL.md": "stub\n"}).items():
        p = upstream / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    if version is not None:
        (upstream / ".bmad-version").write_text(
            yaml.safe_dump(version, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
    for sub, payload in (
        ("customize", customize),
        ("policy", policy),
        ("lessons", lessons),
        ("patches", patches),
    ):
        d = root / sub
        d.mkdir(parents=True, exist_ok=True)
        for rel, body in (payload or {}).items():
            p = d / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, encoding="utf-8")
    return root


def _build_source(
    tmp_path: Path,
    files: dict[str, str],
    *,
    name: str = "source",
) -> Path:
    """Construct a synthetic upstream source tree (no .bmad-version inside)."""
    src = tmp_path / name
    src.mkdir(parents=True)
    for rel, body in files.items():
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return src


def _unified_diff(rel_path: str, before: str, after: str) -> str:
    """Hand-rolled minimal unified diff that ``git apply`` accepts.

    Single-hunk; lines preserved verbatim. ``rel_path`` is interpreted relative
    to ``git apply``'s CWD (i.e., the target directory).
    """
    before_lines = before.splitlines()
    after_lines = after.splitlines()
    header = (
        f"--- a/{rel_path}\n"
        f"+++ b/{rel_path}\n"
        f"@@ -1,{len(before_lines)} +1,{len(after_lines)} @@\n"
    )
    body = "".join(f"-{ln}\n" for ln in before_lines)
    body += "".join(f"+{ln}\n" for ln in after_lines)
    return header + body


# ─────────────── 1-4. read_bmad_version ──────────────────────────────────────


def test_read_bmad_version_ok(tmp_path: Path) -> None:
    root = _build_skills_root(tmp_path, version=CANONICAL_VERSION)
    version = read_bmad_version(root)
    assert version.source_git_rev == CANONICAL_VERSION["source_git_rev"]
    assert version.skills_count == 2
    assert version.skills == ["bmad-auto-dev", "bmad-dev-story"]


def test_read_bmad_version_missing_raises(tmp_path: Path) -> None:
    root = _build_skills_root(tmp_path)  # no version arg → no .bmad-version
    with pytest.raises(BmadVersionNotFoundError) as exc:
        read_bmad_version(root)
    assert ".bmad-version" in str(exc.value)


def test_read_bmad_version_invalid_yaml_raises(tmp_path: Path) -> None:
    root = _build_skills_root(tmp_path)
    (root / "upstream" / ".bmad-version").write_text(
        "key: value\n:bad: : indent\n", encoding="utf-8"
    )
    with pytest.raises(BmadVersionInvalidError):
        read_bmad_version(root)


def test_read_bmad_version_missing_required_keys_raises(tmp_path: Path) -> None:
    partial = dict(CANONICAL_VERSION)
    partial.pop("source_git_rev")
    partial.pop("skills_count")
    root = _build_skills_root(tmp_path, version=partial)
    with pytest.raises(BmadVersionInvalidError) as exc:
        read_bmad_version(root)
    msg = str(exc.value)
    assert "source_git_rev" in msg
    assert "skills_count" in msg


# ─────────────── 5-6. compute_diff ──────────────────────────────────────────


def test_compute_diff_no_changes_returns_empty(tmp_path: Path) -> None:
    a = _build_source(tmp_path, {"a/SKILL.md": "x\n"}, name="a")
    b = _build_source(tmp_path, {"a/SKILL.md": "x\n"}, name="b")
    diff = compute_diff(a, b)
    assert diff.is_empty
    assert diff.added == diff.removed == diff.modified == []


def test_compute_diff_detects_added_removed_modified(tmp_path: Path) -> None:
    cur = _build_source(
        tmp_path,
        {
            "shared/SKILL.md": "old body\n",
            "only-old/SKILL.md": "removed\n",
        },
        name="cur",
    )
    new = _build_source(
        tmp_path,
        {
            "shared/SKILL.md": "new body\n",
            "only-new/SKILL.md": "added\n",
        },
        name="new",
    )
    diff = compute_diff(cur, new)
    assert diff.added == ["only-new/SKILL.md"]
    assert diff.removed == ["only-old/SKILL.md"]
    assert diff.modified == ["shared/SKILL.md"]


# ─────────────── 7-11. patches ──────────────────────────────────────────────


def test_apply_patches_check_no_patches_returns_empty(tmp_path: Path) -> None:
    target = _build_source(tmp_path, {"a.txt": "hi\n"})
    patches = tmp_path / "patches"  # does not exist
    assert apply_patches_check(patches, target) == []


def test_apply_patches_check_clean_patch_returns_ok(tmp_path: Path) -> None:
    target = _build_source(tmp_path, {"file.txt": "alpha\nbeta\ngamma\n"})
    patches = tmp_path / "patches"
    patches.mkdir()
    (patches / "01-tweak.diff").write_text(
        _unified_diff("file.txt", "alpha\nbeta\ngamma\n", "alpha\nBETA\ngamma\n"),
        encoding="utf-8",
    )
    results = apply_patches_check(patches, target)
    assert len(results) == 1
    assert results[0].status == "ok"
    # check is a dry-run — target untouched
    assert (target / "file.txt").read_text(encoding="utf-8") == "alpha\nbeta\ngamma\n"


def test_apply_patches_check_conflict_patch_returns_conflict(tmp_path: Path) -> None:
    target = _build_source(tmp_path, {"file.txt": "completely different\n"})
    patches = tmp_path / "patches"
    patches.mkdir()
    (patches / "01-wont-apply.diff").write_text(
        _unified_diff("file.txt", "alpha\nbeta\ngamma\n", "alpha\nBETA\ngamma\n"),
        encoding="utf-8",
    )
    results = apply_patches_check(patches, target)
    assert len(results) == 1
    assert results[0].status == "conflict"
    assert results[0].detail  # stderr captured


def test_apply_patches_real_modifies_target(tmp_path: Path) -> None:
    target = _build_source(tmp_path, {"file.txt": "alpha\nbeta\ngamma\n"})
    patches = tmp_path / "patches"
    patches.mkdir()
    (patches / "01-tweak.diff").write_text(
        _unified_diff("file.txt", "alpha\nbeta\ngamma\n", "alpha\nBETA\ngamma\n"),
        encoding="utf-8",
    )
    results = apply_patches(patches, target)
    assert [r.status for r in results] == ["ok"]
    assert (target / "file.txt").read_text(encoding="utf-8") == "alpha\nBETA\ngamma\n"


def test_apply_patches_real_conflict_raises_after_partial(tmp_path: Path) -> None:
    target = _build_source(tmp_path, {"file.txt": "alpha\nbeta\ngamma\n"})
    patches = tmp_path / "patches"
    patches.mkdir()
    (patches / "01-ok.diff").write_text(
        _unified_diff("file.txt", "alpha\nbeta\ngamma\n", "alpha\nBETA\ngamma\n"),
        encoding="utf-8",
    )
    (patches / "02-conflict.diff").write_text(
        _unified_diff("file.txt", "context that does not match\n", "ignored\n"),
        encoding="utf-8",
    )
    with pytest.raises(PatchConflictError) as exc:
        apply_patches(patches, target)
    assert "02-conflict.diff" in str(exc.value)


# ─────────────── 12. conflict report ────────────────────────────────────────


def test_write_conflict_report_format(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    results = [
        PatchResult(patch_name="01-ok.diff", status="ok"),
        PatchResult(patch_name="02-bad.diff", status="conflict", detail="hunk #1 failed"),
    ]
    ts = _dt.datetime(2026, 5, 17, 14, 30, 0, tzinfo=_dt.UTC)
    path = write_conflict_report(
        root, results, source=Path("/src"), source_rev="abc123", ts=ts
    )
    assert path.name == "upstream-conflicts-20260517T143000Z.md"
    body = path.read_text(encoding="utf-8")
    assert "patches attempted:** 2" in body
    assert "conflicts:** 1" in body
    assert "02-bad.diff" in body
    assert "hunk #1 failed" in body
    # All-results section lists both
    assert "01-ok.diff: ok" in body
    assert "02-bad.diff: conflict" in body


# ─────────────── 13-18. update_skills orchestrator ──────────────────────────


def test_update_skills_source_missing_raises(tmp_path: Path) -> None:
    version = dict(CANONICAL_VERSION)
    version["source_path"] = str(tmp_path / "does-not-exist")
    root = _build_skills_root(tmp_path, version=version)
    with pytest.raises(SourceMissingError):
        update_skills(root, source=None, dry_run=True)


def test_update_skills_dry_run_returns_diff_no_writes(tmp_path: Path) -> None:
    root = _build_skills_root(
        tmp_path,
        upstream_files={"bmad-auto-dev/SKILL.md": "old\n"},
        version=CANONICAL_VERSION,
    )
    src = _build_source(tmp_path, {"bmad-auto-dev/SKILL.md": "new\n"})
    result = update_skills(root, source=src, dry_run=True)
    assert not result.applied
    assert result.diff.modified == ["bmad-auto-dev/SKILL.md"]
    # upstream untouched
    assert (root / "upstream" / "bmad-auto-dev" / "SKILL.md").read_text(
        encoding="utf-8"
    ) == "old\n"
    # .bmad-version untouched
    raw = yaml.safe_load((root / "upstream" / ".bmad-version").read_text(encoding="utf-8"))
    assert raw["source_git_rev"] == CANONICAL_VERSION["source_git_rev"]


def test_update_skills_apply_replaces_upstream(tmp_path: Path) -> None:
    root = _build_skills_root(
        tmp_path,
        upstream_files={"bmad-auto-dev/SKILL.md": "old\n", "stale/file.md": "drop\n"},
        version=CANONICAL_VERSION,
    )
    src = _build_source(
        tmp_path,
        {"bmad-auto-dev/SKILL.md": "new\n", "fresh/file.md": "added\n"},
    )
    ts = _dt.datetime(2026, 6, 1, 9, 0, 0, tzinfo=_dt.UTC)
    result = update_skills(root, source=src, dry_run=False, ts=ts)
    assert result.applied
    upstream = root / "upstream"
    assert (upstream / "bmad-auto-dev" / "SKILL.md").read_text(encoding="utf-8") == "new\n"
    assert (upstream / "fresh" / "file.md").exists()
    assert not (upstream / "stale" / "file.md").exists()
    # No leftover backup dir
    assert not (root / "upstream.backup").exists()


def test_update_skills_apply_preserves_customize_policy_lessons(tmp_path: Path) -> None:
    root = _build_skills_root(
        tmp_path,
        upstream_files={"bmad-auto-dev/SKILL.md": "old\n"},
        customize={"bmad-auto-dev.customize.toml": 'description_override = "tweaked"\n'},
        policy={"code-review-gates.yaml": "p0_threshold: 0.9\n"},
        lessons={"odyssey/wave-1.md": "## lesson\nfoo\n"},
        version=CANONICAL_VERSION,
    )
    src = _build_source(tmp_path, {"bmad-auto-dev/SKILL.md": "new\n"})
    update_skills(root, source=src, dry_run=False)
    # All three sibling dirs untouched, byte-for-byte
    assert (root / "customize" / "bmad-auto-dev.customize.toml").read_text(
        encoding="utf-8"
    ) == 'description_override = "tweaked"\n'
    assert (root / "policy" / "code-review-gates.yaml").read_text(
        encoding="utf-8"
    ) == "p0_threshold: 0.9\n"
    assert (root / "lessons" / "odyssey" / "wave-1.md").read_text(
        encoding="utf-8"
    ) == "## lesson\nfoo\n"


def test_update_skills_apply_updates_bmad_version(tmp_path: Path) -> None:
    root = _build_skills_root(
        tmp_path,
        upstream_files={"bmad-auto-dev/SKILL.md": "old\n"},
        version=CANONICAL_VERSION,
    )
    src = _build_source(tmp_path, {"bmad-auto-dev/SKILL.md": "new\n"})
    ts = _dt.datetime(2026, 6, 1, 9, 30, 0, tzinfo=_dt.UTC)
    update_skills(
        root, source=src, dry_run=False, ts=ts, updated_by="pytest E4 fixture"
    )
    raw = yaml.safe_load((root / "upstream" / ".bmad-version").read_text(encoding="utf-8"))
    assert raw["source_path"] == str(src)
    assert raw["copied_at"] == "2026-06-01T09:30:00Z"
    assert raw["source_git_date"] == "2026-06-01"
    assert raw["copied_by"] == "pytest E4 fixture"
    # F3 P2-1: skills + skills_count are recomputed from the new upstream tree
    # (canonical fixture lists 2 skills but only 1 is in source).
    assert raw["skills"] == ["bmad-auto-dev"]
    assert raw["skills_count"] == 1
    assert raw["source_repo"] == CANONICAL_VERSION["source_repo"]


def test_update_skills_apply_conflict_rolls_back_writes_report(tmp_path: Path) -> None:
    root = _build_skills_root(
        tmp_path,
        upstream_files={"bmad-auto-dev/SKILL.md": "alpha\nbeta\ngamma\n"},
        patches={
            "01-bad.diff": _unified_diff(
                "bmad-auto-dev/SKILL.md",
                "totally different\nlines\nhere\n",
                "x\ny\nz\n",
            )
        },
        version=CANONICAL_VERSION,
    )
    # source has new content that does NOT match the patch context either
    src = _build_source(
        tmp_path, {"bmad-auto-dev/SKILL.md": "fresh upstream body\n"}
    )
    original_body = (root / "upstream" / "bmad-auto-dev" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    ts = _dt.datetime(2026, 6, 1, 9, 45, 0, tzinfo=_dt.UTC)
    with pytest.raises(PatchConflictError):
        update_skills(root, source=src, dry_run=False, ts=ts)
    # On-disk upstream rolled back byte-for-byte
    assert (root / "upstream" / "bmad-auto-dev" / "SKILL.md").read_text(
        encoding="utf-8"
    ) == original_body
    # Conflict report written
    reports = list(root.glob("upstream-conflicts-*.md"))
    assert len(reports) == 1
    assert "01-bad.diff" in reports[0].read_text(encoding="utf-8")
    # .bmad-version unchanged
    raw = yaml.safe_load((root / "upstream" / ".bmad-version").read_text(encoding="utf-8"))
    assert raw["source_git_rev"] == CANONICAL_VERSION["source_git_rev"]


# ─────────────── 19. skill_status ───────────────────────────────────────────


def test_skill_status_reports_version_patches_pending(tmp_path: Path) -> None:
    root = _build_skills_root(
        tmp_path,
        upstream_files={"bmad-auto-dev/SKILL.md": "stub\n"},
        patches={"01-ok.diff": "irrelevant body\n"},
        version=CANONICAL_VERSION,
    )
    (root / "upstream-conflicts-20260517T120000Z.md").write_text("pre-existing\n")
    snapshot = skill_status(root)
    assert snapshot["version"] is not None
    assert snapshot["version"]["source_git_rev"] == CANONICAL_VERSION["source_git_rev"]
    assert snapshot["patches"] == ["01-ok.diff"]
    assert snapshot["pending_conflicts"] == ["upstream-conflicts-20260517T120000Z.md"]


# ─────────────── 20. CLI integration round-trip ─────────────────────────────


def test_integration_skill_update_cli_round_trip(tmp_path: Path) -> None:
    """End-to-end: dry-run shows diff, apply replaces upstream + updates version,
    skill-status reflects the new state. Exercises both subcommands through
    typer's CliRunner against a mock BMad upgrade."""
    root = _build_skills_root(
        tmp_path,
        upstream_files={"bmad-auto-dev/SKILL.md": "old upstream\n"},
        version=CANONICAL_VERSION,
    )
    src = _build_source(tmp_path, {"bmad-auto-dev/SKILL.md": "new upstream\n"})
    runner = CliRunner()

    # Step 1: dry-run shows diff, does NOT mutate upstream.
    dry = runner.invoke(
        app,
        [
            "skill-update",
            "--source", str(src),
            "--skills-root", str(root),
        ],
    )
    assert dry.exit_code == 0, dry.output
    assert "dry-run" in dry.output
    assert "~1" in dry.output  # one modified file
    assert (root / "upstream" / "bmad-auto-dev" / "SKILL.md").read_text(
        encoding="utf-8"
    ) == "old upstream\n"

    # Step 2: --apply mutates upstream and rewrites .bmad-version.
    applied = runner.invoke(
        app,
        [
            "skill-update",
            "--source", str(src),
            "--skills-root", str(root),
            "--apply",
        ],
    )
    assert applied.exit_code == 0, applied.output
    assert "applied" in applied.output
    assert (root / "upstream" / "bmad-auto-dev" / "SKILL.md").read_text(
        encoding="utf-8"
    ) == "new upstream\n"
    raw = yaml.safe_load((root / "upstream" / ".bmad-version").read_text(encoding="utf-8"))
    assert raw["source_path"] == str(src)

    # Step 3: skill-status reports the post-apply state.
    status_run = runner.invoke(
        app,
        ["skill-status", "--skills-root", str(root)],
    )
    assert status_run.exit_code == 0, status_run.output
    assert "source_repo" in status_run.output
    assert "patches" in status_run.output
