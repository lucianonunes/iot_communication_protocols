"""Dispositivo MQTT: publica no broker com o QoS configurado (OASIS MQTT 3.1.1/5.0)."""

from pathlib import Path

import aiomqtt

from iotsim.adapters.base import ProtocolAdapter


class MqttAdapter(ProtocolAdapter):
    def __init__(
        self,
        device_id: str,
        host: str,
        port: int,
        tls: bool,
        ca_file: Path | None,
        qos: int = 0,
    ) -> None:
        super().__init__(device_id, host, port, tls, ca_file)
        self._qos = qos
        self._client = aiomqtt.Client(
            hostname=host,
            port=port,
            identifier=device_id,
            tls_context=self.ssl_context(),
        )

    async def connect(self) -> None:
        await self._client.__aenter__()

    async def send(self, resource: str, payload: bytes) -> None:
        await self._client.publish(resource, payload, qos=self._qos)

    async def close(self) -> None:
        await self._client.__aexit__(None, None, None)
