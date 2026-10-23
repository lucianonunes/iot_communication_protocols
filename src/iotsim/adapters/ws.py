"""Dispositivo WebSocket (RFC 6455): quadros binários sobre uma conexão persistente.

O recurso é fixado no handshake (caminho da URI) e cada quadro leva apenas a carga útil,
como na estimativa do Quadro 2 da Nota 2. O WebSocket não tem confirmação nativa:
``send`` retorna quando o quadro é entregue ao transporte (TCP).
"""

from pathlib import Path

from websockets.asyncio.client import ClientConnection, connect

from iotsim.adapters.base import ProtocolAdapter


class WsAdapter(ProtocolAdapter):
    def __init__(
        self, device_id: str, host: str, port: int, tls: bool, ca_file: Path | None
    ) -> None:
        super().__init__(device_id, host, port, tls, ca_file)
        self._connection: ClientConnection | None = None
        self._resource: str | None = None

    async def connect(self) -> None:
        # O handshake depende do recurso; ele é feito no primeiro envio (como no HTTP).
        return None

    async def _open(self, resource: str) -> ClientConnection:
        scheme = "wss" if self.tls else "ws"
        return await connect(
            f"{scheme}://{self.host}:{self.port}/{resource}",
            ssl=self.ssl_context(),
            compression=None,  # sem permessage-deflate: quadros comparáveis ao Quadro 2
            proxy=None,  # ignora proxies do ambiente
        )

    async def send(self, resource: str, payload: bytes) -> None:
        if self._connection is None or self._resource != resource:
            await self.close()
            self._connection = await self._open(resource)
            self._resource = resource
        await self._connection.send(payload)

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()
            self._connection = None
            self._resource = None
