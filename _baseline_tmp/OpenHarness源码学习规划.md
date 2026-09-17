# OpenHarness 源码学习规划

> 目标：就业方向 AI Agent 后端，把本项目从"能跑"读到"能改能讲能建"。
> 项目本质：Claude Code 的开源 Python 复刻，一个生产级 Agent Harness。

---

## 一、项目定位与就业对齐

OpenHarness 覆盖了 AI Agent 后端岗位的全部核心技能点：

| JD 高频要求 | 本项目对应模块 |
|------------|--------------|
| 对话 / LLM API 对接 | `api/` · `engine/` |
| 工具调用（function calling） | `tools/` · `engine/query.py` |
| 任务编排 | `swarm/` · `coordinator/` · `tasks/` |
| 上下文管理 / 记忆 | `memory/` · `services/compact` |
| 权限治理 / 安全沙箱 | `permissions/` · `sandbox/` |
| 后端服务化 | `ohmo/gateway.py` |
| MCP 协议 | `mcp/` |

---

## 二、掌握程度三级标准

| 等级 | 名称 | 标准 | 面试对应 |
|------|------|------|---------|
| L1 | 能跑能讲 | 能跑通、能画调用链、能脱稿讲 Agent Loop | 过简历筛选 |
| L2 | 能改能说 | 写过新 Tool、接过 MCP、能讲设计权衡 | 拿到 offer |
| L3 | 能建能优 | 改成 HTTP 服务、能讲多 Agent 编排和成本控制 | 高薪 / SP |

---

## 三、阅读顺序与文件清单

### 阶段一 · 跑通与入口（3-5 天，目标 L1）

**目标**：跑通项目，理解一次对话的完整调用链，能脱稿画出 Agent Loop 流程图。

| 顺序 | 文件 | 重要性 | 掌握程度 | 阅读重点 |
|------|------|--------|---------|---------|
| 1 | `src/openharness/cli.py` | ★★★★★ | L1 | Typer 入口、参数解析、QueryEngine 装配过程 |
| 2 | `src/openharness/__main__.py` | ★★ | L1 | 仅 6 行，理解模块入口 |
| 3 | `src/openharness/api/client.py` | ★★★★ | L1 | `SupportsStreamingMessages` 抽象接口 |
| 4 | `src/openharness/api/provider.py` | ★★★ | L1 | provider 探测优先级、环境变量自动发现 |
| 5 | `src/openharness/prompts/system_prompt.py` | ★★★ | L1 | system prompt 如何动态拼装环境信息 |
| 6 | `src/openharness/engine/query_engine.py` | ★★★★★ | L1 | 只看 `submit_message()`，理解会话总管角色 |

**自测**：关掉代码，对着白墙讲一遍"一句用户输入怎么走完全程"。讲得出来就过。

**实操**：
```powershell
uv run oh -p "读一下 engine/query.py 的 run_query 函数，用大白话解释它每一步在做什么"
```

---

### 阶段二 · 攻克 Agent Loop（1-2 周，目标 L2，最关键）

**目标**：吃透工具调用循环的每一个决策点，写过至少一个新 Tool。

| 顺序 | 文件 | 重要性 | 掌握程度 | 阅读重点 |
|------|------|--------|---------|---------|
| 7 | `src/openharness/engine/query.py` | ★★★★★ | **L2** | **花一半时间在这里**。`run_query()` 的 while 循环 |
| 8 | `src/openharness/tools/base.py` | ★★★★★ | L2 | `BaseTool` 抽象（仅 80 行）：name/description/input_model/execute |
| 9 | `src/openharness/tools/bash_tool.py` | ★★★★ | L2 | 完整工具范本：子进程、超时、输出截断 |
| 10 | `src/openharness/engine/messages.py` | ★★★★ | L1 | ConversationMessage / TextBlock / ToolResultBlock 数据结构 |
| 11 | `src/openharness/engine/stream_events.py` | ★★★ | L1 | 流式事件类型定义 |
| 12 | `src/openharness/permissions/checker.py` | ★★★★ | L2 | 多级权限模式、read-only 放行 vs mutating 确认 |
| 12b | `src/openharness/permissions/modes.py` | ★★ | L1 | 权限模式枚举 |
| 13 | `src/openharness/hooks/executor.py` | ★★★ | L2 | pre_tool_use / post_tool_use 钩子机制 |
| 13b | `src/openharness/hooks/events.py` | ★★ | L1 | Hook 事件类型 |

**`query.py` 必须吃透的 8 个决策点**：
1. `while` 循环条件和 `max_turns` 限制
2. 自动压缩 `auto_compact_if_needed`（提前压缩）
3. 反应式压缩（`prompt too long` 错误后重试）
4. 单工具顺序执行 vs 多工具 `asyncio.gather` 并发
5. `return_exceptions=True` 为什么必须（防止单工具失败拖垮整轮）
6. 工具输出超长落盘 `_offload_tool_output_if_needed`
7. `_execute_tool_call` 完整链路：Hooks → 权限 → 执行 → carryover 记忆
8. 空消息丢弃逻辑

**改造产出**：仿照 `bash_tool.py` 写一个新 Tool（建议 `http_request_tool` 或 `sql_query_tool`），注册到 `ToolRegistry`，跑通模型自主调用。

**自测**：能对着面试官说"我给 OpenHarness 加了一个 XX 工具，过程中踩过 YY 坑"。

---

### 阶段三 · 上下文与扩展机制（1-2 周，目标 L2 巩固）

**目标**：理解 Agent 如何从"无状态"变"有状态、可扩展"。

| 顺序 | 文件 | 重要性 | 掌握程度 | 阅读重点 |
|------|------|--------|---------|---------|
| 14 | `src/openharness/services/token_estimation.py` | ★★★ | L1 | token 估算逻辑 |
| 15 | `tests/test_services/test_compact.py` | ★★★★ | L1 | 先读测试再读实现，看压缩行为契约 |未读
| 16 | `src/openharness/memory/memdir.py` | ★★★ | L2 | 文件式记忆存储 |
| 17 | `src/openharness/memory/search.py` | ★★★ | L1 | 相关性检索 |
| 18 | `src/openharness/memory/relevance.py` | ★★ | L1 | 记忆打分 |
| 19 | `src/openharness/mcp/client.py` | ★★★★ | L2 | MCP 客户端，连接外部工具服务器 |
| 20 | `src/openharness/mcp/config.py` | ★★★ | L1 | MCP server 配置解析 |
| 21 | `src/openharness/skills/loader.py` | ★★★ | L1 | Markdown skill 按需加载 |
| 22 | `src/openharness/skills/registry.py` | ★★ | L1 | skill 注册表 |
| 23 | `src/openharness/plugins/loader.py` | ★★★ | L1 | 插件生命周期 |
| 24 | `src/openharness/sandbox/docker_backend.py` | ★★★ | L1 | Docker 沙箱隔离执行 |

**改造产出**：接入一个真实 MCP server（如 GitHub MCP、文件系统 MCP），让 Agent 能操作外部系统。

---

### 阶段四 · 多智能体与后端化（2-3 周，目标 L3，对标就业）

**目标**：把 Agent 从 CLI 改造成可被业务调用的后端服务。

| 顺序 | 文件 | 重要性 | 掌握程度 | 阅读重点 |
|------|------|--------|---------|---------|
| 25 | `src/openharness/swarm/types.py` | ★★★ | L1 | 多 Agent 数据结构 |
| 26 | `src/openharness/swarm/subprocess_backend.py` | ★★★★ | L2 | 子进程隔离、worktree |
| 27 | `src/openharness/swarm/in_process.py` | ★★★ | L1 | 进程内模式 |
| 28 | `src/openharness/swarm/mailbox.py` | ★★★ | L2 | Agent 间消息通信 |
| 29 | `src/openharness/swarm/team_lifecycle.py` | ★★★ | L2 | team 生命周期管理 |
| 30 | `src/openharness/coordinator/coordinator_mode.py` | ★★★★ | L2 | 主 Agent 调度 worker 的编排模式 |
| 31 | `src/openharness/coordinator/agent_definitions.py` | ★★ | L1 | worker 角色定义 |
| 32 | `src/openharness/tasks/manager.py` | ★★★ | L2 | 后台任务生命周期 |
| 33 | `ohmo/gateway.py` | ★★★★★ | L2 | **最直接的参考**：把 Agent 包装成 gateway 服务 |
| 34 | `ohmo/gateway/router.py` | ★★★ | L2 | 路由设计 |
| 35 | `ohmo/gateway/service.py` | ★★★ | L2 | 服务层 |
| 36 | `src/openharness/engine/cost_tracker.py` | ★★★ | L2 | token 计数与成本跟踪 |

**改造产出（简历项目）**：用 FastAPI 包一层 HTTP 服务，暴露：
- `POST /chat`（SSE 流式，复用 `QueryEngine`）
- `POST /agents/spawn`（复用 `swarm`）
- `GET /agents/{id}/status`
- 复用 `memory/` 做会话持久化

---

## 四、文件重要性总览

### P0 · 必须精读（不理解这些等于没学）

| 文件 | 为什么重要 |
|------|-----------|
| `engine/query.py` | Agent Loop 主体，所有后端面试考点浓缩于此 |
| `engine/query_engine.py` | 会话总管，串起所有组件 |
| `tools/base.py` | 工具系统抽象，4 个要素定义一切 |
| `cli.py` | 装配清单，理解组件如何组装 |

### P1 · 强烈建议精读

| 文件 | 为什么重要 |
|------|-----------|
| `tools/bash_tool.py` | 完整工具范本 |
| `api/client.py` | 多 provider 适配的工程范例 |
| `permissions/checker.py` | 权限治理，面试高频 |
| `hooks/executor.py` | 可插拔架构 |
| `mcp/client.py` | MCP 协议，生态标准 |
| `ohmo/gateway.py` | 后端服务化参考 |

### P2 · 按需阅读

| 文件 | 什么时候读 |
|------|-----------|
| `memory/*` | 做会话持久化时 |
| `swarm/*` | 做多 Agent 编排时 |
| `coordinator/*` | 做任务调度时 |
| `sandbox/*` | 做安全隔离时 |
| `skills/*` `plugins/*` | 做扩展机制时 |

### P3 · 可跳过（与后端核心关系不大）

| 文件 | 原因 |
|------|------|
| `ui/*` | TUI 前端，与后端无关 |
| `themes/*` `keybindings/*` | 界面定制 |
| `vim/*` | vim 模式 |
| `voice/*` | 语音功能 |
| `autopilot-dashboard/*` | React 仪表盘 |
| `frontend/*` | React TUI |

---

## 五、测试文件导航

读实现卡住时，**先读对应测试**，测试是最好的行为说明书：

| 测试文件 | 对应实现 | 看什么 |
|---------|---------|--------|
| `tests/test_engine/test_query_engine.py` | `engine/query_engine.py` | 引擎行为契约 |
| `tests/test_tools/test_core_tools.py` | `tools/*` | 工具输入输出 |
| `tests/test_tools/test_bash_tool.py` | `tools/bash_tool.py` | 工具细节 |
| `tests/test_permissions/test_checker.py` | `permissions/checker.py` | 权限判定 |
| `tests/test_mcp/test_integration.py` | `mcp/client.py` | MCP 集成 |
| `tests/test_swarm/test_subprocess_backend.py` | `swarm/subprocess_backend.py` | 子进程隔离 |
| `tests/conftest.py` | 全局 | 测试 fixture |

---

## 六、六周时间规划

| 周次 | 阶段 | 产出 | 掌握层级 |
|------|------|------|---------|
| 第 1 周 | 跑通 + 入口精读 | 能脱稿讲调用链 | L1 |
| 第 2-3 周 | 攻克 Agent Loop | 写一个新 Tool | L2 |
| 第 4 周 | 上下文与扩展 | 接入一个 MCP server | L2 巩固 |
| 第 5-6 周 | 多智能体与后端化 | FastAPI 服务封装 | L3 |

---

## 七、学习方法

1. **带着断点读**：在 `query.py` 的 `while` 循环打断点，跑 `oh -p "读 README"`，观察 messages 增长
2. **先读测试再读实现**：测试的 `assert` 就是行为说明书
3. **每阶段一个小 PR**：形成可讲述的改造记录
4. **用 Agent 读 Agent**：`uv run oh -p "解释 run_query 函数"` 让它自己讲自己
5. **不追求读完所有源码**：精读 P0 的 4 个文件 + 动手改 2 处，远胜浏览全部 50 个文件

---

## 八、面试自测题

### L1 必答
- [ ] 画图：一句用户输入从 `cli.py` 到模型返回的完整调用链
- [ ] Agent Loop 的本质是什么？循环什么时候结束？
- [ ] `BaseTool` 抽象有哪 4 个要素？

### L2 必答
- [ ] 为什么单工具顺序执行、多工具用 `asyncio.gather` 并发？
- [ ] `return_exceptions=True` 为什么必须？不加会怎样？
- [ ] 工具输出超长时怎么处理？为什么要落盘？
- [ ] 权限检查在工具执行的哪一步？read-only 和 mutating 的区别？
- [ ] 你给项目加了什么工具？踩过什么坑？

### L3 加分
- [ ] 自动压缩和反应式压缩的区别？各自在什么场景触发？
- [ ] 多 Agent 之间怎么通信？worktree 隔离解决了什么问题？
- [ ] 如果要把这个 CLI 改成生产级 HTTP 服务，你会怎么设计架构？
- [ ] 成本跟踪怎么实现？生产环境为什么必须做？
