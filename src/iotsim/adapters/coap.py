"""Dispositivo CoAP (RFC 7252): POST da leitura sobre UDP, em mensagem CON ou NON.

Com ``tls=True`` usa DTLS 1.2 com chave pré-compartilhada (PSK), via tinydtls (DTLSSocket):
é o modo de segurança típico de nós restritos (RFC 7925). Certificados X.509 não são
usados aqui porque o transporte DTLS do aiocoap só suporta PSK.
"""

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
        *,
        psk: str | None = None,
        psk_identity: str = "iotsim",
    ) -> None:
        if tls and not psk:
            raise ValueError("CoAP sobre DTLS requer a chave pré-compartilhada (IOTSIM_COAP_PSK)")
        super().__init__(device_id, host, port, tls, ca_file)
        self._tuning = Reliable if confirmable else Unreliable
        self._psk = psk
        self._psk_identity = psk_identity
        self._scheme = "coaps" if tls else "coap"
        # Mesma origem nas requisições e na chave das credenciais: o aiocoap casa a
        # credencial pela URI completa, que inclui a porta.
        self._origin = f"{self._scheme}://{host}:{port}"
        self._context: Context | None = None
        # O aiocoap guarda as sessões DTLS num WeakValueDictionary: sem uma referência forte,
        # o coletor de lixo descarta a sessão entre envios e cada envio refaz o handshake.
        self._remote: object | None = None

    async def connect(self) -> None:
        # CoAP não tem conexão: o contexto abre o socket UDP local. Com DTLS, o handshake
        # ocorre no primeiro envio e a sessão é reaproveitada nos seguintes.
        self._context = await Context.create_client_context()
        if self.tls:
            self._context.client_credentials.load_from_dict(
                {
                    f"{self._origin}/*": {
                        "dtls": {
                            "psk": {"ascii": self._psk},
                            "client-identity": {"ascii": self._psk_identity},
                        }
                    }
                }
            )

    async def send(self, resource: str, payload: bytes) -> None:
        if self._context is None:
            raise RuntimeError("connect() não foi chamado")
        request = Message(
            transport_tuning=self._tuning,
            code=POST,
            uri=f"{self._origin}/{resource}",
            payload=payload,
        )
        # Em CON, a resposta chega no ACK (piggybacked); em NON, numa mensagem NON separada.
        response = await self._context.request(request).response
        self._remote = response.remote
        if not response.code.is_successful():
            raise CoapSendError(str(response.code))

    async def close(self) -> None:
        self._remote = None
        if self._context is not None:
            await self._context.shutdown()
            self._context = None
