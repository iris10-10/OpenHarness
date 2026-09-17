"""Load MCP server config from settings and plugins."""
#负责从设置文件和插件中加载并合并 MCP 服务器配置

from __future__ import annotations

from openharness.plugins.types import LoadedPlugin


def load_mcp_server_configs(settings, plugins: list[LoadedPlugin]) -> dict[str, object]:
    """Merge settings and plugin MCP server configs."""
    servers = dict(settings.mcp_servers)
    for plugin in plugins:
        #跳过未启用的插件
        if not plugin.enabled:
            continue
        for name, config in plugin.mcp_servers.items():
            servers.setdefault(f"{plugin.manifest.name}:{name}", config)
    return servers
