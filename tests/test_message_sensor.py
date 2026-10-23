from iotsim.message import Reading
from iotsim.sensors import TemperatureSensor


def test_reading_roundtrip() -> None:
    reading = Reading("mqtt-0001", 7, 1_730_000_000_000_000_000, 23.54)
    decoded = Reading.decode(reading.encode())
    assert decoded == Reading("mqtt-0001", 7, 1_730_000_000_000_000_000, 23.5)


def test_payload_size_is_protocol_independent() -> None:
    # A carga útil depende só do conteúdo, não do protocolo: base da comparação justa.
    a = Reading("http-0001", 1, 1_730_000_000_000_000_000, 23.5).encode()
    b = Reading("mqtt-0001", 1, 1_730_000_000_000_000_000, 23.5).encode()
    assert len(a) == len(b)


def test_sensor_is_deterministic_and_bounded() -> None:
    s1, s2 = TemperatureSensor(seed=1), TemperatureSensor(seed=1)
    seq1 = [s1.read() for _ in range(1000)]
    seq2 = [s2.read() for _ in range(1000)]
    assert seq1 == seq2
    assert all(15.0 <= v <= 35.0 for v in seq1)
