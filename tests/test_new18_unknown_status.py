"""NEW-18 — unrecognised story Status → structured warning, not a raw print.

pilot_findings_closure_v6 spec §6: the status parser logged an opaque
``bmad_format.unknown_status`` line 4× per pilot run — an operator could not
see *which* Status values failed to parse or in *which* sprint-status layout.
The warning now carries ``story_id`` + ``raw_status`` + ``layout`` as
structured fields, and the parser recognises the extended BMad status set
(capitalised forms, ``drafted``, ``approved``) case-insensitively.

Coverage: 2 unit — structured warning fields; extended-status recognition.
"""

from __future__ import annotations

import logging

import pytest

from bmad_orchestrator.runtime.bmad_format import (
    KNOWN_STATUSES,
    parse_sprint_status_bmad,
)

_LOGGER = "bmad_orchestrator.runtime.bmad_format"


def test_unknown_status_emits_structured_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A genuinely unknown Status → WARNING with story_id + raw_status + layout."""
    data = {
        "development_status": {
            "epic-7": "in-progress",
            "7-3-weird": "frobnicating",
        }
    }
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        result = parse_sprint_status_bmad(data)

    # falls back to backlog, as before
    assert result["epics"]["7"]["stories"]["7.3"] == "backlog"

    unknown = [
        r for r in caplog.records if "unknown_status" in r.getMessage()
    ]
    assert len(unknown) == 1
    rec = unknown[0]
    # NEW-22: fields are interpolated into the rendered message itself (the
    # stdlib default formatter does not render `extra=` attributes), so the
    # operator no longer sees a bare opaque `bmad_format_unknown_status` key.
    msg = rec.getMessage()
    assert msg.startswith("bmad_format_unknown_status ")
    assert "story_id=7-3-weird" in msg
    assert "raw_status=frobnicating" in msg
    assert "layout=bmad" in msg
    # `extra=` attributes are kept for structured-log aggregators.
    assert getattr(rec, "story_id") == "7-3-weird"
    assert getattr(rec, "raw_status") == "frobnicating"
    assert getattr(rec, "layout") == "bmad"


def test_parser_recognises_extended_status_set() -> None:
    """Capitalised forms + ``drafted`` / ``approved`` resolve case-insensitively
    to their lowercase canonical token instead of falling back to backlog."""
    assert {"drafted", "approved"} <= KNOWN_STATUSES

    data = {
        "development_status": {
            "epic-8": "Done",
            "8-1-cap": "Done",
            "8-2-draft": "Drafted",
            "8-3-appr": "approved",
            "8-4-rfd": "Ready-For-Dev",
        }
    }
    result = parse_sprint_status_bmad(data)
    stories = result["epics"]["8"]["stories"]
    assert stories["8.1"] == "done"
    assert stories["8.2"] == "drafted"
    assert stories["8.3"] == "approved"
    assert stories["8.4"] == "ready-for-dev"
    assert result["epics"]["8"]["status"] == "done"
