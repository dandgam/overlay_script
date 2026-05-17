# Embedded BMad Skills

Каноническая копия BMad phase 4+5 skills + механизмы кастомизации без потери upgrade.

## Структура

```
skills/
├── upstream/       Pristine copies (read-only с точки зрения dev workflow)
│   ├── bmad-auto-dev/
│   ├── bmad-dev-story/
│   ├── bmad-code-review/
│   ├── ...                  (14 skills total — см. .bmad-version)
│   └── .bmad-version        Source SHA + date + canonical path
├── customize/      Per-skill TOML overrides (E2 — overlays поверх upstream)
├── policy/         YAML configs (gates, cost, retry — auto-tuned в E5/E6)
├── lessons/        Retrospective output (filled at runtime в E8)
├── patches/        Code patches re-applied поверх upstream upgrades (E4)
└── README.md       This file
```

## Принципы (skills-as-data)

1. **Upstream pristine.** `upstream/` НЕ редактируется руками. Содержимое = exact copy из canonical source (см. `.bmad-version`).
2. **Customize through overlay.** Любое изменение поведения skill = TOML override в `customize/<skill>.customize.toml`. Loader накладывает override поверх upstream's `SKILL.md` (E2).
3. **Policy as data.** Code-review gates, cost thresholds, retry limits = YAML в `policy/`. Auto-tuned через L2 live tuning (E6).
4. **Patches survive upgrade.** Если нужна модификация кода skill — `patches/<name>.diff`. CLI `bmad-orchestrator skill-update` повторно применяет patches после pull upstream (E4).
5. **Lessons → proposals.** Retrospective output идёт в `lessons/<project>/wave-<N>.md`. Парсер генерит policy proposals (E8) → user approve'ит → policy YAML updates.

## Upgrade workflow (E4 — implemented in later session)

```
bmad-orchestrator skill-update --source <path-or-url>
  ├─ Pull latest upstream → diff vs current upstream/
  ├─ Re-apply patches/*.diff (conflict → exit 1 + report)
  ├─ customize/, policy/, lessons/ untouched
  └─ Update .bmad-version
```

Default = dry-run; добавь `--apply` для write.

## Worker spawn (E3 — implemented in later session)

При спавне worker'а `runtime/worker_spawn.py` копирует `upstream/` + apply `customize/` overrides в `<worktree>/.claude/skills/` перед launch. Project's main checkout НЕ модифицируется.

## Source

Skills скопированы из `/home/server/odyssey/.claude/skills/` — canonical reference repo для BMad-методологии. См. `upstream/.bmad-version` для exact SHA.

## Initiative trail

- **E1** (2026-05-17): этот scaffolding + 14 skills copied.
- **E2..E9**: см. `spec/spec_embed_phase45_with_selflearning.md`.
