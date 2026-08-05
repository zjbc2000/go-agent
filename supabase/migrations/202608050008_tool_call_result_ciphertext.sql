-- Task 3 review fix: store the result payload in sandbox_tool_calls so
-- idempotent replay can return the prior result data, not just a status.
--
-- The broker reserves the row (INSERT) BEFORE executing the side effect to
-- close the TOCTOU gap where two concurrent invocations both pass the existence
-- check. The reservation winner executes and stores the encrypted result; a
-- retried step returns the stored result without re-executing.
--
-- result_ciphertext is KMS-envelope encrypted; raw result content is never
-- exposed at rest.

alter table public.sandbox_tool_calls
  add column result_ciphertext text;
