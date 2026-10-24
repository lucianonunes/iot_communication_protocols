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
import shlex
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from iotsim.experiments.metrics import ByteMetrics, Frame, compute_metrics

log = logging.getLogger(__name__)

NETSHOOT = "nicolaka/netshoot:v0.14"
IMAGE = "iotsim:dev"
DEVICE = "e1-device"
SNIFFER = "e1-sniffer"


@dataclass(frozen=True)
class Scenario:
    name: str
    protocol: str
    server: str  # serviço do Compose que recebe o tráfego do dispositivo
    port: int
    tls: bool
    env: tuple[tuple[str, str], ...] = ()


def scenarios(tls: bool) -> list[Scenario]:
    """Cenários do E1: modos de entrega de cada protocolo, sem ou com TLS/DTLS."""
    plain = [
        Scenario("mqtt-qos0", "mqtt", "mosquitto", 1883, False, (("IOTSIM_MQTT_QOS", "0"),)),
        Scenario("mqtt-qos1", "mqtt", "mosquitto", 1883, False, (("IOTSIM_MQTT_QOS", "1"),)),
        Scenario("coap-non", "coap", "coap-server", 5683, False),
        Scenario(
            "coap-con", "coap", "coap-server", 5683, False, (("IOTSIM_COAP_CONFIRMABLE", "true"),)
        ),
        Scenario("http", "http", "http-server", 8080, False),
        Scenario(
            "amqp-presettled", "amqp", "rabbitmq", 5672, False, (("IOTSIM_AMQP_CONFIRM", "false"),)
        ),
        Scenario("amqp-settled", "amqp", "rabbitmq", 5672, False),
        Scenario("ws", "ws", "ws-server", 8081, False),
    ]
    tls_ports = {
        "mosquitto": 8883,
        "coap-server": 5684,
        "http-server": 443,
        "rabbitmq": 5671,
        "ws-server": 443,
    }
    if not tls:
        return plain
    return [
        Scenario(f"{s.name}-tls", s.protocol, s.server, tls_ports[s.server], True, s.env)
        for s in plain
    ]


def _run(cmd: list[str], timeout: float | None = None) -> subprocess.CompletedProcess[str]:
    log.debug("$ %s", shlex.join(cmd))
    return subprocess.run(cmd, check=True, text=True, capture_output=True, timeout=timeout)


def _compose_container(service: str) -> str:
    cid = _run(["docker", "compose", "ps", "-q", service]).stdout.strip()
    if not cid:
        raise RuntimeError(f"serviço {service} não está em execução (rode `make up`)")
    return cid


def _container_ip(cid: str) -> str:
    out = _run(
        [
            "docker",
            "inspect",
            "-f",
            "{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}",
            cid,
        ]
    ).stdout
    return out.split()[0]


def _network(cid: str) -> str:
    out = _run(
        [
            "docker",
            "inspect",
            "-f",
            "{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}",
            cid,
        ]
    ).stdout
    return out.split()[0]


def _env_value(name: str, env_file: Path) -> str:
    for line in env_file.read_text().splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1]
    raise KeyError(f"{name} ausente em {env_file}")


def check_stack_mode(tls: bool) -> None:
    """Confere se os servidores de modo único estão no modo pedido."""
    for service in ("http-server", "coap-server", "ws-server"):
        cid = _compose_container(service)
        env = _run(["docker", "inspect", "-f", "{{range .Config.Env}}{{println .}}{{end}}", cid])
        active = "IOTSIM_TLS=true" in env.stdout.splitlines()
        if active != tls:
            hint = "make up-tls" if tls else "make up"
            raise RuntimeError(f"{service} está com TLS={active}; suba a pilha com `{hint}`")


def _cleanup() -> None:
    subprocess.run(["docker", "rm", "-f", DEVICE, SNIFFER], capture_output=True, check=False)


def _device_env(
    scenario: Scenario, run_id: str, messages: int, interval_s: float, env_file: Path
) -> list[str]:
    env = {
        "IOTSIM_RUN_ID": run_id,
        "IOTSIM_PROTOCOL": scenario.protocol,
        "IOTSIM_HOST": scenario.server,
        "IOTSIM_PORT": str(scenario.port),
        "IOTSIM_TLS": str(scenario.tls).lower(),
        "IOTSIM_CA_FILE": "/app/certs/ca.crt",
        "IOTSIM_DEVICES": "1",
        "IOTSIM_INTERVAL_S": str(interval_s),
        # Número exato de envios, independente do tempo de conexão de cada protocolo.
        "IOTSIM_MAX_MESSAGES": str(messages),
        "IOTSIM_DEVICE_ID_PREFIX": f"e1-{scenario.name}",
        "IOTSIM_LOG_LEVEL": "WARNING",
        **dict(scenario.env),
    }
    if scenario.protocol == "amqp":
        env["IOTSIM_USERNAME"] = _env_value("AMQP_USER", env_file)
        env["IOTSIM_PASSWORD"] = _env_value("AMQP_PASSWORD", env_file)
    if scenario.protocol == "coap" and scenario.tls:
        env["IOTSIM_COAP_PSK"] = _env_value("COAP_PSK", env_file)
    args: list[str] = []
    for key, value in env.items():
        args += ["-e", f"{key}={value}"]
    return args


def run_scenario(
    scenario: Scenario, run_id: str, messages: int, interval_s: float, out_dir: Path, env_file: Path
) -> tuple[Path, str]:
    """Executa um cenário e devolve a captura (pcap) e o IP do dispositivo."""
    server = _compose_container(scenario.server)
    network = _network(server)
    pcap = out_dir / f"{scenario.name}.pcap"
    data_dir = (out_dir / "device").resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    _cleanup()

    # 1) Container do dispositivo, ainda parado em `sleep`: fixa o namespace de rede
    #    antes de a captura começar, para registrar desde o primeiro pacote.
    _run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            DEVICE,
            "--network",
            network,
            "-v",
            f"{(Path('infra/certs')).resolve()}:/app/certs:ro",
            "-v",
            f"{data_dir}:/app/data",
            *_device_env(scenario, run_id, messages, interval_s, env_file),
            "--entrypoint",
            "sleep",
            IMAGE,
            "infinity",
        ]
    )
    try:
        # 2) tshark no mesmo namespace, gravando apenas o tráfego com o servidor.
        device_ip = _container_ip(DEVICE)
        server_ip = _container_ip(server)
        _run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                SNIFFER,
                "--net",
                f"container:{DEVICE}",
                "--cap-add",
                "NET_ADMIN",
                "--cap-add",
                "NET_RAW",
                "-v",
                f"{out_dir.resolve()}:/cap",
                NETSHOOT,
                "tshark",
                "-i",
                "eth0",
                "-f",
                f"host {server_ip}",
                "-w",
                f"/cap/{pcap.name}",
                "-q",
            ]
        )
        time.sleep(2.0)  # tshark pronto antes do primeiro pacote

        # 3) Executa o simulador e aguarda o término (duração definida no ambiente).
        _run(["docker", "exec", DEVICE, "iotsim-device"], timeout=messages * interval_s + 60)
        time.sleep(2.0)  # pacotes finais (FIN/close) antes de parar a captura
    finally:
        subprocess.run(["docker", "stop", "-t", "5", SNIFFER], capture_output=True, check=False)
        _cleanup()
    return pcap, device_ip


def read_frames(pcap: Path, device_ip: str) -> list[Frame]:
    """Extrai, via tshark, o tamanho de cada quadro IP e da carga L4 (acima de TCP/UDP).

    Quadros sem IP (ARP) são descartados: resolvem endereço na rede local e não
    pertencem ao protocolo avaliado.
    """
    fields = ["frame.time_epoch", "frame.len", "ip.src", "ip.dst", "tcp.len", "udp.length"]
    cmd = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{pcap.parent.resolve()}:/cap",
        NETSHOOT,
        "tshark",
        "-r",
        f"/cap/{pcap.name}",
        "-T",
        "fields",
        "-E",
        "separator=,",
        "-E",
        "occurrence=f",
    ]
    for f in fields:
        cmd += ["-e", f]
    rows = [line.split(",") for line in _run(cmd).stdout.splitlines() if line]
    frames = []
    for epoch, length, src, _dst, tcp_len, udp_len in rows:
        if not src:
            continue
        seconds, _, frac = epoch.partition(".")
        ts_ns = int(seconds) * 1_000_000_000 + int(frac.ljust(9, "0")[:9])
        l4 = int(tcp_len) if tcp_len else (int(udp_len) - 8 if udp_len else 0)
        frames.append(Frame(ts_ns, int(length), src == device_ip, l4))
    return frames


def read_sent(
    out_dir: Path, run_id: str, protocol: str, device_prefix: str
) -> tuple[list[int], float]:
    """Instantes de envio e tamanho médio da carga útil das mensagens em regime."""
    path = out_dir / "device" / run_id / f"sent_{protocol}.csv"
    with path.open() as f:
        rows = [r for r in csv.DictReader(f) if r["device_id"].startswith(device_prefix)]
    failed = [r for r in rows if r["ok"] != "1"]
    if failed:
        raise RuntimeError(f"{len(failed)} envio(s) falharam: {failed[0]['error']}")
    rows.sort(key=lambda r: int(r["sent_ns"]))
    # A carga varia com o tamanho do ID e do número de sequência; usa a média do regime
    # (mesmas mensagens consideradas por compute_metrics).
    steady = rows[1:-1]
    payload = sum(int(r["payload_bytes"]) for r in steady) / max(len(steady), 1)
    return [int(r["sent_ns"]) for r in rows], payload


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
        pcap, device_ip = run_scenario(
            scenario, run_id, args.messages, args.interval, out_dir, args.env_file
        )
        sent, payload = read_sent(out_dir, run_id, scenario.protocol, f"e1-{scenario.name}-")
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
