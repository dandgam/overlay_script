# Spec — Bundled Adversarial Review Skill (zero-critical gate + auto-fix loop)

**Версия:** 0.1.0
**Дата:** 2026-05-20
**Автор:** AABIT
**Статус:** ready for /auto-loop-spec-long (после verifier_contracts)
**Источник:** bmad-automator `skills/bmad-story-automator-review/` + `docs/review-workflow.md`
**Размер:** LONG (6-10 сессий)
**Приоритет:** P1 — quality gap, у нас сейчас этого нет в pipeline

---

## 1. Executive Summary

Сейчас review в Virgil = вызов skill `bmad-code-review` через directive prompt + парсинг jsonl. Это **generic** review без чёткого gate: «zero critical issues remaining after auto-fix». Нет автоматического fix loop. Нет git-reality cross-check (этот патч уезжает в spec_dual_source_verdict, но без bundled review skill он остаётся reactive — мы только детектим mismatch, не лечим).

Конкурент bundle'ит **собственный** review skill с жёсткой семантикой:

1. Inputs: story file + acceptance criteria + tasks/subtasks + Dev Agent Record + File List + actual git changes + sprint-status.yaml.
2. **Severity model:** только `CRITICAL` блокирует gate. High/Medium/Low логируются, не блокируют.
3. **Auto-fix loop** — если AI может, чинит сам внутри той же сессии (тесты + код).
4. **Sync sprint-status / story-file** — после прохода review записывает `Status: done` или `in-progress` в оба источника.
5. **Excludes** non-source surfaces (`_bmad/`, `_bmad-output/`, IDE config) — review не отвлекается на доки.

Перенос даёт нам: явный quality gate, autofix loop вместо external retry, и close регрессии типа NEW-9/21/26 на источнике, а не на patch'ах.

---

## 2. Goals / Non-Goals

### Goals
- G1: создать `src/bmad_orchestrator/skills_repo/virgil-adversarial-review/` (bundled skill, embedded в Virgil).
- G2: skill принимает inputs: `story_file`, `acceptance_criteria`, `dev_record`, `git_diff`, `excluded_paths`.
- G3: outputs JSON: `{critical: [...], high: [...], medium: [...], low: [...], auto_fixed: [...], status: done|in-progress}`.
- G4: auto-fix loop (max N attempts из policy.workflow.repeat.review.maxCycles).
- G5: после прохода — sync status в `_bmad-output/sprint-status.yaml` (если есть) и в story-file frontmatter.
- G6: review-runner wrapper в Virgil вызывает bundled skill вместо external `bmad-code-review`.

### Non-Goals
- НЕ заменяем `/bmad-code-review` глобально — оставляем как опцию через policy.
- НЕ делаем security-specific review (security_review.py отдельный pipeline).
- НЕ генерим threat-model — это `/bmad-threat-model`.

---

## 3. Архитектура

```
virgil-adversarial-review skill:
  SKILL.md (≤10 строк per spec_competitor_quickwins)
  workflow.md
    - Load: story_file, ACs, dev_record, git diff
    - For each AC: verified vs claimed in dev_record
    - For each file in dev_record.file_list: exists in git diff?
    - Severity classify findings
    - If criticals: present + try_auto_fix (loop while attempts < max)
    - When 0 criticals: sync sprint-status.yaml + story-file Status: done; exit
    - If max attempts hit: write findings, exit with status: in-progress
  templates/
    - findings-report.md
  checks/
    - ac-coverage.md
    - file-list-vs-git.md
    - security-keywords.md
    - test-coverage.md
```

```
runtime/review_runner.py (refactor):
  def run_review(story_id, policy) -> ReviewResult:
      inputs  = collect_inputs(story_id)
      cycle   = 0
      while cycle < policy.workflow.repeat.review.maxCycles:
          result = invoke_bundled_skill(inputs)   # via Claude SDK
          if result.criticals_count == 0:
              sync_status(done=True)
              return result
          if not result.can_autofix:
              break
          apply_fix_patch(result.autofix_patch)   # in worktree
          cycle += 1
      sync_status(done=False)
      return result
```

## 4. Изменения по файлам

| Файл | Что |
|---|---|
| `src/bmad_orchestrator/skills_repo/virgil-adversarial-review/` (новый пакет) | bundled skill |
| `src/bmad_orchestrator/runtime/review_runner.py` (новый или refactor security_review.py) | wrapper |
| `src/bmad_orchestrator/runtime/status_sync.py` (новый) | sync sprint-status + story-file |
| `src/bmad_orchestrator/runtime/embedded_skills.py` | регистрация bundled skill |
| `src/bmad_orchestrator/runtime/verifiers/review_completion.py` | вызывает review_runner |
| `src/bmad_orchestrator/runtime/phase4_subscribers.py` | переключается с external `/bmad-code-review` на bundled (через policy flag) |
| `tests/skills/test_adversarial_review.py` (новый) | 10+ |
| `tests/runtime/test_review_runner.py` (новый) | 8+ |
| `tests/runtime/test_status_sync.py` (новый) | 5+ |

## 5. Acceptance Criteria

- AC1: Bundled skill даёт structured JSON output на любую mock-story.
- AC2: При 0 criticals — sprint-status.yaml И story-file frontmatter обновляются на `done` атомарно (либо оба, либо никто).
- AC3: При criticals — auto-fix loop пытается ≤maxCycles раз; каждый attempt commit'ит в worktree branch.
- AC4: При attempt fail — exit `in-progress`, findings записаны в `_bmad-output/virgil/review-reports/<story>-<ts>.md`.
- AC5: Excluded paths (`_bmad/`, `_bmad-output/`, `.bmad/`, `node_modules/`) не попадают в review surface.
- AC6: Regress NEW-26 test passes (interactive review skill вызывает halt instead of error).
- AC7: Tests grow ≥23.

## 6. Test Plan

| Тест | Сценарий |
|---|---|
| `test_review_zero_criticals_marks_done` | happy path |
| `test_review_with_criticals_no_autofix_returns_in_progress` | bad path |
| `test_review_autofix_loop_max_cycles` | bounded retry |
| `test_review_autofix_succeeds_marks_done` | self-heal |
| `test_review_excludes_non_source_paths` | scope |
| `test_review_detects_ac_not_implemented` | severity |
| `test_review_detects_file_list_vs_git_mismatch` | hallucination |
| `test_status_sync_atomic_both_or_neither` | atomicity |
| `test_status_sync_handles_missing_sprint_yaml` | tolerance |
| `test_review_runner_uses_dual_source_verdict` | integration |

## 7. Rollout

- Feature flag: `BMAD_BUNDLED_REVIEW=1` (default off → smoke pilot → on for new runs).
- Сравнение: первые 5 prod runs идут оба review (external + bundled), сравнение verdict'ов, anomaly logged.

## 8. Risks

| Риск | Митигация |
|---|---|
| Bundled review hallucinates "fixed" | git_reality_check (из spec_dual_source_verdict) + diff-size gate |
| Auto-fix loop вечно крутится | max cycles из policy + cost_tracker hard-cap |
| Sync status разъезжается с реальностью | атомарный sync через file lock |

## 9. Effort

8-10 сессий. (1-2) skill design + prompts, (3-4) review_runner + autofix loop, (5) status_sync, (6) verifier integration, (7-8) tests, (9-10) parallel-run validation + pilot.

## 10. Dependencies

- Зависит от: spec_verifier_contracts (review_completion verifier).
- Зависит от: spec_dual_source_verdict (resolver + git_reality).
- Зависит от: spec_competitor_quickwins (slim SKILL.md, log pre-filter).
- Блокирует: ничего (но closes большой quality gap).
