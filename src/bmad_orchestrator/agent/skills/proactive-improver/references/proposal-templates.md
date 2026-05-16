# Proposal templates (proactive-improver)

## Template — policy update (low-risk, auto-apply on approval)

```yaml
type: policy
file: <target>/_bmad-output/_config/orchestrator-policy.yaml
title: "<one-line summary>"
risk: low
auto_apply_if_approved: yes
diff: |
  + - id: <stable-slug>
  +   match: { topics: [<topic1>, <topic2>] }
  +   action: auto_resolve | escalate | conditional
  +   default: "<canonical answer>"
effect: "<expected % reduction in escalations or $ savings>"
evidence:
  - "<wave/story id where this pattern showed up>"
  - "<lesson file pointer>"
```

## Template — config update (medium-risk, auto-apply on approval)

```yaml
type: config
file: <orchestrator>/.env OR <orchestrator>/config.py
title: "<one-line summary>"
risk: medium
auto_apply_if_approved: yes
diff: |
  - watchdog_threshold_minutes: 5
  + watchdog_threshold_minutes: 8
effect: "<observable change in behavior>"
evidence:
  - "<incident or pattern that prompted this>"
```

## Template — code refactor (high-risk, PR-only)

```yaml
type: code
file: src/bmad_orchestrator/agent/tools/dag.py
title: "Cache epic frontmatter parsing"
risk: high
auto_apply_if_approved: no   # always PR + human review
diff_lines: +28 / -5
pr_branch: auto-improvement/dag-cache-<YYYY-MM-DD>
proposal: |
  Currently `parse_epic_frontmatter()` re-parses on every DAG call. Add
  module-level `@lru_cache(maxsize=64)` keyed by file mtime so reruns within
  the same wave skip the parse step.
effect: "~30s saved per wave start"
evidence:
  - "wave 1a startup time: 38s (measured 2026-05-16)"
```

## Telegram push (consolidated, max 5 proposals)

```
📊 Wave {wave} complete ({done}/{total} merged, ${spent})

Я научился ({lessons_count} новых уроков, /lessons {wave}):
  • {lesson 1}
  • {lesson 2}

Предлагаю {N} улучшений:

📝 [policy] {title}  · risk: {risk}  · effect: {effect}
   [✓ apply] [✗ reject] [подробнее]

⚙ [config] {title}  · risk: {risk}
   [✓ apply] [✗ reject] [подробнее]

🔧 [code] {title}  · diff: +{lines}/-{lines}
   [📋 view PR diff] [✓ create PR] [✗ skip]

Без твоего ответа — продолжаю по существующим правилам.
Reply: /approve all | /review | /skip
```

## Storage layout

```
<target>/_bmad-output/runs/<wave>/improvement-proposals.md
<target>/_bmad-output/runs/<wave>/improvement-proposals.audit.jsonl
```

`audit.jsonl` — per-proposal lifecycle events (created / approved / rejected /
applied / effect_measured). Used for «is this proposer trustworthy» feedback
loop в reflexion-learner.
