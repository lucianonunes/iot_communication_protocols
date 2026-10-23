"""Consumidor MQTT: assina todos os tópicos dos sensores no broker e registra a chegada.

A latência medida no MQTT inclui os dois saltos do modelo pub/sub
(dispositivo → broker → assinante), ao contrário do HTTP (dispositivo → servidor).
"""

import asyncio
import logging
import signal
import ssl
import time

import aiomqtt

from iotsim.config import DEFAULT_PORTS, Protocol, ReceiverSettings
from iotsim.message import Reading
from iotsim.sink import ReceiveSink

log = logging.getLogger(__name__)

RECONNECT_DELAY_S = 2.0


async def consume(settings: ReceiverSettings, stop: asyncio.Event) -> None:
    plain, secure = DEFAULT_PORTS[Protocol.MQTT]
    port = settings.broker_port or (secure if settings.tls else plain)
    tls_context = (
        ssl.create_default_context(cafile=str(settings.ca_file) if settings.ca_file else None)
        if settings.tls
        else None
    )
    async with ReceiveSink(Protocol.MQTT, settings) as sink:
        while not stop.is_set():
            try:
                async with aiomqtt.Client(
                    settings.broker_host,
                    port,
                    identifier="iotsim-consumer",
                    tls_context=tls_context,
                ) as client:
                    # QoS 2 na assinatura: o QoS efetivo passa a ser o escolhido pelo publicador.
                    await client.subscribe(f"{settings.topic_prefix}/#", qos=2)
                    log.info(
                        "assinado em %s/# no broker %s:%d",
                        settings.topic_prefix,
                        settings.broker_host,
                        port,
                    )
                    async for message in client.messages:
                        recv_ns = time.time_ns()
                        payload = message.payload
                        if not isinstance(payload, bytes | bytearray):
                            continue
                        sink.record(Reading.decode(payload), recv_ns, len(payload))
            except aiomqtt.MqttError as exc:
                log.warning("conexão com o broker perdida: %s", exc)
                await asyncio.sleep(RECONNECT_DELAY_S)


async def _main(settings: ReceiverSettings) -> None:
    stop = asyncio.Event()
    task = asyncio.create_task(consume(settings, stop))
    loop = asyncio.get_running_loop()

    def shutdown() -> None:
        stop.set()
        task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown)
    try:
        await task
    except asyncio.CancelledError:
        log.info("consumidor encerrado")


def main() -> None:
    settings = ReceiverSettings()
    logging.basicConfig(
        level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    asyncio.run(_main(settings))


if __name__ == "__main__":
    main()
