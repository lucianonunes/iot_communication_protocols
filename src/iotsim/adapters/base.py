"""Interface comum a todos os protocolos: uma instância por dispositivo simulado."""

import ssl
from abc import ABC, abstractmethod
from pathlib import Path


class ProtocolAdapter(ABC):
    """Contrato mínimo de um dispositivo: conectar, enviar uma leitura, encerrar.

    ``send`` só retorna quando o envio é considerado concluído pelo protocolo
    (ex.: PUBACK no MQTT QoS 1, resposta HTTP), o que permite medir o tempo de confirmação.
    """

    def __init__(self, device_id: str, host: str, port: int, tls: bool, ca_file: Path | None):
        self.device_id = device_id
        self.host = host
        self.port = port
        self.tls = tls
        self.ca_file = ca_file

    def ssl_context(self) -> ssl.SSLContext | None:
        if not self.tls:
            return None
        return ssl.create_default_context(cafile=str(self.ca_file) if self.ca_file else None)

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def send(self, resource: str, payload: bytes) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...
