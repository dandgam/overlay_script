"""Runtime DAG façade (networkx 3.x) — wraps tool implementations for direct
non-LLM use (CLI, watchdog, tests).

Tools в `agent.tools.dag` обёрнуты в @tool decorator → их прямой вызов из
non-async путей неудобен. Этот модуль выдаёт чистые async функции без
MCP-обёртки + один class `DagPlanner` который держит cached state per session.

Mutex policy: story is «ready» если:
1. Все `depends_on` story IDs в Completed.
2. `touches_files` И `touches_shared` не пересекаются с union(`touches_files`)
   текущих in-flight stories.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import networkx as nx

from bmad_orchestrator.agent.tools._common import (
    list_stories,
    read_sprint_status_yaml,
)


def build_graph(stories: list[dict[str, Any]]) -> nx.DiGraph:
    """Build DAG over stories. Nodes — story dicts, edges — `depends_on`."""
    g: nx.DiGraph = nx.DiGraph()
    for story in stories:
        g.add_node(story["id"], **story)
    for story in stories:
        for dep in story.get("depends_on") or []:
            if dep in g:
                g.add_edge(dep, story["id"])
    if not nx.is_directed_acyclic_graph(g):
        cycles = list(nx.simple_cycles(g))
        raise ValueError(f"cycle in DAG: {cycles!r}")
    return g


def filter_wave(
    stories: list[dict[str, Any]], sprint_status: dict[str, Any]
) -> list[dict[str, Any]]:
    """Restrict to stories enumerated in sprint-status wave block."""
    enumerated: set[str] = set()
    for epic in (sprint_status.get("epics") or {}).values():
        for sid in (epic.get("stories") or {}).keys():
            enumerated.add(str(sid))
    if not enumerated:
        return stories
    return [s for s in stories if s["id"] in enumerated]


def _status_by_id(sprint_status: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for epic in (sprint_status.get("epics") or {}).values():
        for sid, st in (epic.get("stories") or {}).items():
            out[str(sid)] = str(st)
    return out


def ready_stories(
    g: nx.DiGraph, sprint_status: dict[str, Any], active_touches: set[str] | None = None
) -> list[dict[str, Any]]:
    """Topological roots — deps done + status ready-for-dev/backlog + no mutex clash."""
    status = _status_by_id(sprint_status)
    active = active_touches or set()
    out: list[dict[str, Any]] = []
    for nid in g.nodes:
        deps = list(g.predecessors(nid))
        deps_done = all(status.get(d) == "done" for d in deps)
        st = status.get(nid, "backlog")
        if not (deps_done and st in ("ready-for-dev", "backlog")):
            continue
        node = dict(g.nodes[nid])
        files = set(node.get("touches_files") or [])
        shared = set(node.get("touches_shared") or [])
        if (files & active) or (shared & active):
            continue
        out.append(node)
    return out


def detect_conflicts(
    story_ids: list[str], stories: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    by_id = {s["id"]: s for s in stories}
    conflicts: list[dict[str, Any]] = []
    for i, a in enumerate(story_ids):
        for b in story_ids[i + 1:]:
            if a not in by_id or b not in by_id:
                continue
            files_a = set(by_id[a].get("touches_files") or [])
            files_b = set(by_id[b].get("touches_files") or [])
            shared_a = set(by_id[a].get("touches_shared") or [])
            shared_b = set(by_id[b].get("touches_shared") or [])
            overlap = (files_a & files_b) | (shared_a & shared_b)
            if overlap:
                conflicts.append(
                    {
                        "story_a": a,
                        "story_b": b,
                        "files": sorted(overlap),
                        "severity": "high" if shared_a & shared_b else "medium",
                    }
                )
    return conflicts


@dataclass(slots=True)
class DagPlanner:
    """Caches stories + DAG; tracks in-flight worker touches for mutex."""

    stories: list[dict[str, Any]] = field(default_factory=list)
    sprint_status: dict[str, Any] = field(default_factory=dict)
    graph: nx.DiGraph = field(default_factory=nx.DiGraph)
    in_flight_touches: set[str] = field(default_factory=set)

    @classmethod
    def from_target(cls) -> DagPlanner:
        sprint = read_sprint_status_yaml()
        raw = list_stories()
        wave_stories = filter_wave(raw, sprint)
        graph = build_graph(wave_stories)
        return cls(stories=wave_stories, sprint_status=sprint, graph=graph)

    def reload(self) -> None:
        self.sprint_status = read_sprint_status_yaml()
        raw = list_stories()
        self.stories = filter_wave(raw, self.sprint_status)
        self.graph = build_graph(self.stories)

    def find_ready(self, max_n: int = 3) -> list[dict[str, Any]]:
        cands = ready_stories(self.graph, self.sprint_status, self.in_flight_touches)
        return cands[:max_n]

    def reserve(self, story: dict[str, Any]) -> None:
        for f in story.get("touches_files") or []:
            self.in_flight_touches.add(str(f))
        for f in story.get("touches_shared") or []:
            self.in_flight_touches.add(str(f))

    def release(self, story: dict[str, Any]) -> None:
        for f in story.get("touches_files") or []:
            self.in_flight_touches.discard(str(f))
        for f in story.get("touches_shared") or []:
            self.in_flight_touches.discard(str(f))


__all__ = [
    "DagPlanner",
    "build_graph",
    "detect_conflicts",
    "filter_wave",
    "ready_stories",
]
