"""TLS de ponta a ponta com a CA de laboratório (scripts/gen-certs.sh), sem broker externo.

Cobre HTTPS e WSS, que não dependem de broker. MQTT e AMQP sobre TLS são validados na pilha
Docker; CoAP sobre DTLS requer DTLSSocket (extra ``dtls``, instalado na imagem Docker).
"""

import asyncio
import csv
import shutil
import subprocess
from pathlib import Path

import pytest
import uvicorn

from iotsim.adapters.http import HttpAdapter
from iotsim.adapters.ws import WsAdapter
from iotsim.config import ReceiverSettings
from iotsim.message import Reading
from iotsim.servers.http_server import create_app
from iotsim.servers.ws_server import serve_ws

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl ausente")


@pytest.fixture(scope="module")
def certs(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("certs")
    subprocess.run(
        [str(ROOT / "scripts" / "gen-certs.sh"), str(out)], check=True, capture_output=True
    )
    return out


def _settings(tmp_path: Path, certs: Path, port: int) -> ReceiverSettings:
    return ReceiverSettings(
        data_dir=tmp_path,
        run_id="t",
        listen_host="127.0.0.1",
        listen_port=port,
        tls=True,
        tls_cert=certs / "server.crt",
        tls_key=certs / "server.key",
    )


def _seqs(path: Path) -> list[str]:
    with path.open() as f:
        return [r["seq"] for r in csv.DictReader(f)]


def _payloads(device_id: str) -> list[bytes]:
    return [Reading(device_id, s, 1_730_000_000_000_000_000, 23.5).encode() for s in range(3)]


async def test_https_roundtrip(tmp_path: Path, certs: Path) -> None:
    port = 58443
    settings = _settings(tmp_path, certs, port)
    config = uvicorn.Config(
        create_app(settings),
        host="127.0.0.1",
        port=port,
        ssl_certfile=str(settings.tls_cert),
        ssl_keyfile=str(settings.tls_key),
        log_level="warning",
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    # uvicorn.Server só expõe a flag `started`, sem evento a aguardar.
    while not server.started:  # noqa: ASYNC110
        await asyncio.sleep(0.05)

    adapter = HttpAdapter("http-0000", "localhost", port, True, certs / "ca.crt")
    try:
        for payload in _payloads("http-0000"):
            await adapter.send("sensors/http-0000/temperature", payload)
    finally:
        await adapter.close()
        server.should_exit = True
        await task
    assert _seqs(tmp_path / "t" / "recv_http.csv") == ["0", "1", "2"]


async def test_wss_roundtrip(tmp_path: Path, certs: Path) -> None:
    port = 58444
    stop = asyncio.Event()
    server = asyncio.create_task(serve_ws(_settings(tmp_path, certs, port), stop))
    await asyncio.sleep(0.2)

    adapter = WsAdapter("ws-0000", "localhost", port, True, certs / "ca.crt")
    try:
        for payload in _payloads("ws-0000"):
            await adapter.send("sensors/ws-0000/temperature", payload)
    finally:
        await adapter.close()
        stop.set()
        await server
    assert _seqs(tmp_path / "t" / "recv_ws.csv") == ["0", "1", "2"]


async def test_tls_rejects_untrusted_ca(tmp_path: Path, certs: Path) -> None:
    port = 58445
    stop = asyncio.Event()
    server = asyncio.create_task(serve_ws(_settings(tmp_path, certs, port), stop))
    await asyncio.sleep(0.2)

    adapter = WsAdapter("ws-0000", "localhost", port, True, None)  # CA do sistema
    try:
        with pytest.raises(Exception, match="CERTIFICATE_VERIFY_FAILED"):
            await adapter.send("sensors/ws-0000/temperature", _payloads("ws-0000")[0])
    finally:
        await adapter.close()
        stop.set()
        await server
