"""Token usage tracker (deterministic, спец §10 observability).

Parses Anthropic JSONL `usage` blocks:
  usage.input_tokens
  usage.cache_creation_input_tokens  (split 5m / 1h)
  usage.cache_read_input_tokens
  usage.output_tokens

Applies model-specific pricing → SQL aggregates per story/batch/wave/day.
"""

from __future__ import annotations

# TODO: implement aggregator over aiosqlite. Schema:
#   token_usage(ts, story_id, wave, model, input, cache_create, cache_read, output, cost_usd)
