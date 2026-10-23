"""Servidor CoAP que recebe as leituras por POST em ``/<topic_prefix>/...`` e registra a chegada."""

import asyncio
import logging
import signal
import time

from aiocoap import CHANGED, Context, Message
from aiocoap.resource import PathCapable, Resource, Site

from iotsim.config import DEFAULT_PORTS, Protocol, ReceiverSettings
from iotsim.message import Reading
from iotsim.sink import ReceiveSink

log = logging.getLogger(__name__)


class TelemetryResource(Resource, PathCapable):
    """Aceita qualquer sub-caminho (ex.: sensors/coap-0001/temperature)."""

    def __init__(self, sink: ReceiveSink) -> None:
        super().__init__()
        self._sink = sink

    async def render_post(self, request: Message) -> Message:
        recv_ns = time.time_ns()
        payload = request.payload
        self._sink.record(Reading.decode(payload), recv_ns, len(payload))
        return Message(code=CHANGED)


def build_site(settings: ReceiverSettings, sink: ReceiveSink) -> Site:
    site = Site()
    site.add_resource((settings.topic_prefix,), TelemetryResource(sink))
    return site


async def serve(settings: ReceiverSettings, stop: asyncio.Event) -> None:
    if settings.tls:
        raise NotImplementedError("CoAP sobre DTLS ainda não suportado")
    plain, _ = DEFAULT_PORTS[Protocol.COAP]
    port = settings.listen_port or plain
    async with ReceiveSink(Protocol.COAP, settings) as sink:
        context = await Context.create_server_context(
            build_site(settings, sink), bind=(settings.listen_host, port)
        )
        log.info("servidor CoAP em udp://%s:%d", settings.listen_host, port)
        try:
            await stop.wait()
        finally:
            await context.shutdown()


async def _main(settings: ReceiverSettings) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await serve(settings, stop)
    log.info("servidor CoAP encerrado")


def main() -> None:
    settings = ReceiverSettings()
    logging.basicConfig(
        level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    asyncio.run(_main(settings))


if __name__ == "__main__":
    main()
