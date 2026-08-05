"""Internal skill-execution API routes.

Reachable only by the BFF proxy, which forwards the end-user JWT in
``Authorization`` and the shared internal token in ``X-Internal-Token``. The
browser never calls the Python service directly. The execution response is a
discriminated envelope (``{approval: ...}`` or ``{run: ...}``) carrying safe
fields only: no secrets, no ciphertext.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.chat.deps import get_request_context, require_internal_token
from app.core.context import RequestContext
from app.core.errors import ApiError
from app.skills.schemas import ExecutionRequestResult
from app.skills.service import SkillService

router = APIRouter()

_VALID_DECISIONS = ("approve", "reject", "regenerate")


def get_skill_service(request: Request) -> SkillService:
    return request.app.state.skill_service


@router.post("/internal/v1/skills/approvals/{approval_id}/decisions")
async def decide_execution_approval(
    approval_id: UUID,
    request: Request,
    _: None = Depends(require_internal_token),
    context: RequestContext = Depends(get_request_context),
    service: SkillService = Depends(get_skill_service),
) -> dict:
    """Confirm a pending execution approval and queue its sandbox run.

    The decision vocabulary matches the planning approvals (approve/reject/
    regenerate) but the execution service only supports ``approve`` in this MVP:
    a rejected/regenerated execution approval has no backend path yet. Expired
    approvals surface APPROVAL_EXPIRED (409) and a repeated idempotency_key
    returns the same run.
    """
    try:
        body = await request.json()
    except ValueError:
        raise ApiError("VALIDATION_FAILED", "Invalid JSON body.", False) from None
    if not isinstance(body, dict):
        raise ApiError("VALIDATION_FAILED", "Request body must be an object.", False)
    decision = body.get("decision")
    idempotency_key = body.get("idempotency_key")
    if decision not in _VALID_DECISIONS:
        raise ApiError(
            "VALIDATION_FAILED", "decision must be one of approve, reject, regenerate.", False
        )
    if decision != "approve":
        raise ApiError("VALIDATION_FAILED", "Only approve is supported for execution approvals.", False)
    if not isinstance(idempotency_key, str) or not idempotency_key.strip():
        raise ApiError("VALIDATION_FAILED", "idempotency_key is required.", False)

    run = await service.confirm_execution(context, approval_id, idempotency_key)
    return {
        "decision": "confirmed",
        "run": {
            "id": str(run.id),
            "status": run.status,
            "planHash": run.plan_hash,
        },
    }


@router.post("/internal/v1/skills/{document_id}/executions")
async def request_execution(
    document_id: UUID,
    request: Request,
    _: None = Depends(require_internal_token),
    context: RequestContext = Depends(get_request_context),
    service: SkillService = Depends(get_skill_service),
) -> dict:
    """Compile the skill document and record an approval or a queued run."""
    try:
        body = await request.json()
    except ValueError:
        raise ApiError("VALIDATION_FAILED", "Invalid JSON body.", False) from None
    if not isinstance(body, dict):
        raise ApiError("VALIDATION_FAILED", "Request body must be an object.", False)
    inputs = body.get("inputs")
    idempotency_key = body.get("idempotency_key")
    if not isinstance(inputs, dict):
        raise ApiError("VALIDATION_FAILED", "inputs must be an object.", False)
    if not isinstance(idempotency_key, str) or not idempotency_key.strip():
        raise ApiError("VALIDATION_FAILED", "idempotency_key is required.", False)

    result = await service.request_execution(context, document_id, inputs, idempotency_key)
    return _execution_result_to_dict(result)


def _execution_result_to_dict(result: ExecutionRequestResult) -> dict:
    if result.approval is not None:
        return {
            "approval": {
                "id": str(result.approval.id),
                "status": result.approval.status,
                "expiresAt": result.approval.expires_at.isoformat(),
                "createdAt": result.approval.created_at.isoformat(),
            }
        }
    if result.run is None:
        raise ApiError("INTERNAL_ERROR", "Execution returned neither approval nor run.", False)
    return {
        "run": {
            "id": str(result.run.id),
            "status": result.run.status,
            "planHash": result.run.plan_hash,
        }
    }
