"""Servidor HTTP/HTTPS que recebe as leituras por POST e registra a chegada."""

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request, Response

from iotsim.config import DEFAULT_PORTS, Protocol, ReceiverSettings
from iotsim.message import Reading
from iotsim.sink import ReceiveSink

log = logging.getLogger(__name__)


def create_app(settings: ReceiverSettings) -> FastAPI:
    sink = ReceiveSink(Protocol.HTTP, settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        async with sink:
            yield

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post(f"/{settings.topic_prefix}/{{path:path}}", status_code=204)
    async def ingest(request: Request) -> Response:
        body = await request.body()
        recv_ns = time.time_ns()
        sink.record(Reading.decode(body), recv_ns, len(body))
        return Response(status_code=204)

    return app


def main() -> None:
    settings = ReceiverSettings()
    logging.basicConfig(
        level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    plain, secure = DEFAULT_PORTS[Protocol.HTTP]
    port = settings.listen_port or (secure if settings.tls else plain)
    uvicorn.run(
        create_app(settings),
        host=settings.listen_host,
        port=port,
        ssl_certfile=str(settings.tls_cert) if settings.tls and settings.tls_cert else None,
        ssl_keyfile=str(settings.tls_key) if settings.tls and settings.tls_key else None,
        access_log=False,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
