---
name: update
description: README 与版本更新规则。每当项目发生用户可见变更（功能、配置、命令、结构、依赖、版本发布）时，用它同步 README、版本号与变更记录，确保文档与代码一致。触发词："更新 README"、"写 changelog"、"升级版本"、"发布 vX"、"文档同步"。
---

# README 与版本更新规则

本项目采用 **Semantic Versioning** + **Conventional Commits**，README 与版本号必须在每次版本更新时保持同步。本 skill 定义强制规则，防止文档与代码漂移。

## 何时必须更新（硬性门槛）

以下任一发生，都必须同步更新 README（否则视为未完成）：

- **功能变更**：新增/删除/改变用户可见行为 → README + 变更记录
- **配置变更**：`.env` / `.env.example` / 环境变量增删或含义改变 → README 配置表 + 变更记录
- **命令变更**：启动/测试/构建命令、`scripts/` 变化 → README 快速开始 + 开发节
- **结构变更**：新增/删除模块、目录重命名 → README 架构总览
- **依赖变更**：新增核心依赖（如框架、模型提供方）→ README 技术栈
- **版本发布**：任何版本号 bump → README 版本节 + 变更记录 + 版本标签
- **修复**：仅 bug 修复 → 变更记录即可，README 视需要

**反例（不需要更新）**：纯内部重构且无行为/命令/结构变化；内部测试调整；注释与格式改动。

## 版本号规则（SemVer）

- **major（X.0.0）**：破坏性变更（如 API 不兼容、删除功能、RLS 策略收紧）
- **minor（1.X.0）**：向后兼容的新功能（如新路由、新配置项）
- **patch（1.2.X）**：bug 修复、非行为性内部改动

版本号**必须三处同步**：`agent-service/pyproject.toml`、`web/package.json`、README 版本节。每次发布验证三者一致。

## 提交信息规则（Conventional Commits）

```
<type>: <description>

[body]
```

- `feat:` → minor；`fix:` → patch；`!` 或 `BREAKING CHANGE:` → major
- `docs:` / `chore:` / `refactor:` 不 bump 版本，但文档变更仍需更新 README
- 每个用户可见变更在提交前先写好变更记录（不是事后补）

## 变更记录格式（Keep a Changelog）

每个版本一节，分类顺序固定：

```
## [Unreleased]
## [1.1.0] - 2026-08-06
### Added      # 新功能
### Changed    # 行为/配置变更（破坏性标 **BREAKING**
### Deprecated # 即将移除（注明替代路径）
### Removed    # 已移除（注明迁移路径）
### Fixed      # 修复
### Security   # 安全修复（不泄露利用细节）
```

- 用**现在时祈使句**（"Add X" 而非 "Added X"）
- 具体：含文件路径/函数名便于导航
- 面向用户：描述行为变化，而非实现细节
- `[Unreleased]` 始终保留在顶部

## README 结构基线（保持一致性）

版本化项目的 README 按此顺序，别随意重排：

1. **标题 + 单句描述**（项目是什么、一句话）
2. **版本**（当前版本 + 指向本 skill 的维护规则链接）
3. **架构总览**（目录/模块 + 依赖关系图）
4. **快速开始**（5 分钟内跑起来：前置 → 安装 → 启动 → 首屏验证）
5. **配置**（`.env` 配置表：变量 | 作用）
6. **开发**（测试/lint/类型检查命令）
7. **核心机制**（架构决策简述，如 LangGraph 编排、RLS、加密）
8. **贡献与更新**（指向本 skill）
9. **参考**（docs/ 链接）
10. **许可**

## 执行清单（每次版本更新走一遍）

```markdown
- [ ] 更新变更记录（[Unreleased] → 新版本节，分类 Added/Changed/Fixed…）
- [ ] bump 版本号（pyproject.toml、package.json 同步）
- [ ] README 同步（功能/配置/命令/结构/依赖有变则改对应节）
- [ ] 更新 README 版本节
- [ ] 验证：`git diff` 检查版本号三处一致、README 无过时描述
- [ ] 提交（Conventional Commits），必要时打 tag
```

## 校验要点

- **README 声明必须可验证**：命令能跑、路径存在、配置项在 `.env.example` 中有定义、版本号与代码一致
- **无硬编码漂移**：如提到模块数/路由数，先核对文件系统再写；宁可泛述
- **无失效链接**：README 中的相对链接（docs/、scripts/）必须存在
- **更新时机**：先改代码 → 写变更记录 → 改 README → 提交（同一 commit 或紧随其后，勿滞后）
