"""Permission checking for tool execution."""
#工具执行前的权限检查

from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass

from openharness.config.settings import PermissionSettings
from openharness.permissions.modes import PermissionMode

log = logging.getLogger(__name__)   #说明这个模块有自己的日志输出，调试权限问题时可以看这个 logger

# Paths that are always denied regardless of permission mode or user config.
# These protect high-value credential and key material from LLM-directed access
# (including via prompt injection).  Patterns use fnmatch syntax and are matched
# against the fully-resolved absolute path produced by the query engine.
#这段代码定义了一个敏感文件路径黑名单
#用于防止 AI 助手（或工具）在读取用户文件时，意外泄露密码、密钥或云服务凭证
SENSITIVE_PATH_PATTERNS: tuple[str, ...] = (
    # SSH keys and config
    "*/.ssh/*",
    # AWS credentials
    "*/.aws/credentials",
    "*/.aws/config",
    # GCP credentials
    "*/.config/gcloud/*",
    # Azure credentials
    "*/.azure/*",
    # GPG keys
    "*/.gnupg/*",
    # Docker credentials
    "*/.docker/config.json",
    # Kubernetes credentials
    "*/.kube/config",
    # OpenHarness own credential stores
    "*/.openharness/credentials.json",
    "*/.openharness/copilot_auth.json",
)


@dataclass(frozen=True)
class PermissionDecision:
    """Result of checking whether a tool invocation may run."""

    allowed: bool   #工具是否被允许执行
    requires_confirmation: bool = False #这个字段决定是"直接拒死"还是"还能弹窗问用户"
    reason: str = ""


@dataclass(frozen=True)
class PathRule:
    """A glob-based path permission rule."""

    pattern: str    #Glob 模式匹配路径
    allow: bool  # True = allow, False = deny


class PermissionChecker:
    """Evaluate tool usage against the configured permission mode and rules."""

    def __init__(self, settings: PermissionSettings) -> None:
        self._settings = settings
        # Parse path rules from settings
        #从设置中解析路径规则
        self._path_rules: list[PathRule] = []
        for rule in getattr(settings, "path_rules", []):
            pattern = getattr(rule, "pattern", None) or (rule.get("pattern") if isinstance(rule, dict) else None)
            allow = getattr(rule, "allow", True) if not isinstance(rule, dict) else rule.get("allow", True)
            if isinstance(pattern, str) and pattern.strip():
                self._path_rules.append(PathRule(pattern=pattern.strip(), allow=allow))
            else:
                log.warning(
                    "Skipping path rule with missing, empty, or non-string 'pattern' field: %r",
                    rule,
                )

    def evaluate(
        self,
        tool_name: str,
        *,
        is_read_only: bool,
        file_path: str | None = None,
        command: str | None = None,
    ) -> PermissionDecision:
        """Return whether the tool may run immediately."""
        # Built-in sensitive path protection — always active, cannot be
        # overridden by user settings or permission mode.  This is a
        # defence-in-depth measure against LLM-directed or prompt-injection
        # driven access to credential files.
        # 内置敏感路径保护 — 始终生效，无法被用户设置或权限模式覆盖。
        # 这是一项纵深防御措施，用于防范大语言模型（LLM）定向访问或提示注入攻击驱动的凭证文件读取行为。
        if file_path:
            #因为_policy_match_paths函数返回的是两个结果，所以用for循环
            for candidate_path in _policy_match_paths(file_path):
                for pattern in SENSITIVE_PATH_PATTERNS:
                    if fnmatch.fnmatch(candidate_path, pattern):
                        return PermissionDecision(
                            allowed=False,
                            reason=(
                                f"Access denied: {file_path} is a sensitive credential path "
                                f"(matched built-in pattern '{pattern}')"
                            ),
                        )

        # Explicit tool deny list
        #工具黑名单 
        if tool_name in self._settings.denied_tools:
            return PermissionDecision(allowed=False, reason=f"{tool_name} is explicitly denied")

        # Explicit tool allow list
        #工具白名单
        if tool_name in self._settings.allowed_tools:
            return PermissionDecision(allowed=True, reason=f"{tool_name} is explicitly allowed")

        # Check path-level rules
        #检查路径权限规则
        #要符合用户自己配置的路径规则
        if file_path and self._path_rules:
            for candidate_path in _policy_match_paths(file_path):
                for rule in self._path_rules:   #self._path_rules是init方法里从设置里解析出来的
                    if fnmatch.fnmatch(candidate_path, rule.pattern):
                        if not rule.allow:
                            return PermissionDecision(
                                allowed=False,
                                reason=f"Path {file_path} matches deny rule: {rule.pattern}",
                            )

        # Check command deny patterns (e.g. deny "rm -rf /")
        #检查命令是否命中危险操作黑名单（例如：禁止执行 `rm -rf /` 等破坏性命令）
        if command:
            for pattern in getattr(self._settings, "denied_commands", []):
                if isinstance(pattern, str) and fnmatch.fnmatch(command, pattern):
                    return PermissionDecision(
                        allowed=False,
                        reason=f"Command matches deny pattern: {pattern}",
                    )

        # Full auto: allow everything
        # 全自动模式：跳过所有权限检查，允许任意操作
        if self._settings.mode == PermissionMode.FULL_AUTO:
            return PermissionDecision(allowed=True, reason="Auto mode allows all tools")

        # Read-only tools always allowed
        if is_read_only:
            return PermissionDecision(allowed=True, reason="read-only tools are allowed")

        # Plan mode: block mutating tools
        if self._settings.mode == PermissionMode.PLAN:
            return PermissionDecision(
                allowed=False,
                reason="Plan mode blocks mutating tools until the user exits plan mode",
            )

        # Default mode: require confirmation for mutating tools
        #说明当前是"默认模式"下的处理逻辑。在这个模式下
        #所有会修改系统状态的操作（mutating tools）都不能直接执行，必须经过用户确认。
        bash_hint = _bash_permission_hint(command)
        reason = (
            "Mutating tools require user confirmation in default mode. "
            "Approve the prompt when asked, or run /permissions full_auto "
            "if you want to allow them for this session."
        )
        if bash_hint:
            reason = f"{reason} {bash_hint}"
        return PermissionDecision(
            allowed=False,
            requires_confirmation=True,
            reason=reason,
        )


#本质上就是在做"格式处理"——给路径加（或补）斜杠，生成一个"替身"去参与匹配
def _policy_match_paths(file_path: str) -> tuple[str, ...]:
    """Return path forms that should participate in policy matching.

    Directory-scoped tools like ``grep`` and ``glob`` may operate on a root such
    as ``/home/user/.ssh``. Appending a trailing slash lets glob-style deny
    patterns like ``*/.ssh/*`` and ``/etc/*`` match the directory root itself.
    """
    normalized = file_path.rstrip("/")
    if not normalized:
        return (file_path,)
    return (normalized, normalized + "/")


def _bash_permission_hint(command: str | None) -> str:
    if not command:
        return ""
    lowered = command.lower()
    install_markers = (
        "npm install",
        "pnpm install",
        "yarn install",
        "bun install",
        "pip install",
        "uv pip install",
        "poetry install",
        "cargo install",
        "create-next-app",
        "npm create ",
        "pnpm create ",
        "yarn create ",
        "bun create ",
        "npx create-",
        "npm init ",
        "pnpm init ",
        "yarn init ",
    )
    if any(marker in lowered for marker in install_markers):
        return (
            "Package installation and scaffolding commands change the workspace, "
            "so they will not run automatically in default mode."
        )
    return ""
