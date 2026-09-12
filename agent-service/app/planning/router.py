"""Internal planning API routes: documents, versions, drafts, and decisions.

These routes are reachable only by the BFF proxy, which forwards the end-user JWT in
``Authorization`` and the shared internal token in ``X-Internal-Token``. The browser
never calls the Python service directly. There is intentionally NO route that reads
or decrypts planning content outside the owner's RLS scope.
"""

from typing import cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.chat.deps import get_request_context, require_internal_token
from app.core.context import RequestContext
from app.core.errors import ApiError
from app.planning.schemas import ApprovalResult, DraftApproval
from app.planning.service import PlanningService
from app.repositories.planning import Document, DocumentRepository, DocumentType

router = APIRouter()

_VALID_DECISIONS = ("approve", "reject", "regenerate")
_VALID_DOCUMENT_TYPES: tuple[DocumentType, ...] = ("memory", "interest", "task", "skill")


def get_planning_service(request: Request) -> PlanningService:
    return request.app.state.planning_service


def get_planning_repository(request: Request) -> DocumentRepository:
    return request.app.state.planning_repository


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
        raise ApiError("VALIDATION_FAILED", "decision must be one of approve, reject, regenerate.", False)
    if not isinstance(idempotency_key, str) or not idempotency_key.strip():
        raise ApiError("VALIDATION_FAILED", "idempotency_key is required.", False)
    if edited_payload is not None:
        if not isinstance(edited_payload, dict):
            raise ApiError("VALIDATION_FAILED", "edited_payload must be an object.", False)
        if not isinstance(edited_payload.get("title"), str) or not edited_payload["title"].strip():
            raise ApiError("VALIDATION_FAILED", "edited_payload.title is required.", False)
        if not isinstance(edited_payload.get("body"), str) or not edited_payload["body"].strip():
            raise ApiError("VALIDATION_FAILED", "edited_payload.body is required.", False)

    result = await service.decide_document_approval(context, approval_id, decision, edited_payload, idempotency_key)
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


# --- Documents, versions, and draft creation --------------------------------


@router.get("/internal/v1/documents")
async def list_documents(
    type: str | None = None,
    _: None = Depends(require_internal_token),
    context: RequestContext = Depends(get_request_context),
    repository: DocumentRepository = Depends(get_planning_repository),
) -> list[dict]:
    """List the caller's active documents, optionally narrowed to one category."""
    filter_type: DocumentType | None = None
    if type is not None:
        if type not in _VALID_DOCUMENT_TYPES:
            raise ApiError("VALIDATION_FAILED", "type must be one of memory, interest, task, skill.", False)
        filter_type = cast(DocumentType, type)
    documents = await repository.list_active(context, filter_type)
    return [_document_to_dict(document) for document in documents]


@router.delete("/internal/v1/documents/{document_id}")
async def delete_document(
    document_id: UUID,
    _: None = Depends(require_internal_token),
    context: RequestContext = Depends(get_request_context),
    repository: DocumentRepository = Depends(get_planning_repository),
) -> dict:
    """Hard-delete the caller's document (RLS-gated to the owner)."""
    if not await repository.delete(context, document_id):
        raise ApiError("NOT_FOUND", "Document not found.", False)
    return {"ok": True}


@router.put("/internal/v1/documents/{document_id}")
async def update_document(
    document_id: UUID,
    request: Request,
    _: None = Depends(require_internal_token),
    context: RequestContext = Depends(get_request_context),
    repository: DocumentRepository = Depends(get_planning_repository),
) -> dict:
    """Edit the caller's document, appending a new version (title/body both required)."""
    try:
        body = await request.json()
    except ValueError:
        raise ApiError("VALIDATION_FAILED", "Invalid JSON body.", False) from None
    if not isinstance(body, dict):
        raise ApiError("VALIDATION_FAILED", "Invalid JSON body.", False)
    title = body.get("title")
    content = body.get("body")
    if not isinstance(title, str) or not title.strip():
        raise ApiError("VALIDATION_FAILED", "title is required.", False)
    if not isinstance(content, str) or not content.strip():
        raise ApiError("VALIDATION_FAILED", "body is required.", False)
    document = await repository.update_active(context, document_id, title.strip(), content.strip())
    return _document_to_dict(document)


@router.delete("/internal/v1/documents/{document_id}/versions/{version_id}")
async def delete_document_version(
    document_id: UUID,
    version_id: UUID,
    _: None = Depends(require_internal_token),
    context: RequestContext = Depends(get_request_context),
    repository: DocumentRepository = Depends(get_planning_repository),
) -> dict:
    """Delete a non-current version row; the current version is immutable."""
    if not await repository.delete_version(context, document_id, version_id):
        raise ApiError("NOT_FOUND", "Version not found or is the current version.", False)
    return {"ok": True}


@router.get("/internal/v1/documents/{document_id}/versions")
async def list_document_versions(
    document_id: UUID,
    _: None = Depends(require_internal_token),
    context: RequestContext = Depends(get_request_context),
    repository: DocumentRepository = Depends(get_planning_repository),
) -> list[dict]:
    """List the caller's version rows for a document (oldest first)."""
    versions = await repository.list_versions(context, document_id)
    return [
        {
            "id": str(version.id),
            "documentId": str(version.document_id),
            "version": version.version,
            "title": version.title,
            "body": version.body,
            "createdAt": version.created_at.isoformat(),
        }
        for version in versions
    ]


@router.post("/internal/v1/documents/{document_id}/versions/{version_id}/restore")
async def restore_document_version(
    document_id: UUID,
    version_id: UUID,
    _: None = Depends(require_internal_token),
    context: RequestContext = Depends(get_request_context),
    repository: DocumentRepository = Depends(get_planning_repository),
) -> dict:
    """Restore a prior version as a NEW current version (RLS-scoped)."""
    document = await repository.restore_version(context, document_id, version_id)
    return _document_to_dict(document)


@router.post("/internal/v1/approvals")
async def create_draft(
    request: Request,
    _: None = Depends(require_internal_token),
    context: RequestContext = Depends(get_request_context),
    service: PlanningService = Depends(get_planning_service),
) -> dict:
    """Create a pending proposal draft + approval from the real stack."""
    try:
        body = await request.json()
    except ValueError:
        raise ApiError("VALIDATION_FAILED", "Invalid JSON body.", False) from None

    if not isinstance(body, dict):
        raise ApiError("VALIDATION_FAILED", "Request body must be an object.", False)
    type = body.get("type")
    title = body.get("title")
    raw_body = body.get("body")
    if not isinstance(type, str) or type not in _VALID_DOCUMENT_TYPES:
        raise ApiError("VALIDATION_FAILED", "type must be one of memory, interest, task, skill.", False)
    if not isinstance(title, str) or not title.strip():
        raise ApiError("VALIDATION_FAILED", "title is required.", False)
    if not isinstance(raw_body, str) or not raw_body.strip():
        raise ApiError("VALIDATION_FAILED", "body is required.", False)

    document_id = _optional_uuid(body.get("document_id"), "document_id")
    run_id = _optional_uuid(body.get("run_id"), "run_id")

    draft = await service.create_document_draft(
        context,
        type,
        title,
        raw_body,
        document_id=document_id,
        run_id=run_id,
    )
    return _draft_to_dict(draft)


def _optional_uuid(value: object, field: str) -> UUID | None:
    """Parse an optional UUID body field; a malformed value fails validation."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ApiError("VALIDATION_FAILED", f"{field} must be a UUID string.", False)
    try:
        return UUID(value)
    except ValueError:
        raise ApiError("VALIDATION_FAILED", f"{field} must be a UUID string.", False) from None


def _draft_to_dict(draft: DraftApproval) -> dict:
    return {
        "approvalId": str(draft.approval_id),
        "draftId": str(draft.draft_id),
        "documentId": str(draft.document_id) if draft.document_id is not None else None,
        "type": draft.type,
        "title": draft.title,
        "body": draft.body,
    }


def _document_to_dict(document: Document) -> dict:
    return {
        "id": str(document.id),
        "type": document.type,
        "title": document.title,
        "body": document.body,
        "version": document.version,
        "createdAt": document.created_at.isoformat(),
        "updatedAt": document.updated_at.isoformat(),
    }
