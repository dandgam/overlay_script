---
name: intent-router
description: Parse free-text Russian/English messages from Telegram, route to tool calls or clarification. Activates on every user_chat_message event (chat mode, spec §15).
---

# intent-router skill

## Когда активируется

- Event `user_chat_message` (свободный текст в Telegram)
- Не slash commands — те идут напрямую в handlers

## Disambiguation rules (spec §17)

1. **Read-only ops** (status, show, list, explain) — выполняй сразу, не подтверждай
2. **Destructive ops** (stop, kill, delete, rollback, push) — ВСЕГДА inline button confirmation
3. **Action ops** (start, spawn, set, schedule) — выполняй, но покажи параметры до выполнения
4. **Ambiguous** — один уточняющий вопрос, не предполагай
5. **Unknown** — «не знаю» + предложи близкие варианты

## Examples (few-shot embedded in system prompt)

```
USER: запусти одиссей 1a
→ start_wave(project="odyssey", wave="1a", max_parallel=2)

USER: что 1.8a делает
→ tail_worker_jsonl(worktree="odyssey-wt-1", n=20)

USER: останови
→ "Уточни: graceful (дождаться workers) или hard kill?"
   [inline buttons: graceful | hard]

USER: дорого
→ "Раскладка: planner=opus, reviewer=opus, dev=sonnet, routine=sonnet.
   Перевести routine на haiku? ~30% экономии."
```

## PII handling

- Input уже scrubbed in `bot/pii_detector.py:scrub_input`
- Если pii_found_flag=True → агент аккуратнее формулирует ответ, не повторяет PII в response

## Tools

- Все 22 tools (через tool-search-tool beta — full schema подгружается по запросу)
