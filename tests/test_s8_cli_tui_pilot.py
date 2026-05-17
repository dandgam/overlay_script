"""S8 — CLI + TUI dashboard + model selection + E2E mock pilot.

Spec §22 acceptance:
  - cli/main.py — typer commands per §14.1
  - cli/tui.py — rich.Live 5 panels per §14.2
  - russian localization per §14.3 (locale/ru.yaml)
  - 3 launch modes per §14.4
  - model selection per §16 (CLI flags + orchestrator-models.yaml)
  - E2E mock pilot: `bmad-orchestrator run --project mock --wave 1a --mock` runs to completion
  - regression: все S1–S7 тесты PASS (см. test_s1..s7).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bmad_orchestrator.cli import app
from bmad_orchestrator.cli import models_yaml as models_yaml_mod
from bmad_orchestrator.cli.i18n import t
from bmad_orchestrator.cli.tui import DashboardSnapshot, build_layout, render_once
from bmad_orchestrator.config import ModelConfig

# ── shared fixture: isolated mock-odyssey project ────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_target_project(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = Path(__file__).parent / "fixtures" / "mock-odyssey"
    dst = tmp_path / "mock-odyssey"
    shutil.copytree(src, dst)
    monkeypatch.setenv("ORCHESTRATOR_TARGET_PROJECT", str(dst))
    monkeypatch.setenv("ORCHESTRATOR_STATE_DB", str(tmp_path / "state.db"))
    monkeypatch.setenv("ORCHESTRATOR_ORCHESTRATOR_HOME", str(tmp_path / "orch"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")  # force mock path
    monkeypatch.delenv("BMAD_AUDIT_LOG", raising=False)


# ── i18n ─────────────────────────────────────────────────────────────────────


def test_i18n_ru_localization() -> None:
    # Ключи из locale/ru.yaml
    msg = t("worker.completed", pid=42, story="1-1")
    assert "Worker 42" in msg
    assert "1-1" in msg
    # Кириллица — глобальная invariant локализации (§14.3)
    assert "завершил" in msg


def test_i18n_missing_key_returns_key() -> None:
    assert t("nonexistent.key") == "nonexistent.key"


def test_i18n_agent_idle_present() -> None:
    msg = t("agent.idle")
    assert msg and msg != "agent.idle"


# ── models_yaml ──────────────────────────────────────────────────────────────


def test_models_yaml_default_when_missing(tmp_path: Path) -> None:
    cfg = models_yaml_mod.load_models(tmp_path / "missing.yaml")
    assert cfg.planner == "claude-opus-4-7"
    assert cfg.dev == "claude-sonnet-4-6"


def test_models_yaml_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "orchestrator-models.yaml"
    cfg = ModelConfig(planner="claude-opus-4-7", dev="claude-sonnet-4-6")
    cfg = models_yaml_mod.apply_overrides(cfg, planner="claude-haiku-4-5")
    models_yaml_mod.save_models(path, cfg)
    assert path.is_file()
    loaded = models_yaml_mod.load_models(path)
    assert loaded.planner == "claude-haiku-4-5"
    assert loaded.dev == "claude-sonnet-4-6"


def test_models_apply_all_overrides_every_role() -> None:
    cfg = ModelConfig()
    cfg2 = models_yaml_mod.apply_all(cfg, "claude-opus-4-7")
    for role in models_yaml_mod.ROLE_FIELDS:
        assert getattr(cfg2, role) == "claude-opus-4-7"


def test_models_apply_overrides_rejects_unknown_role() -> None:
    with pytest.raises(ValueError, match="unknown role"):
        models_yaml_mod.apply_overrides(ModelConfig(), bogus="x")


def test_models_apply_overrides_skips_none() -> None:
    cfg = ModelConfig()
    cfg2 = models_yaml_mod.apply_overrides(cfg, planner=None, dev="claude-sonnet-4-6")
    assert cfg2.planner == cfg.planner
    assert cfg2.dev == "claude-sonnet-4-6"


# ── TUI snapshot ─────────────────────────────────────────────────────────────


def test_tui_snapshot_renders_all_five_panels() -> None:
    snap = DashboardSnapshot(
        status="running",
        wave="1a",
        project="mock-odyssey",
        progress_done=2,
        progress_total=5,
        budget_spent_usd=12.34,
        budget_cap_usd=50.0,
        budget_level="ok",
        workers=[
            {"worktree": "wt-1", "story_id": "1-1", "stage": "dev",
             "state": "active", "tokens_used": 1500, "cost_usd": 0.45},
        ],
        ready_next=["1-2", "2-1"],
        blocked=["1-3"],
        done_recent=["1-1"],
        agent_thinking="думаю над DAG",
        events_tail=["[ts] worker_spawned 1-1"],
    )
    out = render_once(snap, width=140)
    # Все 5 секций present (header + workers + DAG + agent + events + hotkeys footer)
    assert "status" in out
    assert "workers" in out
    assert "DAG" in out
    assert "events" in out
    # Hotkeys footer per §14.2
    assert "[q]uit" in out
    # Snapshot data leaked through
    assert "1-1" in out
    assert "mock-odyssey/1a" in out


def test_tui_layout_smoke_no_data() -> None:
    layout = build_layout(DashboardSnapshot())
    # Layout exposes a tree of regions; smoke is that rendering doesn't raise.
    out = render_once(DashboardSnapshot(), width=120)
    assert "Virgil" in out
    # When workers list пуст — placeholder row '—' печатается
    assert "—" in out
    assert layout is not None


# ── CLI smoke (typer.testing.CliRunner) ──────────────────────────────────────


runner = CliRunner()


def test_cli_help_lists_all_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("run", "status", "pause", "resume", "stop", "budget",
                "logs", "dag", "retro", "validate-policy", "memory",
                "model", "bot"):
        assert cmd in result.stdout


def test_cli_budget_command_shows_thresholds() -> None:
    result = runner.invoke(app, ["budget"])
    assert result.exit_code == 0
    assert "story" in result.stdout
    assert "batch" in result.stdout


def test_cli_status_snapshot_runs() -> None:
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    # Header rendered
    assert "Virgil" in result.stdout


def test_cli_model_show_lists_all_roles() -> None:
    result = runner.invoke(app, ["model", "show"])
    assert result.exit_code == 0
    for role in ("planner", "reviewer", "dev", "routine", "mechanical", "fallback"):
        assert role in result.stdout


def test_cli_model_set_saves_yaml(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["model", "set", "planner", "claude-opus-4-7", "--save"]
    )
    assert result.exit_code == 0
    # YAML persisted under target_project
    from bmad_orchestrator.config import load_settings

    path = models_yaml_mod.config_path(load_settings().target_project)
    assert path.is_file()
    cfg = models_yaml_mod.load_models(path)
    assert cfg.planner == "claude-opus-4-7"


def test_cli_dag_renders_ready_stories() -> None:
    result = runner.invoke(app, ["dag", "--wave", "1a"])
    assert result.exit_code == 0
    # mock-odyssey: 1-1-tenant-signup ready-for-dev, no deps
    assert "1-1-tenant-signup" in result.stdout


def test_cli_validate_policy_with_real_file(tmp_path: Path) -> None:
    policy = tmp_path / "p.yaml"
    policy.write_text("rules:\n  - id: x\n    pattern: dev\n", encoding="utf-8")
    result = runner.invoke(app, ["validate-policy", "--path", str(policy)])
    assert result.exit_code == 0
    assert "ok" in result.stdout


def test_cli_validate_policy_bad_yaml(tmp_path: Path) -> None:
    policy = tmp_path / "p.yaml"
    policy.write_text("rules: [: bad\n", encoding="utf-8")
    result = runner.invoke(app, ["validate-policy", "--path", str(policy)])
    assert result.exit_code != 0


def test_cli_validate_policy_missing_file(tmp_path: Path) -> None:
    result = runner.invoke(app, ["validate-policy", "--path", str(tmp_path / "nope.yaml")])
    assert result.exit_code != 0


def test_cli_logs_missing_worker(tmp_path: Path) -> None:
    result = runner.invoke(app, ["logs", "--worker", "ghost-pid"])
    assert result.exit_code != 0


def test_cli_memory_missing(tmp_path: Path) -> None:
    result = runner.invoke(app, ["memory", "--wave", "1a"])
    assert result.exit_code != 0


# ── E2E mock pilot ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mock_pilot_runs_to_completion() -> None:
    """Spec §22 acceptance: full cascade until WAVE_BOUNDARY_REACHED."""
    from bmad_orchestrator.agent.run import run_orchestrator
    from bmad_orchestrator.runtime.event_loop import EventLoop, EventType

    bus = EventLoop()
    seen: list[EventType] = []
    spawned_ids: set[str] = set()

    async def _capture(event: object) -> None:
        from bmad_orchestrator.runtime.event_loop import Event

        if isinstance(event, Event):
            seen.append(event.type)
            if event.type == EventType.WORKER_COMPLETED:
                sid = event.payload.get("story_id")
                if isinstance(sid, str):
                    spawned_ids.add(sid)

    bus.on(_capture)

    result_bus = await run_orchestrator(
        project="mock-odyssey",
        wave="1a",
        max_parallel=2,
        mock=True,
        event_loop=bus,
    )

    # Drain remaining events so subscriber sees everything.
    for _ in range(20):
        ev = await bus.dispatch_one(timeout=0.05)
        if ev is None:
            break

    assert result_bus is bus
    assert EventType.WORKER_COMPLETED in seen, f"events: {seen}"
    assert EventType.WAVE_BOUNDARY_REACHED in seen, f"events: {seen}"
    assert len(spawned_ids) >= 3, f"expected ≥3 stories spawned, got {spawned_ids}"


def test_build_agent_options_matches_sdk_contract() -> None:
    """Spec §22 + FS4 B1: build_agent_options returns SDK-compatible kwargs.

    Field renames since S8:
      - `system` (list[block]) → `system_prompt` (str)
      - `tools` (list[@tool|tool_block]) → `mcp_servers` (dict[name, McpSdkServer])
      - `beta_headers` → `betas`
      - `hooks` callables wrapped in `HookMatcher`
    Memory tool block is server-managed and no longer flows through SDK options
    (see agent/run.py docstring for the rationale).
    """
    from claude_agent_sdk import HookMatcher

    from bmad_orchestrator.agent.run import MCP_SERVER_NAME, build_agent_options

    opts = build_agent_options(
        project_root=Path("/tmp/fake-project"),
        wave="1a",
        models=ModelConfig(),
    )
    assert "system_prompt" in opts
    assert isinstance(opts["system_prompt"], str)
    assert opts["model"] == "claude-sonnet-4-6"  # dev default
    assert MCP_SERVER_NAME in opts["mcp_servers"]
    assert "betas" in opts
    assert "beta_headers" not in opts
    pre = opts["hooks"]["PreToolUse"]
    post = opts["hooks"]["PostToolUse"]
    assert all(isinstance(h, HookMatcher) for h in pre)
    assert all(isinstance(h, HookMatcher) for h in post)


# ── forward_to_agent EventLoop bridge (S6 deferred) ──────────────────────────


@pytest.mark.asyncio
async def test_forward_to_agent_stub_mode() -> None:
    """Без attached bus — синхронный echo (preserves S6 contract)."""
    from bmad_orchestrator.bot import handlers as bot_handlers

    bot_handlers.attach_event_loop(None)
    reply = await bot_handlers.forward_to_agent(
        "test", chat_id=12345, source="text", timeout=1.0
    )
    assert "принято: test" in reply


@pytest.mark.asyncio
async def test_forward_to_agent_real_eventloop_emits_user_chat_message() -> None:
    """С attached bus — emit USER_CHAT_MESSAGE (W5) + await HUMAN_RESPONSE via subscribe_one_correlation."""
    import asyncio

    from bmad_orchestrator.bot import handlers as bot_handlers
    from bmad_orchestrator.runtime.event_loop import EventLoop, EventType

    bus = EventLoop()
    bot_handlers.attach_event_loop(bus)
    bot_handlers.attach_state_db(None, None)
    bot_handlers.reset_for_test()
    try:
        async def _responder() -> None:
            ev = await bus.next(timeout=2.0)
            assert ev is not None
            assert ev.type == EventType.USER_CHAT_MESSAGE
            assert ev.payload["chat_id"] == 999
            assert ev.payload["text"] == "статус"
            corr_id = ev.payload["corr_id"]
            await bus.emit(
                EventType.HUMAN_RESPONSE,
                chat_id=999,
                corr_id=corr_id,
                text="wave 1a: 2/5 done",
            )

        responder = asyncio.create_task(_responder())
        reply = await bot_handlers.forward_to_agent(
            "статус", chat_id=999, source="text", timeout=3.0
        )
        await responder
        assert reply == "wave 1a: 2/5 done"
    finally:
        bot_handlers.attach_event_loop(None)
        bot_handlers.reset_for_test()


@pytest.mark.asyncio
async def test_forward_to_agent_timeout_returns_fallback() -> None:
    from bmad_orchestrator.bot import handlers as bot_handlers
    from bmad_orchestrator.runtime.event_loop import EventLoop

    bus = EventLoop()
    bot_handlers.attach_event_loop(bus)
    bot_handlers.attach_state_db(None, None)
    bot_handlers._HUMAN_RESPONSES.clear()
    try:
        reply = await bot_handlers.forward_to_agent(
            "no-response", chat_id=42, source="text", timeout=0.05
        )
        assert "не ответил" in reply
    finally:
        bot_handlers.attach_event_loop(None)
        bot_handlers._HUMAN_RESPONSES.clear()


# ── system_prompt embeds skill catalog + tool catalog (S5 regression) ────────


def test_system_prompt_blocks_carry_tool_and_skill_metadata() -> None:
    from bmad_orchestrator.agent.system_prompt import build_system_prompt

    blocks = build_system_prompt(Path("/tmp"), wave="1a", locale="ru")
    text_blob = "\n".join(b.get("text", "") for b in blocks if isinstance(b, dict))
    # Tool catalog header
    assert "Tool catalog" in text_blob
    # Skill catalog header
    assert "Skill catalog" in text_blob
    # Ровно один блок tools+skills (объединённый) — но 5 текстовых блоков total
    assert sum(1 for b in blocks if isinstance(b, dict) and b.get("type") == "text") == 5
