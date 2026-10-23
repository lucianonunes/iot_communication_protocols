"""Dispositivo AMQP 1.0 (OASIS/ISO 19464): envio ao exchange ``amq.topic`` do RabbitMQ 4.x.

O cliente qpid-proton usado aqui é síncrono (``BlockingConnection``). Cada dispositivo usa
uma thread própria e exclusiva, pois uma conexão proton não pode ser usada por várias threads.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from proton import Message, SSLDomain
from proton.reactor import AtLeastOnce, AtMostOnce
from proton.utils import BlockingConnection, BlockingSender

from iotsim.adapters.base import ProtocolAdapter

# Limita operações bloqueantes do proton; sem isso a thread do dispositivo poderia travar.
OPERATION_TIMEOUT_S = 10.0


class AmqpAdapter(ProtocolAdapter):
    def __init__(
        self,
        device_id: str,
        host: str,
        port: int,
        tls: bool,
        ca_file: Path | None,
        *,
        username: str | None = None,
        password: str | None = None,
        confirm: bool = True,
    ) -> None:
        super().__init__(device_id, host, port, tls, ca_file)
        self._username = username
        self._password = password
        # confirm=True: liquidação pelo broker (pelo menos uma vez);
        # confirm=False: mensagem pré-liquidada (no máximo uma vez).
        self._confirm = confirm
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=device_id)
        self._connection: BlockingConnection | None = None
        self._senders: dict[str, BlockingSender] = {}

    async def _run(self, fn: Any, *args: Any) -> Any:
        return await asyncio.get_running_loop().run_in_executor(self._executor, fn, *args)

    def _connect_sync(self) -> None:
        scheme = "amqps" if self.tls else "amqp"
        kwargs: dict[str, Any] = {"allowed_mechs": "PLAIN" if self._username else "ANONYMOUS"}
        if self._username:
            kwargs["user"] = self._username
            kwargs["password"] = self._password
        if self.tls:
            domain = SSLDomain(SSLDomain.MODE_CLIENT)
            if self.ca_file:
                domain.set_trusted_ca_db(str(self.ca_file))
            domain.set_peer_authentication(SSLDomain.VERIFY_PEER_NAME)
            kwargs["ssl_domain"] = domain
        self._connection = BlockingConnection(
            f"{scheme}://{self.host}:{self.port}", timeout=OPERATION_TIMEOUT_S, **kwargs
        )

    def _send_sync(self, resource: str, payload: bytes) -> None:
        if self._connection is None:
            raise RuntimeError("connect() não foi chamado")
        sender = self._senders.get(resource)
        if sender is None:
            # Endereço v2 do RabbitMQ 4: /exchanges/<exchange>/<routing-key>.
            # O destino é fixado no attach do enlace, não em cada mensagem (Quadro 2 da Nota 2).
            routing_key = resource.replace("/", ".")
            sender = self._connection.create_sender(
                f"/exchanges/amq.topic/{routing_key}",
                options=AtLeastOnce() if self._confirm else AtMostOnce(),
            )
            self._senders[resource] = sender
        # Com AtLeastOnce, send() bloqueia até o broker liquidar (accepted) a entrega.
        sender.send(Message(body=payload, inferred=True))

    def _close_sync(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None
            self._senders.clear()

    async def connect(self) -> None:
        await self._run(self._connect_sync)

    async def send(self, resource: str, payload: bytes) -> None:
        await self._run(self._send_sync, resource, payload)

    async def close(self) -> None:
        try:
            await self._run(self._close_sync)
        finally:
            self._executor.shutdown(wait=False)
