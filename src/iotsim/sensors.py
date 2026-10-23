"""Sensor de temperatura sintético e determinístico (seed fixa → execução reproduzível)."""

import random


class TemperatureSensor:
    """Passeio aleatório limitado em torno de uma média, com passo gaussiano."""

    def __init__(
        self,
        seed: int,
        mean: float = 23.5,
        step_std: float = 0.1,
        low: float = 15.0,
        high: float = 35.0,
    ) -> None:
        self._rng = random.Random(seed)
        self._value = mean
        self._step_std = step_std
        self._low = low
        self._high = high

    def read(self) -> float:
        self._value += self._rng.gauss(0.0, self._step_std)
        self._value = min(self._high, max(self._low, self._value))
        return self._value
