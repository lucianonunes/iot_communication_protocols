"""Execução concorrente de N dispositivos simulados de um mesmo protocolo."""

import asyncio
import contextlib
import logging
import random
import time
from collections.abc import Callable

from iotsim.adapters import ProtocolAdapter, build_adapter
from iotsim.config import DeviceSettings
from iotsim.message import Reading
from iotsim.sensors import TemperatureSensor
from iotsim.sink import SENT_HEADER, CsvRecorder

log = logging.getLogger(__name__)

AdapterFactory = Callable[[DeviceSettings, str], ProtocolAdapter]

SEND_TIMEOUT_S = 10.0
CONNECT_ATTEMPTS = 5


def device_id_for(settings: DeviceSettings, index: int) -> str:
    prefix = settings.device_id_prefix or settings.protocol.value
    return f"{prefix}-{index:04d}"


def resource_for(settings: DeviceSettings, device_id: str) -> str:
    # Mesmo nome de recurso para todos os protocolos (tópico MQTT, caminho HTTP/CoAP etc.).
    return f"{settings.topic_prefix}/{device_id}/temperature"


async def connect_with_retry(adapter: ProtocolAdapter, stop: asyncio.Event) -> bool:
    for attempt in range(1, CONNECT_ATTEMPTS + 1):
        try:
            await adapter.connect()
            return True
        except Exception as exc:
            log.warning(
                "%s: conexão falhou (%d/%d): %s", adapter.device_id, attempt, CONNECT_ATTEMPTS, exc
            )
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=2.0**attempt)
            if stop.is_set():
                break
    return False


async def run_device(
    settings: DeviceSettings,
    index: int,
    recorder: CsvRecorder,
    stop: asyncio.Event,
    factory: AdapterFactory = build_adapter,
) -> None:
    device_id = device_id_for(settings, index)
    resource = resource_for(settings, device_id)
    sensor = TemperatureSensor(seed=settings.seed + index)
    # Defasagem inicial determinística: evita que todos os dispositivos enviem no mesmo instante.
    offset = random.Random(settings.seed * 1_000_003 + index).uniform(0, settings.interval_s)

    adapter = factory(settings, device_id)
    if not await connect_with_retry(adapter, stop):
        log.error("%s: desistiu de conectar", device_id)
        return

    start = time.monotonic() + offset
    seq = 0
    needs_reconnect = False
    try:
        while not stop.is_set():
            if settings.max_messages is not None and seq >= settings.max_messages:
                break
            # Agendamento sem deriva: o instante k é start + k * intervalo.
            delay = start + seq * settings.interval_s - time.monotonic()
            if delay > 0:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=delay)
                if stop.is_set():
                    break

            if needs_reconnect:
                with contextlib.suppress(Exception):
                    await adapter.close()
                adapter = factory(settings, device_id)
                needs_reconnect = not await connect_with_retry(adapter, stop)

            reading = Reading(device_id, seq, time.time_ns(), sensor.read())
            payload = reading.encode()
            ok, error = True, ""
            try:
                await asyncio.wait_for(adapter.send(resource, payload), SEND_TIMEOUT_S)
            except Exception as exc:
                ok, error = False, type(exc).__name__
                needs_reconnect = True
            recorder.write(
                (
                    settings.run_id,
                    settings.protocol.value,
                    device_id,
                    seq,
                    reading.sent_ns,
                    time.time_ns(),
                    len(payload),
                    int(ok),
                    error,
                )
            )
            seq += 1
    finally:
        with contextlib.suppress(Exception):
            await adapter.close()


async def run_fleet(
    settings: DeviceSettings, stop: asyncio.Event, factory: AdapterFactory = build_adapter
) -> None:
    path = settings.data_dir / settings.run_id / f"sent_{settings.protocol.value}.csv"
    recorder = CsvRecorder(path, SENT_HEADER)

    async def flush_periodically() -> None:
        while not stop.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=1.0)
            recorder.flush()

    try:
        async with asyncio.TaskGroup() as tg:
            flusher = tg.create_task(flush_periodically())
            devices = [
                tg.create_task(run_device(settings, index, recorder, stop, factory))
                for index in range(settings.devices)
            ]
            # Com max_messages os dispositivos terminam sozinhos; o flush periódico,
            # que só para com `stop`, é então cancelado.
            await asyncio.gather(*devices)
            flusher.cancel()
    finally:
        recorder.close()
