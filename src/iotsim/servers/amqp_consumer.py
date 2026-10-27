"""Consumidor AMQP 1.0: lê a fila ligada a ``amq.topic`` (``sensors.#``) e registra a chegada.

Como no MQTT, a latência inclui dois saltos (dispositivo → broker → consumidor).
O cliente proton é bloqueante; o laço de recepção roda numa thread dedicada e
entrega cada registro ao event loop, onde o ``ReceiveSink`` é usado.
"""

import asyncio
import logging
import signal
import threading
import time

from proton import SSLDomain, Timeout
from proton.utils import BlockingConnection

from iotsim.config import DEFAULT_PORTS, Protocol, ReceiverSettings
from iotsim.message import Reading
from iotsim.sink import ReceiveSink

log = logging.getLogger(__name__)

RECONNECT_DELAY_S = 2.0
PREFETCH = 500  # créditos concedidos ao broker (controle de fluxo do AMQP 1.0)


def _open(settings: ReceiverSettings) -> BlockingConnection:
    plain, secure = DEFAULT_PORTS[Protocol.AMQP]
    port = settings.broker_port or (secure if settings.tls else plain)
    scheme = "amqps" if settings.tls else "amqp"
    ssl_domain = None
    if settings.tls:
        ssl_domain = SSLDomain(SSLDomain.MODE_CLIENT)
        if settings.ca_file:
            ssl_domain.set_trusted_ca_db(str(settings.ca_file))
        ssl_domain.set_peer_authentication(SSLDomain.VERIFY_PEER_NAME)
    password = settings.password.get_secret_value() if settings.password else None
    return BlockingConnection(
        f"{scheme}://{settings.broker_host}:{port}",
        ssl_domain=ssl_domain,
        allowed_mechs="PLAIN" if settings.username else "ANONYMOUS",
        user=settings.username,
        password=password,
    )


def _receive_loop(
    settings: ReceiverSettings,
    sink: ReceiveSink,
    loop: asyncio.AbstractEventLoop,
    stop: threading.Event,
) -> None:
    while not stop.is_set():
        try:
            connection = _open(settings)
            try:
                receiver = connection.create_receiver(
                    f"/queues/{settings.amqp_queue}", credit=PREFETCH
                )
                log.info("consumindo a fila %s em %s", settings.amqp_queue, settings.broker_host)
                while not stop.is_set():
                    try:
                        message = receiver.receive(timeout=1.0)
                    except Timeout:
                        continue
                    recv_ns = time.time_ns()
                    body: object = message.body
                    # Corpo em seção de dados binária; o proton pode entregá-lo como memoryview.
                    if not isinstance(body, bytes | bytearray | memoryview):
                        log.warning("corpo AMQP inesperado: %s", type(body).__name__)
                        receiver.reject()
                        continue
                    payload = bytes(body)
                    try:
                        reading = Reading.decode(payload)
                    except (ValueError, KeyError, TypeError) as exc:
                        # Rejeitada (descartada): sem isto, a mensagem voltaria à fila e
                        # derrubaria o consumidor a cada nova entrega.
                        log.warning("mensagem inválida descartada: %r", exc)
                        receiver.reject()
                        continue
                    loop.call_soon_threadsafe(sink.record, reading, recv_ns, len(payload))
                    receiver.accept()
            finally:
                connection.close()
        except Exception as exc:
            if stop.is_set():
                break
            log.warning("conexão com o broker perdida: %s", exc)
            stop.wait(RECONNECT_DELAY_S)


async def consume(settings: ReceiverSettings, stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    thread_stop = threading.Event()
    async with ReceiveSink(Protocol.AMQP, settings) as sink:
        worker = asyncio.create_task(
            asyncio.to_thread(_receive_loop, settings, sink, loop, thread_stop)
        )
        await stop.wait()
        thread_stop.set()
        await worker


async def _main(settings: ReceiverSettings) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await consume(settings, stop)
    log.info("consumidor encerrado")


def main() -> None:
    settings = ReceiverSettings()
    logging.basicConfig(
        level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    asyncio.run(_main(settings))


if __name__ == "__main__":
    main()
