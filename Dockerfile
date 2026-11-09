# Data Quality MCP server over Streamable HTTP (planning/milestone-4.md, D6).
#
#   docker build -t data-quality-mcp .
#   docker run --rm --env-file .env -p 8000:8000 data-quality-mcp
#
# The token comes from DATA_QUALITY_MCP_TOKEN at run time and is never stored
# in the image. Without one the container exits at startup.

# --- build: install from uv.lock into /app/.venv ---
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90 AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.5@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app

# Dependencies first, so a code change doesn't reinstall them
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Then the project itself, as a real (non-editable) install: the runtime stage
# copies only the venv
COPY README.md LICENSE ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

# --- runtime: the venv only, as a non-root user ---
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90

RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin app
COPY --from=build /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
USER app
WORKDIR /app
EXPOSE 8000

# The slim image has no curl: check /health with the standard library
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --start-interval=2s \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"]

CMD ["data-quality-mcp", "--transport", "http", "--host", "0.0.0.0", "--port", "8000"]
