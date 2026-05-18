# Methodology — pointer

> **Внимание:** это pointer-файл. Полная универсальная методичка живёт в глобальном skill `888`.

---

## Где что лежит

| Файл | Расположение | Назначение |
|---|---|---|
| **Универсальная методичка** | `~/.claude/skills/888/REFERENCE.md` | Полный playbook ADLC + Anthropic паттерны — единый источник истины |
| **Skill dispatcher** | `~/.claude/skills/888/SKILL.md` | Краткие правила использования методички |
| **Templates** | `~/.claude/skills/888/templates/*.md` | 9 готовых шаблонов (eval suite, system message, tool design, memory, routing, security, observability, prompt injection defense, methodology-template) |
| **Применение к Virgil** | `spec/methodology-virgil.md` (рядом с этим файлом) | Gap-analysis + priority queue для Virgil конкретно |

---

## Как пользоваться

### Хочешь почитать методичку
```
$ cat ~/.claude/skills/888/REFERENCE.md
```

### Хочешь применить к новому агенту
Скажи Claude в нужном репо одно из:
- «делаю нового агента»
- `/888`
- «как развивать агента»

Skill автоматически прочитает REFERENCE.md, создаст `methodology-<agent>.md` из шаблона и проведёт через фазы ADLC.

### Хочешь поработать над Virgil
Открой `spec/methodology-virgil.md` (рядом) — там gap-analysis и priority queue под этот проект.

---

## Почему pointer, а не копия

Раньше `methodology.md` была полной копией `REFERENCE.md`. Любая правка требовала обновлять оба файла → drift'ы. Pointer-подход:
- ✅ Один источник истины (REFERENCE.md в global skill)
- ✅ Skill автоматом подгружается во всех проектах
- ✅ Нет дублирования контента
- ✅ Update в одном месте = эффект везде

---

**Last updated:** 2026-05-18 (converted from full-copy to pointer)
