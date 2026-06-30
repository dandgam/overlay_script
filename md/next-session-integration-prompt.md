Продолжаем 888 storm real-pilot — ИНТЕГРАЦИЯ В1+В5+В6 (накопительная ветка → main).

Контекст (прочитай ПЕРЕД стартом): MEMORY.md + память
project_milestone_b6_enabled_t9forced_2026-06-02.md (полный итог: F1/F5, В6 включён+validated,
jq-миграция, ПОЛНЫЙ run-all-gates база) + feedback_single_source_drift_class +
feedback_canary_gap_root_of_breeding.

СДЕЛАНО (сессия 2026-06-02, всё в ~/.claude git, ветка 888-wave6-t9forced):
 - F1 (fc945b3): _t9_streak считает code_gate_blocked по scope БЕЗ привязки к причине
   (gate эмитит 6 разных reason → агент мигрирует → streak не достигал 3 → forced-T9 не срабатывал).
 - F5 (ca8d23a): путь T9-артефакта из единого источника (--print-artifact-path), не хардкод.
 - В6 ВКЛЮЧЁН: BMAD_888_T9_FORCED=on в config/.env (gitignored). Live-test 9/9 реальным флагом
   (N=2 nudge → N=3 forced → code-gate exit2 → unblock exit0). План 7 волн В0→В6 ЗАКРЫТ.
 - jq-миграция (5aab1f7): ещё 7 хуков на единый источник _require_jq (F7 single-source-drift).
 - ПОЛНЫЙ run-all-gates: 75/79 green, 0 НОВЫХ регрессий продакшен-кода. Все красные = тест-инфра/
   env/state (сломанный мета-харнесс · fake-root фикстуры без зависимостей · cost-cap пробит $28≥$20).

СОСТОЯНИЕ ФЛАГОВ (config/.env, gitignored, уже live): В1 BMAD_888_FIXTURE_CANARY=on ·
В5 BMAD_888_DECIDER_ENFORCE=on · В6 BMAD_888_T9_FORCED=on. Откат любого = вернуть off (1 строка).

ЗАДАЧА — интеграция:
1. Проверь расхождение ветки 888-wave6-t9forced от main (~/.claude): git log/diff. Накопительная
   (от wave5←wave4←...), может быть много коммитов.
2. Если diverged значительно / конфликты / prod-mirrored файлы → используй skill git-merge-integration
   (9 стадий: preflight → merge → verify → deploy/rollback). Иначе обычный merge.
3. Слить 888-wave6-t9forced → main. Флаги в config/.env (gitignored) НЕ в ветке — останутся live.
   Merge несёт только код+тесты. НЕ force-push, НЕ --no-verify.
4. После merge — sanity: точечные сюиты зелёные (t9-counter 30 · decider 32 · dgsb-jq 22), флаги on.

WIDENET (Q-260602-WIDENET, owner-эмфаза — НЕ чинить в этой сессии, но держать в курсе):
«0 регрессий» волн держалось на точечных сюитах по blast-radius (blast-radius МОЖЕТ промахнуться).
Фикс слепого пятна: (1) run-all-gates ПЕРЕД каждым флипом флага; (2) fixture-completeness contract
(fake-root/fake-888 обязаны нести зависимости целей — qid-validate/_require_jq/canary; корень =
single-source-drift в тест-инфре). + починить smoke_01 + sandbox мета-харнесса regression-smoke_test.

PARKED (не блокеры): F2/F3/F4 [P1] · F6/F7/F5b [P2] (из T9-FMA) · Q-260602-JQ-PARSE-CONTRACT (F3/F4
PATH-shadow) · Q-260602-RGSU-SYSTEMIC (мета-харнесс GREEN-path) · cost-cap пробит ($28≥$20, env/state).

СТРАХОВКА: интеграция накопительной ветки = значимая операция. Если конфликты/расхождение неясны —
НЕ продавливай merge: зафиксируй состояние, опиши расхождение, спроси. Лучше разобрать, чем сломать main.
