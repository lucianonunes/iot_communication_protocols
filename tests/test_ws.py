import asyncio
import csv
from pathlib import Path

from websockets.asyncio.client import connect

from iotsim.adapters.ws import WsAdapter
from iotsim.config import ReceiverSettings
from iotsim.message import Reading
from iotsim.servers.ws_server import serve_ws

PORT = 58081


async def _start(tmp_path: Path) -> tuple[asyncio.Event, asyncio.Task[None]]:
    settings = ReceiverSettings(
        data_dir=tmp_path, run_id="t", listen_host="127.0.0.1", listen_port=PORT
    )
    stop = asyncio.Event()
    server = asyncio.create_task(serve_ws(settings, stop))
    await asyncio.sleep(0.2)  # aguarda o bind do socket
    return stop, server


async def test_ws_roundtrip_records_reading(tmp_path: Path) -> None:
    stop, server = await _start(tmp_path)
    adapter = WsAdapter("ws-0000", "127.0.0.1", PORT, False, None)
    await adapter.connect()
    payloads = [
        Reading("ws-0000", seq, 1_730_000_000_000_000_000, 23.5).encode() for seq in range(3)
    ]
    try:
        for payload in payloads:
            await adapter.send("sensors/ws-0000/temperature", payload)
    finally:
        await adapter.close()  # o fechamento ordenado garante a entrega dos quadros pendentes
        stop.set()
        await server

    with (tmp_path / "t" / "recv_ws.csv").open() as f:
        rows = list(csv.DictReader(f))
    assert [r["seq"] for r in rows] == ["0", "1", "2"]
    assert all(int(r["payload_bytes"]) == len(payloads[0]) for r in rows)


async def test_ws_rejects_unknown_resource(tmp_path: Path) -> None:
    stop, server = await _start(tmp_path)
    try:
        async with connect(f"ws://127.0.0.1:{PORT}/outro", proxy=None) as ws:
            await ws.wait_closed()
            assert ws.close_code == 1008
    finally:
        stop.set()
        await server
