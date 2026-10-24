"""Gravação das medições: CSV bruto (fonte do artigo) e InfluxDB (visualização no Grafana)."""

import asyncio
import contextlib
import csv
import logging
from pathlib import Path
from types import TracebackType
from typing import Self

from influxdb_client.client.influxdb_client_async import InfluxDBClientAsync
from influxdb_client.client.write.point import Point
from influxdb_client.domain.write_precision import WritePrecision

from iotsim.config import Protocol, SinkSettings
from iotsim.message import Reading

log = logging.getLogger(__name__)

RECV_HEADER = ("run_id", "protocol", "device_id", "seq", "sent_ns", "recv_ns", "payload_bytes")
SENT_HEADER = (
    "run_id",
    "protocol",
    "device_id",
    "seq",
    "sent_ns",
    "ack_ns",
    "payload_bytes",
    "ok",
    "error",
)


class CsvRecorder:
    """Escritor CSV em modo append; cria o cabeçalho apenas em arquivo novo.

    Recusa continuar um arquivo com cabeçalho diferente, para não misturar colunas
    (ex.: dados de uma versão anterior com o mesmo run_id).
    """

    def __init__(self, path: Path, header: tuple[str, ...]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        is_new = not path.exists() or path.stat().st_size == 0
        if not is_new:
            with path.open(newline="", encoding="utf-8") as f:
                existing = tuple(next(csv.reader(f), ()))
            if existing != header:
                raise ValueError(f"{path}: cabeçalho diferente do esperado; use outro run_id")
        self._file = path.open("a", newline="", encoding="utf-8")
        self._writer = csv.writer(self._file)
        if is_new:
            self._writer.writerow(header)

    def write(self, row: tuple[object, ...]) -> None:
        self._writer.writerow(row)

    def flush(self) -> None:
        self._file.flush()

    def close(self) -> None:
        self._file.close()


class ReceiveSink:
    """Registra cada mensagem recebida por um servidor/consumidor de protocolo."""

    def __init__(self, protocol: Protocol, settings: SinkSettings) -> None:
        self._protocol = protocol
        self._settings = settings
        path = settings.data_dir / settings.run_id / f"recv_{protocol.value}.csv"
        self._csv = CsvRecorder(path, RECV_HEADER)
        self._points: list[Point] = []
        self._client: InfluxDBClientAsync | None = None
        self._flusher: asyncio.Task[None] | None = None

    async def __aenter__(self) -> Self:
        if self._settings.influx_url:
            self._client = InfluxDBClientAsync(
                url=self._settings.influx_url,
                token=self._settings.influx_token,
                org=self._settings.influx_org,
            )
        self._flusher = asyncio.create_task(self._flush_loop())
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._flusher is not None:
            self._flusher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._flusher
        await self._flush()
        self._csv.close()
        if self._client is not None:
            await self._client.close()

    def record(self, reading: Reading, recv_ns: int, payload_bytes: int) -> None:
        self._csv.write(
            (
                self._settings.run_id,
                self._protocol.value,
                reading.device_id,
                reading.seq,
                reading.sent_ns,
                recv_ns,
                payload_bytes,
            )
        )
        if self._client is not None:
            self._points.append(
                Point("telemetry")
                .tag("run_id", self._settings.run_id)
                .tag("protocol", self._protocol.value)
                .tag("device_id", reading.device_id)
                .field("seq", reading.seq)
                .field("value", reading.value)
                .field("latency_ms", (recv_ns - reading.sent_ns) / 1e6)
                .field("payload_bytes", payload_bytes)
                .time(recv_ns, WritePrecision.NS)
            )

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(self._settings.flush_interval_s)
            await self._flush()

    async def _flush(self) -> None:
        self._csv.flush()
        if self._client is None or not self._points:
            return
        points, self._points = self._points, []
        try:
            await self._client.write_api().write(bucket=self._settings.influx_bucket, record=points)
        except Exception:
            # A visualização é secundária: falha no InfluxDB não interrompe o experimento.
            log.exception("falha ao gravar %d pontos no InfluxDB", len(points))
