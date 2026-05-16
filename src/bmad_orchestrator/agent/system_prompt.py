"""System prompt builder (FS4 — B10 full impl).

Spec §11.2 + §17.4 + §22 acceptance — каждый «большой» блок несёт
``cache_control = {"type": "ephemeral", "ttl": "1h"}``. С 06.03.2026 default
TTL у Anthropic = 5 мин; без явного 1h cache hit rate скатывается к нулю.

Структура blocks (порядок фиксирован — кэш строится по префиксу):
1. project_context (~25K токенов)       — ttl=1h, наибольший слой
2. operational_rules (spec §17 + §9)    — ttl=1h
3. tool_and_skill_metadata              — ttl=1h
4. few_shot_examples (10-15 пар)        — ttl=1h
5. personality                          — без cache_control (мелкий и волатильный)

Контракт ``build_system_prompt`` возвращает list[dict] — Anthropic Messages API
schema (TextBlockParam). SDK wrapper (``agent.run.build_agent_options``) либо
склеит в строку (если ``ClaudeAgentOptions.system_prompt: str``), либо передаст
list as-is если SDK позже добавит block-форму. cache_control в обоих случаях
оседает в финальном API payload через ``betas=["prompt-caching-2024-07-31"]``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

# Anthropic charges ~$1.50 / 1M tokens cache read (Opus 4.7) — лучше ужать project
# context чем платить полный input rate. 25K — мягкий cap, ~95% реальных проектов
# умещается. Эвристика: 4 chars / token.
PROJECT_CONTEXT_TOKEN_CAP: int = 25_000
TOKEN_CHARS: int = 4
CACHE_1H: dict[str, str] = {"type": "ephemeral", "ttl": "1h"}


def build_system_prompt(
    project_root: Path,
    wave: str,
    locale: str = "ru",
) -> list[dict[str, Any]]:
    """Build cached system prompt blocks for ClaudeSDKClient.

    Returns list of TextBlockParam dicts с cache_control где надо.
    """
    blocks: list[dict[str, Any]] = []

    project_context = _load_project_context(project_root, wave)
    blocks.append(
        {
            "type": "text",
            "text": project_context,
            "cache_control": dict(CACHE_1H),
        }
    )

    blocks.append(
        {
            "type": "text",
            "text": _operational_rules(locale),
            "cache_control": dict(CACHE_1H),
        }
    )

    blocks.append(
        {
            "type": "text",
            "text": _tool_and_skill_metadata(),
            "cache_control": dict(CACHE_1H),
        }
    )

    blocks.append(
        {
            "type": "text",
            "text": _few_shot_examples(locale),
            "cache_control": dict(CACHE_1H),
        }
    )

    blocks.append({"type": "text", "text": _personality(locale)})

    return blocks


def blocks_to_string(blocks: list[dict[str, Any]]) -> str:
    """Concat blocks into a single string for SDK consumers that require str.

    Cache markers are dropped by string concat — that's expected. The Anthropic
    CLI subprocess used by claude-agent-sdk re-applies caching at the API
    boundary (system block gets cache_control automatically when long enough).
    """
    parts: list[str] = []
    for block in blocks:
        text = block.get("text", "") if isinstance(block, dict) else ""
        if text:
            parts.append(text)
    return "\n\n".join(parts).strip()


# ── project context (B10) ────────────────────────────────────────────────────


ORCHESTRATOR_ROOT: Path = Path("/home/server/bmad-orchestrator").resolve()


def _load_project_context(project_root: Path, wave: str) -> str:
    """Concat target CLAUDE.md + orchestrator CLAUDE.md + epics.md + sprint-status.

    SECURITY — path-traversal guard: ``project_root`` is canonicalised; every
    context-source path must (post-resolve) stay under ORCHESTRATOR_ROOT (for
    the orchestrator's own CLAUDE.md) or under the resolved ``project_root``
    (for everything else). Symlinks are rejected. Out-of-root paths surface as
    ``(missing: <path>)`` markers — never read.

    Чтения тихие (missing файл → пустой блок с отметкой). Финальный размер
    ограничен ``PROJECT_CONTEXT_TOKEN_CAP``. Truncation — последние N символов
    отрезаются от наибольшего блока (epics обычно), плюс trailing маркер.
    """
    try:
        root_resolved = project_root.resolve(strict=False)
    except (OSError, RuntimeError):
        root_resolved = project_root

    parts: list[str] = [
        "# PROJECT CONTEXT (cached, ttl=1h)",
        f"# wave: {wave}",
        "",
    ]

    sources = _project_context_sources(root_resolved)
    for label, path, allow_root in sources:
        parts.append(f"## {label} ({path})")
        parts.append(_safe_read_text(path, allow_root))
        parts.append("")

    full = "\n".join(parts).rstrip() + "\n"
    cap_chars = PROJECT_CONTEXT_TOKEN_CAP * TOKEN_CHARS
    if len(full) > cap_chars:
        head = full[:cap_chars]
        trailing = (
            f"\n\n[truncated at {PROJECT_CONTEXT_TOKEN_CAP} tokens "
            f"(~{cap_chars} chars); original={len(full)} chars]\n"
        )
        return head + trailing
    return full


def _project_context_sources(
    project_root: Path,
) -> list[tuple[str, Path, Path]]:
    """Resolve context-source paths with their allow-root (allow_root, the
    only directory each path is permitted to live under).

    BMad layout transitioned from ``_bmad-output/`` to ``_bmad/`` mid-2026; we
    accept either by probing both. Sprint-status yaml may live under
    ``planning-artifacts/`` или ``implementation-artifacts/`` depending on epic
    phase — we pick the first existing.

    Returns tuples of (label, candidate_path, allow_root). ``allow_root`` is
    the directory boundary that the eventual ``_safe_read_text`` call must
    enforce.
    """
    target_claude = project_root / "CLAUDE.md"
    orch_claude = ORCHESTRATOR_ROOT / "CLAUDE.md"

    epics = _first_existing(
        project_root / "_bmad" / "planning-artifacts" / "epics.md",
        project_root / "_bmad-output" / "planning-artifacts" / "epics.md",
    )

    sprint_status = _first_existing(
        project_root / "_bmad" / "implementation-artifacts" / "sprint-status.yaml",
        project_root / "_bmad-output" / "implementation-artifacts" / "sprint-status.yaml",
        project_root / "_bmad" / "planning-artifacts" / "sprint-status.yaml",
    )

    return [
        ("TARGET CLAUDE.md", target_claude, project_root),
        ("ORCHESTRATOR CLAUDE.md", orch_claude, ORCHESTRATOR_ROOT),
        ("EPICS", epics, project_root),
        ("SPRINT-STATUS", sprint_status, project_root),
    ]


def _first_existing(*candidates: Path) -> Path:
    """Return the first candidate that exists; else the first one (so the
    missing-file marker shows a canonical path)."""
    for cand in candidates:
        if cand.is_file():
            return cand
    return candidates[0]


def _is_within(child: Path, root: Path) -> bool:
    """True iff ``child`` (already resolved) is equal to or under ``root``
    (already resolved). Pure prefix check on ``parts`` — does not touch FS.
    """
    try:
        child.relative_to(root)
    except ValueError:
        return False
    return True


def _safe_read_text(path: Path, allow_root: Path) -> str:
    """Read file as utf-8 with path-traversal + symlink protection.

    Returns one of:
    - file contents (utf-8)
    - ``(missing: <path>)`` — file absent OR rejected by guard
    - ``(unreadable: <path> — <err>)`` — IO/decode failure

    Guards:
    1. Reject if ``path`` (or any parent up to a missing component) is a
       symlink — symlinks can escape ``allow_root``.
    2. Canonicalise + ensure the resolved path is under ``allow_root``.
    """
    try:
        for parent in [path, *path.parents]:
            if parent.is_symlink():
                return f"(missing: {path})"
            if parent == allow_root:
                break
        resolved = path.resolve(strict=False)
        if not _is_within(resolved, allow_root):
            return f"(missing: {path})"
        if not resolved.is_file():
            return f"(missing: {path})"
        return resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return f"(unreadable: {path} — {exc})"


# ── operational rules (B10) ──────────────────────────────────────────────────


@lru_cache(maxsize=2)
def _operational_rules(locale: str) -> str:
    """Hardcoded rules — spec §17 disambiguation + §9 safety + §10 IN-scope.

    Cached per-locale (only ``ru``/``en`` for MVP).
    """
    if locale == "ru":
        return _OPERATIONAL_RULES_RU
    return _OPERATIONAL_RULES_EN


_OPERATIONAL_RULES_RU: str = """\
# OPERATIONAL RULES (cached, ttl=1h)

## Safety (§9)
1. **Destructive ops требуют подтверждение** через inline buttons:
   stop/kill/delete/rollback/git push --force/git reset --hard.
2. **Read-only ops выполняй сразу:** /status, /budget, list_*, get_*, read_*.
3. **Never `git push --force`** на main/master без explicit signed-token.
4. **Never bypass hooks** (`--no-verify`, `GIT_*_NO_VERIFY=*`).
5. **Always validate worker paths** через worktree env / branch_isolation.
6. **PII** — scrub_input/scrub_output на каждый user-facing edge.

## IN-scope (§10)
- DAG planning + worker dispatch (≤max_parallel)
- Code-review gate перед merge → integration/<wave>
- Retro write на wave_boundary + lessons compaction
- Cost watchdog: alarm/halt thresholds (см. budget tools)
- Elicitation escalation to human (Telegram/email)
- Voice STT через configured provider (whisper_local default)

## OUT-of-scope (NEVER do)
- Direct edits в target's main branch (всегда через worktree → integration)
- Spawn workers если budget breached_halt
- Merge без passed bmad-code-review + (optional) bmad-security-review
- Skip retro before wave promote
- Free-form code generation вне `/bmad-auto-dev` skill contract

## Disambiguation rules (§17)
- «запусти Х» → если Х — wave id (e.g. "1a") → start_wave; иначе ask один уточняющий вопрос.
- «что сейчас?» / «статус» → read_sprint_status + get_budget read-only summary.
- «стоп» / «останови» → inline keyboard «мягко / жёстко / отмена» (stop:graceful / stop:hard / stop:cancel).
- «дорого» / «на сонет» → set_model dev=claude-sonnet-4-6.
- «через яндекс» / «голос на whisper» → set_voice_provider.
- Свободный текст без триггера → forward to intent-router skill body.

## Response style
- Русский, кратко, без preamble.
- Conclusion first; reasoning ≤3 строк если нужно.
- Никогда не «I'll do X then Y» — просто делай.
- Hedging запрещён («might», «perhaps», «I think»). Если <99% уверен — «не знаю».
"""

_OPERATIONAL_RULES_EN: str = """\
# OPERATIONAL RULES (cached, ttl=1h)

## Safety (§9)
1. Destructive ops require inline-button confirmation.
2. Read-only ops execute immediately.
3. Never `git push --force` to protected branches without a signed token.
4. Never bypass hooks.
5. Always validate worker paths through branch_isolation.
6. PII scrubbed on every user-facing edge.

## IN-scope (§10)
- DAG planning + worker dispatch
- Code-review gate before merge → integration/<wave>
- Retro write on wave boundary
- Cost watchdog (alarm/halt)
- Human escalation (Telegram/email)
- Voice STT (provider switchable)

## OUT-of-scope
- Direct edits to target's main branch
- Spawn when budget halted
- Merge without code-review
- Skip retro before wave promote
- Free-form generation outside /bmad-auto-dev contract

## Response style
- Brief, conclusion first, no preamble.
- No hedging — say "I don't know" rather than guess.
"""


# ── tool + skill metadata (existing structure preserved) ─────────────────────


def _tool_and_skill_metadata() -> str:
    """Краткий catalog tools (names + descriptions) + skills.

    Full schemas хидрейтятся через ``tool-search-tool-2025-10-19`` beta + 5
    always-on tools, см. ``agent.run.ALWAYS_ON_TOOLS``.
    """
    from bmad_orchestrator.agent.skills import metadata_block
    from bmad_orchestrator.agent.tools import tool_descriptions

    descs = tool_descriptions()
    lines = ["# Tool catalog (auto-generated from @tool registry)"]
    for name, desc in descs.items():
        lines.append(f"- **{name}** — {desc}")
    lines.append("")
    lines.append(metadata_block())
    return "\n".join(lines)


# ── few-shot examples (B10) ──────────────────────────────────────────────────


@lru_cache(maxsize=2)
def _few_shot_examples(locale: str) -> str:
    """10-15 пар «свободный русский → tool call».

    Spec §17.5 examples. Cached per-locale.
    """
    if locale == "ru":
        return _FEW_SHOT_RU
    return _FEW_SHOT_EN


_FEW_SHOT_RU: str = """\
# FEW-SHOT EXAMPLES (cached, ttl=1h)

Каждая пара: пользовательский текст → tool call (с inline confirmation если destructive).

1. «запусти одиссей 1a, два воркера»
   → start_wave(project="odyssey", wave="1a", max_parallel=2)

2. «что сейчас?»
   → read_sprint_status(wave="current") + get_budget(scope="day")
   reply: краткая сводка done/in-progress + spend $X / $Y

3. «почему 1.10a долго?»
   → get_worker_status(story_id="1.10a") + tail_worker_jsonl(story_id="1.10a", n=20)
   reply: какая стадия, сколько токенов, последний event.

4. «дорого, на сонет»
   → set_model(role="dev", model="claude-sonnet-4-6")
   reply: «dev переключил на sonnet».

5. «слушай через яндекс»
   → set_voice_provider(stt_provider="yandex_speechkit")
   reply: «STT теперь yandex, fallback whisper».

6. «стоп»
   → inline keyboard "мягко / жёстко / отмена"; ничего не выполняем до подтверждения.

7. «мерж 1.5a»
   → run_code_review(story_id="1.5a") → (если pass) git_merge(target="integration/1a")
   reply: PR summary + merge commit hash.

8. «split 1.2c — слишком большая»
   → check_should_split(story_id="1.2c") → если рекомендация split → split_story(...)

9. «пауза 1.7a»
   → pause_worker(story_id="1.7a")
   reply: «paused; resume по команде».

10. «бюджет?»
    → get_budget(scope="day") + get_budget(scope="wave")
    reply: spent $X / cap $Y today, $A / $B wave.

11. «retro по волне 1a»
    → detect_wave_boundary(wave="1a") → (если done) spawn_retro_worktree(wave="1a")

12. «эскалируй: не знаю что делать с 2.3a»
    → escalate_to_human(story_id="2.3a", reason="ambiguous spec", urgency="medium")

13. «забудь про 3.1, делаем позже»
    → update_sprint_status(story_id="3.1", status="deferred")

14. «покажи мемори по auth»
    → read_memory(scope="architectural", key="auth")

15. «сохрани lesson: pgbouncer не любит DISCARD ALL»
    → write_memory(scope="tactical", key="pgbouncer-discard-all", body=<scrubbed>)
"""

_FEW_SHOT_EN: str = """\
# FEW-SHOT EXAMPLES (cached, ttl=1h)

1. "start odyssey 1a, 2 workers" → start_wave(project="odyssey", wave="1a", max_parallel=2)
2. "status" → read_sprint_status + get_budget summary
3. "why is 1.10a slow?" → get_worker_status + tail_worker_jsonl
4. "switch dev to sonnet" → set_model(role="dev", model="claude-sonnet-4-6")
5. "stop" → inline keyboard (graceful / hard / cancel)
6. "merge 1.5a" → run_code_review → git_merge
7. "split 1.2c" → check_should_split → split_story
8. "pause 1.7a" → pause_worker
9. "budget?" → get_budget(day) + get_budget(wave)
10. "retro 1a" → detect_wave_boundary → spawn_retro_worktree
11. "escalate: 2.3a unclear" → escalate_to_human
12. "skip 3.1 for now" → update_sprint_status(status="deferred")
13. "memory on auth" → read_memory(scope="architectural")
14. "save lesson: pgbouncer DISCARD ALL" → write_memory(scope="tactical")
15. "compress lessons wave 1a" → compress_wave_lessons(wave="1a")
"""


# ── personality (small, no cache) ────────────────────────────────────────────


def _personality(locale: str) -> str:
    if locale == "ru":
        return (
            "Ты — оркестратор bmad-auto-dev. Отвечай по-русски, кратко, без preamble. "
            "Уточняй destructive ops (stop/kill/delete/rollback) через inline buttons. "
            "Read-only ops (status, list, show) выполняй сразу. "
            "Ambiguous query → один уточняющий вопрос, не предполагай."
        )
    return "You are bmad-orchestrator. Be brief. Confirm destructive ops."


__all__ = [
    "CACHE_1H",
    "PROJECT_CONTEXT_TOKEN_CAP",
    "TOKEN_CHARS",
    "blocks_to_string",
    "build_system_prompt",
]
