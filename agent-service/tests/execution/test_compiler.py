"""Compiler tests: allowed tools, immutable input substitution, per-tool schemas.

``compile_skill`` turns a manifest (parsed from a skill document's body) into an
immutable ``ExecutionPlan`` bound to a specific document version. Substitution
never mutates the source manifest; the plan hash is deterministic over the
canonical JSON of the compiled steps.
"""

import uuid

import pytest
from app.core.errors import ApiError
from app.skills.compiler import compile_skill
from app.skills.schemas import ExecutionPlan

VERSION_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


def test_compiler_rejects_unregistered_tool():
    manifest = {"schema_version": 1, "steps": [{"id": "s1", "tool": "shell.exec", "input": {}}]}
    with pytest.raises(ApiError, match="SKILL_INVALID"):
        compile_skill(manifest, {}, version_id=VERSION_ID)


def test_compiler_accepts_document_tools():
    doc_id = "00000000-0000-0000-0000-000000000010"
    manifest = {
        "schema_version": 1,
        "steps": [
            {"id": "s1", "tool": "document.read", "input": {"document_id": doc_id}},
            {"id": "s2", "tool": "document.create", "input": {"type": "task", "title": "t", "body": "b"}},
            {"id": "s3", "tool": "document.update", "input": {"document_id": doc_id, "title": "t2"}},
            {"id": "s4", "tool": "document.delete", "input": {"document_id": doc_id}},
        ],
    }
    plan = compile_skill(manifest, {}, version_id=VERSION_ID)
    assert isinstance(plan, ExecutionPlan)
    assert len(plan.steps) == 4
    assert plan.skill_version_id == VERSION_ID
    assert plan.hash


def test_compiler_substitutes_inputs_without_mutating_source():
    manifest = {
        "schema_version": 1,
        "steps": [
            {
                "id": "s1",
                "tool": "document.create",
                "input": {"type": "task", "title": "{{title}}", "body": "hi {{who}}"},
            }
        ],
    }
    plan = compile_skill(manifest, {"title": "x", "who": "you"}, version_id=VERSION_ID)
    assert plan.steps[0].input["title"] == "x"
    assert plan.steps[0].input["body"] == "hi you"
    # The source manifest is never mutated.
    assert manifest["steps"][0]["input"]["title"] == "{{title}}"
    assert manifest["steps"][0]["input"]["body"] == "hi {{who}}"


def test_compiler_rejects_unknown_placeholder():
    manifest = {
        "schema_version": 1,
        "steps": [{"id": "s1", "tool": "document.create", "input": {"type": "task", "title": "{{nope}}", "body": "b"}}],
    }
    with pytest.raises(ApiError, match="SKILL_INVALID"):
        compile_skill(manifest, {"title": "x"}, version_id=VERSION_ID)


def test_compiler_validates_per_tool_input_schema():
    manifest = {
        "schema_version": 1,
        "steps": [{"id": "s1", "tool": "document.read", "input": {"document_id": "not-a-uuid"}}],
    }
    with pytest.raises(ApiError, match="SKILL_INVALID"):
        compile_skill(manifest, {}, version_id=VERSION_ID)


def test_compiler_rejects_unsupported_schema_version():
    manifest = {
        "schema_version": 2,
        "steps": [
            {"id": "s1", "tool": "document.read", "input": {"document_id": "00000000-0000-0000-0000-000000000010"}}
        ],
    }
    with pytest.raises(ApiError, match="SKILL_INVALID"):
        compile_skill(manifest, {}, version_id=VERSION_ID)


def test_compiler_hash_is_deterministic_over_canonical_json():
    manifest = {
        "schema_version": 1,
        "steps": [{"id": "s1", "tool": "document.read", "input": {"document_id": "{{document_id}}"}}],
    }
    inputs = {"document_id": "00000000-0000-0000-0000-000000000010"}
    first = compile_skill(manifest, inputs, version_id=VERSION_ID)
    second = compile_skill(manifest, inputs, version_id=VERSION_ID)
    assert first.hash == second.hash
    # Different inputs substitute into the plan, so the hash changes.
    other = compile_skill(manifest, {"document_id": "00000000-0000-0000-0000-000000000011"}, version_id=VERSION_ID)
    assert first.hash != other.hash
    # A different version binding also changes the hash.
    rebound = compile_skill(
        manifest, inputs, version_id=uuid.UUID("00000000-0000-0000-0000-000000000002")
    )
    assert first.hash != rebound.hash
