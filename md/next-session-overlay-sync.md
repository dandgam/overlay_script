# Overlay-sync — хэндофф в следующую сессию (2026-06-17)

> Открой это первым сообщением. Авто-память: `project_overlay_sync_build_2026-06-17.md` (детальнее).
> Инструмент: `tools/overlay_sync.py` + `tests/test_overlay_sync.py`. План: `~/.claude/plans/witty-sauteeing-blanket.md`.

## Старт следующей сессии
```bash
cd ~/bmad-orchestrator && git switch feat/overlay-sync
.venv/bin/python -m pytest tests/test_overlay_sync.py -q      # ожидать 34 passed
.venv/bin/python tools/overlay_sync.py --exempt ~/.claude/bmad-overlays/exempt.yaml check   # 0 error / 4 warn
```

## Что инструмент уже умеет (Этапы 1 → 2b + категория-3)
```
капать (capture)      odyssey → vault     overlays + forks + own-skill
раздавать (consume)   vault/odyssey → проекты:
   ├─ check        ДИФФ дрейфа по 4 проектам
   ├─ propagate    КОПИЯ odyssey→targets (CREATE/REPLACE, exempt-aware)
   ├─ init-project  bootstrap нового проекта из vault (idempotent, never-clobber, exit-6)
   └─ post-upgrade  заново наложить форк-патчи на новый upstream после re-vendor (3-way, exit-6)
```
База: 34 теста зелёные · ruff + mypy strict чисто · `check` = 0 error / 4 warn (warn = by-design exempt).

## Что отгружено ЭТОЙ сессией
| Репо | Коммиты |
|---|---|
| orchestrator (`feat/overlay-sync`) | `97a3c9d` 2b consume · `a80779a` skill→watched · `bedb1f4` skill→vault · `840bd4d` presence-exempt · `c0e6c39` propagate ROLLBACK-fix |
| odyssey | `d264c10` миграция оверлеев на имена 6.8 · `45d2b3b` methods.csv 51→70 |
| vault (`~/.claude/bmad-overlays`) | `bc6d30e` 14 overlays + 4 forks + 12 skill-files |
| legal / pcb | `58ce4c5` / `304e192` скилл bmad-auto-dev (Antares — `.claude/` gitignored, на диске) |

**Скилл `bmad-auto-dev` раскатан** во все 4 проекта (md5 идентичен). MF-9 reconcile ОТМЕНЁН: odyssey = эталон, global вне скоупа.

## Осталось доделать (по приоритету)

1. **🔴 methods.csv + brain-methods.csv в vault (re-vendor дыра).** Оба CSV — НАШИ расширения (methods.csv 69 методов; brain-methods.csv 61 техника/10 кат — **проверить, наш ли это форк vs upstream 6.8**), но `capture` их в vault НЕ кладёт (они только `watched`). Коммит спас от `git clean`, но **BMAD re-vendor 6.9 перезатрёт upstream-версией → потеряем расширения.** Решение: завести оба CSV vault-артефактами как форки (pinned upstream + patch/snapshot + канарейка), чтобы `post-upgrade` восстанавливал. Сначала сверить с upstream 6.8 (`DEFAULT_UPSTREAM` в коде).

2. **🟡 Таргеты имеют СВОИ незакоммиченные миграции оверлеев** — pcb 18 путей, Antares 14, legal 1 (ШИРЕ нашей миграции). Каждый проект сам, **НЕ свипать вслепую**. Если синхронизировать — отдельный per-project разбор.

3. **🟡 freshness-арбитр** — `propagate` запрещён пока odyssey впереди vault (сначала `capture`). Не реализован.

4. **⬜ Этап 3 (за golden-гейтом, ПОСЛЕДНИМ):** brain-канарейка (csv.reader не wc-l; split по em-dash U+2014; «N techniques across M categories»; file-aware breakdown=02a) + golden-fixtures + словарь алиасов фантомов (Context Review→#23 · Devil's Advocate→#59 · Edge Case Hunter→#69) + `report`/regen канваса.

## Ключевые развязки (чтобы не путать)
- **НЕ путать 2 сущности:** overlay `_bmad/custom/bmad-auto-dev.toml` (прод-правила crm/oodyssey.ru) = odyssey-only **exempt**; скилл-дир `.claude/skills/bmad-auto-dev/` = **эталон во все проекты**.
- **2 разных CSV:** `brain-methods.csv` (скилл brainstorming, 61 техника/10 кат, 3 кол.) ≠ `methods.csv` (скилл advanced-elicitation, 69 методов/12 кат, 5 кол.).
- **Форк был на step-02 (ПРОЗА), не на CSV:** step-02 писал «36 техник/7 кат» (врал), brain-methods.csv = 61/10 (верно) → форкнули ОПИСАНИЕ, чтобы совпало с данными.
- **`fork` (в vault, переживает re-vendor) ≠ `watched` (только сверка между проектами, re-vendor НЕ переживает).**
- **post-upgrade на конфликт = СТОП exit-6, дерево не тронуто** (строгий `git apply`, без fuzzy). Если 6.9 сам починит нашу строку → патч reject → conflict → ты выкидываешь форк. **Авто-различение «upstream догнал нас» vs «изменил иначе» даст brain-канарейка (Этап 3).**
- **Глобальные флаги (`--vault`/`--exempt`/`--root`) идут ДО подкоманды** (argparse).
- **all-CREATE propagate** требует `mkdir backup_dir` перед ROLLBACK.json (починено `c0e6c39`).
