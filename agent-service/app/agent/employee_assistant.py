"""Employee assistant behavior — 员工会话的人格、能力与上下文组装。

独立脚本集中员工会话的所有行为定义，方便后续修改：调整员工能 propose 的文档
类型、员工意图提示词、或员工上下文的注入方式，都只改这一个文件，graph.py
无需改动。员工会话与苟蛋会话（planning assistant）共享 HITL 草稿流，但员工
只 propose 记忆/兴趣/任务，绝不管理其他员工。
"""

from __future__ import annotations

from app.repositories.employees import Employee
from app.repositories.planning import Document

# 员工可 propose 的文档类型（不含 skill：skill 需 JSON manifest 编译，员工不参与）。
EMPLOYEE_DOC_TYPES: tuple[str, ...] = ("memory", "interest", "task")


def build_employee_intent_prompt(employee: Employee) -> str:
    """员工版意图提示词：身份 + 允许 propose 记忆/兴趣/任务；不含 manage_employee。

    员工是用户聘请的专职角色，判断用户消息意图时只关心能否帮助用户落地规划，
    绝不解雇/招聘/调整其他员工。
    """
    return (
        f'你是{employee.name}，职位是{employee.position}。请仅输出 JSON，判断用户最近一条消息的意图。'
        '意图取值："chitchat"（闲聊/请求帮助）、"plan_interest"（用户表达新兴趣或想尝试的方向）、'
        '"manage_document"（对已有兴趣/任务/记忆文档的调整或更新）。'
        '输出形如：{"intent":"...","needs_more_info":true,"draft":{"type":"memory|interest|task",'
        '"title":"...","body":"..."},"document_id":null}。'
        '当信息不足无法成稿时 needs_more_info 为 true 且 draft 为 null，此时可继续提问而非成稿。'
        '不要写入正式文档，不要输出 employee_action。'
    )


def build_employee_context_prompt(employee: Employee, docs: list[Document]) -> str:
    """员工会话系统提示：员工人格 + 用户已确认的记忆/兴趣/任务。

    让员工既以自身身份对话，又能"传入"用户已确认的规划（HITL 确认过的正式
    文档，草稿绝不注入）。员工人格不是苟蛋，所以不注入苟蛋 SOUL。
    """
    lines: list[str] = []
    for doc in docs[:5]:
        body = doc.body.replace("\n", " ").strip()
        lines.append(f"- {doc.title}：{body}")
    persona = (
        f"你是{employee.name}，职位是{employee.position}。请以这个身份与用户对话，"
        f"严格遵循你的职责描述，不要以其他身份回应。\n\n以下是你的职责描述：\n{employee.prompt}"
    )
    docs_text = "\n".join(lines) if lines else "（暂无已确认文档）"
    labels = {"memory": "记忆", "interest": "兴趣", "task": "任务"}
    draft_types = "/".join(labels[t] for t in EMPLOYEE_DOC_TYPES)
    prompt = (
        f"{persona}\n\n"
        "以下是与用户已确认的规划文档（仅注入已确认内容，未确认草稿绝不注入）：\n"
        f"{docs_text}\n\n"
        f"你可以基于这些规划帮用户落地，也可以在合适时机把用户表达的新方向整理成{draft_types}草稿供用户确认。"
    )
    return prompt[:6000]
