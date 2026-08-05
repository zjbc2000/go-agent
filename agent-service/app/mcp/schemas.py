"""MCP registry schemas (Pydantic v2).

``McpManifest`` is the validated JSON manifest a user submits to register a
third-party MCP server. It requires an ``oci://`` image pinned by
``@sha256:<64-hex>`` digest (else MCP_DIGEST_REQUIRED), provenance + sbom
(non-empty), and a list of declared tools with their schemas and mutability.

``McpToolSchema`` represents a single tool declared in the manifest.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


class McpToolSchema(BaseModel):
    """One tool declared in an MCP manifest."""

    tool_id: str
    mutable: bool = False
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)


class McpManifest(BaseModel):
    """Validated manifest for registering an MCP server.

    ``image`` must be ``oci://...@sha256:<64-hex-characters>``.
    ``provenance`` and ``sbom`` must be non-empty (admission gate).
    """

    schema_version: int
    name: str
    image: str
    provenance: str
    sbom: str
    tools: list[McpToolSchema] = Field(default_factory=list)

    @field_validator("image")
    @classmethod
    def _image_must_be_digest_pinned(cls, v: str) -> str:
        if not v.startswith("oci://") or "@sha256:" not in v:
            raise ValueError(
                "MCP image must be pinned by digest: oci://<path>@sha256:<hex64>"
            )
        _, digest_part = v.rsplit("@sha256:", 1)
        if len(digest_part) != 64 or not all(c in "0123456789abcdef" for c in digest_part):
            raise ValueError(
                "MCP image must be pinned by digest: oci://<path>@sha256:<hex64>"
            )
        return v

    @field_validator("provenance", "sbom")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("provenance and sbom must be non-empty.")
        return v
