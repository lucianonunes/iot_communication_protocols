"""Mensagem de telemetria idêntica para todos os protocolos (comparação justa)."""

import json
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Reading:
    device_id: str
    seq: int
    sent_ns: int
    value: float

    def encode(self) -> bytes:
        # JSON compacto com chaves curtas: mesma carga útil em bytes para todos os protocolos.
        obj = {"d": self.device_id, "s": self.seq, "t": self.sent_ns, "v": round(self.value, 1)}
        return json.dumps(obj, separators=(",", ":")).encode()

    @classmethod
    def decode(cls, raw: bytes) -> "Reading":
        obj = json.loads(raw)
        return cls(device_id=obj["d"], seq=obj["s"], sent_ns=obj["t"], value=obj["v"])
