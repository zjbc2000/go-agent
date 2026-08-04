# 苟蛋前端实现计划

## Summary

从零创建 `web/` Next.js 前端工程，目标是完成普通用户 MVP：

- 登录页
- 聊天页
- 统一“规划”页
- 最小个人设置页
- 深色默认主题，并支持手动切换浅色
- Typed Mock 优先，后续可替换为真实 API
- 暂不实现管理员后台、真实 Supabase、LangGraph 后端和沙箱执行

桌面端优先覆盖 1440px、1280px、1024px；移动端只保留基础响应式能力。

## Implementation Changes

### 1. 工程基础

- 使用 Next.js App Router、TypeScript、Tailwind CSS、shadcn/ui、Lucide。
- 使用 `pnpm` 作为包管理器。
- 引入：
  - `zustand`：聊天交互和本地 UI 状态
  - `@tanstack/react-query`：会话、规划文档等服务端状态
  - `next-themes`：主题切换与持久化
  - `react-hook-form` + `zod`：规划卡片内联编辑和校验
  - `react-markdown` + `remark-gfm`：助手消息渲染
  - `vitest`、Testing Library、Playwright：测试与视觉验收
- 设计 Token 统一落在 CSS 变量中，禁止组件内散落颜色值。
- 将 `goudan-logo-outline.svg` 作为唯一 Logo 来源；浅色使用黑色描边，深色通过主题颜色显示白色描边。

### 2. 页面与路由

- `/login`
  - Mock 登录
  - 登录失败状态
  - 登录加载状态
- `/chat`
  - 自动创建或恢复最近会话
- `/chat/[sessionId]`
  - 会话导航
  - 消息时间线
  - 流式输出
  - 断线重连
  - 错误恢复
  - 待确认计划卡片
- `/planning`
  - 统一规划页
  - 分类筛选：全部、记忆、兴趣、任务、Skill
  - 文档详情、编辑、删除
  - 历史版本查看与恢复
- `/settings`
  - 主题切换
  - 用户信息
  - 退出登录
  - 不包含管理员配置

### 3. 组件边界

按领域拆分：

- `components/layout`
  - AppShell
  - Sidebar
  - TopBar
  - PlanningNavigation
- `components/chat`
  - SessionList
  - SessionHeader
  - MessageList
  - MessageBubble
  - StreamingMessage
  - Composer
  - ConnectionBanner
  - ErrorMessage
  - EmptyConversation
- `components/planning`
  - PlanningList
  - PlanningFilters
  - PlanningDocument
  - VersionHistory
- `components/approval`
  - PlanDraftCard
  - InlinePlanEditor
  - ApprovalActions
- `components/brand`
  - GoudanLogo
- `components/ui`
  - shadcn/ui 基础组件

草稿编辑必须在 `PlanDraftCard` 内展开，不使用额外抽屉。

### 4. Typed Mock 与接口边界

前端不直接依赖数据库表，先定义领域类型和仓储接口：

```ts
type ChatEvent =
  | { type: "message-start"; messageId: string }
  | { type: "token"; messageId: string; text: string }
  | { type: "draft"; draft: PlanDraft }
  | { type: "done"; messageId: string }
  | { type: "error"; code: string; message: string };

interface ChatRepository {
  listSessions(): Promise<Session[]>;
  getMessages(sessionId: string): Promise<Message[]>;
  sendMessage(
    sessionId: string,
    content: string,
    requestId: string
  ): AsyncIterable<ChatEvent>;
  stopGeneration(messageId: string): Promise<void>;
}

interface PlanningRepository {
  listDocuments(filter?: PlanningFilter): Promise<PlanningDocument[]>;
  updateDocument(id: string, input: UpdateDocumentInput): Promise<PlanningDocument>;
  decideApproval(id: string, decision: ApprovalDecision): Promise<void>;
  listVersions(documentId: string): Promise<DocumentVersion[]>;
  restoreVersion(documentId: string, versionId: string): Promise<void>;
}
```

Mock 实现提供以下固定场景：

- 空会话
- 正常多轮对话
- token 流式输出
- 待确认兴趣计划
- 内联编辑并保存
- 驳回和重新生成
- SSE 断线后重连
- 模型错误
- token/费用超限

后续真实 API 只替换 Repository 实现，页面组件和状态模型不改。

### 5. 前端状态模型

聊天状态：

```text
idle
connecting
streaming
stopped
reconnecting
completed
error
```

计划草稿状态：

```text
pending_confirmation
editing
saving
confirmed
rejected
regenerating
```

关键规则：

- 用户消息先本地展示，再提交 Mock/API。
- 每次请求必须带 `requestId`，避免重连或重复点击造成重复消息。
- 未确认的草稿不能进入已确认规划列表。
- 保存失败时保留用户编辑内容，不自动丢失。
- 重连成功后按 `messageId` 去重。
- 用户滚动离开底部时不强制跳转，显示“回到最新消息”。

### 6. 主题与视觉实现

- 默认主题：深色。
- 用户手动切换后保存到本地存储。
- 主题变量：
  - 深色背景：`#0C0D0E`
  - 深色表面：`#141518`
  - 深色边框：`#2A2D33`
  - 深色主色：`#D9FF5A`
  - 浅色背景：`#F7F7F8`
  - 浅色表面：`#FFFFFF`
  - 浅色边框：`#E2E4E8`
  - 浅色主色：`#246BFD`
- 圆角以 6px 为主。
- 不使用渐变、装饰性插画或多色彩状态。
- 所有图标使用 Lucide，不使用 emoji。
- 每个图标按钮必须提供 Tooltip 和可访问名称。

### 7. 实现顺序

1. 初始化 Next.js 工程、主题、字体、Token 和 Logo。
2. 完成 AppShell、侧栏、顶栏和桌面三栏布局。
3. 完成会话列表、空会话和基础消息渲染。
4. 接入 Typed Mock，实现流式消息、停止生成和滚动策略。
5. 实现错误、断线重连和超限状态。
6. 实现待确认计划卡片和卡片内联编辑。
7. 实现确认、驳回、重新生成和保存失败恢复。
8. 实现统一“规划”页、文档 CRUD 和版本历史。
9. 实现登录 Mock 与最小个人设置。
10. 完成深色/浅色主题、桌面响应式和无障碍验收。
11. 增加真实 API Repository 的占位实现和切换配置。

## Test Plan

### 单元测试

- ChatEvent 流转和状态机。
- 重连消息去重。
- 草稿确认、驳回、重新生成状态。
- 文档版本恢复。
- 主题持久化。
- 请求幂等键生成。

### 组件测试

- 空会话。
- 流式消息。
- 失败消息和重试。
- 断线横幅。
- 内联编辑表单。
- 保存中、保存失败和已确认状态。
- 深色/浅色主题下的对比度与可读性。

### Playwright 验收

- 登录后进入最近会话。
- 新建会话并发送消息。
- 查看流式响应并停止生成。
- 生成计划草稿并直接在卡片内编辑。
- 确认、驳回和重新生成计划。
- 模拟断线并恢复。
- 进入规划页查看和恢复历史版本。
- 检查 1440px、1280px、1024px 和基础移动端布局。
- 截图保存浅色和深色基线，防止布局回归。

### 验收标准

- `pnpm dev` 可以启动。
- `pnpm test`、`pnpm test:e2e` 通过。
- 深色主题首次打开即生效。
- 普通用户完整走通“聊天 → 草稿 → 内联编辑 → 确认 → 规划页查看”。
- 未确认草稿不会进入正式规划。
- 管理员功能不会出现在普通用户界面。
- 浏览器控制台无未处理错误。
- 1440px 桌面布局与设计稿结构一致。

## Assumptions

- 本阶段只实现普通用户端，不实现管理员后台。
- Mock 登录只用于前端开发，不代表最终认证方案。
- 前端不直接访问 Supabase。
- SSE、LangGraph、模型供应商和沙箱均通过后续 Repository/API 接入。
- 深色主题为默认，浅色主题保留完整可用性。
- 现有旧版设计稿保留作历史参考，v2 浅色/深色桌面稿作为实现依据。
