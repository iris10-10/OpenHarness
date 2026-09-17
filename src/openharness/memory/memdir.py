"""Memory prompt helpers."""
#记忆提示词辅助工具

from __future__ import annotations

from pathlib import Path

from openharness.memory.paths import get_memory_entrypoint, get_project_memory_dir
from openharness.memory.schema import (
    MAX_ENTRYPOINT_BYTES,#记忆文件的最大字节数限制（常量）
    MEMORY_POLICY_LINES,#记忆使用策略的固定文本行（列表），比如"只追加不删除"、"每次会话结束更新"等规则
    truncate_entrypoint_content,#截断记忆内容的函数，保证不超过限制
)


def load_memory_prompt(
    cwd: str | Path,
    *,
    max_entrypoint_lines: int = 200,
    max_entrypoint_bytes: int = MAX_ENTRYPOINT_BYTES,
) -> str | None:
    """Return the memory prompt section for the current project."""
    #说明返回值是"当前项目的记忆提示部分"
    memory_dir = get_project_memory_dir(cwd)    #获取记忆目录
    entrypoint = get_memory_entrypoint(cwd)     #获取记忆入口文件
    lines = [
        "# Memory",
        f"- Persistent memory directory: {memory_dir}",
        "- Use this directory to store durable project and repository context that should survive future sessions.",
        "- Prefer concise topic files plus an index entry in MEMORY.md.",
        "",
        *MEMORY_POLICY_LINES,
    ]

    if entrypoint.exists():
        raw = entrypoint.read_text(encoding="utf-8", errors="replace")
        view = truncate_entrypoint_content(
            raw,
            max_lines=max_entrypoint_lines,
            max_bytes=max_entrypoint_bytes,
        )
        content = view.content.strip()
        if content:
            lines.extend(["", "## MEMORY.md", "```md", content, "```"])
    else:
        lines.extend(
            [
                "",
                "## MEMORY.md",
                "(not created yet)",
            ]
        )

    return "\n".join(lines)
