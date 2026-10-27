"""E3/E4 — latência fim a fim e taxa de entrega sob perda de pacotes.

Para cada cenário (protocolo e modo de entrega) e cada taxa de perda, um dispositivo envia
N leituras enquanto o netem descarta pacotes IP nos dois sentidos entre ele e o servidor.
A entrega e a latência são medidas no receptor (servidor ou consumidor do broker).

- E3: latência p50/p95/p99 por taxa de perda.
- E4: fração entregue por modo de entrega (QoS 0/1/2, NON/CON, pré-liquidado/liquidado).

Roda com a pilha sem TLS (decisão de 2026-10-27: o TLS não muda a retransmissão)::

    make up && uv run python -m iotsim.experiments.e3_loss
"""

import argparse
import csv
import json
import logging
import time
from dataclasses import asdict
from functools import partial
from pathlib import Path

from iotsim.experiments.capture import (
    DEVICE,
    Scenario,
    apply_loss,
    check_stack_mode,
    clear_loss,
    compose_container,
    container_env,
    container_ip,
    run_scenario,
    scenarios,
)
from iotsim.experiments.metrics import compute_delivery_metrics

log = logging.getLogger(__name__)

LOSSES_PCT = (0.0, 1.0, 5.0, 10.0)
# Maior que a desistência do CoAP CON no aiocoap (~83 s, medida em 2026-10-27): os
# protocolos completam suas próprias retransmissões antes de o simulador desistir.
SEND_TIMEOUT_S = 100.0
RECV_FLUSH_S = 3.0  # os receptores gravam o CSV a cada 1 s


def e3_scenarios() -> list[Scenario]:
    """Cenários do E1 sem TLS, mais o MQTT QoS 2 (exatamente uma vez)."""
    base = scenarios(tls=False)
    qos2 = Scenario("mqtt-qos2", "mqtt", "mosquitto", 1883, False, (("IOTSIM_MQTT_QOS", "2"),))
    index = next(i for i, s in enumerate(base) if s.name == "mqtt-qos1") + 1
    return [*base[:index], qos2, *base[index:]]


def receiver_csv(scenario: Scenario) -> Path:
    """CSV em que o receptor do cenário grava as chegadas (data/<run_id>/recv_<proto>.csv)."""
    service = {
        "mosquitto": "mqtt-consumer",
        "rabbitmq": "amqp-consumer",
    }.get(scenario.server, scenario.server)
    run_id = container_env(compose_container(service))["IOTSIM_RUN_ID"]
    return Path("data") / run_id / f"recv_{scenario.protocol}.csv"


def _read_sent(path: Path, prefix: str) -> list[tuple[str, int, int]]:
    with path.open() as f:
        return [
            (r["device_id"], int(r["seq"]), int(r["ok"]))
            for r in csv.DictReader(f)
            if r["device_id"].startswith(prefix)
        ]


def _read_received(path: Path, prefix: str) -> list[tuple[str, int, int, int]]:
    with path.open() as f:
        return [
            (r["device_id"], int(r["seq"]), int(r["sent_ns"]), int(r["recv_ns"]))
            for r in csv.DictReader(f)
            if r["device_id"].startswith(prefix)
        ]


def _degrade(server: str, server_ip: str, loss: float, seed: int, device_ip: str) -> None:
    """Perda nos dois sentidos: na saída do servidor (volta) e do dispositivo (ida)."""
    if loss > 0:
        apply_loss(server, device_ip, loss, seed)
        apply_loss(DEVICE, server_ip, loss, seed + 1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--messages", type=int, default=100)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--losses", type=float, nargs="*", default=list(LOSSES_PCT))
    parser.add_argument("--only", nargs="*", help="nomes de cenários (padrão: todos)")
    parser.add_argument("--seed", type=int, default=42, help="semente do netem")
    parser.add_argument("--out", type=Path, default=Path("results/e3"))
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    check_stack_mode(tls=False)
    run_id = time.strftime("e3-%Y%m%d-%H%M%S")
    out_dir = args.out / run_id
    out_dir.mkdir(parents=True)

    selected = [s for s in e3_scenarios() if not args.only or s.name in args.only]
    results: list[dict[str, object]] = []
    for scenario in selected:
        server = compose_container(scenario.server)
        server_ip = container_ip(server)
        recv_path = receiver_csv(scenario)
        for loss in args.losses:
            prefix = f"e3-{scenario.name}-{loss:g}-{run_id[3:]}"
            log.info("cenário %s, perda %g%% (%d mensagens)", scenario.name, loss, args.messages)

            try:
                run_scenario(
                    scenario,
                    run_id,
                    prefix,
                    args.messages,
                    args.interval,
                    out_dir,
                    args.env_file,
                    extra_env={"IOTSIM_SEND_TIMEOUT_S": str(SEND_TIMEOUT_S)},
                    on_device_ready=partial(_degrade, server, server_ip, loss, args.seed),
                    # Pior caso: cada envio esgota o tempo limite.
                    run_timeout_s=args.messages * (args.interval + SEND_TIMEOUT_S) + 60,
                    capture_name=f"{scenario.name}-loss{loss:g}",
                )
            finally:
                clear_loss(server)  # o servidor é compartilhado com o restante da pilha
            time.sleep(RECV_FLUSH_S)

            sent_path = out_dir / "device" / run_id / f"sent_{scenario.protocol}.csv"
            metrics = compute_delivery_metrics(
                _read_sent(sent_path, prefix), _read_received(recv_path, prefix)
            )
            results.append(
                {
                    "scenario": scenario.name,
                    "protocol": scenario.protocol,
                    "loss_pct": loss,
                    **asdict(metrics),
                    "delivery_ratio": metrics.delivery_ratio,
                }
            )
            log.info(
                "  entrega %.1f%% (%d/%d, %d duplicadas), latência p50 %.1f / p95 %.1f / "
                "p99 %.1f ms",
                100 * metrics.delivery_ratio,
                metrics.delivered,
                metrics.sent,
                metrics.duplicates,
                metrics.latency_ms_p50,
                metrics.latency_ms_p95,
                metrics.latency_ms_p99,
            )
            # Gravação incremental: uma execução longa interrompida preserva o que já rodou.
            with (out_dir / "summary.csv").open("w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=list(results[0]))
                writer.writeheader()
                writer.writerows(results)

    (out_dir / "meta.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "messages": args.messages,
                "interval_s": args.interval,
                "losses_pct": args.losses,
                "netem_seed": args.seed,
                "send_timeout_s": SEND_TIMEOUT_S,
                "scenarios": [s.name for s in selected],
            },
            indent=2,
        )
    )
    log.info("resultados em %s", out_dir)


if __name__ == "__main__":
    main()
