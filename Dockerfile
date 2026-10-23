# Imagem única para simuladores e receptores; o comando define o papel do container.
FROM python:3.12-slim-bookworm AS base

COPY --from=ghcr.io/astral-sh/uv:0.11.19 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependências primeiro (camada em cache enquanto o lockfile não muda).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

RUN useradd --create-home --uid 1000 iotsim && mkdir -p /app/data && chown iotsim /app/data
USER iotsim

CMD ["iotsim-device"]
