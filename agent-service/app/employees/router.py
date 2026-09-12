"""Internal company/employees API routes.

These routes are reachable only by the BFF proxy, which forwards the end-user JWT in
``Authorization`` and the shared internal token in ``X-Internal-Token``. There is
intentionally NO route that reads or decrypts employee content outside the owner's
RLS scope. Fire/rehire/adjust_position are never applied directly: they go through
``employee_approvals`` and the owner's decision.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.chat.deps import get_request_context, require_internal_token
from app.core.context import RequestContext
from app.core.errors import ApiError
from app.employees.service import EmployeeService
from app.repositories.employees import Employee, EmployeeApproval, EmployeeDecision

router = APIRouter()

_VALID_DECISIONS = ("approve", "reject")
_VALID_ACTIONS = ("fire", "rehire", "adjust_position")


def get_employee_service(request: Request) -> EmployeeService:
    return request.app.state.employee_service


@router.get("/internal/v1/employees")
async def list_employees(
    _: None = Depends(require_internal_token),
    context: RequestContext = Depends(get_request_context),
    service: EmployeeService = Depends(get_employee_service),
) -> list[dict]:
    """List the caller's employees (active and inactive)."""
    employees = await service.list_employees(context)
    return [_employee_to_dict(employee) for employee in employees]


@router.post("/internal/v1/employees")
async def create_employee(
    request: Request,
    _: None = Depends(require_internal_token),
    context: RequestContext = Depends(get_request_context),
    service: EmployeeService = Depends(get_employee_service),
) -> dict:
    """Create an employee with status active (name/position/prompt required)."""
    body = await _json_body(request)
    name = body.get("name")
    position = body.get("position")
    prompt = body.get("prompt")
    if not isinstance(name, str) or not name.strip():
        raise ApiError("VALIDATION_FAILED", "name is required.", False)
    if not isinstance(position, str) or not position.strip():
        raise ApiError("VALIDATION_FAILED", "position is required.", False)
    if not isinstance(prompt, str) or not prompt.strip():
        raise ApiError("VALIDATION_FAILED", "prompt is required.", False)
    employee = await service.create_employee(context, name, position, prompt)
    return _employee_to_dict(employee)


@router.put("/internal/v1/employees/{employee_id}")
async def update_employee(
    employee_id: UUID,
    request: Request,
    _: None = Depends(require_internal_token),
    context: RequestContext = Depends(get_request_context),
    service: EmployeeService = Depends(get_employee_service),
) -> dict:
    """Update the caller's employee name/position/prompt (at least one field)."""
    body = await _json_body(request)
    name = body.get("name")
    position = body.get("position")
    prompt = body.get("prompt")
    employee = await service.update_employee(
        context,
        employee_id,
        name=name,
        position=position,
        prompt=prompt,
    )
    return _employee_to_dict(employee)


@router.post("/internal/v1/employees/approvals")
async def request_employee_action(
    request: Request,
    _: None = Depends(require_internal_token),
    context: RequestContext = Depends(get_request_context),
    service: EmployeeService = Depends(get_employee_service),
) -> dict:
    """Create a pending fire/rehire/adjust approval (card-driven path)."""
    body = await _json_body(request)
    employee_id = _require_uuid(body.get("employeeId"), "employeeId")
    action = body.get("action")
    position = body.get("position")
    idempotency_key = body.get("idempotencyKey")
    if action not in _VALID_ACTIONS:
        raise ApiError("VALIDATION_FAILED", "action must be one of fire, rehire, adjust_position.", False)
    if not isinstance(idempotency_key, str) or not idempotency_key.strip():
        raise ApiError("VALIDATION_FAILED", "idempotencyKey is required.", False)
    if position is not None and not isinstance(position, str):
        raise ApiError("VALIDATION_FAILED", "position must be a string.", False)
    approval = await service.create_employee_action_draft(
        context,
        employee_id=employee_id,
        action=action,
        position=position,
        idempotency_key=idempotency_key.strip(),
    )
    return _approval_to_dict(approval)


@router.post("/internal/v1/employees/approvals/{approval_id}/decisions")
async def decide_employee_approval(
    approval_id: UUID,
    request: Request,
    _: None = Depends(require_internal_token),
    context: RequestContext = Depends(get_request_context),
    service: EmployeeService = Depends(get_employee_service),
) -> dict:
    """Apply an employee-action decision; idempotent under a repeated key."""
    body = await _json_body(request)
    decision = body.get("decision")
    idempotency_key = body.get("idempotency_key")
    if decision not in _VALID_DECISIONS:
        raise ApiError("VALIDATION_FAILED", "decision must be one of approve, reject.", False)
    if not isinstance(idempotency_key, str) or not idempotency_key.strip():
        raise ApiError("VALIDATION_FAILED", "idempotency_key is required.", False)
    result = await service.decide_employee_approval(context, approval_id, decision, idempotency_key)
    return _decision_to_dict(result)


async def _json_body(request: Request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        raise ApiError("VALIDATION_FAILED", "Invalid JSON body.", False) from None
    if not isinstance(body, dict):
        raise ApiError("VALIDATION_FAILED", "Request body must be an object.", False)
    return body


def _require_uuid(value: object, field: str) -> UUID:
    if not isinstance(value, str):
        raise ApiError("VALIDATION_FAILED", f"{field} must be a UUID string.", False)
    try:
        return UUID(value)
    except ValueError:
        raise ApiError("VALIDATION_FAILED", f"{field} must be a UUID string.", False) from None


def _employee_to_dict(employee: Employee) -> dict:
    return {
        "id": str(employee.id),
        "name": employee.name,
        "position": employee.position,
        "prompt": employee.prompt,
        "status": employee.status,
        "createdAt": employee.created_at.isoformat(),
        "updatedAt": employee.updated_at.isoformat(),
    }


def _approval_to_dict(approval: EmployeeApproval) -> dict:
    return {
        "approvalId": str(approval.approval_id),
        "employeeId": str(approval.employee_id),
        "action": approval.action,
        "name": approval.name,
        "position": approval.position,
        "status": approval.status,
        "positionToSet": approval.position_to_set,
    }


def _decision_to_dict(result: EmployeeDecision) -> dict:
    return {
        "approvalId": str(result.approval_id),
        "decision": result.decision,
        "employee": _employee_to_dict(result.employee) if result.employee is not None else None,
    }
