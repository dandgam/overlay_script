# CLAUDE.md — bmad-orchestrator

> Автономный оркестратор BMad Phase 4 для проектов с `_bmad/` структурой.
> Greenfield Python repo. **Project-agnostic** — работает с любым target BMad-проектом через `--project-root`.

## Project Overview

**bmad-orchestrator** — отдельный Python-инструмент, читает `_bmad-output/planning-artifacts/` целевого проекта и автоматизирует Phase 4 (Implementation):

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
2. **Main-ветка target проекта — никогда напрямую.** Все правки через worktree → branch → merge gate → integration.
3. **Cost budget hard-cap.** Оркестратор остановится при превышении дневного лимита токенов.
4. **Никогда `--no-verify`, `git push --force`, `git reset --hard` без explicit human approval.**
5. **Worker isolation — primary safety = OS-level sandbox** (`runtime/sandbox.py`, bwrap). `_scan_bash` (`agent/safety/hooks.py`) — **defence-in-depth, не primary**. Новые bash bypass'ы НЕ добавлять как patterns в scanner; fix в sandbox если bypass'ит изоляцию. Prod prerequisite: `apt install bubblewrap` (отсутствует → `NoSandbox` fallback + loud audit warning, primary safety теряется). См. `spec/spec_orchestrator_agent.md` §22.7.

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
- ✅ MVP оркестратора (DAG planner + sequential worker)
- ✅ Parallel worker pool (parallelism_initiatives merged `6e8a18e`)
- ✅ Pilot 1/2 success (Stories 1.1 / 1.2 end-to-end + autofix loop)
- 🟡 Eval suite — Step A done (5 synthetic mock 100% PASS), Step B = реальные BMad stories из любого target проекта
- ⬜ Production run на любом target BMad-проекте

## Pointers to Read on Demand

- Architecture spec: `spec/spec_master_orchestrator.md`
- Target project BMad artifacts: `<project-root>/_bmad-output/planning-artifacts/` (путь = `--project-root` при запуске)
- Target project epics: `<project-root>/_bmad-output/planning-artifacts/epics.md`
- Существующий `bmad-auto-dev` skill: `~/.claude/skills/bmad-auto-dev/` (если установлен глобально) или в target `.claude/skills/`

## Memory Bank Protocol

В начале сессии прочесть `.claude/memory/activeContext.md` и `progress.md` если есть. В конце — обновить. Hook `init_memory_bank.sh` создаёт автоматически.
