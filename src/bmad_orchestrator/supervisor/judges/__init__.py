"""Multi-LLM judge implementations for SupervisorEngine Tier 1.

Registered providers
--------------------
* ``anthropic`` — :class:`AnthropicJudge`  (Sonnet 4.6 via AsyncAnthropic SDK)

Adding a new provider
---------------------
1. Create ``<provider>_judge.py`` in this package.
2. Implement :class:`~bmad_orchestrator.supervisor.llm_judge.LLMJudgeProtocol`:
   a single ``async def classify(self, input_: JudgeInput) -> JudgeVerdict`` method.
   Raise :class:`~bmad_orchestrator.supervisor.llm_judge.JudgeError` on any
   transport / parse failure (engine falls back to Tier 2 escalate_human).
3. Import and re-export from this ``__init__.py``.
4. Wire in ``agent/run.py:_supervisor_judge_factory`` for the new provider name.

Planned (not yet implemented)
------------------------------
* ``gemini_judge.py``  — Google Gemini via ``google-generativeai`` SDK
* ``openai_judge.py``  — OpenAI GPT via ``openai`` SDK
* ``yandex_judge.py``  — YandexGPT via REST API
* ``ollama_judge.py``  — Local Ollama endpoint (no API key required)
"""

from __future__ import annotations

from bmad_orchestrator.supervisor.judges.anthropic_judge import AnthropicJudge

__all__ = ["AnthropicJudge"]
