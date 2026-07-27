FROM node:22-bookworm-slim AS web-build

WORKDIR /app/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web ./
RUN npm run build

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app
ARG PCN_INSTALL_FUNASR=false

COPY src ./src
COPY scripts ./scripts
COPY config ./config
COPY --from=web-build /app/web/dist ./web/dist
COPY SYSTEM_DESIGN_CN.md ARCHITECTURE.md IMPLEMENTATION_PLAN.md ./
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
RUN if [ "$PCN_INSTALL_FUNASR" = "true" ]; then uv sync --frozen --no-dev --extra funasr; fi

ENTRYPOINT ["uv", "run", "--frozen", "--no-dev", "pcn"]
