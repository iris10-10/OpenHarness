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


# ---------------------------------------------------------------------------
# 数据源与采集配置（Phase 4）
# ---------------------------------------------------------------------------


class ScrapingSourceSettings(BaseModel):
    """Per-source crawling controls and optional authentication hints."""

    enabled: bool = False
    base_url: str = ""
    cookie: str = ""
    request_interval_min: float = 1.0
    request_interval_max: float = 3.0


class ScrapingSettings(BaseModel):
    """Recruiting/interview/company data-source scraping configuration."""

    enabled: bool = False
    respect_robots_txt: bool = True
    request_delay_min: float = 2.0
    request_delay_max: float = 5.0
    max_requests_per_minute: int = 10
    max_retries: int = 3
    backoff_base_seconds: float = 0.5
    timeout_seconds: float = 30.0
    proxy: str = ""
    proxy_http: str = ""
    proxy_https: str = ""
    user_agents: list[str] = Field(
        default_factory=lambda: [
            (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 "
                "(KHTML, like Gecko) Version/17.4 Safari/605.1.15"
            ),
        ]
    )
    boss: ScrapingSourceSettings = Field(
        default_factory=lambda: ScrapingSourceSettings(base_url="https://www.zhipin.com")
    )
    lagou: ScrapingSourceSettings = Field(
        default_factory=lambda: ScrapingSourceSettings(base_url="https://www.lagou.com")
    )
    nowcoder: ScrapingSourceSettings = Field(
        default_factory=lambda: ScrapingSourceSettings(base_url="https://www.nowcoder.com")
    )
    leetcode: ScrapingSourceSettings = Field(
        default_factory=lambda: ScrapingSourceSettings(base_url="https://leetcode.cn")
    )
    tianyancha: ScrapingSourceSettings = Field(
        default_factory=lambda: ScrapingSourceSettings(base_url="https://www.tianyancha.com")
    )
    maimai: ScrapingSourceSettings = Field(
        default_factory=lambda: ScrapingSourceSettings(base_url="https://maimai.cn")
    )
    github: ScrapingSourceSettings = Field(
        default_factory=lambda: ScrapingSourceSettings(enabled=True, base_url="https://api.github.com")
    )


# ---------------------------------------------------------------------------
# 求职领域配置（Phase 2）
# ---------------------------------------------------------------------------


class JobHuntMatchingSettings(BaseModel):
    """加权匹配与推荐阈值配置（权重会自动归一化）。"""

    weight_skills: float = 0.35
    weight_experience: float = 0.20
    weight_education: float = 0.10
    weight_projects: float = 0.15
    weight_soft_skills: float = 0.10
    weight_other: float = 0.10
    freshness_days: int = 30  # 岗位发布时间在此天数内享受新鲜度加成
    freshness_boost: float = 0.10
    match_threshold: float = 65.0  # “匹配”建议的最低总分
    safety_threshold: float = 80.0  # “保底”建议的最低总分
    top_k: int = 10  # 岗位检索默认返回条数


class JobHuntReminderSettings(BaseModel):
    """投递跟进提醒配置。"""

    follow_up_days: int = 7  # 超过 N 天未跟进则提醒
    stale_days: int = 14  # 超过 N 天无状态变化视为停滞


class JobHuntSettings(BaseModel):
    """求职领域配置：存储位置、语言、用户偏好与匹配参数。

    ``expected_salary_min`` / ``expected_salary_max`` 单位为 K（月薪），与
    配置文件中 ``20-40`` 的惯用写法一致。
    """

    data_directory: str = ""  # 空 → <data_dir>/jobhunt
    language: str = "zh"
    target_cities: list[str] = Field(default_factory=list)
    target_positions: list[str] = Field(default_factory=list)
    expected_salary_min: int | None = None
    expected_salary_max: int | None = None
    years_of_experience: float | None = None
    default_company_types: list[str] = Field(default_factory=list)
    matching: JobHuntMatchingSettings = Field(default_factory=JobHuntMatchingSettings)
    reminder: JobHuntReminderSettings = Field(default_factory=JobHuntReminderSettings)
