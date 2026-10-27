"""E5 — escala: CPU e memória dos servidores com 10, 100 e 1000 dispositivos.

Para cada protocolo (um modo de entrega por protocolo) e cada nível de escala, um container
com N dispositivos envia 1 leitura/s por dispositivo. Após o aquecimento, mede-se durante
uma janela fixa:

- CPU (núcleos, via ``usage_usec`` do cgroup v2) e memória (``memory.current``) do servidor
  e, nos protocolos com broker, também do consumidor;
- CPU do próprio simulador, para detectar se ele, e não o servidor, é o gargalo;
- vazão alcançada, entrega e latência das mensagens enviadas na janela.

As frotas contínuas da pilha (``*-devices``) são paradas durante o E5 e religadas ao final,
para que só a carga do experimento chegue aos servidores. Roda nos dois modos::

    make up      && uv run python -m iotsim.experiments.e5_scale --mode plain
    make up-tls  && uv run python -m iotsim.experiments.e5_scale --mode tls
"""

import argparse
import csv
import json
import logging
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from iotsim.experiments.capture import (
    DEVICE,
    Scenario,
    check_stack_mode,
    cleanup,
    compose_container,
    container_env,
    cpu_usage_usec,
    memory_bytes,
    scenarios,
    start_device,
)
from iotsim.experiments.metrics import DeliveryMetrics, compute_delivery_metrics

log = logging.getLogger(__name__)

SCALES = (10, 100, 1000)
# Um modo por protocolo (decisão de 2026-10-27): entrega confirmada, exceto o WebSocket.
SCENARIO_NAMES = ("mqtt-qos1", "coap-con", "http", "amqp-settled", "ws")
# Limite do cliente, não do protocolo (medido em 2026-10-27): o python-qpid-proton cria um
# contexto OpenSSL por conexão, mesmo sem TLS; com centenas de conexões simultâneas num só
# processo o simulador satura (1000 conexões levam ~87 s) com o broker ocioso.
MAX_DEVICES = {"amqp": 100}
FLEETS = ("mqtt-devices", "http-devices", "coap-devices", "amqp-devices", "ws-devices")
CONSUMERS = {"mosquitto": "mqtt-consumer", "rabbitmq": "amqp-consumer"}
MEM_SAMPLE_S = 5.0
RECV_FLUSH_S = 3.0  # os receptores gravam o CSV a cada 1 s
MIB = 1024 * 1024


@dataclass(frozen=True, slots=True)
class Usage:
    """Uso de recursos de um container numa janela de medição."""

    cpu_pct: float  # 100 = um núcleo inteiro
    mem_mib_mean: float
    mem_mib_max: float


def cpu_percent(start_usec: int, end_usec: int, seconds: float) -> float:
    return (end_usec - start_usec) / (seconds * 1e6) * 100


def measure(containers: dict[str, str], seconds: float) -> dict[str, Usage]:
    """Mede CPU média e memória (amostrada) de cada container durante ``seconds``."""
    cpu_start = {k: cpu_usage_usec(c) for k, c in containers.items()}
    samples: dict[str, list[int]] = {k: [] for k in containers}
    t0 = time.monotonic()
    deadline = t0 + seconds
    while True:
        for key, container in containers.items():
            samples[key].append(memory_bytes(container))
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(MEM_SAMPLE_S, remaining))
    elapsed = time.monotonic() - t0
    return {
        key: Usage(
            cpu_pct=cpu_percent(cpu_start[key], cpu_usage_usec(container), elapsed),
            mem_mib_mean=sum(samples[key]) / len(samples[key]) / MIB,
            mem_mib_max=max(samples[key]) / MIB,
        )
        for key, container in containers.items()
    }


def _receiver_csv(scenario: Scenario) -> Path:
    service = CONSUMERS.get(scenario.server, scenario.server)
    run_id = container_env(compose_container(service))["IOTSIM_RUN_ID"]
    return Path("data") / run_id / f"recv_{scenario.protocol}.csv"


def window_delivery(
    sent_path: Path, recv_path: Path, prefix: str, warmup_s: float, window_s: float
) -> tuple[DeliveryMetrics, float]:
    """Entrega e latência das mensagens enviadas na janela; devolve também a vazão (msg/s).

    A janela é definida pelos próprios instantes de envio do dispositivo (mesmo relógio do
    receptor): ``[t0 + aquecimento, t0 + aquecimento + janela)``, com t0 = primeiro envio.
    """
    with sent_path.open() as f:
        sent_rows = [r for r in csv.DictReader(f) if r["device_id"].startswith(prefix)]
    t0 = min(int(r["sent_ns"]) for r in sent_rows)
    start, end = t0 + int(warmup_s * 1e9), t0 + int((warmup_s + window_s) * 1e9)
    sent = [
        (r["device_id"], int(r["seq"]), int(r["ok"]))
        for r in sent_rows
        if start <= int(r["sent_ns"]) < end
    ]
    with recv_path.open() as f:
        received = [
            (r["device_id"], int(r["seq"]), int(r["sent_ns"]), int(r["recv_ns"]))
            for r in csv.DictReader(f)
            if r["device_id"].startswith(prefix) and start <= int(r["sent_ns"]) < end
        ]
    return compute_delivery_metrics(sent, received), len(sent) / window_s


def _flatten(prefix: str, usage: Usage | None) -> dict[str, object]:
    if usage is None:
        return {f"{prefix}_cpu_pct": "", f"{prefix}_mem_mib_mean": "", f"{prefix}_mem_mib_max": ""}
    return {f"{prefix}_{k}": round(v, 3) for k, v in asdict(usage).items()}


def _targets(scenario: Scenario) -> dict[str, str]:
    """Containers medidos: servidor/broker e, quando houver, o consumidor."""
    targets = {"server": compose_container(scenario.server)}
    if scenario.server in CONSUMERS:
        targets["consumer"] = compose_container(CONSUMERS[scenario.server])
    return targets


def run_scale(
    scenario: Scenario,
    devices: int,
    run_id: str,
    out_dir: Path,
    env_file: Path,
    interval_s: float,
    warmup_s: float,
    window_s: float,
) -> dict[str, object]:
    # Prefixo único por execução: os receptores acumulam todas as execuções no mesmo CSV.
    prefix = f"{run_id}-{scenario.name}-{devices}"
    # Folga de 10 envios: os dispositivos seguem ativos até depois do fim da janela.
    messages = round((warmup_s + window_s) / interval_s) + 10
    start_device(
        scenario,
        run_id,
        prefix,
        messages,
        interval_s,
        out_dir,
        env_file,
        extra_env={"IOTSIM_DEVICES": str(devices)},
    )
    log_path = out_dir / f"{scenario.name}-{devices}.log"
    try:
        with log_path.open("w") as log_file:
            proc = subprocess.Popen(
                ["docker", "exec", DEVICE, "iotsim-device"],
                stdout=subprocess.DEVNULL,
                stderr=log_file,
            )
            try:
                time.sleep(warmup_s)
                usage = measure({**_targets(scenario), "simulator": DEVICE}, window_s)
                proc.wait(timeout=messages * interval_s + 120)
            finally:
                if proc.poll() is None:
                    proc.kill()
    finally:
        cleanup()
    if proc.returncode != 0:
        log.warning("  simulador terminou com código %s (ver %s)", proc.returncode, log_path)
    time.sleep(RECV_FLUSH_S)

    sent_path = out_dir / "device" / run_id / f"sent_{scenario.protocol}.csv"
    delivery, rate = window_delivery(
        sent_path, _receiver_csv(scenario), f"{prefix}-", warmup_s, window_s
    )
    return {
        "devices": devices,
        "target_rate": devices / interval_s,
        "achieved_rate": round(rate, 2),
        **asdict(delivery),
        "delivery_ratio": delivery.delivery_ratio,
        **_flatten("server", usage["server"]),
        **_flatten("consumer", usage.get("consumer")),
        **_flatten("simulator", usage["simulator"]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=["plain", "tls"], required=True)
    parser.add_argument("--scales", type=int, nargs="*", default=list(SCALES))
    parser.add_argument("--only", nargs="*", help="cenários sem o sufixo -tls (padrão: todos)")
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--warmup", type=float, default=60.0)
    parser.add_argument("--window", type=float, default=60.0)
    parser.add_argument("--idle", type=float, default=20.0, help="janela da linha de base")
    parser.add_argument("--out", type=Path, default=Path("results/e5"))
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    tls = args.mode == "tls"
    check_stack_mode(tls)
    run_id = time.strftime(f"e5-{args.mode}-%Y%m%d-%H%M%S")
    out_dir = args.out / run_id
    out_dir.mkdir(parents=True)

    names = [n for n in SCENARIO_NAMES if not args.only or n in args.only]
    suffix = "-tls" if tls else ""
    selected = [s for s in scenarios(tls) if s.name.removesuffix(suffix) in names]

    results: list[dict[str, object]] = []
    subprocess.run(["docker", "compose", "stop", *FLEETS], check=True, capture_output=True)
    try:
        for scenario in selected:
            # Linha de base: servidor sem a carga do experimento.
            idle = measure(_targets(scenario), args.idle)
            log.info(
                "cenário %s: servidor ocioso %.1f%% CPU, %.0f MiB",
                scenario.name,
                idle["server"].cpu_pct,
                idle["server"].mem_mib_mean,
            )
            for devices in args.scales:
                limit = MAX_DEVICES.get(scenario.protocol)
                if limit is not None and devices > limit:
                    log.info("  %d dispositivos: ignorado (limite do cliente: %d)", devices, limit)
                    continue
                log.info("  %d dispositivos", devices)
                row = run_scale(
                    scenario,
                    devices,
                    run_id,
                    out_dir,
                    args.env_file,
                    args.interval,
                    args.warmup,
                    args.window,
                )
                results.append(
                    {
                        "scenario": scenario.name,
                        "protocol": scenario.protocol,
                        "tls": scenario.tls,
                        **_flatten("idle_server", idle["server"]),
                        **_flatten("idle_consumer", idle.get("consumer")),
                        **row,
                    }
                )
                log.info(
                    "    %.0f msg/s, entrega %.1f%%, p95 %.1f ms | CPU servidor %.1f%%, "
                    "consumidor %s%%, simulador %.1f%% | memória servidor %.0f MiB",
                    row["achieved_rate"],
                    100 * float(str(row["delivery_ratio"])),
                    float(str(row["latency_ms_p95"])),
                    float(str(row["server_cpu_pct"])),
                    row["consumer_cpu_pct"] or "-",
                    float(str(row["simulator_cpu_pct"])),
                    float(str(row["server_mem_mib_mean"])),
                )
                # Gravação incremental: uma execução interrompida preserva o que já rodou.
                with (out_dir / "summary.csv").open("w", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=list(results[0]))
                    writer.writeheader()
                    writer.writerows(results)
    finally:
        subprocess.run(["docker", "compose", "start", *FLEETS], check=False, capture_output=True)

    (out_dir / "meta.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "mode": args.mode,
                "scales": args.scales,
                "interval_s": args.interval,
                "warmup_s": args.warmup,
                "window_s": args.window,
                "idle_s": args.idle,
                "max_devices": MAX_DEVICES,
                "scenarios": [s.name for s in selected],
            },
            indent=2,
        )
    )
    log.info("resultados em %s", out_dir)


if __name__ == "__main__":
    main()
