# MVP Foundation and Recoverable Chat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the tracked FastAPI/BFF foundation and a real, encrypted, recoverable session/message/SSE chat path.

**Architecture:** Next.js remains the same-origin BFF and browser UI. A new FastAPI Agent service owns user-scoped persistence, model streaming, durable run events, and the OpenAI-compatible provider port; the browser creates a run then subscribes to its SSE event log.

**Tech Stack:** Next.js 16.3, React 19, TypeScript, FastAPI, Pydantic v2, SQLAlchemy 2, Supabase Auth/PostgreSQL/RLS, KMS envelope encryption, pytest, Vitest, Playwright.

## Global Constraints

- Before execution, make `/Users/edy/Z-github/go-agent` a tracked Git root without discarding the existing dirty `web/.git` working tree; do not execute implementation tasks until the repository ownership decision is approved.
- Read `web/node_modules/next/dist/docs/` before changing Next.js server code, as required by `web/AGENTS.md`.
- The browser calls only relative `/api/v1` paths; the Python service is never a public browser origin.
- All user-domain rows carry `user_id` and are protected by Supabase RLS using the end-user JWT.
- Store message and stream-event bodies only as KMS-encrypted ciphertext; RabbitMQ, Celery, audit records, usage records, logs, and telemetry contain no plaintext content.
- Use test-first changes. Each completed task must be committed in the approved Git root.

---

## File Structure

- Create: `agent-service/pyproject.toml` - pinned Python runtime and test/tool dependencies.
- Create: `agent-service/app/main.py` - FastAPI app factory, routers, OpenAPI configuration.
- Create: `agent-service/app/core/{config,context,errors,crypto}.py` - runtime settings, immutable identity, error envelope, envelope encryption port.
- Create: `agent-service/app/api/{schemas,deps}.py` - typed API models and JWT/request-signature dependencies.
- Create: `agent-service/tests/{conftest,test_health,test_errors,test_crypto}.py` - foundation fixtures and unit tests.
- Create: `supabase/migrations/202608050001_identity.sql` - profile table and RLS policy.
- Create: `web/src/lib/supabase/{browser,server}.ts` - Supabase clients for browser/server contexts.
- Create: `web/src/app/api/v1/auth/{login,logout,me}/route.ts` - cookie-backed BFF auth routes.
- Modify: `web/src/lib/api/real-auth-repository.ts` - relative BFF auth client and typed error conversion.
- Create: `agent-service/app/{db,models,repositories}/` - SQLAlchemy transaction, encrypted persistence, and chat storage boundary.
- Create: `supabase/migrations/202608050002_chat.sql` - sessions, messages, runs, encrypted stream events, indexes, RLS.
- Create: `agent-service/app/chat/{service,events,provider,router}.py` - run creation, provider stream, durable event append, SSE router.
- Create: `web/src/app/api/v1/[...path]/route.ts` and `web/src/lib/server/agent-proxy.ts` - signed BFF proxy with streaming response preservation.
- Create: `web/e2e/support/auth.ts` - real-stack login helper for seeded Supabase test accounts.
- Modify: `web/src/lib/domain/{types,repositories}.ts`, `web/src/lib/api/real-chat-repository.ts`, `web/src/lib/stores/chat-store.ts` - run-first subscription contract.
- Create: `web/src/lib/api/__tests__/real-chat-repository.test.ts` and `agent-service/tests/chat/` - API, replay, and client mapping coverage.

## Task 1: FastAPI Runtime, Typed Errors, and Encryption Port

**Files:**
- Create: `agent-service/pyproject.toml`, `agent-service/app/main.py`, `agent-service/app/core/config.py`, `agent-service/app/core/errors.py`, `agent-service/app/core/crypto.py`
- Create: `agent-service/tests/test_health.py`, `agent-service/tests/test_errors.py`, `agent-service/tests/test_crypto.py`

**Interfaces:**
- Produces: `create_app() -> FastAPI`, `ApiError(code: ErrorCode, message: str, retryable: bool)`, `EnvelopeCipher.encrypt(plaintext: str) -> str`, `EnvelopeCipher.decrypt(ciphertext: str) -> str`.

- [ ] **Step 1: Write the failing health, envelope, and cipher tests**

```python
def test_health_returns_service_name(client):
    assert client.get("/healthz").json() == {"service": "agent-service", "status": "ok"}

def test_api_error_has_safe_envelope():
    assert ApiError("VALIDATION_FAILED", "Invalid input", False).to_body()["error"]["retryable"] is False

TEST_KEY = base64.urlsafe_b64encode(b"0" * 32).decode()

def test_cipher_round_trip_does_not_return_plaintext():
    cipher = LocalEnvelopeCipher.from_base64_key(TEST_KEY)
    token = cipher.encrypt("private message")
    assert token != "private message"
    assert cipher.decrypt(token) == "private message"
```

- [ ] **Step 2: Run the tests to verify the foundation is absent**

Run: `cd agent-service && uv run pytest tests/test_health.py tests/test_errors.py tests/test_crypto.py -v`

Expected: FAIL because the app and core modules do not exist.

- [ ] **Step 3: Create the minimal runtime and encryption implementation**

```python
def create_app() -> FastAPI:
    app = FastAPI(title="Goudan Agent API", version="1.0.0")
    app.add_api_route("/healthz", lambda: {"service": "agent-service", "status": "ok"})
    app.add_exception_handler(ApiError, api_error_handler)
    return app
```

Use `cryptography.fernet.MultiFernet` behind `EnvelopeCipher`; load a local test key from settings and define the production KMS adapter in the same port without putting a key in source control.

```python
class LocalEnvelopeCipher:
    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        return self._fernet.decrypt(ciphertext.encode()).decode()
```

- [ ] **Step 4: Run focused tests and static checks**

Run: `cd agent-service && uv run pytest tests/test_health.py tests/test_errors.py tests/test_crypto.py -v && uv run ruff check app tests && uv run mypy app`

Expected: PASS with no lint or type errors.

- [ ] **Step 5: Commit the runtime boundary**

```bash
git add agent-service/pyproject.toml agent-service/app agent-service/tests
git commit -m "feat(agent): add FastAPI runtime and encryption port"
```

## Task 2: Supabase Identity, BFF Authentication, and Request Context

**Files:**
- Create: `supabase/migrations/202608050001_identity.sql`, `agent-service/app/core/context.py`, `agent-service/app/api/deps.py`
- Create: `web/src/lib/supabase/browser.ts`, `web/src/lib/supabase/server.ts`, `web/src/app/api/v1/auth/login/route.ts`, `web/src/app/api/v1/auth/logout/route.ts`, `web/src/app/api/v1/auth/me/route.ts`
- Modify: `web/src/lib/api/real-auth-repository.ts`, `web/src/lib/domain/types.ts`
- Test: `agent-service/tests/test_context.py`, `web/src/lib/api/__tests__/real-auth-repository.test.ts`

**Interfaces:**
- Consumes: `ApiError` from Task 1.
- Produces: `RequestContext(user_id: UUID, role: Literal["user", "admin"], request_id: str)`, `decode_request_context(authorization: str | None) -> RequestContext`, and BFF responses of `User { id, name, email, role }`.

- [ ] **Step 1: Write failing request-context and BFF repository tests**

```python
def test_context_rejects_missing_bearer_token():
    with pytest.raises(ApiError, match="AUTH_REQUIRED"):
        decode_request_context(None)
```

```ts
it("posts login to the relative BFF route", async () => {
  await createRealAuthRepository().login("user@example.com", "secret");
  expect(fetch).toHaveBeenCalledWith("/api/v1/auth/login", expect.objectContaining({ method: "POST" }));
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd agent-service && uv run pytest tests/test_context.py -v`

Run: `cd web && pnpm test -- real-auth-repository.test.ts`

Expected: FAIL because the JWT dependency, profile policy, and BFF routes are absent.

- [ ] **Step 3: Implement profile RLS and cookie-backed authentication**

```sql
create table public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  role text not null check (role in ('user', 'admin')) default 'user',
  display_name text not null,
  disabled_at timestamptz
);
alter table public.profiles enable row level security;
create policy "profile owner reads self" on public.profiles for select using (id = auth.uid());
```

Use `@supabase/ssr` in Next route handlers. In FastAPI, validate the Supabase JWT against JWKS, reject disabled profiles, and construct `RequestContext`; never accept `user_id` from JSON body or query parameters.

```python
def decode_request_context(authorization: str | None) -> RequestContext:
    if not authorization or not authorization.startswith("Bearer "):
        raise ApiError("AUTH_REQUIRED", "Authentication is required.", False)
    claims = verify_supabase_jwt(authorization.removeprefix("Bearer "))
    return RequestContext(user_id=UUID(claims["sub"]), role=load_role(claims["sub"]), request_id=new_request_id())
```

- [ ] **Step 4: Run focused tests and a Supabase migration check**

Run: `supabase db reset && cd agent-service && uv run pytest tests/test_context.py -v`

Run: `cd web && pnpm test -- real-auth-repository.test.ts`

Expected: PASS; a token for user A cannot load profile B.

- [ ] **Step 5: Commit authentication boundaries**

```bash
git add supabase/migrations/202608050001_identity.sql agent-service/app/core agent-service/app/api web/src/lib/supabase web/src/app/api/v1/auth web/src/lib/api/real-auth-repository.ts web/src/lib/domain/types.ts
git commit -m "feat(auth): add Supabase BFF authentication and request context"
```

## Task 3: Encrypted Session, Message, Run, and Event Persistence

**Files:**
- Create: `supabase/migrations/202608050002_chat.sql`, `agent-service/app/db/session.py`, `agent-service/app/models/chat.py`, `agent-service/app/repositories/chat.py`
- Create: `agent-service/tests/chat/conftest.py`, `agent-service/tests/chat/test_repository.py`, `agent-service/tests/chat/test_rls.py`

**Interfaces:**
- Consumes: `RequestContext`, `EnvelopeCipher`.
- Produces: `ChatRepository.create_run(context, session_id, content, idempotency_key) -> CreatedRun`, `append_event(run_id, kind, payload) -> StreamEvent`, `list_messages(context, session_id) -> list[Message]`.

- [ ] **Step 1: Write failing persistence, idempotency, encryption, and RLS tests**

```python
async def test_create_run_is_idempotent(chat_repository, user_context):
    first = await chat_repository.create_run(user_context, SESSION_ID, "hello", "req-1")
    second = await chat_repository.create_run(user_context, SESSION_ID, "hello", "req-1")
    assert second.run_id == first.run_id

async def test_raw_message_column_never_contains_plaintext(db_session, chat_repository, user_context):
    await chat_repository.create_run(user_context, SESSION_ID, "private", "req-2")
    assert "private" not in await db_session.scalar(text("select content_ciphertext from messages limit 1"))
```

- [ ] **Step 2: Run the repository tests to verify they fail**

Run: `cd agent-service && uv run pytest tests/chat/test_repository.py tests/chat/test_rls.py -v`

Expected: FAIL because the chat schema and repository are absent.

- [ ] **Step 3: Add the migration and repository transaction boundary**

Create `sessions`, `messages`, `agent_runs`, and `stream_events` with `user_id`, encrypted content columns, status constraints, indexes, RLS policies, and unique `(user_id, idempotency_key)` plus `(run_id, sequence)` constraints. Create the user message, streaming assistant placeholder, and queued run in one transaction; encrypt content before insert.

```python
async with self._transaction(context) as session:
    run = AgentRun(user_id=context.user_id, session_id=session_id, idempotency_key=idempotency_key, status="queued")
    session.add_all([Message.user(context.user_id, session_id, cipher.encrypt(content)), Message.assistant_placeholder(context.user_id, session_id, run.id), run])
    await session.flush()
    return CreatedRun.from_model(run)
```

- [ ] **Step 4: Run migrations and repository tests**

Run: `supabase db reset && cd agent-service && uv run pytest tests/chat/test_repository.py tests/chat/test_rls.py -v`

Expected: PASS; user B receives no rows for user A's session and duplicate requests return the original run.

- [ ] **Step 5: Commit durable chat storage**

```bash
git add supabase/migrations/202608050002_chat.sql agent-service/app/db agent-service/app/models/chat.py agent-service/app/repositories/chat.py agent-service/tests/chat
git commit -m "feat(chat): add encrypted recoverable chat persistence"
```

## Task 4: Provider Stream, FastAPI Chat API, SSE Replay, and BFF Proxy

**Files:**
- Create: `agent-service/app/chat/provider.py`, `agent-service/app/chat/events.py`, `agent-service/app/chat/service.py`, `agent-service/app/chat/router.py`
- Create: `agent-service/tests/chat/test_api.py`, `agent-service/tests/chat/test_sse_replay.py`
- Create: `web/src/lib/server/agent-proxy.ts`, `web/src/app/api/v1/[...path]/route.ts`
- Test: `web/e2e/real-chat.spec.ts`

**Interfaces:**
- Produces: `ModelProvider.stream(messages) -> AsyncIterator[ProviderDelta]`, `POST /internal/v1/sessions/{id}/runs`, `GET /internal/v1/runs/{id}/events`, and BFF pass-through `/api/v1/*` routes.

- [ ] **Step 1: Write failing API and replay tests with a deterministic provider**

```python
async def test_sse_replays_only_events_after_last_id(client, seeded_run):
    first = await collect_sse(client, f"/internal/v1/runs/{seeded_run}/events")
    replay = await collect_sse(client, f"/internal/v1/runs/{seeded_run}/events", last_event_id=first[0].id)
    assert [event.id for event in replay] == [event.id for event in first[1:]]
```

- [ ] **Step 2: Run the API tests to verify they fail**

Run: `cd agent-service && uv run pytest tests/chat/test_api.py tests/chat/test_sse_replay.py -v`

Expected: FAIL because run routes and SSE encoding are absent.

- [ ] **Step 3: Implement the provider port, event writer, and streaming routes**

Use named SSE events and `id: <sequence>`. Persist each encrypted delta before yielding it. On terminal success or failure, finalize the assistant message, persist `run.completed` or `run.failed`, and erase replay events only after the configured retention period. Make the BFF proxy preserve `text/event-stream`, `Cache-Control: no-cache`, and the `Last-Event-ID` header while adding the signed internal request header.

```python
async def event_stream(run_id: UUID, after: int) -> AsyncIterator[str]:
    async for event in events.replay_and_follow(run_id, after):
        yield f"id: {event.sequence}\nevent: {event.kind}\ndata: {event.public_payload()}\n\n"
```

- [ ] **Step 4: Run API, replay, and proxy tests**

Run: `cd agent-service && uv run pytest tests/chat/test_api.py tests/chat/test_sse_replay.py -v`

Run: `cd web && pnpm test -- agent-proxy && pnpm test:e2e -- real-chat.spec.ts`

Expected: PASS; network interruption reconnects to an existing run without creating a second user message.

- [ ] **Step 5: Commit the server-side streaming path**

```bash
git add agent-service/app/chat agent-service/tests/chat web/src/lib/server/agent-proxy.ts 'web/src/app/api/v1/[...path]/route.ts' web/e2e/real-chat.spec.ts
git commit -m "feat(chat): add durable SSE run streaming through BFF"
```

## Task 5: Switch the Web Chat Repository to the Run-First Contract

**Files:**
- Modify: `web/src/lib/domain/types.ts`, `web/src/lib/domain/repositories.ts`, `web/src/lib/api/real-chat-repository.ts`, `web/src/lib/stores/chat-store.ts`, `web/src/components/chat/Composer.tsx`, `web/src/components/chat/ConnectionBanner.tsx`
- Create: `web/src/lib/api/__tests__/real-chat-repository.test.ts`, `web/src/lib/stores/__tests__/chat-run-state.test.ts`

**Interfaces:**
- Consumes: BFF APIs from Task 4.
- Produces: `ChatRepository.createRun(...)`, `ChatRepository.subscribeRunEvents(runId, lastEventId?)`, `ChatRepository.getRun(runId)`, and a store that persists `activeRunId` and `lastEventId` per session.

- [ ] **Step 1: Write failing client state and event-mapping tests**

```ts
it("reconnects with the last event id without resending content", async () => {
  const run = await repository.createRun("session-1", "hello", "req-1");
  await repository.subscribeRunEvents(run.runId, "3");
  expect(fetch).not.toHaveBeenCalledWith(expect.stringContaining("/runs"), expect.objectContaining({ method: "POST", body: expect.stringContaining("hello") }));
});
```

- [ ] **Step 2: Run the frontend tests to verify they fail**

Run: `cd web && pnpm test -- real-chat-repository.test.ts chat-run-state.test.ts`

Expected: FAIL because the old repository posts directly to a stream and the store lacks run state.

- [ ] **Step 3: Implement run creation, event subscription, and explicit error mapping**

Map `AUTH_REQUIRED` to logout/re-login, stream loss to `reconnecting`, idempotency conflict to loading the existing run, and provider failure to a retriable terminal message. Preserve accumulated text, close the subscription on cancel, and never expose an Agent base URL in browser code.

```ts
async createRun(sessionId, content, idempotencyKey) {
  return requestJson<CreatedRun>(`/api/v1/sessions/${sessionId}/runs`, { method: "POST", headers: { "Idempotency-Key": idempotencyKey }, body: JSON.stringify({ content }) });
}
```

- [ ] **Step 4: Run all frontend unit tests and the real-chat E2E test**

Run: `cd web && pnpm test && pnpm test:e2e -- real-chat.spec.ts`

Expected: PASS; the existing Mock repositories still satisfy the updated interface.

- [ ] **Step 5: Commit real chat client integration**

```bash
git add web/src/lib/domain web/src/lib/api/real-chat-repository.ts web/src/lib/stores/chat-store.ts web/src/components/chat web/src/lib/api/__tests__ web/src/lib/stores/__tests__
git commit -m "feat(web): consume durable real chat API"
```
