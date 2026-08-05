"""Sandbox worker package: Celery consumer for immutable skill-plan execution.

The worker is the TRUSTED orchestrator (service-role Postgres for
is_terminal/claim/finish). It imports the agent-service ``app.*`` submodules
directly so ORM models, the cipher, and the skill compiler stay single-source.
The isolated CONTAINER gets no database credentials — that is Task 3.
"""
