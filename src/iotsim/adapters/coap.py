"""Dispositivo CoAP (RFC 7252): POST da leitura sobre UDP, em mensagem CON ou NON."""

from pathlib import Path

from aiocoap import POST, Context, Message, Reliable, Unreliable

from iotsim.adapters.base import ProtocolAdapter


class CoapSendError(Exception):
    """Resposta CoAP sem código de sucesso (2.xx)."""


class CoapAdapter(ProtocolAdapter):
    def __init__(
        self,
        device_id: str,
        host: str,
        port: int,
        tls: bool,
        ca_file: Path | None,
        confirmable: bool = False,
    ) -> None:
        if tls:
            # DTLS no servidor aiocoap depende do módulo DTLSSocket; avaliado em fase própria.
            raise NotImplementedError("CoAP sobre DTLS ainda não suportado")
        super().__init__(device_id, host, port, tls, ca_file)
        self._tuning = Reliable if confirmable else Unreliable
        self._context: Context | None = None

    async def connect(self) -> None:
        # CoAP não tem conexão: o contexto apenas abre o socket UDP local.
        self._context = await Context.create_client_context()

    async def send(self, resource: str, payload: bytes) -> None:
        if self._context is None:
            raise RuntimeError("connect() não foi chamado")
        request = Message(
            transport_tuning=self._tuning,
            code=POST,
            uri=f"coap://{self.host}:{self.port}/{resource}",
            payload=payload,
        )
        # Em CON, a resposta chega no ACK (piggybacked); em NON, numa mensagem NON separada.
        response = await self._context.request(request).response
        if not response.code.is_successful():
            raise CoapSendError(str(response.code))

    async def close(self) -> None:
        if self._context is not None:
            await self._context.shutdown()
            self._context = None
