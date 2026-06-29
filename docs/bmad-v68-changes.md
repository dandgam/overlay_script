Документ создан: `/home/server/bmad-orchestrator/docs/bmad-v68-changes.md`

# BMAD METHOD: v6.2.2 → v6.8.0 — карта изменений

> **От:** v6.2.2 (2026-03-25) — твоя точка отсчёта, к ней привязаны 5 doc-файлов раскопок.
> **До:** v6.8.0 (2026-05-25). Семь релизов за два месяца.
> **Дата сборки карты:** 2026-06-10.
> **Зачем:** понять ЧТО изменилось и какие из твоих старых знаний ещё в силе.

Три системных сдвига между версиями: **(а)** консолидация персон в Amelia (v6.3.0), **(б)** TOML-кастомизация всего метода (v6.4.0), **(в)** переписывание планировочных скиллов из step-файлов в conversational SKILL.md (v6.7.0 + v6.8.0). Phase 1-2 переписаны полностью; Phase 3-4 структурно почти не тронуты.

---

## 1. Шкала версий

| Версия | Дата | Headline | Breaking |
|---|---|---|---|
| **v6.2.2** | 2026-03-25 | твоя точка отсчёта; module-help CSV → 13 колонок, bmad-help outcome-based | нет |
| **v6.3.0** | 2026-04-09 | консолидация персон в Amelia; +bmad-prfaq, +bmad-checkpoint-preview; marketplace модулей | удалены `bmad-init`, `spec-wip.md` singleton; слиты Barry/Quinn/Bob; custom-content → marketplace |
| **v6.4.0** | 2026-04-24 | **TOML-кастомизация** (`_bmad/custom/`); стабильный + bleeding-edge каналы | кастомизация только TOML (YAML-вариант снят) |
| **v6.5.0** | 2026-04-26 | 18 новых agent-платформ (всего 42), стандарт `.agents/skills/` | нет |
| **v6.6.0** | 2026-04-28 | non-interactive config для CI/Docker (`--set`); brownfield epic scoping (file-churn) | `--tools none` снят (нужен явный `--tools <id>`); `project_name` → `[core]` (автомиграция) |
| **v6.7.0** | 2026-05-17 | **PRD и Brief переписаны lean outcome-driven**; +bmad-investigate; `.decision-log.md` | убран community-modules picker; удалён remote marketplace registry (ноль сетевых вызовов) |
| **v6.7.1** | 2026-05-18 | хотфикс инсталлятора (`baut`→`automator` source-resolve) | нет |
| **v6.8.0** | 2026-05-25 | **новые формы**: bmad-ux (two-spine), bmad-spec (5-полевое ядро); Web Bundles; +19 elicitation | `bmad-create-ux-design` → `bmad-ux`; `bmad-distillator` retired → `bmad-spec` |

---

## 2. Компонент → статус → что делать со старым разбором

Статусы: **NEW** (нет в v6.2.2) · **RENAMED** (переименован/слит) · **REMOVED** (удалён) · **CHANGED** (есть, но изменился) · **SAME** (структурно как был).

### Phase 1 — Analysis + Core

| Компонент | Статус | Версия | Что делать со старым разбором |
|---|---|---|---|
| **bmad-prfaq** (Working Backwards, код WB) | NEW | v6.3.0 | Дописать. В v6.2.2-разборе Working Backwards/PRFAQ числились ОТСУТСТВУЮЩИМИ — теперь есть. 5 стадий, вердикт forged/needs-heat/cracked. |
| **bmad-investigate** (forensic, код IN) | NEW | v6.7.0 | Дописать. Криминалистическая модель (Confirmed/Deduced/Hypothesized) — нового жанра в раскопках нет. |
| **bmad-spec** (core, дистиллятор любого intent) | NEW (← distillator) | v6.8.0 | Дописать. 5-полевое ядро, 8 правил Spec Law, 2-проходный self-validate. Заменил retired `bmad-distillator`. |
| **bmad-brainstorming** | CHANGED (переписан) | v6.8.0 | Переучить полностью. 61→108 техник, 13 категорий, 3 стенса, Python brain.py+memlog.py. step-02a..04 мертвы. |
| **bmad-advanced-elicitation** | CHANGED | v6.8.0 | Обновить. 50→69 методов (+19), 12 категорий (+framing). Все 50 старых сохранены. |
| **bmad-agent-analyst** (Mary) | CHANGED | v6.4.0+ | Поправить. Персона/принципы вынесены в customize.toml; SWOT/root-cause убраны из текста, Porter+Minto в identity. |
| **bmad-product-brief** | CHANGED (переписан) | v6.7.0 | Переучить. 5-стадийный workflow → single SKILL.md (~92 стр.), 3 интента, Fast/Coaching path. |
| **research** (market/domain/technical) | SAME | — | Оставить. Каркас прежний (steps/×6, HALT-гейты). Дрейф `stepsCompleted` в market-research ЖИВ (см. §4). |
| **bmad-document-project**, **tech-writer (Paige)** | SAME | — | Оставить (только +customize.toml). |

### Phase 2 — Planning

| Компонент | Статус | Версия | Что делать со старым разбором |
|---|---|---|---|
| **bmad-prd** | NEW (← 3 скилла) | v6.7.0 | Переучить. Слиты create/edit/validate → 1 conversational скилл, 3 intent, 7-мерная рубрика, run-folder. |
| **bmad-ux** | RENAMED (← create-ux-design) | v6.8.0 | Переучить. Two-spine DESIGN.md + EXPERIENCE.md; единый артефакт расщеплён на два контракта. |
| **bmad-create-prd / edit-prd / validate-prd** | REMOVED→shim | v6.7.0 | Не строй на них. DEPRECATED shims, форвардят в bmad-prd, **умрут в v7**. |
| **data/ CSV** (prd-purpose, domain-complexity, project-types) | REMOVED | v6.7.0 | Вычеркнуть. Снесены из Phase 2 (остались только в Phase 3 architecture). |
| **steps-c/v/e** микрофайлы | REMOVED | v6.7.0 | Вычеркнуть. Step-file архитектура Phase 2 удалена целиком. |
| **bmad-agent-pm (John) / ux-designer (Sally)** | CHANGED | v6.7-6.8 | Поправить точки входа (resolve_customization.py). |

### Phase 3 — Solutioning

| Компонент | Статус | Версия | Что делать со старым разбором |
|---|---|---|---|
| **bmad-create-architecture** (8 step) | SAME | — | **Оставить.** Step-файлы живы, контракты те же. +file-churn принцип, +customize.toml. |
| **bmad-create-epics-and-stories** (4 step) | SAME | — | **Оставить.** Connextra+BDD дословно. +«File Churn Check». Дрейфы And-строка/NFR ЖИВЫ. |
| **bmad-check-implementation-readiness** (6 step) | SAME | — | **Оставить.** step-07 честный чеклист починен (v6.6.0); остальные дрейфы живы. |
| **bmad-agent-architect (Winston)** | CHANGED | v6.4.0+ | Поправить. workflow.md → инлайн SKILL.md; меню динамическое из customize.toml. |
| **workflow.md** (отдельный файл) | REMOVED | v6.7-6.8 | Вычеркнуть. Инлайнен в SKILL.md во всех компонентах. |

### Phase 4 — Implementation

| Компонент | Статус | Версия | Что делать со старым разбором |
|---|---|---|---|
| **bmad-dev-story / code-review / create-story** | SAME | — | **Оставить.** 🔴 Разорванная петля dev↔review ЖИВА (см. §4). Все промпт-контрактные дрейфы живы. |
| **bmad-checkpoint-preview** (код CK) | NEW | v6.3.0 | Дописать. Guided human walkthrough коммита/PR. |
| **bmad-agent-sm (Bob)** | REMOVED | v6.3.0 | Вычеркнуть. Роль SM → прямые скиллы (sprint-planning/create-story/retro). |
| **bmad-agent-qa (Quinn)** | REMOVED | v6.3.0 | Вычеркнуть. → `bmad-qa-generate-e2e-tests` напрямую. |
| **bmad-quick-flow-solo-dev (Barry)** | REMOVED | v6.3.0 | Вычеркнуть. → `bmad-quick-dev` без persona-обёртки. |

### Кросс-фазовое

| Компонент | Статус | Версия | Что делать со старым разбором |
|---|---|---|---|
| **customize.toml + resolve_customization.py** | NEW | v6.4.0 | Дописать. Overlay (base→team→user) — закрывает претензию «нет custom/». |
| **.decision-log.md / addendum.md** | NEW | v6.7.0 | Дописать. Журнал решений вместо stepsCompleted-frontmatter. |
| **Web Bundles v6** (Gemini/ChatGPT) | NEW | v6.8.0 | Дописать с осторожностью — числа в бандлах расходятся с IDE (см. §4). |
| **bmad-init** | REMOVED | v6.3.0 | Вычеркнуть. Config грузится из `_bmad/bmm/config.yaml` напрямую. |
| **remote marketplace registry** | REMOVED | v6.7.0 | Вычеркнуть. Единственный источник — локальный `bmad-modules.yaml`. |

---

## 3. Топ-7 изменений, меняющих понимание метода

1. **Step-файлы умерли в Phase 1-2, выжили в Phase 3-4.** Самый важный структурный факт. Planning переписан в conversational SKILL.md (без шагов, без счётчиков, без эмодзи-блоков «Critical Rules»). Solutioning + Implementation — почти как в v6.2.2. Твои раскопки по верхней половине устарели, по нижней — актуальны.

2. **TOML-overlay закрыл главную старую претензию.** `customize.toml` («DO NOT EDIT — overwritten on every update») + `resolve_customization.py` + слои team/user. Правки больше НЕ перезаписываются при апдейте. Твоё «папки custom/ нет» — снято.

3. **Консолидация персон в Amelia.** Bob (SM), Quinn (QA), Barry (quick-dev-solo) удалены как отдельные агенты. Роли вызываются прямыми скиллами.

4. **PRD: 3 скилла → 1 conversational + рубрика вместо линтера.** create/edit/validate сведены в `bmad-prd`. 13 step-v рубрик с порогами → 7 измерений, severity = impact-судья LLM. SMART-рубрика и Fagan-разделение reframed.

5. **UX расщеплён на two-spine.** Единый артефакт → DESIGN.md (по спеке Google Labs design.md, Apache 2.0) + EXPERIENCE.md. Первая явная атрибуция внешнего стандарта по URL.

6. **bmad-spec — новый domain-agnostic core.** Дистиллятор любого intent в SPEC.md (single writer, derive-on-each-run из append-only .memlog.md). Заменил retired `bmad-distillator`.

7. **🔴 Разорванная петля dev↔review ЖИВА в v6.8.0.** Главный дефект для автономного оркестратора. dev-story и code-review не сходятся ни по именам секций, ни по маркерам, ни по шкалам severity. Твой код, не доверяющий LLM, остаётся нужен.

---

## 4. Что осталось верным из старых доков

Несущая дисциплина без изменений (HALT-гейты, FORBIDDEN в неперезаписанных фазах, TDD red-green-refactor, Perspective-Based Reading, vertical slicing-дух, отсутствие машинных гейтов). Живые дрейфы Phase 3 (And-строка, NFR-трассировка, битый CSV, A/P/C-противоречие, нереализованные режимы) и Phase 4 (разорванная петля, невычислимое done-условие step-04, GOTO 2a, дрейфующий DoD, fail-open, underscore/дефис) — твои находки всё ещё точны. Полные таблицы статусов, числа было→стало и список «что diff НЕ покрыл» + новые дрейфы v6.8.0 — в файле.

---

## Вывод одной фразой

Файлы по **Phase 3-4 держи** (актуальны 80-85%, дефекты живы). Файлы по **Core + Phase 2 перепиши** (сменилась архитектура). **Допиши** bmad-prfaq / bmad-investigate / bmad-spec / bmad-checkpoint-preview. Главный вывод неизменен: BMAD по-прежнему доверяет LLM исполнять текст без машинных гейтов — твой код, не доверяющий LLM, нужен.

Файл сохранён по абсолютному пути: `/home/server/bmad-orchestrator/docs/bmad-v68-changes.md`