---
name: proactive-improver
description: Proactive code/policy/config improvement proposals — pushes to Telegram with inline buttons после каждого retro + monthly cron + chat запрос. Closes the gap "agent accumulates knowledge but never proposes improvements".
---

# proactive-improver skill

## Когда активируется (3 trigger'а)

| Trigger | Когда | Что делает |
|---------|-------|------------|
| `wave_boundary_reached` / `epic_boundary_reached` / `phase4_complete` | Сразу после `retrospective-writer` отработала | Анализирует свежие lessons, формирует 2-5 предложений, шлёт Telegram push с inline buttons |
| `monthly_review_scheduled` | 1-го числа в 10:00 (cron) | **Deep self-review** — анализ всех accumulated lessons за месяц, 3-7 архитектурных предложений |
| `user_chat_message` с триггерами «насколько умнее», «что предлагаешь», «как улучшить», «improve» | По запросу | Срочный deep review |

## Inputs

- `_bmad-output/runs/*/lessons.md` — все per-story lessons
- `_bmad-output/runs/*/policy-deltas.yaml` — предложенные но **не применённые** policy правки
- `_bmad-output/retrospectives/` — wave + epic retros
- `<orchestrator>/memory/cross-wave-learnings.md` — long-term паттерны
- Текущий код orchestrator'а (для code-level proposals)
- Audit log past proposals (чтобы не повторяться)

## Output (3 типа proposals)

### 1. Policy update (low-risk)

```yaml
type: policy
file: <target>/_bmad-output/_config/orchestrator-policy.yaml
diff:
  + - id: impl-async-error-pattern
  +   match: { topics: [error-handling, async] }
  +   action: auto_resolve
  +   default: "Используй tokio::Result, propagate через ?"
effect: "~15% меньше escalations на async stories"
risk: low
auto_apply_if_approved: yes
```

### 2. Config update (medium-risk)

```yaml
type: config
file: <orchestrator>/.env OR config.py
diff:
  - watchdog_threshold_minutes: 5
  + watchdog_threshold_minutes: 8
effect: "Исключит false-positive STALE detection для DB migration stories"
risk: medium
auto_apply_if_approved: yes
```

### 3. Code refactor proposal (high-risk)

```yaml
type: code
file: src/bmad_orchestrator/agent/tools/dag.py
proposal: "Cache epic frontmatter parsing (currently re-parsed every call)"
diff_lines: +28 / -5
effect: "~30s экономии на старте каждой wave"
risk: high
auto_apply_if_approved: no   # ← создаётся PR, не applies автоматом
pr_branch: auto-improvement/dag-cache-2026-05-16
```

## Telegram push template

```
📊 Wave {wave} complete ({done}/{total} merged, ${spent})

Я научился ({lessons_count} новых уроков, /lessons {wave}):
  • {lesson 1}
  • {lesson 2}
  ...

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

## Tools используются

- `read_memory`, `write_memory` (lessons + audit log past proposals)
- `escalate_to_human` (push в Telegram с buttons)
- Для code proposals: спавн отдельного `claude -p` subagent с задачей «сгенерируй PR diff и commit в branch `auto-improvement/<slug>`»

## Хранение proposals (audit)

```
<target>/_bmad-output/runs/<wave>/improvement-proposals.md
```

Каждое proposal — entry с полями:
- `id` (uuid)
- `type` (policy / config / code)
- `created_at`
- `approved` (bool) / `rejected` (bool) / `pending` (bool)
- `approved_by` (telegram_user_id)
- `applied_at` (если applied)
- `effect_measured` (заполняется через 1-2 waves после apply)

## Rules

1. **Не дублировать** уже approved или rejected proposals (check audit log)
2. **Code proposals** — НИКОГДА не auto-apply. Только PR + human review.
3. **Policy/config с risk=low** — можно auto-apply если в Telegram нажат `[✓ apply]`
4. **Policy/config с risk=medium/high** — всегда требуют explicit approval + diff preview
5. **Max 5 proposals per push** (anti-spam)
6. **Banner в chat-режиме**: «💡 +{N} unreviewed proposals» если есть pending

## Effect tracking (long-term)

Через 1-2 waves после применения proposal — `reflexion-learner` оценивает фактический эффект:
- Сэкономили ли заявленный $?
- Исчезли ли заявленные escalations?
- Добавилась ли новая проблема?

Это пишется обратно в audit log → следующие proposals агент делает на основе **реальной effectiveness past predictions**.

## Failure modes

- Proposal слишком complex → simplify или split
- Past proposal не дал заявленного эффекта → понижается доверие к similar proposals
- 5+ rejected proposals подряд → escalate «может я не понимаю чего ты хочешь?»
- Code proposal не компилируется → reject + lesson «не предлагать без syntax check»
