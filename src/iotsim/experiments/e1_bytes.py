"""E1 — bytes na rede por mensagem (validação empírica do Quadro 2 da Nota 2).

Para cada cenário (protocolo e TLS), sobe um único dispositivo num container próprio,
captura o tráfego com tshark no mesmo namespace de rede e calcula bytes e pacotes por
mensagem em regime, além do custo de estabelecimento de conexão.

Os servidores HTTP, CoAP e WebSocket escutam em um modo por vez; por isso o E1 roda em
duas etapas, cada uma com a pilha no modo correspondente::

    make up      && uv run python -m iotsim.experiments.e1_bytes --mode plain
    make up-tls  && uv run python -m iotsim.experiments.e1_bytes --mode tls
"""

import argparse
import csv
import json
import logging
import time
from dataclasses import asdict
from pathlib import Path

from iotsim.experiments.capture import (
    check_stack_mode,
    read_frames,
    read_sent,
    run_scenario,
    scenarios,
)
from iotsim.experiments.metrics import ByteMetrics, compute_metrics

log = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=["plain", "tls"], required=True)
    parser.add_argument("--messages", type=int, default=30)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--only", nargs="*", help="nomes de cenários (padrão: todos)")
    parser.add_argument("--out", type=Path, default=Path("results/e1"))
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    tls = args.mode == "tls"
    check_stack_mode(tls)
    run_id = time.strftime(f"e1-{args.mode}-%Y%m%d-%H%M%S")
    out_dir = args.out / run_id
    out_dir.mkdir(parents=True)

    selected = [s for s in scenarios(tls) if not args.only or s.name in args.only]
    results: list[dict[str, object]] = []
    for scenario in selected:
        log.info("cenário %s (%d mensagens)", scenario.name, args.messages)
        prefix = f"e1-{scenario.name}"
        pcap, device_ip = run_scenario(
            scenario, run_id, prefix, args.messages, args.interval, out_dir, args.env_file
        )
        sent, payload = read_sent(out_dir, run_id, scenario.protocol, f"{prefix}-")
        metrics: ByteMetrics = compute_metrics(read_frames(pcap, device_ip), sent, payload)
        row: dict[str, object] = {
            "scenario": scenario.name,
            "protocol": scenario.protocol,
            "tls": scenario.tls,
            **asdict(metrics),
            "up_overhead_per_msg": metrics.up_overhead_per_msg,
        }
        results.append(row)
        log.info(
            "  %.1f B/msg na rede, %.1f pacotes/msg, overhead de aplicação %.1f B, "
            "estabelecimento %.0f B",
            metrics.wire_bytes_per_msg,
            metrics.packets_per_msg,
            metrics.up_overhead_per_msg,
            metrics.setup_wire_bytes,
        )

    with (out_dir / "summary.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0]))
        writer.writeheader()
        writer.writerows(results)
    (out_dir / "meta.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "mode": args.mode,
                "messages": args.messages,
                "interval_s": args.interval,
                "scenarios": [s.name for s in selected],
            },
            indent=2,
        )
    )
    log.info("resultados em %s", out_dir)


if __name__ == "__main__":
    main()
