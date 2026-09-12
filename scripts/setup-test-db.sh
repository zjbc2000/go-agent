#!/usr/bin/env bash
# Create the isolated test database (`postgres_test`) used by agent-service tests.
#
# WHY: the agent-service test suite TRUNCATEs chat/planning/execution tables before
# each test (autouse fixtures in tests/{chat,planning,execution}/conftest.py). That
# MUST NOT run against the dev database (127.0.0.1:54322/postgres) or it wipes real
# user data. Tests refuse to run unless TEST_DATABASE_URL points at an isolated DB.
#
# This script builds that DB: it copies the `auth` schema (GoTrue is managed, not
# defined in migrations) and then applies every supabase migration in order — the same
# tables / RLS policies / GRANTs the dev DB has, but no data.
#
# Usage:
#   ./scripts/setup-test-db.sh
# Then run tests with:
#   TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:54322/postgres_test \
#     uv run pytest tests/ -q

set -euo pipefail
cd "$(dirname "$0")/.."

DB_CONTAINER="supabase_db_go-agent"
TEST_DB="postgres_test"
SUPABASE_PSQL="docker exec $DB_CONTAINER psql -U postgres -d postgres"

echo "› Dropping existing $TEST_DB (if any)"
$SUPABASE_PSQL -c "drop database if exists $TEST_DB" 2>/dev/null || true

echo "› Creating $TEST_DB"
$SUPABASE_PSQL -c "create database $TEST_DB"

echo "› Copying the auth schema (auth.users, auth.uid, ...) — managed by GoTrue"
docker exec "$DB_CONTAINER" sh -c \
  "pg_dump -U postgres -d postgres --schema=auth --no-owner --schema-only 2>/dev/null | psql -U postgres -d $TEST_DB" \
  >/dev/null

echo "› Applying migrations"
for f in supabase/migrations/*.sql; do
  errs=$(docker exec -i "$DB_CONTAINER" psql -U postgres -d "$TEST_DB" < "$f" 2>&1 | grep -icE "^ERROR" || true)
  echo "  $(basename "$f"): $errs error(s)"
  [[ "$errs" -eq 0 ]] || { echo "  ✗ migration failed; aborting" >&2; exit 1; }
done

echo "✓ $TEST_DB ready."
echo "  Run tests with: TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:54322/$TEST_DB uv run pytest tests/ -q"
