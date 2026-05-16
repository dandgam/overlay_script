# networkx patterns for DAG planner

## Graph construction

```python
import networkx as nx

g = nx.DiGraph()
for story in stories:
    g.add_node(story.id, **story.frontmatter)
for story in stories:
    for dep in story.depends_on:
        g.add_edge(dep, story.id)
```

## Cycle detection (fail-fast)

```python
cycles = list(nx.simple_cycles(g))
if cycles:
    raise PlannerError(f"Cycle in DAG: {cycles!r}")
```

## Topological ready set

```python
ready = [n for n in g.nodes if g.in_degree(n) == 0]
```

`ready` после фильтрации по `status in {ready-for-dev, backlog}` и
shared-files mutex — это `ready_now` ответа planner'а.

## Critical path estimation

```python
weighted = g.copy()
for n in weighted.nodes:
    weighted.nodes[n]["weight"] = weighted.nodes[n].get("estimated_minutes", 30)
longest = nx.dag_longest_path(weighted, weight="weight")
critical_minutes = sum(weighted.nodes[n]["weight"] for n in longest)
```

## Subgraph by wave

```python
wave_nodes = [n for n, d in g.nodes(data=True) if d.get("wave") == target_wave]
g_wave = g.subgraph(wave_nodes).copy()
```
