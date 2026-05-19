"""NEW-12 (pilot_findings_closure_v5 S1) — pre-commit config missing.

Spec: spec/spec_pilot_findings_closure_v5.md §2.

A target BMad project is not required to ship ``.pre-commit-config.yaml``.
When it does not, a worktree that still has a pre-commit git hook installed
fails the worker's ``git commit`` ("No .pre-commit-config.yaml file"). The
fix injects ``PRE_COMMIT_ALLOW_NO_CONFIG=1`` into every worker subprocess
env, so pre-commit exits 0 on the no-config path. A pre-spawn detector logs
``precommit_config_absent`` for observability.

Coverage (5 tests):
  * 3 unit — env injection constant + ``_build_worker_env`` carries the flag;
             ``_detect_precommit_config`` detection; caller ``extra`` still
             applied alongside (and may override) the injected flag.
  * 2 integration — real ``git commit`` through a pre-commit hook: passes
             with the worker env on a config-less worktree (and would fail
             without the flag); passes on a worktree WITH a config (flag
             harmless).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bmad_orchestrator.runtime.worker_spawn import (
    WORKER_ENV_INJECTED,
    _build_worker_env,
    _detect_precommit_config,
)

# ─── unit ─────────────────────────────────────────────────────────────────────


def test_worker_env_injects_precommit_allow_no_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    assert WORKER_ENV_INJECTED.get("PRE_COMMIT_ALLOW_NO_CONFIG") == "1"
    env = _build_worker_env(extra=None)
    assert env["PRE_COMMIT_ALLOW_NO_CONFIG"] == "1"


def test_detect_precommit_config(tmp_path: Path) -> None:
    assert not _detect_precommit_config(tmp_path)
    (tmp_path / ".pre-commit-config.yaml").write_text("repos: []\n", encoding="utf-8")
    assert _detect_precommit_config(tmp_path)


def test_caller_extra_applied_alongside_injected_flag() -> None:
    env = _build_worker_env(extra={"ORCHESTRATOR_WORKER_STORY_ID": "s9"})
    assert env["PRE_COMMIT_ALLOW_NO_CONFIG"] == "1"
    assert env["ORCHESTRATOR_WORKER_STORY_ID"] == "s9"
    # extra is applied last → a caller may still override the injected default.
    overridden = _build_worker_env(extra={"PRE_COMMIT_ALLOW_NO_CONFIG": "0"})
    assert overridden["PRE_COMMIT_ALLOW_NO_CONFIG"] == "0"


# ─── integration — git commit through a pre-commit hook ───────────────────────

_HOOK = """#!/bin/sh
if [ -z "$PRE_COMMIT_ALLOW_NO_CONFIG" ] && [ ! -f .pre-commit-config.yaml ]; then
  echo "No .pre-commit-config.yaml file was found"
  exit 1
fi
exit 0
"""


def _init_repo_with_precommit_hook(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    hook = root / ".git" / "hooks" / "pre-commit"
    hook.write_text(_HOOK, encoding="utf-8")
    hook.chmod(0o755)
    return root


def _commit(root: Path, env: dict[str, str], message: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "git", "-c", "user.email=t@t", "-c", "user.name=t",
            "commit", "-m", message,
        ],
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
    )


def test_commit_passes_without_precommit_config(tmp_path: Path) -> None:
    """Worktree with a pre-commit hook but no config: commit fails with a
    plain env, passes with the worker env (the injected flag is the fix)."""
    repo = _init_repo_with_precommit_hook(tmp_path / "repo")
    (repo / "f.txt").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "f.txt"], check=True)

    base = {"PATH": "/usr/bin:/bin"}
    plain = _commit(repo, base, "no flag")
    assert plain.returncode != 0
    assert "No .pre-commit-config.yaml" in (plain.stdout + plain.stderr)

    worker_env = _build_worker_env(extra=None)
    worker_env.setdefault("PATH", "/usr/bin:/bin")
    ok = _commit(repo, worker_env, "with worker env")
    assert ok.returncode == 0, ok.stdout + ok.stderr


def test_commit_passes_with_precommit_config(tmp_path: Path) -> None:
    """Worktree WITH a ``.pre-commit-config.yaml``: the injected flag is
    harmless — the hook runs its normal path and the commit passes."""
    repo = _init_repo_with_precommit_hook(tmp_path / "repo")
    (repo / ".pre-commit-config.yaml").write_text("repos: []\n", encoding="utf-8")
    (repo / "f.txt").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)

    worker_env = _build_worker_env(extra=None)
    worker_env.setdefault("PATH", "/usr/bin:/bin")
    ok = _commit(repo, worker_env, "config present")
    assert ok.returncode == 0, ok.stdout + ok.stderr
