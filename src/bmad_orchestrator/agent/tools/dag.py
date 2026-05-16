"""DAG tools (spec §17 — 3 tools).

build_dag, find_ready_stories, predict_conflicts.

Phase 2 MVP heuristic-based DAG built from BMad story metadata:

- `depends_on` produces topological edges (parent → child).
- `touches_files` overlap between two stories yields a shared-files mutex —
  ready-set tightening prevents merge conflicts (spec §6.2 + §21).
- networkx 3.x is used for graph ops; the graph is recomputed on each call
  (cheap for typical wave size ≤ 20 stories).
"""

from __future__ import annotations

from typing import Any

import networkx as nx
from claude_agent_sdk import tool

from bmad_orchestrator.agent.tools._common import (
    error,
    json_ok,
    list_stories,
    read_sprint_status_yaml,
)


def _filter_wave(stories: list[dict[str, Any]], wave_status: dict[str, Any]) -> list[dict[str, Any]]:
    """Restrict to stories enumerated in sprint-status wave block.

    sprint-status.yaml shape (BMad-canonical):
        epics:
          "1":
            stories:
              "1-1-tenant-signup": ready-for-dev
              ...
    """
    enumerated: set[str] = set()
    for epic in (wave_status.get("epics") or {}).values():
        for sid in (epic.get("stories") or {}).keys():
            enumerated.add(str(sid))
    if not enumerated:
        return stories
    return [s for s in stories if s["id"] in enumerated]


def _build_graph(stories: list[dict[str, Any]]) -> nx.DiGraph:
    g: nx.DiGraph = nx.DiGraph()
    for story in stories:
        g.add_node(story["id"], **story)
    for story in stories:
        for dep in story.get("depends_on") or []:
            if dep in g:
                g.add_edge(dep, story["id"])
    return g


def _serialize_dag(g: nx.DiGraph) -> dict[str, Any]:
    return {
        "nodes": [
            {"id": nid, "depends_on": list(g.predecessors(nid))}
            for nid in g.nodes
        ],
        "edges": [{"from": u, "to": v} for u, v in g.edges],
        "node_count": g.number_of_nodes(),
        "edge_count": g.number_of_edges(),
    }


@tool(
    "build_dag",
    "Build DAG of stories for a wave from sprint-status + story frontmatter.",
    {"wave": str},
)
async def build_dag(args: dict[str, Any]) -> dict[str, Any]:
    wave = str(args.get("wave", ""))
    stories = list_stories()
    sprint = read_sprint_status_yaml()
    if sprint.get("wave") and wave and sprint["wave"] != wave:
        return error(f"sprint-status wave={sprint.get('wave')!r} != requested {wave!r}", code="wave_mismatch")
    wave_stories = _filter_wave(stories, sprint)
    g = _build_graph(wave_stories)
    if not nx.is_directed_acyclic_graph(g):
        cycles = list(nx.simple_cycles(g))
        return error(f"cycle detected: {cycles!r}", code="cycle")
    return json_ok({"wave": wave or sprint.get("wave"), "dag": _serialize_dag(g)})


def _ready_set(g: nx.DiGraph, sprint: dict[str, Any]) -> list[dict[str, Any]]:
    """Topological roots with no in-progress deps + status ready-for-dev."""
    status_by_id: dict[str, str] = {}
    for epic in (sprint.get("epics") or {}).values():
        for sid, st in (epic.get("stories") or {}).items():
            status_by_id[str(sid)] = str(st)

    ready: list[dict[str, Any]] = []
    for nid in g.nodes:
        deps = list(g.predecessors(nid))
        deps_done = all(status_by_id.get(d) == "done" for d in deps)
        status_ok = status_by_id.get(nid, "backlog") in ("ready-for-dev", "backlog")
        if deps_done and status_ok:
            ready.append({**g.nodes[nid]})
    return ready


def _apply_shared_mutex(
    candidates: list[dict[str, Any]],
    active_touches: set[str],
) -> list[dict[str, Any]]:
    """Filter out candidates that touch a file already claimed by an active worker.

    `active_touches` is the union of `touches_files` across all in-flight stories.
    """
    out: list[dict[str, Any]] = []
    for c in candidates:
        files = set(c.get("touches_files") or [])
        shared = set(c.get("touches_shared") or [])
        if files & active_touches or shared & active_touches:
            continue
        out.append(c)
    return out


@tool(
    "find_ready_stories",
    "Find next-N stories ready to spawn (deps satisfied + no shared-file mutex with active workers).",
    {"max_n": int},
)
async def find_ready_stories(args: dict[str, Any]) -> dict[str, Any]:
    max_n = max(1, int(args.get("max_n", 3)))
    active_files = args.get("active_files") or []
    active_set: set[str] = {str(p) for p in active_files}

    sprint = read_sprint_status_yaml()
    stories = _filter_wave(list_stories(), sprint)
    g = _build_graph(stories)
    if not nx.is_directed_acyclic_graph(g):
        return error("cycle in DAG — fix depends_on", code="cycle")

    candidates = _ready_set(g, sprint)
    candidates = _apply_shared_mutex(candidates, active_set)
    candidates = candidates[:max_n]
    return json_ok({"ready": candidates, "count": len(candidates)})


@tool(
    "predict_conflicts",
    "Predict potential merge conflicts among given story IDs by overlapping touches_files.",
    {"story_ids": list[str]},
)
async def predict_conflicts(args: dict[str, Any]) -> dict[str, Any]:
    raw_ids = args.get("story_ids", [])
    if not isinstance(raw_ids, list):
        return error("'story_ids' must be a list", code="invalid_arg")
    ids: list[str] = [str(x) for x in raw_ids]

    by_id = {s["id"]: s for s in list_stories()}
    missing = [sid for sid in ids if sid not in by_id]
    if missing:
        return error(f"unknown story ids: {missing!r}", code="unknown_story")

    conflicts: list[dict[str, Any]] = []
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
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
                        "severity": "high" if (shared_a & shared_b) else "medium",
                    }
                )
    return json_ok({"conflicts": conflicts, "count": len(conflicts)})


TOOLS = [build_dag, find_ready_stories, predict_conflicts]


__all__ = ["TOOLS", "build_dag", "find_ready_stories", "predict_conflicts"]
