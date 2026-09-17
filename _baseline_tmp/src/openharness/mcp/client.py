"""MCP client manager."""
#MCP 客户端管理器

from __future__ import annotations

import asyncio
import contextlib
from contextlib import AsyncExitStack
from typing import Any

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, ReadResourceResult

from openharness.mcp.types import (
    McpConnectionStatus,
    McpHttpServerConfig,
    McpResourceInfo,
    McpStdioServerConfig,
    McpToolInfo,
)


#自定义异常类，用于Python程序中与MCP（Model Context Protocol）服务器交互时的错误处理
class McpServerNotConnectedError(Exception):
    """Raised when an MCP server is not connected or its session has been lost."""


class McpClientManager:
    """Manage MCP connections and expose tools/resources."""
    #“管理MCP连接并暴露工具/资源”

    def __init__(self, server_configs: dict[str, object]) -> None:
        self._server_configs = server_configs
        self._statuses: dict[str, McpConnectionStatus] = {
            name: McpConnectionStatus(
                name=name,
                state="pending",
                transport=getattr(config, "type", "unknown"),
            )
            for name, config in server_configs.items()
        }
        self._sessions: dict[str, ClientSession] = {}   #用于存储已成功建立的客户端会话
        self._stacks: dict[str, AsyncExitStack] = {}    #用于存储异步上下文栈

    #批量连接所有配置的 MCP 服务器
    async def connect_all(self) -> None:
        """Connect all configured MCP servers supported by the current build."""
        for name, config in self._server_configs.items():
            if isinstance(config, McpStdioServerConfig):
                await self._connect_stdio(name, config)
            elif isinstance(config, McpHttpServerConfig):
                await self._connect_http(name, config)
            else:
                #创建失败状态记录
                self._statuses[name] = McpConnectionStatus(
                    name=name,
                    state="failed",
                    transport=config.type,
                    auth_configured=bool(getattr(config, "headers", None)),
                    detail=f"Unsupported MCP transport in current build: {config.type}",
                )

    async def reconnect_all(self) -> None:
        """Reconnect all configured servers."""
        await self.close()
        self._statuses = {
            name: McpConnectionStatus(name=name, state="pending", transport=getattr(config, "type", "unknown"))
            for name, config in self._server_configs.items()
        }
        await self.connect_all()

    def update_server_config(self, name: str, config: object) -> None:
        """Replace one server config in memory."""
        self._server_configs[name] = config

    def get_server_config(self, name: str) -> object | None:
        """Return one configured server object if present."""
        return self._server_configs.get(name)

    async def _close_failed_stack(self, stack: AsyncExitStack) -> None:
        """Best-effort cleanup for a connection attempt that never finished."""
        try:
            await stack.aclose()
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise

    def _mark_connection_failed(
        self,
        name: str,
        config: object,
        *,
        auth_configured: bool,
        exc: BaseException,
    ) -> None:
        """Record one MCP connection failure without aborting startup."""
        self._statuses[name] = McpConnectionStatus(
            name=name,
            state="failed",
            transport=getattr(config, "type", "unknown"),
            auth_configured=auth_configured,
            detail=str(exc) or exc.__class__.__name__,
        )

    async def close(self) -> None:
        """Close all active MCP sessions."""
        for stack in list(self._stacks.values()):#遍历副本
            #一个栈关闭失败，不应该影响其他栈的关闭。所以跳过一些错误
            with contextlib.suppress(RuntimeError, asyncio.CancelledError):
                await stack.aclose()
        self._stacks.clear()
        self._sessions.clear()

    def list_statuses(self) -> list[McpConnectionStatus]:
        """Return statuses for all configured servers."""
        return [self._statuses[name] for name in sorted(self._statuses)]

    def list_tools(self) -> list[McpToolInfo]:
        """Return all connected MCP tools."""
        tools: list[McpToolInfo] = []
        for status in self.list_statuses():
            tools.extend(status.tools)
        return tools

    def list_resources(self) -> list[McpResourceInfo]:
        """Return all connected MCP resources."""
        resources: list[McpResourceInfo] = []
        for status in self.list_statuses():
            resources.extend(status.resources)
        return resources

    #实际调用外部工具的核心入口
    async def call_tool(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> str:
        """Invoke one MCP tool and stringify the result."""
        session = self._sessions.get(server_name)
        if session is None:
            status = self._statuses.get(server_name)
            detail = status.detail if status else "unknown server"
            raise McpServerNotConnectedError(
                f"MCP server '{server_name}' is not connected: {detail}"
            )
        try:
            result: CallToolResult = await session.call_tool(tool_name, arguments)
        except Exception as exc:
            raise McpServerNotConnectedError(
                f"MCP server '{server_name}' call failed: {exc}"
            ) from exc
        parts: list[str] = []#创建一个空列表，用于存储工具返回的各个部分
        for item in result.content:
            if getattr(item, "type", None) == "text":
                parts.append(getattr(item, "text", ""))
            else:
                parts.append(item.model_dump_json())
        if result.structuredContent and not parts:#处理结构化内容
            parts.append(str(result.structuredContent))
        if not parts:
            parts.append("(no output)")
        return "\n".join(parts).strip()

    async def read_resource(self, server_name: str, uri: str) -> str:
        """Read one MCP resource and stringify the response."""
        session = self._sessions.get(server_name)
        if session is None:
            status = self._statuses.get(server_name)
            detail = status.detail if status else "unknown server"
            raise McpServerNotConnectedError(
                f"MCP server '{server_name}' is not connected: {detail}"
            )
        try:
            result: ReadResourceResult = await session.read_resource(uri)
        except Exception as exc:
            raise McpServerNotConnectedError(
                f"MCP server '{server_name}' resource read failed: {exc}"
            ) from exc
        parts: list[str] = []
        for item in result.contents:
            text = getattr(item, "text", None)
            if text is not None:
                parts.append(text)
            else:
                parts.append(str(getattr(item, "blob", "")))
        return "\n".join(parts).strip()

    async def _connect_stdio(self, name: str, config: McpStdioServerConfig) -> None:
        stack = AsyncExitStack()#为这个服务器创建一个独立的 AsyncExitStack，管理该服务器所有异步资源的生命周期
        try:#尝试连接
            #将这个服务器注册到栈中，确保它们能按正确的顺序被清理
            read_stream, write_stream = await stack.enter_async_context(
                stdio_client(
                    #一个参数对象，描述如何启动子进程
                    StdioServerParameters(
                        command=config.command,#要执行的命令
                        args=config.args,#命令行参数
                        env=config.env,#环境变量
                        cwd=config.cwd,#工作目录
                    )
                )
            )
            #注册连接
            await self._register_connected_session(
                name=name,
                config=config,
                stack=stack,
                read_stream=read_stream,
                write_stream=write_stream,
                auth_configured=bool(config.env),
            )
        except asyncio.CancelledError as exc:#当任务被取消时抛出（如用户中断、超时）
            await self._close_failed_stack(stack)#关闭已经注册的资源（子进程、流等），避免资源泄漏
            #将连接失败的信息规范化地记录下来。
            self._mark_connection_failed(
                name,
                config,
                auth_configured=bool(config.env),
                exc=exc,
            )
        except Exception as exc:
            await self._close_failed_stack(stack)
            self._mark_connection_failed(
                name,
                config,
                auth_configured=bool(config.env),
                exc=exc,
            )

    async def _connect_http(self, name: str, config: McpHttpServerConfig) -> None:
        stack = AsyncExitStack()
        try:
            #创建 HTTP 客户端
            http_client = await stack.enter_async_context(
                httpx.AsyncClient(headers=config.headers or None)
            )
            #建立流式 HTTP 连接
            read_stream, write_stream, _get_session_id = await stack.enter_async_context(
                streamable_http_client(config.url, http_client=http_client)
            )
            #注册连接
            await self._register_connected_session(
                name=name,
                config=config,
                stack=stack,
                read_stream=read_stream,
                write_stream=write_stream,
                auth_configured=bool(config.headers),
            )
        except asyncio.CancelledError as exc:
            await self._close_failed_stack(stack)
            self._mark_connection_failed(
                name,
                config,
                auth_configured=bool(config.headers),
                exc=exc,
            )
        except Exception as exc:
            await self._close_failed_stack(stack)
            self._mark_connection_failed(
                name,
                config,
                auth_configured=bool(config.headers),
                exc=exc,
            )

#将原始连接升级为完整 MCP 会话并记录所有可用能力的核心方法
    async def _register_connected_session(
        self,
        *,
        name: str,
        config: object,
        stack: AsyncExitStack,
        read_stream: Any,
        write_stream: Any,
        auth_configured: bool,
    ) -> None:
        #创建并注册 ClientSession
        session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
        #mcp握手
        await session.initialize()
        tool_result = await session.list_tools()
        resource_result = None# 默认没有资源
        try:
            resource_result = await session.list_resources()
        except Exception as exc:
            if "Method not found" not in str(exc):
                raise
        tools = [
            McpToolInfo(
                server_name=name,
                name=tool.name,
                description=tool.description or "",
                input_schema=dict(tool.inputSchema or {"type": "object", "properties": {}}),
            )
            for tool in tool_result.tools
        ]
        resources = [
            McpResourceInfo(
                server_name=name,
                name=resource.name or str(resource.uri),
                uri=str(resource.uri),
                description=resource.description or "",
            )
            for resource in (resource_result.resources if resource_result is not None else [])
        ]
        self._sessions[name] = session
        self._stacks[name] = stack
        self._statuses[name] = McpConnectionStatus(
            name=name,
            state="connected",
            transport=getattr(config, "type", "unknown"),
            auth_configured=auth_configured,
            tools=tools,
            resources=resources,
        )
