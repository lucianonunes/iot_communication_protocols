"""E2 — custo de cada ciclo de um dispositivo sonolento (acorda, conecta, envia, desconecta).

Complementa o E1: em vez de uma conexão persistente, cada leitura paga o estabelecimento
de conexão (TCP, TLS/DTLS e o handshake do protocolo). Mede bytes e pacotes por ciclo e o
tempo entre o primeiro e o último pacote do ciclo, aproximação do tempo de rádio ligado.

Como no E1, roda em duas etapas, com a pilha no modo correspondente::

    make up      && uv run python -m iotsim.experiments.e2_sleepy --mode plain
    make up-tls  && uv run python -m iotsim.experiments.e2_sleepy --mode tls
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
from iotsim.experiments.metrics import compute_cycle_metrics

log = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=["plain", "tls"], required=True)
    parser.add_argument("--cycles", type=int, default=20)
    # Intervalo maior que o fechamento das conexões (FIN/close), para não sobrepor ciclos.
    parser.add_argument("--interval", type=float, default=3.0)
    parser.add_argument("--only", nargs="*", help="nomes de cenários (padrão: todos)")
    parser.add_argument("--out", type=Path, default=Path("results/e2"))
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    tls = args.mode == "tls"
    check_stack_mode(tls)
    run_id = time.strftime(f"e2-{args.mode}-%Y%m%d-%H%M%S")
    out_dir = args.out / run_id
    out_dir.mkdir(parents=True)

    selected = [s for s in scenarios(tls) if not args.only or s.name in args.only]
    results: list[dict[str, object]] = []
    for scenario in selected:
        log.info("cenário %s (%d ciclos)", scenario.name, args.cycles)
        prefix = f"e2-{scenario.name}"
        pcap, device_ip = run_scenario(
            scenario,
            run_id,
            prefix,
            args.cycles,
            args.interval,
            out_dir,
            args.env_file,
            extra_env={"IOTSIM_SLEEPY": "true"},
        )
        sent, payload = read_sent(out_dir, run_id, scenario.protocol, f"{prefix}-")
        metrics = compute_cycle_metrics(read_frames(pcap, device_ip), sent)
        results.append(
            {
                "scenario": scenario.name,
                "protocol": scenario.protocol,
                "tls": scenario.tls,
                "payload_bytes": payload,
                **asdict(metrics),
            }
        )
        log.info(
            "  %.0f B/ciclo, %.1f pacotes/ciclo, rádio %.1f ms (p50 %.1f, máx %.1f)",
            metrics.wire_bytes_per_cycle,
            metrics.packets_per_cycle,
            metrics.radio_ms_mean,
            metrics.radio_ms_p50,
            metrics.radio_ms_max,
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
                "cycles": args.cycles,
                "interval_s": args.interval,
                "scenarios": [s.name for s in selected],
            },
            indent=2,
        )
    )
    log.info("resultados em %s", out_dir)


if __name__ == "__main__":
    main()
