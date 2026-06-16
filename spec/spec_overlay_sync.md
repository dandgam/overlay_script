# spec: bmad-overlay-sync — validate → compare → propagate → re-validate

> Источник: ультракод-анализ 2026-06-16 (workflow `overlay-fork-sync-analysis`, 11 агентов, ~564k токенов).
> Канон = **odyssey**. Проекты: odyssey, legal, pcb, Antares.
> Цель: машинно сверять и синхронизировать overlays/forks по 5 сущностям (skills · anchors · forks · unique methods · method calls) и не дать дрейфу вернуться.

---

## 1. Реальный дрейф, найденный сегодня (verified)

### 1.1 Сверка счётчиков (канвас ЗАЯВЛЯЕТ vs РЕАЛЬНОСТЬ из файлов)

| Метрика | Канвас | Реальность | Совпало | Причина расхождения |
|---|---|---|---|---|
| anchors | 53 | 53 | ✅ | — (канвас честен по якорям) |
| skills | 13 | 14 | ❌ | канвас не учитывает `bmad-auto-dev.toml` (0-anchor guard-overlay, только odyssey) |
| method_calls | 88 | 90 | ❌ | СМЕШАНО: реальный пропуск `Red Team vs Blue Team` в канвасе + артефакт парсера (двойной счёт `Context Review`/`Code Review Gauntlet`) |
| unique_methods | 32 | 33 | ❌ | то же |

⚠️ Числа 90/33 **сами под подозрением** — парсер, которым их считали, провально работает на реальных данных (см. §4). Твёрдо доверять можно только `anchors 53=53` (через byte-md5, без парсинга) и `skills 13 vs 14`.

### 1.2 🔴 Фантомные вызовы методов (ТОТ ЖЕ КЛАСС, что brain-methods)

Вызов метода в overlay ссылается на имя, которого НЕТ в `methods.csv` (каталог 69 методов). Проверено grep'ом (0 hits):

| Фантом в overlay | Реальный метод в каталоге | Где вызывается | Природа |
|---|---|---|---|
| `Devil's Advocate` | `Challenge from Critical Perspective` (#59) | product-brief, check-readiness, retrospective | НАСТОЯЩИЙ фантом (имя не из каталога) |
| `Edge Case Hunter` | `Boundary & Edge Case Sweep` (#69) | create-epics, prd | ссылка на СКИЛЛ, не метод каталога |
| `Context Review` | `Code Review Gauntlet` (#19/#23) | create-architecture, create-story, ux | **намеренный алиас** (адаптация: ревью story-файла, НЕ кода) |

Нормализуемые (НЕ фантомы, 1:1 в каталог — чинить = нормализовать, не флажить): `First Principles`→`First Principles Analysis`, `What If`→`What If Scenarios`, `SCAMPER`→`SCAMPER Method`.

### 1.3 Cross-project дрейф

| Артефакт | Состояние | Чинить? |
|---|---|---|
| `_bmad/custom/bmad-auto-dev.toml` | только odyssey; нет в legal/pcb/Antares | **Вероятно by-design** (auto-dev — только odyssey). Нужен exempt. |
| `_bmad/custom/bmad-create-story.toml` | Antares STALE (1619 B) vs канон (2331 B) | Канвас уже метит 🟰 «есть-но-иное» → **by-design**, в exempt |
| 14 прочих overlay + methods.csv + brain-methods.csv | байт-в-байт во всех 4 | ✅ чисто |

### 1.4 Форки (verified: ровно 4, единственные на 44 скилла)

`bmad-brainstorming/steps/step-02a..d.md` — fix Parse 7→3 (наш сегодняшний). Байт-в-байт во всех 4 проектах. **РИСК: живут только в uncommitted working-tree (HEAD == чистый upstream 6.8.0) → re-vendoring 6.9 затрёт молча**, если не переприменить или не вынести в overlay.

---

## 2. Дизайн скрипта (синтез 3 независимых проектов)

`bmad-overlay-sync` — Python 3.11+ stdlib CLI, `tools/overlay_sync.py`. Канон = odyssey (никогда не цель записи). Бэкбон = **реестр инвариантов** (каждый класс бага = именованная функция `INV-*`), питается **манифестом**, который генерится из файлов (счётчики — чистая функция канона, не руками). Канвас = **выход, не вход**.

### Команды
```
check        STEP 1+2: построить манифест, прогнать инварианты, отчёт. Без записи. (default)
propagate    STEP 3: канон → цели. Запись только с --apply (иначе dry-run + diff).
revalidate   STEP 4: заново с диска, повторить инварианты, проверить сходимость.
sync         check→propagate→revalidate (CI; auto-rollback при регрессе).
report       перегенерировать .canvas из манифеста (канвас = output).
rollback     восстановить из бэкапа.
```
Дефолт всех пишущих команд = `--dry-run`. Безопасность: атомарная запись (`tmp+os.replace`), per-file бэкап + `ROLLBACK.json`, md5-проверка каждой записи, auto-rollback при регрессе.

### Инварианты (каждый = `fn(manifest, exempt) -> [Violation]`)
| ID | Что проверяет | Severity | Класс бага |
|---|---|---|---|
| INV-PHANTOM-CALL | каждый вызов ∈ methods.csv (после нормализации/алиасов) | ERROR | фантомный вызов |
| INV-BRAIN-COLS | brain-methods.csv = 3 колонки, форки ссылаются только на них | ERROR | brain-methods column-drift |
| INV-COUNT-RECON | rollups(файлы) == canvas_claimed | ERROR | сверка счётчиков |
| INV-OVERLAY-PRESENT | каждый overlay канона есть во всех проектах | ERROR, fixable | пропавший overlay |
| INV-OVERLAY-IDENTICAL | md5 равен по проектам (кроме exempt) | ERROR, fixable | byte-дрейф |
| INV-CANVAS-COVERAGE | каждый overlay (вкл. 0-anchor) есть в канвасе и наоборот | ERROR | orphan / missing node |
| INV-FORK-PERSIST | форк стабилен; WARN если uncommitted-only | WARN | потеря форка на re-vendor |

Exit codes: 0 ok · 1 WARN · 2 ERROR · 3 propagate-revalidate упал (откатился) · 4 канвас устарел · 5 internal.

### План постройки (фазами)
1. **Манифест (read-only)** — парсинг overlay/anchor/call + methods.csv каталог + форк-md5 + git-HEAD. Уже валидирует структуру.
2. **Батарея инвариантов + `check`** — CI может принять здесь.
3. **propagate dry-run** — план + diff + guards.
4. **`--apply` + backup + атомарная запись + rollback**.
5. **revalidate + sync + auto-rollback**.
6. **report (regen канваса)** — последним; канвас перестаёт врать.

---

## 3. ⚠️ Adversarial вердикт (что СЛОМАЕТСЯ — проверено на реальных данных)

Скелет здоровый (canonical=odyssey, dry-run, atomic+backup+md5, byte-инварианты ловят cross-project дрейф и brainstorm-форк БЕЗ парсинга). НО текстовый парсинг провально ломается:

- **🔴 BLOCKER 1 — anchor-regex ловит 13/53.** `## <section>` требует `##`, но 40 якорей «HOT STEP» пишут `Step N (Name):` без `##` → пропущены. INV-COUNT-RECON выдал бы ложный ERROR 13 vs 53.
- **🔴 BLOCKER 2 — апостроф рвёт флагманский фантом.** `re.findall(r"'([^']+)'")` на `'Devil's Advocate'` даёт `['Devil', ...]` → имя метода НИКОГДА не извлекается целиком; любой метод с апострофом прячется.
- **🔴 BLOCKER 3 — кавычки в прозе = фейковые методы.** В реальных якорях ~18 русских фраз в одинарных кавычках (`'почему'`, `'уже поздно'`) → ловятся как «фантомы» → ложные ERROR + раздувают счётчики.
- **🟠 CSV-conflation** — дизайн путает `methods.csv` (5 колонок, каталог методов) и `brain-methods.csv` (3 колонки, техники). Разные файлы.
- **🟠 propagate небезопасен на форках** — 4 форка uncommitted-only; дизайн даёт лишь WARN (не block) → по твоей же canary-doctrine это «отложенная потеря».
- **🟠 алиас-rewrite меняет СМЫСЛ** — авто-замена `'Context Review'→'Code Review Gauntlet'` в каноне = ревью story превращается в ревью кода. Должно быть INFO/exempt, НИКОГДА не auto-fix.

**Главный фикс до постройки:** Фаза 1 = жёсткий гейт. Собрать golden-fixture из реальных 53 якорей и сырых токенов; НЕ пускать ни один parsing-инвариант, пока парсер не воспроизведёт его точно (HOT STEP `Step N (Name):`, имена с апострофом, форма `вызови ... с методом '...'`, отбрасывая кавычки-в-прозе).

→ Урок: наивный фантом-детектор сам стал бы «врущей канарейкой» — тот же класс, что мы лечим. byte-md5 инварианты надёжны без парсинга; текстовые — только после golden-gate.
