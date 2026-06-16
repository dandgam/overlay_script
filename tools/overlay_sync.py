#!/usr/bin/env python3
"""overlay_sync — keep BMAD overlays/forks byte-consistent across projects.

Этап 1 (byte-core) + Этап 1.5 (computed fork census). NO text parsing of
anchors/methods and NO brain-methods canary here — those live behind the
golden-fixture gate in Этап 3 (see plan witty-sauteeing-blanket.md). The vault
(`capture`/`init-project`/`post-upgrade`) is Этап 2; until it exists the
canonical PROJECT (odyssey) is the propagation source.

Subcommands:
    check        build manifest, run invariants, report. Read-only. (default)
    propagate    canonical -> targets for MISSING/STALE watched files.
                 --dry-run is the DEFAULT; --apply writes (backup+atomic+verify).
    revalidate   rebuild manifest from disk, re-run invariants, assert convergence.
    census       Этап 1.5: 3-way fork census (worktree vs pinned upstream vs HEAD).
    init-project Этап 2b: install vault overlays+forks into a fresh target.
                 Idempotent, exempt-aware, never-clobber; --dry-run is the DEFAULT.
    post-upgrade Этап 2b: re-apply fork patches onto a freshly re-vendored target.
                 git-apply-check first; ANY reject => exit 6, tree untouched.

Design rules enforced here (from the hardened plan):
  - overlay set = _bmad/custom/*.toml MINUS {config.toml, config.user.toml}
  - fork count is COMPUTED by census, never hardcoded
  - git-guard is fail-closed and worktree/detached-HEAD safe
  - INV-PERSIST covers overlays AND forks (uncommitted-in-canon is a finding)
  - exempt.yaml is scoped to byte-identity only (INV-OVERLAY-IDENTICAL)
  - all I/O is utf-8 + pathlib; concurrency guarded by flock per target
"""

from __future__ import annotations

import argparse
import difflib
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

# ----------------------------------------------------------------------------
# Defaults (overridable via CLI)
# ----------------------------------------------------------------------------

DEFAULT_ROOT = Path("/home/server")
DEFAULT_CANONICAL = "odyssey"
DEFAULT_PROJECTS = ["odyssey", "legal", "pcb", "Antares"]
# Pinned upstream BMAD 6.8.0 core-skills tree (the fork census baseline).
DEFAULT_UPSTREAM = Path(
    "/home/server/.npm/_npx/972d8b6f68f8e071/node_modules/bmad-method/src/core-skills"
)

OVERLAY_DIR = Path("_bmad/custom")
# config.toml = base config, config.user.toml = gitignored per-user; never sync.
OVERLAY_EXCLUDE = {"config.toml", "config.user.toml"}

# Vault (single source of truth) — Этап 2a capture target.
DEFAULT_VAULT = Path("/home/server/.claude/bmad-overlays")
BMAD_VERSION = "6.8.0"  # pinned upstream the fork base-blobs are taken from
# Fork stored as a full snapshot when changed-line density >= this; else as a patch.
# Dense rewrites (e.g. step-02a) -> snapshot; sparse tweaks (02b/c/d) -> patch.
DENSITY_SNAPSHOT_THRESHOLD = 0.5

# Vendored skill whose step files carry our forks (census target).
FORK_SKILL = "bmad-brainstorming"
FORK_SKILL_STEPS = Path(".claude/skills/bmad-brainstorming/steps")

# Watched data files (byte-identical across projects; not overlays, not forks).
WATCHED_CSVS = [
    Path(".claude/skills/bmad-brainstorming/brain-methods.csv"),
    Path(".claude/skills/bmad-advanced-elicitation/methods.csv"),
]

# Own skill: odyssey's bmad-auto-dev is THE canonical copy (the global one belongs
# to 888/Virgil and is out of scope). Its canon files are byte-identical across all
# projects; per-project config (customize.toml = epic tags) and per-install history
# (learnings.md) are EXCLUDED — watching them would register as false drift.
OWN_SKILL_DIR = Path(".claude/skills/bmad-auto-dev")
OWN_SKILL_EXCLUDE_NAMES = {"customize.toml", "learnings.md"}

EXIT_OK = 0
EXIT_WARN = 1
EXIT_ERROR = 2
EXIT_REVALIDATE = 3
EXIT_INTERNAL = 5
EXIT_CONFLICT = 6  # fork-patch-conflict / verify-present (Этап 2 verbs)


# ----------------------------------------------------------------------------
# Small utilities
# ----------------------------------------------------------------------------


def md5(path: Path) -> str:
    h = hashlib.md5()  # noqa: S324 - content identity, not security
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def md5_bytes(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()  # noqa: S324


def run_git(args: list[str], cwd: Path) -> tuple[int, str]:
    """Run a git command; return (returncode, stdout).

    Strips only the trailing newline. NEVER lstrip: `git status --porcelain`
    encodes file status in the leading two columns (` M path`), so a leading
    space is significant — stripping it shifts the first line and corrupts the
    parsed path.
    """
    proc = subprocess.run(  # noqa: S603 - fixed git binary, args list
        ["git", "-C", str(cwd), *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode, proc.stdout.rstrip("\n")


def git_show_head(repo: Path, rel: str) -> bytes | None:
    """Bytes of <rel> at HEAD, or None if untracked / not in HEAD."""
    proc = subprocess.run(  # noqa: S603
        ["git", "-C", str(repo), "show", f"HEAD:{rel}"],  # noqa: S607
        capture_output=True,
        check=False,
    )
    return proc.stdout if proc.returncode == 0 else None


@dataclass
class GitGuard:
    """Fail-closed, worktree/detached-HEAD-safe write guard for a target tree."""

    ok: bool
    reason: str
    dirty_paths: list[str] = field(default_factory=list)


def git_guard(target_root: Path, rel_paths: list[str]) -> GitGuard:
    """Refuse writes unless target is a coherent work tree and the specific
    rel_paths are clean. Any git error => refuse (fail-closed). Detached HEAD is
    allowed; worktrees (.git is a file) are fine because we never assume .git/ dir.
    """
    rc, out = run_git(["rev-parse", "--is-inside-work-tree"], target_root)
    if rc != 0 or out.strip() != "true":
        return GitGuard(False, f"not a git work tree (rc={rc})")
    rc, out = run_git(["status", "--porcelain", "--", *rel_paths], target_root)
    if rc != 0:
        return GitGuard(False, f"git status failed (rc={rc})")
    dirty = [ln[3:] for ln in out.splitlines() if ln.strip()]
    if dirty:
        return GitGuard(False, "target has uncommitted edits to target paths", dirty)
    return GitGuard(True, "clean")


@contextmanager
def project_lock(project_root: Path) -> Iterator[None]:
    """flock a per-project lockfile so parallel claude -p workers can't race."""
    bmad = project_root / "_bmad"
    bmad.mkdir(parents=True, exist_ok=True)
    lock_path = bmad / ".overlay_sync.lock"
    fh = lock_path.open("w", encoding="utf-8")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        fh.close()
        raise RuntimeError(f"target busy (locked): {project_root}") from exc
    try:
        yield
    finally:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()


def atomic_copy(src: Path, dst: Path) -> None:
    """Copy src->dst atomically (tmp in same dir + os.replace), preserving bytes."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(dst.parent), prefix=".overlay_sync.")
    try:
        with os.fdopen(fd, "wb") as out, src.open("rb") as inp:
            shutil.copyfileobj(inp, out)
        os.replace(tmp, dst)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# ----------------------------------------------------------------------------
# exempt.yaml (tiny hand-rolled reader; stdlib has no yaml)
# ----------------------------------------------------------------------------


@dataclass
class Exemption:
    artifact: str
    project: str
    reason: str
    expires: str = ""


def load_exempt(path: Path | None) -> list[Exemption]:
    """Read exempt entries. Minimal YAML-ish list-of-mappings parser; tolerant of
    absent file. Scoped ONLY to byte-identity (INV-OVERLAY-IDENTICAL)."""
    if not path or not path.exists():
        return []
    out: list[Exemption] = []
    cur: dict[str, str] = {}

    def flush() -> None:
        if cur.get("artifact") and cur.get("project"):
            out.append(
                Exemption(
                    artifact=cur["artifact"],
                    project=cur["project"],
                    reason=cur.get("reason", ""),
                    expires=cur.get("expires", ""),
                )
            )

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("- "):
            flush()
            cur = {}
            line = line[2:].strip()
        if ":" in line:
            key, _, val = line.partition(":")
            cur[key.strip()] = val.strip().strip("'\"")
    flush()
    return out


def is_exempt(exemptions: list[Exemption], artifact: str, project: str) -> Exemption | None:
    for ex in exemptions:
        if ex.artifact == artifact and ex.project == project:
            return ex
    return None


# ----------------------------------------------------------------------------
# Discovery + manifest
# ----------------------------------------------------------------------------


def discover_overlays(project_root: Path) -> dict[str, Path]:
    """Return {basename: path} for _bmad/custom/*.toml minus the config files."""
    cdir = project_root / OVERLAY_DIR
    if not cdir.is_dir():
        return {}
    return {
        p.name: p
        for p in sorted(cdir.glob("*.toml"))
        if p.name not in OVERLAY_EXCLUDE
    }


@dataclass
class CensusEntry:
    rel: str
    is_fork: bool
    head_relation: str  # uncommitted-fork | committed-fork | vendor-bump | clean | no-upstream


def fork_census(
    canonical_root: Path, upstream_skill_steps: Path
) -> list[CensusEntry]:
    """Этап 1.5: classify each brainstorming step file by 3-way diff.

    A file is a FORK iff its working-tree content differs from the pinned
    upstream (6.8.0) version. HEAD relation is reported for context:
      - uncommitted-fork: worktree != upstream, HEAD == upstream
      - committed-fork:   worktree != upstream, HEAD == worktree (committed)
      - vendor-bump:      worktree == upstream, HEAD != upstream (e.g. step-03)
      - clean:            worktree == upstream == HEAD
      - no-upstream:      no matching upstream file (own content)
    """
    steps = canonical_root / FORK_SKILL_STEPS
    out: list[CensusEntry] = []
    if not steps.is_dir():
        return out
    repo = canonical_root
    for f in sorted(steps.glob("*.md")):
        rel = str(f.relative_to(canonical_root))
        up = upstream_skill_steps / f.name
        wt_md5 = md5(f)
        up_md5 = md5(up) if up.exists() else None
        head_bytes = git_show_head(repo, rel)
        head_md5 = md5_bytes(head_bytes) if head_bytes is not None else None

        if up_md5 is None:
            out.append(CensusEntry(rel, is_fork=False, head_relation="no-upstream"))
            continue
        is_fork = wt_md5 != up_md5
        if not is_fork:
            relation = "clean" if head_md5 == up_md5 else "vendor-bump"
        elif head_md5 == up_md5:
            relation = "uncommitted-fork"
        elif head_md5 == wt_md5:
            relation = "committed-fork"
        else:
            relation = "diverging-fork"
        out.append(CensusEntry(rel, is_fork=is_fork, head_relation=relation))
    return out


def own_skill_rel_paths(canonical_root: Path) -> list[str]:
    """Canon files of our own bmad-auto-dev skill (odyssey is canonical), as rel
    paths under the project root. Excludes per-project config / per-install history
    and __pycache__ / *.pyc, which would otherwise register as false cross-project
    drift. Empty if the skill is absent in the canonical project."""
    base = canonical_root / OWN_SKILL_DIR
    if not base.is_dir():
        return []
    out: list[str] = []
    for p in sorted(base.rglob("*")):
        if not p.is_file():
            continue
        if "__pycache__" in p.parts or p.suffix == ".pyc":
            continue
        if p.name in OWN_SKILL_EXCLUDE_NAMES:
            continue
        out.append(str(p.relative_to(canonical_root)))
    return out


def watched_rel_paths(canonical_root: Path, upstream_skill_steps: Path) -> list[str]:
    """All non-overlay watched files: computed forks + data CSVs + own-skill canon."""
    forks = [e.rel for e in fork_census(canonical_root, upstream_skill_steps) if e.is_fork]
    csvs = [str(c) for c in WATCHED_CSVS]
    skill = own_skill_rel_paths(canonical_root)
    return forks + csvs + skill


@dataclass
class Finding:
    inv: str
    severity: str  # error | warn
    artifact: str
    project: str
    detail: str


@dataclass
class PlanItem:
    action: str  # CREATE | REPLACE
    artifact: str
    project: str
    src: Path
    dst: Path


@dataclass
class RollbackEntry:
    project: str
    rel: str
    backup: str | None  # None => file was created (rollback = delete)


@dataclass
class FileState:
    present: bool
    md5: str | None


@dataclass
class ArtifactInfo:
    rel: str
    canonical_md5: str
    projects: dict[str, FileState]


@dataclass
class Manifest:
    canonical: str
    projects: list[str]
    overlays: dict[str, ArtifactInfo]
    watched_files: dict[str, ArtifactInfo]
    census: list[CensusEntry]
    fork_count: int
    exemptions: list[Exemption]


def _scan_artifact(rel: str, cpath: Path, root: Path, projects: list[str]) -> ArtifactInfo:
    per_project: dict[str, FileState] = {}
    for proj in projects:
        ppath = root / proj / rel
        per_project[proj] = (
            FileState(present=True, md5=md5(ppath))
            if ppath.exists()
            else FileState(present=False, md5=None)
        )
    return ArtifactInfo(rel=rel, canonical_md5=md5(cpath), projects=per_project)


def build_manifest(
    root: Path,
    canonical: str,
    projects: list[str],
    upstream_skill_steps: Path,
    exemptions: list[Exemption],
) -> Manifest:
    """Compute the comparison manifest from real files (never hand-typed)."""
    canon_root = root / canonical
    overlays = {
        name: _scan_artifact(str(cpath.relative_to(canon_root)), cpath, root, projects)
        for name, cpath in discover_overlays(canon_root).items()
    }
    watched_files: dict[str, ArtifactInfo] = {}
    for rel in watched_rel_paths(canon_root, upstream_skill_steps):
        cpath = canon_root / rel
        if cpath.exists():
            watched_files[rel] = _scan_artifact(rel, cpath, root, projects)
    census = fork_census(canon_root, upstream_skill_steps)
    return Manifest(
        canonical=canonical,
        projects=projects,
        overlays=overlays,
        watched_files=watched_files,
        census=census,
        fork_count=sum(1 for e in census if e.is_fork),
        exemptions=exemptions,
    )


# ----------------------------------------------------------------------------
# Invariants
# ----------------------------------------------------------------------------


def run_invariants(
    manifest: Manifest, root: Path, exemptions: list[Exemption]
) -> list[Finding]:
    findings: list[Finding] = []
    canonical = manifest.canonical

    def check_artifact(name: str, info: ArtifactInfo) -> None:
        for proj in manifest.projects:
            if proj == canonical:
                continue
            pinfo = info.projects[proj]
            if not pinfo.present:
                findings.append(
                    Finding("INV-OVERLAY-PRESENT", "error", name, proj, "missing in target")
                )
            elif pinfo.md5 != info.canonical_md5:
                ex = is_exempt(exemptions, name, proj)
                if ex:
                    findings.append(
                        Finding(
                            "INV-OVERLAY-IDENTICAL",
                            "warn",
                            name,
                            proj,
                            f"differs (exempt: {ex.reason or 'no reason'})",
                        )
                    )
                else:
                    shown = (pinfo.md5 or "?")[:8]
                    findings.append(
                        Finding(
                            "INV-OVERLAY-IDENTICAL",
                            "error",
                            name,
                            proj,
                            f"differs from canonical ({shown} != {info.canonical_md5[:8]})",
                        )
                    )

    for name, info in manifest.overlays.items():
        check_artifact(name, info)
    for rel, info in manifest.watched_files.items():
        check_artifact(rel, info)

    # INV-PERSIST: any watched artifact uncommitted/untracked in the CANON project
    # is a deferred-loss risk (re-vendor / git clean would wipe it).
    canon_root = root / canonical
    persist_rels = [info.rel for info in manifest.overlays.values()] + list(
        manifest.watched_files.keys()
    )
    if persist_rels:
        rc, out = run_git(["status", "--porcelain", "--", *persist_rels], canon_root)
        if rc == 0:
            for ln in out.splitlines():
                code, path = ln[:2], ln[3:]
                if code.strip():
                    findings.append(
                        Finding(
                            "INV-PERSIST",
                            "error",
                            path,
                            canonical,
                            f"uncommitted/untracked in canon ({code.strip()}) "
                            "-> at re-vendor/clean risk",
                        )
                    )
    return findings


# ----------------------------------------------------------------------------
# Propagation
# ----------------------------------------------------------------------------


def build_plan(manifest: Manifest, root: Path, exemptions: list[Exemption]) -> list[PlanItem]:
    canon_root = root / manifest.canonical
    plan: list[PlanItem] = []

    def consider(name: str, info: ArtifactInfo) -> None:
        src = canon_root / info.rel
        for proj in manifest.projects:
            if proj == manifest.canonical:
                continue
            if is_exempt(exemptions, name, proj):
                continue
            pinfo = info.projects[proj]
            dst = root / proj / info.rel
            if not pinfo.present:
                plan.append(PlanItem("CREATE", name, proj, src, dst))
            elif pinfo.md5 != info.canonical_md5:
                plan.append(PlanItem("REPLACE", name, proj, src, dst))

    for name, info in manifest.overlays.items():
        consider(name, info)
    for rel, info in manifest.watched_files.items():
        consider(rel, info)
    return plan


def apply_plan(
    plan: list[PlanItem], root: Path, backup_dir: Path
) -> list[RollbackEntry]:
    """Apply with per-file backup, atomic write, md5 verify. Returns rollback log."""
    rollback: list[RollbackEntry] = []
    by_project: dict[str, list[PlanItem]] = {}
    for item in plan:
        by_project.setdefault(item.project, []).append(item)

    for proj, items in by_project.items():
        proj_root = root / proj
        rels = [str(it.dst.relative_to(proj_root)) for it in items]
        guard = git_guard(proj_root, rels)
        if not guard.ok:
            raise RuntimeError(
                f"git-guard refused {proj}: {guard.reason} {guard.dirty_paths}"
            )
        with project_lock(proj_root):
            for it in items:
                rel = str(it.dst.relative_to(proj_root))
                if it.dst.exists():
                    bdst = backup_dir / proj / rel
                    bdst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(it.dst, bdst)
                    rollback.append(RollbackEntry(proj, rel, str(bdst)))
                else:
                    rollback.append(RollbackEntry(proj, rel, None))
                atomic_copy(it.src, it.dst)
                if md5(it.dst) != md5(it.src):
                    raise RuntimeError(f"verify failed after write: {it.dst}")
    return rollback


def rollback_plan(rollback: list[RollbackEntry], root: Path) -> None:
    for entry in reversed(rollback):
        dst = root / entry.project / entry.rel
        if entry.backup is None:
            dst.unlink(missing_ok=True)
        else:
            atomic_copy(Path(entry.backup), dst)


# ----------------------------------------------------------------------------
# Capture (Этап 2a: canonical project -> vault)
# ----------------------------------------------------------------------------

CAT_OVERLAY = "overlay"
CAT_FORK = "fork"
CAT_SKILL = "skill"


@dataclass
class CaptureItem:
    category: str  # overlay | fork | skill
    artifact: str  # basename
    rel: str  # path relative to canonical root ("" for deferred skill)
    status: str  # capture | skip-dirty | defer
    detail: str = ""


@dataclass
class CapturedFork:
    artifact: str
    rel: str
    base_md5: str | None
    fork_md5: str
    storage: str  # snapshot | patch
    density: float
    note: str = ""


@dataclass
class CaptureResult:
    overlays: list[str]
    forks: list[CapturedFork]
    canary_errors: list[str]
    skill_files: list[str] = field(default_factory=list)


def make_patch(base: bytes, fork: bytes, name: str) -> str:
    """Unified diff base->fork, git-apply friendly (a/<name> b/<name>, default -p1)."""
    base_lines = base.decode("utf-8").splitlines(keepends=True)
    fork_lines = fork.decode("utf-8").splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(
            base_lines, fork_lines, fromfile=f"a/{name}", tofile=f"b/{name}"
        )
    )


def patch_density(base: bytes, fork: bytes, name: str) -> float:
    """Fraction of fork lines touched by the diff (changed lines / fork lines)."""
    patch = make_patch(base, fork, name)
    changed = sum(
        1
        for ln in patch.splitlines()
        if (ln.startswith("+") and not ln.startswith("+++"))
        or (ln.startswith("-") and not ln.startswith("---"))
    )
    fork_lines = max(len(fork.decode("utf-8").splitlines()), 1)
    return changed / fork_lines


def verify_patch(base: bytes, patch_text: str, expected: bytes, name: str) -> bool:
    """Round-trip canary: apply patch to base in a throwaway git repo, compare to
    expected fork bytes. Any failure => caller falls back to a full snapshot."""
    if not patch_text:
        return base == expected
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        run_git(["init", "-q"], d)
        (d / name).write_bytes(base)
        (d / "p.patch").write_text(patch_text, encoding="utf-8")
        if run_git(["apply", "--check", "p.patch"], d)[0] != 0:
            return False
        if run_git(["apply", "p.patch"], d)[0] != 0:
            return False
        return (d / name).read_bytes() == expected


@contextmanager
def vault_lock(vault: Path) -> Iterator[None]:
    """flock the vault so parallel captures can't race its commit."""
    vault.mkdir(parents=True, exist_ok=True)
    fh = (vault / ".overlay_sync.lock").open("w", encoding="utf-8")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        fh.close()
        raise RuntimeError(f"vault busy (locked): {vault}") from exc
    try:
        yield
    finally:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()


def plan_capture(
    canon_root: Path, upstream_skill_steps: Path, allow_dirty: bool
) -> list[CaptureItem]:
    """Decide per-artifact: capture / skip-dirty / defer. NEVER captures dirty
    working-tree state unless allow_dirty (then it is loudly listed)."""
    items: list[CaptureItem] = []
    overlays = discover_overlays(canon_root)  # {name: path}
    forks = [e for e in fork_census(canon_root, upstream_skill_steps) if e.is_fork]

    skill_rels = own_skill_rel_paths(canon_root)
    cap_rels = [str(p.relative_to(canon_root)) for p in overlays.values()]
    cap_rels += [e.rel for e in forks]
    cap_rels += skill_rels
    dirty: set[str] = set()
    if cap_rels:
        rc, out = run_git(["status", "--porcelain", "--", *cap_rels], canon_root)
        if rc == 0:
            dirty = {ln[3:] for ln in out.splitlines() if ln.strip()}

    for name, p in overlays.items():
        rel = str(p.relative_to(canon_root))
        if rel in dirty and not allow_dirty:
            items.append(
                CaptureItem(CAT_OVERLAY, name, rel, "skip-dirty", "uncommitted in canon")
            )
        else:
            items.append(CaptureItem(CAT_OVERLAY, name, rel, "capture"))

    for e in forks:
        name = Path(e.rel).name
        if e.rel in dirty and not allow_dirty:
            items.append(
                CaptureItem(CAT_FORK, name, e.rel, "skip-dirty", "uncommitted in canon")
            )
        else:
            items.append(CaptureItem(CAT_FORK, name, e.rel, "capture"))

    # Category 3 — own skill bmad-auto-dev: odyssey is THE canonical copy (owner
    # decision; the global one belongs to 888/Virgil and is out of scope). Capture
    # its canon files (own_skill_rel_paths already drops per-project / per-install /
    # __pycache__). skip-dirty if any canon file is uncommitted (unless allow_dirty).
    if not skill_rels:
        items.append(CaptureItem(CAT_SKILL, "bmad-auto-dev", "", "defer", "skill absent in canon"))
    elif any(r in dirty for r in skill_rels) and not allow_dirty:
        items.append(
            CaptureItem(CAT_SKILL, "bmad-auto-dev", str(OWN_SKILL_DIR), "skip-dirty",
                        "uncommitted skill files in canon")
        )
    else:
        items.append(
            CaptureItem(CAT_SKILL, "bmad-auto-dev", str(OWN_SKILL_DIR), "capture",
                        f"{len(skill_rels)} canon file(s)")
        )
    return items


def apply_capture(
    items: list[CaptureItem],
    canon_root: Path,
    upstream_skill_steps: Path,
    vault: Path,
) -> CaptureResult:
    """Write captured artifacts into the vault and run per-category canaries.
    A canary failure is recorded (caller HALTs and does NOT commit)."""
    overlays_dir = vault / "overlays"
    forks_dir = vault / "forks"
    overlays_dir.mkdir(parents=True, exist_ok=True)
    forks_dir.mkdir(parents=True, exist_ok=True)
    captured_overlays: list[str] = []
    captured_forks: list[CapturedFork] = []
    captured_skill: list[str] = []
    errors: list[str] = []

    for it in items:
        if it.status != "capture":
            continue
        if it.category == CAT_OVERLAY:
            src = canon_root / it.rel
            dst = overlays_dir / it.artifact
            atomic_copy(src, dst)
            try:  # canary: must parse as TOML
                with dst.open("rb") as fh:
                    tomllib.load(fh)
            except (tomllib.TOMLDecodeError, OSError) as exc:
                errors.append(f"overlay canary FAIL {it.artifact}: {exc}")
                continue
            if md5(dst) != md5(src):
                errors.append(f"overlay verify FAIL {it.artifact}")
                continue
            captured_overlays.append(it.artifact)
        elif it.category == CAT_FORK:
            fork_bytes = (canon_root / it.rel).read_bytes()
            fork_md5_val = md5_bytes(fork_bytes)
            base_path = upstream_skill_steps / it.artifact
            base_bytes = base_path.read_bytes() if base_path.exists() else b""
            base_md5_val = md5_bytes(base_bytes) if base_bytes else None
            # Always pin the upstream base blob (Этап 2b replays patches onto it).
            (forks_dir / f"{it.artifact}.upstream").write_bytes(base_bytes)

            note = ""
            density = (
                patch_density(base_bytes, fork_bytes, it.artifact) if base_bytes else 1.0
            )
            if not base_bytes:
                note = "no-upstream-base"
            storage = "snapshot" if density >= DENSITY_SNAPSHOT_THRESHOLD else "patch"

            if storage == "patch":
                patch_text = make_patch(base_bytes, fork_bytes, it.artifact)
                if verify_patch(base_bytes, patch_text, fork_bytes, it.artifact):
                    (forks_dir / f"{it.artifact}.patch").write_text(
                        patch_text, encoding="utf-8"
                    )
                    (forks_dir / f"{it.artifact}.snapshot").unlink(missing_ok=True)
                else:  # patch did not round-trip -> safe fallback to snapshot
                    storage = "snapshot"
                    note = (note + "; " if note else "") + "patch-roundtrip-failed->snapshot"

            if storage == "snapshot":
                snap = forks_dir / f"{it.artifact}.snapshot"
                snap.write_bytes(fork_bytes)
                (forks_dir / f"{it.artifact}.patch").unlink(missing_ok=True)
                if md5(snap) != fork_md5_val:
                    errors.append(f"fork snapshot verify FAIL {it.artifact}")
                    continue

            captured_forks.append(
                CapturedFork(
                    it.artifact, it.rel, base_md5_val, fork_md5_val, storage,
                    round(density, 3), note,
                )
            )
        elif it.category == CAT_SKILL:
            skill_dst_root = vault / "skills" / it.artifact
            for rel in own_skill_rel_paths(canon_root):
                src = canon_root / rel
                sub = Path(rel).relative_to(OWN_SKILL_DIR)
                dst = skill_dst_root / sub
                atomic_copy(src, dst)
                if md5(dst) != md5(src):
                    errors.append(f"skill verify FAIL {sub}")
                    continue
                if dst.suffix == ".py":  # canary: each script must parse
                    try:
                        compile(dst.read_text(encoding="utf-8"), str(sub), "exec")
                    except (SyntaxError, ValueError) as exc:
                        errors.append(f"skill py-canary FAIL {sub}: {exc}")
                        continue
                captured_skill.append(str(sub))
            if "SKILL.md" not in captured_skill:  # canary: the skill entrypoint must be present
                errors.append(f"skill canary FAIL {it.artifact}: SKILL.md missing from capture")
    return CaptureResult(captured_overlays, captured_forks, errors, captured_skill)


def _yaml_str(v: str) -> str:
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


def write_manifest(
    vault: Path,
    overlays: list[str],
    forks: list[CapturedFork],
    canon_head: str,
    stamp: str,
    overlay_md5s: dict[str, str],
    skill_files: list[str] | None = None,
) -> None:
    """Serialise the capture manifest (hand-rolled YAML; stdlib has no writer)."""
    out: list[str] = [
        "# manifest.yaml — generated by `overlay_sync capture` (Этап 2a). Do not hand-edit.",
        f"bmad_version: {_yaml_str(BMAD_VERSION)}",
        "captured_from: odyssey",
        f"captured_at: {_yaml_str(stamp)}",
        f"canon_head: {_yaml_str(canon_head)}",
    ]
    if overlays:
        out.append("overlays:")
        for name in sorted(overlays):
            out.append(f"  - artifact: {_yaml_str(name)}")
            out.append(f"    md5: {_yaml_str(overlay_md5s[name])}")
    else:
        out.append("overlays: []")
    if forks:
        out.append("forks:")
        for f in sorted(forks, key=lambda x: x.artifact):
            out.append(f"  - artifact: {_yaml_str(f.artifact)}")
            out.append(f"    rel: {_yaml_str(f.rel)}")
            out.append(f"    base_version: {_yaml_str(BMAD_VERSION)}")
            out.append(f"    base_md5: {_yaml_str(f.base_md5 or '')}")
            out.append(f"    fork_md5: {_yaml_str(f.fork_md5)}")
            out.append(f"    storage: {_yaml_str(f.storage)}")
            out.append(f"    density: {f.density}")
            if f.note:
                out.append(f"    note: {_yaml_str(f.note)}")
    else:
        out.append("forks: []")
    if skill_files:
        out.append("skills:")
        out.append('  - skill: "bmad-auto-dev"')
        out.append(f"    file_count: {len(skill_files)}")
    else:
        out.append("skills: []")
    (vault / "manifest.yaml").write_text("\n".join(out) + "\n", encoding="utf-8")


# ----------------------------------------------------------------------------
# Этап 2b: consume (vault -> target project): init-project + post-upgrade
# ----------------------------------------------------------------------------


def load_manifest(vault: Path) -> dict[str, list[dict[str, str]]]:
    """Parse the capture manifest (our fixed hand-rolled YAML; see write_manifest).

    Returns {'overlays': [{artifact, md5}], 'forks': [{artifact, rel, base_md5,
    fork_md5, storage, ...}]}. Section-aware list-of-mappings reader; tolerant of
    the leading top-level scalars and the inline `[]` empty lists. We own both the
    writer and this reader, so the narrow format contract is intentional."""
    mf = vault / "manifest.yaml"
    if not mf.exists():
        raise RuntimeError(f"vault has no manifest.yaml: {mf}")
    sections: dict[str, list[dict[str, str]]] = {"overlays": [], "forks": []}
    section: str | None = None
    cur: dict[str, str] = {}

    def flush() -> None:
        if section in sections and cur.get("artifact"):
            sections[section].append(dict(cur))

    for raw in mf.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if not raw.startswith((" ", "\t")):  # top-level line ends a list/scalar
            flush()
            cur = {}
            stripped = raw.rstrip()
            name = stripped[:-1] if stripped.endswith(":") else ""
            section = name if name in sections else None
            continue
        if section is None:
            continue
        item = raw.strip()
        if item.startswith("- "):
            flush()
            cur = {}
            item = item[2:].strip()
        if ":" in item:
            key, _, val = item.partition(":")
            cur[key.strip()] = val.strip().strip("'\"")
    flush()
    return sections


def apply_patch_onto(current: bytes, patch_text: str, name: str) -> tuple[bytes, bool]:
    """Apply unified diff `patch_text` onto `current` in a throwaway git repo.

    Returns (merged_bytes, True) on a clean apply, else (b"", False). Never partial:
    `git apply --check` gates before the real apply, and git apply is atomic per
    file (a reject leaves the temp file untouched and we discard it)."""
    if not patch_text:
        return current, True
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        run_git(["init", "-q"], d)
        (d / name).write_bytes(current)
        (d / "p.patch").write_text(patch_text, encoding="utf-8")
        if run_git(["apply", "--check", "p.patch"], d)[0] != 0:
            return b"", False
        if run_git(["apply", "p.patch"], d)[0] != 0:
            return b"", False
        return (d / name).read_bytes(), True


def reconstruct_fork(vault: Path, artifact: str) -> bytes:
    """Rebuild fork bytes from the vault: read `<artifact>.snapshot`, or apply
    `<artifact>.patch` onto `<artifact>.upstream`. Pure read of the vault; raises
    RuntimeError on a corrupt/missing/non-applying vault entry (caller -> conflict)."""
    fdir = vault / "forks"
    snap = fdir / f"{artifact}.snapshot"
    if snap.exists():
        return snap.read_bytes()
    patch = fdir / f"{artifact}.patch"
    upstream = fdir / f"{artifact}.upstream"
    if not patch.exists() or not upstream.exists():
        raise RuntimeError(f"vault fork {artifact}: missing .snapshot and (.patch + .upstream)")
    merged, ok = apply_patch_onto(
        upstream.read_bytes(), patch.read_text(encoding="utf-8"), artifact
    )
    if not ok:
        raise RuntimeError(f"vault fork {artifact}: pinned patch does not apply onto its own base")
    return merged


def atomic_write_bytes(dst: Path, data: bytes) -> None:
    """Atomically write `data` to `dst` (tmp in same dir + os.replace)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(dst.parent), prefix=".overlay_sync.")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, dst)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


@dataclass
class InstallItem:
    category: str  # overlay | fork
    artifact: str
    rel: str  # path relative to the target project root
    action: str  # install | skip-present | skip-exempt | conflict
    detail: str = ""
    content: bytes | None = None  # final bytes to write when action == "install"


def plan_install(
    vault: Path,
    target_root: Path,
    manifest: dict[str, list[dict[str, str]]],
    exemptions: list[Exemption],
    project: str,
    mode: str,  # "init" | "post-upgrade"
) -> list[InstallItem]:
    """Decide, WITHOUT writing, what each artifact needs in the target. Idempotent
    and never-clobber: a target already matching => skip-present; a target diverged
    from BOTH the pinned base and our fork => conflict (caller HALTs, exit 6, never
    partial). mode 'init' installs overlays+forks expecting the pinned 6.8.0 base;
    mode 'post-upgrade' re-applies fork patches onto whatever upstream is on disk."""
    items: list[InstallItem] = []

    if mode == "init":
        for ov in manifest["overlays"]:
            name = ov["artifact"]
            rel = str(OVERLAY_DIR / name)
            dst = target_root / OVERLAY_DIR / name
            if is_exempt(exemptions, name, project):
                items.append(
                    InstallItem(CAT_OVERLAY, name, rel, "skip-exempt", "by-design absent in target")
                )
                continue
            src = vault / "overlays" / name
            if not src.exists():
                items.append(InstallItem(CAT_OVERLAY, name, rel, "conflict", "vault missing overlay file"))
                continue
            content = src.read_bytes()
            if not dst.exists():
                items.append(InstallItem(CAT_OVERLAY, name, rel, "install", "create", content))
            elif md5_bytes(content) == md5(dst):
                items.append(InstallItem(CAT_OVERLAY, name, rel, "skip-present", "identical"))
            else:
                items.append(
                    InstallItem(CAT_OVERLAY, name, rel, "conflict", "present and differs — refusing to clobber")
                )

        # Own skill bmad-auto-dev: install the vault canon tree (bootstrap a project).
        skill_root = vault / "skills" / "bmad-auto-dev"
        if skill_root.is_dir():
            for src in sorted(skill_root.rglob("*")):
                if not src.is_file() or "__pycache__" in src.parts or src.suffix == ".pyc":
                    continue
                sub = src.relative_to(skill_root)
                rel = str(OWN_SKILL_DIR / sub)
                dst = target_root / rel
                content = src.read_bytes()
                if not dst.exists():
                    items.append(InstallItem(CAT_SKILL, str(sub), rel, "install", "create", content))
                elif md5_bytes(content) == md5(dst):
                    items.append(InstallItem(CAT_SKILL, str(sub), rel, "skip-present", "identical"))
                else:
                    items.append(
                        InstallItem(CAT_SKILL, str(sub), rel, "conflict",
                                    "present and differs — refusing to clobber")
                    )

    for fk in manifest["forks"]:
        name = fk["artifact"]
        rel = fk.get("rel") or str(FORK_SKILL_STEPS / name)
        base_md5 = fk.get("base_md5", "")
        fork_md5 = fk.get("fork_md5", "")
        storage = fk.get("storage", "")
        dst = target_root / rel
        cur_md5 = md5(dst) if dst.exists() else None

        if cur_md5 is not None and cur_md5 == fork_md5:
            items.append(InstallItem(CAT_FORK, name, rel, "skip-present", "already forked"))
            continue
        # Vault integrity: the reconstructed fork must match the recorded md5.
        try:
            fork_bytes = reconstruct_fork(vault, name)
        except RuntimeError as exc:
            items.append(InstallItem(CAT_FORK, name, rel, "conflict", str(exc)))
            continue
        if md5_bytes(fork_bytes) != fork_md5:
            items.append(
                InstallItem(CAT_FORK, name, rel, "conflict", "vault integrity: reconstructed fork md5 mismatch")
            )
            continue

        if mode == "init":
            if cur_md5 is None:
                items.append(InstallItem(CAT_FORK, name, rel, "install", "create (step file absent)", fork_bytes))
            elif cur_md5 == base_md5:
                items.append(
                    InstallItem(CAT_FORK, name, rel, "install", "apply fork onto pinned 6.8.0 base", fork_bytes)
                )
            else:
                items.append(
                    InstallItem(CAT_FORK, name, rel, "conflict",
                                f"target is neither base nor fork ({cur_md5[:8]}) — manual review")
                )
            continue

        # mode == "post-upgrade": re-apply onto current (possibly new) upstream.
        if cur_md5 is None:
            items.append(InstallItem(CAT_FORK, name, rel, "conflict", "step file absent after upgrade"))
            continue
        if storage == "snapshot":
            if cur_md5 == base_md5:
                items.append(
                    InstallItem(CAT_FORK, name, rel, "install", "snapshot onto unchanged upstream", fork_bytes)
                )
            else:
                items.append(
                    InstallItem(CAT_FORK, name, rel, "conflict",
                                "snapshot fork cannot merge onto changed upstream — re-author")
                )
            continue
        patch_text = (vault / "forks" / f"{name}.patch").read_text(encoding="utf-8")
        merged, ok = apply_patch_onto(dst.read_bytes(), patch_text, name)
        if ok:
            note = "patch onto upstream" + ("" if cur_md5 == base_md5 else " (changed — 3-way)")
            items.append(InstallItem(CAT_FORK, name, rel, "install", note, merged))
        else:
            items.append(
                InstallItem(CAT_FORK, name, rel, "conflict",
                            "patch rejected onto new upstream — hunks need manual rebase")
            )

    return items


def apply_install(
    items: list[InstallItem], target_root: Path, backup_dir: Path
) -> list[RollbackEntry]:
    """Write install items into the target under git-guard + flock, with per-file
    backup and a post-write md5 verify. Caller MUST have ensured zero conflicts."""
    writes = [it for it in items if it.action == "install"]
    guard = git_guard(target_root, [it.rel for it in writes])
    if not guard.ok:
        raise RuntimeError(f"git-guard refused {target_root.name}: {guard.reason} {guard.dirty_paths}")
    rollback: list[RollbackEntry] = []
    with project_lock(target_root):
        for it in writes:
            assert it.content is not None  # plan guarantees install items carry bytes
            dst = target_root / it.rel
            if dst.exists():
                bdst = backup_dir / it.rel
                bdst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dst, bdst)
                rollback.append(RollbackEntry(target_root.name, it.rel, str(bdst)))
            else:
                rollback.append(RollbackEntry(target_root.name, it.rel, None))
            atomic_write_bytes(dst, it.content)
            if md5(dst) != md5_bytes(it.content):
                raise RuntimeError(f"verify failed after write: {dst}")
    return rollback


def rollback_install(rollback: list[RollbackEntry], target_root: Path) -> None:
    for entry in reversed(rollback):
        dst = target_root / entry.rel
        if entry.backup is None:
            dst.unlink(missing_ok=True)
        else:
            atomic_copy(Path(entry.backup), dst)


# ----------------------------------------------------------------------------
# Commands
# ----------------------------------------------------------------------------


def _report(manifest: Manifest, findings: list[Finding], as_json: bool) -> None:
    if as_json:
        print(
            json.dumps(
                {
                    "fork_count": manifest.fork_count,
                    "census": [vars(e) for e in manifest.census],
                    "findings": [vars(f) for f in findings],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    print(f"canonical={manifest.canonical}  projects={','.join(manifest.projects)}")
    print(f"overlays={len(manifest.overlays)}  fork_count={manifest.fork_count} (computed)")
    for e in manifest.census:
        if e.is_fork:
            print(f"  fork: {e.rel}  [{e.head_relation}]")
    errors = [f for f in findings if f.severity == "error"]
    warns = [f for f in findings if f.severity == "warn"]
    if not findings:
        print("OK — all watched artifacts consistent.")
    for f in findings:
        mark = "ERROR" if f.severity == "error" else "warn "
        print(f"  [{mark}] {f.inv}: {f.artifact} @ {f.project} — {f.detail}")
    print(f"summary: {len(errors)} error(s), {len(warns)} warn(s)")


def _exit_code(findings: list[Finding]) -> int:
    if any(f.severity == "error" for f in findings):
        return EXIT_ERROR
    if any(f.severity == "warn" for f in findings):
        return EXIT_WARN
    return EXIT_OK


def cmd_check(args: argparse.Namespace) -> int:
    exemptions = load_exempt(args.exempt)
    manifest = build_manifest(
        args.root, args.canonical, args.projects, args.upstream_steps, exemptions
    )
    findings = run_invariants(manifest, args.root, exemptions)
    _report(manifest, findings, args.json)
    return _exit_code(findings)


def cmd_census(args: argparse.Namespace) -> int:
    canon_root = args.root / args.canonical
    census = fork_census(canon_root, args.upstream_steps)
    if args.json:
        print(json.dumps([vars(e) for e in census], ensure_ascii=False, indent=2))
    else:
        forks = [e for e in census if e.is_fork]
        print(f"computed fork_count={len(forks)} (canonical={args.canonical})")
        for e in census:
            tag = "FORK" if e.is_fork else "----"
            print(f"  [{tag}] {Path(e.rel).name:32} {e.head_relation}")
    return EXIT_OK


def cmd_propagate(args: argparse.Namespace) -> int:
    exemptions = load_exempt(args.exempt)
    manifest = build_manifest(
        args.root, args.canonical, args.projects, args.upstream_steps, exemptions
    )
    plan = build_plan(manifest, args.root, exemptions)
    if not plan:
        print("nothing to propagate — targets already consistent.")
        return EXIT_OK
    print(f"plan ({len(plan)} action(s)):")
    for it in plan:
        print(f"  {it.action:7} {it.artifact} -> {it.project}")
    if not args.apply:
        print("\n(dry-run — pass --apply to write; nothing changed)")
        return EXIT_OK
    backup_dir = args.root / args.canonical / "_bmad" / ".overlay_sync_backups" / args.stamp
    try:
        rollback = apply_plan(plan, args.root, backup_dir)
    except RuntimeError as exc:
        print(f"ABORT: {exc}", file=sys.stderr)
        return EXIT_ERROR
    (backup_dir / "ROLLBACK.json").write_text(
        json.dumps([vars(e) for e in rollback], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    # Re-validate convergence.
    manifest2 = build_manifest(
        args.root, args.canonical, args.projects, args.upstream_steps, exemptions
    )
    findings2 = [f for f in run_invariants(manifest2, args.root, exemptions)
                 if f.severity == "error" and f.inv != "INV-PERSIST"]
    if findings2:
        print("revalidate FAILED — rolling back.", file=sys.stderr)
        rollback_plan(rollback, args.root)
        return EXIT_REVALIDATE
    print(f"applied {len(plan)} action(s); backup at {backup_dir}")
    return EXIT_OK


def cmd_revalidate(args: argparse.Namespace) -> int:
    exemptions = load_exempt(args.exempt)
    manifest = build_manifest(
        args.root, args.canonical, args.projects, args.upstream_steps, exemptions
    )
    findings = run_invariants(manifest, args.root, exemptions)
    propagatable = [f for f in findings if f.inv != "INV-PERSIST"]
    _report(manifest, findings, args.json)
    return EXIT_REVALIDATE if any(f.severity == "error" for f in propagatable) else EXIT_OK


def cmd_capture(args: argparse.Namespace) -> int:
    canon_root = args.root / args.canonical
    vault: Path = args.vault
    items = plan_capture(canon_root, args.upstream_steps, args.allow_dirty)
    cap = [i for i in items if i.status == "capture"]
    skipped = [i for i in items if i.status == "skip-dirty"]
    deferred = [i for i in items if i.status == "defer"]

    n_ov = sum(1 for i in cap if i.category == CAT_OVERLAY)
    n_fk = sum(1 for i in cap if i.category == CAT_FORK)
    n_sk = sum(1 for i in cap if i.category == CAT_SKILL)
    print(f"capture plan: {n_ov} overlay(s) + {n_fk} fork(s) + {n_sk} skill(s) -> {vault}")
    for i in cap:
        print(f"  capture  [{i.category}] {i.artifact}")
    for i in skipped:
        print(f"  SKIP     [{i.category}] {i.artifact} — {i.detail} (commit it, or --allow-dirty)")
    for i in deferred:
        print(f"  defer    [{i.category}] {i.artifact} — {i.detail}")

    if not args.apply:
        print("\n(dry-run — pass --apply to write vault + run canaries; nothing changed)")
        return EXIT_OK
    if not cap:
        print("nothing to capture (all skipped/deferred).")
        return EXIT_OK

    rc, canon_head = run_git(["rev-parse", "--short", "HEAD"], canon_root)
    canon_head = canon_head if rc == 0 else "unknown"
    # captured_at = canon HEAD commit date (deterministic): re-running on the same
    # canon content reproduces an identical manifest, so capture is idempotent and
    # does not churn the vault with wall-clock-only commits. --stamp overrides.
    if args.stamp != "manual":
        stamp = args.stamp
    else:
        rc_d, cdate = run_git(["show", "-s", "--format=%cI", "HEAD"], canon_root)
        stamp = cdate if rc_d == 0 else "unknown"
    try:
        with vault_lock(vault):
            result = apply_capture(items, canon_root, args.upstream_steps, vault)
            if result.canary_errors:
                for e in result.canary_errors:
                    print(f"  CANARY FAIL: {e}", file=sys.stderr)
                print("capture HALTED — canary failed; vault NOT committed.", file=sys.stderr)
                return EXIT_CONFLICT
            overlay_md5s = {n: md5(vault / "overlays" / n) for n in result.overlays}
            write_manifest(
                vault, result.overlays, result.forks, canon_head, stamp,
                overlay_md5s, result.skill_files,
            )
            for f in result.forks:
                extra = f"  ({f.note})" if f.note else ""
                print(f"  fork {f.artifact}: storage={f.storage} density={f.density}{extra}")
            if result.skill_files:
                print(f"  skill bmad-auto-dev: {len(result.skill_files)} canon file(s)")
            if args.no_commit:
                print(
                    f"captured {len(result.overlays)} overlay(s) + "
                    f"{len(result.forks)} fork(s); NOT committed (--no-commit)."
                )
                return EXIT_OK
            wt_rc, wt_out = run_git(["rev-parse", "--is-inside-work-tree"], vault)
            if wt_rc != 0 or wt_out != "true":
                print(f"ABORT: vault is not a git work tree: {vault}", file=sys.stderr)
                return EXIT_ERROR
            run_git(["add", "-A"], vault)
            _, st = run_git(["status", "--porcelain"], vault)
            if not st:
                print(f"captured: vault already up to date at {vault}")
                return EXIT_OK
            msg = (
                f"feat(vault): capture {len(result.overlays)} overlays + "
                f"{len(result.forks)} forks + {len(result.skill_files)} skill-files "
                f"из odyssey@{canon_head}"
            )
            crc, _ = run_git(["commit", "-q", "-m", msg], vault)
            if crc != 0:
                print("ABORT: vault commit failed", file=sys.stderr)
                return EXIT_ERROR
            _, head = run_git(["rev-parse", "--short", "HEAD"], vault)
            print(
                f"captured {len(result.overlays)} overlay(s) + {len(result.forks)} fork(s) + "
                f"{len(result.skill_files)} skill-file(s); vault commit {head}"
            )
    except RuntimeError as exc:
        print(f"ABORT: {exc}", file=sys.stderr)
        return EXIT_ERROR
    return EXIT_OK


def _report_install(target: Path, mode: str, items: list[InstallItem]) -> None:
    installs = [it for it in items if it.action == "install"]
    skips = [it for it in items if it.action.startswith("skip")]
    conflicts = [it for it in items if it.action == "conflict"]
    print(
        f"{mode} plan for {target.name}: {len(installs)} install, "
        f"{len(skips)} skip, {len(conflicts)} conflict  -> {target}"
    )
    for it in installs:
        print(f"  install  [{it.category}] {it.artifact} — {it.detail}")
    for it in skips:
        print(f"  skip     [{it.category}] {it.artifact} — {it.detail}")
    for it in conflicts:
        print(f"  CONFLICT [{it.category}] {it.artifact} — {it.detail}")


def _resolve_target(args: argparse.Namespace) -> Path:
    """A bare name resolves under --root; a path (has a slash / is absolute) is used as-is."""
    t = str(args.target)
    p = Path(t)
    return p if (p.is_absolute() or "/" in t) else args.root / t


def _consume(args: argparse.Namespace, mode: str) -> int:
    if not getattr(args, "target", None):
        print("ERROR: --target <project-name-or-path> is required", file=sys.stderr)
        return EXIT_ERROR
    target_root = _resolve_target(args)
    vault: Path = args.vault
    try:
        manifest = load_manifest(vault)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_ERROR
    exemptions = load_exempt(args.exempt)
    try:
        items = plan_install(vault, target_root, manifest, exemptions, target_root.name, mode)
    except (RuntimeError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_ERROR
    _report_install(target_root, mode, items)

    if any(it.action == "conflict" for it in items):
        n = sum(1 for it in items if it.action == "conflict")
        print(
            f"\n{n} conflict(s) — HALT, target NOT touched (exit 6, never partial).",
            file=sys.stderr,
        )
        return EXIT_CONFLICT
    installs = [it for it in items if it.action == "install"]
    if not args.apply:
        print("\n(dry-run — pass --apply to write target; nothing changed)")
        return EXIT_OK
    if not installs:
        print("nothing to install — target already up to date.")
        return EXIT_OK
    backup_dir = target_root / "_bmad" / ".overlay_sync_backups" / args.stamp
    try:
        rollback = apply_install(items, target_root, backup_dir)
    except (RuntimeError, OSError) as exc:
        print(f"ABORT: {exc}", file=sys.stderr)
        return EXIT_ERROR
    backup_dir.mkdir(parents=True, exist_ok=True)
    (backup_dir / "ROLLBACK.json").write_text(
        json.dumps([vars(e) for e in rollback], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"applied {len(installs)} install(s); backup at {backup_dir}")
    return EXIT_OK


def cmd_init_project(args: argparse.Namespace) -> int:
    return _consume(args, "init")


def cmd_post_upgrade(args: argparse.Namespace) -> int:
    return _consume(args, "post-upgrade")


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="overlay_sync", description=__doc__)
    p.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    p.add_argument("--canonical", default=DEFAULT_CANONICAL)
    p.add_argument("--projects", default=",".join(DEFAULT_PROJECTS))
    p.add_argument("--upstream", type=Path, default=DEFAULT_UPSTREAM,
                   help="BMAD core-skills upstream tree (fork census baseline)")
    p.add_argument("--exempt", type=Path, default=None, help="path to exempt.yaml")
    p.add_argument("--vault", type=Path, default=DEFAULT_VAULT, help="vault repo path (capture)")
    p.add_argument("--json", action="store_true")
    p.add_argument("--stamp", default="manual", help="backup subdir name (pass a timestamp)")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("check")
    sub.add_parser("census")
    prop = sub.add_parser("propagate")
    prop.add_argument("--apply", action="store_true", help="actually write (default: dry-run)")
    sub.add_parser("revalidate")
    cap = sub.add_parser("capture")
    cap.add_argument("--apply", action="store_true",
                     help="write vault + run canaries + commit (default: dry-run)")
    cap.add_argument("--allow-dirty", action="store_true",
                     help="capture uncommitted working-tree state (loudly listed)")
    cap.add_argument("--no-commit", action="store_true",
                     help="write vault but do not git-commit it")
    for verb in ("init-project", "post-upgrade"):
        cp = sub.add_parser(verb)
        cp.add_argument("--target", required=True,
                        help="target project: a bare name (under --root) or a path")
        cp.add_argument("--apply", action="store_true",
                        help="actually write the target (default: dry-run)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.projects = [x for x in str(args.projects).split(",") if x]
    args.upstream_steps = args.upstream / FORK_SKILL / "steps"
    cmd = args.cmd or "check"
    handlers = {
        "check": cmd_check,
        "census": cmd_census,
        "propagate": cmd_propagate,
        "revalidate": cmd_revalidate,
        "capture": cmd_capture,
        "init-project": cmd_init_project,
        "post-upgrade": cmd_post_upgrade,
    }
    try:
        return handlers[cmd](args)
    except KeyboardInterrupt:
        return EXIT_INTERNAL


if __name__ == "__main__":
    raise SystemExit(main())
