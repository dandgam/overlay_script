# 888 Storm Framework — S5 Findings

**Date:** 2026-05-26
**Runner:** S5 end-to-end validation sub-agent
**Spec:** spec/spec_888_storm_framework.md §13
**Audit log:** _storm_audit/S5_run_2026-05-26.md

---

## Q-260526-STRM-1 — code-gate.sh storm-pending check uses wrong field ('scope' vs 'slug')

**Severity:** MAJOR (silent no-op — defeats T1/T2/T3/T5 backfill path B from §16)

**Symptom:**
After injecting a `state.json` initiative with `status: storm-pending`, `slug: s5-override-test`,
`code-gate.sh` invoked with a matching file path returns exit 0 (no block) instead of exit 2.
Override mechanism therefore cannot even be exercised, because the gate never fires in the first place.

**Expected:**
Per spec §5.3 T1/T2/T3/T5 gate — "Если в state.json есть active initiative для этого scope с
status=storm-pending — нужен соответствующий artifact. Нет → exit 2."

**Actual:**
exit 0 silently; no event in events.jsonl; override file written but never consumed.

**Root cause:**
`/tmp/_888_check_pending.py` helper (invoked by code-gate.sh:78) iterates initiatives looking
for `init.get('scope') == scope`. But `storm-orchestrator.py` and the rest of the codebase write
initiatives keyed by `slug` (no `scope` field). The check is structurally guaranteed to miss.

```python
# /tmp/_888_check_pending.py — bug:
if init.get('scope') == scope and init.get('status') == 'storm-pending':
# state.json reality:
{"trigger":"T1","slug":"...","status":"storm-pending","required_artifact":"..."}
```

**Compounding issue:** `scope_from_path.py` for absolute paths under `/home/server/...` always
returns `home` (already noted in S2 findings §1). Even if the field name were fixed, the
heuristic would not align scope-from-path with slug-from-initiative without additional
git-root-relative resolution.

**Suggested fix scope:**
1. `/tmp/_888_check_pending.py` — change `init.get('scope')` → `init.get('slug')`.
   - Better: move helper inline into code-gate.sh or into `~/.claude/skills/888/storm/_check_pending.py`
     (current `/tmp/` placement is fragile — can be reaped between sessions).
2. `scope_from_path.py` — resolve git-root-relative path before slugifying so the produced
   scope can match storm slug naming (matches S2 deferral).
3. Add a regression test that injects a pending state + sends edit → expects exit 2.

**Impact if unfixed:**
- Mode B (code-gate backfill) from §16 is non-functional — only T7 (patch counter) path actually
  blocks in code-gate. T1/T2/T3/T5 storm-pending initiatives sit in state.json with no enforcement.
- §13 acceptance item "code-gate реально блокирует Edit при отсутствии artifact" — PARTIAL (only
  T7 path works; T1-T5 storm-pending path doesn't).

---

## Q-260526-STRM-2 — code-gate.sh does not consult T11 regression_detected

**Severity:** MAJOR (R1→T11→block chain in §19 broken at the block step)

**Symptom:**
After R1 detector emits `regression_detected` (T11) event with `scope: test-inject`, code-gate
invoked against the same scope exits 0 — does not block. T11 storm artifact is not required for
edits.

**Expected:**
Per spec §19 §"Integration with existing hooks" — `code-gate.sh` should "check T11 active for
scope: если есть regression_<hash>_storm.md pending → block until acknowledged".

**Actual:**
`code-gate.sh` only checks: (a) patch-counter ≥5 + missing `spec/taxonomy_<scope>.md`,
(b) `state.json` storm-pending initiative (broken — see Q-260526-STRM-1).
No code path consults `events.jsonl` or `error-fingerprints.jsonl` for active regressions.
T11 storm artifact `spec/regression_<slug>_storm.md` is never required by the gate.

**Root cause:**
S1 hook implementation predates the §19 R1 detector wire-up. The §19 acceptance lists this
integration in S2 ("+ check T11 active for scope") but it was not implemented in `code-gate.sh`.

**Suggested fix scope:**
1. Add to `code-gate.sh` a third check: scan recent (e.g. 7d) `events.jsonl` for
   `regression_detected` events touching this scope; if found AND no corresponding
   `spec/regression_<slug>_storm.md` exists → exit 2 with T11 message.
2. Decide on "acknowledged" semantics — spec says "block until artifact_exists AND
   user_acknowledged" — acknowledgement vehicle is undefined. Default to "artifact exists" only
   for v1; user_acknowledged via the storm-complete state update is implicit.
3. R1 scope is `test-inject` from `inject` CLI path; real `record(scope, stderr)` uses caller's
   scope. Fix `scope_from_path.py` git-root-relative resolution (overlaps Q-260526-STRM-1).

**Impact if unfixed:**
- §13 acceptance item "Real-world cycle ... проходит end-to-end" — for T11, only manual
  inspection of events.jsonl catches regressions; the deterministic enforcement layer is absent.
- The S2 smoke test "T11 FIRED + state in events.jsonl" passes only because it stops short of
  asking code-gate to enforce; the enforcement step was never tested.

---

## Q-260526-STRM-3 — scope_from_path resolves all absolute paths to "home"

**Severity:** MAJOR (cascades into Q-260526-STRM-1 and Q-260526-STRM-2)

**Symptom:**
```
python3 ~/.claude/skills/888/storm/scope_from_path.py /home/server/bmad-orchestrator/agents/foo-bar-baz.py
# → home
python3 ~/.claude/skills/888/storm/scope_from_path.py /home/server/bmad-orchestrator/s5-override-test/x.py
# → home
```
All distinct files under `/home` collapse to scope `home`. Code-gate cannot distinguish scopes.

**Expected:**
Per spec example `runtime/sandbox.py → virgil-sandbox` — git-root-relative + dir+base slug.

**Actual:**
Heuristic takes first path component after `/`. For all `/home/...` paths → `home`.

**Root cause:**
`scope_from_path.py` handles relative paths correctly; absolute paths starting at `/home` map to
the top-level directory `home` instead of relativizing against git root or known project roots.
Already flagged in S2 audit §"Spec Gaps / Open Questions" item 1 and deferred to S4 polishing,
but not picked up.

**Suggested fix scope:**
- Detect git root via `git -C <dirname> rev-parse --show-toplevel` (or fall back to ENV
  `BMAD_PROJECT_ROOT`); relativize file_path against root before slugifying.
- Add unit test: `/home/server/bmad-orchestrator/runtime/sandbox.py` → `virgil-sandbox` (or
  similar deterministic scope).

**Impact if unfixed:**
- All per-scope features (patch counter buckets, code-gate gating, hot-file tracking) effectively
  use scope=`home` for every file in this layout, dramatically lowering signal/noise.

---

## Q-260526-STRM-4 — LLM-judge is a stub (informational, already documented)

**Severity:** MINOR (already documented in S2 audit §"Spec Gaps")

**Symptom:**
Every Scenario A/B/C invocation prints `WARNING: LLM-judge STUB invoked` when static rules don't
match. Stub returns first item from optional_pool (e.g. `[23]` for T2).

**Status:** Known limitation, deferred to follow-up per S2 audit. No action in S5.

---

## Summary

| Q-ID | Severity | Surface | Blocks ship? |
|---|---|---|---|
| STRM-1 | MAJOR | code-gate.sh storm-pending check | partial — only T1/T2/T3/T5 backfill broken; T7 path works |
| STRM-2 | MAJOR | code-gate.sh T11 wiring | partial — regression detection works, enforcement doesn't |
| STRM-3 | MAJOR | scope_from_path.py absolute path heuristic | foundational — cascades into STRM-1/2 |
| STRM-4 | MINOR | LLM-judge stub | no — known deferral |

**Recommendation:** SHIP WITH FOLLOWUPS. The deterministic baseline (intent detector, storm
orchestrator, audit trail, patch counter, merge-guard, destructive-guard, sync_manifest, T11
detection) all work. The gaps are in code-gate's secondary enforcement paths (storm-pending and
T11) and scope resolution. None of these are silent data-loss bugs; they're "enforcement gates
that no-op" gaps. Surface them as Q-260526-STRM-1..3 for a focused S5.5 fix session.
