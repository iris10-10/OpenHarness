# 求职 Agent 用户手册

## 数据位置与隐私

求职画像、投递记录、模拟面试会话和 RAG 索引默认保存在本机 `~/.openharness/` 下。可以通过环境变量把数据放到指定目录：

```bash
set OPENHARNESS_CONFIG_DIR=D:\openharness\config
set OPENHARNESS_DATA_DIR=D:\openharness\data
set OPENHARNESS_JOBHUNT_DIR=D:\openharness\data\jobhunt
```

PowerShell 写法：

```powershell
$env:OPENHARNESS_CONFIG_DIR = "D:\openharness\config"
$env:OPENHARNESS_DATA_DIR = "D:\openharness\data"
$env:OPENHARNESS_JOBHUNT_DIR = "D:\openharness\data\jobhunt"
```

## 命令总览

| 命令 | 用途 |
|---|---|
| `oh job-hunt setup` | 配置目标城市、岗位、薪资和技能 |
| `oh job-hunt import --dir PATH` | 导入岗位、面经或本地知识 |
| `oh job-hunt search` | 从岗位 RAG 集合检索岗位 |
| `oh job-hunt match` | 计算简历与 JD 的匹配分、覆盖技能和差距 |
| `oh job-hunt resume parse FILE` | 解析简历并计算 ATS 分 |
| `oh job-hunt resume generate --jd FILE` | 生成针对 JD 的简历草稿 |
| `oh job-hunt interview --jd FILE` | 生成面试题与答题提示 |
| `oh job-hunt applications add` | 新增投递记录 |
| `oh job-hunt applications update ID` | 推进投递状态 |
| `oh job-hunt applications list` | 查看投递记录和状态统计 |
| `oh job-hunt status` | 查看画像和投递概览 |

所有数据处理优先走本地确定性模块。模型调用只发生在明确进入对话或配置了对应工具的场景。

## 岗位搜索

导入时建议按集合区分内容：

```bash
oh job-hunt import --dir ./jobs --collection jobs --category 后端
oh job-hunt import --dir ./interviews --collection interview --category 面经
oh job-hunt import --dir ./notes --collection knowledge --tags 求职,系统设计
```

搜索支持关键词、城市和最低月薪：

```bash
oh job-hunt search --query "FastAPI" --city 杭州 --salary-min 25
```

## 简历匹配

匹配分由技能、经验、学历、项目、软技能和其他维度组成。结果同时提供：

- 总分与推荐分层：冲刺、匹配、保底
- 已覆盖、部分覆盖和缺口技能
- 各维度得分与解释
- 岗位发布时间和来源信息

## 投递追踪

```bash
oh job-hunt applications add \
  --company "云帆科技" \
  --position "Python 后端工程师" \
  --channel 内推
oh job-hunt applications list
oh job-hunt applications update APP_ID --status 一面 --note "已约面"
```

状态只能向前推进，或者从活动状态标记为 `已拒绝`。终态记录不能继续编辑状态。

## 面试练习

Web UI 的“面试准备”页面支持完整练习闭环：

1. 填写公司、岗位、JD、简历文本、轮次、难度和题量。
2. 创建会话后一次回答一道题。
3. 提交回答后获得总分、分维度评分、缺失关键词和改进建议。
4. 完成或提前结束后，在历史练习中查看报告和逐题复盘。

相关接口为：

```text
POST /api/interview/practice
GET  /api/interview/sessions
GET  /api/interview/sessions/{id}
POST /api/interview/sessions/{id}/answer
POST /api/interview/sessions/{id}/finish
```

项目面试表达、演示顺序和高频追问见
[项目面试准备稿](interview-playbook.md)。

## Web UI

```bash
uv run --extra web oh job-hunt web --host 127.0.0.1 --port 8000
```

健康检查地址为 `GET /api/health`。数据 API 位于 `/api/profile`、`/api/jobs`、`/api/applications`、`/api/resumes`、`/api/interview` 和 `/api/rag`。

## 故障排查

先查看命令帮助：

```bash
oh job-hunt --help
oh job-hunt search --help
```

若岗位库为空，先运行 `import`。若 RAG 依赖不可用，使用 `--embedding-provider hash` 可以继续进行离线演示。
