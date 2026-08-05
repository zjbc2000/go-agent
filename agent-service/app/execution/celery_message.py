"""Celery task-protocol-v2 message builder for the ``sandbox.execute`` queue.

The publisher emits exactly what a standard Celery producer puts on the AMQP
wire for a task with one positional argument (the ``sandbox_run_id``): a
JSON-serialized ``(args, kwargs, embed)`` body with the task metadata carried in
the AMQP headers and persistent delivery. The sandbox Celery worker consumes
these with a regular ``sandbox.execute`` task, so the payload carries ONLY the
``sandbox_run_id`` (plan Global Constraint). Headers mirror Celery's
``TaskProducer.as_task_v2`` output for the fields the worker reads.
"""

from __future__ import annotations

import json
import socket
import uuid
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TaskMessage:
    """A fully-specified message for the publisher's RabbitMQ client."""

    body: bytes
    headers: dict[str, Any]
    properties: dict[str, str]
    content_type: str = "application/json"
    content_encoding: str = "utf-8"


def build_sandbox_execute_message(sandbox_run_id: str) -> TaskMessage:
    """Build the Celery v2 message that runs ``execute_sandbox_run(run_id)``."""
    task_id = str(uuid.uuid4())
    args = [sandbox_run_id]
    body = json.dumps(
        (args, {}, {"callbacks": None, "errbacks": None, "chain": None, "chord": None})
    ).encode("utf-8")
    headers: dict[str, Any] = {
        "lang": "py",
        "task": "sandbox.execute",
        "id": task_id,
        "shadow": None,
        "eta": None,
        "expires": None,
        "group": None,
        "group_index": None,
        "retries": 0,
        "timelimit": [None, None],
        "root_id": task_id,
        "parent_id": None,
        "argsrepr": repr(args),
        "kwargsrepr": "{}",
        "origin": f"publisher@{socket.gethostname()}",
        "ignore_result": False,
        "replaced_task_nesting": 0,
        "stamped_headers": None,
        "stamps": {},
    }
    return TaskMessage(
        body=body,
        headers=headers,
        properties={"correlation_id": task_id, "reply_to": ""},
    )
