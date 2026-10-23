# Imagem única para simuladores e receptores; o comando define o papel do container.
# Estágio de build: compila as dependências sem wheel para Linux:
# - python-qpid-proton (AMQP 1.0), com TLS (OpenSSL) e SASL (Cyrus), detectados via pkg-config;
# - DTLSSocket (tinydtls, DTLS do CoAP), que usa autotools.
FROM python:3.12-slim-bookworm AS build

COPY --from=ghcr.io/astral-sh/uv:0.11.19 /uv /uvx /bin/

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libc6-dev pkg-config libssl-dev libsasl2-dev \
       autoconf automake autoconf-archive libtool make \
    && rm -rf /var/lib/apt/lists/*

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependências primeiro (camada em cache enquanto o lockfile não muda).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra dtls --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev --extra dtls --no-editable

# Estágio final: apenas bibliotecas de runtime, sem compilador.
FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends libssl3 libsasl2-2 libsasl2-modules \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app
COPY --from=build /app/.venv /app/.venv

RUN useradd --create-home --uid 1000 iotsim && mkdir -p /app/data && chown iotsim /app/data
USER iotsim

CMD ["iotsim-device"]
