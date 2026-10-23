import asyncio
import csv
from pathlib import Path
from typing import ClassVar

import pytest

from iotsim.adapters.base import ProtocolAdapter
from iotsim.config import DeviceSettings, Protocol
from iotsim.devices.runner import run_fleet
from iotsim.message import Reading


class FakeAdapter(ProtocolAdapter):
    """Adaptador em memória; falha no envio de seq definido para testar reconexão."""

    sent: ClassVar[list[tuple[str, Reading]]] = []
    fail_seq: ClassVar[int | None] = None

    async def connect(self) -> None:
        return None

    async def send(self, resource: str, payload: bytes) -> None:
        reading = Reading.decode(payload)
        if reading.seq == self.fail_seq:
            raise ConnectionError("falha simulada")
        FakeAdapter.sent.append((resource, reading))

    async def close(self) -> None:
        return None


def fake_factory(settings: DeviceSettings, device_id: str) -> ProtocolAdapter:
    return FakeAdapter(device_id, settings.host, 0, False, None)


@pytest.fixture(autouse=True)
def _reset() -> None:
    FakeAdapter.sent = []
    FakeAdapter.fail_seq = None


async def _run(settings: DeviceSettings, seconds: float) -> None:
    stop = asyncio.Event()
    asyncio.get_running_loop().call_later(seconds, stop.set)
    await run_fleet(settings, stop, fake_factory)


def _settings(tmp_path: Path) -> DeviceSettings:
    return DeviceSettings(
        protocol=Protocol.MQTT, devices=3, interval_s=0.05, data_dir=tmp_path, run_id="t"
    )


async def test_fleet_sends_from_all_devices_and_logs(tmp_path: Path) -> None:
    await _run(_settings(tmp_path), 0.5)

    devices = {r.device_id for _, r in FakeAdapter.sent}
    assert devices == {"mqtt-0000", "mqtt-0001", "mqtt-0002"}
    assert all(res == f"sensors/{r.device_id}/temperature" for res, r in FakeAdapter.sent)

    with (tmp_path / "t" / "sent_mqtt.csv").open() as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == len(FakeAdapter.sent)
    assert all(row["ok"] == "1" for row in rows)


async def test_send_failure_is_logged_and_device_continues(tmp_path: Path) -> None:
    FakeAdapter.fail_seq = 1
    await _run(_settings(tmp_path), 0.5)

    with (tmp_path / "t" / "sent_mqtt.csv").open() as f:
        rows = list(csv.DictReader(f))
    failed = [r for r in rows if r["ok"] == "0"]
    assert {r["seq"] for r in failed} == {"1"}
    assert all(r["error"] == "ConnectionError" for r in failed)
    assert any(int(r["seq"]) > 1 and r["ok"] == "1" for r in rows)
