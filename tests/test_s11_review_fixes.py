"""Regression tests for S11 review fixes (Phase 4B).

One section per finding from
``.claude/checkpoints/parallelism_initiatives-review-S10.md``. Each test pins
the post-fix behaviour so a future refactor that re-introduces the bug fails
loudly.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

from bmad_orchestrator.cli.main import (
    _SUBPROCESS_ENV_ALLOWLIST,
    _drain_capped,
    _read_spend_report,
    _subprocess_env,
)
from bmad_orchestrator.runtime.multi_run import (
    MultiProjectPlan,
    ProjectRunResult,
    run_multi,
)
from bmad_orchestrator.runtime.project_registry import (
    ProjectEntry,
    ProjectIsolationError,
    ProjectsRegistry,
    register_project,
    validate_project_path,
)

# ── helpers ──────────────────────────────────────────────────────────────────


def _fixture_registry(tmp_path: Path, slugs: tuple[str, ...]) -> ProjectsRegistry:
    """Build a tiny registry of bmm-v6 fixture projects under ``tmp_path``."""
    projects: dict[str, ProjectEntry] = {}
    for slug in slugs:
        root = tmp_path / slug
        (root / "_bmad" / "bmm").mkdir(parents=True, exist_ok=True)
        (root / "_bmad" / "bmm" / "config.yaml").write_text(
            f"project_name: {slug}\n", encoding="utf-8"
        )
        (root / "_bmad-output" / "implementation-artifacts").mkdir(
            parents=True, exist_ok=True
        )
        projects[slug] = ProjectEntry(path=root.resolve(), bmad_layout="bmm-v6")
    return ProjectsRegistry(projects=projects)


# ── P1-A: SharedSpendTracker receives production spend ──────────────────────


class TestP1ASpendHandoff:
    """Child orchestrators write ``spend.json``; parent folds into shared cap."""

    def test_read_spend_report_returns_zero_on_missing(self, tmp_path: Path) -> None:
        path = tmp_path / "missing" / "spend.json"
        assert _read_spend_report(path) == 0.0

    def test_read_spend_report_parses_payload(self, tmp_path: Path) -> None:
        wrap = tmp_path / "bmad-multi-slug-abc"
        wrap.mkdir()
        path = wrap / "spend.json"
        path.write_text(json.dumps({"spent_usd": 12.5}), encoding="utf-8")
        assert _read_spend_report(path) == 12.5
        assert not path.exists(), "report file should be cleaned up after read"
        assert not wrap.exists(), "tempdir should be removed when prefix matches"

    def test_read_spend_report_returns_zero_on_malformed(
        self, tmp_path: Path
    ) -> None:
        wrap = tmp_path / "bmad-multi-slug-bad"
        wrap.mkdir()
        path = wrap / "spend.json"
        path.write_text("{not-json", encoding="utf-8")
        assert _read_spend_report(path) == 0.0

    async def test_shim_runner_spend_reaches_tracker(
        self, tmp_path: Path
    ) -> None:
        """Real-subprocess shim writes spend.json → parent updates shared tracker."""
        registry = _fixture_registry(tmp_path, ("alpha", "beta"))

        shim = textwrap.dedent(
            """
            import json, os, pathlib, sys
            target = pathlib.Path(os.environ["BMAD_MULTI_SPEND_REPORT"])
            target.write_text(json.dumps({"spent_usd": 7.5}), encoding="utf-8")
            sys.exit(0)
            """
        ).strip()

        async def runner(slot, tracker, plan):
            spend_report = Path(
                tempfile.mkdtemp(prefix=f"bmad-multi-{slot.slug}-")
            ) / "spend.json"
            env = dict(os.environ)
            env["BMAD_MULTI_SPEND_REPORT"] = str(spend_report)
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-c", shim,
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            spent = _read_spend_report(spend_report)
            await tracker.add(spent)
            return ProjectRunResult(
                slug=slot.slug, completed=proc.returncode == 0, spent_usd=spent
            )

        plan = MultiProjectPlan(
            projects=("alpha", "beta"),
            total_parallel=2,
            wave="1a",
            daily_max_spend_usd=20.0,
            mock=False,
        )
        outcome = await run_multi(plan, registry=registry, runner_fn=runner)
        assert outcome.total_spent_usd == pytest.approx(15.0)
        assert outcome.per_project["alpha"].spent_usd == pytest.approx(7.5)


# ── P1-C: stdout DEVNULL / stderr capped ────────────────────────────────────


class TestP1CStreamCaps:
    """``_drain_capped`` returns first N bytes and drains rest of stream."""

    async def test_drain_capped_truncates_to_cap(self) -> None:
        reader = asyncio.StreamReader()
        payload = b"x" * 200_000
        reader.feed_data(payload)
        reader.feed_eof()
        out = await _drain_capped(reader, cap_bytes=1024)
        assert len(out) == 1024
        assert out == b"x" * 1024

    async def test_drain_capped_returns_all_when_below_cap(self) -> None:
        reader = asyncio.StreamReader()
        reader.feed_data(b"hello world\n")
        reader.feed_eof()
        out = await _drain_capped(reader, cap_bytes=1024)
        assert out == b"hello world\n"

    async def test_drain_capped_handles_none(self) -> None:
        assert await _drain_capped(None, cap_bytes=64) == b""


# ── P1-D: env allow-list ────────────────────────────────────────────────────


class TestP1DEnvAllowlist:
    """``_subprocess_env`` filters orchestrator-internal env from child."""

    def test_bmad_disable_budget_not_propagated(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("BMAD_DISABLE_BUDGET", "1")
        monkeypatch.setenv("BMAD_AUTO_SPLIT", "1")
        monkeypatch.setenv("BMAD_PROJECTS_REGISTRY", "/tmp/test.yaml")
        monkeypatch.setenv("BMAD_REQUIRE_CGROUP", "1")
        env = _subprocess_env(tmp_path / "slot", tmp_path / "spend.json")
        assert "BMAD_DISABLE_BUDGET" not in env
        assert "BMAD_AUTO_SPLIT" not in env
        assert "BMAD_PROJECTS_REGISTRY" not in env
        assert "BMAD_REQUIRE_CGROUP" not in env

    def test_orchestrator_target_and_spend_report_set(
        self, tmp_path: Path
    ) -> None:
        env = _subprocess_env(tmp_path / "p", tmp_path / "spend.json")
        assert env["ORCHESTRATOR_TARGET_PROJECT"] == str(tmp_path / "p")
        assert env["BMAD_MULTI_SPEND_REPORT"] == str(tmp_path / "spend.json")

    def test_allowlist_keys_pass_through(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("PATH", "/usr/bin:/bin")
        monkeypatch.setenv("HOME", "/home/test")
        monkeypatch.setenv("LANG", "en_US.UTF-8")
        env = _subprocess_env(tmp_path, tmp_path / "s.json")
        assert env["PATH"] == "/usr/bin:/bin"
        assert env["HOME"] == "/home/test"
        assert env["LANG"] == "en_US.UTF-8"

    def test_anthropic_api_key_passthrough(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        env = _subprocess_env(tmp_path, tmp_path / "s.json")
        assert env["ANTHROPIC_API_KEY"] == "sk-ant-test"

    def test_allowlist_excludes_bmad_prefix(self) -> None:
        for key in _SUBPROCESS_ENV_ALLOWLIST:
            assert not key.startswith("BMAD_"), (
                f"allowlist must not include BMAD_* keys (got {key!r})"
            )
        assert not any(
            key.startswith("ORCHESTRATOR_") for key in _SUBPROCESS_ENV_ALLOWLIST
        )


# ── P1-E: isolation at registry + single-project entry ─────────────────────


class TestP1EIsolationGate:
    """``validate_project_path`` enforced at ``register_project`` + spawn boundary."""

    def test_validate_rejects_forbidden_path(self) -> None:
        with pytest.raises(ProjectIsolationError, match="forbidden host mount"):
            validate_project_path(Path("/home/server/crm"))

    def test_validate_rejects_subpath(self) -> None:
        with pytest.raises(ProjectIsolationError):
            validate_project_path(Path("/home/server/crm/agent"))

    def test_validate_allows_safe_path(self, tmp_path: Path) -> None:
        safe = tmp_path / "safe-project"
        safe.mkdir()
        validate_project_path(safe)

    def test_register_project_rejects_forbidden(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        forbidden_dir = tmp_path / "crm-mirror"
        forbidden_dir.mkdir()
        from bmad_orchestrator.runtime import project_registry as pr_mod

        monkeypatch.setattr(
            pr_mod, "FORBIDDEN_PROJECT_PATHS", (forbidden_dir,)
        )
        reg = ProjectsRegistry(projects={})
        with pytest.raises(ProjectIsolationError):
            register_project(reg, forbidden_dir)

    def test_register_project_rejects_subpath_of_forbidden(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        forbidden_dir = tmp_path / "crm-mirror"
        forbidden_dir.mkdir()
        sub = forbidden_dir / "child"
        sub.mkdir()
        from bmad_orchestrator.runtime import project_registry as pr_mod

        monkeypatch.setattr(
            pr_mod, "FORBIDDEN_PROJECT_PATHS", (forbidden_dir,)
        )
        reg = ProjectsRegistry(projects={})
        with pytest.raises(ProjectIsolationError):
            register_project(reg, sub)


# ── P1-B: per-child timeout ─────────────────────────────────────────────────


class TestP1BTimeout:
    """``per_project_timeout_sec`` validated + slow child SIGKILLed."""

    def test_timeout_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="per_project_timeout_sec"):
            MultiProjectPlan(
                projects=("a",),
                total_parallel=1,
                wave="1a",
                per_project_timeout_sec=0,
            )

    async def test_slow_child_times_out_and_sibling_completes(
        self, tmp_path: Path
    ) -> None:
        """Hung shim should not park the wave; sibling still completes."""
        registry = _fixture_registry(tmp_path, ("fast", "slow"))

        async def runner(slot, tracker, plan):
            if slot.slug == "slow":
                proc = await asyncio.create_subprocess_exec(
                    sys.executable, "-c", "import time; time.sleep(60)",
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                timed_out = False
                try:
                    await asyncio.wait_for(
                        proc.wait(), timeout=plan.per_project_timeout_sec
                    )
                except TimeoutError:
                    timed_out = True
                    proc.kill()
                    await proc.wait()
                return ProjectRunResult(
                    slug=slot.slug,
                    completed=not timed_out,
                    error="timeout" if timed_out else None,
                )
            return ProjectRunResult(slug=slot.slug, completed=True)

        plan = MultiProjectPlan(
            projects=("fast", "slow"),
            total_parallel=2,
            wave="1a",
            per_project_timeout_sec=0.5,
        )
        outcome = await run_multi(plan, registry=registry, runner_fn=runner)
        assert outcome.per_project["fast"].completed is True
        assert outcome.per_project["slow"].completed is False
        assert "timeout" in (outcome.per_project["slow"].error or "")


# ── H-2: stale worker home cleanup ─────────────────────────────────────────


class TestH2StaleWorkerHomeCleanup:
    """``cleanup_stale_worker_homes`` rms old ``bmad-worker-*`` dirs only."""

    def test_old_dir_removed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import tempfile as _tempfile
        import time as _time

        from bmad_orchestrator.runtime.worker_spawn import cleanup_stale_worker_homes

        monkeypatch.setattr(_tempfile, "gettempdir", lambda: str(tmp_path))
        stale = tmp_path / "bmad-worker-old-xxxx"
        stale.mkdir()
        (stale / ".claude.json").write_text("{}", encoding="utf-8")
        old = _time.time() - 7200  # 2h ago
        os.utime(stale, (old, old))
        removed = cleanup_stale_worker_homes(max_age_seconds=3600)
        assert removed >= 1
        assert not stale.exists()

    def test_fresh_dir_kept(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import tempfile as _tempfile

        from bmad_orchestrator.runtime.worker_spawn import cleanup_stale_worker_homes

        monkeypatch.setattr(_tempfile, "gettempdir", lambda: str(tmp_path))
        fresh = tmp_path / "bmad-worker-active-yyyy"
        fresh.mkdir()
        removed = cleanup_stale_worker_homes(max_age_seconds=3600)
        assert fresh.exists()
        assert removed == 0

    def test_non_prefixed_dir_ignored(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import tempfile as _tempfile
        import time as _time

        from bmad_orchestrator.runtime.worker_spawn import cleanup_stale_worker_homes

        monkeypatch.setattr(_tempfile, "gettempdir", lambda: str(tmp_path))
        other = tmp_path / "some-other-tmp"
        other.mkdir()
        old = _time.time() - 7200
        os.utime(other, (old, old))
        cleanup_stale_worker_homes(max_age_seconds=3600)
        assert other.exists()


# ── H-1: sandbox sensitive-path blackouts ──────────────────────────────────


class TestH1SandboxBlackouts:
    """``BwrapSandbox.wrap_command`` includes blackouts on sensitive host paths."""

    def test_crm_directory_blackout_present_when_host_has_path(
        self, tmp_path: Path
    ) -> None:
        from bmad_orchestrator.runtime.sandbox import BwrapSandbox

        if not Path("/home/server/crm").exists():
            pytest.skip("no /home/server/crm on this host — blackout no-op")
        sb = BwrapSandbox()
        out = sb.wrap_command(["echo"], worktree=tmp_path)
        tmpfs_indices = [i for i, t in enumerate(out) if t == "--tmpfs"]
        crm_blacked = any(out[i + 1] == "/home/server/crm" for i in tmpfs_indices)
        assert crm_blacked, "expected --tmpfs /home/server/crm in bwrap args"

    def test_etc_shadow_blackout_present(self, tmp_path: Path) -> None:
        from bmad_orchestrator.runtime.sandbox import BwrapSandbox

        if not Path("/etc/shadow").exists():
            pytest.skip("no /etc/shadow on this host")
        sb = BwrapSandbox()
        out = sb.wrap_command(["echo"], worktree=tmp_path)
        # /etc/shadow is a file → --ro-bind /dev/null /etc/shadow
        for i, t in enumerate(out):
            if t == "--ro-bind" and out[i + 1] == "/dev/null" and out[i + 2] == "/etc/shadow":
                return
        pytest.fail("expected --ro-bind /dev/null /etc/shadow in bwrap args")

    def test_blackouts_appear_after_open_ro_bind(self, tmp_path: Path) -> None:
        """Override order: blackouts must come after the blanket ``--ro-bind / /``."""
        from bmad_orchestrator.runtime.sandbox import BwrapSandbox

        sb = BwrapSandbox()
        out = sb.wrap_command(["echo"], worktree=tmp_path)
        ro_root_idx = next(
            (
                i
                for i, t in enumerate(out)
                if t == "--ro-bind" and out[i + 1] == "/" and out[i + 2] == "/"
            ),
            None,
        )
        assert ro_root_idx is not None, "missing --ro-bind / / in bwrap args"
        if Path("/etc/shadow").exists():
            shadow_idx = next(
                (
                    i
                    for i, t in enumerate(out)
                    if t == "--ro-bind"
                    and i + 2 < len(out)
                    and out[i + 1] == "/dev/null"
                    and out[i + 2] == "/etc/shadow"
                ),
                None,
            )
            assert shadow_idx is not None and shadow_idx > ro_root_idx


# ── P1-F: auto-split fallback resets worktree ──────────────────────────────


class TestP1FAutoSplitReset:
    """``_reset_worktree_to_base`` restores worktree HEAD to ``base_sha``."""

    def test_reset_drops_extra_commits(self, tmp_path: Path) -> None:
        from bmad_orchestrator.agent.run import _reset_worktree_to_base

        repo = tmp_path / "repo"
        repo.mkdir()
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
        }

        def run(*args: str) -> str:
            r = subprocess.run(
                ["git", "-C", str(repo), *args],
                capture_output=True,
                text=True,
                check=True,
                env=env,
            )
            return r.stdout.strip()

        run("init", "-q")
        run("config", "commit.gpgsign", "false")
        (repo / "a.txt").write_text("a\n", encoding="utf-8")
        run("add", "a.txt")
        run("commit", "-q", "-m", "base")
        base_sha = run("rev-parse", "HEAD")

        # Simulate partial sub-story commits landed by auto_split.
        for i in range(2):
            (repo / f"sub{i}.txt").write_text(f"sub{i}\n", encoding="utf-8")
            run("add", f"sub{i}.txt")
            run("commit", "-q", "-m", f"sub story {i}")
        assert run("rev-parse", "HEAD") != base_sha
        assert (repo / "sub0.txt").exists()

        _reset_worktree_to_base(repo, base_sha)

        assert run("rev-parse", "HEAD") == base_sha
        assert not (repo / "sub0.txt").exists()
        assert not (repo / "sub1.txt").exists()

    def test_reset_handles_missing_base_sha(self, tmp_path: Path) -> None:
        from bmad_orchestrator.agent.run import _reset_worktree_to_base

        # Empty base_sha must no-op (logged warning, not raise).
        _reset_worktree_to_base(tmp_path, "")

    def test_reset_handles_invalid_sha(self, tmp_path: Path) -> None:
        from bmad_orchestrator.agent.run import _reset_worktree_to_base

        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(
            ["git", "-C", str(repo), "init", "-q"],
            check=True,
            capture_output=True,
        )
        # Bogus sha — helper must log + return, not raise.
        _reset_worktree_to_base(repo, "deadbeef" * 5)


__all__: list[str] = []
