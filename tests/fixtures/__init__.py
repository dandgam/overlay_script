"""Mock projects + stories для разработки orchestrator-а без реального target.

См. spec §11 и §22 S1 acceptance: tests/fixtures/ с mock projects + mock stories.
"""

from pathlib import Path

FIXTURES_ROOT: Path = Path(__file__).parent
MOCK_ODYSSEY: Path = FIXTURES_ROOT / "mock-odyssey"
MOCK_ARTIFACTS: Path = MOCK_ODYSSEY / "_bmad-output" / "planning-artifacts"
MOCK_STORIES: Path = MOCK_ARTIFACTS / "stories"
MOCK_RUNS: Path = MOCK_ODYSSEY / "_bmad-output" / "runs"

__all__ = [
    "FIXTURES_ROOT",
    "MOCK_ARTIFACTS",
    "MOCK_ODYSSEY",
    "MOCK_RUNS",
    "MOCK_STORIES",
]
