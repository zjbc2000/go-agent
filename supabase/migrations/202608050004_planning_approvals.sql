-- Task 2 approval flow: proposal drafts, canonical encrypted payloads, decisions,
-- idempotency, expiry, and an append-only audit trail.
--
-- A pending draft is NOT a formal document: create_document_draft stores a proposal
-- that either edits an existing document (document_id set) or proposes a brand-new
-- one (document_id NULL; approving it CREATES the document). Migration 003 declared
-- document_id NOT NULL, so this migration relaxes that and adds the decision
-- columns the approval flow needs.

alter table public.document_drafts
  alter column document_id drop not null;

alter table public.approvals
  alter column document_id drop not null;

-- Canonical encrypted payload ({"type","title","body"}) plus its SHA-256, the
-- decision outcome, idempotency key, expiry, the originating run (for the
-- run.completed follow-on), and who resolved the approval.
alter table public.approvals
  add column payload_ciphertext text not null default '',
  add column payload_sha256 text not null default '',
  add column decision text,
  add column idempotency_key text,
  add column expires_at timestamptz,
  add column run_id uuid,
  add column resolved_by uuid;

-- Idempotency: at most one decision result per (approval, idempotency_key). The
-- service returns the original result for a repeated key and APPROVAL_CONFLICT for
-- a different key after resolution.
create unique index approvals_id_idempotency_key_idx on public.approvals (id, idempotency_key);

-- regenerate supersedes the draft and its approval instead of confirming it.
alter table public.document_drafts
  drop constraint document_drafts_status_check,
  add constraint document_drafts_status_check
    check (status in ('pending', 'confirmed', 'rejected', 'superseded'));
alter table public.document_drafts
  add column superseded_at timestamptz;

alter table public.approvals
  drop constraint approvals_status_check,
  add constraint approvals_status_check
    check (status in ('pending', 'confirmed', 'rejected', 'superseded'));

-- The audit trail is append-only: drop the owner update/delete paths so the trail
-- is not user-editable. Select and insert (the write path used by decisions) stay.
drop policy "audit_logs owner update" on public.audit_logs;
drop policy "audit_logs owner delete" on public.audit_logs;
revoke update, delete on table public.audit_logs from authenticated;
