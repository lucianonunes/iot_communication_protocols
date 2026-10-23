"""Fábrica de adaptadores por protocolo."""

from iotsim.adapters.base import ProtocolAdapter
from iotsim.config import DeviceSettings, Protocol, resolve_port


def build_adapter(settings: DeviceSettings, device_id: str) -> ProtocolAdapter:
    port = resolve_port(settings)
    match settings.protocol:
        case Protocol.MQTT:
            from iotsim.adapters.mqtt import MqttAdapter

            return MqttAdapter(
                device_id, settings.host, port, settings.tls, settings.ca_file, settings.mqtt_qos
            )
        case Protocol.HTTP:
            from iotsim.adapters.http import HttpAdapter

            return HttpAdapter(device_id, settings.host, port, settings.tls, settings.ca_file)
        case Protocol.COAP:
            from iotsim.adapters.coap import CoapAdapter

            return CoapAdapter(
                device_id,
                settings.host,
                port,
                settings.tls,
                settings.ca_file,
                settings.coap_confirmable,
            )
        case _:
            raise NotImplementedError(f"protocolo ainda não implementado: {settings.protocol}")


__all__ = ["ProtocolAdapter", "build_adapter"]
