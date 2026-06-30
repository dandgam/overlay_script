"""conftest_storm.py — Storm module path setup.

Handles:
1. Adding ~/.claude/skills/888/storm to sys.path (for underscore modules)
2. Loading dashed-name modules (intent-classifier.py, storm-orchestrator.py, etc.)
   via importlib and registering them in sys.modules with underscore names.
"""

import importlib.util
import sys
from pathlib import Path

STORM_DIR = Path.home() / ".claude" / "skills" / "888" / "storm"

# Add storm dir to sys.path if not already present
if str(STORM_DIR) not in sys.path:
    sys.path.insert(0, str(STORM_DIR))


def _load_dashed(filename: str, module_name: str) -> None:
    """Load a dashed-name .py file and register it in sys.modules."""
    if module_name in sys.modules:
        return
    path = STORM_DIR / filename
    if not path.exists():
        return
    spec = importlib.util.spec_from_file_location(module_name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)


# Pre-register all dashed modules under their underscore aliases
_load_dashed("intent-classifier.py", "intent_classifier")
_load_dashed("storm-orchestrator.py", "storm_orchestrator")
_load_dashed("taxonomy-checker.py", "taxonomy_checker")
_load_dashed("audit-trail.py", "audit_trail")
