"""Conversation message models used by the query engine."""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any, Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


#纯文本内容
class TextBlock(BaseModel):
    """Plain text content."""

    type: Literal["text"] = "text"#只能是"text"
    text: str


class ImageBlock(BaseModel):
    """Image content encoded inline for multimodal providers."""

    type: Literal["image"] = "image"
    media_type: str # MIME类型，如 "image/png"
    data: str   # Base64编码的图像数据
    source_path: str = ""   ## 原始文件路径（可选）

    @classmethod
    def from_path(cls, path: str | Path) -> "ImageBlock":
        """Load a local image file into a base64-backed content block."""
        #路径解析
        resolved = Path(path).expanduser().resolve()
        #猜测MIME类型
        media_type, _ = mimetypes.guess_type(str(resolved))
        #验证是否为图片
        if not media_type or not media_type.startswith("image/"):
            raise ValueError(f"Unsupported image attachment: {resolved}")
        #读取并编码
        payload = base64.b64encode(resolved.read_bytes()).decode("ascii")
        return cls(media_type=media_type, data=payload, source_path=str(resolved))


class ToolUseBlock(BaseModel):
    """A request from the model to execute a named tool."""

    type: Literal["tool_use"] = "tool_use"
    #每次创建都生成新的唯一的id
    id: str = Field(default_factory=lambda: f"toolu_{uuid4().hex}")
    name: str
    input: dict[str, Any] = Field(default_factory=dict)


class ToolResultBlock(BaseModel):
    """Tool result content sent back to the model."""

    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: str    #核心结果
    is_error: bool = False
    result_metadata: dict[str, Any] = Field(default_factory=dict)   #额外信息


ContentBlock = Annotated[
    TextBlock | ImageBlock | ToolUseBlock | ToolResultBlock,
    Field(discriminator="type"),#用以区分是哪一种
]


class ConversationMessage(BaseModel):
    """A single assistant or user message."""

    role: Literal["user", "assistant"]  #消息发送者
    content: list[ContentBlock] = Field(default_factory=list)   #消息内容列表

    @field_validator("content", mode="before")
    @classmethod
    #在验证之前，将 None 值转为空列表。
    def _normalize_content(cls, value: Any) -> list[Any]:
        """Normalize legacy/null payloads before block validation."""
        if value is None:
            return []
        return value

    ## 创建用户消息
    @classmethod
    def from_user_text(cls, text: str) -> "ConversationMessage":
        """Construct a user message from raw text."""
        return cls(role="user", content=[TextBlock(text=text)])

    @classmethod
    def from_user_content(cls, content: list[ContentBlock]) -> "ConversationMessage":
        """Construct a user message from explicit content blocks."""
        return cls(role="user", content=list(content))#防御性代码

    #把消息里所有纯文本块（TextBlock）的文字按顺序拼成一个完整字符串
    @property
    def text(self) -> str:
        """Return concatenated text blocks."""
        return "".join(
            block.text for block in self.content if isinstance(block, TextBlock)
        )

    #一个便捷的过滤器，快速从混杂的内容中提取出所有工具调用请求
    @property
    def tool_uses(self) -> list[ToolUseBlock]:
        """Return all tool calls contained in the message."""
        return [block for block in self.content if isinstance(block, ToolUseBlock)]

    #将内部的 ConversationMessage 对象转换为 Anthropic API 要求的格式
    def to_api_param(self) -> dict[str, Any]:
        """Convert the message into Anthropic SDK message params."""
        return {
            "role": self.role,
            "content": [serialize_content_block(block) for block in self.content],
        }

    #判断消息是否包含"有用的"内容，空的就返回True
    def is_effectively_empty(self) -> bool:
        """Return True when the message carries no useful content."""
        if self.content:
            for block in self.content:
                if isinstance(block, TextBlock) and block.text.strip():
                    return False
                if isinstance(block, (ImageBlock, ToolUseBlock, ToolResultBlock)):
                    return False
        return True


#整个函数是一个「先append、后追责」的状态机：每遇到 assistant 的 tool_use 就先收进 sanitized 并记账（pending）；
#下一条消息来了再回头审计——如果tool_use得到了完整result就销账，否则把欠债的那条assistant消息回删。三个状态变量里，pending_tool_use_index是「回删能力」的关键，没有它就无法定位要删除的目标。
def sanitize_conversation_messages(messages: list[ConversationMessage]) -> list[ConversationMessage]:
    """Normalize restored conversation history into a provider-safe sequence.

    This drops legacy empty assistant messages and trims malformed trailing tool
    turns, such as an assistant ``tool_use`` message that never received a
    matching user ``tool_result`` response. Those broken tails can happen when a
    session is interrupted mid-turn and would later cause OpenAI-compatible
    providers to reject the resumed conversation.
    """
    sanitized: list[ConversationMessage] = []   #用来放清洗后的好消息
    pending_tool_use_ids: set[str] = set()  #记录哪些工具调用已经被 AI 发出，但还没有收到执行结果。
    pending_tool_use_index: int | None = None   

    for message in messages:
        #移除空助手消息
        if message.role == "assistant" and message.is_effectively_empty():
            continue

        tool_uses = message.tool_uses if message.role == "assistant" else [] #只有 AI 助手（assistant）才会调用工具。   
        tool_results = [
            block for block in message.content if isinstance(block, ToolResultBlock)
        ] if message.role == "user" else []

        matched_pending_tool_results = False
        #看看有没有待匹配的
        if pending_tool_use_ids:
            #提取当前消息中的工具结果 ID
            result_ids = {block.tool_use_id for block in tool_results}  #生成一个集合
            if message.role != "user" or not pending_tool_use_ids.issubset(result_ids):
                if pending_tool_use_index is not None and pending_tool_use_index < len(sanitized):#删除前的边界处理
                    sanitized.pop(pending_tool_use_index)
                pending_tool_use_ids = set()
                pending_tool_use_index = None
            else:
                matched_pending_tool_results = True
                pending_tool_use_ids = set()
                pending_tool_use_index = None

        #用户发了工具结果，但这个工具结果"没人认领"
        #就是把里面跟工具调用有关的去除掉了
        if message.role == "user" and tool_results and not matched_pending_tool_results:
            content = [
                block for block in message.content if not isinstance(block, ToolResultBlock)
            ]
            if not content:
                continue
            message = ConversationMessage(role="user", content=content)

        sanitized.append(message)

        if tool_uses:
            pending_tool_use_ids = {block.id for block in tool_uses}
            pending_tool_use_index = len(sanitized) - 1

    if pending_tool_use_ids and pending_tool_use_index is not None and pending_tool_use_index < len(sanitized):
        sanitized.pop(pending_tool_use_index)

    return sanitized


#把Python对象转化成Json格式返回
def serialize_content_block(block: ContentBlock) -> dict[str, Any]:
    """Convert a local content block into the provider wire format."""
    if isinstance(block, TextBlock):
        return {"type": "text", "text": block.text}

    if isinstance(block, ImageBlock):
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": block.media_type,
                "data": block.data,
            },
        }

    if isinstance(block, ToolUseBlock):
        return {
            "type": "tool_use",
            "id": block.id,
            "name": block.name,
            "input": block.input,
        }

    return {
        "type": "tool_result",
        "tool_use_id": block.tool_use_id,
        "content": block.content,
        "is_error": block.is_error,
    }


#将 Anthropic API 返回的原始消息对象，转换为系统内部统一使用的 ConversationMessage 格式。
def assistant_message_from_api(raw_message: Any) -> ConversationMessage:
    """Convert an Anthropic SDK message object into a conversation message."""
    content: list[ContentBlock] = []

    for raw_block in getattr(raw_message, "content", []):
        block_type = getattr(raw_block, "type", None)
        if block_type == "text":
            content.append(TextBlock(text=getattr(raw_block, "text", "")))
        elif block_type == "tool_use":
            content.append(
                ToolUseBlock(
                    id=getattr(raw_block, "id", f"toolu_{uuid4().hex}"),
                    name=getattr(raw_block, "name", ""),
                    input=dict(getattr(raw_block, "input", {}) or {}),
                )
            )

    return ConversationMessage(role="assistant", content=content)
