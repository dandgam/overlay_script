# CLAUDE.md — bmad-orchestrator

> Автономный оркестратор BMad Phase 4 для проектов с `_bmad/` структурой.
> Greenfield Python repo. Цель — pilot на `/home/server/odyssey-ux/` Wave 1a.

## Project Overview

**bmad-orchestrator** — отдельный Python-инструмент, читает `_bmad/planning-artifacts/` целевого проекта и автоматизирует Phase 4 (Implementation):

- Строит DAG зависимостей stories внутри эпиков
- Определяет какие stories можно гнать параллельно (нет shared files)
- Спавнит N параллельных `claude -p` worker'ов в `git worktree` копиях
- Каждый worker запускает `/bmad-auto-dev` или `/bmad-dev-story` на одной story
- После завершения — `bmad-code-review` gate перед merge в `integration/<wave>` ветку
- Human checkpoint каждые 10 stories или на Wave-границе
- Elicitation policy engine — 80% решений автоматом, 20% эскалация пользователю (Telegram/email)

**Не делает:** Phase 1-3 (Analysis / Planning / Solutioning) — те остаются guided.

## Critical Boundaries

1. **Target проекты — read-only для metadata, write-only через worktree.** Оркестратор НЕ пишет напрямую в target's main-branch. Только через worktree → branch → merge gate → integration.
2. **`/home/server/crm/` и `/home/server/odyssey-ux/main` — никогда напрямую.** Все правки через ветку.
3. **Cost budget hard-cap.** Оркестратор остановится при превышении дневного лимита токенов.
4. **Никогда `--no-verify`, `git push --force`, `git reset --hard` без explicit human approval.**

## Stack

- **Python 3.11+** (для match statements, exception groups, claude-agent-sdk совместимости)
- **Anthropic SDK** — DAG planner, elicitation engine, merge decisions (Opus 4.7 для архитектурных решений)
- **Claude Agent SDK (Python)** — worker'ы с полным tool harness (Sonnet 4.6 для имплементации)
- **networkx** — DAG operations
- **pydantic v2** — data contracts
- **pyyaml** — policy configs
- **typer + rich** — CLI
- **pytest + ruff + mypy** — quality gates

## Where Things Live

| Что | Где |
|---|---|
| Архитектурная спека | `spec/spec_master_orchestrator.md` |
| Пример elicitation policy | `examples/elicitation-policy.example.yaml` |
| Исходники | `src/bmad_orchestrator/` |
| Тесты | `tests/` |
| Memory Bank (per worktree) | `.claude/memory/` (gitignored) |

## Working Style

- **Автономно.** Выбирай лучшее решение, не спрашивай между вариантами.
- **Не выдумывай.** Если не ≥99% уверен — «не знаю», проверь через `context7` (Anthropic SDK docs) или WebFetch.
- **Output style:** plain language (пользователь не разработчик).
- **Skill discipline:** при вызове `/skill-name` первым действием `Read .claude/skills/<name>/SKILL.md`.
- **Anthropic SDK + Claude Agent SDK** — всегда с **prompt caching** включённым. Cache hit rate <50% = bug.
- **Python:** type hints обязательны (3.11+), `async def` для всех I/O, `ruff check` ДО коммита.

## Commit Discipline

- 1 задача = 1 commit
- Doc updates в **том же** коммите что и код
- Никогда `--amend` после hook reject
- Никогда `--no-verify` без явной просьбы
- Commit message на русском, формат: `<type>(<scope>): <описание>`

Типы: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `infra`.
Скоупы: `dag`, `worker`, `merge-gate`, `policy`, `cli`, `sdk`, `spec`.

## Status

- ✅ Scaffold + spec готов (2026-05-15)
- ⬜ Pilot run на Odyssey Wave 1a через `/bmad-auto-dev` (без оркестратора)
- ⬜ Сбор policy data из pilot logs
- ⬜ MVP оркестратора (DAG planner + sequential worker)
- ⬜ Parallel worker pool
- ⬜ Production run на Odyssey Wave 1b

## Pointers to Read on Demand

- Architecture spec: `spec/spec_master_orchestrator.md`
- Target project (Odyssey) BMad artifacts: `/home/server/odyssey-ux/_bmad/planning-artifacts/`
- Target project epics: `/home/server/odyssey-ux/_bmad/planning-artifacts/epics.md` (16 эпиков, 133 FR)
- Существующий `bmad-auto-dev` skill: `~/.claude/skills/bmad-auto-dev/` (если установлен глобально) или в target `.claude/skills/`

## Memory Bank Protocol

В начале сессии прочесть `.claude/memory/activeContext.md` и `progress.md` если есть. В конце — обновить. Hook `init_memory_bank.sh` создаёт автоматически.
