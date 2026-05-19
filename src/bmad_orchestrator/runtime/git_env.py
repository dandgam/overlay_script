"""Shared subprocess env for orchestrator-spawned ``git commit`` invocations.

NEW-12 (pilot_findings_closure_v5) injected ``PRE_COMMIT_ALLOW_NO_CONFIG=1``
into the *worker* subprocess env so a config-less target repo could not break
the worker's own ``git commit``. NEW-14 closes the regression: the stage 5
completeness and pre-merge commit-recovery paths spawn their own ``git commit``
subprocesses straight from the orchestrator process, which inherits a plain
``os.environ`` that does NOT carry the flag. In a worktree that has a
pre-commit git hook installed but ships no ``.pre-commit-config.yaml``
(project-agnostic target repos), that hook aborts ``git commit`` with
``No .pre-commit-config.yaml file was found``.

Injecting the flag here keeps recovery commits working. It is harmless when a
config IS present — pre-commit only consults the variable on the no-config
path. The single :func:`_git_commit_env` helper is the one place the flag is
defined, and is reused by ``worker_spawn``, ``stage5_completeness`` and
``commit_recovery`` so the logic is never duplicated.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

# env vars the orchestrator INJECTS into every ``git commit`` subprocess it
# spawns directly (constants, not passthrough from os.environ).
GIT_COMMIT_ENV_INJECTED: dict[str, str] = {
    "PRE_COMMIT_ALLOW_NO_CONFIG": "1",
}


def _git_commit_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return the env mapping for an orchestrator-spawned ``git commit``.

    ``base`` defaults to the current ``os.environ``. The fixed
    :data:`GIT_COMMIT_ENV_INJECTED` constants are applied on top so a
    config-less worktree's pre-commit git hook cannot abort the commit
    (NEW-14). Callers pass the result as ``env=`` to
    ``asyncio.create_subprocess_exec``.
    """
    env: dict[str, str] = dict(os.environ if base is None else base)
    env.update(GIT_COMMIT_ENV_INJECTED)
    return env


__all__ = ["GIT_COMMIT_ENV_INJECTED", "_git_commit_env"]
