a.agent架构参考开源项目：pi-agent和hermess-agent
b.知识库搭建框架：lightRAG、llamaindex
c.数据库+高频query看板：elasticsearch、postgresql、redis

语言（待探讨）：前期采用next.js，后面改用java，python
流程：
1.写PRD(/write-prd /write-stories — 生成用户故事 /sprint — Sprint 规划)
2.设计前端网页，并给出前端的代码
3.定后端架构、写接口文档和数据库表结构设计（可以存在Apifox里的yaml）
4.给出技术架构
5.CI/CD和部署上线（supabase做数据库、vercel部署、微服务，k8s）

a.前端效果：流式输出(SSE)、有侧边拦（登陆、权限）
b.agent框架：langgraph和autoGen对比
c.harness框架：deepagent和opensandbox
d.技术栈：任务、消息队列(pydantic,SQLAichemy,celery,RabbitMQ)

模型网关与数据监控：LiteLLM Proxy、Langfuse 和 OpenTelemetry

核心功能：
1.关于记忆、skill、mcp等都支持开关，关闭后不再注入上下文
2.对于每个session能支持根据sessionId查找历史记录
3.界面简单明了，无需暴露给用户的开关都只在管理员账号中体现
4.支持通过langgraph搭建主智能体（助手栏）、autoGen框架搭建多智能体（暂不搭建，团队栏）
5.目前只需要有一个助手即可，情绪陪伴 + 兴趣规划助手（会在聊天中逐步理解用户意图推荐最合适的兴趣点，并且在兴趣执行的过程会将用户的问题沉淀成skill，类似于秘书）

要求：
1.先帮我产出产品PRD，然后设计前端，再定后端架构，最后给出MVP的开发计划
2.核心需求实现和非功能需求需要通过对话逐步完善，确认完善后将功能整理成一个完整的文档，最后再根据需求检索类似的开源项目，以成熟的开源项目为基，再在代码上做一些取舍
3.项目的文件夹结构要清晰

问题：
1.前端框架我打算用react，然后用next.js写后端，这样可以免费部署到vercel里，可行吗？
2.用postgresql作为数据库还有必要用elasticsearch吗？