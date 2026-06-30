# Spec — Фаза имплементации для 888 (забрать себе, не делегировать)

**Дата:** 2026-05-21
**Owner:** 888-persona-improver → architect Phase 2 (pending)
**Status:** research-complete, design-draft, build-pending
**Trigger:** user dandgam — «почему мы отдали BMB? нужен ресёрч ADLC vs BMad implementation, забрать себе фазу имплементации в 888 + severity tiers (мелкие/средние/тяжёлые), мелкие не плавить токенами».
**Memory:** [[feedback-research-persistence]]

---

## 1. Почему сейчас имплементация делегирована (и почему это меняем)

**Текущее состояние:** у 888 НЕТ своей фазы имплементации. Фаза 2 (architect) = только дизайн («Build prep»). Реальная сборка делегирована:
- агенты/навыки → BMB (bmad-agent-builder Phase 5 Build)
- код-фичи → Virgil / `/featurenew`

**Почему так было:** 888 задумывался как чистый dispatcher (§4 invariant «888 не редактирует файлы сам»). Делегирование снимало риск что дирижёр напортачит.

**Почему меняем (запрос user'а):** делегирование = качество имплементации **зависит от того, кому отдали**. BMB пишет «в лоб» без staging/rollback, lint-gate мягкий. Нет единого контроля. User хочет **забрать фазу себе** — встроить в 888 с собственными gate'ами и severity-tiering.

---

## 2. Research — сравнение implementation-фаз (3 источника)

### ADLC implementation (Generate)
- **Структура:** Build-фаза = **Generate** — код + тесты + API-doc + миграции + deploy-конфиг генерируются **параллельно**. Архитектор задаёт intent + границы, не пишет код.
- **Гейты:** не дискретные — **непрерывный loop** (Validate работает параллельно с Generate; Govern проверяет «output serves intent?»). Traceable артефакты на каждой фазе.
- **Слабость для нас:** rigor **постоянный**, не масштабируется по размеру задачи. Тяжело для мелочи.
- Источник: [adlc.io](https://www.adlc.io/), [IBM ADLC](https://www.ibm.com/think/topics/agent-development-lifecycle-adlc)

### BMad Phase 4 (Implementation)
- **Структура:** create-story → validate-story → dev-story → **code-review** (loop назад при issues) → retrospective. `sprint-status.yaml` = single source of truth.
- **Гейты:** **code-review обязателен для КАЖДОЙ story** без исключений. Тесты (QA) после эпика. TEA-модуль (NFR, traceability, release gates) — опционально (regulated/enterprise).
- **Слабость для нас:** гейты **task-agnostic** — одинаковы для любого размера. Облегчение только отключением TEA. Тяжело для мелких фич.
- Источник: [BMad DeepWiki](https://deepwiki.com/bmadcode/BMAD-METHOD/4.1-four-phase-methodology-overview), [BMad testing](https://docs.bmad-method.org/reference/testing/)

### BMB Phase 5 (Build)
- **Структура:** 6 фаз (Discover → Capabilities → Requirements → Draft → **Build** → Summary). Build пишет файлы + bmad-manifest.json.
- **Гейты:** lint-gate (scan-path-standards.py + scan-scripts.py) — критичное чинить, warnings проходят. Quality Optimizer только репортит, не блокирует.
- **Слабость:** пишет **in-place**, нет staging / two-phase commit / rollback. npm update стирает правки (HIGH risk).
- Источник: `/home/server/odyssey/spec/bmb-capability-audit.md`

### Industry — tiered/right-sized workflows
- GitHub Flow / TBD: short-lived branch + CI; для крупного — branch-by-abstraction (инкрементальные коммиты).
- Walking skeleton: для L/XL — тончайший build→deploy→test e2e сквозь все слои ПЕРЕД наполнением.
- Vertical slice + TDD: acceptance test пишут только ПОСЛЕ деплоя скелета.
- Источник: [LaunchDarkly TBD](https://launchdarkly.com/blog/git-branching-strategies-vs-trunk-based-development/), [Code Climate Walking Skeleton](https://codeclimate.com/blog/kickstart-your-next-project-with-a-walking-skeleton), [buildplease vertical slice](https://buildplease.com/pages/tdd-feedback-part2-100215/)

---

## 3. Вердикт — какая лучше

**Гибрид BMad-каркас × ADLC parallel-generate × severity tiering.**

| Берём | Откуда | Почему |
|---|---|---|
| Каркас create→dev→review→retro | BMad | проверенная backbone |
| Code-review + CI как **неснижаемый гейт ВСЕХ размеров** | BMad | дёшево по токенам, ловит регрессии |
| Parallel generate (код+тесты+миграции вместе) | ADLC | скорость для M/L |
| Continuous validate (не post-hoc гейт) | ADLC | раннее обнаружение |
| **S/M/L severity switch** | industry (t-shirt sizing) | **то, чего НЕТ ни у BMad (task-agnostic), ни у ADLC (rigor постоянна)** — главный наш вклад |
| Staging + rollback (snapshot → diff-view) | 888 dispatcher-wrap (уже есть) | компенсирует слабость BMB |

**Ключевой инсайт:** ни ADLC, ни BMad НЕ масштабируют процесс по размеру задачи. Это и есть наш дифференциатор — авто-tiering.

---

## 4. Severity-tiered implementation model (главное)

**Авто-классификация ДО старта** (как t-shirt sizing): число затронутых слоёв/файлов + наличие неизвестного API/архитектурной неопределённости → tier.

| Tier | Что это | Обязательно (всегда) | Можно пропустить | Бюджет |
|---|---|---|---|---|
| **S — мелкая** | bug fix, ≤1-2 файла, путь очевиден | тест-на-изменение + CI (lint/типы) + 1 review-проход | research/spike, walking skeleton, full RED-GREEN TDD, retrospective, validate-story | ~50k токенов, 1 сессия |
| **M — средняя** | фича в 1 слое | create-story → dev-story → **code-review** + тесты + continuous validate | TEA NFR/traceability, walking skeleton (нет арх. риска) | ~200k, 1-2 сессии |
| **L/XL — тяжёлая** | мульти-слой, новая архитектура, многосессионная | **walking skeleton** (e2e скелет первым) → acceptance test → TDD по vertical slices + полный review + retrospective + upfront spike | ничего из качества | 500k+, 3+ сессий |

**Неснижаемый минимум для ВСЕХ (даже S):** code-review + CI (lint + типы + тест на изменение). Это «ремень безопасности» который дёшев и обязателен всегда.

**Когда нужен upfront spike/research:** только при неизвестном API/интеграции ИЛИ архитектурной неопределённости. Путь очевиден + один слой → пропускаем (не плавим токены).

**Когда полный RED-GREEN TDD:** L/XL окупается (раннее обнаружение интеграционных проблем). S → test-after на фикс достаточно.

---

## 5. Предлагаемая структура — Phase 2.5 «Implementation» для 888

```
Фаза 1 — analyst        Анализ
Фаза 2 — architect      Build PREP (дизайн) + ВЫДАЁТ severity verdict (S/M/L)
Фаза 2.5 — IMPLEMENTER   ← НОВАЯ ФАЗА (забрана себе, не делегирована)
   ├─ читает severity hint из Фазы 2
   ├─ S: lightweight path (fix + test + review)
   ├─ M: BMad story-cycle (create→dev→review)
   ├─ L: walking-skeleton-first + TDD vertical slices
   └─ всегда: snapshot → generate → continuous validate → code-review + CI → diff-view → apply/rollback
Фаза 3 — qa             Тест
Фаза 3.5 — pilot        Пилот на реальных данных
Фаза 4 — ops            Деплой
Фаза 5 — improver       Мониторинг
```

**Новая персона:** `888-persona-implementer` (Phase 2.5). Внутри — severity router + 3 path'а (S/M/L). Может всё ещё вызывать BMB как **инструмент** для скаффолда, но gate'ы и контроль качества — **свои** (snapshot/diff-view/rollback/CI), не доверяем BMB lint'у.

**Чем отличается от текущего делегирования:** раньше 888 отдавал BMB и доверял его слабым проверкам. Теперь 888 **владеет** фазой: severity-tiering + неснижаемый gate + own rollback. BMB — опциональный скаффолд-инструмент внутри, не владелец процесса.

---

## 6. Effort

| Stage | Sessions | Activity |
|---|---|---|
| Phase 1 (analyst) | 1 | brief: pain (делегированное качество), severity-tiering требование |
| Phase 2 (architect) | 2 | design Phase 2.5 + severity classifier + 3 path'а + gate matrix |
| Phase 3 (qa) | 1 | RED tests: severity router выбирает правильный tier; неснижаемый gate срабатывает на S |
| Phase 4 (build) | 2-3 | impl `888-persona-implementer` + severity classifier + path templates |
| Phase 5 (improver) | 1 | calibration на 3 задачах (S/M/L) — проверка что мелкая не жжёт токены |
| **Total** | **7-8 sessions** | |

---

## 7. NOT в scope

- Не выкидываем BMB — он остаётся опциональным скаффолд-инструментом внутри Phase 2.5
- Не трогаем Virgil's BMad Phase 4 (это отдельный продукт-execution слой)
- Не вводим TEA-модуль (regulated/enterprise overkill для solo-оператора)

---

## 7.5. Bootstrap quality protocol (ОБЯЗАТЕЛЬНО — пока Phase 2.5 не построена)

> Пока своей фазы 2.5 нет, эти Q-спеки (IMPL/SELF/CMPF) строятся текущими средствами
> (`/auto-loop-spec-long` · persona-pipeline · прямая сессия). Чтобы качество не
> просело — **обязательный 2-уровневый протокол проверок** на время bootstrap.
> Trigger: user dandgam 2026-05-21 — «после каждой спеки и после каждого написания
> кода — проверки, пока нет 2.5».

### Доступные инструменты (verified 2026-05-21)
- `ruff` ✓ (lint Python) · `shellcheck` ✗ нет → fallback `bash -n` + code-reviewer subagent
- `~/.claude/skills/888/evals/a2b3-test-runner.sh` ✓ (comparator tests)
- `~/.claude/skills/888/scripts/menu-validator.sh` ✓ (menu-UX rules)
- review-skills: `bmad-code-review` · `bmad-security-review` · `bmad-review-edge-case-hunter` · `bmad-check-implementation-readiness` ✓
- subagents: `code-reviewer` · `code-auditor` · `security-auditor` ✓

### Уровень 1 — после КАЖДОГО написания кода (дёшево, всегда)
1. `ruff check <file>` — если Python тронут
2. `bash -n <script>` — синтаксис bash (shellcheck отсутствует)
3. relevant unit test RED→GREEN (`a2b3-test-runner.sh` / `menu-validator.sh` / `pytest`)
4. self read-back — перечитать diff как независимый рецензент

### Уровень 2 — после КАЖДОЙ спеки (перед `done`, обязательно, исключений нет)
1. **diff-view confirm** — user видит патч до apply
2. **`bmad-code-review`** (3 слоя Blind/Edge/Acceptance) — ОБЯЗАТЕЛЬНО для каждой спеки любого размера
3. **`bmad-security-review`** (4 hunter'а) — если тронуто security (canary / injection / пути / auth / secrets)
4. `menu-validator.sh` — если тронуто меню
5. **Iron Law check** — был ли RED-тест ДО кода для нового поведения?
6. **`bmad-check-implementation-readiness`** — перед финальным merge

### Severity-аналог на время bootstrap (не плавить токены на мелочи)
- **S (мелкая правка ≤2 файла):** Уровень 1 + только пп.1-2 Уровня 2 (diff-view + code-review). Пропускаем security-review (если не тронуто security), edge-case, readiness.
- **M (фича в 1 слое):** Уровень 1 + пп.1-3,5 Уровня 2.
- **L (мульти-слой, многосессионная):** полный Уровень 1 + полный Уровень 2 + ревью **после каждого vertical-slice**, не только в конце.

**Неснижаемый минимум для ВСЕХ (даже S):** diff-view + `bmad-code-review` + Iron Law. Это «ремень безопасности» — дёшев и обязателен всегда.

**Когда Phase 2.5 построена** — этот раздел становится её внутренним gate-механизмом (severity router уже встроен), bootstrap-протокол растворяется в фазе.

---

## 8. References
- **Q-NNN:** Q-260521-IMPL в `~/.claude/skills/888/methodology-888.md §5`
- **BMB audit:** `/home/server/odyssey/spec/bmb-capability-audit.md`
- **Industry URLs:** см. §2 (ADLC io/IBM · BMad DeepWiki/docs · LaunchDarkly · Code Climate · buildplease)
- **Related:** [[feedback-research-persistence]] · `spec/spec_comparator_full_fat.md` (sibling research)
