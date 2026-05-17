# Checkpoint — dag_planner_bmad_compat / B1

**Session:** B1 — BMad-format sprint-status parser
**Completed:** 2026-05-17 18:50 UTC
**Commit:** `bd6ab2a` on `integration/dag_planner_bmad_compat`
**Branch state:** integration only (Auto merge=false — no main merge)

## Diff stats
```
 src/bmad_orchestrator/runtime/bmad_format.py | 266 +++++++++++++++++++++++++++
 tests/test_b1_bmad_format.py                 | 249 +++++++++++++++++++++++++
 2 files changed, 515 insertions(+)
```

## Quality gates
- pytest tests/ — **788 PASS** (760 baseline + 28 new; spec floor 15)
- ruff check — clean
- mypy --strict src/bmad_orchestrator/runtime/bmad_format.py — clean

## Public surface
- `parse_sprint_status_bmad(yaml_data) → {"epics": {epic_id: {"status", "stories"}}}`
- `normalize_story_id(raw) → "X.Y[a-z]*"` (kebab + dotted + prefixed)
- `extract_status_token(value) → str` (strips `#` comments + multi-token tails)
- `KNOWN_STATUSES` frozenset for downstream gating

## Key decisions
- Probe order: `epics:` (legacy) → `development_status:` (BMad upstream) → bare flat with `epic-N`/`N-M` keys → empty
- Unknown statuses downgraded to `backlog` + WARNING via `logging.getLogger(__name__)` (matches `sandbox.py` convention; no new dep)
- Retrospective entries silently dropped (don't drive readiness)
- Stories without prior `epic-N` entry create epic with default `backlog` status — safe for downstream iteration

## Deferred to B2
- Wire `DagPlanner._load()` / `from_target()` to consume `parse_sprint_status_bmad` output
- Story title via `parse_story_md` from `_bmad/stories/X.Y.md` frontmatter
- Real Odyssey integration test (must return epic-3 stories, not epic-1)
- Backward compat with legacy fixture-based tests

## Next session
**B2** — Wire DagPlanner + integration tests + Odyssey golden fixture. Final session; manual merge per `Auto merge: false`.
