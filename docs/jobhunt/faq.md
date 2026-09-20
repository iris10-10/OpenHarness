# 求职 Agent FAQ

## 是否需要 API Key？

不需要。岗位解析、简历解析、匹配评分、投递追踪和 `hash` embedding 都可以离线运行。只有模型对话、在线 embedding 或外部数据采集才需要额外配置。

## RAG 导入后为什么搜不到内容？

确认三件事：

1. 导入和搜索使用了同一个配置目录。
2. 导入使用的 collection 与搜索使用的 collection 一致，岗位通常是 `jobs`。
3. 导入输出中的 `processed` 和 `chunks` 大于零。

可以通过 `OPENHARNESS_RAG_DATA_DIR` 固定向量库位置，避免不同 shell 使用不同数据目录。

## 为什么默认使用 `hash` embedding？

`hash` provider 不需要下载模型、不访问网络，适合首次体验、CI 和离线环境。它主要用于功能验证；需要更高语义质量时应配置在线或本地模型。

## 简历支持哪些格式？

CLI 的 `resume parse` 直接支持 Markdown 和纯文本。Web 上传接口可以接收文本文件，并对 PDF 进行可选文本提取；复杂排版 PDF 仍建议先导出为 Markdown 或纯文本复核。

## 匹配分很低怎么办？

查看结果中的 `missing_skills` 和各维度明细。优先补充 JD 中的必需技能证据、项目成果、年限和学历信息，再重新运行匹配。匹配器不会为了提高分数凭空补造经历。

## 投递状态为什么不能回退？

投递看板使用单向状态机，避免历史记录被覆盖。可以推进到更后阶段或标记为 `已拒绝`；如果同一公司重新投递，建议新建记录。

## Web UI 启动时报依赖缺失怎么办？

安装 Web extra：

```bash
uv sync --extra web
```

或：

```bash
pip install "openharness-ai[web]"
```

## 如何清理示例数据？

示例命令使用当前 OpenHarness 数据目录。查看目录后，删除其中的 `jobhunt/` 和 RAG 持久化目录即可。生产数据删除前请先备份。
