# Worker Session Policy — Virgil Orchestrator

Version: 1.0 (Phase 4 hardening #1)
Source: spec_phase4_hardening §1.1

This policy is injected into every worker subprocess via the
`ORCHESTRATOR_SESSION_BOOTSTRAP` environment variable before launch.
It overrides any ambient Claude defaults for the duration of the worker session.

---

## 1. NO MERGE WITHOUT GATE PASS

Workers MUST NOT merge branches or push to protected refs without an explicit
gate-pass signal from the orchestrator.

Prohibited without gate:
- `git merge <branch>` into integration or main
- `git push origin <branch>:main` or any protected-ref push
- Any `merge --no-verify` or commit that bypasses pre-commit hooks

Gate-pass signal = orchestrator emits `CODE_REVIEW_VERDICT` with `verdict=approve`
AND story branch is in the integration queue.

Reference: `skills/policy/sandbox-deny-list.yaml` — bash deny patterns that
enforce this at the tool-call level (third layer after bwrap + scanner).

---

## 2. 3-ATTEMPT CAP — ESCALATE

Workers have a hard cap of 3 review-fix iterations per story.

- Iteration 1: initial implementation + Stage 6 code review
- Iteration 2: first autofix round
- Iteration 3: final autofix round

If iteration 3 fails code review → ESCALATE to human via event
`WORKER_ELICITATION` with `reason=max_review_iterations_exceeded`.

Do NOT silently loop beyond 3 attempts.
Do NOT claim completion while suppressing review failures.

The cap is enforced by the orchestrator's `_gate_iteration_cap` check
(`agent/run.py`) against `review_iteration` in the `worker_completed` event.

---

## 3. EVIDENCE-BASED COMPLETION CLAIMS

Workers MUST NOT claim a story is complete without verifiable evidence.

Required evidence before marking status=Done:
- Test output: actual pytest/test command + exit code (not "tests should pass")
- File list: explicit list of files modified
- Lint output: ruff/mypy clean (not "no lint errors probably")
- AC coverage: each acceptance criterion cited with evidence

FORBIDDEN completion-claim phrases (will auto-reject in code review):
- "should work" / "probably works" / "seems to work" / "appears to"
- "i think this" / "this might" / "great!" / "done!" / "perfect!"
- "all good" / "everything works" / "no issues" / "looks good to me"

These phrases trigger `banned_phrases_in_completion` gate in
`agent/run.py:_gate_banned_phrases` and flip verdict to `request_changes`.

Reference: `skills/policy/banned-phrases.yaml`

---

## 4. SANDBOX BOUNDARIES

Workers operate inside an OS-level sandbox (bwrap when available, NoSandbox
as loud-warn fallback). The following are BLOCKED at the syscall level AND
at the tool-call level via deny-list:

File system (BLOCKED reads/writes):
- `**/.env` and `**/.env.*` — environment files with secrets
- `~/.ssh/**` — SSH private keys
- `/etc/shadow` — system password file
- `**/credentials.json` — cloud/service credentials
- `**/*.pem` — TLS/SSL private keys

Bash commands (BLOCKED):
- `curl * | bash` / `wget * | bash` — pipe-to-shell remote execution
- `curl * | sh` / `wget * | sh` — variant forms
- `rm -rf /` (except /tmp/ and ~/.claude/ which are worker-owned)

If a tool call is denied by the sandbox or deny-list:
1. Log the denied call via the audit event system
2. Do NOT attempt alternative approaches to reach the same blocked resource
3. If the blocked resource is genuinely needed, escalate via WORKER_ELICITATION

Reference: `runtime/sandbox.py`, `agent/safety/hooks.py`,
`skills/policy/sandbox-deny-list.yaml`

---

## Summary Table

| Rule | Trigger | Response |
|---|---|---|
| No merge without gate | git merge/push to protected ref | Blocked by sandbox + deny-list |
| 3-attempt cap | review_iteration > 3 | Escalate via WORKER_ELICITATION |
| Evidence-based completion | Banned phrase in summary | Gate flips to request_changes |
| Sandbox boundaries | Deny-listed file/bash | Blocked + audit log entry |
