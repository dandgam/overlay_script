"""3-layer safety (spec §9).

Layer 1 — PreToolUse hooks: deny dangerous Bash combos и path-escape Edit/Write.
Layer 2 — Deterministic interceptors: budget alarm/halt, liveness-before-kill.
Layer 3 — Branch isolation: worker'ы пишут только в свой worktree, никогда в main.

Все три слоя логируют события через `record_audit` → `audit.events.jsonl`.
"""

from bmad_orchestrator.agent.safety.audit import audit_log_path, record_audit
from bmad_orchestrator.agent.safety.branch_isolation import (
    FORBIDDEN_DIRECT_MERGE_TARGETS,
    validate_merge_target,
    validate_worker_write_path,
)
from bmad_orchestrator.agent.safety.budget_guard import (
    BudgetGuard,
    BudgetLevel,
    BudgetResult,
)
from bmad_orchestrator.agent.safety.hooks import (
    audit_tool_output,
    security_check_hook,
)
from bmad_orchestrator.agent.safety.main_merge_token import (
    consume_token,
    generate_token,
    has_active_token,
    revoke_token,
    token_path,
    validate_token,
)

__all__ = [
    "FORBIDDEN_DIRECT_MERGE_TARGETS",
    "BudgetGuard",
    "BudgetLevel",
    "BudgetResult",
    "audit_log_path",
    "audit_tool_output",
    "consume_token",
    "generate_token",
    "has_active_token",
    "record_audit",
    "revoke_token",
    "security_check_hook",
    "token_path",
    "validate_merge_target",
    "validate_token",
    "validate_worker_write_path",
]
