"""Configuração tipada, lida de variáveis de ambiente com prefixo ``IOTSIM_``."""

from enum import StrEnum
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Protocol(StrEnum):
    MQTT = "mqtt"
    COAP = "coap"
    HTTP = "http"
    AMQP = "amqp"
    WS = "ws"


class CommonSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="IOTSIM_", env_file=".env", extra="ignore")

    run_id: str = Field(default="dev", description="Identificador da execução/experimento")
    data_dir: Path = Path("data")
    log_level: str = "INFO"


class DeviceSettings(CommonSettings):
    """Parâmetros de um simulador (um container = um protocolo, N dispositivos)."""

    protocol: Protocol
    devices: int = Field(default=10, ge=1)
    interval_s: float = Field(default=1.0, gt=0)
    duration_s: float | None = Field(default=None, gt=0, description="None = sem fim")
    sleepy: bool = Field(
        default=False,
        description="Dispositivo sonolento: conecta, envia uma leitura e desconecta a cada ciclo",
    )
    max_messages: int | None = Field(
        default=None, ge=1, description="Encerra cada dispositivo após N envios (None = sem fim)"
    )
    seed: int = 42
    device_id_prefix: str | None = Field(
        default=None, description="Prefixo dos IDs (padrão: nome do protocolo)"
    )
    host: str = "localhost"
    port: int | None = None
    tls: bool = False
    ca_file: Path | None = Field(default=None, description="CA para validar o servidor (TLS)")
    topic_prefix: str = "sensors"
    mqtt_qos: int = Field(default=0, ge=0, le=2)
    coap_confirmable: bool = Field(
        default=False, description="CoAP: CON (com ACK e retransmissão) ou NON"
    )
    amqp_confirm: bool = Field(
        default=True, description="AMQP: aguarda a liquidação pelo broker ou envia pré-liquidado"
    )
    username: str | None = None
    password: SecretStr | None = None
    coap_psk: SecretStr | None = Field(
        default=None, description="CoAP/DTLS: chave pré-compartilhada"
    )
    coap_psk_identity: str = "iotsim"


class SinkSettings(CommonSettings):
    """Destino das medições do lado receptor (CSV bruto + InfluxDB opcional)."""

    influx_url: str | None = None
    influx_token: str = ""
    influx_org: str = "unesp"
    influx_bucket: str = "iot"
    flush_interval_s: float = Field(default=1.0, gt=0)


class ReceiverSettings(SinkSettings):
    """Lado servidor: endpoints próprios (HTTP, CoAP, WS) ou consumidores de broker (MQTT, AMQP)."""

    listen_host: str = "0.0.0.0"  # escuta em todas as interfaces do container
    listen_port: int | None = None
    broker_host: str = "localhost"
    broker_port: int | None = None
    tls: bool = False
    ca_file: Path | None = None
    tls_cert: Path | None = None
    tls_key: Path | None = None
    topic_prefix: str = "sensors"
    username: str | None = None
    password: SecretStr | None = None
    amqp_queue: str = "iotsim.telemetry"
    coap_psk: SecretStr | None = None
    coap_psk_identity: str = "iotsim"


DEFAULT_PORTS: dict[Protocol, tuple[int, int]] = {
    # (sem TLS, com TLS) — portas convencionais citadas no Quadro 1 da Nota 2
    Protocol.MQTT: (1883, 8883),
    Protocol.COAP: (5683, 5684),
    Protocol.HTTP: (80, 443),
    Protocol.AMQP: (5672, 5671),
    Protocol.WS: (80, 443),
}


def resolve_port(settings: DeviceSettings) -> int:
    if settings.port is not None:
        return settings.port
    plain, secure = DEFAULT_PORTS[settings.protocol]
    return secure if settings.tls else plain
