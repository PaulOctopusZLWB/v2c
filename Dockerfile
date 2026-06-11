FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

COPY src ./src
COPY SYSTEM_DESIGN_CN.md ARCHITECTURE.md IMPLEMENTATION_PLAN.md ./
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

ENTRYPOINT ["uv", "run", "--frozen", "--no-dev", "pcn"]
