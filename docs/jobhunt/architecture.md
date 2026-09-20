# 求职 Agent 架构说明

## 分层

```text
CLI / Web API
    |
    +-- Tool adapters
    |     +-- resume / job_match / interview / application_tracker
    |
    +-- Domain layer
    |     +-- parsing.py      简历和 JD 结构化解析
    |     +-- scoring.py      ATS 与岗位匹配评分
    |     +-- questions.py    面试题选择
    |     +-- storage.py      本地 JSON 持久化
    |
    +-- RAG layer
          +-- sources         本地导入与可选数据采集
          +-- ingestion       分块、元数据、增量状态
          +-- vectorstore      Chroma 或 simple fallback
          +-- retriever       向量召回、BM25、重排、上下文预算
```

## 数据流

岗位、面经和知识文档先经过 `DocumentIngestor` 分块，再由 `VectorStore` 写入集合。查询时 `Retriever` 组合向量相似度和 BM25 关键词分数，随后执行元数据加权和上下文预算控制。

求职画像、投递记录和面试会话使用 `JobHuntStore` 保存为 JSON。写入采用临时文件加原子替换，并使用 lock 文件保护并发读改写。

## 关键边界

- 工具层负责输入校验、上下文解析和 JSON 输出。
- 领域层不依赖 FastAPI、Typer 或模型 SDK，便于离线测试。
- RAG provider 和后端通过配置选择，并在不可用时保留 `simple` 和 `hash` 降级路径。
- Web API 不直接操作工具私有状态，使用统一的 store 与 schema。

## 配置

主要配置区块：

- `rag.enabled`
- `rag.persist_directory`
- `rag.embedding`
- `rag.retrieval`
- `job_hunt.data_directory`
- `job_hunt.matching`
- `job_hunt.reminder`

配置文件默认位于 `~/.openharness/settings.json`，环境变量可用于 CI 和临时隔离。

## 测试策略

- 单元测试验证解析、评分、存储和 RAG 组件边界。
- `tests/integration/` 验证跨层链路，使用 hash embedding 和 simple backend。
- `tests/e2e/` 验证用户实际使用的 Typer 命令链。
- 测试不依赖外部招聘网站、模型 API 或预下载模型。
