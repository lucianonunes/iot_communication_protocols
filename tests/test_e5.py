import csv
from pathlib import Path

from iotsim.experiments.e5_scale import cpu_percent, window_delivery
from iotsim.sink import RECV_HEADER, SENT_HEADER

S = 1_000_000_000


def test_cpu_percent_is_relative_to_one_core() -> None:
    assert cpu_percent(1_000_000, 3_000_000, 2.0) == 100.0  # 2 s de CPU em 2 s
    assert cpu_percent(0, 500_000, 10.0) == 5.0


def _write(path: Path, header: tuple[str, ...], rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)


def test_window_delivery_uses_only_messages_sent_in_window(tmp_path: Path) -> None:
    sent, recv = tmp_path / "sent.csv", tmp_path / "recv.csv"
    base = {"run_id": "r", "protocol": "mqtt", "ack_ns": 0, "payload_bytes": 60, "error": ""}
    # Um envio por segundo, de t = 0 a 9 s; aquecimento 2 s, janela 5 s → seq 2..6.
    _write(
        sent,
        SENT_HEADER,
        [{**base, "device_id": "p-0000", "seq": k, "sent_ns": k * S, "ok": 1} for k in range(10)],
    )
    received = [
        {
            "run_id": "r",
            "protocol": "mqtt",
            "device_id": "p-0000",
            "seq": k,
            "sent_ns": k * S,
            "recv_ns": k * S + 2_000_000,
            "payload_bytes": 60,
        }
        for k in range(10)
        if k != 4  # perdida dentro da janela
    ]
    _write(recv, RECV_HEADER, received)

    metrics, rate = window_delivery(sent, recv, "p-", warmup_s=2.0, window_s=5.0)

    assert (metrics.sent, metrics.delivered) == (5, 4)
    assert rate == 1.0
    assert metrics.latency_ms_p50 == 2.0
