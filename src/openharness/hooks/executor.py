"""Hook execution engine."""
#钩子（Hook）执行引擎 

from __future__ import annotations

import asyncio
import fnmatch
import json
import os
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from openharness.api.client import ApiMessageCompleteEvent, ApiMessageRequest, SupportsStreamingMessages
from openharness.engine.messages import ConversationMessage
from openharness.hooks.events import HookEvent
from openharness.hooks.loader import HookRegistry
from openharness.hooks.schemas import (
    AgentHookDefinition,
    CommandHookDefinition,
    HookDefinition,
    HttpHookDefinition,
    PromptHookDefinition,
)
from openharness.hooks.types import AggregatedHookResult, HookResult
from openharness.sandbox import SandboxUnavailableError
from openharness.utils.shell import create_shell_subprocess


@dataclass
class HookExecutionContext:
    """Context passed into hook execution."""

    cwd: Path
    api_client: SupportsStreamingMessages   #一个支持流式消息传输的 API 客户端对象
    default_model: str  #默认的模型名称，它是一个字符串，用来指定钩子执行时默认使用哪个 AI 模型


class HookExecutor:
    """Execute hooks for lifecycle events."""

    def __init__(self, registry: HookRegistry, context: HookExecutionContext) -> None:
        self._registry = registry
        self._context = context

    def update_registry(self, registry: HookRegistry) -> None:
        """Replace the active hook registry."""
        self._registry = registry

    def update_context(
        self,
        *,
        api_client: SupportsStreamingMessages | None = None,
        default_model: str | None = None,
    ) -> None:
        """Update the active hook execution context."""
        if api_client is not None:
            self._context.api_client = api_client
        if default_model is not None:
            self._context.default_model = default_model

    #payload是事件携带的数据
    async def execute(self, event: HookEvent, payload: dict[str, Any]) -> AggregatedHookResult:
        """Execute all matching hooks for an event."""
        results: list[HookResult] = []
        for hook in self._registry.get(event):
            #用钩子去跟payload里的内容做匹配，匹配的上才可以用
            if not _matches_hook(hook, payload):
                continue
            #类型判断 + 多态执行
            if isinstance(hook, CommandHookDefinition):
                results.append(await self._run_command_hook(hook, event, payload))
            elif isinstance(hook, HttpHookDefinition):
                results.append(await self._run_http_hook(hook, event, payload))
            elif isinstance(hook, PromptHookDefinition):
                results.append(await self._run_prompt_like_hook(hook, event, payload, agent_mode=False))
            elif isinstance(hook, AgentHookDefinition):
                results.append(await self._run_prompt_like_hook(hook, event, payload, agent_mode=True))
        return AggregatedHookResult(results=results)

    async def _run_command_hook(
        self,
        hook: CommandHookDefinition,    #钩子本身
        event: HookEvent,   #触发钩子的场景
        payload: dict[str, Any],
    ) -> HookResult:
        #把 hook.command 中的占位符，用 payload 里的实际数据替换掉
        #同时做 shell 转义处理（防止用户输入恶意内容（如 ; rm -rf /）），生成一条最终可执行的命令
        command = _inject_arguments(hook.command, payload, shell_escape=True)
        try:
            #创建一个子进程
            process = await create_shell_subprocess(
                command,
                cwd=self._context.cwd,  #工作目录
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={
                    **os.environ,   # 继承系统环境变量
                    "OPENHARNESS_HOOK_EVENT": event.value,  # 注入事件信息
                    "OPENHARNESS_HOOK_PAYLOAD": json.dumps(payload),    # 注入完整payload
                },
            )
        except SandboxUnavailableError as exc:#当沙盒环境不可用或创建失败时，不崩溃，而是返回一个封装好的失败结果
            return HookResult(
                hook_type=hook.type,
                success=False,
                blocked=hook.block_on_failure,  #根据配置决定是否"阻断"主流程
                reason=str(exc),
            )

        #stdout:标准输出内容
        #stderr:标准错误内容
        #communicate同时读 stdout 和 stderr，自动避免死锁，等待进程结束
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=hook.timeout_seconds,
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()    # 等待操作系统回收进程资源
            return HookResult(
                hook_type=hook.type,
                success=False,
                blocked=hook.block_on_failure,
                reason=f"command hook timed out after {hook.timeout_seconds}s",
            )

        #合并stdout和stderr，丢弃空部分
        output = "\n".join(
            part for part in (
                stdout.decode("utf-8", errors="replace").strip(),
                stderr.decode("utf-8", errors="replace").strip(),
            ) if part
        )
        #process.returncode == 0，退出码是0，代表子进程正常退出
        success = process.returncode == 0
        return HookResult(
            hook_type=hook.type,
            success=success,
            output=output,
            blocked=hook.block_on_failure and not success,
            reason=output or f"command hook failed with exit code {process.returncode}",
            metadata={"returncode": process.returncode},
        )

    async def _run_http_hook(
        self,
        hook: HttpHookDefinition,
        event: HookEvent,
        payload: dict[str, Any],
    ) -> HookResult:
        try:
            async with httpx.AsyncClient(timeout=hook.timeout_seconds) as client:#✅ 有 with（自动管理），退出后自动关闭，不用你操心
                response = await client.post(
                    hook.url,
                    json={"event": event.value, "payload": payload},
                    headers=hook.headers,
                )
            success = response.is_success
            output = response.text
            return HookResult(
                hook_type=hook.type,
                success=success,
                output=output,
                blocked=hook.block_on_failure and not success,
                reason=output or f"http hook returned {response.status_code}",
                metadata={"status_code": response.status_code},
            )
        except Exception as exc:
            return HookResult(
                hook_type=hook.type,
                success=False,
                blocked=hook.block_on_failure,
                reason=str(exc),
            )

    async def _run_prompt_like_hook(
        self,
        hook: PromptHookDefinition | AgentHookDefinition,
        event: HookEvent,
        payload: dict[str, Any],
        *,
        agent_mode: bool,   #是否启用"代理模式"
    ) -> HookResult:
        prompt = _inject_arguments(hook.prompt, payload)
        #基础 prefix，告诉 LLM 它的角色（验证者）和输出格式（严格 JSON）
        prefix = (
            "You are validating whether a hook condition passes in OpenHarness. "
            "Return strict JSON: {\"ok\": true} or {\"ok\": false, \"reason\": \"...\"}."
        )
        if agent_mode:
            #代理模式需要llm更加"深思熟虑"
            prefix += " Be more thorough and reason over the payload before deciding."
        #构造API请求
        request = ApiMessageRequest(
            model=hook.model or self._context.default_model,
            messages=[ConversationMessage.from_user_text(prompt)],
            system_prompt=prefix,
            max_tokens=512,
        )

        text_chunks: list[str] = [] #存储流式响应中的文本碎片(每个chunk是一小段文字)
        final_event: ApiMessageCompleteEvent | None = None  #存储流结束时的完整事件，初始为 None
        async for event_item in self._context.api_client.stream_message(request):
            #event_item是ai回答的文字片段
            #看这个片段是不是完整事件
            if isinstance(event_item, ApiMessageCompleteEvent):
                final_event = event_item
            else:
                text_chunks.append(event_item.text)

        text = "".join(text_chunks)
        #如果有完整事件，就用完整事件替换
        if final_event is not None and final_event.message.text:
            text = final_event.message.text

        #parsed 是 AI 钩子（Prompt/Agent Hook）返回的执行结果判断
        parsed = _parse_hook_json(text)
        if parsed["ok"]:
            return HookResult(hook_type=hook.type, success=True, output=text)
        return HookResult(
            hook_type=hook.type,
            success=False,
            output=text,
            blocked=hook.block_on_failure,
            reason=parsed.get("reason", "hook rejected the event"),
        )


##判断当前钩子是否应该被执行
def _matches_hook(hook: HookDefinition, payload: dict[str, Any]) -> bool:
    matcher = getattr(hook, "matcher", None)
    if not matcher:
        return True
    subject = str(payload.get("tool_name") or payload.get("prompt") or payload.get("event") or "")
    return fnmatch.fnmatch(subject, matcher)


#把 payload 数据序列化为 JSON 字符串，替换模板中的 $ARGUMENTS 占位符
def _inject_arguments(
    template: str, payload: dict[str, Any], *, shell_escape: bool = False
) -> str:
    serialized = json.dumps(payload, ensure_ascii=True)
    if shell_escape:
        serialized = shlex.quote(serialized)
    return template.replace("$ARGUMENTS", serialized)


#把AI返回的混乱文本，尽量解析成结构化的 {"ok": bool} 字典，实在解析不了就返回一个"拒绝"的兜底结果
def _parse_hook_json(text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict) and isinstance(parsed.get("ok"), bool):
            return parsed
    except json.JSONDecodeError:
        pass
    lowered = text.strip().lower()
    if lowered in {"ok", "true", "yes"}:
        return {"ok": True}
    return {"ok": False, "reason": text.strip() or "hook returned invalid JSON"}
