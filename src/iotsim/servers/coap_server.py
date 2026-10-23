"""Servidor CoAP que recebe as leituras por POST em ``/<topic_prefix>/...`` e registra a chegada."""

import asyncio
import logging
import signal
import socket
import time

from aiocoap import CHANGED, Context, Message
from aiocoap.credentials import CredentialsMap
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


async def _create_context(settings: ReceiverSettings, site: Site) -> tuple[Context, str, int]:
    plain, secure = DEFAULT_PORTS[Protocol.COAP]
    if not settings.tls:
        port = settings.listen_port or plain
        context = await Context.create_server_context(site, bind=(settings.listen_host, port))
        return context, "udp", port

    if settings.coap_psk is None:
        raise ValueError("CoAP sobre DTLS requer a chave pré-compartilhada (IOTSIM_COAP_PSK)")
    credentials = CredentialsMap()
    credentials.load_from_dict(
        {
            ":device": {
                "dtls": {
                    "psk": {"ascii": settings.coap_psk.get_secret_value()},
                    "client-identity": {"ascii": settings.coap_psk_identity},
                }
            }
        }
    )
    port = settings.listen_port or secure
    # O transporte DTLS do aiocoap não aceita o endereço genérico (precisa responder do mesmo
    # endereço em que recebeu); no container, usa-se o IP do próprio host na rede Docker.
    host = settings.listen_host
    if host in ("0.0.0.0", "::", ""):
        host = socket.gethostbyname(socket.gethostname())
    # O transporte DTLS do aiocoap soma (5684 - 5683) à porta de bind; por isso o bind
    # recebe a porta desejada menos esse deslocamento.
    context = await Context.create_server_context(
        site,
        bind=(host, port - (secure - plain)),
        server_credentials=credentials,
        transports=["tinydtls_server"],
    )
    return context, "dtls", port


async def serve(settings: ReceiverSettings, stop: asyncio.Event) -> None:
    async with ReceiveSink(Protocol.COAP, settings) as sink:
        context, transport, port = await _create_context(settings, build_site(settings, sink))
        log.info("servidor CoAP em %s://%s:%d", transport, settings.listen_host, port)
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
