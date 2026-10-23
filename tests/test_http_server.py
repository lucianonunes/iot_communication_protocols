import csv
from pathlib import Path

from fastapi.testclient import TestClient

from iotsim.config import ReceiverSettings
from iotsim.message import Reading
from iotsim.servers.http_server import create_app


def test_http_server_records_reading(tmp_path: Path) -> None:
    settings = ReceiverSettings(data_dir=tmp_path, run_id="t")
    payload = Reading("http-0000", 3, 1_730_000_000_000_000_000, 23.5).encode()

    with TestClient(create_app(settings)) as client:
        assert client.get("/health").status_code == 200
        resp = client.post("/sensors/http-0000/temperature", content=payload)
        assert resp.status_code == 204

    with (tmp_path / "t" / "recv_http.csv").open() as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["device_id"] == "http-0000"
    assert rows[0]["seq"] == "3"
    assert int(rows[0]["payload_bytes"]) == len(payload)
