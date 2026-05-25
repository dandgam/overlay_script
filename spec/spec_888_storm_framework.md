# Спека — 888 Storm Framework v1.0

**Дата:** 2026-05-26
**Статус:** Approved v1.3 (§19 regression detection added 2026-05-26)
**Owner:** AABIT Server
**Контекст:** консолидация обсуждения architectural brief / Virgil-reactive-patching / 888-feature-creep / scripts-vs-LLM на сессии 2026-05-26

---

## §1. Цель и main claim

**Цель:** превратить 888 из LLM-dispatcher'а который полагается на «правила в промпте» в **self-contained систему с детерминированным enforcement** ключевых дисциплин разработки.

**Main claim:** все 10 gap'ов выявленных в сессии (reactive-patching, feature-creep, comparator shallow, ...) закрываются **3-слойной архитектурой** Hooks + Storm Core + Embedded Skills. После имплементации `~/.claude/CLAUDE.md` правила перестают полагаться на «надеюсь LLM вспомнит» и переходят в **«физически не может пропустить»**.

**Не-цель:** заменить bmad-agent-builder, BMad Method, или Anthropic SDK. Storm framework — **слой поверх** этих инструментов, добавляющий enforcement которого у них нет.

---

## §2. Проблема — что мы выявили в обсуждении

| # | Проблема | Источник в memory |
|---|---|---|
| P1 | Virgil reactive-patching цикл NEW-1..NEW-27 без taxonomy | project_pilot_antares_*, project_milestone_pilot_findings_closure_v* |
| P2 | 888 feature-creep через накопление feedback_*-memory без enumeration | feedback_888_auto_park, feedback_888_human_names, feedback_888_queue_fresh, feedback_888_explain_simply, feedback_menu_ux_no_jargon |
| P3 | Comparator v1 shallow — docs-only 9-dim parse, пропускает code-grounding | feedback_comparator_shallow_template |
| P4 | Правила в CLAUDE.md / SKILL.md забываются из-за context rot | feedback_llm_worker_overthinks_skills + общая physics LLM attention |
| P5 | BMAD bmad-agent-builder не имеет Phase 4.5 (adversarial review до build) | вердикт сессии после Read build-process.md |
| P6 | LLM-dispatcher над LLM-workers — anti-pattern из брифа | бриф §1, §7; vision_888_self_contained |
| P7 | Bug fixes без minimal reproducer | feedback_no_full_story_replay |
| P8 | Merge'ы без обязательного review | NEW-22, NEW-26 lessons |
| P9 | Destructive operations без double-confirm | risk из user-level CLAUDE.md §Risky actions |
| P10 | Skills могут ломаться upstream → 888 ломается каскадом | bmb_integration_888, vision_888_self_contained |

---

## §3. Решение — 3-слойная архитектура

```
LAYER 1 — HOOKS  (deterministic, .claude/hooks/*.sh + settings.json)
    ↓ inject context / exit 2 block
LAYER 2 — STORM CORE  (Python, ~/.claude/skills/888/storm/)
    ↓ invokes embedded methods
LAYER 3 — EMBEDDED SKILLS  (~/.claude/skills/888/storm/embedded/)
```

### Layer 1 — Hooks (физическая гарантия)

6 хуков в `~/.claude/hooks/`, зарегистрированы в `~/.claude/settings.json`:

| Hook | Event | Что делает |
|---|---|---|
| `intent-detector.sh` | UserPromptSubmit | Классифицирует prompt → inject storm requirement в context |
| `patch-counter.sh` | PostToolUse, matcher=Bash with `git commit` | Инкрементирует counter по scope; emit warning при N≥5 |
| `code-gate.sh` | PreToolUse, matcher=Edit\|Write | Блокирует если scope с counter≥5 не имеет taxonomy artifact'а |
| `merge-guard.sh` | PreToolUse, matcher=Bash with `git merge\|push` | Блокирует merge/push без review artifact |
| `destructive-guard.sh` | PreToolUse, matcher=Bash | Блокирует rm -rf, DROP TABLE, force-push без double-confirm |
| `audit-trail.sh` | Stop | Append session_end в events.jsonl |

Графически:
```
user prompt → [intent-detector] → context inject → LLM
LLM tool call (Edit) → [code-gate] → exit 2 если нет artifact'а
LLM tool call (Bash git commit) → success → [patch-counter] → log
LLM tool call (Bash git merge) → [merge-guard] → exit 2 если нет review
session end → [audit-trail] → events.jsonl
```

### Layer 2 — Storm Core (Python orchestration)

Файлы в `~/.claude/skills/888/storm/`:

| Файл | Назначение |
|---|---|
| `storm-orchestrator.py` | Главный entry: `storm-orchestrator.py <trigger_id> <slug>` → запускает scenario → пишет artifact |
| `intent-classifier.py` | Принимает текст prompt'а → возвращает JSON `{triggers, scenarios, context_inject}` |
| `taxonomy-checker.py` | Для scope проверяет существование taxonomy artifact'а |
| `audit-trail.py` | Append-only event log: events.jsonl + state.json + decision-log.md |
| `patch_counter.py` | Per-scope counter с time window (14d default) |
| `scope_from_path.py` | Маппит file path → scope name (например `runtime/sandbox.py` → `virgil-sandbox`) |
| `sync_manifest.py` | Сравнивает embedded files с upstream source, генерирует divergence log |
| `state.json` | Current state всех активных initiatives |
| `events.jsonl` | Append-only log всех событий (storm started/completed, halts, overrides) |
| `decision-log.md` | Append-only список решений с justification |
| `patch-counter.json` | `{<scope>: [<iso-timestamp>, ...]}` |

### Layer 3 — Embedded Skills (no external refs)

Файлы в `~/.claude/skills/888/storm/embedded/`:

| Файл | Origin | Назначение |
|---|---|---|
| `elicitation-methods.csv` | bmad-advanced-elicitation/methods.csv | 50 методов критического мышления |
| `edge-case-hunter.md` | bmad-review-edge-case-hunter/SKILL.md | Чек-лист edge cases по branch'ам |
| `adversarial-protocol.md` | bmad-review-adversarial-general/SKILL.md | Cynical Review protocol |
| `code-review-triage.md` | bmad-code-review/SKILL.md | Triage таксономия Blind Hunter / Edge Case / Acceptance Auditor |
| `readiness-checklist.md` | bmad-check-implementation-readiness/SKILL.md | PRD/UX/Architecture/Epics проверка |
| `comparator-rubric-9d.md` | 888-persona-comparator/SKILL.md | 9-dim сравнительная рубрика |
| `failure-mode-template.md` | NEW (888 original) | Шаблон FMA для любого scope |
| `taxonomy-template.md` | NEW (888 original) | Шаблон closed-set taxonomy |

Все файлы — **копии**, не symlinks. Manifest tracks origin + version + last-sync.

---

## §4. Карта 10 trigger-сценариев

| ID | Trigger | Detection | Required Storm | Output Artifact | Block Condition | Closes Gap |
|---|---|---|---|---|---|---|
| **T1** | Feature intent | Keywords: «хочу внедрить», «добавим», «новая фича», «build feature» | #39 First Principles, #11 Tree of Thoughts, #34 Pre-mortem, #20 ADR, #42 Critique | `spec/feature_<slug>_storm.md` | code-gate блокирует Edit в scope `<slug>` | P2 |
| **T2** | Agent create | «создам агента», «новый агент», «build agent» | #34 Pre-mortem, #35 FMA, #11 Tree of Thoughts, #17 Red Team, #4 User Persona | `spec/agent_<slug>_storm.md` | bmad-agent-builder Phase 5 не запускается без artifact | P5 |
| **T3** | Agent edit | «изменю агента», «отредактирую агента», «обнови агент» | #20 ADR (why change), #34 Pre-mortem (what breaks), cross-impact tracing | `spec/agent_<slug>_change_storm.md` | code-gate на агентовские файлы | P5 |
| **T4** | Comparator | «сравни», «vs», «лучше чем», «compare» | #33 Comparative Matrix, #36 Devil's Advocate, code-grounding (grep counts), dedup-grep, counter-example gate | `spec/compare_<slug>_storm.md` | Comparator verdict не публикуется без passes на rigor checks | P3 |
| **T5** | Improvement | «хочу улучшить», «оптимизирую», «refactor» | #42 Critique, #15 Meta-Prompting, #11 Tree of Thoughts | `spec/improve_<slug>_storm.md` | code-gate в scope | P2 |
| **T6** | Doubt resolution | «не уверен», «сомневаюсь», «правильно ли», «как лучше» | #41 Socratic, #40 5 Whys, #39 First Principles | `_storm_audit/doubt_<date>.md` | None (informational); inject в context | P4 |
| **T7** | Bug fix counter | git commit с `fix(<scope>)` | Increment counter; if N≥5 in 14d → forced #35 FMA + taxonomy | `_storm_audit/patch-counter.json` + `spec/taxonomy_<scope>.md` (если N≥5) | code-gate блокирует следующий fix в этом scope без taxonomy | P1 |
| **T8** | Merge | `git merge`, `git push origin main` | #34 Pre-mortem of merge + #42 Critique + #17 Red Team via adversarial-protocol | `_bmad/reviews/<branch>.md` | merge-guard exit 2 | P8 |
| **T9** | Stuck signal | «топчусь», «в кругу», «нагороэжение», «куда мы идём» | Forced STOP-session: #50 Lessons Learned + #15 Meta-Prompting + #11 Tree of Thoughts | `_storm_audit/pause_<date>.md` | Inject в context «STOP. Не патчить.»; ручной resume | P1, P2 |
| **T10** | Destructive op | `rm -rf`, `DROP TABLE`, force-push, `git reset --hard` | Double-confirm с явным justification | Audit entry в events.jsonl | destructive-guard exit 2 до touch override-file | P9 |
| **T11** | Regression detected | Авто-детект от R1-R5 (§19): error fingerprint repeat / hot-file / commit cycle / test regression | #40 5 Whys, #35 FMA, #36 Devil's Advocate, #50 Lessons Learned | `spec/regression_<fingerprint_hash>_storm.md` | code-gate блокирует Edit в affected scope до artifact | P1 (regressions) |

**Расширяемость:** новый триггер = одна строка в `intent-classifier.py` + один scenario.md в `scenarios/`. Closed-set: 11 категорий, всё остальное мапится. Расширение через явный акт (правило M3).

---

## §5. Hook contracts — детальные

### 5.1 intent-detector.sh

**Trigger:** UserPromptSubmit
**Input (stdin):** `{"prompt": "<user text>", "session_id": "..."}`
**Action:**
1. Извлечь prompt
2. Вызвать `python3 ~/.claude/skills/888/storm/intent-classifier.py <prompt>`
3. Если найдены triggers — emit `context_inject` text в stdout (попадает в контекст LLM)
4. Exit 0 always (не блокирует)

**Edge cases:**
- prompt пустой → exit 0 silent
- intent-classifier.py упал → log в events.jsonl, exit 0 silent (не блокирует user)
- jq отсутствует → exit 0 + warning в stderr

### 5.2 patch-counter.sh

**Trigger:** PostToolUse, matcher=`Bash`
**Input (stdin):** `{"tool_input": {"command": "..."}, ...}`
**Action:**
1. Если command содержит `git commit` И message содержит `fix(<scope>)` → парсим scope
2. `python3 patch_counter.py increment <scope>` (запись в `patch-counter.json`)
3. `python3 patch_counter.py get <scope> --window 14d`
4. Если count ≥ 5 → echo warning в stderr:
   ```
   ⚠ PATCH COUNTER: <scope> = <count> fixes за 14 дней.
   Правило №8: следующий fix будет заблокирован code-gate'ом до создания taxonomy.
   Запусти: /888 storm T7-enumeration <scope>
   ```
5. Exit 0 (не блокирует уже произошедший commit)

**Edge cases:**
- Не fix commit → exit 0 noop
- scope невозможно распарсить → log + exit 0

### 5.3 code-gate.sh

**Trigger:** PreToolUse, matcher=`Edit|Write`
**Input (stdin):** `{"tool_input": {"file_path": "..."}, ...}`
**Action:**
1. Извлечь file_path
2. `scope=$(python3 scope_from_path.py <file>)`
3. Проверить:
   - **T7 gate:** `patch_counter.py get <scope> --window 14d`. Если ≥5 — нужен `spec/taxonomy_<scope>.md`. Нет → exit 2.
   - **T1/T2/T3/T5 gate:** Если в `state.json` есть active initiative для этого scope с status=storm-pending — нужен соответствующий artifact. Нет → exit 2.
4. Иначе exit 0.

**Exit 2 message format:**
```
BLOCKED by 888 storm code-gate:
  Reason: <T7 patch count exceeded | T1 storm pending | ...>
  Scope: <scope>
  Required artifact: <path>
  How to fix: <command>
  Override (last resort): echo "<reason>" > /tmp/.888_override_<scope> && retry
  (override file удаляется автоматически после use; reason логируется в events.jsonl)
```

### 5.4 merge-guard.sh

**Trigger:** PreToolUse, matcher=`Bash`
**Input (stdin):** `{"tool_input": {"command": "..."}}`
**Action:**
1. Если command содержит `git merge` или `git push origin main` или `git push.*main` → проверка
2. branch = `git rev-parse --abbrev-ref HEAD`
3. Проверить `_bmad/reviews/<branch>.md` exists
4. Если нет → exit 2 с инструкцией создать через `/888 storm T8-merge`
5. Иначе exit 0

### 5.5 destructive-guard.sh

**Trigger:** PreToolUse, matcher=`Bash`
**Input (stdin):** `{"tool_input": {"command": "..."}}`
**Action:**
1. Pattern list (regex):
   - `rm -rf`
   - `DROP TABLE`
   - `git push.*--force`
   - `git push.*\+`
   - `git reset --hard`
   - `DELETE FROM.*WHERE.*1\s*=\s*1`
   - `find.*-delete`
2. Если match:
   - Compute hash: `confirm_file=/tmp/.888_destructive_confirm_$(md5sum cmd | head -c8)`
   - Если confirm_file exists И содержит non-empty reason → удалить + log reason в events.jsonl + exit 0
   - Если нет ИЛИ файл пустой → exit 2 с инструкцией `echo "<reason>" > <confirm_file>`
3. Иначе exit 0

### 5.6 audit-trail.sh

**Trigger:** Stop
**Input (stdin):** `{"session_id": "...", "stop_hook_active": false}`
**Action:**
1. Append `{"event": "session_end", "session_id": "...", "timestamp": "<iso>", "summary": {...}}` в events.jsonl
2. Exit 0

---

## §6. Storm Core — Python contracts

### 6.1 intent-classifier.py

```python
def classify(prompt: str) -> dict:
    """
    Input: raw user prompt text
    Output: {
        "triggers": ["T1", "T6", ...],     # IDs which matched
        "scenarios": ["T1_feature_intent.md", ...],
        "context_inject": "⚠ STORM REQUIRED ...",  # инжектится в context LLM
        "block": false                     # классификатор сам не блокирует
    }
    """
```

**Trigger patterns** определены как module-level dict (см. псевдокод в session chat §7).

### 6.2 storm-orchestrator.py

```bash
storm-orchestrator.py <trigger_id> <slug> [--method <id>]+
# Examples:
storm-orchestrator.py T1 multi-llm-routing
storm-orchestrator.py T7-enumeration virgil-sandbox
storm-orchestrator.py T2 my-new-agent --method 34 --method 35 --method 11
```

**Что делает:**
1. Загружает scenario template из `scenarios/<trigger_id>_*.md`
2. Для каждого required method — открывает interactive prompt LLM-у с method description из methods.csv
3. Собирает output в structured YAML
4. Записывает `spec/<artifact_name>_<slug>_storm.md`
5. Update `state.json`: добавляет initiative с status=storm-complete

### 6.3 taxonomy-checker.py

```bash
taxonomy-checker.py <scope>
# Возвращает 0 если spec/taxonomy_<scope>.md существует И прошёл schema check
# Возвращает 1 если отсутствует
# Возвращает 2 если файл есть, но шаблон не заполнен (TODO/N/A/empty sections)
```

**Schema check:** taxonomy artifact обязан содержать:
- `## Categories` секцию с ≥3 пунктами
- `## Reactions` секцию где каждой категории присвоено действие (с одним из 4 уровней: try-fix / soft-warn / LLM-judge / hard-halt)
- `## Coverage` секцию с явным «новый случай → мапится в категорию N или расширяет схему»

### 6.4 audit-trail.py

```python
# Event types:
# - "intent_detected"      : trigger fired
# - "storm_started"        : storm-orchestrator invoked
# - "storm_completed"      : artifact written
# - "code_gate_blocked"    : edit/write blocked
# - "merge_gate_blocked"   : merge blocked
# - "destructive_blocked"  : destructive op blocked
# - "destructive_confirmed": override + retry
# - "patch_counter_warn"   : counter exceeded threshold
# - "session_end"          : Stop hook
# - "manual_override"      : user touch'нул override file

# Все события идут в:
# - ~/.claude/skills/888/storm/events.jsonl (append-only)
# - state.json (current state, updated)
# - decision-log.md (если событие = decision, append)
```

---

## §7. Embedded Skills — manifest

`~/.claude/skills/888/storm/manifest.json`:

```json
{
  "version": "888.storm.v1.0.0",
  "absorbed_at": "2026-05-26",
  "originals": [
    {
      "absorbed_file": "embedded/elicitation-methods.csv",
      "source_path": "~/.claude/skills/888/vendor/BMAD-METHOD/bmad-advanced-elicitation/methods.csv",
      "source_version": "BMAD-METHOD@<commit-sha-at-absorb>",
      "absorbed_at": "2026-05-26",
      "last_sync": "2026-05-26",
      "update_protocol": "diff against vendor → manual review → opt-in adopt",
      "divergence_file": null
    },
    {
      "absorbed_file": "embedded/edge-case-hunter.md",
      "source_path": "~/.claude/skills/bmad-review-edge-case-hunter/SKILL.md",
      "source_version": "user-local@2026-05-20",
      "absorbed_at": "2026-05-26",
      "last_sync": "2026-05-26",
      "update_protocol": "diff against source path → manual review",
      "divergence_file": null
    },
    {"absorbed_file": "embedded/adversarial-protocol.md", "source_path": "~/.claude/skills/bmad-review-adversarial-general/SKILL.md", "...": "..."},
    {"absorbed_file": "embedded/code-review-triage.md", "source_path": "~/.claude/skills/bmad-code-review/SKILL.md", "...": "..."},
    {"absorbed_file": "embedded/readiness-checklist.md", "source_path": "~/.claude/skills/bmad-check-implementation-readiness/SKILL.md", "...": "..."},
    {"absorbed_file": "embedded/comparator-rubric-9d.md", "source_path": "~/.claude/skills/888-persona-comparator/SKILL.md", "...": "..."}
  ],
  "originals_added": [
    {"file": "embedded/failure-mode-template.md", "author": "888-storm-v1", "purpose": "FMA template for any scope"},
    {"file": "embedded/taxonomy-template.md", "author": "888-storm-v1", "purpose": "Closed-set taxonomy template"}
  ],
  "update_command": "python3 ~/.claude/skills/888/storm/sync_manifest.py check-upstream",
  "review_cadence": "monthly",
  "divergence_policy": "manual_review_required"
}
```

**Update workflow:**
1. Monthly cron OR manual: `python3 sync_manifest.py check-upstream`
2. Скрипт сравнивает каждый absorbed_file с source_path
3. Если diff != ∅ → пишется `embedded/divergence/<name>.diff.md`
4. User читает, выбирает: ADOPT (refresh + update version) / REJECT (log как conscious divergence) / DEFER
5. Никакого автоматического sync

---

## §8. Scenario template — структура

Все scenarios в `~/.claude/skills/888/storm/scenarios/T<N>_<name>.md` следуют общему шаблону:

```markdown
---
trigger_id: T<N>
trigger_name: <name>
detection_keywords: [<list>]
required_artifact: <path template, e.g. spec/feature_<slug>_storm.md>
required_methods: [<list of method IDs from elicitation-methods.csv>]
optional_methods_pool: [<list of candidates>]
selection_rules:
  - if: <context signal regex>
    add: [<method IDs>]
    rationale: "<why these methods for this context>"
  - if: <another signal>
    add: [<method IDs>]
    rationale: "..."
selection_fallback: llm_judge_picks_1_to_2  # если 0 static rules matched
max_optional_selected: 2
block_until: <condition>
closes_gap: [<P1-P10>]
---

# Storm: <Name>

## Trigger context
<когда срабатывает, какой user intent ловится>

## Mandatory storm
<список required методов с краткой инструкцией каждого>

## Conditional storms (auto-selection — см. §18)
<какие optional методы при каких context signals; описание из selection_rules>

## Output schema (YAML in markdown)
<structured fields для artifact>

## Block condition (когда code-gate блокирует)
<когда какой хук блокирует что>
```

10 scenario файлов будут написаны в S4 по этому шаблону.

---

## §9. Файловая структура (target)

```
~/.claude/
├── settings.json                              ← обновлён: hooks section
├── hooks/
│   ├── intent-detector.sh                    ← NEW
│   ├── patch-counter.sh                      ← NEW
│   ├── code-gate.sh                          ← NEW
│   ├── merge-guard.sh                        ← NEW
│   ├── destructive-guard.sh                  ← NEW
│   └── audit-trail.sh                        ← NEW
└── skills/
    └── 888/
        ├── SKILL.md                           ← обновлён: storm references
        ├── storm/                             ← NEW
        │   ├── manifest.json
        │   ├── storm-orchestrator.py
        │   ├── intent-classifier.py
        │   ├── taxonomy-checker.py
        │   ├── audit-trail.py
        │   ├── patch_counter.py
        │   ├── scope_from_path.py
        │   ├── sync_manifest.py
        │   ├── state.json                     ← init: {"initiatives": []}
        │   ├── events.jsonl                   ← init: пустой
        │   ├── decision-log.md                ← init: header only
        │   ├── patch-counter.json             ← init: {}
        │   ├── scenarios/
        │   │   ├── T1_feature_intent.md
        │   │   ├── T2_agent_create.md
        │   │   ├── T3_agent_edit.md
        │   │   ├── T4_comparator.md
        │   │   ├── T5_improvement.md
        │   │   ├── T6_doubt_resolution.md
        │   │   ├── T7_bug_fix.md
        │   │   ├── T8_merge.md
        │   │   ├── T9_stuck_signal.md
        │   │   └── T10_destructive_op.md
        │   └── embedded/
        │       ├── elicitation-methods.csv
        │       ├── edge-case-hunter.md
        │       ├── adversarial-protocol.md
        │       ├── code-review-triage.md
        │       ├── readiness-checklist.md
        │       ├── comparator-rubric-9d.md
        │       ├── failure-mode-template.md
        │       ├── taxonomy-template.md
        │       └── divergence/                ← initially empty
        └── vendor/                            ← остаётся read-only reference
            └── ...                            ← как сейчас
```

---

## §10. Implementation roadmap (5 сессий)

### S1 — Hooks (Layer 1)
**Эффорт:** 1 сессия ~ 2 часа
**Файлы:**
- `~/.claude/hooks/intent-detector.sh` (~50 LOC bash)
- `~/.claude/hooks/patch-counter.sh` (~60 LOC)
- `~/.claude/hooks/code-gate.sh` (~80 LOC)
- `~/.claude/hooks/merge-guard.sh` (~40 LOC)
- `~/.claude/hooks/destructive-guard.sh` (~60 LOC)
- `~/.claude/hooks/audit-trail.sh` (~30 LOC)
- `~/.claude/settings.json` — добавить hooks section

**Зависимости:** S2 stub'ы для Python вызовов (можно noop'ить пока)

**Acceptance:**
- Каждый хук вызывается через ручной test (echo JSON | hook.sh)
- 6 test scenarios: один edit/commit/merge/destructive/prompt/session-end, все блокируются/inject'ятся корректно
- Settings.json валиден через `jq`

### S2 — Storm Core (Layer 2)
**Эффорт:** 2 сессии ~ 4 часа
**Файлы:**
- `intent-classifier.py` (~100 LOC)
- `storm-orchestrator.py` (~200 LOC)
- `taxonomy-checker.py` (~80 LOC)
- `audit-trail.py` (~120 LOC)
- `patch_counter.py` (~60 LOC)
- `scope_from_path.py` (~40 LOC)
- `state.json`, `events.jsonl`, `decision-log.md`, `patch-counter.json` — init

**Acceptance:**
- `python3 intent-classifier.py "хочу внедрить multi-LLM"` → returns `{triggers: ["T1"], ...}`
- `python3 storm-orchestrator.py T1 test-feature` → создаёт `spec/feature_test-feature_storm.md` с заполненными секциями
- `python3 taxonomy-checker.py virgil-sandbox` → exit 1 (artifact missing)
- Unit tests: 80%+ coverage

### S3 — Embedded skills + sync (Layer 3)
**Эффорт:** 0.5 сессии ~ 1 час
**Файлы:**
- `embedded/*.md` — копирование 6 source файлов
- `embedded/elicitation-methods.csv` — копия
- `embedded/failure-mode-template.md` + `taxonomy-template.md` — NEW
- `manifest.json` — заполнение
- `sync_manifest.py` (~80 LOC)

**Acceptance:**
- Все 8 файлов в `embedded/` присутствуют
- `manifest.json` валиден, проходит JSON schema check
- `sync_manifest.py check-upstream` запускается без ошибок, генерирует пустой divergence report для свежеабсорбированных файлов

### S4 — Scenarios + 888/BMAD phase-embedded integration
**Эффорт:** 1.5 сессии ~ 3 часа
**Файлы:**
- `scenarios/T1-T10.md` — 10 файлов по template из §8
- `~/.claude/skills/888/SKILL.md` — секция «Storm framework» + dispatcher integration
- **Phase-embedded edits** (см. §17 mapping):
  - `vendor/bmad-builder/.../build-process.md` — добавить Phase 4.5 с auto-call `storm-orchestrator.py T2`
  - `~/.claude/skills/888-persona-analyst/SKILL.md` — call T1 на завершении фазы
  - `~/.claude/skills/888-persona-architect/SKILL.md` — call T1+T2 на старте фазы
  - `~/.claude/skills/888-persona-implementer/SKILL.md` — T7 check на старте
  - `~/.claude/skills/888-persona-qa/SKILL.md` — T8 на старте
  - `~/.claude/skills/888-persona-improver/SKILL.md` — T9 check
  - `~/.claude/skills/bmad-auto-dev/SKILL.md` — T1 + T34 на story-start, T8 pre-merge

**Acceptance:**
- 10 scenario файлов валидируются schema (YAML frontmatter корректен)
- 888 SKILL.md ссылается на storm как обязательный layer
- 7 phase-embedded edits применены (verify via grep `storm-orchestrator.py` в каждом)
- End-to-end smoke interactive: user prompt «хочу внедрить X» → intent-detector → context inject → 888 dispatcher предлагает storm → orchestrator → artifact

### S5 — End-to-end test (interactive + headless) + bug iteration
**Эффорт:** 1 сессия ~ 2 часа

**Сценарий A — Interactive:** Реальный «создам нового агента foo-bar-baz»:
1. User prompt активирует T2 (intent-detector A-path)
2. Context inject
3. 888 предлагает запустить T2 storm
4. Storm orchestrator проходит 5 методов
5. Artifact создан
6. bmad-agent-builder Phase 5 запускается (code-gate проверяет artifact, пропускает)
7. Сессия завершается
8. audit-trail записал session_end

**Сценарий B — Headless/auto:** `claude -p --headless "build agent bar-baz from spec/agent_bar-baz_brief.md"`:
1. NO user keywords → intent-detector A-path silent
2. bmad-agent-builder loaded, доходит до Phase 4.5
3. Phase 4.5 **proactively** вызывает `python3 storm-orchestrator.py T2 bar-baz` (C-path)
4. Storm проходит 5 методов automatically (с pre-defined responses or LLM-judge in non-interactive mode)
5. Artifact создан
6. Phase 5 проходит code-gate
7. session_end в audit

**Acceptance:**
- Сценарий A: полный цикл без падений, ≥6 событий в events.jsonl
- Сценарий B: полный цикл без падений в headless mode, storm вызван из workflow не из hook
- Все 5 storm методов отработали (оба сценария)
- Artifact содержит реальное содержание (не TODO/N/A) — verified taxonomy-checker.py
- Документированный список найденных bugs + fixes

**Total:** ~10-15 часов работы, 5 сессий.

---

## §11. Risks & mitigations

| # | Risk | Probability | Impact | Mitigation |
|---|---|---|---|---|
| R1 | Хуки блокируют legitimate работу (false positives) | High | Medium | Override через `echo "<reason>" > /tmp/.888_override_<scope>` + auto-delete после use. Логирование override с reason → если ≥3/неделю → review правил |
| R2 | Embedded copies устаревают | Medium | Low | `sync_manifest.py` monthly + manual divergence review |
| R3 | Storm artifact = checkbox theatre | Medium | High | `taxonomy-checker.py` schema check + LLM-judge через #41 Socratic spot-check |
| R4 | Слишком много triggers → user раздражение | Medium | Medium | Adaptive sensitivity per scope: 3 user-refuses → снижение sensitivity, log в events |
| R5 | Hooks не работают (missing jq/python3) | Low | High | Каждый hook: `command -v jq >/dev/null \|\| exit 0` silent fallback + warning |
| R6 | 888 self-modifying ломает storm | Low | Critical | Manifest hash check на старте сессии; storm/ files in `.gitignore` для prevent accidental modification из других проектов |
| R7 | Cross-platform issues (Linux only) | Low | Medium | bash + python3 only, no macOS-specific tools. Tested на Linux 6.17 |
| R8 | settings.json conflict с существующими hooks | Medium | Medium | Перед S1 — read existing settings.json, merge не replace |

---

## §12. Resolved decisions (approved 2026-05-26)

| # | Вопрос | Решение | Обоснование |
|---|---|---|---|
| Q1 | T6 doubt resolution — informational vs blocking | **Informational inject** | Сомнения сами по себе не риск; блок остановит нормальный диалог |
| Q2 | T4 comparator output | **Hybrid: artifact обязателен + summary в чате** | Artifact = source of truth для persist; summary = UX |
| Q3 | Patch counter window | **14d uniform** | 7d слишком жёстко; per-scope — overengineering; расширим если данные покажут |
| Q4 | Override mechanism | **`echo "<reason>" > /tmp/.888_override_<scope>` + auto-delete после use** | `touch` слишком легко; reason заставляет user'а сформулировать почему; одноразовый |
| Q5 | Manifest review cadence | **Monthly + on-demand** | Skills меняются медленно; cron monthly + `sync_manifest.py check-upstream` руками когда нужно |
| Q6 | vendor/ vs embedded/ | **Keep vendor/ read-only for diff'ов** | Удалить = `sync_manifest.py` не с чем сравнивать; vendor становится исторический snapshot |
| Q7 | Patch counter scopes | **Default separate + опциональный `scope_aliases.json`** | Default: scopes независимы; alias `{"virgil-*": "virgil"}` агрегирует если user видит связь |
| Q8 | 888 dispatcher | **Оставляем LLM + детерминированные gates через хуки** | Переписать на Python = 10+ сессий + потеря conversational UX; LLM-dispatcher + хуки-gates = «вариант 2 done right» из брифа |

**Принцип:** везде где можно — детерминированный хук-gate; везде где LLM реально нужен (диалог, intent, semantic check) — оставляем LLM. Без over-engineering: uniform window, monthly cadence, separate scopes by default — всё с опциональным расширением если данные покажут.

---

## §13. Acceptance criteria — full system

После S5 система считается **готовой** если:

- [ ] Все 6 хуков срабатывают на correct trigger'ы (verified test scenarios)
- [ ] 10 scenario файлов проходят schema validation
- [ ] manifest.json валиден, sync tool работает
- [ ] events.jsonl растёт корректно при каждом cycle
- [ ] code-gate реально блокирует Edit при отсутствии artifact (verified manual test)
- [ ] merge-guard реально блокирует merge без review (verified)
- [ ] destructive-guard требует double-confirm для rm -rf (verified)
- [ ] Patch counter инкрементируется на fix-commit (verified)
- [ ] Real-world cycle «создам агента» проходит end-to-end
- [ ] Audit trail сохраняет все события сцикла
- [ ] Override mechanism работает (touch override-file → next attempt проходит)
- [ ] sync_manifest.py обнаруживает divergence когда упстрим меняется (manual test через изменение source файла)

---

## §14. Cross-references

**Память проекта:**
- `feedback_build_discipline_rules` — 10 правил-дисциплины (входной материал)
- `feedback_llm_dev_best_practices` — 10 best practices
- `feedback_comparator_shallow_template` — мотивация T4 rigor
- `feedback_888_auto_park` — мотивация audit trail
- `feedback_research_persistence` — persist artifact требование
- `project_vision_888_self_contained` — vision alignment

**Specs в queue:**
- `spec/spec_verifier_contracts.md` — частично пересекается с T7-T8 (рассмотреть консолидацию)
- `spec/spec_step_file_runtime_architecture.md` — связано но orthogonal
- `spec/spec_comparator_full_fat.md` — закрывается через T4 scenario
- `spec/spec_adversarial_review_bundled.md` — embedded в T8 scenario
- `spec/spec_operator_first_class_modes.md` — orthogonal, оставлен как отдельная инициатива

**External refs:**
- BMAD-METHOD/bmad-advanced-elicitation (vendor/)
- bmad-builder build-process.md (vendor/, для Phase 4.5 design)
- Anthropic «Building Effective Agents» (концептуальный фреймворк)
- Architecture brief из сессии 2026-05-26 (chat artifact)

---

## §15. Phase 0 — review этого spec'а (THIS session)

Перед стартом S1:

1. User читает §1-§14
2. Возражает к open questions §12
3. Adjusts triggers/scenarios/file structure if needed
4. Approves → создаётся git commit с spec'ом
5. S1 запускается в **отдельной** сессии (свежий context, чтобы не упереться в context limit)

**Это применение правила №5 (enumeration ДО кода) к самому storm framework. Дисциплина начинается с этого spec'а.**

---

## §16. Invocation Modes — как вызывается elicitation

Storm может быть вызван **четырьмя путями**. Каждый mode имеет primary path + backup.

| Mode | Когда работает | Primary path | Backup |
|---|---|---|---|
| **A. Interactive (user в чате)** | User пишет текст с keyword'ами | `intent-detector.sh` (UserPromptSubmit) → context inject → LLM зовёт storm-orchestrator.py | `code-gate.sh` блокирует Edit если artifact missing |
| **B. Code-gate backfill** | LLM пытается Edit без artifact'а | `code-gate.sh` (PreToolUse) → exit 2 → форс запустить storm-orchestrator.py | — (это сам backup) |
| **C. Phase-embedded (proactive)** | На определённой фазе workflow | Прямой `python3 storm-orchestrator.py T<N> <slug>` внутри SKILL.md / build-process.md | code-gate + merge-guard страхуют если skipped |
| **D. Headless/auto-mode (`-H`)** | `claude -p --headless ...` | C (phase-embedded, primary) — workflow proactively вызывает storm | B + merge-guard + patch-counter + destructive-guard (все работают независимо от mode) |

### Принцип

- **Interactive mode** → primary A (reactive on keywords), backup B
- **Auto mode** → primary C (proactive on phase entry), backup B+merge-guard+patch-counter
- **Hooks ВСЕ работают независимо от mode** (B, merge-guard, patch-counter, destructive-guard, audit-trail) — это последний safety net
- **Phase-embedded C** — главная страховка для auto-mode, где A не срабатывает

### Что меняется в headless mode

| Mechanism | Interactive | Headless |
|---|---|---|
| `intent-detector.sh` keyword parsing | Активно работает | Может не сработать (нет user-keywords) |
| `code-gate.sh` artifact check | Активно | Активно |
| `merge-guard.sh` | Активно | Активно |
| `patch-counter.sh` + threshold | Активно | Активно |
| `destructive-guard.sh` | Активно | Активно с required-reason |
| Phase-embedded storm call | Optional (полагается на intent-detector) | **Обязательное** |
| Storm interactivity | Step-by-step user choice | Pre-defined methods + LLM-judge in non-interactive mode |

### Storm orchestrator behavior в headless

`storm-orchestrator.py` детектит mode через `os.isatty(0)` (или env `HEADLESS_MODE=1`):

| Mode | Method invocation |
|---|---|
| Interactive | Пошагово показывает 5 методов, ждёт user input (как `/bmad-advanced-elicitation`) |
| Headless | Запускает 5 методов автоматически: каждый метод = LLM-call с structured prompt + structured output → собирает в YAML → пишет artifact без user interaction |

---

## §17. Phase-Embedded Mapping (proactive storm calls)

Для каждого workflow — какие storm-вызовы добавляются на каких фазах:

| Workflow | Фаза | Storm trigger | Когда вызывается | Что закрывает |
|---|---|---|---|---|
| **bmad-agent-builder** | **Phase 4.5** (новая, между Draft и Build) | T2 (agent_create) | Сразу после Phase 4 (Draft & Refine), до Phase 5 (Build) | Gap P5 (BMAD без Phase 4.5) |
| **888-persona-analyst** | End of Phase 1 | T1 (feature_intent) | Перед handoff в Architect | Feature без First Principles → Architect |
| **888-persona-architect** | Start of Phase 2 | T1 + T20 ADR | На старте architecture work | Architecture решения без явных trade-offs |
| **888-persona-implementer** | Start of Phase 2.5 | T7 check (patch counter) | Перед началом implementation | Reactive-patching без taxonomy |
| **888-persona-qa** | Start of Phase 3 | T8 (merge prep) | Перед review handoff | Merge без adversarial review |
| **888-persona-improver** | Start of Phase 5 | T9 check (stuck signal) | На retro session | Reactive pattern не замечен |
| **bmad-auto-dev** | story-start | T1 + #34 Pre-mortem | Перед dev-story | Story без pre-mortem |
| **bmad-auto-dev** | pre-merge | T8 | Перед finalize | Merge без review |

### Конкретный пример для bmad-agent-builder Phase 4.5

Edit в `vendor/bmad-builder/src/skills/bmad-agent-builder/build-process.md` после §Phase 4 и до §Phase 5:

```markdown
## Phase 4.5: Adversarial Review (NEW — 888 storm v1.0)

**MANDATORY before Phase 5 Build.** Run storm T2 to validate the draft before committing to code.

### Invocation

Interactive mode:
```bash
python3 ~/.claude/skills/888/storm/storm-orchestrator.py T2 {slug} --interactive
```

Headless mode (auto):
```bash
HEADLESS_MODE=1 python3 ~/.claude/skills/888/storm/storm-orchestrator.py T2 {slug}
```

### What it runs

5 mandatory methods from embedded/elicitation-methods.csv:
- #34 Pre-mortem Analysis — «через 3 месяца провал — почему?»
- #35 Failure Mode Analysis — что может сломаться в каждом компоненте?
- #11 Tree of Thoughts — какие 3 альтернативы рассмотрены?
- #17 Red Team vs Blue Team — adversarial stress-test
- #4 User Persona Focus Group — кому это нужно?

### Output

`spec/agent_{slug}_storm.md` — without this artifact Phase 5 blocked by code-gate.

### Block condition

Phase 5 build commands invoke Edit/Write. `code-gate.sh` will exit 2 if `spec/agent_{slug}_storm.md` missing.
```

### Принцип расширения mapping

Новый workflow → добавить строку в §17 + edit соответствующий SKILL.md/build-process.md. Закрытая схема: 8 mapping строк сейчас, новые добавляются явно (правило M3 — closed-set).

### Авто-mode summary

В `--headless` mode:
1. **Hooks работают** как обычно (включая code-gate как safety net)
2. **A-path (intent-detector)** может не сработать без user-keywords
3. **C-path (phase-embedded)** — главный механизм; workflow сам вызывает storm на нужных фазах
4. **Если skipped C-path** — code-gate / merge-guard / patch-counter всё равно перехватят

Таким образом auto-mode имеет **усиленный** enforcement: меньше reactive (нет user keywords), больше proactive (phase-embedded) + все хуки активны.

---

## §18. Method Auto-Selection (cascade)

Каждый scenario имеет `required_methods` (фиксированные) + `optional_methods_pool` (кандидаты). Реально-запускаемое подмножество выбирается через **3-step cascade**.

### Cascade

```
1. REQUIRED      ← всегда запускаются, не выбираются (deterministic baseline)
2. STATIC RULES  ← matched context signals добавляют методы из pool (deterministic)
3. LLM-JUDGE     ← fallback только если step 2 ничего не добавил
4. USER OVERRIDE ← interactive only; headless skips
```

### Step 1: Required methods (no choice)

Из scenario YAML `required_methods: [<list>]`. Всегда запускаются все. Не зависят от context, mode или signals.

**Пример T1:** required = `[39 First Principles, 11 Tree of Thoughts, 34 Pre-mortem, 20 ADR, 42 Critique]` — это 5 баз для **любой** feature.

### Step 2: Static rules (deterministic context match)

YAML `selection_rules` — список правил вида:
```yaml
- if: <regex applied to scope name + slug + recent events.jsonl>
  add: [<method IDs>]
  rationale: "<why>"
```

**Алгоритм:**
1. Для каждого правила — проверка matched
2. Если matched → add методы из правила в selected set
3. Дедупликация (избегаем дублей)
4. Cap по `max_optional_selected` (default 2)

**Пример T1 (security feature):**
- scope = `feature_payment-flow`
- rule matched: `scope содержит "security|auth|payment|crypto"`
- added: `[17 Red Team, 23 Security Audit Personas]`
- final = required (5) + selected (2) = 7 методов

### Step 3: LLM-judge fallback (только если step 2 = ∅)

Если `static_rules` не добавили ни одного метода — вызывается LLM-judge:

```
prompt:
  "Scope: <slug>. Context: <PRD excerpt or recent events>.
   Available pool: <optional_methods_pool with descriptions from CSV>.
   Pick 1-2 methods most relevant. Output JSON {methods: [N,M], rationale: '...'}"
```

Это узкий single-purpose LLM call. Cost ~$0.01-0.05 per storm. Audited в events.jsonl с full reasoning.

**Когда срабатывает в реальности:** для exploratory scopes без явных signals (например, scope=`feature_dashboard-revamp` — нет security/UI/novel match).

### Step 4: User override (interactive only)

В **interactive** mode:
- После step 1-3 — system показывает финальный список: «Selected required: [...]. Selected optional via static_rules: [...]. Final 7 methods. Override? (y/n/replace)»
- User может: accept (y) / decline optional (n) / replace selection (replace with method IDs)

В **headless** mode:
- Step 4 skipped automatically
- Final selection логируется в events.jsonl
- Storm запускается без вопросов

### Headless mode — особенности

**Critical для качества:** в headless нет step 4 (user override), поэтому steps 1-3 = единственный shot.

Защитные меры:
- **Static rules — primary** (deterministic, audit-able)
- **LLM-judge — только fallback** (не primary, чтобы не зависеть от LLM в каждом случае)
- **Selection logged** в events.jsonl с полным reasoning (rule matched / LLM rationale)
- **Если headless storm падает** (LLM-judge ошибка, методы конфликтуют) → code-gate всё равно блокирует Phase 5 (artifact не создан или taxonomy-checker invalid)

### Audit format

Каждая storm session пишет в events.jsonl:
```json
{
  "event": "storm_method_selection",
  "trigger_id": "T1",
  "slug": "payment-flow",
  "mode": "headless",
  "required": [39, 11, 34, 20, 42],
  "static_rules_matched": [
    {"rule_id": 0, "rule_if": "scope contains security|auth|payment|crypto", "added": [17, 23]}
  ],
  "llm_judge_invoked": false,
  "llm_judge_picks": null,
  "user_override": null,
  "final_methods": [39, 11, 34, 20, 42, 17, 23],
  "timestamp": "<iso>"
}
```

Это даёт **полный audit trail** того что и почему выбрано в каждой сессии. Через месяц можно spot patterns («static rules слишком часто пропускают X — расширить»).

### Расширение selection_rules

Новый context signal → одна строка в `selection_rules` соответствующего scenario.md. **Closed-set с расширением через явный акт** (правило M3).

Если static rules слишком часто промахиваются (LLM-judge fallback >30% от всех storm sessions) → review правил, добавление новых rules. Триггер: monthly manifest review (§7).

### Сравнение режимов

| Mechanism | Interactive | Headless |
|---|---|---|
| Required methods | Все 5 | Все 5 |
| Static rules cascade | Полностью работает | Полностью работает |
| LLM-judge fallback | Полностью работает | Полностью работает |
| User override (step 4) | Активно — можно accept/decline/replace | **Skipped** automatically |
| Audit trail в events.jsonl | Активно | **Усилен** — full reasoning обязателен |
| Storm может провалиться | Возможен (user reject все) | Если LLM-judge crash → fallback to required-only + warning |

---

## §19. Regression & Pattern Detection (T11)

Базовый patch-counter (§5.2) ловит только grossest case: ≥5 `fix(<scope>)` в 14d. Реальные regressions часто subtler: тот же error повторяется под разными scope'ами, hot-file копится, тесты регрессируют, commit cycle. §19 закрывает эти случаи через **5 авто-детекторов** R1-R5 → запускают T11.

### R1 — Error Fingerprint Tracker

**Что:** каждый halt/error логируется как fingerprint в `~/.claude/skills/888/storm/error-fingerprints.jsonl`.

**Format:**
```json
{"ts": "<iso>", "fingerprint": "<sha1 от (file:line:exception_type:normalized_msg)>", "scope": "<scope>", "raw_error": "<truncated>", "commit_after": "<hash|null>"}
```

**Trigger:** если same fingerprint встречается ≥3 раз в 30d → emit T11 event «regression_detected: error fingerprint X».

**Where:** Stop hook OR PostToolUse on Bash (capture exit ≠ 0).

**LOC:** ~80 в `fingerprint_tracker.py`.

### R2 — Hot-file Counter

**Что:** PostToolUse on Edit|Write → инкрементирует `hot-files.json[<file>]` (sliding 14d window).

**Threshold:** >10 edits в одном файле за 14d → emit T11 «hot_file: <path>».

**Storage:** `~/.claude/skills/888/storm/hot-files.json`:
```json
{"src/runtime/sandbox.py": ["2026-05-12T...", "2026-05-13T...", ...], "...": [...]}
```

**LOC:** ~50 в `hot_files.py`.

### R3 — Commit Cycle Detector

**Что:** PostToolUse on `git commit` → сравнивает текущий commit subject с предыдущими 10 в этом scope через difflib (Python stdlib). Если ≥3 commits с similarity ≥70% в 30d → T11.

**Storage:** `commit-history.jsonl` (append-only, scope-tagged subjects).

**Limitation:** false positives на formulaic messages типа «fix(virgil): typo». Mitigation: ignore commits с body <40 chars.

**LOC:** ~60 в `cycle_detector.py`.

### R4 — Test Regression Detector

**Что:** PostToolUse on Bash matcher=`pytest|cargo test|npm test` → парсит output, extract'ит failed tests, log в `test-failures.jsonl`.

**Pattern:** если test X **failed → passed → failed** в течение 7 коммитов → T11 «test_regression: X».

**LOC:** ~80 в `test_regression.py` (output parsers для pytest/cargo/npm).

### R5 — Daily Pattern Report

**Что:** systemd timer / cron daily — анализ events.jsonl + fingerprints + hot-files + cycles за 24h. Генерация:
- `~/.claude/skills/888/storm/_storm_audit/pattern_report_<date>.md`
- Top-3 patterns с recommended T11 storm scopes

**Doesn't block:** только report. User читает, решает запускать T11 storm руками.

**LOC:** ~150 в `pattern_report.py`.

### T11 storm — scenario contract

`scenarios/T11_regression_detected.md`:
```yaml
trigger_id: T11
trigger_name: regression_detected
detection: automatic via R1-R5 (NOT keyword-based)
required_artifact: spec/regression_<fingerprint_hash>_storm.md
required_methods: [40, 35, 36, 50]  # 5 Whys + FMA + Devil's Advocate + Lessons Learned
optional_methods_pool: [39, 17, 11]   # First Principles, Red Team, Tree of Thoughts
selection_rules:
  - if: scope содержит "security|auth|crypto"
    add: [17]
  - if: ≥3 occurrences in test code
    add: [35]  # extra FMA для test infrastructure
selection_fallback: llm_judge_picks_1_to_2
max_optional_selected: 2
block_until: artifact_exists AND user_acknowledged
closes_gap: [P1 — reactive-patching, subtle regressions]
```

### Integration с existing hooks

| Hook | Что добавляется в S2 для §19 |
|---|---|
| `patch-counter.sh` | + invoke `cycle_detector.py` after counter increment |
| `code-gate.sh` | + check T11 active for scope: если есть regression_<hash>_storm.md pending → block until acknowledged |
| `audit-trail.sh` (Stop) | + invoke `pattern_report.py` если ≥24h с последнего report'а |
| **NEW** `error-trap.sh` | PostToolUse on Bash exit ≠ 0 → invoke `fingerprint_tracker.py` |
| **NEW** `test-result.sh` | PostToolUse on Bash matcher=`pytest|cargo test|npm test` → invoke `test_regression.py` |

### Effect on S1-S5 plan

| Session | Add to existing scope |
|---|---|
| **S1** | +1 hook: `error-trap.sh` (~30 LOC). Stub только; реальная имплементация in S2. |
| **S2** | +5 Python модулей: fingerprint_tracker, hot_files, cycle_detector, test_regression, pattern_report (~420 LOC total). +1 hook test-result.sh. |
| **S3** | NO change (embedded skills уже покрывают) |
| **S4** | +1 scenario: T11_regression_detected.md |
| **S5** | +regression test scenario: симулировать 3 same fingerprints → T11 должен trigger automatically, code-gate должен block |

**Total addition:** ~600 LOC. **+1 hook**. **+1 scenario**. **+1 trigger T11**. **+1 session estimate** (S2 теперь ~3 сессии вместо 2).

**Updated total:** S1-S5 = ~11-16 часов (было 10-15), 6 коммитов (было 5).

### Real-world пример (NEW-26 reconstruction)

Если бы §19 был активен **до** реального NEW-26 в Virgil:

| Real timeline | С §19 |
|---|---|
| Commit 1: fix(review-runner): isolated_home | R1 logs fingerprint A (stdin_blocked) |
| Commit 2: fix(review-runner): EOF on stdin | R1: same fingerprint A → counter=2 |
| Commit 3: fix(review-runner): subprocess pty | R1: counter=3 → **T11 fires** + R3 cycle detector matches «fix(review-runner): ...» similarity ≥70% |
| LLM пытается 4-й fix | code-gate **BLOCKS** до `spec/regression_<A>_storm.md` |
| Storm проходит: 5 Whys → root cause = «review-skill интерактивен, не subprocess pty issue» | Real fix через workaround = directive-prompt (то что в итоге сработало) |
| ROI: ~3 wasted sessions сэкономлены |

Это **точно** то что произошло (см. memory `project_milestone_new26_27_skill_isolation`). С §19 — поймали бы на 3-м commit'е.
