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
