"""Ponto de entrada do simulador: ``iotsim-device`` (configurado por variáveis IOTSIM_*)."""

import asyncio
import logging
import signal

from iotsim.config import DeviceSettings
from iotsim.devices.runner import run_fleet

log = logging.getLogger(__name__)


async def _main(settings: DeviceSettings) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    if settings.duration_s is not None:
        loop.call_later(settings.duration_s, stop.set)

    log.info(
        "iniciando %d dispositivo(s) %s -> %s (tls=%s, run=%s)",
        settings.devices,
        settings.protocol.value,
        settings.host,
        settings.tls,
        settings.run_id,
    )
    await run_fleet(settings, stop)
    log.info("simulação encerrada")


def main() -> None:
    settings = DeviceSettings()  # type: ignore[call-arg]  # campos vêm do ambiente
    logging.basicConfig(
        level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    # O httpx registra cada requisição em INFO; com N dispositivos isso inunda o log.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(_main(settings))


if __name__ == "__main__":
    main()
