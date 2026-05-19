"""NEW-28 — the DAG must parse prose dependency declarations.

Real BMad story files declare dependencies in prose headers
(``**Зависит от:** 1.3``), not the canonical ``- **depends_on:** []`` machine
field. ``parse_story_md`` understood only the machine field, so the DagPlanner
saw no edges and parallelized dependent stories — pilot wave 2a ran 1.3 ‖ 1.4
even though 1.4 consumes ``core/db.py`` created by 1.3.
"""

from __future__ import annotations

from bmad_orchestrator.agent.tools._common import parse_story_md
from bmad_orchestrator.runtime.dag_planner import build_graph

# ── parse_story_md — prose extraction ───────────────────────────────────────


def test_prose_depends_on_is_extracted() -> None:
    md = (
        "# Story 1.4: Alembic\n\n"
        "**Эпик:** 1\n"
        "**Зависит от:** 1.1 (skeleton — DONE), 1.2 (compose), 1.3 (core/db.py)\n"
    )
    assert parse_story_md(md)["depends_on"] == ["1.1", "1.2", "1.3"]


def test_parenthetical_version_numbers_are_not_mistaken_for_ids() -> None:
    """Digits inside parens (``alembic>=1.14``) must not become story ids."""
    md = "# Story 1.4\n\n**Зависит от:** 1.1 (needs `alembic>=1.14` in pyproject)\n"
    assert parse_story_md(md)["depends_on"] == ["1.1"]


def test_prose_blocks_is_extracted() -> None:
    md = "# Story 1.3\n\n**Блокирует:** 1.4 (Alembic), 1.5 (CI)\n"
    assert parse_story_md(md)["blocks"] == ["1.4", "1.5"]


def test_english_prose_headers_work() -> None:
    md = "# Story 1.4\n\n**Depends on:** 1.3\n**Blocks:** 1.5\n"
    meta = parse_story_md(md)
    assert meta["depends_on"] == ["1.3"]
    assert meta["blocks"] == ["1.5"]


def test_canonical_machine_field_still_wins() -> None:
    """An explicit ``- **depends_on:**`` field must not be clobbered by prose."""
    md = (
        "# Story 1.4\n\n"
        "- **depends_on:**\n"
        "  - 1.3\n"
        "**Зависит от:** 9.9 (stale prose)\n"
    )
    assert parse_story_md(md)["depends_on"] == ["1.3"]


# ── build_graph — edge construction + id resolution ─────────────────────────


def test_dag_edge_from_prose_dependency() -> None:
    """The NEW-28 regression: 1.4 prose-depends on 1.3 → edge 1.3 → 1.4."""
    stories = [
        {"id": "1.3", "depends_on": []},
        {"id": "1.4", "depends_on": ["1.3"]},
    ]
    g = build_graph(stories)
    assert g.has_edge("1.3", "1.4")
    assert not g.has_edge("1.4", "1.3")


def test_prose_id_resolves_across_id_conventions() -> None:
    """Prose ``2.1`` must resolve to a kebab node id ``2-1-authentik-idp``."""
    stories = [
        {"id": "2-1-authentik-idp", "depends_on": []},
        {"id": "2-5-sso", "depends_on": ["2.1"]},
    ]
    g = build_graph(stories)
    assert g.has_edge("2-1-authentik-idp", "2-5-sso")


def test_blocks_field_adds_reverse_edge() -> None:
    """A ``blocks`` entry adds ``self → blocked``."""
    stories = [
        {"id": "1.3", "blocks": ["1.4"]},
        {"id": "1.4", "depends_on": []},
    ]
    g = build_graph(stories)
    assert g.has_edge("1.3", "1.4")


def test_stray_unresolvable_token_is_dropped() -> None:
    """A dependency id matching no node must not raise — just no edge."""
    stories = [{"id": "1.4", "depends_on": ["9.9", "1.3"]}, {"id": "1.3"}]
    g = build_graph(stories)
    assert g.has_edge("1.3", "1.4")
    assert "9.9" not in g
