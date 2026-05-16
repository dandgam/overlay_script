"""Memory tools (spec §17 + §18.5 Anthropic Memory Tool integration).

read_memory, write_memory, compress_wave_lessons.

Memory layout (S2 mock-mode):
    <orchestrator_home>/.claude/memory/
        per-story/<story_id>.md
        per-wave/<wave>.md
        per-phase/<phase>.md
        index.md

write_memory enforces the path is rooted under memory_dir() — prevents
path-traversal writes via crafted `path` argument.
compress_wave_lessons concatenates per-story files for a given wave into
a single wave-lessons file with a YAML header.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from claude_agent_sdk import tool

from bmad_orchestrator.agent.tools._common import (
    error,
    json_ok,
    memory_dir,
    now_iso,
)


def _safe_memory_path(path: str) -> Path | None:
    """Resolve `path` against memory_dir; reject anything escaping the root."""
    root = memory_dir().resolve()
    root.mkdir(parents=True, exist_ok=True)
    candidate = (root / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


@tool(
    "read_memory",
    "Read memory file (lessons, retros, architectural patterns).",
    {"path": str},
)
async def read_memory(args: dict[str, Any]) -> dict[str, Any]:
    raw = str(args.get("path", "")).strip()
    if not raw:
        return error("missing 'path'", code="invalid_arg")
    target = _safe_memory_path(raw)
    if target is None:
        return error(f"path escapes memory root: {raw!r}", code="path_traversal")
    if not target.exists():
        return json_ok({"path": str(target), "exists": False, "content": ""})
    return json_ok(
        {
            "path": str(target),
            "exists": True,
            "content": target.read_text(encoding="utf-8"),
        }
    )


@tool(
    "write_memory",
    "Append or overwrite memory file. Used by retro/reflexion skills.",
    {"path": str, "content": str, "mode": str},
)
async def write_memory(args: dict[str, Any]) -> dict[str, Any]:
    raw = str(args.get("path", "")).strip()
    content = str(args.get("content", ""))
    mode = str(args.get("mode", "append"))
    if not raw:
        return error("missing 'path'", code="invalid_arg")
    if mode not in ("append", "overwrite"):
        return error(f"invalid mode: {mode!r}", code="invalid_arg")
    target = _safe_memory_path(raw)
    if target is None:
        return error(f"path escapes memory root: {raw!r}", code="path_traversal")
    target.parent.mkdir(parents=True, exist_ok=True)
    if mode == "overwrite":
        target.write_text(content, encoding="utf-8")
    else:
        with target.open("a", encoding="utf-8") as f:
            f.write(content)
            if not content.endswith("\n"):
                f.write("\n")
    return json_ok(
        {
            "path": str(target),
            "mode": mode,
            "bytes_written": len(content.encode("utf-8")),
        }
    )


@tool(
    "compress_wave_lessons",
    "Summarize per-story lessons into a single wave-lessons.md (memory-curator skill).",
    {"wave": str},
)
async def compress_wave_lessons(args: dict[str, Any]) -> dict[str, Any]:
    wave = str(args.get("wave", "")).strip()
    if not wave:
        return error("missing 'wave'", code="invalid_arg")
    root = memory_dir().resolve()
    per_story = root / "per-story"
    out_path = root / "per-wave" / f"{wave}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    fragments: list[str] = []
    sources: list[str] = []
    if per_story.exists():
        for story_md in sorted(per_story.glob(f"{wave}-*.md")):
            fragments.append(f"\n## {story_md.stem}\n\n{story_md.read_text(encoding='utf-8')}")
            sources.append(story_md.name)

    header = (
        f"---\nwave: {wave}\ncompressed_at: {now_iso()}\nsource_count: {len(sources)}\n---\n"
    )
    body = "\n".join(fragments) if fragments else "_no per-story lessons collected_\n"
    out_path.write_text(header + body, encoding="utf-8")
    return json_ok(
        {
            "wave": wave,
            "out_path": str(out_path),
            "sources": sources,
            "byte_count": out_path.stat().st_size,
        }
    )


TOOLS = [read_memory, write_memory, compress_wave_lessons]


__all__ = ["TOOLS", "compress_wave_lessons", "read_memory", "write_memory"]
