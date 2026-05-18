"""Self-learning consolidation loop (Phase 5 — P5 Evaluator-Optimizer).

Public API for the self_learning package.

See spec/spec_self_learning_loop.md.
"""

from bmad_orchestrator.self_learning.audit import log_decision
from bmad_orchestrator.self_learning.config import (
    PolicyNotFoundError,
    PolicyValidationError,
    SelfLearningConfig,
    SelfLearningDefaults,
    SelfLearningError,
    load_config,
)
from bmad_orchestrator.self_learning.extractor import (
    ExtractorProtocol,
    Lesson,
    ProposedPattern,
    StubExtractor,
)

__all__ = [
    "ExtractorProtocol",
    "Lesson",
    "PolicyNotFoundError",
    "PolicyValidationError",
    "ProposedPattern",
    "SelfLearningConfig",
    "SelfLearningDefaults",
    "SelfLearningError",
    "StubExtractor",
    "load_config",
    "log_decision",
]
