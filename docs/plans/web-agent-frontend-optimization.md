# OpenHarness Web Agent 前端优化方案

> **状态**：规划中  
> **版本**：v1.0  
> **日期**：2026-09-20  
> **目标**：让 OpenHarness Agent 拥有可在浏览器中直接使用的 Web 工作台，而不是只能通过终端 TUI 交互。

## 1. 背景与问题

当前项目已经具备 Agent 的核心能力：

- `QueryEngine` 负责多轮对话和 Agent loop；
- `tools` 提供文件读取、代码搜索、命令执行、求职分析、RAG 等能力；
- `permissions` 负责工具执行权限；
- `memory` 和 session backend 负责上下文与会话持久化；
- `frontend/terminal` 通过 React/Ink 提供终端交互界面；
- `jobhunt-web` 已经提供浏览器页面和一个对话页面；
- `autopilot-dashboard` 已经提供自动化任务看板。

但浏览器中的对话目前还不是完整的 Agent：

1. `jobhunt-web` 的 `/api/chat/send` 当前返回固定文本；
2. 浏览器聊天接口没有真正调用 `QueryEngine`；
3. 工具调用、工具结果、权限请求没有完整传给前端；
4. `/chat/stop` 目前不负责取消实际运行中的 Agent task；
5. 聊天历史使用单一文件，缺少独立会话和项目上下文；
6. `autopilot-dashboard` 是静态任务看板，不是 Agent 工作台；
7. 终端里的权限确认、问答交互和 Diff 展示无法直接迁移到浏览器。

因此，本次优化的重点不是重新编写一个聊天机器人，而是新增一层 Web 交互适配，让浏览器成为 OpenHarness Agent 的正式入口。

## 2. 产品目标

### 2.1 总目标

构建一个通用的 **OpenHarness Web Agent Workspace**：

```text
浏览器页面
  -> Web API / SSE
  -> OpenHarness Runtime
  -> QueryEngine
  -> 模型、工具、记忆、权限和任务系统
```

浏览器负责输入、展示和确认；Python 后端负责 Agent 执行、模型调用、工具调用和本地数据访问。

### 2.2 第一阶段必须支持

- 浏览器创建和切换会话；
- 输入消息并获取真实模型回复；
- 回复内容流式显示；
- Agent 调用工具时显示工具状态；
- 支持多轮上下文；
- 支持停止当前任务；
- 认证或模型配置错误时显示明确提示；
- 刷新页面后恢复当前会话；
- 不把 API Key 暴露给浏览器。

### 2.3 后续支持

- 文件和图片附件；
- 文件修改 Diff；
- 工具权限确认；
- `ask_user_question` 浏览器交互；
- Todo 和后台任务面板；
- 项目目录切换；
- Provider 和模型切换；
- Agent 与求职模块、Autopilot 模块联动；
- 多项目、多会话和会话搜索。

## 3. 前端定位

项目中的几个前端入口应保持清晰边界：

| 前端 | 定位 | 是否负责通用 Agent |
|---|---|---:|
| `frontend/terminal` | React/Ink 终端交互入口 | 是 |
| `web-agent`（规划新增） | 通用 OpenHarness Web Agent 工作台 | 是 |
| `jobhunt-web` | 求职领域业务页面 | 间接支持 |
| `autopilot-dashboard` | Autopilot 任务看板 | 否 |

建议新增独立的 `web-agent` 前端，而不是把通用 Agent 能力全部塞进 `jobhunt-web`。

`jobhunt-web` 可以继续保留“对话助手”页面，但它应当通过统一的 Agent API 调用通用 Agent；求职页面中的岗位、简历和投递操作可以作为 Agent 的业务上下文和工具入口。

## 4. 总体架构

```text
┌──────────────────────────────────────────────────────────────┐
│                        Browser                               │
│  Web Agent Workspace                                         │
│  ┌───────────────┬──────────────────────┬─────────────────┐  │
│  │ Sessions       │ Conversation          │ Activity        │  │
│  │ Projects       │ Streaming messages   │ Tools           │  │
│  │ Settings       │ Markdown / Diff      │ Tasks           │  │
│  │                 │ Composer             │ Permissions     │  │
│  └───────────────┴──────────────────────┴─────────────────┘  │
└──────────────────────────┬───────────────────────────────────┘
                           │ HTTP + SSE
┌──────────────────────────▼───────────────────────────────────┐
│ Web Agent API                                                │
│  Session routes · Message routes · Permission routes         │
│  SSE event adapter · Request validation · Error mapping       │
└──────────────────────────┬───────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────┐
│ WebSessionManager / WebAgentService                          │
│  Runtime lifecycle · session lock · cancellation             │
│  permission broker · snapshot persistence                    │
└──────────────────────────┬───────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────┐
│ Existing OpenHarness runtime                                 │
│  RuntimeBundle · QueryEngine · ToolRegistry                  │
│  API clients · permissions · memory · hooks · MCP             │
└──────────────────────────────────────────────────────────────┘
```

核心原则：

1. **复用 Agent 引擎**：浏览器不重新实现 Agent loop；
2. **复用工具注册器**：浏览器和终端使用同一套工具；
3. **复用配置体系**：Provider、模型和认证继续由 OpenHarness 管理；
4. **服务端持有密钥**：API Key、OAuth token 和本地文件路径不直接发送到前端；
5. **事件驱动交互**：模型输出、工具执行、权限请求统一转换为 Web 事件。

## 5. 后端设计

### 5.1 建议新增目录

```text
src/openharness/web/
├── __init__.py
├── app.py
├── events.py
├── sessions.py
└── service.py

src/openharness/web_api/
├── __init__.py
├── app.py
├── schemas.py
└── routes/
    ├── agent.py
    ├── sessions.py
    └── permissions.py
```

实际目录可以根据现有 FastAPI 组织方式合并，但应保持“Web 适配层”和“Agent 核心层”分离。

### 5.2 WebSessionManager

`WebSessionManager` 管理浏览器会话与运行时对象：

```python
session_id -> {
    bundle: RuntimeBundle,
    active_task: asyncio.Task | None,
    lock: asyncio.Lock,
    pending_requests: dict[str, Future],
}
```

职责：

- 创建 `RuntimeBundle`；
- 恢复历史消息和工具 metadata；
- 为同一个 session 防止并发提交；
- 保存 session snapshot；
- 取消正在执行的 task；
- 清理长期闲置的 runtime；
- 在服务重启后从磁盘恢复会话。

### 5.3 WebAgentService

Web Agent 服务调用现有运行时：

```python
async for event in bundle.engine.submit_message(prompt):
    yield convert_event(event)
```

需要复用：

- `build_runtime()`；
- `QueryEngine.submit_message()`；
- `create_default_tool_registry()`；
- `build_runtime_system_prompt()`；
- `SessionBackend`；
- 现有 API provider client；
- 现有权限检查器和工具系统。

禁止在 Web 路由中直接重新编写 Anthropic/OpenAI 请求逻辑。

### 5.4 权限桥接

终端模式使用回调等待用户输入，Web 模式需要将等待过程转换为浏览器事件：

```text
Agent 请求工具权限
       |
       v
WebPermissionBroker 创建 request_id
       |
       v
SSE: permission_required
       |
       v
浏览器展示允许 / 拒绝
       |
       v
POST /api/agent/requests/{request_id}/permission
       |
       v
恢复等待中的 Agent task
```

需要支持：

- 普通工具权限；
- 文件修改确认；
- 命令执行确认；
- `ask_user_question`；
- 请求超时；
- 用户拒绝后的正常 Agent 收尾。

## 6. API 设计

建议以 `/api/agent` 作为通用 Agent API 前缀。

### 6.1 会话接口

```text
GET    /api/agent/sessions
POST   /api/agent/sessions
GET    /api/agent/sessions/{session_id}
PATCH  /api/agent/sessions/{session_id}
DELETE /api/agent/sessions/{session_id}
```

会话字段建议：

```json
{
  "session_id": "session_abc123",
  "title": "分析当前项目",
  "cwd": "E:/project",
  "model": "claude-sonnet-4-6",
  "provider": "anthropic",
  "message_count": 8,
  "created_at": "2026-09-20T10:00:00Z",
  "updated_at": "2026-09-20T10:10:00Z"
}
```

### 6.2 消息接口

```text
GET  /api/agent/sessions/{session_id}/messages
POST /api/agent/sessions/{session_id}/messages
POST /api/agent/sessions/{session_id}/cancel
```

发送请求：

```json
{
  "message": "请分析当前项目的 Web 对话助手",
  "attachments": [],
  "cwd": null
}
```

### 6.3 权限和问答接口

```text
POST /api/agent/requests/{request_id}/permission
POST /api/agent/requests/{request_id}/answer
```

权限回复：

```json
{
  "allowed": true,
  "reply": "once"
}
```

### 6.4 SSE 事件

第一阶段统一使用 Server-Sent Events：

```text
event: user_message
data: {"message": {...}}

event: status
data: {"message": "正在分析项目"}

event: assistant_delta
data: {"message": "我先检查"}

event: tool_started
data: {
  "tool_name": "grep",
  "tool_input": {"pattern": "chat"}
}

event: tool_completed
data: {
  "tool_name": "grep",
  "output": "...",
  "is_error": false
}

event: permission_required
data: {
  "request_id": "req_123",
  "tool_name": "file_write",
  "reason": "即将修改项目文件"
}

event: assistant_complete
data: {"message": {...}}

event: done
data: {"session_id": "session_abc123"}
```

错误事件：

```text
event: error
data: {
  "code": "AUTH_REQUIRED",
  "message": "当前没有配置模型认证，请先执行 oh setup"
}
```

### 6.5 为什么第一阶段使用 SSE

- 现有 `jobhunt-web` 已经有 SSE 解析代码；
- 模型回复天然是服务端到客户端的流；
- 比 WebSocket 更容易和现有 FastAPI 路由结合；
- 发送消息、权限回复和停止请求仍可使用普通 HTTP；
- 后续需要双向实时协作时再升级 WebSocket。

## 7. 会话持久化

不再把所有浏览器对话共用一个 `chat_history.json`。建议使用独立会话：

```text
~/.openharness/data/web-sessions/
├── index.json
├── session-abc123.json
└── session-def456.json
```

每个 session 至少保存：

```json
{
  "session_id": "session_abc123",
  "title": "分析项目",
  "cwd": "E:/project",
  "model": "claude-sonnet-4-6",
  "system_prompt": "...",
  "messages": [],
  "tool_metadata": {},
  "usage": {},
  "created_at": "...",
  "updated_at": "..."
}
```

要求：

- 使用原子写入和文件锁；
- 限制单个会话消息数量；
- 保存前执行 `sanitize_conversation_messages()`；
- 不持久化 API Key；
- 不把临时权限请求写入历史消息；
- 服务重启后可以恢复正常会话；
- 同一 session 的消息按顺序写入。

## 8. Web Agent 页面设计

### 8.1 页面布局

```text
┌─────────────────────────────────────────────────────────────┐
│ OpenHarness Agent     当前项目  当前模型  运行状态  设置     │
├────────────────┬─────────────────────────────┬────────────┤
│ 会话列表        │ 对话内容                    │ 活动面板     │
│                │                             │            │
│ + 新建会话      │ 用户消息                    │ 当前工具     │
│ 历史会话        │ Agent Markdown 回复         │ 工具结果     │
│ 项目会话        │ 流式输出                    │ Todo         │
│                │ Diff / 权限请求              │ 运行信息     │
├────────────────┴─────────────────────────────┴────────────┤
│ 输入消息、附件                                发送 / 停止 │
└─────────────────────────────────────────────────────────────┘
```

### 8.2 必要组件

```text
web-agent/src/components/
├── chat/
│   ├── ConversationView.tsx
│   ├── MessageBubble.tsx
│   ├── Composer.tsx
│   └── MarkdownRenderer.tsx
├── sessions/
│   ├── SessionList.tsx
│   └── NewSessionButton.tsx
├── tools/
│   ├── ToolCallCard.tsx
│   ├── ToolResultPanel.tsx
│   └── ActivityTimeline.tsx
├── permissions/
│   ├── PermissionRequest.tsx
│   └── DiffApproval.tsx
└── workspace/
    ├── StatusBar.tsx
    ├── TodoPanel.tsx
    └── ProjectSelector.tsx
```

### 8.3 交互要求

- 回复流式更新时保持滚动位置稳定；
- Agent 运行时允许停止，不允许重复发送；
- 允许用户复制消息、代码块和工具输出；
- 工具调用默认折叠，点击后查看输入和结果；
- 文件修改使用 Diff 展示；
- 权限请求必须清晰说明工具和影响范围；
- 请求失败后保留用户输入，允许重试；
- 页面刷新后恢复 session；
- 移动端至少可以进行普通对话；
- 长文本、表格、代码块不能溢出页面。

## 9. `jobhunt-web` 的处理策略

当前 `jobhunt-web` 已有：

```text
jobhunt-web/src/pages/ChatPage.tsx
jobhunt-web/src/components/chat/
jobhunt-web/src/api/chat.ts
```

建议按以下方式处理：

### 第一阶段

保留现有页面视觉结构，先把固定回复替换为真实 Agent API。

需要完成：

- `streamChat()` 改为解析完整 Agent SSE 事件；
- `ChatPanel` 使用函数式状态更新；
- 显示工具调用和错误状态；
- 增加停止按钮；
- 使用 `session_id`；
- 后端接入真实 `QueryEngine`。

### 第二阶段

将通用 Agent 工作台抽成共享组件：

```text
shared-agent-ui/
├── ConversationView
├── Composer
├── ToolCallCard
├── PermissionRequest
└── SessionList
```

`jobhunt-web` 只负责注入求职业务上下文和业务快捷入口。

### 第三阶段

如果通用 Agent 页面需求增加，再创建独立 `web-agent` 项目，避免求职页面变成过度复杂的全能页面。

## 10. 权限策略

### 10.1 默认自动执行

- 文件读取；
- 文件搜索；
- 代码检索；
- 项目状态查询；
- 用户画像查询；
- 岗位和知识库查询；
- 只读分析工具。

### 10.2 默认需要确认

- 文件写入和编辑；
- Shell 命令；
- 修改用户画像；
- 写入投递记录；
- 创建后台任务；
- 修改配置；
- 启动或停止外部服务。

### 10.3 Web 默认禁止自动执行

- 自动投递简历；
- 自动发送 HR 或招聘平台消息；
- 对外发布内容；
- 上传用户隐私资料；
- 删除整个项目或数据目录。

所有外部副作用都必须由用户明确确认。

## 11. 分阶段实施计划

### Phase 1：真实 Web 对话闭环

目标：浏览器可以完成一次真实 Agent 对话。

任务：

- 新增 Web Agent API；
- 创建 `WebSessionManager`；
- 接入 `build_runtime()` 和 `QueryEngine`；
- 实现 SSE `assistant_delta`、`assistant_complete`、`done`；
- 实现会话创建和恢复；
- 实现认证错误展示；
- 替换当前固定文本回复。

验收：

```text
浏览器打开 Agent 页面
-> 输入问题
-> 后端真实调用模型
-> 页面显示流式回复
-> 继续追问时上下文仍然存在
```

### Phase 2：工具执行可视化

任务：

- `tool_started`；
- `tool_completed`；
- `status`；
- `error`；
- 工具输入和输出折叠展示；
- 工具执行耗时和错误状态；
- Todo 面板。

验收：

```text
输入“读取 README 并总结”
-> 页面显示 file_read / grep 等工具活动
-> 显示最终总结
```

### Phase 3：权限和中断

任务：

- Web 权限 broker；
- 文件修改 Diff；
- 命令执行确认；
- `ask_user_question`；
- 真正取消运行中的 asyncio task；
- session busy 锁；
- 超时和错误恢复。

验收：

```text
输入“修改某个文件”
-> 浏览器显示 Diff
-> 用户允许后才执行
-> 用户拒绝后 Agent 正常解释并结束
```

### Phase 4：工作区能力

任务：

- 项目目录选择；
- 当前模型和 provider 展示；
- 模型切换；
- 新建、重命名、删除和搜索会话；
- 附件上传；
- 文件和图片消息；
- 多项目隔离。

### Phase 5：业务能力整合

任务：

- 接入求职画像和岗位工具；
- 从岗位详情页带入 Agent；
- 从简历页面发起分析；
- 展示 Autopilot 任务；
- Agent 与任务看板联动；
- 共享会话和权限组件。

## 12. 测试方案

### 12.1 后端单元测试

- session 创建、恢复和删除；
- 同一 session 并发请求被拒绝；
- SSE 事件格式；
- Agent event 到 Web event 的转换；
- 权限 request 创建和回复；
- cancel 可以取消当前 task；
- 无认证时返回可读错误；
- session snapshot 不包含密钥。

### 12.2 后端集成测试

使用 fake API client 验证：

- 普通文本回复；
- 流式文本回复；
- 工具调用；
- 工具错误；
- 权限确认；
- 用户问题；
- 多轮上下文；
- 中断后重新发送。

### 12.3 前端测试

- SSE 事件解析；
- delta 拼接；
- assistant 消息不会重复；
- 工具卡片状态更新；
- 权限按钮行为；
- 停止按钮行为；
- session 切换；
- 页面刷新恢复。

### 12.4 端到端测试

至少覆盖：

```text
创建会话
发送普通问题
读取项目文件
触发工具权限
批准文件修改
拒绝命令执行
停止长任务
刷新页面恢复会话
```

## 13. 安全与运行要求

- API Key 只存在 Python 进程和本地配置中；
- Web API 默认绑定 `127.0.0.1`；
- 远程部署必须增加认证和 HTTPS；
- 必须限制允许访问的工作目录；
- 文件上传需要限制大小和类型；
- SSE 输出不能泄露完整环境变量；
- 工具错误需要脱敏；
- 生产模式关闭详细异常堆栈；
- 所有写入操作都保留用户确认边界。

## 14. 推荐启动方式

开发阶段：

```bash
uv run --extra web oh job-hunt web --host 127.0.0.1 --port 8000
cd web-agent
npm run dev
```

如果第一阶段直接复用 `jobhunt-web`：

```bash
uv run --extra web oh job-hunt web --host 127.0.0.1 --port 8000
cd jobhunt-web
npm run dev
```

浏览器访问：

```text
http://127.0.0.1:5174/chat
```

生产阶段：

1. 构建前端静态文件；
2. 由 FastAPI 托管静态资源；
3. 使用单一域名提供页面和 `/api/agent`；
4. 配置认证、HTTPS 和访问目录限制。

## 15. 最终验收标准

当以下流程可以稳定完成时，说明浏览器页面已经具备 Agent 能力：

```text
1. 浏览器创建一个新会话；
2. 输入“分析当前项目的对话助手实现”；
3. Agent 流式输出；
4. Agent 自动调用文件搜索和读取工具；
5. 页面显示工具执行状态；
6. Agent 给出分析结果；
7. 用户继续追问；
8. Agent 记住上一轮上下文；
9. 用户要求修改文件；
10. 页面展示 Diff 并请求确认；
11. 用户批准后完成修改；
12. 刷新浏览器后会话和消息仍然存在。
```

最终目标不是让浏览器“看起来像聊天”，而是让浏览器成为 OpenHarness Agent 的正式工作入口：

```text
终端 TUI       -> 适合本地快速操作
Web Agent      -> 适合可视化工作流和项目协作
Job Hunt Web   -> 适合求职业务页面
Autopilot      -> 适合任务看板和自动化状态
IM Gateway     -> 适合外部消息渠道
```

