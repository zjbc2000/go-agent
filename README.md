# go-agent — 兴趣陪伴与规划助手

Go-Agent 是一个以聊天为入口的个人 AI 助手：通过 LangGraph 编排的"苟蛋"主助手，在对话中逐步识别用户的兴趣、任务与记忆，将其转化为**可确认的兴趣/任务/记忆文档**（Human-in-the-Loop），并通过沙箱执行声明式 Skill。前端为 Next.js 16 BFF，后端为 FastAPI + LangGraph，数据层为 Supabase（PostgreSQL + RLS + KMS 信封加密）。

## 版本

当前版本：**v0.1.0**（[agent-service/pyproject.toml](agent-service/pyproject.toml) 与 [web/package.json](web/package.json) 同步）。版本变更与 README 维护规则见 [`.claude/skills/update/SKILL.md`](.claude/skills/update/SKILL.md)。

## 架构总览

```
┌──────────────┐   SSE/相对路径   ┌───────────────┐   X-Internal-Token + JWT   ┌────────────────┐
│  浏览器 /web  │ ───────────────► │  Next.js BFF  │ ─────────────────────────► │ agent-service  │
│  Next.js 16   │                 │  /api/v1/*     │                            │  FastAPI       │
└──────────────┘                 └───────────────┘                            └───────┬────────┘
                                                                                    │
                                          ┌────────────────────┬────────────────────┼─────────────────────┐
                                          │                    │                    │                     │
                                     ┌────▼─────┐      ┌───────▼──────┐     ┌───────▼──────┐     ┌───────▼─────┐
                                     │ Supabase │      │ RabbitMQ     │     │ Sandbox      │     │ MCP sidecar │
                                     │ PG+RLS  │      │ + Celery     │     │ (隔离执行)    │     │ (受控)      │
                                     └──────────┘      └──────────────┘     └──────────────┘     └─────────────┘
```

- **`web/`** — Next.js 16 前端（App Router）。浏览器只访问同源 `/api/v1/*`；BFF 代理把请求转发到 agent-service，附加内部 token 与用户 JWT。
- **`agent-service/`** — FastAPI 后端：LangGraph 主助手（意图识别 → 上下文组装 → 回复生成 → 文档草稿）、加密持久化、审批/版本、沙箱工具代理、MCP 注册表。
- **`sandbox/`** — Celery 工作器：消费 outbox 事件，在隔离容器中执行 Skill 计划，通过一次性授权令牌调用工具代理。
- **`supabase/`** — 迁移与种子（身份、聊天、规划、执行、MCP 表 + RLS 策略）。
- **`infra/`** — 开发用 docker-compose（RabbitMQ 等）。
- **`scripts/`** — `start.sh`（一键启动前后端）。

## 快速开始

前置：`uv`、`pnpm`、Docker（Supabase + RabbitMQ）、`supabase` CLI。

```bash
# 1. 配置环境变量（从模板复制，填入 Supabase anon key 与模型 key）
cp .env.example .env

# 2. 启动本地 Supabase 与 RabbitMQ（必需容器）
supabase start
docker compose -f infra/compose/dev-compose.yml up -d rabbitmq

# 3. 应用数据库迁移 + 种子（含 e2e@goudan.app / password123 测试账号）
supabase db reset

# 4. 安装依赖
cd agent-service && uv sync
cd ../web && pnpm install

# 5. 一键启动前后端（agent-service :8010 + web :3000）
cd .. && scripts/start.sh
```

打开 <http://localhost:3000>，用 `e2e@goudan.app` / `password123` 登录。发一条具体需求（如"我每周六有空想学摄影，帮我规划"），苟蛋会流式回复并弹出**兴趣/任务草稿卡片**，确认后写入 `/planning`。

### 单独启动

```bash
# 仅后端
cd agent-service && uv run uvicorn app.main:app --port 8010

# 仅前端（需 agent-service 在跑，读 web/.env.local）
cd web && pnpm dev
```

## 配置

所有配置统一在仓库根 `.env`（严格 `KEY=value` 格式），由 `scripts/start.sh` 加载：后端变量传给 uvicorn，前端 `NEXT_PUBLIC_*` 同步到 `web/.env.local`。完整清单见 [`.env.example`](.env.example)。

| 变量 | 作用 |
|---|---|
| `NEXT_PUBLIC_SUPABASE_URL` / `ANON_KEY` | 前端 Supabase 连接 |
| `AGENT_SERVICE_URL` / `AGENT_INTERNAL_TOKEN` | BFF → 后端代理（端口/内部令牌） |
| `SUPABASE_URL` / `SUPABASE_ANON_KEY` | 后端 JWT 校验 + 角色加载 |
| `RABBITMQ_URL` | 沙箱 outbox 队列（聊天开发可选） |
| `AGENT_PROVIDER_BASE_URL` / `API_KEY` / `MODEL` | OpenAI 兼容模型（留空则用确定性假模型） |

## 模型接入

本项目通过 OpenAI 兼容接口接入模型（如 DeepSeek）。设置 `.env` 中 `AGENT_PROVIDER_*` 三项即走真实模型；留空时回退到确定性假模型（测试/开发用）。模型名需符合供应商规范（如 DeepSeek 要求小写 `deepseek-v4-flash`）。

## 开发

### 测试

```bash
cd agent-service && uv run pytest tests/          # 后端单元/集成（含 LangGraph、审批、RLS）
cd agent-service && uv run ruff check app tests   # 后端 lint
cd agent-service && uv run mypy app               # 后端类型检查
cd web && pnpm test                               # 前端单元
cd web && pnpm exec tsc --noEmit                  # 前端类型检查
cd web && pnpm lint                               # 前端 lint
cd web && pnpm test:e2e                           # Playwright E2E（真实栈）
```

### 核心机制

- **LangGraph 主助手**：4 节点图（`agent-service/app/agent/graph.py`）——意图识别、上下文组装（注入已确认文档）、流式回复、草稿持久化。草稿通过 `document.draft` SSE 事件推给前端 `PlanDraftCard`，确认/编辑/驳回走审批流。
- **审批与版本**：草稿生成即审批（15 分钟过期），确认后激活为正式文档并追加版本；所有写操作幂等且可审计。
- **沙箱与 Skill**：声明式 Skill（manifest + steps）经审批后由 Celery 调度到隔离容器执行；工具调用通过一次性授权令牌经工具代理校验。
- **加密与 RLS**：用户内容一律 KMS 信封加密存储，所有表 RLS 按 `auth.uid()` 隔离，服务无管理员明文读取路径。

## 贡献与更新

- 版本更新时按 [`.claude/skills/update/SKILL.md`](.claude/skills/update/SKILL.md) 的规则同步 README / 版本号 / 变更记录。
- 提交遵循 Conventional Commits（`feat:` / `fix:` / `docs:` / `chore:` 等）。

## 参考

- 产品 PRD 与用户故事：[`docs/prd/`](docs/prd/)
- 前端/后端设计：[`docs/design/`](docs/design/)
- 实现计划：[`docs/plan/`](docs/plan/)
- 开发说明与讨论：[`discussion.md`](discussion.md)

## 许可

暂未指定许可证。请在使用前确认。
