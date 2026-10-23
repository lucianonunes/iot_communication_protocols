import asyncio
import csv
from pathlib import Path

import pytest

from iotsim.adapters.coap import CoapAdapter
from iotsim.config import ReceiverSettings
from iotsim.message import Reading
from iotsim.servers.coap_server import serve

PORT = 56830  # porta alta para não conflitar com um servidor CoAP local


@pytest.mark.parametrize("confirmable", [False, True], ids=["NON", "CON"])
async def test_coap_roundtrip_records_reading(tmp_path: Path, confirmable: bool) -> None:
    settings = ReceiverSettings(
        data_dir=tmp_path, run_id="t", listen_host="127.0.0.1", listen_port=PORT
    )
    stop = asyncio.Event()
    server = asyncio.create_task(serve(settings, stop))
    await asyncio.sleep(0.2)  # aguarda o bind do socket UDP

    adapter = CoapAdapter("coap-0000", "127.0.0.1", PORT, False, None, confirmable)
    await adapter.connect()
    payloads = [
        Reading("coap-0000", seq, 1_730_000_000_000_000_000, 23.5).encode() for seq in range(3)
    ]
    try:
        for payload in payloads:
            await asyncio.wait_for(adapter.send("sensors/coap-0000/temperature", payload), 5)
    finally:
        await adapter.close()
        stop.set()
        await server

    with (tmp_path / "t" / "recv_coap.csv").open() as f:
        rows = list(csv.DictReader(f))
    assert [r["seq"] for r in rows] == ["0", "1", "2"]
    assert all(int(r["payload_bytes"]) == len(payloads[0]) for r in rows)


def test_coap_tls_not_supported_yet() -> None:
    with pytest.raises(NotImplementedError):
        CoapAdapter("coap-0000", "127.0.0.1", PORT, True, None)
