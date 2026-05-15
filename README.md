# bmad-orchestrator

Автономный оркестратор BMad Phase 4 (Implementation) — читает `_bmad/` целевого проекта, строит DAG зависимостей stories, запускает параллельных worker'ов в git-worktree'ах, прогоняет каждую story через цикл `create-story → dev-story → code-review`, мержит готовое в integration-ветку.

## Назначение

- **Цель:** превратить BMad Phase 4 из ручного «один story за раз» в autonomous pipeline с параллелизацией и human checkpoints.
- **Не цель:** заменить Phase 1-3 (Analysis / Planning / Solutioning) — те остаются guided workflow с человеком в петле.

## Stack

- Python 3.11+
- [Anthropic Python SDK](https://github.com/anthropics/anthropic-sdk-python) — прямые вызовы Claude API (DAG-планировщик, elicitation engine, merge gate)
- [Claude Agent SDK (Python)](https://github.com/anthropics/claude-agent-sdk-python) — для worker'ов, которым нужен полный tool-harness (Read/Edit/Bash/Grep/Glob)
- `git worktree` для изоляции параллельных stories
- `networkx` для DAG manipulation
- `pydantic` для контрактов данных
- `typer` + `rich` для CLI

## Быстрый старт

> ⚠️ В разработке. Сейчас только scaffold + spec. Реальный код — после pilot run на Odyssey Wave 1a.

```bash
# (планируется)
bmad-orchestrator run --project /home/server/odyssey-ux --wave 1a --max-parallel 3
```

## Документы

- [spec/spec_master_orchestrator.md](spec/spec_master_orchestrator.md) — детальная архитектурная спека
- [examples/elicitation-policy.example.yaml](examples/elicitation-policy.example.yaml) — пример policy YAML

## Связанные проекты

- `/home/server/odyssey-ux/` — первый target project (Odyssey SaaS экосистема). BMad Phase 1-3 завершены, Phase 4 — pilot для оркестратора.
- `/home/server/crm/` — потенциальный второй target (single-tenant CRM, AABIT).

## Лицензия

TBD (private).
