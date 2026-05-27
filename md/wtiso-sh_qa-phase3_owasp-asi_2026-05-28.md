# Q-260527-WTISO-SH — Phase 3 QA: OWASP ASI light mapping (2026-05-28)

**Q-ID:** Q-260527-WTISO-SH
**Phase:** 3 (Test) — light scope, `security_critical: false`
**Spec:** `spec/spec_wtiso-sh.md` v2.1-block-fix §9 (mini threat model T1-T4)
**Implementation:** `scripts/888-shard-merger.sh` (~530 LOC) + 16 RED→GREEN tests
**Persona:** 888-persona-qa (handoff from implementer §4gzf Stage 3 closure)

## 1. Baseline regression verification

```
for f in tests/wtiso/test-shard-*.sh; do bash "$f" …; done
```

Result: **16/16 PASS** — full RED→GREEN matrix intact. No regression introduced
since implementer Stage 3 closure commit `3130e4c`.

## 2. Perf budget verification

Fixture: 4-shard mock-mode batch via `tests/wtiso/_lib/fixture-helpers.sh`,
`BATCH_MOCK_MODE=1 BMAD_SHARD_L1_MOCK=PASS`.

| Run | Wall-clock |
|---|---|
| cold (1st) | 369 ms |
| warm 1 | 197 ms |
| warm 2 | 161 ms |
| warm 3 | 153 ms |
| warm 4 | 98 ms |
| warm 5 | 117 ms |

**Verdict:** mock-mode target per spec §10 is ~200 ms (4-shard). Warm median
~150 ms — **under target**. Cold-cache outlier 369 ms is one-shot price of
first-invocation `bash` startup + tool resolution; subsequent runs cache.

Real-mode (L1 LLM gate enabled, target ~25 s) **not exercised** — cost-
prohibitive per Phase 3 light scope. Parked as Q-260528-WTISO-SH-QA-REAL-BATCH.

## 3. OWASP ASI mapping for §9 threat vectors

Light mapping — one paragraph per vector covering test coverage, residual
risk, and OWASP ASI category. Full STRIDE / fuzz suite out of scope for
solo-operator non-security-critical batch.

### T1 — Shard injection

Worker writes adversarial content into its shard: HTML/markdown control
strings (`</methodology>`), unicode tricks (RTL override, zero-width),
fenced-block escape attempts, or injection of fake `## 4XXX.` headings.

**Existing coverage:**
- `RED-SH-9` (fenced-block) — in-fence `## 4XXX.` heading is **not** counted
  by L3 state machine and **not** rewritten by placeholder sed; round-trip
  preserves the example byte-for-byte.
- `RED-SH-10` (shell-injection-batch-id) — `batch-id="; rm -rf /tmp"`
  rejected by P2 regex, RC=10, zero filesystem mutation.
- `RED-SH-4` (append-only) — synthetic shard mutating prior methodology
  content rejected by L3, batch RC=14.

**Residual risk:** non-ASCII section-body content is permitted (spec §11.3,
locale `LC_ALL=C` only at merger entry, not on shard body). A worker
producing valid UTF-8 with RTL override bytes is appended verbatim. For
solo-operator workload this is acceptable — operator sees the rendered
methodology and would notice glyph anomalies. Multi-tenant deployment
would need an explicit allow-list filter.

**OWASP ASI category:** **ASI-05 Improper Output Handling** (raw markdown
append without re-parse — merger trusts shard bytes after L3 structural
check). Secondary: **ASI-01 Prompt Injection** — methodology is read by
future 888 dispatcher invocations as part of state; a malicious shard could
plant prompt-injection content into dispatcher's read horizon. Mitigation:
methodology is operator-curated; workers are themselves trusted Claude
processes under same operator. Threat model = insider/buggy worker, not
external attacker.

### T2 — Merge-time TOCTOU

`audit/shards/<batch-id>/` modified between L1/L2/L3 gate-time and the
atomic `mv` step.

**Existing coverage:**
- Merger §3.4 step 11.1 + 11.6 — dual-hash check: `shard_sha` computed
  before gates, re-checked after gates pass and before append. Mismatch ⇒
  abort batch, audit event `shard_toctou_mismatch`, RC=15.
- `RED-SH-16` (content-aware-hash) — receipt's `shards_sha256` is content-
  aware (path + sha256(body) per shard, NUL-delimited), so cross-invocation
  mutation cannot masquerade as idempotent noop.

**Residual risk:** the TOCTOU window between `cp --reflink=auto` (step 10)
and the per-shard sha re-check (step 11.6) is on the order of milliseconds
per shard. An attacker would need write access to `audit/shards/<batch-id>/`
during merger execution — same trust boundary as the worker itself.
Practical risk = zero on single-operator workstation.

**OWASP ASI category:** **ASI-06 Excessive Agency** (worker has filesystem
write access; merger downstream trusts that the worker doesn't race-mutate
its own output after L3 verdict). Mitigation = dual-hash gate is correctly
positioned at the choke point (post-L3, pre-append).

### T3 — Orphan cleanup

`audit/shards/<batch-id>/` directories left after failed/crashed batches
accumulate and eventually consume disk.

**Existing coverage:**
- `scripts/888-shard-cleanup.sh --retention 7d` (NEW per spec §7) invoked
  pre-batch by `888-batch.sh`. Retention default 7-30 days, aligned with
  `audit/batches/`.
- `RED-SH-3` (partial-rollback) — on L3 violation, `audit/shards/<batch-id>/`
  is **retained** for forensics (intentional, not orphan), but tagged via
  audit event for later cleanup-policy decision.

**Residual risk:** no automatic disk-quota enforcement. If operator never
runs `888-batch.sh` for >7d, no GC occurs (cleanup is opportunistic, not
cron-driven). Solo-operator usage pattern makes this acceptable.

**OWASP ASI category:** **ASI-10 Unbounded Consumption** (resource
exhaustion via accumulated forensic state). Mitigation = retention window
+ pre-batch GC. No DoS amplification — orphans are bounded by batch
cardinality, not per-request.

### T4 — Receipt forgery / gate-bypass env

Adversarial actor (or buggy worker) writes a fake `.merge-receipt.json` or
sets `SHARD_GATE_SKIP_L1=1` outside mock-mode to bypass code review.

**Existing coverage:**
- Merger §3.1 step 3 — `jq -e .` validate before trusting receipt;
  malformed JSON ⇒ RC=11.
- Merger §3.1 step 3 — receipt `shards_sha256` re-computed and matched
  against current shard set; mismatch ⇒ RC=3 (`receipt_conflict`).
- ADR-008 — `SHARD_GATE_SKIP_L1` is **derived from** `BATCH_MOCK_MODE=1`,
  not honoured as an independent toggle. The merger reads `BATCH_MOCK_MODE`
  first and sets gate-skip internally; an env-injected `SHARD_GATE_SKIP_L1`
  without `BATCH_MOCK_MODE` has no effect.
- `RED-SH-2` (idempotent-rerun) — second invocation on same batch-id is
  byte-identical noop, audit `shard_merger_skipped_idempotent`.

**Residual risk:** receipt files live in the same filesystem as the
methodology; an operator with write access to `audit/shards/<batch-id>/`
can forge a receipt. This is the same trust boundary as the methodology
itself — operator-write is operator-owned state. No cross-boundary
elevation.

**OWASP ASI category:** **ASI-07 System Prompt Leakage** — adjacent risk
(receipt is operator-readable, contains hashes but no secrets). Secondary:
**ASI-06 Excessive Agency** (gate-skip env must be tightly coupled to mock-
mode declaration). Mitigation = derivation-not-toggle pattern + jq
validation.

## 4. Coverage summary table

| Vector | RED tests | ASI primary | ASI secondary | Residual risk |
|---|---|---|---|---|
| T1 Shard injection | SH-4, SH-9, SH-10 | ASI-05 | ASI-01 | non-ASCII RTL bytes (operator-visible) |
| T2 Merge TOCTOU | SH-16 + merger §3.4 dual-hash | ASI-06 | — | sub-ms window, same trust boundary |
| T3 Orphan cleanup | cleanup.sh + SH-3 retention | ASI-10 | — | no auto disk-quota |
| T4 Receipt forgery | SH-2, merger §3.1 jq validate | ASI-07 | ASI-06 | operator-write = operator-trust |

## 5. Parked for follow-up

- **Q-260528-WTISO-SH-QA-REAL-BATCH** — real-data validation via production
  `888-batch.sh` invocation on a real Q-NNN batch with L1 LLM gate enabled.
  Effort: ~30 min wall-clock + Sonnet token budget for L1 reviews.
  `security_critical: false`. Dep: `Q-260527-WTISO-SH` (this Q) complete.
  Trigger: when next real 4-shard parallel batch is scheduled.

- **Q-260528-WTISO-SH-NONASCII-FILTER** (optional, deferred) — multi-tenant
  hardening for T1: explicit unicode allow-list filter on shard body for
  control characters (RTL override, zero-width). Solo-operator workload
  does not need this; revisit if methodology is ever exposed cross-tenant.

## 6. Verdict

**Phase 3 light QA: PASS.**

- Baseline regression: 16/16 GREEN.
- Perf budget: ~150 ms warm median (target 200 ms mock) — under budget.
- OWASP ASI mapping: 4/4 vectors documented with test cross-references and
  residual-risk statements.
- No qa-discovered bugs in merger that 16 RED tests miss.

Handoff to **888-persona-ops** Phase 4 (Deploy): feature flag wiring,
gradual ramp from sequential → parallel batches, rollback plan.

**Merge-blocking criteria (for Phase 4):**
1. Any future regression in the 16-test RED→GREEN baseline.
2. Mock-mode perf > 500 ms (2.5× current warm median).
3. Receipt JSON validation failure on any production batch.
