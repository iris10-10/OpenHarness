# OpenHarness 求职 Agent 改造计划 · 分阶段执行版

> **来源**: 拆分自根目录《项目改造计划.md》（v1.0，2026-09-16 修订版）。
> **拆分目的**: 按开发阶段拆为独立文件，每个阶段文件自包含——背景事实、功能与文件清单、详细设计、验证与测试、完成检查清单全部内联。开发某一阶段时**只需读取对应的 phase 文件**，无需阅读其他阶段文档。

## 如何使用这套计划

1. **开发顺序**: Phase 0 → 1 → 2 → 3 → 4 → 5 → 6 → 7（P0 主线为 0 → 1 → 2 → 5，Phase 3/4/6/7 可穿插并行）。
2. **开始某阶段前**: 打开对应 `phase-N-*.md`，先读文件开头的元信息与「背景速览」，再按文件清单施工。阶段文件已内联各自所需的关键架构事实，不需要回头读本 README。
3. **每阶段收尾**: 必须通过该文件「验证与测试」章节的全部检查项（DoD 清单全部勾选）才可进入下一阶段。
4. **跨阶段公共信息**: 总体架构、开发规范、风险应对集中在本文件，供全局查阅。

## 阶段总览

| 阶段 | 文件 | 名称 | 前置条件 | 优先级 | 预计工时 | 验证方式速览 |
|------|------|------|---------|--------|---------|------------|
| Phase 0 | `phase-0-init.md` | 项目初始化与基础设施搭建 | 无 | P0 | 0.5 天 | 依赖安装 + 包导入 + `oh --dry-run` |
| Phase 1 | `phase-1-rag.md` | RAG 知识检索基础设施 | Phase 0 | P0 | 3 天 | 单测 + 验收脚本 + dry-run 工具列表 |
| Phase 2 | `phase-2-jobhunt-tools.md` | 求职专用工具集 | Phase 1 | P0 | 7 天 | 单测 + dry-run 工具注册 + 数据存取验证 |
| Phase 3 | `phase-3-prompts-skills.md` | 提示词改造与技能定义 | Phase 1、2 | P1 | 3 天 | 对话行为测试 + Skill 触发测试 + prompt 断点检查 |
| Phase 4 | `phase-4-datasources.md` | 数据源与信息采集 | Phase 1 | P1 | 4 天 | 单测（mock）+ 导入验收 + 去重/增量验证 |
| Phase 5 | `phase-5-cli-ux.md` | CLI 命令与用户体验 | Phase 2、4 | P0 | 2 天 | `--help` + 配置加载 + 权限行为 + setup 引导 |
| Phase 6 | `phase-6-web-ui.md` | Web UI 界面开发 | Phase 1–5 | P1 | 10 天* | 构建 + 启动 + 页面功能 + API 测试 |
| Phase 7 | `phase-7-test-release.md` | 集成测试与上线准备 | 全部 | P1 | 3 天 | 全量 pytest + 集成/E2E + 打包安装 |

\* Phase 6 的 10 天为团队产能口径，个人执行建议按 2~3 倍估时，或按该文件中的优先级裁剪 P2 页面。

**总计**: 约 32.5 人天。

## 总体架构

### 系统架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                        用户交互层                                │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────────┐  │
│  │ CLI      │  │ TUI      │  │ Web UI   │  │ IM 渠道       │  │
│  │ (oh)     │  │ (React)  │  │ (新增)   │  │ (复用 channels)│ │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └───────┬───────┘  │
└───────┼──────────────┼─────────────┼─────────────────┼──────────┘
        │              │             │                 │
┌───────▼──────────────▼─────────────▼─────────────────▼──────────┐
│                       Agent 引擎层 (复用)                        │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │  QueryEngine · Agent Loop · 工具注册器 · 权限系统       │    │
│  │  多 LLM Provider · 记忆系统 · Hooks 机制 · MCP 集成    │    │
│  └─────────────────────────────────────────────────────────┘    │
└───────┬──────────────────────────────┬──────────────────────────┘
        │                              │
┌───────▼──────────────────────────────▼──────────────────────────┐
│                      求职领域扩展层 (新增)                       │
│  ┌─────────────────────┐  ┌─────────────────────────────────┐  │
│  │  RAG 知识检索层     │  │  求职工具层                     │  │
│  │  · 向量数据库       │  │  · 简历管理 · 岗位匹配          │  │
│  │  · Embedding 服务   │  │  · 面试准备 · 薪资调研          │  │
│  │  · 文档摄入管道     │  │  · 公司背调 · 投递追踪          │  │
│  │  · 检索排序器       │  │  · 技能差距 · 求职信            │  │
│  └─────────────────────┘  └─────────────────────────────────┘  │
│  ┌─────────────────────┐  ┌─────────────────────────────────┐  │
│  │  提示词层           │  │  技能层 (Skills)                │  │
│  │  · 求职系统提示词   │  │  · 简历优化 · 面试模拟          │  │
│  │  · RAG 上下文注入   │  │  · 求职策略 · 系统设计          │  │
│  │  · 用户画像上下文   │  │  · 八股题库 · 薪资谈判          │  │
│  └─────────────────────┘  └─────────────────────────────────┘  │
└───────┬──────────────────────────────┬──────────────────────────┘
        │                              │
┌───────▼──────────────────────────────▼──────────────────────────┐
│                        数据源层                                  │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐  │
│  │ 招聘平台│ │ 面经社区│ │ 公司信息│ │ 算法题库│ │ 本地知识│  │
│  └─────────┘ └─────────┘ └─────────┘ └─────────┘ └─────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

### 核心数据流（以岗位匹配为例）

```
用户输入 "找北京 Java 3年岗位，匹配我的简历"
        │
        ▼
  QueryEngine 接收消息
        │
        ├─► 系统提示词组装（注入求职身份 + RAG 上下文 + 用户画像）
        │
        ├─► RAG 检索（jobs collection + resumes collection）
        │
        ├─► 工具调用链：
        │     1. jd_parse （如果有新 JD）
        │     2. job_match （简历 ↔ 岗位双向匹配）
        │     3. skill_gap_analyze （技能差距分析）
        │
        ▼
  LLM 生成匹配报告
        │
        ▼
  输出渲染（表格 + 进度条 + 建议）
```

## 与实际代码对齐的关键架构事实（必读）

以下事实已逐一与当前代码库核实（2026-09-16），各阶段文件的「背景速览」会按需内联，此处集中备查：

1. **Skills 物理布局**: bundled 技能加载器 `src/openharness/skills/bundled/__init__.py` 的 `get_bundled_skills()` 只扫描 `content/` 目录下的平铺单 `.md` 文件（`glob("*.md")`），**不支持** `<skill>/SKILL.md` 子目录与 `references/` 结构（该布局仅被用户级/项目级技能目录的 `load_skills_from_dirs()` 支持）。frontmatter 实际支持字段：`name / description / user-invocable / disable-model-invocation / model / argument-hint`，**无 `triggers` 字段**——技能触发依赖系统提示 "Available Skills" 列表对 `description` 的语义匹配。
2. **权限模型**: `PermissionChecker`（`src/openharness/permissions/checker.py`）基于 `permission.mode`（DEFAULT / PLAN / FULL_AUTO）+ `allowed_tools` / `denied_tools` 白黑名单 + `path_rules` 路径规则 + `denied_commands` 命令黑名单，外加每个工具自身的 `is_read_only()` 布尔值。DEFAULT 模式下只读工具直接执行、变更类工具需用户批准。**不存在** read-only / file_write / network 三级权限类别配置。
3. **动态上下文**: `build_runtime_system_prompt()`（`src/openharness/prompts/context.py`）内部按条件动态组装 `sections` 列表，段数不固定（随 fast_mode、Issue/PR/Repo 上下文、项目记忆等条件增减）。求职扩展应在 `sections` 末尾追加，而非寻找固定编号槽位。
4. **CLI 框架**: `src/openharness/cli.py` 使用 Typer，现有子命令组（mcp / plugin / auth / provider / config / cron / autopilot）均通过 `app.add_typer()` 挂载，`job-hunt` 照抄此模式。
5. **依赖现状**: 核心依赖已含 `httpx`、`pydantic`；**未引入** fastapi、uvicorn、chromadb、sentence-transformers、torch、pypdf、beautifulsoup4。重依赖一律放可选分组（optional extras），不进核心 `[project.dependencies]`。Python 版本要求 ≥ 3.10，使用 uv 管理。
6. **无 HTTP 服务端**: `src/openharness/api/` 目录只包含 LLM provider API 客户端（Anthropic/OpenAI/Codex/Copilot 等），不存在任何 HTTP 服务端实现；现有 `stream-json` 是 stdout 输出，Web UI 的 SSE 层为净新增。`QueryEngine` 为依赖注入设计（api_client / tool_registry / permission_checker），可被 FastAPI 服务独立调用。

## 公共开发约定

### 改造原则

| 原则 | 说明 |
|------|------|
| **最小侵入** | 最大化复用现有架构，核心引擎（Agent Loop、工具注册、权限检查等）不做破坏性修改 |
| **分层扩展** | 仅在知识层（RAG）、工具层（求职专用工具）、提示词层（领域上下文）三个层面扩展 |
| **渐进交付** | 按阶段迭代，每阶段可独立运行验证，降低集成风险 |
| **本地优先** | 敏感数据（简历、投递记录）本地存储优先，减少外部依赖和隐私风险 |

### 代码规范

| 类别 | 规范 |
|------|------|
| Python | 遵循 PEP 8，使用 ruff + black 格式化，类型注解完整 |
| TypeScript | 遵循 ESLint + Prettier，严格模式 |
| 命名 | 文件名 snake_case，类名 PascalCase，函数/变量 camelCase |
| 注释 | 公共 API 必须有 docstring，复杂逻辑加行内注释 |
| 导入 | 标准库 → 第三方 → 本地，分组排序 |

### 目录规范

```
src/openharness/
├── rag/                    # RAG 模块（新增，独立）
│   ├── __init__.py
│   ├── vectorstore.py
│   ├── embedding.py
│   ├── ingestion.py
│   ├── retriever.py
│   └── sources/            # 数据源采集
├── jobhunt/                # 求职模块（新增，独立）
│   ├── __init__.py
│   ├── cli.py
│   ├── setup.py
│   ├── output.py
│   └── api/                # Web API
├── tools/                  # 工具（新增文件，不修改原有）
│   ├── rag_search_tool.py
│   ├── resume_tool.py
│   └── ...
├── skills/bundled/content/ # 技能（新增平铺 .md 文件，不修改原有代码）
│   ├── resume_optimize.md
│   └── ...
├── prompts/                # 提示词（最小修改）
│   ├── system_prompt.py    # 仅扩展，不改写原有逻辑
│   └── context.py          # 新增注入点
├── config/                 # 配置（扩展新增段）
│   ├── schema.py
│   └── settings.py
└── ...                     # 其余核心模块尽量不修改
```

### 依赖策略

- 全部新增依赖通过**可选依赖分组**提供：`rag`（chromadb、rank-bm25、pypdf、beautifulsoup4、lxml）、`rag-local`（sentence-transformers、torch）、`web`（fastapi、uvicorn、python-multipart）。
- 核心 `[project.dependencies]` 不新增任何包，保持 OpenHarness 轻量安装与 CI 速度。
- `httpx` 与 `pydantic` 已是现有核心依赖，直接使用。

### Git 工作流

| 分支 | 用途 |
|------|------|
| `main` | 主分支，稳定版本 |
| `feature/phase-x-xxx` | 功能开发分支，按阶段 + 功能命名 |
| `fix/xxx` | Bug 修复分支 |
| `release/vx.y.z` | 发布分支 |

**提交规范**: Conventional Commits（`feat:` / `fix:` / `docs:` / `refactor:` / `test:` / `chore:`）。

### 测试规范

- 每个新模块必须有对应的单元测试
- 核心算法必须有边界情况测试
- 集成测试覆盖关键用户路径
- 测试数据使用 fixtures，不依赖外部服务
- 优先使用 mock 隔离外部依赖
- 现有 tests/ 组织为 `test_*` 顶层目录风格（test_api、test_engine 等），`tests/integration/` 与 `tests/e2e/` 为 Phase 7 新建

### 配置约定

- 所有可配置项必须有默认值
- 支持环境变量覆盖（`OPENHARNESS_` 前缀；现有优先级：CLI > 环境变量 > 配置文件 > 默认值）
- 敏感信息（API Key、Cookie 等）不写入代码
- 配置变更需向后兼容，旧配置不报错

## 风险与应对

### 技术风险

| 风险 | 影响 | 概率 | 应对措施 |
|------|------|------|---------|
| ChromaDB 在大数据量下性能下降 | 检索变慢 | 中 | 提前做性能测试，预留 Qdrant 作为备选；合理设置 collection 分区 |
| 本地 Embedding 模型内存占用高 | 低配机器无法运行 | 中 | 提供小模型选项（all-MiniLM），默认使用在线 API，本地模型为可选 |
| RAG 检索质量不达预期 | 回答不准确 | 中 | 优化分块策略、增加重排序、引入查询改写、持续迭代 |
| 爬虫反爬升级导致采集失败 | 数据源中断 | 高 | 爬虫仅作为补充，核心数据依赖本地导入；提供 API 接入方式 |
| Web UI 与 CLI 功能不一致 | 用户体验割裂 | 中 | 统一调用后端 API 层，CLI 和 Web UI 共享同一套业务逻辑 |

### 合规风险

| 风险 | 影响 | 应对措施 |
|------|------|---------|
| 爬虫数据合规性 | 法律风险 | 遵守 robots.txt，仅采集公开信息，提供开关默认关闭，用户自行承担风险 |
| 简历隐私数据泄露 | 安全风险 | 本地优先存储，敏感字段脱敏，不将隐私数据发送到外部 API（可选本地 Embedding） |
| 求职建议免责 | 误导风险 | 明确声明"仅供参考，不构成求职决策依据"，鼓励多方验证 |

### 项目风险

| 风险 | 影响 | 应对措施 |
|------|------|---------|
| 工作量超出预期 | 延期 | 按优先级迭代，P0 功能先交付，P1/P2 可延后 |
| 代码质量下降 | 维护困难 | 严格遵循开发规范，Code Review，单元测试覆盖 |
| 与上游 OpenHarness 升级冲突 | 合并困难 | 最小侵入原则，新增代码独立成模块，尽量不修改核心代码 |
