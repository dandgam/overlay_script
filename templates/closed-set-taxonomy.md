# Closed-Set Taxonomy Template — YAML + python3 stdlib parser

**Q-260526-ICST** · extracted from Q-260526-ISOL (improver §4dw F6-L2) · 2026-05-28

> Pattern для bash-tool'ов, которые принимают решение по closed-set правил без
> внешних зависимостей (нет `yq`, `jq`, нет pip packages). YAML — для
> human-readable rule file, **python3 stdlib `re`** — для парсинга, чистый bash
> — для оркестрации/audit/exit codes.

## Когда применять

Применяй когда выполнены **все** условия:

1. **Closed-set правила.** Конечный enumerable набор условий (5-20 правил, не
   open-ended regex/ML). Каждое правило = простой dict вида
   `{key: value, ...}`. Никаких vinegar-nested структур.
2. **First-match-wins** или **all-must-match** семантика. Не нужна полная
   YAML-семантика (anchors, multi-line scalars, type coercion).
3. **No-deps requirement.** Инструмент устанавливается на чистый Linux/macOS,
   нельзя требовать `apt install yq` или `pip install pyyaml`. Только
   `bash`, `python3` (stdlib), `awk`, `grep`.
4. **Audit trail обязателен.** Решение нужно логировать в append-only журнал
   (audit-log.jsonl или similar) с timestamp + rule hit + inputs.

## Когда **НЕ** применять

- Open-ended user input → нужна real схема и validation (используй `pyyaml` + Pydantic).
- Глубоко-nested config (≥3 уровня) → embedded parser станет хрупким, переходи на `pyyaml` опционально.
- Высокая частота вызова (>100/sec) → накладные расходы fork python3, переходи на чистый Python entry-point.

## Reference implementation

| Файл | Роль |
|---|---|
| `~/.claude/skills/888/config/session-isolation.yaml` | YAML — closed-set rules + defaults |
| `~/.claude/skills/888/scripts/session-isolation.sh` | bash orchestrator + python3 inline parser |
| `~/.claude/skills/888/tests/test_session_isolation_red.bats` | bats тесты T-ISOL-1..4 |

## Скелет YAML (taxonomy file)

```yaml
# <feature>.yaml — Q-NNN-XXX
# Closed-set taxonomy for <decision>.
#
# Spec: spec_<feature>.md §N
# Author: <persona> Phase <N.M> (YYYY-MM-DD)
#
# Decision values:
#   <value-A>    — <when applied>
#   <value-B>    — <safe default>
#
# Rules evaluated top-down in <primary_section> first.
# First matching rule wins and returns <value-A>.
# If no rule matches → <value-B> (safe default).

version: 1

rules:
  <primary_section>:        # e.g. fresh_claude_p_required
    - <key-1>: <value-1>
      reason: "human-readable why"

    - <key-1>: <value-2>
      <key-2>: <value-3>
      reason: "compound rule"

  <fallback_section>:       # e.g. current_session_ok (optional, documentation-only)
    - <key-1>: <value-4>

defaults:
  <unknown_input_1>: <safe-value>
  <unknown_input_2>: <safe-value>
```

**Соглашения:**

- `version: 1` обязателен — bump при breaking change (rule key rename, semantics flip).
- Каждое правило **минимум один key + `reason`**. `reason` идёт в audit log.
- 2-space indent. NO tabs. Strings без кавычек если возможно (parser strip их всё равно).
- Inline комментарии (`key: value  # comment`) — допустимы, parser удалит.
- Никаких anchors (`&`, `*`), multi-line strings (`|`, `>`), references.

## Bash + python3 parser — copy reference, не пиши с нуля

Полная reference implementation в `~/.claude/skills/888/scripts/session-isolation.sh`.
Скопируй и адаптируй — НЕ пиши парсер с нуля каждый раз.

**Структура скрипта (для адаптации):**

| Строки в reference | Что делает | Что менять |
|---|---|---|
| `1-40` | Header + usage doc | Имя файла, аргументы, Q-NNN |
| `41-105` | Arg parsing + validation, exit 5 on misuse | Список аргументов |
| `106-125` | Config fallback (exit 3 if missing) | Путь к config |
| `127-265` | Inline python3 parser (`parse_section`, `parse_defaults`, rule engine) | Имена rule keys, decision values |
| `267-290` | `--decide` / `--apply` mode handling | Audit log path |

**Парсер закрыт на структуру:**

```yaml
rules:
  <primary_section>: [ - {key: val, reason: "..."} ]
  <fallback_section>: [ - {key: val} ]
defaults: { key: val }
```

Никаких anchors, multi-line strings, nested >2 уровней. Если нужна более
сложная схема — берёт `pyyaml`, не extend этот парсер.

## Exit code convention

| Code | Meaning |
|---|---|
| `0` | Decision emitted (or apply succeeded) |
| `2` | Lock contention (flock failed) |
| `3` | Config missing → defaulted (warning on stderr) |
| `5` | Misuse — bad args, invalid input values |

## Audit log shape (when `--apply` provided)

Append-only JSONL line:

```json
{"ts":"2026-05-28T14:00:00+00:00","tool":"<feature>","inputs":{"a":"X","b":"Y"},"decision":"<value-A>","rule_hit":"<reason>","script_version":"1.0.0"}
```

## Testing rubric (bats)

Minimum 4 tests per closed-set taxonomy:

1. **T-1 happy:** primary rule matches → expected decision.
2. **T-2 fallback:** no rule matches → safe default.
3. **T-3 missing config:** config file absent → exit 3 + safe default.
4. **T-4 misuse:** invalid input → exit 5 with diagnostic on stderr.

Если есть `--apply` режим — добавь **T-5: audit log append + idempotency check**
(повторный `--apply` с теми же inputs не дублирует запись или дублирует
sentinel'но — задокументируй явно, не оставляй как "verification debt").

## Anti-patterns

- ❌ `eval $(grep ... config.yaml)` — code injection если config user-writable.
- ❌ `awk '/key:/ {print $2}'` — ломается на quoted values + inline comments.
- ❌ python3 без `flush=True` на print — bash перехватит пустую строку при `set -e` race.
- ❌ Ловить exception молча → парсер должен либо вернуть safe default + WARN на stderr, либо exit 3.
- ❌ Закладывать в YAML колоночные данные / table-like структуры — taxonomy = rule list, не data store.

## Related Q-NNN

- Q-260526-ISOL — reference implementation (session-isolation taxonomy).
- Q-260526-IAPL — `--apply` mode test (idempotency).
- build-discipline rule #5 (closed-set enumeration before code).

## Changelog

- 2026-05-28 — v1.0 initial template extracted from Q-260526-ISOL retro F6-L2.
