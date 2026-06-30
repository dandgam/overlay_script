# Spec — Comparator «полный фарш» (Q-260521-CMPF)

**Дата:** 2026-05-21
**Owner:** 888-persona-improver → architect Phase 2 (pending)
**Status:** research-complete, design-pending, build-pending
**Cross-link:** Q-260521-CMPF в `~/.claude/skills/888/methodology-888.md §5 active`
**Memory:** [[feedback-comparator-shallow-template]] · [[feedback-research-persistence]]

---

## 1. Контекст и проблема

**Trigger:** 2026-05-21 user dandgam — «у нас comparator с поверхностным шаблоном, нужен фикс на полный фарш сравнения, изучать оба продукта досконально».

**Что произошло:**
- 04:49 запустил `888-persona-comparator` v1 для Virgil ↔ `bmad-code-org/bmad-automator` → 21KB отчёт, 9 паттернов
- В той же сессии вспомнил: 01:56 уже был deep handoff `md/handoff_888_competitor_uplift.md` (29KB) с РЕАЛЬНЫМ code-audit:
  - god-module `agent/run.py` = 5226 строк
  - −3000 LOC dead code (bot/, auto_split, AnthropicJudge, multi_run)
  - token economy −60% через cascade judges (Haiku→Opus)
  - 14 готовых спек в `spec/`
- Мой comparator переоткрыл 8 из 9 паттернов как «новые», добавил мусорные Q-NNN в очередь Virgil

**Корень:** `888-persona-comparator/SKILL.md` v1 — это **docs-only 9-dim rubric parse** (20 шагов).
- ❌ Не клонирует репо конкурента
- ❌ Не читает Python код ни нашего, ни их
- ❌ Не запускает `code-auditor` subagent
- ❌ Не считает LOC / cyclomatic / dead-code / Halstead
- ❌ Не делает runtime probe
- ❌ Нет pre-flight dedup gate против `md/handoff_*` + `spec/spec_*` + `cache/comparisons/`
- ❌ Auto-park без dedup-check

---

## 2. Research — 66 gap'ов в 19 категориях (3 параллельных subagent'а)

### Источник 1 — Patterns из соседних skill'ов (Explore subagent)

Прочитаны полностью: `research-compare`, `audit-setup`, `audit-skills`, `design-research`.

| # | Pattern | Из skill'а | Что даёт | Куда в comparator pipeline |
|---|---|---|---|---|
| 1 | Code-Reality Grounding | research-compare 2.5 | Eliminates confabulation; quote из real file | Step 7: прочитать .md/.py обеих сторон, inventory snippets перед dim-parse |
| 2 | Web Research Cross-Validation | research-compare 3.5 | Verify library/practice через context7 / counter-search | Step 13: context7 verification перед включением в transferable patterns |
| 3 | Counter-Example Gate | research-compare 4.6 | Catches cherry-picked claims | Step 11: 10-й field в JSON dim'а — `counter_example_checked: true` + ответ |
| 4 | Pre-flight Coverage Synthesis | research-compare 4.7 | Audits what wasn't covered | Step 14: новая section `## Coverage Gaps` (≥5 items) |
| 5 | Scope Routing S/M/L/XL | research-compare | Scales analysis by complexity | Step 8: расширить cost gate с scope determination |
| 6 | Independent Review | research-compare 5.7 | Fresh-context auditor | Step 14: parallel code-auditor с 8-12 stress questions |
| 7 | Epistemic Humility | research-compare 5.8 | Explicit uncertainty section | Step 15: `## Assumptions & Limitations` ≥7 items |
| 8 | Read-back Gate | research-compare 5.5 | Self-review после write | Step 15.5: прочитать report как независимый рецензент |
| 9 | Gap Analysis Classification | audit-setup 3a | `WHERE_SEEN + BENEFIT + IMPL_COMPLEXITY` | Step 13: переструктурировать transferable patterns |
| 10 | Community Registry Parallel Search | audit-setup 2 | 3 subagents (curated + fresh + niche) | Step 7: 3 parallel subagent'а вместо одного |
| 11 | Injection Detection + REJECTED-INJECTION Marking | audit-setup | Adversarial content handling | Step 9: расширить логирование |
| 12 | Atomic Write + Metadata Tracking | research-compare + audit-setup | Fail-safe persistence | Step 18: meta.json fields coverage_items_count + verification_status |

### Источник 2 — Industry frameworks (general-purpose + WebSearch subagent)

Найдено 41 уникальный dimension/check из 9 frameworks.

| Framework | Откуда | Уникальные dimensions / checks |
|---|---|---|
| **M&A Tech Due Diligence** (Black Duck / DataTeams / DevCom 2025) | [Black Duck eBook](https://www.blackduck.com/resources/ebooks/software-due-diligence.html), [DataTeams 2025](https://www.datateams.ai/blog/technical-due-diligence-checklist) | OSS license conflict scan (94% deals имели conflicts); unpatched CVE inventory (97%); technical debt в часах/$; integration risk score |
| **ATAM (SEI CMU)** | [SEI Library](https://www.sei.cmu.edu/library/architecture-tradeoff-analysis-method-collection/) | Quality Attribute Utility Tree; Sensitivity points; Tradeoffs; Risk themes |
| **SonarQube «7 axes»** | [Sonar Docs](https://docs.sonarsource.com/sonarqube-server/latest/user-guide/code-metrics/metrics-definition/) | Duplications % (AST); Technical debt ratio; Reliability rating A-E; Code rules violations |
| **Code Climate / Qlty** | [Code Climate Blog](https://codeclimate.com/blog/10-point-technical-debt-assessment), [Cognitive Complexity](https://docs.codeclimate.com/docs/cognitive-complexity) | Churn × complexity hotspots; Cognitive complexity (≠ cyclomatic); AST-level similar block detection |
| **Classic static metrics (McCabe / Halstead / Oman)** | [Codacy](https://blog.codacy.com/code-complexity), [In-Com](https://www.in-com.com/blog/halstead-complexity-measures-explained-calculating-software-complexity/) | Cyclomatic complexity per function (distribution); Halstead Volume + Difficulty + Effort; Maintainability Index 0-100; Coupling Between Objects (CBO); LCOM / cohesion |
| **OpenSSF Scorecard** | [scorecard.dev](https://scorecard.dev/), [GitHub](https://github.com/ossf/scorecard) | Branch Protection; SECURITY.md + disclosure path; Signed releases / SLSA provenance; Pinned dependencies; Token permissions in CI; OSV database hits |
| **CHAOSS + Bus Factor** | [CHAOSS metrics](https://github.com/chaoss/metrics), [Bus Factor Explorer](https://www.cesarsotovalero.net/blog/bus-factor-a-human-centered-risk-metric-in-the-software-supply-chain.html) | Bus factor / truck factor (target ≥5); Contributor diversity Gini; Response time to issues / PRs median; Release cadence regularity; Time-to-first-response |
| **Code smells / God-class research** | [Legit Security](https://www.legitsecurity.com/aspm-knowledge-base/code-smells), [SmellyCode++ PMC](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC12255726/) | God-class / God-module count; Dead code % (unreachable + unused — vulture-style); Bloated dependencies (<X% imported surface used); Long parameter lists |
| **SWE-bench / SWE-Compass / τ-bench / AgentBench** | [SWE-Bench Pro arXiv](https://arxiv.org/pdf/2509.16941), [τ-bench arXiv](https://arxiv.org/pdf/2406.12045), [SWE-Compass](https://arxiv.org/pdf/2511.05459) | Multi-turn context retention; Tool-use efficiency (calls/task, error-recovery); Policy adherence (τ-bench); Long-context degradation curve; Multi-language / multi-repo coverage; Environment breadth (8 envs) |

### Источник 3 — Investigation patterns из handoff (Explore subagent)

Прочитан полностью `md/handoff_888_competitor_uplift.md` (539 строк). Извлечены 13 investigation patterns:

| # | Что автор делал | Какой gap закрыл | Tool / method |
|---|---|---|---|
| 1 | Runtime probe скриптов (`probe_prompt_caching.py`) | False alarms на cache + format compat | Bash + Python + Anthropic API inspection |
| 2 | Grep по patterns (unused functions) | 4000 LOC unused code | `grep -r`, `git log --grep` |
| 3 | LOC + cyclomatic complexity (5226 строк god-module) | Точное разбиение на 8 файлов | `wc -l`, Serena symbol-tree, call-graph depth |
| 4 | Token economy simulation (cascade Haiku→Opus) | −60% review tokens предсказан | API pricing + logs + worst-case estimate |
| 5 | Dual-source auditing (yaml + story-files write-verify) | 3 NEW-XX класса by design | Direct file-read + git status |
| 6 | Dead-code classification (post-MVP / subscription-only / refactored) | −3000 LOC systematized | Memory + git blame + commit message |
| 7 | Competitor codebase cloning (`/tmp/bmad-automator/`) | 5 переносимых P1-P5 паттернов | `git clone` + selective Read |
| 8 | Policy JSON reverse-engineering | Pluggable verifier contract | Manual schema inspection + symbol matching |
| 9 | Convention mapping (BMad canonical format) | Разгромлен false-alarm format spec | File-structure audit + frontmatter parsing |
| 10 | Watchdog deduplication analysis (3 detector → 1) | −500 LOC consolidation | Symbol-graph + grep intersection |
| 11 | CLI size analysis (166 vs 1858 строк) | CLI bloat identification | `wc -l` + symbolic parse |
| 12 | Gate architecture pattern mining | NEW-26 закрытие bundled skill | Markdown doc + call-graph search |
| 13 | Synthetic test target setup (`/tmp/bmad-cmp/`) | Safe re-validation | Manual directory inspection |

**Verified facts pattern (§1 handoff'а):** каждый факт = file + line + конкретное число / runtime output. Гипотезы помечены ⚠️. False-alarms отклоняются явно (2 spec'а rejected).

---

## 3. Сводная таблица 66 gap'ов в 19 категориях

| Категория | Gap'ы (число) | Tools / cost |
|---|---|---|
| **A. Code-level grounding** | 3 (git clone, LOC inventory, symbol-tree) | Bash + Serena, ~1 мин |
| **B. Quantitative metrics** | 7 (cyclomatic, Halstead, MI, cognitive, dups, CBO, LCOM) | radon, lizard, jscpd — install |
| **C. Bloat / dead code** | 4 (grep, god-module count, vulture, watchdog dedup) | bash + vulture + subagent |
| **D. Supply chain & security** | 5 (license, CVE, OpenSSF, SECURITY.md, pinned deps) | osv-scanner, scorecard, gh-policy |
| **E. Project health** | 4 (bus factor, response time, release cadence, contributor diversity) | git log + GitHub API |
| **F. Architecture tradeoff (ATAM)** | 4 (utility tree, sensitivity, tradeoffs, risk themes) | LLM-driven |
| **G. Agent-specific benchmarking** | 6 (multi-turn, tool-use, policy adherence, long-context, multi-lang, envs) | eval runner |
| **H. Runtime probes** | 2 (token economy, behaviour verification) | Python scripts |
| **I. Methodology gates (из research-compare/audit)** | 9 (dedup gate, scope routing, web cross-val, counter-ex, coverage gaps, indep review, epistemic humility, read-back, parallel multi-source) | LLM + subagent calls |
| **J. Verified facts pattern** | 3 (file:line proof, ⚠️ hypothesis marker, false-alarm rejection) | LLM-rule |
| **K-S — variations** | 19 категорий total (остальные = детализация выше) | mixed |

(Полная таблица 41 industry + 12 internal + 13 handoff = 66 уникальных gap'ов выше в §2.)

---

## 4. Архитектура фикса — Comparator v2 3-tier pipeline

### Tier 1 — Pre-flight gates (sequential)

```
1. Dedup gate:
   grep -r <reference-name> ~/<project>/md/handoff_*
   ls ~/<project>/spec/spec_*<reference-keyword>*
   ls ~/.claude/skills/888/cache/comparisons/
   → если есть ≤30 дней: AskUserQuestion(обновить / новое / прервать / читать существующий)

2. Scope routing S/M/L/XL:
   S = 1-2 файла (1 dim parse)
   M = 1 подсистема (3 dim parse + 1 audit)
   L = 3+ файлов cross-file (full 9-dim + 4 subagents)
   XL = целая система (full + ATAM + benchmarks)

3. Cost gate (tier-dependent):
   S: < 50k tokens
   M: < 200k tokens
   L: < 500k tokens
   XL: < 1.5M tokens — AskUserQuestion confirm
```

### Tier 2 — Inventory (4 параллельных subagent'а)

```
subagent_1 (Explore + Read):
  git clone <reference> /tmp/cmp-<sha>/
  → inventory: LOC per module, file tree, key Python files
  → output: cache/comparisons/<sha>/reference_inventory.json

subagent_2 (code-auditor):
  bloat audit OURS: ours-path
  → output: god-modules (>500 LOC), dead-code candidates, dups, cyclomatic distributions
  → output: cache/comparisons/<sha>/ours_audit.json

subagent_3 (general-purpose + WebSearch):
  context7 verification всех упомянутых libraries
  industry context для reference (similar tools, peer agents)
  → output: cache/comparisons/<sha>/external_context.json

bash runtime probes (если применимо):
  probe_prompt_caching.py
  token rates measurement
  latency / behaviour checks
  → output: cache/comparisons/<sha>/runtime_facts.json
```

### Tier 3 — Analysis (sequential, depends on Tier 2)

```
1. Quantitative metrics на обе стороны:
   radon cc <ours>; radon cc <reference>
   vulture <ours>; vulture <reference>
   lizard <ours>; lizard <reference>
   scorecard --repo <reference>
   git log --numstat для churn/hotspots
   → metrics.json per side

2. 9-dim rubric (текущая) + ATAM расширение:
   Per dim:
     observations_ours / observations_reference (existing)
     comparison_verdict / transferable_pattern (existing)
     + counter_example_checked: true + ответ (Q-NNN-3)
     + atam_sensitivity: [points where small change → big impact]
     + atam_tradeoffs: [where improving A hurts B]

3. Cross-dim synthesis:
   risk themes (clusters)
   transferable patterns с verified-facts format (file:line+число)
   все claims прошли counter-example gate

4. Report writing:
   ## TL;DR (verified facts только)
   ## 9 dims (с ATAM extension)
   ## Transferable patterns (с file:line proof)
   ## Coverage Gaps (≥5 items что НЕ проверили)
   ## Assumptions & Limitations (≥7 epistemic humility items)
   ## Suggested Q-NNN entries (с pre-checked dedup status)
```

### Tier 4 — Output gates

```
1. Read-back gate (self-review):
   re-read report как независимый рецензент
   проверить: count consistency (N found vs table rows)
   проверить: каждый transferable pattern имеет supporting observation

2. Independent review subagent:
   Agent(code-auditor or general-purpose, opus):
     prompt: 8-12 stress questions on report
     "что пропустили? fair comparison? stack compatibility? counter-example для P0 #1?"
   verdict: PASS / RETRY-ANALYSIS / FAIL-REPORT

3. Canary check (existing v1)

4. Auto-park с dedup-check:
   для каждого suggested Q-NNN:
     grep <Q-keyword> ~/<project>/.claude/skills/888/methodology-*.md
     grep <Q-keyword> ~/<project>/spec/
     если совпадение ≥50% → SKIP, не добавлять (log как "duplicate-of-<existing>")
     если уникален → add to active queue
```

---

## 5. Effort estimate

| Stage | Sessions | Activity |
|---|---|---|
| Phase 1 (analyst) | 1 | brief на v2: pain (shallow v1), user (solo-operator), success metric (≥85% deep-audit findings caught) |
| Phase 2 (architect) | 2-3 | design 3-tier pipeline + Field 4 expansion (new tools) + Field 5 cost ceilings |
| Phase 3 (qa) | 1 | RED tests на new gates (dedup gate fires on existing handoff; ATAM section presence; counter-example field) |
| Phase 4 (build) | 2-3 | impl: bash gates + subagent invocations + new tools install (radon, vulture, lizard, scorecard) |
| Phase 5 (ops + improver) | 1 | calibration: re-run на Virgil ↔ automator → должен НЕ запуститься (dedup hits handoff) |
| **Total** | **7-9 sessions** | |

---

## 6. Unlock conditions (когда запускать)

- v1 (Q-260520-A2B3) — ✅ done (validated 2026-05-21)
- handoff `feedback_comparator_shallow_template` — ✅ saved
- этот spec — review через `bmad-review-edge-case-hunter` PENDING
- Сначала: `888-persona-improver` retro на Q-260521-CMPF → разрешает Phase 1 на новой версии

---

## 7. NOT в scope этого spec'а

- Не строит UI для comparator (CLI достаточно)
- Не вводит multi-LLM routing (Sonnet везде, кроме code-auditor = Opus)
- Не интегрирует с external SaaS (Sonar Cloud, Snyk) — только local tools (radon/vulture/scorecard standalone)
- Не покрывает Q-260521-J1K2 «Find competitors» — отдельная фича на v3

---

## 8. References

- **Q-NNN entry:** `~/.claude/skills/888/methodology-888.md §5` строка ~1689
- **Memory:**
  - [[feedback-comparator-shallow-template]] — запрет на v1 для production
  - [[feedback-research-persistence]] — новое правило 888 (см. §9 ниже)
- **Existing v1:** `~/.claude/skills/888-persona-comparator/SKILL.md` + 11 файлов
- **Existing handoff:** `md/handoff_888_competitor_uplift.md` (539 строк, основная reference glossary)
- **Industry research links:** см. §2 источник 2 (9 frameworks с URL'ами)

---

## 9. Связанное правило в 888 SKILL.md — Research persistence (Q-260521-RSPS)

Этот spec — **первая instance** нового правила, добавленного в 888 SKILL.md 2026-05-21:

> Любой turn где ≥3 sources / ≥1 subagent invocation / ≥10 findings — research output **обязательно**
> persistent'ится в файл (`spec/spec_<topic>.md` или `md/research_<topic>_YYYYMMDD.md`) ДО парковки Q-NNN.
> Q-NNN MUST содержать `attachment:` поле со ссылкой на файл.
> Запрещено парковать «66 items в чате» без file persistence.

См. правило в `~/.claude/skills/888/SKILL.md` §1 «Research persistence rule».
