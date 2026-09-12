"""Skill manifest compilation into immutable execution plans.

``compile_skill`` validates a manifest (parsed from a skill document's body),
substitutes ``{{name}}`` request inputs into a COPIED plan (never mutating the
source manifest), validates each step against its per-tool input schema, and
returns a plan whose hash pins it to the exact ``document_version``.

Allowed tools: the document.* core tools are always allowed; a manifest may
declare extra MCP tools in ``allowed_tools`` (Task 4 gates them against the live
registry). Any other tool is rejected as ``SKILL_INVALID``.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from uuid import UUID

from app.core.errors import ApiError
from app.skills.schemas import CompiledStep, ExecutionPlan, SkillManifest, SkillStep

# Core tools every skill may use. MCP tools must be declared in the manifest's
# allowed_tools; Task 4 gates them against the live registry.
ALWAYS_ALLOWED_TOOLS = frozenset({"document.read", "document.create", "document.update", "document.delete"})

# Steps that write or delete state require an execution approval.
WRITE_TOOLS = frozenset({"document.create", "document.update", "document.delete"})

_VALID_CATEGORIES = frozenset({"memory", "interest", "task", "skill"})

_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_.]+)\s*\}\}")

_MANIFEST_VERSION = 1


def canonical_json(payload: dict[str, Any]) -> str:
    """Canonical JSON for hashing: sorted keys, compact separators."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def compile_skill(manifest: dict[str, Any], inputs: dict[str, Any], *, version_id: UUID) -> ExecutionPlan:
    """Compile a manifest (as a raw dict) into an immutable plan for ``version_id``."""
    parsed = _parse_manifest(manifest)
    steps = [_compile_step(step, inputs, parsed.allowed_tools) for step in parsed.steps]
    payload = {
        "skill_version_id": str(version_id),
        "steps": [step.model_dump(mode="json") for step in steps],
    }
    plan_hash = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return ExecutionPlan(skill_version_id=version_id, hash=plan_hash, steps=steps)


def _parse_manifest(data: dict[str, Any]) -> SkillManifest:
    if not isinstance(data, dict):
        raise ApiError("SKILL_INVALID", "Skill manifest must be an object.", False)
    try:
        manifest = SkillManifest.model_validate(data)
    except Exception:
        raise ApiError("SKILL_INVALID", "Skill manifest is invalid.", False) from None
    if manifest.schema_version != _MANIFEST_VERSION:
        raise ApiError("SKILL_INVALID", "Unsupported skill schema_version.", False)
    return manifest


def _compile_step(step: SkillStep, inputs: dict[str, Any], allowed_tools: list[str]) -> CompiledStep:
    allowed = ALWAYS_ALLOWED_TOOLS.union(allowed_tools)
    if step.tool not in allowed:
        raise ApiError("SKILL_INVALID", f"Tool {step.tool!r} is not allowed by this skill.", False)
    substituted = _substitute(step.input, inputs)
    _validate_step_input(step.tool, substituted)
    return CompiledStep(id=step.id, tool=step.tool, input=substituted)


def _substitute(value: Any, inputs: dict[str, Any]) -> Any:
    """Copy ``value`` replacing ``{{name}}`` placeholders; never mutates the source."""
    if isinstance(value, str):
        return _substitute_string(value, inputs)
    if isinstance(value, list):
        return [_substitute(item, inputs) for item in value]
    if isinstance(value, dict):
        return {key: _substitute(item, inputs) for key, item in value.items()}
    return value


def _substitute_string(template: str, inputs: dict[str, Any]) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in inputs:
            raise ApiError("SKILL_INVALID", "Unknown input placeholder {{name}}.", False)
        return str(inputs[name])

    return _PLACEHOLDER_RE.sub(replace, template)


def _validate_step_input(tool: str, input: dict[str, Any]) -> None:
    """Validate a compiled step's input against its per-tool schema."""
    if tool == "document.read":
        _require_uuid(input, "document_id")
    elif tool == "document.create":
        _require_str(input, "title")
        _require_str(input, "body")
        category = input.get("type")
        if not isinstance(category, str) or category not in _VALID_CATEGORIES:
            raise ApiError(
                "SKILL_INVALID",
                "document.create type must be one of memory, interest, task, skill.",
                False,
            )
    elif tool == "document.update":
        _require_uuid(input, "document_id")
        if "title" in input and not isinstance(input["title"], str):
            raise ApiError("SKILL_INVALID", "document.update title must be a string.", False)
        if "body" in input and not isinstance(input["body"], str):
            raise ApiError("SKILL_INVALID", "document.update body must be a string.", False)
    elif tool == "document.delete":
        _require_uuid(input, "document_id")
    # MCP tools accept a permissive input schema for now; Task 4 adds real schemas.


def _require_str(input: dict[str, Any], key: str) -> None:
    value = input.get(key)
    if not isinstance(value, str):
        raise ApiError("SKILL_INVALID", f"{key} must be a string.", False)


def _require_uuid(input: dict[str, Any], key: str) -> None:
    value = input.get(key)
    if not isinstance(value, str):
        raise ApiError("SKILL_INVALID", f"{key} must be a UUID string.", False)
    try:
        UUID(value)
    except ValueError:
        raise ApiError("SKILL_INVALID", f"{key} must be a valid UUID.", False) from None
