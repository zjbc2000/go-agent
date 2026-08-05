"""MCP result validator: JSON Schema validation of untrusted sidecar output.

Every MCP tool result is treated as UNTRUSTED text. The validator checks the
raw output against the registered ``output_schema``. A mismatch returns a typed
failure — raw content is never surfaced.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from jsonschema import ValidationError, validate


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of validating an untrusted MCP result."""

    valid: bool
    data: Any | None = None
    error_code: str | None = None
    error_message: str | None = None


class McpValidator:
    """Validates untrusted MCP tool output against a registered JSON Schema."""

    def validate_output(
        self, output_schema: dict[str, Any], raw: dict[str, Any]
    ) -> ValidationResult:
        """Validate ``raw`` against ``output_schema``.

        On success returns the data. On failure returns a typed error — raw
        content is never surfaced to the caller.
        """
        try:
            validate(instance=raw, schema=output_schema)
        except ValidationError as exc:
            return ValidationResult(
                valid=False,
                error_code="VALIDATION_FAILED",
                error_message=f"MCP output does not match registered schema: {exc.message}",
            )
        except Exception:
            return ValidationResult(
                valid=False,
                error_code="VALIDATION_FAILED",
                error_message="MCP output schema validation failed.",
            )
        return ValidationResult(valid=True, data=raw)
