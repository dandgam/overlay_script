"""FS1 acceptance tests — secret hygiene, env allow-list, PII regex gaps.

Covers: B6 (audit secret scrubbing + 0o600), B7 (telegram audit drops `original`
by default + opt-in HMAC log), B8 (worker subprocess env allow-list), H5
(gh_client uses urllib not curl argv), H13 (PHONE_RU `:`/`8`-prefix, PATH_LIKE
negative-lookahead for email).
"""

from __future__ import annotations

import json
import os
import stat
import warnings
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bmad_orchestrator.agent.safety import audit as safety_audit
from bmad_orchestrator.agent.safety import audit_log_path, record_audit
from bmad_orchestrator.bot.audit import (
    record_telegram_event,
    telegram_audit_path,
    telegram_original_path,
)
from bmad_orchestrator.bot.pii_detector import (
    EMAIL,
    PATH_LIKE,
    PHONE_RU,
    scrub_input,
    scrub_output,
)
from bmad_orchestrator.imports.from_bad import gh_client
from bmad_orchestrator.runtime.worker_spawn import (
    ALLOWED_WORKER_ENV,
    WORKER_ENV_INJECTED,
    _build_worker_env,
)

_scrub_secrets = safety_audit._scrub_secrets
_scrub_value = safety_audit._scrub_value


@pytest.fixture(autouse=True)
def _isolate_audit_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BMAD_AUDIT_LOG", str(tmp_path / "audit.events.jsonl"))
    monkeypatch.setenv("BMAD_TELEGRAM_AUDIT_LOG", str(tmp_path / "telegram.jsonl"))
    monkeypatch.setenv("ORCHESTRATOR_TARGET_PROJECT", str(tmp_path / "target"))
    monkeypatch.delenv("BMAD_AUDIT_KEEP_ORIGINAL", raising=False)
    monkeypatch.delenv("BMAD_AUDIT_HMAC_KEY", raising=False)


# ─── B6 — audit secret scrubbing ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "secret",
    [
        "sk-ant-" + "A" * 60,
        "1234567890:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef-_x",
        "ghp_" + "a" * 36,
        "AKIA" + "A" * 16,
    ],
    ids=["anthropic", "telegram", "github_pat", "aws"],
)
def test_b6_secret_pattern_scrubbed(secret: str) -> None:
    out = _scrub_secrets(f"prefix {secret} suffix")
    assert secret not in out
    assert "[REDACTED:SECRET]" in out


def test_b6_bearer_scrubbed() -> None:
    out = _scrub_secrets("X-Header: Bearer abcdefghijklmnopqrst.uvwxyz123456")
    assert "abcdefghijklmnopqrst" not in out
    assert "[REDACTED:SECRET]" in out


def test_b6_url_creds_scrubbed() -> None:
    out = _scrub_secrets("clone https://alice:s3cret@github.com/x/y.git")
    assert "alice:s3cret" not in out
    assert "[REDACTED:SECRET]" in out


def test_b6_audit_file_created_with_0600_perms() -> None:
    record_audit("test_event", note="hello")
    path = audit_log_path()
    perms = stat.S_IMODE(path.stat().st_mode)
    assert perms == 0o600, f"expected 0o600, got {oct(perms)}"


def test_b6_record_audit_scrubs_payload_string() -> None:
    record_audit("hook", tool_input="export ANTHROPIC_API_KEY=sk-ant-" + "A" * 60)
    path = audit_log_path()
    body = path.read_text()
    assert "sk-ant-" not in body
    assert "[REDACTED:SECRET]" in body


def test_b6_record_audit_scrubs_nested_dict() -> None:
    record_audit(
        "hook",
        tool_input={
            "command": "curl -H 'Authorization: Bearer " + "X" * 40 + "' https://api",
            "envs": {"GH_TOKEN": "ghp_" + "z" * 36},
            "tags": ["normal", "AKIA" + "B" * 16],
        },
    )
    body = audit_log_path().read_text()
    assert "ghp_" not in body
    assert "AKIA" not in body
    assert body.count("[REDACTED:SECRET]") >= 3


# ─── B7 — telegram audit drops `original`, opt-in HMAC log ────────────────────


def test_b7_default_drops_original_field() -> None:
    record_telegram_event(
        direction="in",
        chat_id=123,
        message_type="text",
        original="мой телефон +79161234567",
        redacted="мой телефон [PHONE]",
        pii_categories=["PHONE"],
    )
    line = telegram_audit_path().read_text().splitlines()[0]
    entry = json.loads(line)
    assert "original" not in entry
    assert entry["redacted"] == "мой телефон [PHONE]"
    assert entry["pii"] == ["PHONE"]


def test_b7_telegram_log_perm_0600() -> None:
    record_telegram_event(direction="in", chat_id=1, message_type="text", redacted="hi")
    perms = stat.S_IMODE(telegram_audit_path().stat().st_mode)
    assert perms == 0o600


def test_b7_opt_in_writes_original_with_hmac(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BMAD_AUDIT_KEEP_ORIGINAL", "1")
    monkeypatch.setenv("BMAD_AUDIT_HMAC_KEY", "deadbeef" * 4)
    record_telegram_event(
        direction="in",
        chat_id=42,
        message_type="text",
        original="секрет sk-ant-XYZ",
        redacted="секрет [REDACTED]",
    )
    primary = telegram_audit_path()
    assert "original" not in primary.read_text()
    orig_path = telegram_original_path()
    assert orig_path.exists()
    orig_perms = stat.S_IMODE(orig_path.stat().st_mode)
    assert orig_perms == 0o600
    orig_entry = json.loads(orig_path.read_text().splitlines()[0])
    assert orig_entry["original"] == "секрет sk-ant-XYZ"
    assert orig_entry["redacted"] == "секрет [REDACTED]"
    assert "hmac_sha256" in orig_entry
    assert len(orig_entry["hmac_sha256"]) == 64  # SHA256 hex


def test_b7_opt_in_without_hmac_key_warns_and_skips_original(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BMAD_AUDIT_KEEP_ORIGINAL", "1")
    # No BMAD_AUDIT_HMAC_KEY set
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        record_telegram_event(
            direction="in", chat_id=1, message_type="text",
            original="raw", redacted="r",
        )
    msgs = [str(w.message) for w in caught]
    assert any("HMAC" in m for m in msgs), f"expected HMAC warning, got {msgs}"
    assert not telegram_original_path().exists(), "original file must NOT be written"


# ─── B8 — worker subprocess env allow-list ────────────────────────────────────


def test_b8_worker_env_contains_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("HOME", "/home/test")
    env = _build_worker_env(extra=None)
    assert env["PATH"] == "/usr/bin:/bin"
    assert env["HOME"] == "/home/test"


def test_b8_worker_env_strips_api_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-secret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1234:secret")
    monkeypatch.setenv("GH_TOKEN", "ghp_secret")
    monkeypatch.setenv("YANDEX_API_KEY", "yandex_secret")
    monkeypatch.setenv("GOOGLE_API_KEY", "google_secret")
    env = _build_worker_env(extra=None)
    leaked = [
        k for k in env
        if k not in ALLOWED_WORKER_ENV and k not in WORKER_ENV_INJECTED
    ]
    assert leaked == [], f"secrets leaked into worker env: {leaked}"
    for forbidden in (
        "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "TELEGRAM_BOT_TOKEN",
        "GH_TOKEN", "YANDEX_API_KEY", "GOOGLE_API_KEY",
    ):
        assert forbidden not in env


def test_b8_worker_env_passes_caller_extras() -> None:
    env = _build_worker_env(extra={"ORCHESTRATOR_WORKER_STORY_ID": "S5"})
    assert env["ORCHESTRATOR_WORKER_STORY_ID"] == "S5"


def test_b8_allowlist_does_not_include_secret_carriers() -> None:
    for forbidden in ("ANTHROPIC_API_KEY", "GITHUB_TOKEN", "OPENAI_API_KEY"):
        assert forbidden not in ALLOWED_WORKER_ENV


# ─── H5 — gh_client uses urllib (no token in argv) ────────────────────────────


def test_h5_gh_client_token_not_in_subprocess_argv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When falling through to HTTP path, token must travel in urllib headers,
    not as a `curl -H "Authorization: Bearer ..."` argv element."""
    monkeypatch.setenv("GITHUB_PERSONAL_ACCESS_TOKEN", "ghp_test_token_value")
    monkeypatch.setattr(gh_client.shutil, "which", lambda _name: None)

    captured_subprocess_calls: list[tuple] = []

    def _fake_run(*args, **kwargs):  # type: ignore[no-untyped-def]
        captured_subprocess_calls.append((args, kwargs))
        return MagicMock(returncode=0, stdout="")

    monkeypatch.setattr(gh_client.subprocess, "run", _fake_run)

    fake_resp = MagicMock()
    fake_resp.read.return_value = b'{"ok":true}'
    fake_resp.__enter__ = lambda self: self
    fake_resp.__exit__ = lambda *a: None

    captured_request: list = []

    def _fake_urlopen(req, timeout=None):  # type: ignore[no-untyped-def]
        captured_request.append(req)
        return fake_resp

    with patch("urllib.request.urlopen", _fake_urlopen):
        rc, body = gh_client.gh_or_curl(
            ["api", "/repos/x/y"], "https://api.github.com/repos/x/y",
        )

    assert rc == 0
    assert body == '{"ok":true}'
    assert captured_subprocess_calls == [], (
        f"no subprocess (e.g. curl) must run on urllib path, got {captured_subprocess_calls}"
    )
    assert len(captured_request) == 1
    req = captured_request[0]
    auth = req.headers.get("Authorization") or req.get_header("Authorization")
    assert auth == "Bearer ghp_test_token_value"


# ─── H13 — PHONE_RU lookbehind extension + 8-prefix; PATH_LIKE no email-eat ──


def test_h13_phone_ru_colon_prefix() -> None:
    text = "tel:+79161234567 — позвони"
    matches = list(PHONE_RU.finditer(text))
    assert len(matches) == 1
    assert matches[0].group().startswith("+7")


def test_h13_phone_ru_eight_prefix() -> None:
    text = "номер 89161234567 контакт"
    matches = list(PHONE_RU.finditer(text))
    assert len(matches) == 1
    assert matches[0].group() == "89161234567"


def test_h13_phone_ru_comma_prefix() -> None:
    text = "Иван,+79161234567,Петров"
    matches = list(PHONE_RU.finditer(text))
    assert len(matches) == 1


def test_h13_phone_ru_slash_prefix() -> None:
    text = "client/+79161234567"
    matches = list(PHONE_RU.finditer(text))
    assert len(matches) == 1


def test_h13_phone_scrub_input_returns_phone_category() -> None:
    _, cats = scrub_input("свяжись tel:+79161234567 срочно")
    assert "PHONE" in cats


def test_h13_path_like_does_not_eat_email_span() -> None:
    text = " /var/lib/foo.bar@example.com"
    matches = list(PATH_LIKE.finditer(text))
    assert matches == [], f"PATH_LIKE swallowed email-like path, got {[m.group() for m in matches]}"


def test_h13_email_inside_path_still_detected_by_scrub_output() -> None:
    text = "see /var/lib/foo.bar@example.com for log"
    redacted, cats = scrub_output(text)
    assert "EMAIL" in cats
    assert "[EMAIL]" in redacted
    assert "foo.bar@example.com" not in redacted


def test_h13_pure_path_still_masked() -> None:
    text = " /etc/passwd /var/log/syslog"
    matches = [m.group().strip() for m in PATH_LIKE.finditer(text)]
    assert matches, "pure paths must still match PATH_LIKE"


# ─── M10 — umask is process-wide; sanity check umask call survives import ────


def test_m10_umask_call_does_not_raise() -> None:
    saved = os.umask(0o077)
    try:
        assert isinstance(saved, int)
    finally:
        os.umask(saved)


# ─── EMAIL detection still works (sanity, non-regression for H13) ────────────


def test_email_detection_baseline() -> None:
    matches = list(EMAIL.finditer("contact: alice@example.com please"))
    assert len(matches) == 1
    assert matches[0].group() == "alice@example.com"
