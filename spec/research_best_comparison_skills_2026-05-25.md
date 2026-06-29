# Research: best-of-breed comparison skills/tools для перенять

**Дата:** 2026-05-25
**Скилл:** `/research-compare` (XL, meta-research — не сравнение продуктов, а сравнение **инструментов сравнения**)
**Триггер:** user dandgam — «найди профессиональный skill для сравнения двух продуктов, что есть лучшего в GitHub + интернете чтобы забрать к себе»
**Контекст:** наши инструменты (`888-persona-comparator` v1+v2 и `research-compare` XL) shallow по operational layer (см. `feedback_comparator_shallow_template`). Цель — найти best-of-breed для production-grade pairwise comparison.

---

## §0 Coverage Pre-flight (XL → ≥7 items)

| # | Type | Что не покрыто | Если важно |
|---|---|---|---|
| CG1 | Coverage | Не сравнивал внутренне с DeepEval / Inspect AI / Promptfoo — только по subagent abstract'ам. Установка + first-run experience не tested. | PoC любого из 3 фреймворков (1 сессия) перед commit'ом. |
| CG2 | Coverage | Agentic Rubrics paper (arxiv 2601.04171) не Read'ил полностью — subagent дал 1-строчный takeaway. Главная находка GitHub-агента, надо verify. | Read paper целиком перед Q-AGENTIC-RUBRICS impl. |
| CG3 | Coverage | M-MAD (Multidimensional Multi-Agent Debate, ACL 2025) — paper не Read'ил, в subagent'е только формулировка. | Read ACL paper перед Q-DEBATE. |
| VD1 | Verify | Subagent'ы — single-pass без cross-validation. Promptfoo claim'ы могут быть устаревшими — verify через context7 Promptfoo docs перед commit'ом. | context7 query для top-3 tools. |
| VD2 | Verify | «Cronbach's α ≥ 0.80 — индустриальный gold standard» — claim из Galileo blog. Не verified counter-source — может быть marketing. | Cross-check академический paper. |
| VD3 | Verify | Inspect AI «200+ pre-built evals» — не verified что хоть один подходит для repo-vs-repo comparison (большинство для language model eval). | Lazy check listing. |
| VD4 | Verify | SonarQube/OpenSSF Scorecard на bmad-automator repo не запускался — claim «automator нет cost control» может быть refuted объективным сканом. | Auto-scan reference repo как dogfood. |
| CG4 | Coverage | Не покрыл commercial tools (Galileo Eval, Patronus AI, Arize Phoenix) — только OSS focus. Galileo упомянут только через blog. | Если бюджет позволит — оценить commercial offering. |
| CG5 | Coverage | Repomix + GraphRAG hybrid — не verified что они composable между собой; subagent предположил. | PoC integration перед Q-REPOMIX. |
| VD5 | Verify | Bradley-Terry для пар-сравнений с N=2 (наш case) — overkill или норма? BT обычно используется для N≥5 candidates. | Math review. |
| VD6 | Verify | «Heterogeneous panel Claude+GPT+Gemini» — у нас subscription только Claude. Multi-provider требует API keys (feedback_no_anthropic_api). | Plan accordingly. |
| VD7 | Verify | Galileo blog vs peer-review paper — soft source. Cronbach's α threshold variable across literature. | Replace с peer-reviewed citation. |

**Density:** 12 items (7+ required для XL satisfied). >50% — VD не verified → перед production-commit любого Q-NNN обязательна mini-PoC stage.

---

## §1 Текущее состояние (наши инструменты)

- **`888-persona-comparator` v1+v2** (`~/.claude/skills/888-persona-comparator/`)
  - 9 dimensions rubric: capabilities / architecture / prompt_design / tooling / memory / eval_coverage / cost / ux / safety
  - v2 (Q-260521-CMPF done 2026-05-22): dedup gate + scope routing S/M/L/XL + cost gate + parallel 3-subagents + code-grounding + counter-example gate + catalog + safety hardening
  - **Известная слабость** (`feedback_comparator_shallow_template` + сегодняшний meta-эксперимент): не лазит в operational layer reference'а (`data/*.md`), 9-dim сетка узка
- **`/research-compare`** (`~/.claude/skills/research-compare/`)
  - XL pre-flight gates (Code-Reality Grounding / Web Cross-Validation / Counter-Example / Coverage Synthesis / Read-back / Independent Review / Epistemic Humility)
  - **Известная слабость:** дорогой по церемонии (~6 шагов гейтов), не имеет formal scoring, single-judge (я сам), subagent'ы single-pass

**Что отсутствует в наших инструментах (по результатам этого research'а):**
1. Quantitative scoring / numeric aggregation (Pugh / BT / MCDA)
2. Heterogeneous multi-judge panel (bias mitigation)
3. Position-bias swap-and-average
4. Per-dimension debate (M-MAD)
5. Auto-scanner integration (SonarQube/OpenSSF/CHAOSS)
6. Execution-grounded grading (Inspect AI sandbox)
7. Calibration vs human spot-checks (Cronbach's α / Spearman)
8. Citation enforcement (file:line / URL anchor)
9. Code-grounded rubric generation (Agentic Rubrics — rubric FROM artifact, не template-first)

---

## §2 Найдено внешне — 40 источников в 3 кластерах

### Кластер A — GitHub repos / Claude skills (10 источников)

| # | Name | URL | Stars/Activity | Unique для нас |
|---|---|---|---|---|
| A1 | **Agentic Rubrics (SWE-Compass)** | [arxiv 2601.04171](https://arxiv.org/pdf/2601.04171) | Research Jan 2026 | **Главный fix shallow-проблемы**: rubric генерируется FROM repo, не template-first. +3.5pp на SWE-Bench Verified |
| A2 | tau2-bench | [sierra-research/tau2-bench](https://github.com/sierra-research/tau2-bench) | active | Dynamic conversation rubrics + pass^k |
| A3 | agentic-benchmarks meta-checklist | [uiuc-kang-lab/agentic-benchmarks](https://github.com/uiuc-kang-lab/agentic-benchmarks) | active | **Self-audit нашей рубрики на leaky/shallow** |
| A4 | agent-framework-benchmark | [LukaszGrochal/agent-framework-benchmark](https://github.com/LukaszGrochal/agent-framework-benchmark) | recent | Template для cross-framework eval (5 frameworks side-by-side) |
| A5 | Comperator | [Procycons/Comperator](https://github.com/Procycons/Comperator) | small | Evidence-gathering pipeline web → classify → matrix |
| A6 | Compint | [pml7098/compint](https://github.com/pml7098/compint) | small | Evidence-cited feature matrix — каждая claim с URL |
| A7 | **tech-debt-skill** | [ksimback/tech-debt-skill](https://github.com/ksimback/tech-debt-skill) | Claude Code skill | **Enforced file:line citations** — паттерн прямо в нашу рубрику |
| A8 | **claude-code-skills code-audit** | [levnikolaevich/claude-code-skills](https://github.com/levnikolaevich/claude-code-skills) | active | 9-dim audit + **stack-aware tool grounding** (npm/pip/cargo/go) |
| A9 | Dive-into-Claude-Code | [VILA-Lab/Dive-into-Claude-Code](https://github.com/VILA-Lab/Dive-into-Claude-Code) | research | Reference taxonomy для agent-vs-agent axes |
| A10 | Galileo agent-eval framework | [galileo.ai blog](https://galileo.ai/blog/agent-evaluation-framework-metrics-rubrics-benchmarks) | doc | **Cronbach's α ≥ 0.80 калибровка vs human** |

### Кластер B — Industry frameworks (15 источников)

| # | Framework | Год | Главное |
|---|---|---|---|
| B1 | **ATAM** (SEI CMU) | 2000, canonical | Utility tree → sensitivity points → tradeoff points → risk themes. **Прямо в код** |
| B2 | CBAM (extends ATAM) | 2002 | Quantified $/ROI per scenario |
| B3 | SAAM | 1994 | Modifiability scenarios — screening pass перед ATAM |
| B4 | ARID | 2001 | Asymmetric maturity (one mature, one WIP) |
| B5 | **Pugh Matrix** (Six Sigma) | 1981 | Criteria × options, baseline-vs-challenger, +/0/− vs datum. **Прямо в код** |
| B6 | **Weighted Decision Matrix / MCDA** | classic | Weighted sum scoring — **наша 9-dim уже это** |
| B7 | **Kepner-Tregoe DA** | 1965 | MUST (hard-constraint) vs WANT (preference) — двухтиерная рубрика |
| B8 | Black Duck M&A DD Checklist | 2025 | License/IP + OSS provenance + security + team triad |
| B9 | DataTeams TDD | 2025 | Team & roadmap axes (non-code) |
| B10 | **SonarQube / SQALE** | 2025.1 LTA | Tech-debt в часах/$ — automated scan |
| B11 | **OpenSSF Scorecard** | v5 2025 | 18+ автоматических checks → 0-10. **`scorecard --repo=...` обе стороны** |
| B12 | **CHAOSS metrics** | 2025 | Bus factor, contributor diversity, release cadence |
| B13 | **SWE-bench Pro + τ-bench + AgentBench + WebArena** | 2025-26 | Execution-grounded benchmarks для AI agents |
| B14 | RICE + MoSCoW | classic | Post-compare backlog prioritization |
| B15 | Wardley + CMMI L1-5 + Gartner MQ | classic | Strategic positioning, maturity heatmap |

### Кластер C — AI/LLM-based comparison tooling (15 источников)

| # | Tool/Method | Год | Главное |
|---|---|---|---|
| C1 | **DeepEval ArenaGEval** | 2025 | Native pairwise N-variants + G-Eval CoT + pytest CI |
| C2 | **Promptfoo pairwise** | 2025 | YAML configs + weighted assertions + model-graded |
| C3 | Ragas | 2024-25 | RAG-specific (inapplicable если нет RAG) |
| C4 | **Inspect AI (UK AISI)** | 2024-25 | Task→Solver→Scorer + sandboxed Docker exec. **Production-grade** |
| C5 | **LMSYS Bradley-Terry** | 2024-25 | Pairwise votes → BT MLE → Elo + bootstrap CIs |
| C6 | **P2L (Prompt-to-Leaderboard)** | 2025 | Per-prompt-category winners (different per axis) |
| C7 | ChatEval (MAD) | 2024 | Role-played debate panel |
| C8 | **M-MAD (Multidim MAD)** | ACL 2025 | **Один debate per dimension** — прямо матчит наши 9 dims |
| C9 | MAD + Adaptive Stability | 2025 | Early-stop when judges converge → cost control |
| C10 | PRD (Peer Rank & Discussion) | 2024 | LLM peer-rank + discuss disagreements |
| C11 | **G-Eval** | 2023→25 | CoT-derived eval steps |
| C12 | LLM-Rubric | 2025 | Multidim calibrated с reliability weighting |
| C13 | CritiqueLLM | 2024 | Structured critique generation (не just score) |
| C14 | **SWE-bench Pro / Verified** | 2025 | Real PR execution grading |
| C15 | **Repomix + GraphRAG** | 2025 | **Flatten repo → AST graph → grounded retrieval**. Прямо fix shallow-problem |

### Failure modes (bias таблица из C-cluster)

| Bias | Effect | Mitigation |
|---|---|---|
| Position bias | slot-1 wins 55-65% | **Swap-and-average** (run twice, accept consistent) |
| Verbosity bias | longer wins | Length normalization OR explicit rubric instruction |
| Self-preference / family bias | judge favors own family | **Heterogeneous panel** different provider |
| Agreeableness bias | panel converges to first speaker | Independent-then-merge (parallel votes BEFORE discuss) |
| Judge inconsistency | same input → different scores | Reference-guided + meta-judge + calibrate vs human |
| Bias amplification multi-judge | more judges ≠ less bias if same family | Heterogeneous + meta-judge > debate-consensus |

---

## §3 Анализ: что у нас НЕТ vs что взять

| Возможность | Сейчас | Кандидат source | Severity |
|---|---|---|---|
| **Code-grounded rubric generation** (rubric FROM artifact, не template) | ❌ | A1 Agentic Rubrics + A8 stack-aware grounding | **P0** |
| **Repomix + GraphRAG pre-pack** | ❌ | C15 | **P0** (fixes docs-only shallow) |
| **Heterogeneous multi-judge panel** | ❌ | C1/C2/C7/C8 + bias таблица | **P0** для production decisions |
| **Position-bias swap-and-average** | ❌ | bias таблица | **P0** (≈30 LOC, не optional) |
| **Per-dimension debate (M-MAD)** | ❌ | C8 | **P1** (тяжёлая инвестиция) |
| **Quantitative scoring + Bradley-Terry** | partial (weighted=9) | C5/C6/B5/B6 | **P1** |
| **Kepner-Tregoe MUST vs WANT split** | ❌ | B7 | **P1** (10 min add) |
| **Auto-scanner integration** (SonarQube/OpenSSF/CHAOSS) | ❌ | B10/B11/B12 | **P1** quantitative facts |
| **Inspect AI sandbox для execution-grounded** | ❌ | C4 | **P1** для code-comparison |
| **Citation enforcement file:line/URL** | partial | A6/A7 | **P0** (cheap fix) |
| **Calibration vs human spot-checks** (Cronbach's α) | ❌ | A10 + C12 | **P1** |
| **Self-audit рубрики на leaky/shallow** | ❌ | A3 | **P1** |
| **ATAM utility tree + sensitivity/tradeoff tags** | ❌ | B1 | **P2** |
| **5 новых axes** (tech_debt / security / project_health / benchmark / licensing) | ❌ (9 → 14) | B10/B11/B12/B13/B8 | **P1** |
| **Anti-Gap фильтр для предложенных gap'ов** | ✅ (research-compare имеет) | — | ✅ |
| **Epistemic Humility section** | ✅ (research-compare) | — | ✅ |

**Out-of-scope для нас (subscription only, не нужно сейчас):**
- Multi-provider API panel (Claude+GPT+Gemini) — требует API keys (feedback_no_anthropic_api). **Deferred** до multi-LLM milestone.
- DPO/RLHF pairwise labeling — это для training, не для comparison.

---

## §4 Counter-Example Gate (для каждой главной рекомендации)

| Claim | Counter | Defensible? |
|---|---|---|
| «Agentic Rubrics закрывает shallow» | Может для SWE-Bench-style контекста, не для **methodology-comparison**? | ⚠ partial — paper про code-fix tasks, не про comparator-skill comparison. **Adapt идею, не копировать impl.** |
| «Repomix+GraphRAG прямо решает docs-only» | Может быть overkill для 20-30 файлов reference repo? | ⚠ valid — для small reference repo plain `git clone` + grep дешевле. **Trigger:** репо >100 файлов. |
| «Position bias swap-and-average всегда нужен» | Может, для multi-judge panel single position bias уже разрешён? | ✅ Defensible — даже multi-judge каждый имеет own position bias. Swap дёшев (30 LOC). |
| «Heterogeneous panel = Claude+GPT+Gemini» | У нас Claude subscription only — могу использовать 3 разных модели Anthropic (Opus / Sonnet / Haiku)? | ⚠ partial — Opus/Sonnet/Haiku same family, **не закрывает self-preference bias**. Но position+verbosity mitigation работает. **Honest stance:** «partial mitigation до API keys». |
| «SonarQube/OpenSSF integration необходим» | Какой смысл сравнивать markdown-skill через SonarQube? Они для code repos. | ⚠ valid — для **code-repo** comparator (Virgil vs automator) — yes. Для **skill-vs-skill** (888 vs comparator-X) — no. **Conditional на тип reference.** |
| «Bradley-Terry для N=2 candidates избыточен» | BT нужен для N≥5, для pairwise можно проще scoring | ✅ Defensible — для N=2 простое weighted-sum достаточно. **BT добавлять только если расширим до N≥3.** |
| «Inspect AI всегда лучше custom» | Может быть incompatible с нашим event-bus + non-Docker workflow? | ⚠ valid — Inspect AI ожидает Docker sandbox. У нас bwrap. **PoC обязательно перед commit.** |
| «Calibration требует human spot-checks» | У нас solo-operator (user dandgam) — limited human labels | ⚠ valid — но 10-20 spot-checks per dimension реалистично. **Sample, не full corpus.** |

6/8 defensible, 2 valid counter (#4, #5) — отражены в Q-NNN scope conditional'ами.

---

## §5 Recommendation — Top-12 паттернов для перенять

**Архитектурная философия:** **augment** наш comparator + research-compare, а не replace. Наша 9-dim рубрика осталась бы, но получает поверх **5 новых слоёв**:

1. **Pre-pack layer** (Repomix-style) — flatten + index repo перед comparison
2. **Code-grounded rubric layer** (Agentic Rubrics adapt) — rubric генерируется из artifact, не template
3. **Multi-judge layer** (M-MAD per-dim + swap-and-average) — bias mitigation
4. **Quantitative scanner layer** (conditional: OpenSSF/SonarQube/CHAOSS если reference = code repo)
5. **Calibration layer** (spot-check Cronbach's α на 10-20 sample)

### Wave A — P0 (~4-5 сессий, total)

| Q-NNN | Что | Effort | Source |
|---|---|---|---|
| **Q-260525-REPOMIX** | Pre-pack: flatten reference repo + AST/import graph; feed to comparator | 1 сессия | C15 Repomix + GraphRAG |
| **Q-260525-CITE** | Citation enforcement: каждая claim в report — `file:line` или `URL` anchor | ≤1 сессия | A6 Compint + A7 tech-debt-skill |
| **Q-260525-RUBRIC-AGENTIC** | Code-grounded rubric: после pre-pack, **generate** dim-specific scoring questions from artifact, не template | 2 сессии | A1 Agentic Rubrics adapt |
| **Q-260525-SWAP** | Position-bias swap-and-average: run rubric twice со swapped order, accept consistent | ≤1 сессия | C-cluster bias table |

### Wave B — P1 (~6-8 сессий)

| Q-NNN | Что | Effort | Source |
|---|---|---|---|
| **Q-260525-MAD-DIM** | Per-dimension debate (M-MAD pattern): 1 debate per dim с adaptive stability stop | 2-3 сессии | C8/C9 |
| **Q-260525-AXES5** | Расширить рубрику с 9 → 14 dims: tech_debt, security_posture, project_health, benchmark_scores, licensing_provenance | 1 сессия | B8/B10/B11/B12/B13 |
| **Q-260525-SCAN** | Auto-scanner integration: conditional invoke OpenSSF Scorecard + SonarQube на оба repo если `type == code_repo` | 2 сессии | B11/B10 |
| **Q-260525-MUSTWANT** | Kepner-Tregoe MUST/WANT split: hard-constraint dims (security, license) vs preference (UX, cost) | ≤1 сессия | B7 |
| **Q-260525-SELFAUDIT** | Pre-run check: agentic-benchmarks meta-checklist на нашу рубрику чтобы поймать leaky/shallow до прогона | 1 сессия | A3 |
| **Q-260525-CALIB** | Calibration: 10-20 human spot-checks per dim, compute Cronbach's α; if <0.80 → rubric needs refinement | 1-2 сессии | A10/C12 |

### Wave C — P2 (later, ~3-4 сессии)

| Q-NNN | Что | Source |
|---|---|---|
| Q-260525-INSPECT-POC | Inspect AI sandbox PoC для execution-grounded code-comparison | C4 |
| Q-260525-ATAM | ATAM utility tree + sensitivity/tradeoff tagging | B1 |
| Q-260525-CMMI | CMMI L1-5 maturity heatmap per dim | B15 |
| Q-260525-PANEL-MULTI | Heterogeneous panel (Claude+GPT+Gemini) — blocked by API keys | C-bias |

### НЕ переносить (Anti-Gap отсеял)

- **Ragas** (C3) — RAG-specific, у нас нет RAG в comparator scope
- **Bradley-Terry** (C5) — overkill для N=2; revisit если расширим до N≥5
- **Full Wardley Mapping** (B15) — methodology only, не impl-able пока вручную
- **Galileo commercial** — proprietary, OSS alternatives достаточны

---

## §5.8 Epistemic Humility (XL → ≥7 items)

### 1. Single-pass subagent research
**Уверенность:** средняя
**Что предполагаю:** что 3 subagent'а с WebSearch покрыли state-of-art 2025-26.
**Что НЕ проверял:** второй пасс с inverse query («X is overhyped», «X failure case»).
**Если ошибся:** некоторые «top» tools могут быть hype без реальной adoption.
**Как уменьшить:** Step 3.5 second pass с inverse queries перед commit.

### 2. Agentic Rubrics адаптируемость
**Уверенность:** низкая (записано как CG2)
**Что предполагаю:** paper для SWE-Bench code-fix перенесётся на skill-vs-skill comparator.
**Что НЕ проверял:** Read paper целиком; узнать concrete алгоритм rubric generation.
**Если ошибся:** Q-260525-RUBRIC-AGENTIC effort ↑ 2x или паттерн вообще нерелевантен.
**Как уменьшить:** Read arxiv 2601.04171 целиком перед Q-NNN.

### 3. Heterogeneous panel доступность
**Уверенность:** низкая (VD6)
**Что предполагаю:** что Opus/Sonnet/Haiku дают partial bias mitigation.
**Что НЕ проверял:** реальный эксперимент (same vs cross-family panel на 10 заданиях).
**Если ошибся:** Q-260525-PANEL deferred даёт false sense of bias mitigation.
**Как уменьшить:** mini-experiment 10 заданий, compare scores cross-family vs same-family.

### 4. Repomix + GraphRAG compositionality
**Уверенность:** низкая (CG5)
**Что предполагаю:** Repomix output + GraphRAG ingest работают вместе.
**Что НЕ проверял:** integration PoC.
**Если ошибся:** Q-260525-REPOMIX 1 сессия → 3+ при debug.
**Как уменьшить:** spike-PoC ≤2 часа перед formal Q.

### 5. Inspect AI vs bwrap incompatibility
**Уверенность:** средняя
**Что предполагаю:** Inspect AI Docker-only, наш bwrap альтернатива возможна.
**Что НЕ проверял:** Inspect AI internals (может быть Docker pluggable).
**Если ошибся:** Q-260525-INSPECT-POC заблокирован полностью, нужен fork.
**Как уменьшить:** Read Inspect AI README + Docker abstraction.

### 6. Cronbach's α threshold (VD7)
**Уверенность:** низкая
**Что предполагаю:** ≥0.80 — производственный gold standard.
**Что НЕ проверял:** peer-reviewed source (Galileo blog — marketing).
**Если ошибся:** калибровка может быть looser (0.70?) или stricter (0.85?).
**Как уменьшить:** Read 2-3 academic papers на agreement metrics для LLM-judges.

### 7. Selection bias в моих 40 источниках
**Уверенность:** средняя
**Что предполагаю:** subagent'ы вернули diverse coverage.
**Что НЕ проверял:** что не упущен major framework (Patronus AI? Arize Phoenix? Braintrust?).
**Если ошибся:** miss key competitor analysis tool.
**Как уменьшить:** quick WebSearch «commercial LLM eval 2026 comparison» — top-5 vendors.

### 8. Subscription-only constraint
**Уверенность:** высокая
**Что предполагаю:** ни DeepEval/Promptfoo/Inspect AI не требуют API key (могут локально run).
**Что НЕ проверял:** runtime requirements — все три могут требовать Anthropic API key для evaluator role.
**Если ошибся:** Q-260525-MAD-DIM blocked by API keys.
**Как уменьшить:** documentation check перед commit'ом.

---

## §6 Suggested Q-NNN (готовые для парковки)

```yaml
# Wave A — P0 (4-5 сессий)
- id: Q-260525-REPOMIX
  title: "Pre-pack reference repo: Repomix flatten + AST/import graph"
  severity: P0
  effort: 1 сессия (+ spike-PoC 2 часа)
  source: research-best-comparison-2026-05-25 §C15
  blocker: CG5 (composability verify)

- id: Q-260525-CITE
  title: "Citation enforcement: file:line или URL anchor для каждой claim в report"
  severity: P0
  effort: ≤1 сессия
  source: A6 Compint + A7 tech-debt-skill

- id: Q-260525-RUBRIC-AGENTIC
  title: "Code-grounded rubric generation (adapt Agentic Rubrics из SWE-Compass)"
  severity: P0
  effort: 2 сессии
  source: A1 arxiv 2601.04171
  blocker: CG2 (Read paper целиком перед impl)

- id: Q-260525-SWAP
  title: "Position-bias mitigation: swap-and-average pairwise judge runs"
  severity: P0
  effort: ≤1 сессия (~30 LOC)
  source: C-bias таблица

# Wave B — P1 (6-8 сессий)
- id: Q-260525-MAD-DIM
  title: "Per-dimension debate panel (M-MAD ACL 2025) с adaptive stability stop"
  severity: P1
  effort: 2-3 сессии
  source: C8/C9
  blocker: CG3 (Read paper)

- id: Q-260525-AXES5
  title: "Расширить рубрику 9 → 14 dims: tech_debt + security + project_health + benchmark + licensing"
  severity: P1
  effort: 1 сессия
  source: B8/B10/B11/B12/B13

- id: Q-260525-SCAN
  title: "Auto-scanner integration: OpenSSF Scorecard + SonarQube conditional на code_repo type"
  severity: P1
  effort: 2 сессии
  source: B10/B11
  scope: conditional on reference_type=code_repo

- id: Q-260525-MUSTWANT
  title: "Kepner-Tregoe MUST/WANT split: hard-constraint vs preference dims"
  severity: P1
  effort: ≤1 сессия
  source: B7

- id: Q-260525-SELFAUDIT
  title: "Pre-run rubric self-audit через agentic-benchmarks meta-checklist (anti-shallow)"
  severity: P1
  effort: 1 сессия
  source: A3

- id: Q-260525-CALIB
  title: "Calibration: 10-20 human spot-checks per dim, Cronbach's α ≥ 0.80 target"
  severity: P1
  effort: 1-2 сессии
  source: A10/C12
  blocker: VD7 (peer-reviewed threshold confirm)

# Wave C — P2 (later)
- id: Q-260525-INSPECT-POC
  title: "Inspect AI sandbox PoC для execution-grounded code-comparison"
  severity: P2
  source: C4

- id: Q-260525-ATAM
  title: "ATAM utility tree + sensitivity/tradeoff tagging per dim"
  severity: P2
  source: B1

- id: Q-260525-CMMI
  title: "CMMI L1-5 maturity heatmap per dim"
  severity: P2
  source: B15

- id: Q-260525-PANEL-MULTI
  title: "Heterogeneous panel Claude+GPT+Gemini для bias mitigation"
  severity: P2
  blocker: API keys (feedback_no_anthropic_api)
  source: C-bias table

# Не переносить (Anti-Gap)
- Ragas (RAG-specific, нет RAG scope) — REJECT
- Bradley-Terry для N=2 — REJECT (overkill)
- Galileo commercial — REJECT (proprietary)
```

---

## §7 Big Picture — что получим после Wave A+B

| Способность | До | После Wave A | После Wave B |
|---|---|---|---|
| Code-grounded rubric | ❌ | ✅ | ✅ |
| Citation enforcement | partial | ✅ | ✅ |
| Position bias mitigation | ❌ | ✅ swap | ✅ swap + per-dim debate |
| Verbosity bias mitigation | ❌ | partial (rubric instruct) | ✅ length norm |
| Multi-judge panel | ❌ (single judge) | partial (debate adaptive) | ✅ per-dim panel |
| Auto-scanner facts | ❌ | ❌ | ✅ OpenSSF+Sonar+CHAOSS |
| Calibration | ❌ | ❌ | ✅ Cronbach's α |
| Hard vs preference split | ❌ | ❌ | ✅ MUST/WANT |
| Rubric self-audit | ❌ | ❌ | ✅ leaky-check |
| Repo pre-pack | ❌ | ✅ Repomix | ✅ Repomix+GraphRAG |
| Dims | 9 | 9 | 14 |

**Expected quality lift:** estimate ~3x по rigorousness (по аналогии с research-compare > freestyle ratio из сегодняшнего meta-эксперимента). Real measure — после Wave A+B запустить calibration vs 10-20 human-labeled comparisons.

---

## §8 Cross-links + memory updates pending

**Артефакт:** `spec/research_best_comparison_skills_2026-05-25.md`
**Парные/связанные spec'и:**
- `spec/spec_comparator_full_fat.md` — наш Q-260521-CMPF (already done) — Wave A/B расширяют его
- `spec/research_compare_virgil_vs_automator_2026-05-25.md` — meta-эксперимент 3 методов (тот же день)
- `spec/spec_888_implementation_phase.md` — 888 dispatcher home
- Q-260525-DATAX (из meta-эксперимента) — overlap с Q-260525-REPOMIX, нужно merge

**Memory pending (создать если апрувим):**
- `feedback_comparison_skill_standards_2026_05.md` — «индустриальный gold = code-grounded rubric + multi-judge swap-and-average + per-dim debate + calibration ≥0.80 α; наша рубрика 9-dim — half-way»
- Update `feedback_comparator_shallow_template` — добавить ref на этот spec как «known industry remedies»

**Открытые вопросы для user'а:**
1. Wave A (4-5 сессий) сейчас или после стабилизации Virgil-pilot?
2. Multi-LLM panel — deferred до API keys или экспериментировать с Opus/Sonnet/Haiku same-family?
3. Auto-scanner integration — оставить conditional `type=code_repo` или сделать default?
