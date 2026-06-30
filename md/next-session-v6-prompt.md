Продолжаем 888 storm real-pilot — ШАГ 3: В6 forced-T9 (BMAD_888_T9_FORCED) — ВКЛЮЧЕНИЕ.

Контекст (прочитай ПЕРЕД стартом): MEMORY.md + память
project_milestone_b5_enabled_consult_budget_2026-06-02.md (там итог всех 3 прогонов В5)
+ feedback_single_source_drift_class.md.

СДЕЛАНО (ШАГ 2 ЗАКРЫТ): В5 decider включён (BMAD_888_DECIDER_ENFORCE=on) и валидирован
3 реальными B5-циклами (block→storm→unblock→fix), все коммиты в ~/.claude git, ветка
888-wave6-t9forced:
 - 01814cb consult-budget (первый, но storm был off-target из-за slug=ветка)
 - 69ae63f slug-фикс: per-task slug + описание задачи в claude-p → storm теперь on-target
 - 954ae8c DGSB sibling-sweep на РЕАЛЬНОЙ бэклог-Q (storm on-target, переубедил по fail-closed)
Флаг В6 BMAD_888_T9_FORCED в ~/.claude/skills/888/config/.env = off (canary-armed).

ПРЕДУСЛОВИЕ (НЕ включать В6 пока не закрыто): первый storm (когда slug был ещё сломан)
случайно проанализировал механизм T9 и нашёл 2× P0 pre-enable блокера. Артефакт:
/home/server/bmad-orchestrator/spec/taxonomy_888-wave6-t9forced_storm.md (секция #35 FMA).
 - F1 [P0]: reason-migration убивает streak. _t9_streak считает хвост ОДНОГО reason-строки,
   но code-gate эмитит 6 РАЗНЫХ reason (decider_storm_pending / decider_placeholder / storm_pending /
   storm_mandatory_incomplete / T7_patch_count_exceeded / T11_regression_unresolved) → застрявший
   агент мигрирует между ними → streak сбрасывается → НИКОГДА не 3 → forced-T9 не срабатывает.
   Фикс: считать по СТАБИЛЬНОМУ ключу (scope или reason-КЛАСС), не сырой reason.
 - F5 [P0, UNVERIFIED — СНАЧАЛА ПРОВЕРЬ]: producer↔orchestrator path-seam. t9-counter.sh пишет
   required_artifact = spec/t9_<scope>_storm.md, а storm-orchestrator T9 → _storm_audit/pause_<date>.md
   (≠) → вечный deadlock (ТОТ ЖЕ класс single-source-drift, что E5/slug, см. feedback-память).
   Проверь реально (grep оба пути), если расходятся — фикс единым источником (writer=reader).

ЗАДАЧА:
1. Закрой F1 и F5 как обычные баг-фиксы (T7) — они сами пройдут через ВАЛИДИРОВАННЫЙ В5-цикл
   (decider on → storm mandatory → fix). Storm теперь on-target (slug-фикс), будет про T9.
   Каждый: реальные exit-коды, тесты, 0 регрессий, коммит.
2. ТОЛЬКО ПОСЛЕ закрытия F1+F5 → включи В6: BMAD_888_T9_FORCED=on в config/.env (покажи diff).
3. Обкатай на реальной задаче — покажи реальными exit-кодами: подряд N code_gate_blocked одной
   причины по scope → N=2 nudge «переосмысли» → N=3 _set_pending T9 → per-scope gate блок до
   T9-артефакта. (детали механизма В6 — в project_storm_determinism_design_2026-06-01.md)

СТРАХОВКА (как на В5): первое включение на живой задаче. Если поведение НЕ как ждёшь
(блок не там / не сработал / второй P0 всплыл) — НЕ продавливай: зафиксируй реальный exit-код,
откати флаг (BMAD_888_T9_FORCED=off = одна строка, безопасно), опиши расхождение (ждал X, got Y).
Лучше откатить и разобрать, чем продавить.

НЕ в фокусе этой сессии (parked, не трогать): Q-260602-JQ-CONSOLIDATE (full F7 миграция ~10 хуков),
Q-260602-JQ-PARSE-CONTRACT (F3/F4) — это про jq, отдельно от В6. См. methodology-888.md.

После В6 → план 7 волн В0→В6 полностью закрыт.
