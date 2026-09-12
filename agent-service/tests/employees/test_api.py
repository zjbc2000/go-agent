"""API tests for the internal company/employee routes.

Covered: CRUD, card-driven approval creation, and decisions — all against the
in-process FastAPI app with the fake JWT verifier (``conftest.py``). Every route is
user-scoped by RLS, so another user's employee/approval surfaces as 404.
"""

from uuid import uuid4

from tests.employees.conftest import TEST_INTERNAL_TOKEN


def _headers(api_headers, **extra):
    return {**api_headers, "Content-Type": "application/json", **extra}


# --- GET / POST / PUT /internal/v1/employees ---------------------------------


async def test_list_employees_requires_internal_token(client):
    resp = client.get("/internal/v1/employees", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTH_REQUIRED"


async def test_create_employee_validates_fields(client, api_headers):
    resp = client.post(
        "/internal/v1/employees",
        headers=_headers(api_headers),
        json={"name": "李雷", "position": ""},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_FAILED"


async def test_create_and_list_employees(client, api_headers):
    created = client.post(
        "/internal/v1/employees",
        headers=_headers(api_headers),
        json={"name": "李雷", "position": "运营专员", "prompt": "负责社区运营"},
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["name"] == "李雷"
    assert body["position"] == "运营专员"
    assert body["status"] == "active"
    assert body["createdAt"]

    listed = client.get("/internal/v1/employees", headers=api_headers)
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["prompt"] == "负责社区运营"


async def test_update_employee_persists(client, api_headers):
    created = client.post(
        "/internal/v1/employees",
        headers=_headers(api_headers),
        json={"name": "李雷", "position": "运营专员", "prompt": "运营"},
    ).json()
    resp = client.put(
        f"/internal/v1/employees/{created['id']}",
        headers=_headers(api_headers),
        json={"position": "产品经理"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["position"] == "产品经理"
    assert resp.json()["name"] == "李雷"


async def test_update_employee_not_owned_returns_404(client, api_headers, user_context, service, user_b):
    employee = await service.create_employee(user_context, "李雷", "运营专员", "运营")
    headers_b = {
        "X-Internal-Token": TEST_INTERNAL_TOKEN,
        "Authorization": f"Bearer {user_b.user_id}",
        "Content-Type": "application/json",
    }
    resp = client.put(
        f"/internal/v1/employees/{employee.id}",
        headers=headers_b,
        json={"position": "hijack"},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


# --- Card-driven approval + decision ------------------------------------------


async def test_request_employee_action_and_decide(client, api_headers, service, user_context):
    employee = await service.create_employee(user_context, "李雷", "运营专员", "运营")
    created = client.post(
        "/internal/v1/employees/approvals",
        headers=_headers(api_headers),
        json={"employeeId": str(employee.id), "action": "fire", "idempotencyKey": "fire-1"},
    )
    assert created.status_code == 200, created.text
    approval = created.json()
    assert approval["action"] == "fire"
    assert approval["name"] == "李雷"
    assert approval["approvalId"]

    decided = client.post(
        f"/internal/v1/employees/approvals/{approval['approvalId']}/decisions",
        headers=_headers(api_headers),
        json={"decision": "approve", "idempotency_key": "decide-1"},
    )
    assert decided.status_code == 200, decided.text
    result = decided.json()
    assert result["decision"] == "confirmed"
    assert result["employee"]["status"] == "inactive"

    # The approval is consumed: deciding again with a NEW key conflicts.
    conflicted = client.post(
        f"/internal/v1/employees/approvals/{approval['approvalId']}/decisions",
        headers=_headers(api_headers),
        json={"decision": "approve", "idempotency_key": "decide-OTHER"},
    )
    assert conflicted.status_code == 409
    assert conflicted.json()["error"]["code"] == "APPROVAL_CONFLICT"


async def test_request_employee_action_requires_employee_and_key(client, api_headers):
    resp = client.post(
        "/internal/v1/employees/approvals",
        headers=_headers(api_headers),
        json={"employeeId": str(uuid4()), "action": "fire"},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_FAILED"


async def test_decision_requires_valid_decision(client, api_headers):
    resp = client.post(
        f"/internal/v1/employees/approvals/{uuid4()}/decisions",
        headers=_headers(api_headers),
        json={"decision": "maybe", "idempotency_key": "k"},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_FAILED"
