"""Compatibility channel config models.

These models keep the synced channel adapters importable while the main
OpenHarness settings system evolves independently.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class _CompatModel(BaseModel):
    """Base model that tolerates adapter-specific extra fields."""

    model_config = ConfigDict(extra="allow")


class ProviderApiKeyConfig(_CompatModel):
    api_key: str = ""


class ProviderConfigs(_CompatModel):
    groq: ProviderApiKeyConfig = Field(default_factory=ProviderApiKeyConfig)


class BaseChannelConfig(_CompatModel):
    enabled: bool = False
    # Secure default: enabling a channel does not automatically trust every
    # remote sender. Operators must explicitly allow specific identities, or
    # intentionally set ["*"] when they want open access.
    allow_from: list[str] = Field(default_factory=list)


class TelegramConfig(BaseChannelConfig):
    token: str = ""
    chat_id: str | None = None
    proxy: str | None = None
    reply_to_message: bool = True
    bot_name: str = "ohmo"


class SlackConfig(BaseChannelConfig):
    bot_token: str = ""
    app_token: str = ""
    signing_secret: str = ""


class DiscordConfig(BaseChannelConfig):
    token: str = ""


class FeishuConfig(BaseChannelConfig):
    app_id: str = ""
    app_secret: str = ""
    encrypt_key: str = ""
    verification_token: str = ""
    # Group reply policy is enforced by ohmo gateway because managed-group
    # metadata lives outside the generic Feishu channel adapter.
    group_policy: str = "managed_or_mention"
    bot_open_id: str = ""
    bot_names: list[str] = Field(default_factory=lambda: ["ohmo", "openclaw", "openharness"])
    domain: str = "https://open.feishu.cn"  # use https://open.larksuite.com for Lark international


class DingTalkConfig(BaseChannelConfig):
    client_id: str = ""
    client_secret: str = ""
    robot_code: str = ""


class EmailConfig(BaseChannelConfig):
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    from_address: str = ""


class QQConfig(BaseChannelConfig):
    token: str = ""
    app_id: str = ""
    app_secret: str = ""


class MatrixConfig(BaseChannelConfig):
    homeserver: str = ""
    access_token: str = ""
    user_id: str = ""


class WhatsAppConfig(BaseChannelConfig):
    access_token: str = ""
    phone_number_id: str = ""
    verify_token: str = ""


class MochatConfig(BaseChannelConfig):
    endpoint: str = ""
    token: str = ""


class ChannelConfigs(_CompatModel):
    send_progress: bool = True
    send_tool_hints: bool = True
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    slack: SlackConfig = Field(default_factory=SlackConfig)
    discord: DiscordConfig = Field(default_factory=DiscordConfig)
    feishu: FeishuConfig = Field(default_factory=FeishuConfig)
    dingtalk: DingTalkConfig = Field(default_factory=DingTalkConfig)
    email: EmailConfig = Field(default_factory=EmailConfig)
    qq: QQConfig = Field(default_factory=QQConfig)
    matrix: MatrixConfig = Field(default_factory=MatrixConfig)
    whatsapp: WhatsAppConfig = Field(default_factory=WhatsAppConfig)
    mochat: MochatConfig = Field(default_factory=MochatConfig)


class Config(_CompatModel):
    channels: ChannelConfigs = Field(default_factory=ChannelConfigs)
    providers: ProviderConfigs = Field(default_factory=ProviderConfigs)


# ---------------------------------------------------------------------------
# RAG 知识检索配置（Phase 1）
# ---------------------------------------------------------------------------


class RagEmbeddingSettings(BaseModel):
    """Embedding provider configuration for the RAG pipeline."""

    provider: str = "auto"  # auto / openai / local / hash
    openai_model: str = "text-embedding-3-small"
    api_key: str = ""
    base_url: str = ""
    local_model: str = "BAAI/bge-m3"
    cache_enabled: bool = True
    cache_ttl_days: int = 30


class RagChunkingSettings(BaseModel):
    """Document chunking configuration for the ingestion pipeline."""

    chunk_tokens: int = 512
    overlap_tokens: int = 64
    strategy: str = "semantic"  # semantic / fixed


class RagRetrievalSettings(BaseModel):
    """Two-stage retrieval configuration (recall then rerank)."""

    vector_top_k: int = 20
    final_top_n: int = 5
    candidate_pool: int = 200
    hybrid_alpha: float = 0.7
    rerank_strategy: str = "auto"  # auto / heuristic / llm
    context_budget_ratio: float = 0.25
    max_context_tokens: int = 4000


class RagSettings(BaseModel):
    """RAG knowledge retrieval configuration.

    ``enabled`` gates prompt-time context injection; the ``rag_search`` tools
    stay available even when disabled so users can search explicitly.
    """

    enabled: bool = False
    persist_directory: str = ""  # 空 → ~/.openharness/chromadb
    default_collection: str = "knowledge"
    max_chunks_per_document: int = 400
    embedding: RagEmbeddingSettings = Field(default_factory=RagEmbeddingSettings)
    chunking: RagChunkingSettings = Field(default_factory=RagChunkingSettings)
    retrieval: RagRetrievalSettings = Field(default_factory=RagRetrievalSettings)
