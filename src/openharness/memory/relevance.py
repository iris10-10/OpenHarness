"""Relevant memory selection and formatting."""
#相关记忆的筛选与格式整理

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from openharness.memory.scan import scan_memory_files
from openharness.memory.schema import memory_age_label, memory_freshness_text
from openharness.memory.search import find_relevant_memories
from openharness.memory.types import MemoryHeader


@dataclass(frozen=True)
class RelevantMemory:
    """A memory selected for prompt injection."""

    header: MemoryHeader
    freshness: str = ""


MemorySelector = Callable[[str, list[MemoryHeader]], list[str]]


#将记忆头列表渲染为紧凑的文本格式
def build_memory_manifest(headers: Iterable[MemoryHeader]) -> str:
    """Render a compact manifest for selector prompts and diagnostics."""

    lines: list[str] = []
    for header in headers:
        prefix = f"[{header.memory_type or 'memory'}]"
        bits = [
            prefix,
            header.relative_path or header.path.name,
            f"({memory_age_label(header.modified_at)})",
        ]
        if header.description:
            bits.append(f"- {header.description}")
        lines.append(" ".join(bits))
    return "\n".join(lines)


#智能记忆检索与去重排序
def select_relevant_memories(
    query: str,
    cwd: str | Path,
    *,
    max_results: int = 5,
    already_surfaced: set[str] | None = None,   #已展示过的记忆路径集合，用于去重
    selector: MemorySelector | None = None, #可选的二次排序器（重排序器）
) -> list[RelevantMemory]:
    """Return relevant memories with duplicate and freshness handling.

    ``selector`` is an optional side-query style reranker. It receives the query
    and a heuristic shortlist, and returns relative paths in desired order.
    """

    surfaced = already_surfaced or set()
    #heuristic列表就是去重后的粗召回候选列表
    heuristic = [
        header
        for header in find_relevant_memories(query, cwd, max_results=max(10, max_results * 3))
        if (header.relative_path or str(header.path)) not in surfaced
    ]
    selected = _apply_selector(query, heuristic, selector=selector, max_results=max_results)
    result: list[RelevantMemory] = []
    for header in selected[:max_results]:
        result.append(RelevantMemory(header=header, freshness=memory_freshness_text(header.modified_at)))
    return result


#从完整记忆清单中进行选择
def select_manifest_memories(
    query: str,
    cwd: str | Path,
    *,
    max_results: int = 5,
    selector: MemorySelector | None = None,
) -> list[RelevantMemory]:
    """Select from the full manifest instead of heuristic matches only."""

    headers = scan_memory_files(cwd, max_files=200)
    selected = _apply_selector(query, headers, selector=selector, max_results=max_results)
    return [
        RelevantMemory(header=header, freshness=memory_freshness_text(header.modified_at))
        for header in selected[:max_results]
    ]


#将检索到的记忆内容格式化为 Markdown 文本的工具函数
def format_relevant_memories(memories: Iterable[RelevantMemory], *, max_chars: int = 8000) -> str:
    """Render selected memories for prompt context."""

    lines = ["# Relevant Memories"]
    for item in memories:
        header = item.header
        content = header.path.read_text(encoding="utf-8", errors="replace").strip()
        if item.freshness:
            lines.extend(["", f"## {header.relative_path or header.path.name}", f"> {item.freshness}"])
        else:
            lines.extend(["", f"## {header.relative_path or header.path.name}"])
        lines.extend(["```md", content[:max_chars], "```"])
    return "\n".join(lines)


#用于从 LLM 或其他来源的文本响应中提取路径列表
def json_selector_from_text(text: str) -> list[str]:
    """Parse a selector response as either JSON list or newline paths."""

    stripped = text.strip()
    if not stripped:
        return []
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        #对每行 line：先移除开头的 "- "（如果存在，用于清理 Markdown 列表项），然后再 .strip() 去掉首尾空格
        return [line.strip("- ").strip() for line in stripped.splitlines() if line.strip()]
    if isinstance(payload, list):
        return [str(item).strip() for item in payload if str(item).strip()]
    if isinstance(payload, dict) and isinstance(payload.get("paths"), list):
        return [str(item).strip() for item in payload["paths"] if str(item).strip()]
    return []


#根据一个可选的“选择器”函数，从已有的内存头列表中筛选出最相关的结果，并确保返回数量不超过上限
def _apply_selector(
    query: str,
    headers: list[MemoryHeader],
    *,
    selector: MemorySelector | None,    #可选的重排序器
    max_results: int,
) -> list[MemoryHeader]:
    if not headers or selector is None:
        return headers[:max_results]
    requested = selector(query, headers)
    by_path = {header.relative_path or header.path.name: header for header in headers}
    selected: list[MemoryHeader] = []#存放最终选中的 MemoryHeader 列表
    seen: set[str] = set()#存放已经处理过的路径字符串，用于去重
    for path in requested:
        header = by_path.get(path)
        if header is None or path in seen:
            continue
        selected.append(header)
        seen.add(path)
    if len(selected) < max_results:#数量不够，继续补充
        selected.extend(
            header
            for header in headers
            if (header.relative_path or header.path.name) not in seen
        )
    return selected[:max_results]
