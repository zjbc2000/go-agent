"""Skill manifest and compiled execution-plan schemas (Pydantic v2).

A skill IS a planning document of category ``skill``; the JSON manifest is the
document's body. ``SkillManifest`` is the validated shape of that body.
``ExecutionPlan`` is the immutable compiled artifact bound to the exact
``document_version`` the plan hashed. ``ExecutionRequestResult`` is the
discriminated outcome of ``request_execution``: exactly one of ``approval`` or
``run`` is set.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.repositories.execution import ExecutionApproval, SandboxRun


class SkillStep(BaseModel):
    """One manifest step referencing a tool by id with its raw input."""

    id: str
    tool: str
    input: dict[str, Any]


class SkillManifest(BaseModel):
    """The validated JSON manifest stored as a skill document's body."""

    schema_version: int
    steps: list[SkillStep]
    allowed_tools: list[str] = Field(default_factory=list)


class CompiledStep(BaseModel):
    """A compiled step whose input has been validated and inputs substituted in."""

    id: str
    tool: str
    input: dict[str, Any]


class ExecutionPlan(BaseModel):
    """An immutable plan: the hashed compiled steps bound to one document version.

    ``hash`` is ``sha256(canonical_json({skill_version_id, steps}))``; it pins the
    plan to the exact ``document_version`` the skill was compiled against.
    """

    skill_version_id: UUID
    hash: str
    steps: list[CompiledStep]


@dataclass(frozen=True)
class ExecutionRequestResult:
    """The discriminated result of a skill execution request.

    Write/delete skills produce an ``approval``; read-only skills produce a
    ``run``. Exactly one is set.
    """

    approval: ExecutionApproval | None
    run: SandboxRun | None
