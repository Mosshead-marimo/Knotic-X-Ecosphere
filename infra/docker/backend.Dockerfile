# syntax=docker/dockerfile:1.7@sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e
FROM python:3.13.14-slim-bookworm@sha256:67a1e1f215ccda113cfc024e8639049257e88f273898f595b61476d128d387e8 AS builder
ARG UV_VERSION=0.12.2
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
RUN pip install --no-cache-dir "uv==${UV_VERSION}"
WORKDIR /workspace
COPY pyproject.toml uv.lock ./
COPY packages/config/pyproject.toml packages/config/README.md packages/config/
COPY packages/config/src packages/config/src
COPY mcp/pyproject.toml mcp/README.md mcp/
COPY backend/pyproject.toml backend/README.md backend/
COPY backend/src backend/src
RUN uv sync --locked --no-dev --no-editable --package knotic-api

FROM python:3.13.14-slim-bookworm@sha256:67a1e1f215ccda113cfc024e8639049257e88f273898f595b61476d128d387e8 AS runtime
ENV PATH=/workspace/.venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN groupadd --gid 10001 knotic && useradd --uid 10001 --gid knotic --no-create-home --shell /usr/sbin/nologin knotic
WORKDIR /workspace
COPY --from=builder --chown=10001:10001 /workspace/.venv /workspace/.venv
USER 10001:10001
EXPOSE 8080
CMD ["gunicorn", "--bind=0.0.0.0:8080", "--workers=2", "--threads=4", "--timeout=30", "knotic_api.app:create_app()"]
