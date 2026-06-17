"""Hermetic tests for tools/overlay_sync.py (Этап 1 + 1.5).

Pure-logic and safety tests use fake file trees in tmp_path. Git-dependent
classification (census head_relation) is exercised by monkeypatching the single
git_show_head boundary, so the suite needs no git config.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "overlay_sync", Path(__file__).resolve().parents[1] / "tools" / "overlay_sync.py"
)
osync = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
sys.modules["overlay_sync"] = osync  # register before exec so dataclasses resolve __module__
_SPEC.loader.exec_module(osync)


# --- fixtures --------------------------------------------------------------


def _write(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """root/{odyssey,legal}/_bmad/custom/*.toml + brainstorming steps + upstream."""
    root = tmp_path / "root"
    for proj in ("odyssey", "legal"):
        cdir = root / proj / "_bmad" / "custom"
        _write(cdir / "bmad-prd.toml", "prd-v1\n")
        _write(cdir / "config.toml", f"base-{proj}\n")  # must be excluded
        _write(cdir / "config.user.toml", f"user-{proj}\n")  # must be excluded
    # canonical-only overlay (the auto-dev analogue)
    _write(root / "odyssey" / "_bmad" / "custom" / "bmad-auto-dev.toml", "guard\n")
    # brainstorming steps: step-01 == upstream (clean), step-02a != upstream (fork)
    up = tmp_path / "upstream" / "bmad-brainstorming" / "steps"
    _write(up / "step-01-session-setup.md", "clean\n")
    _write(up / "step-02a-user-selected.md", "UPSTREAM 7 cols\n")
    for proj in ("odyssey", "legal"):
        steps = root / proj / osync.FORK_SKILL_STEPS
        _write(steps / "step-01-session-setup.md", "clean\n")
        _write(steps / "step-02a-user-selected.md", "FORKED 3 cols\n")
    return root


# --- pure utilities --------------------------------------------------------


def test_md5_and_atomic_copy(tmp_path: Path) -> None:
    src = _write(tmp_path / "a.txt", "hello\n")
    dst = tmp_path / "sub" / "b.txt"
    osync.atomic_copy(src, dst)
    assert dst.read_text(encoding="utf-8") == "hello\n"
    assert osync.md5(src) == osync.md5(dst)


def test_discover_overlays_excludes_config(tree: Path) -> None:
    overlays = osync.discover_overlays(tree / "odyssey")
    assert "bmad-prd.toml" in overlays
    assert "bmad-auto-dev.toml" in overlays
    assert "config.toml" not in overlays
    assert "config.user.toml" not in overlays


def test_load_exempt(tmp_path: Path) -> None:
    p = _write(
        tmp_path / "exempt.yaml",
        "- artifact: bmad-create-story.toml\n"
        "  project: Antares\n"
        "  reason: by-design fork\n"
        "  expires: 2026-12-01\n",
    )
    ex = osync.load_exempt(p)
    assert len(ex) == 1
    assert ex[0].artifact == "bmad-create-story.toml"
    assert ex[0].project == "Antares"
    assert osync.is_exempt(ex, "bmad-create-story.toml", "Antares")
    assert osync.is_exempt(ex, "bmad-create-story.toml", "legal") is None
    assert osync.load_exempt(tmp_path / "nope.yaml") == []


# --- census (Этап 1.5) -----------------------------------------------------


def test_fork_census_classifies(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # step-01 worktree==upstream (clean), step-02a worktree!=upstream (fork).
    # head == upstream for both => step-02a is an uncommitted-fork.
    steps_up = tree.parent / "upstream" / "bmad-brainstorming" / "steps"

    def _upstream_bytes(rel: str) -> bytes | None:
        up = steps_up / Path(rel).name
        return up.read_bytes() if up.exists() else None

    monkeypatch.setattr(osync, "git_show_head", lambda repo, rel: _upstream_bytes(rel))
    census = osync.fork_census(tree / "odyssey", steps_up)
    by_name = {Path(e.rel).name: e for e in census}
    assert by_name["step-02a-user-selected.md"].is_fork is True
    assert by_name["step-02a-user-selected.md"].head_relation == "uncommitted-fork"
    assert by_name["step-01-session-setup.md"].is_fork is False
    assert by_name["step-01-session-setup.md"].head_relation == "clean"
    assert sum(1 for e in census if e.is_fork) == 1


def test_census_vendor_bump_not_fork(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # worktree == upstream but HEAD is an OLD different version => vendor-bump, NOT a fork.
    monkeypatch.setattr(osync, "git_show_head", lambda repo, rel: b"OLD 6.7.1 content\n")
    steps_up = tree.parent / "upstream" / "bmad-brainstorming" / "steps"
    census = osync.fork_census(tree / "odyssey", steps_up)
    by_name = {Path(e.rel).name: e for e in census}
    e = by_name["step-01-session-setup.md"]  # worktree==upstream
    assert e.is_fork is False
    assert e.head_relation == "vendor-bump"


# --- own skill canon (bmad-auto-dev, odyssey = reference) -------------------


def _skill_tree(root: Path) -> None:
    """odyssey holds the canonical bmad-auto-dev skill; legal has none."""
    s = root / "odyssey" / osync.OWN_SKILL_DIR
    _write(s / "SKILL.md", "skill body\n")
    _write(s / "scripts" / "runner.sh", "#!/bin/sh\n")
    _write(s / "templates" / "t.md", "tpl\n")
    _write(s / "customize.toml", "[epics]\n")  # per-project -> excluded
    _write(s / "learnings.md", "history\n")  # per-install -> excluded
    _write(s / "scripts" / "__pycache__" / "x.pyc", "bytecode\n")  # excluded


def test_own_skill_rel_paths_excludes_per_project(tmp_path: Path) -> None:
    root = tmp_path / "root"
    _skill_tree(root)
    rels = osync.own_skill_rel_paths(root / "odyssey")
    names = {Path(r).name for r in rels}
    assert {"SKILL.md", "runner.sh", "t.md"} <= names
    assert "customize.toml" not in names  # per-project epic tags
    assert "learnings.md" not in names  # per-install history
    assert not any("__pycache__" in r or r.endswith(".pyc") for r in rels)
    assert osync.own_skill_rel_paths(root / "legal") == []  # absent -> empty


def test_skill_drift_diff_and_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "root"
    _skill_tree(root)  # odyssey only
    monkeypatch.setattr(osync, "git_show_head", lambda repo, rel: None)
    up = tmp_path / "upstream" / "bmad-brainstorming" / "steps"
    up.mkdir(parents=True)
    m = osync.build_manifest(root, "odyssey", ["odyssey", "legal"], up, [])
    # DIFF: check reports each canon file missing in legal
    findings = [f for f in osync.run_invariants(m, root, []) if f.inv != "INV-PERSIST"]
    missing = {
        f.artifact for f in findings
        if f.project == "legal" and f.inv == "INV-OVERLAY-PRESENT"
    }
    assert str(osync.OWN_SKILL_DIR / "SKILL.md") in missing
    # excluded files never surface as drift
    assert not any(
        n in f.artifact for f in findings for n in ("customize.toml", "learnings.md")
    )
    # COPY: propagate plans CREATE for each canon file into legal
    plan = osync.build_plan(m, root, [])
    creates = {it.artifact for it in plan if it.action == "CREATE" and it.project == "legal"}
    assert str(osync.OWN_SKILL_DIR / "SKILL.md") in creates
    assert str(osync.OWN_SKILL_DIR / "scripts" / "runner.sh") in creates


# --- invariants + plan -----------------------------------------------------


def _manifest(tree: Path, monkeypatch: pytest.MonkeyPatch, exemptions=None):
    monkeypatch.setattr(osync, "git_show_head", lambda repo, rel: None)
    steps_up = tree.parent / "upstream" / "bmad-brainstorming" / "steps"
    return osync.build_manifest(tree, "odyssey", ["odyssey", "legal"], steps_up, exemptions or [])


def test_invariant_present_and_identical(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    m = _manifest(tree, monkeypatch)
    # silence INV-PERSIST (no git in tmp): only look at PRESENT/IDENTICAL
    findings = [f for f in osync.run_invariants(m, tree, []) if f.inv != "INV-PERSIST"]
    invs = {(f.inv, f.artifact, f.project) for f in findings}
    # auto-dev present only in odyssey -> missing in legal
    assert ("INV-OVERLAY-PRESENT", "bmad-auto-dev.toml", "legal") in invs
    # step-02a fork differs? no — both projects have identical "FORKED 3 cols" -> no finding
    # prd identical -> no finding
    assert not any(f.artifact == "bmad-prd.toml" for f in findings)


def test_presence_exempt_downgrades_missing(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # auto-dev.toml lives only in odyssey -> missing in legal. exempt presence -> warn,
    # so propagate's revalidate does not treat it as a blocking error.
    ex = [osync.Exemption("bmad-auto-dev.toml", "legal", "odyssey-only by-design")]
    m = _manifest(tree, monkeypatch, ex)
    adv = [
        f for f in osync.run_invariants(m, tree, ex)
        if f.inv == "INV-OVERLAY-PRESENT" and f.artifact == "bmad-auto-dev.toml" and f.project == "legal"
    ]
    assert adv and adv[0].severity == "warn"
    # no exempt -> stays a hard error
    m2 = _manifest(tree, monkeypatch, [])
    adv2 = [
        f for f in osync.run_invariants(m2, tree, [])
        if f.inv == "INV-OVERLAY-PRESENT" and f.artifact == "bmad-auto-dev.toml" and f.project == "legal"
    ]
    assert adv2 and adv2[0].severity == "error"


def test_invariant_stale_then_exempt(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # make legal's prd differ
    _write(tree / "legal" / "_bmad" / "custom" / "bmad-prd.toml", "prd-DIFFERENT\n")
    m = _manifest(tree, monkeypatch)
    findings = [f for f in osync.run_invariants(m, tree, []) if f.inv != "INV-PERSIST"]
    stale = [f for f in findings if f.artifact == "bmad-prd.toml" and f.project == "legal"]
    assert stale and stale[0].severity == "error"
    # now exempt it -> downgraded to warn
    ex = [osync.Exemption("bmad-prd.toml", "legal", "intentional")]
    m2 = _manifest(tree, monkeypatch, ex)
    findings2 = [f for f in osync.run_invariants(m2, tree, ex) if f.inv != "INV-PERSIST"]
    stale2 = [f for f in findings2 if f.artifact == "bmad-prd.toml" and f.project == "legal"]
    assert stale2 and stale2[0].severity == "warn"


def test_build_plan_create_and_replace(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tree / "legal" / "_bmad" / "custom" / "bmad-prd.toml", "prd-DIFFERENT\n")
    m = _manifest(tree, monkeypatch)
    plan = osync.build_plan(m, tree, [])
    actions = {(it.action, it.artifact, it.project) for it in plan}
    assert ("CREATE", "bmad-auto-dev.toml", "legal") in actions
    assert ("REPLACE", "bmad-prd.toml", "legal") in actions


def test_propagate_all_create_writes_rollback(tmp_path: Path) -> None:
    # Regression: an all-CREATE plan makes no backups, so backup_dir does not exist
    # when ROLLBACK.json is written. cmd_propagate must mkdir it (else FileNotFound).
    root = tmp_path / "root"
    _write(root / "odyssey" / "_bmad" / "custom" / "bmad-x.toml", 'k = "v"\n')
    (root / "legal").mkdir(parents=True, exist_ok=True)
    for proj in ("odyssey", "legal"):
        _git_repo(root / proj)
        osync.run_git(["add", "-A"], root / proj)
        osync.run_git(["commit", "-qm", "init"], root / proj)
    up = tmp_path / "upstream"
    up.mkdir()
    rc = osync.main(
        ["--root", str(root), "--canonical", "odyssey", "--projects", "odyssey,legal",
         "--upstream", str(up), "propagate", "--apply"]
    )
    assert rc == osync.EXIT_OK
    assert (root / "legal" / "_bmad" / "custom" / "bmad-x.toml").read_text(encoding="utf-8") == 'k = "v"\n'
    assert list((root / "odyssey" / "_bmad" / ".overlay_sync_backups").rglob("ROLLBACK.json"))


def test_plan_respects_exempt(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tree / "legal" / "_bmad" / "custom" / "bmad-prd.toml", "prd-DIFFERENT\n")
    ex = [osync.Exemption("bmad-prd.toml", "legal", "intentional")]
    m = _manifest(tree, monkeypatch, ex)
    plan = osync.build_plan(m, tree, ex)
    assert not any(it.artifact == "bmad-prd.toml" for it in plan)


# --- safety ----------------------------------------------------------------


def test_git_guard_fail_closed_on_non_repo(tmp_path: Path) -> None:
    g = osync.git_guard(tmp_path, ["whatever"])
    assert g.ok is False  # not a git work tree => refuse


def test_apply_then_rollback(tmp_path: Path) -> None:
    root = tmp_path / "root"
    src = _write(root / "odyssey" / "f.toml", "NEW\n")
    dst = _write(root / "legal" / "f.toml", "OLD\n")
    item = osync.PlanItem("REPLACE", "f.toml", "legal", src, dst)
    # bypass git_guard for this pure apply/rollback test
    import unittest.mock as mock

    with mock.patch.object(osync, "git_guard", return_value=osync.GitGuard(True, "ok")):
        backup = root / "backups"
        rb = osync.apply_plan([item], root, backup)
        assert dst.read_text(encoding="utf-8") == "NEW\n"
        osync.rollback_plan(rb, root)
        assert dst.read_text(encoding="utf-8") == "OLD\n"


# --- capture (Этап 2a) -----------------------------------------------------


def _git_repo(d: Path) -> None:
    osync.run_git(["init", "-q"], d)
    osync.run_git(["config", "user.email", "t@t"], d)
    osync.run_git(["config", "user.name", "t"], d)


def test_run_git_preserves_porcelain_leading_space(tmp_path: Path) -> None:
    """Regression: run_git must NOT lstrip — the leading status column of the
    first `git status --porcelain` line is significant. Old `.strip()` shifted
    the first path (e.g. `_bmad/...` -> `bmad/...`) and broke dirty detection."""
    _git_repo(tmp_path)
    (tmp_path / "zzz.txt").write_text("a\n", encoding="utf-8")
    osync.run_git(["add", "zzz.txt"], tmp_path)
    osync.run_git(["commit", "-qm", "init"], tmp_path)
    (tmp_path / "zzz.txt").write_text("b\n", encoding="utf-8")  # worktree modify
    rc, out = osync.run_git(["status", "--porcelain", "--", "zzz.txt"], tmp_path)
    assert rc == 0
    line = out.splitlines()[0]
    assert line[:2] == " M"
    assert line[3:] == "zzz.txt"  # not "zz.txt"


def test_make_patch_roundtrips_and_detects_tamper() -> None:
    base, fork = b"a\nb\nc\n", b"a\nB\nc\n"
    patch = osync.make_patch(base, fork, "f.md")
    assert osync.verify_patch(base, patch, fork, "f.md") is True
    assert osync.verify_patch(base, patch, b"a\nWRONG\nc\n", "f.md") is False


def test_patch_density_sparse_vs_dense() -> None:
    base = b"".join(b"l%d\n" % i for i in range(10))
    sparse = base.replace(b"l5\n", b"X5\n")
    dense = b"".join(b"X%d\n" % i for i in range(10))
    assert osync.patch_density(base, sparse, "f.md") < osync.DENSITY_SNAPSHOT_THRESHOLD
    assert osync.patch_density(base, dense, "f.md") >= osync.DENSITY_SNAPSHOT_THRESHOLD


def test_apply_capture_overlay_copy_and_fork_patch(tmp_path: Path) -> None:
    canon = tmp_path / "odyssey"
    _write(canon / "_bmad" / "custom" / "bmad-x.toml", 'key = "v"\n')
    up = tmp_path / "upstream" / "bmad-brainstorming" / "steps"
    base = "".join(f"line{i}\n" for i in range(10))
    _write(up / "step-02b-ai-recommended.md", base)
    fork = base.replace("line5\n", "CHANGED\n")  # sparse -> patch
    frel = str(
        _write(canon / osync.FORK_SKILL_STEPS / "step-02b-ai-recommended.md", fork)
        .relative_to(canon)
    )
    vault = tmp_path / "vault"
    items = [
        osync.CaptureItem(osync.CAT_OVERLAY, "bmad-x.toml", "_bmad/custom/bmad-x.toml", "capture"),
        osync.CaptureItem(osync.CAT_FORK, "step-02b-ai-recommended.md", frel, "capture"),
    ]
    res = osync.apply_capture(items, canon, up, vault)
    assert res.canary_errors == []
    assert "bmad-x.toml" in res.overlays
    assert (vault / "overlays" / "bmad-x.toml").read_text(encoding="utf-8") == 'key = "v"\n'
    f = res.forks[0]
    assert f.storage == "patch"
    assert (vault / "forks" / "step-02b-ai-recommended.md.patch").exists()
    assert (vault / "forks" / "step-02b-ai-recommended.md.upstream").read_text(
        encoding="utf-8"
    ) == base


def test_apply_capture_fork_dense_is_snapshot(tmp_path: Path) -> None:
    canon = tmp_path / "odyssey"
    up = tmp_path / "upstream" / "bmad-brainstorming" / "steps"
    base = "".join(f"u{i}\n" for i in range(10))
    _write(up / "step-02a-user-selected.md", base)
    fork = "".join(f"X{i}\n" for i in range(10))  # every line changed -> snapshot
    frel = str(
        _write(canon / osync.FORK_SKILL_STEPS / "step-02a-user-selected.md", fork)
        .relative_to(canon)
    )
    res = osync.apply_capture(
        [osync.CaptureItem(osync.CAT_FORK, "step-02a-user-selected.md", frel, "capture")],
        canon, up, tmp_path / "vault",
    )
    assert res.forks[0].storage == "snapshot"
    assert (tmp_path / "vault" / "forks" / "step-02a-user-selected.md.snapshot").read_text(
        encoding="utf-8"
    ) == fork


def test_apply_capture_canary_halts_on_bad_toml(tmp_path: Path) -> None:
    canon = tmp_path / "odyssey"
    _write(canon / "_bmad" / "custom" / "bad.toml", "this is = = not toml\n")
    res = osync.apply_capture(
        [osync.CaptureItem(osync.CAT_OVERLAY, "bad.toml", "_bmad/custom/bad.toml", "capture")],
        canon, tmp_path / "upstream", tmp_path / "vault",
    )
    assert res.canary_errors  # HALT signal
    assert "bad.toml" not in res.overlays


def test_plan_capture_defers_when_skill_absent(tmp_path: Path) -> None:
    canon = tmp_path / "odyssey"
    _write(canon / "_bmad" / "custom" / "bmad-x.toml", 'k = "v"\n')  # no skill dir
    items = osync.plan_capture(canon, tmp_path / "upstream", allow_dirty=False)
    skill = [i for i in items if i.category == osync.CAT_SKILL]
    assert len(skill) == 1
    assert skill[0].status == "defer"  # absent -> defer, not capture


def _canon_skill(canon: Path) -> None:
    s = canon / osync.OWN_SKILL_DIR
    _write(s / "SKILL.md", "skill body\n")
    _write(s / "scripts" / "tool.py", "x = 1\n")  # valid python
    _write(s / "templates" / "t.md", "tpl\n")
    _write(s / "customize.toml", "[epics]\n")  # excluded from canon
    _write(s / "learnings.md", "history\n")  # excluded from canon


def test_capture_skill_into_vault(tmp_path: Path) -> None:
    canon = tmp_path / "odyssey"
    _canon_skill(canon)
    vault = tmp_path / "vault"
    steps = tmp_path / "upstream" / "bmad-brainstorming" / "steps"
    items = osync.plan_capture(canon, steps, allow_dirty=False)
    skill_items = [i for i in items if i.category == osync.CAT_SKILL]
    assert skill_items and skill_items[0].status == "capture"

    res = osync.apply_capture(items, canon, steps, vault)
    assert res.canary_errors == []
    files = set(res.skill_files)
    assert {"SKILL.md", "scripts/tool.py", "templates/t.md"} <= files
    assert "customize.toml" not in files and "learnings.md" not in files
    assert (vault / "skills" / "bmad-auto-dev" / "SKILL.md").read_text(encoding="utf-8") == "skill body\n"
    assert not (vault / "skills" / "bmad-auto-dev" / "customize.toml").exists()
    # manifest records the skill block (flat, load_manifest-safe)
    osync.write_manifest(vault, [], [], "h", "s", {}, res.skill_files)
    man = (vault / "manifest.yaml").read_text(encoding="utf-8")
    assert 'skill: "bmad-auto-dev"' in man and "file_count: 3" in man


def test_capture_skill_py_canary_halts(tmp_path: Path) -> None:
    canon = tmp_path / "odyssey"
    _write(canon / osync.OWN_SKILL_DIR / "SKILL.md", "ok\n")
    _write(canon / osync.OWN_SKILL_DIR / "scripts" / "broken.py", "def (:\n")  # syntax error
    items = osync.plan_capture(canon, tmp_path / "steps", allow_dirty=False)
    res = osync.apply_capture(items, canon, tmp_path / "steps", tmp_path / "vault")
    assert any("py-canary FAIL" in e for e in res.canary_errors)  # HALT signal


def test_init_installs_skill_from_vault(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    sk = vault / "skills" / "bmad-auto-dev"
    _write(sk / "SKILL.md", "skill body\n")
    _write(sk / "scripts" / "tool.py", "x = 1\n")
    osync.write_manifest(vault, [], [], "h", "s", {}, ["SKILL.md", "scripts/tool.py"])

    target = tmp_path / "proj"
    target.mkdir(parents=True, exist_ok=True)
    _git_repo(target)
    _write(target / "README", "x\n")
    osync.run_git(["add", "-A"], target)
    osync.run_git(["commit", "-qm", "init"], target)

    rc = osync.main(["--vault", str(vault), "init-project", "--target", str(target), "--apply"])
    assert rc == osync.EXIT_OK
    assert (target / osync.OWN_SKILL_DIR / "SKILL.md").read_text(encoding="utf-8") == "skill body\n"
    assert (target / osync.OWN_SKILL_DIR / "scripts" / "tool.py").read_text(encoding="utf-8") == "x = 1\n"
    osync.run_git(["add", "-A"], target)
    osync.run_git(["commit", "-qm", "installed"], target)
    rc2 = osync.main(["--vault", str(vault), "init-project", "--target", str(target), "--apply"])
    assert rc2 == osync.EXIT_OK  # idempotent, all skip-present


# --- consume (Этап 2b: vault -> target) ------------------------------------

_STEP = "step-02b-ai-recommended.md"


def _make_vault(
    tmp_path: Path, *, base: bytes, fork: bytes, overlay_text: str = 'key = "v"\n'
) -> tuple[Path, str]:
    """Build a minimal vault (1 overlay + 1 patch-stored fork) via the REAL
    write_manifest, so load_manifest is exercised against the true on-disk format.
    Returns (vault_path, fork_rel)."""
    vault = tmp_path / "vault"
    (vault / "overlays").mkdir(parents=True)
    (vault / "forks").mkdir(parents=True)
    (vault / "overlays" / "bmad-x.toml").write_text(overlay_text, encoding="utf-8")
    (vault / "forks" / f"{_STEP}.upstream").write_bytes(base)
    (vault / "forks" / f"{_STEP}.patch").write_text(
        osync.make_patch(base, fork, _STEP), encoding="utf-8"
    )
    rel = str(osync.FORK_SKILL_STEPS / _STEP)
    cf = osync.CapturedFork(
        _STEP, rel, osync.md5_bytes(base), osync.md5_bytes(fork), "patch", 0.1
    )
    osync.write_manifest(
        vault, ["bmad-x.toml"], [cf], "deadbeef", "2026-06-17T00:00:00+07:00",
        {"bmad-x.toml": osync.md5_bytes(overlay_text.encode())},
    )
    return vault, rel


def _ten(repl: dict[int, str] | None = None) -> bytes:
    repl = repl or {}
    return b"".join((repl.get(i, f"l{i}") + "\n").encode() for i in range(10))


def test_load_manifest_roundtrips_writer(tmp_path: Path) -> None:
    base, fork = _ten(), _ten({5: "FORKED"})
    vault, rel = _make_vault(tmp_path, base=base, fork=fork)
    man = osync.load_manifest(vault)
    assert [o["artifact"] for o in man["overlays"]] == ["bmad-x.toml"]
    fk = man["forks"][0]
    assert fk["artifact"] == _STEP
    assert fk["rel"] == rel
    assert fk["storage"] == "patch"
    assert fk["base_md5"] == osync.md5_bytes(base)
    assert fk["fork_md5"] == osync.md5_bytes(fork)


def test_reconstruct_fork_patch_and_snapshot(tmp_path: Path) -> None:
    base, fork = _ten(), _ten({5: "FORKED"})
    vault, _ = _make_vault(tmp_path, base=base, fork=fork)
    assert osync.reconstruct_fork(vault, _STEP) == fork  # patch path
    # snapshot path takes precedence and returns its own bytes
    snap = b"SNAP-ONLY\n"
    (vault / "forks" / f"{_STEP}.snapshot").write_bytes(snap)
    assert osync.reconstruct_fork(vault, _STEP) == snap


def test_apply_patch_onto_clean_and_reject() -> None:
    base, fork = _ten(), _ten({5: "FORKED"})
    patch = osync.make_patch(base, fork, _STEP)
    merged, ok = osync.apply_patch_onto(base, patch, _STEP)
    assert ok and merged == fork
    # patch touches l5; a target that already replaced l5 cannot take the hunk
    _, ok2 = osync.apply_patch_onto(_ten({5: "CONFLICT"}), patch, _STEP)
    assert ok2 is False


def _target_with_step(tmp_path: Path, rel: str, content: bytes) -> Path:
    target = tmp_path / "proj"
    target.mkdir(parents=True, exist_ok=True)  # git init needs an existing cwd
    _git_repo(target)
    _write(target / rel, content.decode())
    osync.run_git(["add", "-A"], target)
    osync.run_git(["commit", "-qm", "init"], target)
    return target


def test_init_project_installs_then_idempotent(tmp_path: Path) -> None:
    base, fork = _ten(), _ten({5: "FORKED"})
    vault, rel = _make_vault(tmp_path, base=base, fork=fork)
    target = _target_with_step(tmp_path, rel, base)  # step on pinned base, overlay absent

    rc = osync.main(["--vault", str(vault), "init-project", "--target", str(target), "--apply"])
    assert rc == osync.EXIT_OK
    assert (target / "_bmad" / "custom" / "bmad-x.toml").read_text(encoding="utf-8") == 'key = "v"\n'
    assert (target / rel).read_bytes() == fork  # fork applied onto base

    osync.run_git(["add", "-A"], target)
    osync.run_git(["commit", "-qm", "installed"], target)
    rc2 = osync.main(["--vault", str(vault), "init-project", "--target", str(target), "--apply"])
    assert rc2 == osync.EXIT_OK  # all skip-present, nothing to do
    assert (target / rel).read_bytes() == fork


def test_init_project_conflict_halts_never_partial(tmp_path: Path) -> None:
    base, fork = _ten(), _ten({5: "FORKED"})
    vault, rel = _make_vault(tmp_path, base=base, fork=fork)
    target = _target_with_step(tmp_path, rel, base)  # fork IS installable...
    # ...but an overlay already exists and differs -> whole run must HALT
    _write(target / "_bmad" / "custom" / "bmad-x.toml", "DIFFERENT\n")
    osync.run_git(["add", "-A"], target)
    osync.run_git(["commit", "-qm", "diff-overlay"], target)

    rc = osync.main(["--vault", str(vault), "init-project", "--target", str(target), "--apply"])
    assert rc == osync.EXIT_CONFLICT
    # never partial: the installable fork was NOT written despite the overlay conflict
    assert (target / rel).read_bytes() == base
    assert (target / "_bmad" / "custom" / "bmad-x.toml").read_text(encoding="utf-8") == "DIFFERENT\n"


def test_init_fork_diverged_is_conflict(tmp_path: Path) -> None:
    base, fork = _ten(), _ten({5: "FORKED"})
    vault, rel = _make_vault(tmp_path, base=base, fork=fork)
    target = _target_with_step(tmp_path, rel, b"NEITHER BASE NOR FORK\n")
    rc = osync.main(["--vault", str(vault), "init-project", "--target", str(target)])
    assert rc == osync.EXIT_CONFLICT  # dry-run still surfaces conflict + exit 6


def test_init_respects_exempt_presence(tmp_path: Path) -> None:
    base, fork = _ten(), _ten({5: "FORKED"})
    vault, rel = _make_vault(tmp_path, base=base, fork=fork)
    target = _target_with_step(tmp_path, rel, base)
    exempt = _write(
        tmp_path / "exempt.yaml",
        "- artifact: bmad-x.toml\n  project: proj\n  reason: by-design absent\n",
    )
    rc = osync.main(
        ["--exempt", str(exempt), "--vault", str(vault), "init-project",
         "--target", str(target), "--apply"]
    )
    assert rc == osync.EXIT_OK
    assert not (target / "_bmad" / "custom" / "bmad-x.toml").exists()  # exempt -> not installed
    assert (target / rel).read_bytes() == fork  # fork still installs


def test_post_upgrade_3way_merge(tmp_path: Path) -> None:
    base, fork = _ten(), _ten({5: "FORKED"})  # patch touches l5
    vault, rel = _make_vault(tmp_path, base=base, fork=fork)
    # re-vendor bumped a DIFFERENT line (l0) -> patch must 3-way onto new upstream
    new_up = _ten({0: "NEWUPSTREAM"})
    target = _target_with_step(tmp_path, rel, new_up)

    rc = osync.main(["--vault", str(vault), "post-upgrade", "--target", str(target), "--apply"])
    assert rc == osync.EXIT_OK
    merged = (target / rel).read_bytes()
    assert b"NEWUPSTREAM" in merged and b"FORKED" in merged


def test_post_upgrade_reject_is_conflict(tmp_path: Path) -> None:
    base, fork = _ten(), _ten({5: "FORKED"})
    vault, rel = _make_vault(tmp_path, base=base, fork=fork)
    # re-vendor changed the SAME line the patch needs (l5) -> hunk rejects
    new_up = _ten({5: "UPSTREAM-CHANGED-SAME-LINE"})
    target = _target_with_step(tmp_path, rel, new_up)

    rc = osync.main(["--vault", str(vault), "post-upgrade", "--target", str(target), "--apply"])
    assert rc == osync.EXIT_CONFLICT
    assert (target / rel).read_bytes() == new_up  # untouched, never partial
