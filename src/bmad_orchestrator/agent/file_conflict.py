"""File-conflict pre-check для batch spawn (Initiative #1 Task 1.2).

При параллельном спавне N stories из одной wave стоит проверить, что в одном
batch'е нет двух stories, которые пишут в один файл — это гарантированный merge
conflict в worker pool. Module decoupled от ``agent/tools/dag.py`` (тот — tool
для LLM): этот используется runtime'ом ``_run_real_pilot_body`` *перед*
спавном batch'а.

Сравнение с ``runtime/dag_planner.py::ready_stories``:

* ``ready_stories`` фильтрует кандидатов против ``in_flight_touches`` —
  файлов, уже занятых **активными** workers. В текущем pipeline'е реально
  пустое, т.к. ``planner.reserve()`` не вызывается из ``_run_real_pilot_body``.
* ``file_conflict.split_batch`` фильтрует **внутри** batch'а — даже если
  ``in_flight_touches`` пуст, две одновременно-готовые stories с
  пересекающимися ``touches_files`` не должны попасть в один parallel spawn.

KISS contract:
* первое появление файла «владеет» им,
* любая последующая story с тем же файлом → ``deferred``,
* ``max_parallel`` capает только ``parallel`` (deferred игнорируют cap — они
  просто отложатся до следующего round'а, когда конфликтующий peer завершён
  и его файлы освободились).
"""

from __future__ import annotations

from typing import Any

Story = dict[str, Any]
Conflict = tuple[str, str, str]


def _touches(s: Story) -> set[str]:
    """Union ``touches_files`` + ``touches_shared`` (оба = merge-conflict risk)."""
    files = set(s.get("touches_files") or [])
    shared = set(s.get("touches_shared") or [])
    return {str(f) for f in (files | shared)}


def find_conflicts(stories: list[Story]) -> list[Conflict]:
    """Вернуть список ``(first_story_id, second_story_id, file)`` для batch'а.

    Linear O(n·|files|). Обходит stories в порядке поступления, запоминая для
    каждого файла первого «владельца». Каждая последующая story, которая
    касается уже-claimed файла, порождает один tuple на конфликтующий файл.
    Stable order: tuples появляются в порядке (second_story_index,
    sorted file name).
    """
    seen: dict[str, str] = {}
    conflicts: list[Conflict] = []
    for s in stories:
        sid_raw = s.get("id")
        sid = str(sid_raw) if sid_raw is not None else ""
        if not sid:
            # Review finding H-6 — silent skip used to hide upstream parser
            # bugs (story dict without ``id``). A missing id means the
            # DagPlanner emitted a malformed entry and the operator must
            # see it, not have conflict-detection silently treat the story
            # as harmless.
            raise ValueError(f"story missing id: {s!r}")
        for f in sorted(_touches(s)):
            owner = seen.get(f)
            if owner is None:
                seen[f] = sid
            elif owner != sid:
                conflicts.append((owner, sid, f))
    return conflicts


def split_batch(
    stories: list[Story], max_parallel: int
) -> tuple[list[Story], list[Story]]:
    """Разбить batch на ``(parallel, deferred)``.

    First-come-first-served. Первая story для каждого файла идёт в ``parallel``,
    последующие конфликтующие — в ``deferred`` (вернутся в следующем round'е,
    когда конфликтующий peer завершён → освободил файлы). ``max_parallel``
    лимитирует только ``parallel`` — лишние неконфликтующие тоже едут в
    ``deferred`` (round-robin естественно подберёт их).

    Edge cases:
    * ``max_parallel <= 0`` → ``parallel`` пуст, всё в ``deferred``.
    * Пустой ``stories`` → ``([], [])``.
    * Story без ``id`` или без ``touches_files`` → попадает в ``parallel`` пока
      ``len(parallel) < max_parallel`` (отсутствие touches = no-op для claim).
    """
    parallel: list[Story] = []
    deferred: list[Story] = []
    claimed: set[str] = set()
    if max_parallel <= 0:
        return parallel, list(stories)
    for s in stories:
        files = _touches(s)
        if files & claimed:
            deferred.append(s)
            continue
        if len(parallel) >= max_parallel:
            deferred.append(s)
            continue
        parallel.append(s)
        claimed |= files
    return parallel, deferred


__all__ = ["Conflict", "Story", "find_conflicts", "split_batch"]
