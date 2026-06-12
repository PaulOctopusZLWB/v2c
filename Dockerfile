FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app
ARG PCN_INSTALL_FUNASR=false

COPY src ./src
COPY scripts ./scripts
COPY SYSTEM_DESIGN_CN.md ARCHITECTURE.md IMPLEMENTATION_PLAN.md ./
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
RUN if [ "$PCN_INSTALL_FUNASR" = "true" ]; then uv pip install --python .venv/bin/python funasr modelscope; fi

ENTRYPOINT ["uv", "run", "--frozen", "--no-dev", "pcn"]
