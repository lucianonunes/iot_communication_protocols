"""Servidor WebSocket: aceita conexões em ``/<topic_prefix>/...`` e registra cada quadro."""

import asyncio
import logging
import signal
import ssl
import time
from collections.abc import Awaitable, Callable

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed
from websockets.frames import CloseCode

from iotsim.config import DEFAULT_PORTS, Protocol, ReceiverSettings
from iotsim.message import Reading
from iotsim.sink import ReceiveSink

log = logging.getLogger(__name__)


def _ssl_context(settings: ReceiverSettings) -> ssl.SSLContext | None:
    if not settings.tls:
        return None
    if settings.tls_cert is None or settings.tls_key is None:
        raise ValueError("TLS requer IOTSIM_TLS_CERT e IOTSIM_TLS_KEY")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(settings.tls_cert, settings.tls_key)
    return context


def make_handler(
    settings: ReceiverSettings, sink: ReceiveSink
) -> Callable[[ServerConnection], Awaitable[None]]:
    prefix = f"/{settings.topic_prefix}/"

    async def handler(connection: ServerConnection) -> None:
        if connection.request is None or not connection.request.path.startswith(prefix):
            await connection.close(CloseCode.POLICY_VIOLATION, "recurso inválido")
            return
        try:
            async for message in connection:
                recv_ns = time.time_ns()
                if not isinstance(message, bytes):
                    continue  # o simulador envia apenas quadros binários
                sink.record(Reading.decode(message), recv_ns, len(message))
        except ConnectionClosed:
            pass

    return handler


async def serve_ws(settings: ReceiverSettings, stop: asyncio.Event) -> None:
    plain, secure = DEFAULT_PORTS[Protocol.WS]
    port = settings.listen_port or (secure if settings.tls else plain)
    async with (
        ReceiveSink(Protocol.WS, settings) as sink,
        serve(
            make_handler(settings, sink),
            settings.listen_host,
            port,
            ssl=_ssl_context(settings),
            compression=None,
        ),
    ):
        log.info("servidor WebSocket em %s:%d (tls=%s)", settings.listen_host, port, settings.tls)
        await stop.wait()


async def _main(settings: ReceiverSettings) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await serve_ws(settings, stop)
    log.info("servidor WebSocket encerrado")


def main() -> None:
    settings = ReceiverSettings()
    logging.basicConfig(
        level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    asyncio.run(_main(settings))


if __name__ == "__main__":
    main()
