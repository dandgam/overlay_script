"""Multi-LLM judge implementations for SupervisorEngine Tier 1.

Registered providers
--------------------
* ``claude_p`` — :class:`ClaudePJudge`  (Sonnet via ``claude -p`` CLI subprocess,
  Claude Code subscription — **default, no API key required**)
* ``anthropic`` — :class:`AnthropicJudge`  (Sonnet 4.6 via AsyncAnthropic SDK,
  requires ``ANTHROPIC_API_KEY``)

Adding a new provider
---------------------
1. Create ``<provider>_judge.py`` in this package.
2. Implement :class:`~bmad_orchestrator.supervisor.llm_judge.LLMJudgeProtocol`:
   a single ``async def classify(self, input_: JudgeInput) -> JudgeVerdict`` method.
   Raise :class:`~bmad_orchestrator.supervisor.llm_judge.JudgeError` on any
   transport / parse failure (engine falls back to Tier 2 escalate_human).
3. Import and re-export from this ``__init__.py``.
4. Add provider name to ``JudgeProvider`` Literal in ``supervisor/policy.py``.
5. Wire in ``agent/run.py:_supervisor_judge_factory`` for the new provider name.
6. Add tests in ``tests/test_supervisor_<provider>_judge.py`` (≥ 8 tests).

Implementation examples
-----------------------
* **ClaudePJudge** (``claude_p``) — subprocess transport, no SDK dependency:
  uses ``asyncio.create_subprocess_exec(claude_bin, "-p", "--model", model, prompt)``.
  JSON recovery: direct parse → fence strip → repair subprocess call.
* **AnthropicJudge** (``anthropic``) — SDK transport with prompt caching:
  uses ``anthropic.AsyncAnthropic().messages.create(...)`` with
  ``cache_control={"type": "ephemeral"}`` on the system block.

Planned (not yet implemented)
------------------------------
* ``gemini_judge.py``  — Google Gemini via ``google-generativeai`` SDK
* ``openai_judge.py``  — OpenAI GPT via ``openai`` SDK
* ``yandex_judge.py``  — YandexGPT via REST API
* ``ollama_judge.py``  — Local Ollama endpoint (no API key required)
"""

from __future__ import annotations

from bmad_orchestrator.supervisor.judges.anthropic_judge import AnthropicJudge
from bmad_orchestrator.supervisor.judges.claude_p_judge import ClaudePJudge

__all__ = ["AnthropicJudge", "ClaudePJudge"]
