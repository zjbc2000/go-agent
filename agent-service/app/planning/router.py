"""Internal planning API routes: approval decisions.

These routes are reachable only by the BFF proxy, which forwards the end-user JWT in
``Authorization`` and the shared internal token in ``X-Internal-Token``. The browser
never calls the Python service directly. There is intentionally NO route that reads
or decrypts planning content outside the owner's RLS scope.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.chat.deps import get_request_context, require_internal_token
from app.core.context import RequestContext
from app.core.errors import ApiError
from app.planning.schemas import ApprovalResult
from app.planning.service import PlanningService

router = APIRouter()

_VALID_DECISIONS = ("approve", "reject", "regenerate")


def get_planning_service(request: Request) -> PlanningService:
    return request.app.state.planning_service


@router.post("/internal/v1/approvals/{approval_id}/decisions")
async def decide_approval(
    approval_id: UUID,
    request: Request,
    _: None = Depends(require_internal_token),
    context: RequestContext = Depends(get_request_context),
    service: PlanningService = Depends(get_planning_service),
) -> dict:
    """Apply an approval decision; idempotent under a repeated idempotency_key."""
    try:
        body = await request.json()
    except ValueError:
        raise ApiError("VALIDATION_FAILED", "Invalid JSON body.", False) from None

    decision = body.get("decision")
    edited_payload = body.get("edited_payload")
    idempotency_key = body.get("idempotency_key")

    if decision not in _VALID_DECISIONS:
        raise ApiError(
            "VALIDATION_FAILED", "decision must be one of approve, reject, regenerate.", False
        )
    if not isinstance(idempotency_key, str) or not idempotency_key.strip():
        raise ApiError("VALIDATION_FAILED", "idempotency_key is required.", False)
    if edited_payload is not None:
        if not isinstance(edited_payload, dict):
            raise ApiError("VALIDATION_FAILED", "edited_payload must be an object.", False)
        if not isinstance(edited_payload.get("title"), str) or not edited_payload["title"].strip():
            raise ApiError("VALIDATION_FAILED", "edited_payload.title is required.", False)
        if not isinstance(edited_payload.get("body"), str) or not edited_payload["body"].strip():
            raise ApiError("VALIDATION_FAILED", "edited_payload.body is required.", False)

    result = await service.decide_document_approval(
        context, approval_id, decision, edited_payload, idempotency_key
    )
    return _approval_result_to_dict(result)


def _approval_result_to_dict(result: ApprovalResult) -> dict:
    document = None
    if result.document is not None:
        document = {
            "id": str(result.document.id),
            "type": result.document.type,
            "title": result.document.title,
            "body": result.document.body,
            "version": result.document.version,
        }
    return {
        "approvalId": str(result.approval_id),
        "decision": result.decision,
        "version": result.version,
        "originalPayload": result.original_payload,
        "document": document,
    }
