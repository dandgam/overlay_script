# Gauntlet — 5 elicitation method prompts

> Real prompt templates used by `scripts/gauntlet_injector.py`.
>
> Parser contract: each template lives in a `## Template N — <Name>` section followed
> by exactly one fenced code block (```...```). The code block body IS the prompt.
> Anything outside the code block is documentation.
>
> Placeholders in the body: `{story_id}`, `{story_text}` — substituted at prompt-build time.
> `{word_budget}` is substituted by the injector with `200` (quick) or `300` (deep).

## Template 1 — Failure Mode Analysis

```
You are reviewing BMad story {story_id} below. Walk through each acceptance
criterion (AC) and identify ONE production-realistic failure mode per AC that
the spec text glosses over. Focus on:
- Concurrency: what happens if N users hit this simultaneously?
- Partial failure: step 2 of 3 fails after step 1 committed state.
- Resource exhaustion: full disk, OOM, connection pool drained.
- External dependency degradation: 3rd-party API timeout, stale cache returned.

Output a single Markdown section titled exactly:
«Где AC может провалиться в реальности»

Format: one `- AC.N: <trigger> → <observable symptom> → <mitigation hint>` bullet
per acceptance criterion. Be specific. NO generic platitudes («handle errors
gracefully», «add try/except»). NO restating the AC.

Word budget: ~{word_budget} words.

Story spec:
{story_text}
```

## Template 2 — Edge Case Hunter

```
You are walking every branching path and boundary condition in BMad story
{story_id} below. Report ONLY UNHANDLED edge cases — orthogonal to
adversarial review, method-driven not attitude-driven. Focus on:
- null / empty / undefined inputs at each input boundary
- numeric overflow / underflow / off-by-one
- race conditions on shared state (DB rows, files, in-memory caches)
- Unicode/encoding (Cyrillic, emoji, zero-width chars, surrogate pairs)
- time / timezone (DST, leap seconds, UTC vs local, clock skew)

Output a single Markdown section titled exactly:
«Boundary conditions: null/empty/overflow/race»

Format: itemized bullets, grouped by category (Null/Empty, Numeric, Race,
Unicode, Time). SKIP edge cases the spec already addresses explicitly.

Word budget: ~{word_budget} words.

Story spec:
{story_text}
```

## Template 3 — Pre-mortem

```
Imagine 6 months from now BMad story {story_id} is in production and has
broken. Identify long-tail risks the current spec does not address. Focus on:
- Dependency rot (library deprecation, breaking changes upstream).
- Scale assumptions (works at 100 users, breaks at 10K).
- Operational drift (manual config humans forget to update).
- Schema evolution (column types, FKs that pin the model in concrete).

Output a single Markdown section titled exactly:
«Через 6 мес это в проде сломалось — почему?»

Format: prioritized list (P0 first). Each risk gets:
- **trigger:** what condition causes the failure
- **symptom:** what users / operators observe
- **mitigation hint:** one concrete action the implementer can take NOW.

Word budget: ~{word_budget} words.

Story spec:
{story_text}
```

## Template 4 — Devil's Advocate

```
Attack the chosen architecture decision in BMad story {story_id} below.
Identify 1-2 plausible alternatives the spec did NOT take. Force a
justification for the chosen path. Focus on:
- «Why not just X?» where X is the simpler/cheaper/safer alternative.
- Cost / complexity vs. requirement coverage tradeoffs.
- Lock-in risks (vendor, framework, schema).
- Performance vs. correctness tradeoffs.

Output a single Markdown section titled exactly:
«Атака на architecture decision: зачем не альтернатива X?»

Format: 1-2 numbered alternatives. For each: strongest-framing description
(do NOT strawman) → rebuttal citing concrete constraint from spec → residual
risk that survives the rebuttal.

Word budget: ~{word_budget} words.

Story spec:
{story_text}
```

## Template 5 — Security Red Team

```
Apply STRIDE-lite threat model to BMad story {story_id} below:
- Spoofing — can auth / identity be forged?
- Tampering — can request / data be modified in transit or at rest?
- Repudiation — can actions be denied / is there an audit gap?
- Information disclosure — PII leakage, logs with secrets, stack traces.
- Denial of service — rate-limit gaps, expensive endpoints, amplification.
- Elevation of privilege — tenant isolation, role checks, RLS bypass.

Output a single Markdown section titled exactly:
«Security implications + threat model»

Format: one bullet per applicable STRIDE category. Each: identified threat →
attack vector → required mitigation. Cite Russian compliance (152-ФЗ
ст.13.11, 187-ФЗ КИИ) where PII / cross-border / critical-infrastructure
data applies.

Word budget: ~{word_budget} words.

Story spec:
{story_text}
```

## quick mode (combined call)

In `quick` mode, all 5 templates are concatenated into a single Opus call. The
combined call prepends a header asking for **~200 words per section** (total
~1000 words enriched output). Each section MUST appear in the output with
its exact title.

## deep mode (5 separate calls)

In `deep` mode, each template runs as an independent Opus call with full
~300-word budget. Total ~1500 words enriched output. Section titles must
match templates exactly so downstream `merge_enriched()` can reassemble.
