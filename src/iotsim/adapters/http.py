"""Dispositivo HTTP/HTTPS: POST da leitura com conexão persistente (keep-alive)."""

from pathlib import Path

import httpx

from iotsim.adapters.base import ProtocolAdapter


class HttpAdapter(ProtocolAdapter):
    def __init__(
        self, device_id: str, host: str, port: int, tls: bool, ca_file: Path | None
    ) -> None:
        super().__init__(device_id, host, port, tls, ca_file)
        scheme = "https" if tls else "http"
        self._client = httpx.AsyncClient(
            base_url=f"{scheme}://{host}:{port}",
            verify=self.ssl_context() or False,
            headers={"Content-Type": "application/json"},
        )

    async def connect(self) -> None:
        # HTTP não tem conexão explícita na aplicação; o TCP/TLS abre no primeiro envio.
        return None

    async def send(self, resource: str, payload: bytes) -> None:
        response = await self._client.post(f"/{resource}", content=payload)
        response.raise_for_status()

    async def close(self) -> None:
        await self._client.aclose()
