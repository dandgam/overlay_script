# Storm Framework — auto-run command

**Что:** один prompt для новой Claude Code сессии. Запускает имплементацию **всего** storm framework (S1-S5) через цепочку sub-agent'ов. Orchestrator session сама ничего не пишет — только координирует и проверяет acceptance.

**Как использовать:**
1. Открой новую Claude Code сессию в `/home/server/bmad-orchestrator/`
2. Скопируй блок ниже целиком
3. Вставь в чат
4. Жди завершения (~3-5 часов реального времени с проверками)

---

## КОМАНДА (копируй всё ниже)

```
Прочитай spec/spec_888_storm_framework.md полностью. Это approved v1.2 спека (commit 19836ac).

Твоя роль: ORCHESTRATOR. Сам код не пиши. Координируй имплементацию через 5 последовательных sub-agent'ов (Agent tool). После каждого — проверяй acceptance criteria из §10 спеки и делай commit.

### Sub-agents to spawn (последовательно, НЕ параллельно — есть зависимости)

**S1 — Hooks (Layer 1)**
- subagent_type: general-purpose
- model: sonnet
- prompt: |
    Прочитай spec/spec_888_storm_framework.md §5 (hook contracts), §10 (S1 acceptance), §19 (regression detection — +error-trap.sh +test-result.sh).
    Прочитай существующий ~/.claude/settings.json — НЕ перезаписывать, только merge hooks section.
    Напиши 8 bash-хук-скриптов в ~/.claude/hooks/:
      intent-detector.sh (UserPromptSubmit)
      patch-counter.sh (PostToolUse on Bash matcher=git commit)
      code-gate.sh (PreToolUse on Edit|Write — включая T11 check per §19)
      merge-guard.sh (PreToolUse on Bash matcher=git merge|push)
      destructive-guard.sh (PreToolUse on Bash dangerous patterns)
      audit-trail.sh (Stop hook — включая pattern_report.py invocation если ≥24h)
      error-trap.sh (PostToolUse on Bash exit ≠ 0 — §19 R1)
      test-result.sh (PostToolUse on Bash matcher=pytest|cargo test|npm test — §19 R4)
    Каждый хук: shebang #!/bin/bash, jq fallback (command -v jq || exit 0), 
    exit codes per spec, stderr messages per spec.
    Python-вызовы внутри хуков — stub'ы пока (echo + exit 0); реальная имплементация в S2.
    Обнови settings.json (merge hooks section, не replace).
    Запусти test scenarios: 8 ручных тестов (echo JSON | hook.sh) — все хуки должны срабатывать корректно.
    Output: список созданных файлов + результаты 8 тестов в формате PASS/FAIL.
    НЕ коммить — это сделает orchestrator.

**ACCEPTANCE CHECK после S1:**
- Verify 6 файлов в ~/.claude/hooks/ существуют и executable
- Verify settings.json валиден через `jq . ~/.claude/settings.json`
- Verify hooks section содержит 6 entries
- Если всё OK → commit "feat(storm): S1 hooks — Layer 1 (6 deterministic gates)"
- Если fail → spawn code-auditor sub-agent с issue, не патчи сам

---

**S2 — Storm Core (Layer 2) + §19 detectors**
- subagent_type: general-purpose
- model: sonnet
- prompt: |
    Прочитай spec/spec_888_storm_framework.md §6 (Python contracts), §10 (S2 acceptance), §18 (auto-selection cascade), §19 (regression detection полностью).
    Создай ~/.claude/skills/888/storm/ directory + написать:
      intent-classifier.py (§6.1 contract, ~100 LOC) — добавить T11 detection support
      storm-orchestrator.py (§6.2 contract, ~200 LOC, поддержка HEADLESS_MODE)
      taxonomy-checker.py (§6.3 contract, ~80 LOC)
      audit-trail.py (§6.4 contract, ~120 LOC)
      patch_counter.py (~60 LOC, per-scope window 14d)
      scope_from_path.py (~40 LOC)
    §19 detectors (новые модули):
      fingerprint_tracker.py (~80 LOC, R1)
      hot_files.py (~50 LOC, R2)
      cycle_detector.py (~60 LOC, R3 — difflib similarity)
      test_regression.py (~80 LOC, R4 — pytest/cargo/npm parsers)
      pattern_report.py (~150 LOC, R5 — daily aggregate)
    Init files: state.json={"initiatives":[]}, events.jsonl=empty, decision-log.md=header only, 
    patch-counter.json={}, error-fingerprints.jsonl=empty, hot-files.json={}, 
    commit-history.jsonl=empty, test-failures.jsonl=empty.
    Type hints обязательны (Python 3.11+). Append-only логика для всех jsonl.
    Selection cascade (§18): required → static_rules → llm-judge fallback → user_override.
    Write unit tests: tests/test_storm_*.py + tests/test_regression_*.py — coverage ≥80%. Run pytest.
    НЕ коммить.

**ACCEPTANCE CHECK после S2:**
- pytest passes (≥80% coverage)
- Manual smoke: `python3 intent-classifier.py "хочу внедрить multi-LLM"` → JSON с T1
- Manual smoke: `python3 patch_counter.py increment virgil-test` + `get virgil-test --window 14d` → 1
- Если OK → commit "feat(storm): S2 core — Layer 2 Python (orchestrator + classifier + audit)"
- Если fail → spawn code-auditor, не патчи

---

**S3 — Embedded + manifest (Layer 3)**
- subagent_type: general-purpose
- model: sonnet
- prompt: |
    Прочитай spec/spec_888_storm_framework.md §7 (manifest schema) и §10 (S3 acceptance).
    Создай ~/.claude/skills/888/storm/embedded/ + скопируй 6 файлов:
      bmad-advanced-elicitation/methods.csv → embedded/elicitation-methods.csv
      bmad-review-edge-case-hunter/SKILL.md → embedded/edge-case-hunter.md
      bmad-review-adversarial-general/SKILL.md → embedded/adversarial-protocol.md
      bmad-code-review/SKILL.md → embedded/code-review-triage.md
      bmad-check-implementation-readiness/SKILL.md → embedded/readiness-checklist.md
      888-persona-comparator/SKILL.md → embedded/comparator-rubric-9d.md
    Создай 2 originals (новые): failure-mode-template.md + taxonomy-template.md per §7 description.
    Заполни manifest.json по схеме §7 — origin path, version, last_sync для каждого absorbed.
    Напиши sync_manifest.py (~80 LOC): команды `check-upstream`, `diff <file>`, `adopt <file>`, `reject <file>`.
    Test: `sync_manifest.py check-upstream` → пустой divergence report для свежих absorbed.
    НЕ коммить.

**ACCEPTANCE CHECK после S3:**
- 8 файлов в embedded/ (6 absorbed + 2 originals)
- manifest.json валиден (jq), все 6 entries имеют source_path + last_sync
- sync_manifest.py не падает
- Если OK → commit "feat(storm): S3 embedded — Layer 3 (6 absorbed skills + manifest + sync tool)"

---

**S4 — Scenarios + phase-embedded integration**
- subagent_type: general-purpose
- model: opus
- prompt: |
    Прочитай spec/spec_888_storm_framework.md §4, §8, §17 (phase mapping), §18 (selection cascade), §10 (S4 acceptance).
    Создай 11 scenario файлов в ~/.claude/skills/888/storm/scenarios/T1-T11*.md per §8 template (включая T11_regression_detected.md per §19).
    Каждый scenario с full YAML frontmatter: trigger_id, detection_keywords, required_methods, 
    optional_methods_pool, selection_rules (минимум 2 rules per scenario), selection_fallback, 
    max_optional_selected, block_until, closes_gap.
    
    Phase-embedded edits per §17 — добавь invocation в 7 файлов:
      - ~/.claude/skills/888/vendor/bmad-builder/src/skills/bmad-agent-builder/build-process.md (Phase 4.5)
      - ~/.claude/skills/888-persona-analyst/SKILL.md (T1 end of phase)
      - ~/.claude/skills/888-persona-architect/SKILL.md (T1+T20 start)
      - ~/.claude/skills/888-persona-implementer/SKILL.md (T7 check start)
      - ~/.claude/skills/888-persona-qa/SKILL.md (T8 start)
      - ~/.claude/skills/888-persona-improver/SKILL.md (T9 check start)
      - ~/.claude/skills/bmad-auto-dev/SKILL.md (T1+#34 story-start, T8 pre-merge)
    
    Обнови ~/.claude/skills/888/SKILL.md — добавь секцию "Storm Framework" с описанием.
    
    Validation: каждый scenario.md проходит YAML frontmatter parse. Phase-embedded edits 
    проверь через `grep "storm-orchestrator.py" <each-file>` — должен найтись.
    НЕ коммить.

**ACCEPTANCE CHECK после S4:**
- 11 файлов scenarios/T*.md созданы (включая T11)
- 7 файлов с phase-embedded edits применены (grep verify)
- 888 SKILL.md содержит storm section
- Smoke: imitate "хочу внедрить foo" prompt → intent-detector hook + classifier → T1 detected
- Если OK → commit "feat(storm): S4 scenarios + phase-embedded — 10 triggers + 7 workflow edits"

---

**S5 — End-to-end test (interactive + headless)**
- subagent_type: general-purpose
- model: opus
- prompt: |
    Прочитай spec/spec_888_storm_framework.md §10 (S5 acceptance) — оба сценария.
    
    Run Scenario A (interactive):
      Simulate user prompt "создам нового агента foo-bar-baz".
      Trace: intent-detector → context inject → 888 dispatcher invokes storm-orchestrator T2 → 
      5 methods run → spec/agent_foo-bar-baz_storm.md создаётся → 
      bmad-agent-builder Phase 5 проходит code-gate → audit-trail записывает session_end.
      Verify: events.jsonl ≥6 событий цикла, artifact valid через taxonomy-checker.
    
    Run Scenario B (headless):
      `HEADLESS_MODE=1 python3 storm-orchestrator.py T2 bar-baz-headless`
      Verify: storm method selection via static_rules + LLM-judge fallback (если), 
      artifact создан, full reasoning в events.jsonl.
    
    Run Scenario C (regression detection, §19):
      Simulate 3 identical error fingerprints через `python3 fingerprint_tracker.py inject test-error-X` 3 раза.
      Verify: после 3-го T11 fired в events.jsonl, code-gate блокирует Edit в scope test-error-X.
      Затем `storm-orchestrator.py T11 test-error-X` → создаёт regression artifact → code-gate пропускает.
    
    Документируй: каждый bug найденный в e2e + fix (если тривиальный) или escalate (если major).
    Если major bug → НЕ патчи в S5, документируй в spec/_storm_s5_findings.md как Q-260526-STRM-<N>.
    НЕ коммить.

**ACCEPTANCE CHECK после S5:**
- Оба сценария A и B завершились (с/без bugs)
- Все 5 methods запустились в каждом сценарии
- Artifacts содержат реальное content (не TODO)
- events.jsonl содержит storm_method_selection events с reasoning
- Если OK → commit "feat(storm): S5 e2e validated — interactive + headless paths"
- Если bugs → commit "feat(storm): S5 e2e — found N bugs (see spec/_storm_s5_findings.md)"

---

### После всех 5 sub-agent'ов

Финальный sweep:
1. `git log --oneline | head -10` — show 5 commits (S1-S5)
2. Update memory: `~/.claude/projects/-home-server-bmad-orchestrator/memory/project_milestone_storm_framework_shipped.md`
3. Report финальный статус: какие S passed, какие bugs, что в queue.

### Принципы (применяй ко всему)

- 1 task = 1 commit per session S (правило commit discipline)
- Если sub-agent fail acceptance → spawn code-auditor (model=opus), НЕ патчи сам (правило №9)
- Каждый sub-agent читает spec ОТ И ДО — не полагайся на summary
- Если найдёшь gap в spec → STOP и предложи user обновить спеку (правило M3) — не патчь код
- Audit trail: после каждого S sub-agent логирует свои действия в `_storm_audit/S<N>_run_<date>.md`

Начинай с S1. Сообщи user когда S1 готов с commit hash, и продолжай S2 без паузы.
```

---

## Что эта команда делает

| Step | Sub-agent | Model | Время |
|---|---|---|---|
| Read spec | (orchestrator) | inherits | 5 мин |
| S1 hooks | general-purpose | sonnet | ~30-40 мин |
| Acceptance + commit | (orchestrator) | inherits | 5 мин |
| S2 Python core | general-purpose | sonnet | ~50-70 мин |
| Acceptance + commit | (orchestrator) | inherits | 5 мин |
| S3 embedded | general-purpose | sonnet | ~20-30 мин |
| Acceptance + commit | (orchestrator) | inherits | 5 мин |
| S4 scenarios | general-purpose | opus | ~70-90 мин |
| Acceptance + commit | (orchestrator) | inherits | 5 мин |
| S5 e2e test | general-purpose | opus | ~50-70 мин |
| Final sweep + report | (orchestrator) | inherits | 10 мин |

**Total: ~4-5 часов реального времени**, 5 коммитов, полностью автономно.

## Что user должен делать пока команда работает

**Ничего.** Orchestrator сам справится. Если попадётся real blocker (typo в спеке, missing dependency) — orchestrator остановится и спросит.

Можно периодически проверять `git log --oneline | head -5` чтобы видеть прогресс.

## Если что-то пойдёт не так

В новой сессии скажи: «покажи последний `_storm_audit/S*_run_*.md`» — там полный лог что делал каждый sub-agent.
