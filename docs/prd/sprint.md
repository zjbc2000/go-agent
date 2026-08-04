# MVP Sprint 规划

## 规划假设

- 每个 Sprint 约 2 周，1 名前端、1 名后端/Agent、1 名全栈或基础设施协作者。
- 先实现单用户可用闭环，再补齐管理员、沙箱和监控；数据表从第一天带 `user_id` 和权限策略。
- 没有提醒和长期后台任务时不强制引入 Celery/RabbitMQ；沙箱异步执行保留任务接口。

## Sprint 1：基础工程、认证和数据基线

**目标**：用户可以登录、进入聊天页、创建会话；数据库和前后端边界可供后续功能扩展。

### 交付任务

- 初始化目录：`web`、`agent-service`、`sandbox`、`infra`、`docs`。
- Next.js/React 项目、基础布局、路由守卫和响应式聊天壳。
- Supabase Auth 账号密码登录、退出和会话续期。
- Supabase PostgreSQL 初始迁移：User profile、Session、Message、Document、DocumentVersion、AuditLog、UsageRecord。
- RLS/服务端权限校验：用户只能读取自己的数据，管理员仅可读取允许的元数据。
- 会话新建、按 `session_id` 加载历史、消息持久化 API。
- Agent service 健康检查、统一错误格式、OpenAI-compatible provider adapter 接口。
- 聊天页接入 SSE mock/最小模型响应，验证增量渲染、完成和错误状态。
- CI 基础检查：格式化、类型检查、单元测试和数据库迁移检查。

### 验收

- 新账号可以登录并进入聊天页。
- 用户可以创建会话、发送消息、看到流式响应并刷新后恢复历史。
- 另一用户无法访问前一用户的数据。
- 管理员能看到会话元数据，但不能看到消息正文。
- `npm`/Python 测试和迁移检查在 CI 中通过。

## Sprint 2：LangGraph 主助手、记忆和文档 CRUD

**目标**：形成“聊天理解 + 已确认文档上下文”的可用对话闭环。

- LangGraph 主图：意图识别、上下文组装、回复生成、文档草稿节点。
- 记忆、兴趣、任务文档 API 和前端列表/详情/编辑/删除。
- 用户确认后写入文档；软删除和版本记录。
- 管理员开关记忆、Skill、MCP 上下文注入。
- SSE 事件协议：token、status、draft、approval_required、error、done。
- 基础 token、费用、延迟记录。

## Sprint 3：兴趣拆解和 Human-in-the-Loop

**目标**：完成“兴趣 → 可执行步骤 → 用户确认 → 后续反馈调整”。

- 结构化兴趣/任务草稿 schema 和状态机。
- LangGraph interrupt/resume，支持按会话恢复待确认节点。
- 前端草稿卡片：确认、编辑确认、驳回、重新生成。
- 待确认列表和离开页面后的恢复。
- 后续聊天读取任务反馈并生成新版本草稿。
- 幂等确认接口和端到端测试。

## Sprint 4：Skill、MCP 和受限沙箱

**目标**：用户能安全确认并运行助手自身数据的 CRUD Skill。

- Skill 文档、权限声明、版本和启停管理。
- MCP 注册/启停/配置模型；默认关闭未配置能力。
- 独立沙箱运行时和任务状态查询。
- CPU、内存、磁盘、超时、并发和网络策略。
- 阻断内网、回环和云元数据地址；支持域名白名单。
- 写入/删除执行前的确认卡片。
- SandboxRun 和审计日志；失败、超时、拒绝和部分变更测试。

## Sprint 5：管理员后台、模型路由和可观测性

**目标**：完成 MVP 的运维控制、成本控制和发布门禁。

- 管理员账号新增、禁用和密码重置。
- LiteLLM Proxy 或等价 provider adapter 的供应商路由。
- 模型、上下文长度、默认参数和启停配置。
- 全局/用户/供应商 token 与费用限额，超限阻断。
- Langfuse/OpenTelemetry 脱敏接入。
- 管理员指标页：调用量、token、费用、延迟、错误、沙箱运行和审计事件。
- 安全检查：密钥扫描、日志脱敏、RLS 检查、沙箱策略检查。

## Sprint 6：全量验收与上线

**目标**：验证 PRD 中的完整 MVP 验收标准，并部署到 Vercel + Supabase 及独立 Agent/Sandbox 服务。

- 登录、聊天流、会话恢复、文档版本、HITL、Skill 确认和沙箱运行 E2E。
- SSE 断线重连、重复请求幂等、模型超限和供应商故障演练。
- Supabase 备份恢复演练和数据删除验证。
- 监控告警、运行手册、环境变量清单和回滚方案。
- 生产环境最小权限、CORS、限流和管理员账号初始化。
- 由产品负责人按 [PRD](./prd.md) 第 10 节逐项签收。

## Sprint 1 Backlog 优先级

| 优先级 | 任务 | 依赖 | 估算 |
| --- | --- | --- | --- |
| P0 | 项目脚手架与环境配置 | 无 | 0.5 人日 |
| P0 | Supabase Auth 登录与会话续期 | 项目脚手架 | 1.5 人日 |
| P0 | 初始数据库迁移与 RLS | Auth 用户 ID | 2 人日 |
| P0 | Next.js 聊天布局与路由保护 | Auth | 1.5 人日 |
| P0 | Session/Message API | 数据库迁移 | 1.5 人日 |
| P0 | SSE 事件协议与 mock 流 | Session/Message API | 1.5 人日 |
| P1 | Agent service provider adapter | 环境配置 | 1 人日 |
| P1 | 管理员元数据查询最小 API | RLS 与审计模型 | 1 人日 |
| P1 | CI、测试模板和迁移检查 | 项目脚手架 | 1 人日 |

## 发布门禁

- P0 用户故事全部通过自动化测试。
- 所有写入接口具备权限校验和幂等策略。
- 管理端无法读取用户消息正文的测试通过。
- 关闭上下文能力后，Agent 工具调用和 prompt 组装均无对应数据。
- 沙箱策略、资源限制和审计测试通过后，才允许启用 Skill。
- 费用/token 记录与供应商响应经过抽样校验。
- 生产环境有备份、错误监控和回滚步骤。

