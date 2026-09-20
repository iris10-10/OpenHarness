# 求职 Agent 扩展开发指南

## 增加一个工具

1. 在 `src/openharness/tools/` 新建模块。
2. 定义继承 `JobHuntToolBase` 的工具类。
3. 用 Pydantic `BaseModel` 定义输入模型。
4. 返回 `ToolResult`，成功输出使用 `json_output`，失败使用 `error_result`。
5. 在 `tests/test_tools/` 添加单元测试。

工具应把解析、评分等业务逻辑放到 `src/openharness/jobhunt/`，保持工具层薄。

## 增加一个数据源

本地文件导入优先复用 `LocalKnowledgeImporter` 和 `DocumentIngestor`。远程数据源应：

- 继承 `BaseScraper`
- 遵守 robots.txt、超时、重试和速率限制
- 返回统一的 `SourceRecord`
- 使用 mock HTTP 响应测试解析，不在测试中访问真实站点

## 增加一个 RAG 集合

集合名应在调用处显式传递，并为每个文档提供稳定的 `id`、`source`、`title` 和领域元数据。查询需要考虑：

- metadata filter 是否能在向量后端执行
- chunk 是否适合上下文预算
- embedding provider 变更后的重建策略

不要直接依赖 ChromaDB API；通过 `VectorStore` 接口访问集合。

## 增加 Web API

在 `src/openharness/jobhunt/api/routes/` 添加路由模块，在 `api/app.py` 注册 router，并在 `api/schemas.py` 添加输入模型。路由应该：

- 使用 Pydantic 校验输入
- 对不存在的资源返回明确的 404
- 复用 `get_store`、`load_jobs` 等持久化 helper
- 为关键成功和错误路径添加 API 测试

## 发布前检查

```bash
pytest tests/ --cov=openharness
pytest tests/integration/
pytest tests/e2e/
uv build
```

发布包必须可以在没有 ChromaDB、sentence-transformers 和模型 API Key 的环境中导入基础模块；RAG 的可选能力应提供可读的降级提示。
