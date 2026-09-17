"""Hook event names supported by OpenHarness."""

from __future__ import annotations

from enum import Enum


#列出了所有可以触发钩子执行的生命周期节点
class HookEvent(str, Enum):
    """Events that can trigger hooks."""

    SESSION_START = "session_start"
    SESSION_END = "session_end"
    PRE_COMPACT = "pre_compact"
    POST_COMPACT = "post_compact"
    PRE_TOOL_USE = "pre_tool_use"
    POST_TOOL_USE = "post_tool_use"
    USER_PROMPT_SUBMIT = "user_prompt_submit"
    NOTIFICATION = "notification"
    STOP = "stop"
    SUBAGENT_STOP = "subagent_stop"
