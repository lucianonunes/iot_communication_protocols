import pytest

from iotsim.experiments.metrics import Frame, compute_metrics

S = 1_000_000_000  # 1 s em ns


def _frames() -> list[Frame]:
    frames = [
        # Estabelecimento: 3 quadros de 70 bytes antes do primeiro envio.
        Frame(0, 70, True, 0),
        Frame(1, 70, False, 0),
        Frame(2, 70, True, 0),
    ]
    # 5 mensagens (t = 1..5 s): requisição de 100 bytes (60 de L4) + resposta de 50 (10 de L4).
    for k in range(1, 6):
        frames.append(Frame(k * S + 10, 100, True, 60))
        frames.append(Frame(k * S + 20, 50, False, 10))
    return frames


def test_steady_state_and_setup() -> None:
    sent = [k * S for k in range(1, 6)]
    m = compute_metrics(_frames(), sent, payload_bytes=56)

    assert m.messages == 3  # mensagens 2, 3 e 4
    assert m.wire_bytes_per_msg == 150
    assert m.packets_per_msg == 2
    assert m.up_l4_per_msg == 60
    assert m.down_l4_per_msg == 10
    assert m.up_overhead_per_msg == 4
    # Antes da 2ª mensagem: 210 de estabelecimento + 150 da 1ª mensagem - 150 em regime.
    assert m.setup_wire_bytes == 210
    assert m.setup_packets == 3


def test_requires_enough_messages() -> None:
    with pytest.raises(ValueError):
        compute_metrics(_frames(), [S, 2 * S, 3 * S], payload_bytes=56)


def test_cycle_metrics_excludes_first_cycle() -> None:
    from iotsim.experiments.metrics import compute_cycle_metrics

    frames: list[Frame] = []
    for k in range(4):  # 4 ciclos em t = 0, 1, 2, 3 s
        base = k * S
        size = 999 if k == 0 else 100  # 1º ciclo diferente: deve ser ignorado
        frames += [
            Frame(base + 1_000_000, size, True, 40),  # +1 ms
            Frame(base + 2_000_000, 60, False, 0),
            Frame(base + 5_000_000, 60, True, 0),  # +5 ms: último pacote do ciclo
        ]
    m = compute_cycle_metrics(frames, [k * S for k in range(4)])

    assert m.cycles == 3
    assert m.wire_bytes_per_cycle == 220
    assert m.packets_per_cycle == 3
    assert m.up_l4_per_cycle == 40
    assert m.radio_ms_mean == 4.0
    assert m.radio_ms_max == 4.0


def test_delivery_metrics_counts_losses_and_duplicates() -> None:
    from iotsim.experiments.metrics import compute_delivery_metrics

    ms = 1_000_000
    sent = [("d", 0, 1), ("d", 1, 1), ("d", 2, 0), ("d", 3, 1)]
    received = [
        ("d", 0, 0, 2 * ms),
        ("d", 1, 0, 4 * ms),
        ("d", 1, 0, 9 * ms),  # duplicata: mantém a primeira chegada
        ("d", 3, 0, 6 * ms),
        ("outro", 0, 0, 1 * ms),  # não pertence ao cenário
    ]
    m = compute_delivery_metrics(sent, received)

    assert (m.sent, m.sender_ok, m.delivered, m.duplicates) == (4, 3, 3, 1)
    assert m.delivery_ratio == 0.75
    assert (m.latency_ms_p50, m.latency_ms_max) == (4.0, 6.0)
