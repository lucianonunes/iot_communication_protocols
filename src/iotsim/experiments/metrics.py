"""Métricas de bytes e pacotes por mensagem, a partir de quadros capturados (E1/E2).

A captura é feita no servidor/broker e filtrada pelo IP do dispositivo. As mensagens
são delimitadas pelos instantes de envio registrados pelo próprio dispositivo
(``sent_ns``), que usa o mesmo relógio da captura (mesma VM do Docker).
"""

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Frame:
    ts_ns: int
    wire_bytes: int  # quadro Ethernet completo
    from_device: bool
    l4_payload: int  # bytes acima de TCP/UDP (aplicação, e TLS/DTLS quando houver)


@dataclass(frozen=True, slots=True)
class ByteMetrics:
    messages: int
    payload_bytes: float
    wire_bytes_per_msg: float
    packets_per_msg: float
    up_l4_per_msg: float  # dispositivo → servidor
    down_l4_per_msg: float  # servidor → dispositivo
    setup_wire_bytes: float
    setup_packets: float

    @property
    def up_overhead_per_msg(self) -> float:
        """Bytes de aplicação enviados além da carga útil (comparável ao Quadro 2)."""
        return self.up_l4_per_msg - self.payload_bytes


def compute_metrics(
    frames: Sequence[Frame], sent_ns: Sequence[int], payload_bytes: float
) -> ByteMetrics:
    """Calcula as médias em regime e o custo de estabelecimento.

    - Regime: quadros em ``[sent_ns[1], sent_ns[-1])``, ou seja, ``len(sent_ns) - 2``
      mensagens completas, excluindo a primeira (que pode carregar o handshake) e a última.
    - Estabelecimento: quadros antes de ``sent_ns[1]`` (conexão + primeira mensagem)
      menos uma mensagem em regime. Assim o custo é comparável entre protocolos que
      conectam antes do primeiro envio (MQTT, AMQP) e os que conectam no primeiro envio
      (HTTP, WebSocket, CoAP/DTLS).
    """
    if len(sent_ns) < 4:
        raise ValueError("são necessárias ao menos 4 mensagens enviadas")
    start, end = sent_ns[1], sent_ns[-1]
    messages = len(sent_ns) - 2

    steady = [f for f in frames if start <= f.ts_ns < end]
    wire = sum(f.wire_bytes for f in steady) / messages
    packets = len(steady) / messages
    up = sum(f.l4_payload for f in steady if f.from_device) / messages
    down = sum(f.l4_payload for f in steady if not f.from_device) / messages

    before = [f for f in frames if f.ts_ns < start]
    return ByteMetrics(
        messages=messages,
        payload_bytes=payload_bytes,
        wire_bytes_per_msg=wire,
        packets_per_msg=packets,
        up_l4_per_msg=up,
        down_l4_per_msg=down,
        setup_wire_bytes=sum(f.wire_bytes for f in before) - wire,
        setup_packets=len(before) - packets,
    )
