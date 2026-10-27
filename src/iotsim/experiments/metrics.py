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


@dataclass(frozen=True, slots=True)
class CycleMetrics:
    """Custo de um ciclo do dispositivo sonolento (acorda, conecta, envia, desconecta)."""

    cycles: int
    wire_bytes_per_cycle: float
    packets_per_cycle: float
    up_l4_per_cycle: float
    down_l4_per_cycle: float
    radio_ms_mean: float  # primeiro ao último pacote do ciclo: proxy do tempo de rádio ligado
    radio_ms_p50: float
    radio_ms_max: float


def _quantile(sorted_values: Sequence[float], q: float) -> float:
    return sorted_values[round(q * (len(sorted_values) - 1))]


def compute_cycle_metrics(frames: Sequence[Frame], sent_ns: Sequence[int]) -> CycleMetrics:
    """Agrupa os quadros por ciclo e calcula médias, excluindo o primeiro ciclo.

    O ciclo k vai de ``sent_ns[k]`` (despertar, antes da conexão) até ``sent_ns[k + 1]``;
    o último vai até o fim da captura. O primeiro ciclo é excluído por incluir efeitos de
    partida a frio do processo (carga de bibliotecas, primeiro uso de caches).
    """
    if len(sent_ns) < 3:
        raise ValueError("são necessários ao menos 3 ciclos")
    bounds = [*sent_ns, max(f.ts_ns for f in frames) + 1]
    per_cycle: list[list[Frame]] = []
    for k in range(1, len(sent_ns)):
        start, end = bounds[k], bounds[k + 1]
        cycle = [f for f in frames if start <= f.ts_ns < end]
        if not cycle:
            raise ValueError(f"ciclo {k} sem quadros capturados")
        per_cycle.append(cycle)

    n = len(per_cycle)
    radio = sorted((c[-1].ts_ns - c[0].ts_ns) / 1e6 for c in per_cycle)
    return CycleMetrics(
        cycles=n,
        wire_bytes_per_cycle=sum(f.wire_bytes for c in per_cycle for f in c) / n,
        packets_per_cycle=sum(len(c) for c in per_cycle) / n,
        up_l4_per_cycle=sum(f.l4_payload for c in per_cycle for f in c if f.from_device) / n,
        down_l4_per_cycle=sum(f.l4_payload for c in per_cycle for f in c if not f.from_device) / n,
        radio_ms_mean=sum(radio) / n,
        radio_ms_p50=_quantile(radio, 0.5),
        radio_ms_max=radio[-1],
    )


@dataclass(frozen=True, slots=True)
class DeliveryMetrics:
    """Latência fim a fim e entrega de um cenário (E3/E4)."""

    sent: int  # envios tentados pelo dispositivo
    sender_ok: int  # envios que o dispositivo considerou concluídos
    delivered: int  # mensagens distintas registradas pelo receptor
    duplicates: int  # registros repetidos no receptor (ex.: QoS 1, AMQP liquidado)
    latency_ms_p50: float
    latency_ms_p95: float
    latency_ms_p99: float
    latency_ms_max: float

    @property
    def delivery_ratio(self) -> float:
        return self.delivered / self.sent if self.sent else 0.0


def compute_delivery_metrics(
    sent: Sequence[tuple[str, int, int]], received: Sequence[tuple[str, int, int, int]]
) -> DeliveryMetrics:
    """Cruza envios ``(device_id, seq, ok)`` e recebimentos ``(device_id, seq, sent_ns, recv_ns)``.

    A latência de cada mensagem usa a primeira chegada; chegadas repetidas contam como
    duplicatas. Recebimentos de mensagens que não constam dos envios são ignorados.
    """
    keys = {(d, s) for d, s, _ in sent}
    first: dict[tuple[str, int], int] = {}
    duplicates = 0
    for device_id, seq, sent_ns, recv_ns in received:
        key = (device_id, seq)
        if key not in keys:
            continue
        latency = recv_ns - sent_ns
        if key in first:
            duplicates += 1
            first[key] = min(first[key], latency)
        else:
            first[key] = latency

    latencies = sorted(v / 1e6 for v in first.values())
    nan = float("nan")
    return DeliveryMetrics(
        sent=len(sent),
        sender_ok=sum(ok for _, _, ok in sent),
        delivered=len(first),
        duplicates=duplicates,
        latency_ms_p50=_quantile(latencies, 0.50) if latencies else nan,
        latency_ms_p95=_quantile(latencies, 0.95) if latencies else nan,
        latency_ms_p99=_quantile(latencies, 0.99) if latencies else nan,
        latency_ms_max=latencies[-1] if latencies else nan,
    )
