"""Teste de integração AMQP 1.0: requer um RabbitMQ 4.x acessível.

Executado apenas quando ``IOTSIM_TEST_AMQP_URL`` estiver definido
(ex.: ``amqp://iot:secret@localhost:5672``) e a fila ``iotsim.telemetry``
estiver ligada a ``amq.topic`` com ``sensors.#`` (ver infra/rabbitmq).
"""

import asyncio
import csv
import os
from pathlib import Path
from urllib.parse import urlparse

import pytest
from pydantic import SecretStr

from iotsim.adapters.amqp import AmqpAdapter
from iotsim.config import ReceiverSettings
from iotsim.message import Reading
from iotsim.servers.amqp_consumer import consume

URL = os.environ.get("IOTSIM_TEST_AMQP_URL")
pytestmark = pytest.mark.skipif(URL is None, reason="IOTSIM_TEST_AMQP_URL não definido")


@pytest.mark.parametrize("confirm", [True, False], ids=["liquidado", "pre-liquidado"])
async def test_amqp_roundtrip_records_reading(tmp_path: Path, confirm: bool) -> None:
    assert URL is not None
    url = urlparse(URL)
    host, port = url.hostname or "localhost", url.port or 5672
    settings = ReceiverSettings(
        data_dir=tmp_path,
        run_id="t",
        broker_host=host,
        broker_port=port,
        username=url.username,
        password=SecretStr(url.password) if url.password else None,
    )
    stop = asyncio.Event()
    consumer = asyncio.create_task(consume(settings, stop))
    await asyncio.sleep(1.0)  # aguarda o attach do receptor

    device_id = f"amqp-test-{int(confirm)}"
    adapter = AmqpAdapter(
        device_id,
        host,
        port,
        False,
        None,
        username=url.username,
        password=url.password,
        confirm=confirm,
    )
    await adapter.connect()
    try:
        for seq in range(3):
            payload = Reading(device_id, seq, 1_730_000_000_000_000_000, 23.5).encode()
            await adapter.send(f"sensors/{device_id}/temperature", payload)
    finally:
        await adapter.close()
    await asyncio.sleep(1.0)
    stop.set()
    await consumer

    with (tmp_path / "t" / "recv_amqp.csv").open() as f:
        rows = [r for r in csv.DictReader(f) if r["device_id"] == device_id]
    assert [r["seq"] for r in rows] == ["0", "1", "2"]
