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
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
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

# Vendored skill whose step files carry our forks (census target).
FORK_SKILL = "bmad-brainstorming"
FORK_SKILL_STEPS = Path(".claude/skills/bmad-brainstorming/steps")

# Watched data files (byte-identical across projects; not overlays, not forks).
WATCHED_CSVS = [
    Path(".claude/skills/bmad-brainstorming/brain-methods.csv"),
    Path(".claude/skills/bmad-advanced-elicitation/methods.csv"),
]

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
    """Run a read-only git command; return (returncode, stdout-stripped)."""
    proc = subprocess.run(  # noqa: S603 - fixed git binary, args list
        ["git", "-C", str(cwd), *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode, proc.stdout.strip()


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


def watched_rel_paths(canonical_root: Path, upstream_skill_steps: Path) -> list[str]:
    """All non-overlay watched files: computed forks + the data CSVs."""
    forks = [e.rel for e in fork_census(canonical_root, upstream_skill_steps) if e.is_fork]
    csvs = [str(c) for c in WATCHED_CSVS]
    return forks + csvs


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
    p.add_argument("--json", action="store_true")
    p.add_argument("--stamp", default="manual", help="backup subdir name (pass a timestamp)")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("check")
    sub.add_parser("census")
    prop = sub.add_parser("propagate")
    prop.add_argument("--apply", action="store_true", help="actually write (default: dry-run)")
    sub.add_parser("revalidate")
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
    }
    try:
        return handlers[cmd](args)
    except KeyboardInterrupt:
        return EXIT_INTERNAL


if __name__ == "__main__":
    raise SystemExit(main())
