"""NEW-22 — `bmad_format_unknown_status` must not log as a bare opaque key.

methodology §5 framed this as "4 calls from a top-level scan, separate from
``_canonical_status``". The diagnosis disproved that: there is exactly ONE
callsite (``bmad_format._canonical_status``) and it was already structured via
``extra=``. The real bug — ``bmad_format`` uses the stdlib ``logging`` module,
whose default formatter does NOT render ``extra=`` record attributes, so the
line printed a bare ``bmad_format_unknown_status`` to stdout (4× per run = 4
stories with an unrecognised Status, same callsite).

Fix: interpolate the fields into the message string itself.

2 unit. See spec/spec_pilot_findings_closure_v7.md §3.
"""

from __future__ import annotations

import logging

import pytest

from bmad_orchestrator.runtime.bmad_format import parse_sprint_status_bmad

_LOGGER = "bmad_orchestrator.runtime.bmad_format"


def test_unknown_status_message_carries_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The rendered warning message itself carries story_id + raw_status +
    token + layout — not just the (unrendered) `extra=` record attributes."""
    data = {"development_status": {"epic-7": "in-progress", "7-3-x": "frobnicated"}}
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        parse_sprint_status_bmad(data)

    unknown = [r for r in caplog.records if "unknown_status" in r.getMessage()]
    assert len(unknown) == 1
    msg = unknown[0].getMessage()
    # The whole line is greppable key=value — not a bare opaque key.
    assert msg.startswith("bmad_format_unknown_status ")
    assert "story_id=7-3-x" in msg
    assert "raw_status=frobnicated" in msg
    assert "token=frobnicated" in msg
    assert "layout=bmad" in msg


def test_each_unknown_status_logs_its_own_story_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Multiple unrecognised statuses → one warning each, every line naming its
    own story — the pre-NEW-22 symptom was 4 indistinguishable opaque lines."""
    data = {
        "development_status": {
            "epic-7": "in-progress",
            "7-1-a": "weird-one",
            "7-2-b": "weird-two",
            "7-3-c": "weird-three",
        }
    }
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        parse_sprint_status_bmad(data)

    msgs = [r.getMessage() for r in caplog.records if "unknown_status" in r.getMessage()]
    assert len(msgs) == 3
    joined = "\n".join(msgs)
    for sid, raw in (
        ("7-1-a", "weird-one"),
        ("7-2-b", "weird-two"),
        ("7-3-c", "weird-three"),
    ):
        assert f"story_id={sid}" in joined
        assert f"raw_status={raw}" in joined
