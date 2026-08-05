# Example read-only MCP server — test fixture for the registry.
# DO NOT BUILD OR RUN this image (resource-constrained environment).
# The manifest.json is the registry test fixture; this Dockerfile
# documents the expected image shape for audit.
FROM python:3.12-slim

RUN useradd --create-home --uid 1000 sandbox
USER sandbox
WORKDIR /home/sandbox

COPY server.py /home/sandbox/server.py
ENTRYPOINT ["python", "-u", "server.py"]
