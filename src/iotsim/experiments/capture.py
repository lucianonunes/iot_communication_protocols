"""Captura de tráfego de um dispositivo isolado, comum aos experimentos E1 e E2.

Cada cenário (protocolo, modo de entrega e TLS) sobe um único dispositivo num container
próprio e grava com tshark, no mesmo namespace de rede, apenas o tráfego com o servidor.
"""

import csv
import logging
import shlex
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from iotsim.experiments.metrics import Frame

log = logging.getLogger(__name__)

NETSHOOT = "nicolaka/netshoot:v0.14"
IMAGE = "iotsim:dev"
DEVICE = "iotsim-exp-device"
SNIFFER = "iotsim-exp-sniffer"


@dataclass(frozen=True)
class Scenario:
    name: str
    protocol: str
    server: str  # serviço do Compose que recebe o tráfego do dispositivo
    port: int
    tls: bool
    env: tuple[tuple[str, str], ...] = ()


def scenarios(tls: bool) -> list[Scenario]:
    """Modos de entrega de cada protocolo, sem ou com TLS/DTLS."""
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


def compose_container(service: str) -> str:
    cid = _run(["docker", "compose", "ps", "-q", service]).stdout.strip()
    if not cid:
        raise RuntimeError(f"serviço {service} não está em execução (rode `make up`)")
    return cid


def container_ip(cid: str) -> str:
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


def container_env(container: str) -> dict[str, str]:
    """Variáveis de ambiente com que o container foi criado."""
    out = _run(["docker", "inspect", "-f", "{{range .Config.Env}}{{println .}}{{end}}", container])
    return dict(line.split("=", 1) for line in out.stdout.splitlines() if "=" in line)


def check_stack_mode(tls: bool) -> None:
    """Confere se os servidores de modo único estão no modo pedido."""
    for service in ("http-server", "coap-server", "ws-server"):
        active = container_env(compose_container(service)).get("IOTSIM_TLS") == "true"
        if active != tls:
            hint = "make up-tls" if tls else "make up"
            raise RuntimeError(f"{service} está com TLS={active}; suba a pilha com `{hint}`")


def _tc(container: str, script: str) -> None:
    """Executa comandos ``tc`` no namespace de rede de um container."""
    _run(
        [
            "docker",
            "run",
            "--rm",
            "--net",
            f"container:{container}",
            "--cap-add",
            "NET_ADMIN",
            NETSHOOT,
            "sh",
            "-ec",
            script,
        ]
    )


def apply_loss(container: str, peer_ip: str, loss_pct: float, seed: int) -> None:
    """Descarta ``loss_pct``% dos pacotes IP que ``container`` envia a ``peer_ip`` (netem).

    O kernel da VM do Docker Desktop não tem ``ifb``, então a perda é sempre aplicada na
    saída: no dispositivo (sentido de ida) e no servidor (volta, filtrada pelo IP do
    dispositivo, sem afetar os demais clientes). Só pacotes IP são descartados; o ARP passa,
    pois sua perda gera falhas de rota que não pertencem ao protocolo avaliado.
    """
    clear_loss(container)
    _tc(
        container,
        "tc qdisc add dev eth0 root handle 1: prio bands 4 "
        "priomap 1 2 2 2 1 2 0 0 1 1 1 1 1 1 1 1\n"
        f"tc qdisc add dev eth0 parent 1:4 handle 40: netem loss {loss_pct}% seed {seed}\n"
        f"tc filter add dev eth0 parent 1: protocol ip prio 1 u32 match ip dst {peer_ip}/32 "
        "flowid 1:4",
    )


def clear_loss(container: str) -> None:
    """Remove a disciplina de fila raiz (idempotente)."""
    _tc(container, "tc qdisc del dev eth0 root 2>/dev/null || true")


def _cleanup() -> None:
    subprocess.run(["docker", "rm", "-f", DEVICE, SNIFFER], capture_output=True, check=False)


def _device_env(
    scenario: Scenario,
    run_id: str,
    device_prefix: str,
    messages: int,
    interval_s: float,
    env_file: Path,
    extra_env: dict[str, str],
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
        "IOTSIM_DEVICE_ID_PREFIX": device_prefix,
        "IOTSIM_LOG_LEVEL": "WARNING",
        **dict(scenario.env),
        **extra_env,
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
    scenario: Scenario,
    run_id: str,
    device_prefix: str,
    messages: int,
    interval_s: float,
    out_dir: Path,
    env_file: Path,
    extra_env: dict[str, str] | None = None,
    on_device_ready: Callable[[str], None] | None = None,
    run_timeout_s: float | None = None,
    capture_name: str | None = None,
) -> tuple[Path, str]:
    """Executa um cenário e devolve a captura (pcap) e o IP do dispositivo.

    ``on_device_ready`` recebe o IP do dispositivo antes de o simulador começar (ex.: para
    aplicar perda de pacotes); ``run_timeout_s`` limita a execução do simulador;
    ``capture_name`` nomeia o pcap (padrão: nome do cenário).
    """
    server = compose_container(scenario.server)
    network = _network(server)
    pcap = out_dir / f"{capture_name or scenario.name}.pcap"
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
            *_device_env(
                scenario,
                run_id,
                device_prefix,
                messages,
                interval_s,
                env_file,
                extra_env or {},
            ),
            "--entrypoint",
            "sleep",
            IMAGE,
            "infinity",
        ]
    )
    try:
        # 2) tshark no mesmo namespace, gravando apenas o tráfego com o servidor.
        device_ip = container_ip(DEVICE)
        server_ip = container_ip(server)
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
        if on_device_ready is not None:
            on_device_ready(device_ip)

        # 3) Executa o simulador e aguarda o término (número de mensagens no ambiente).
        timeout = run_timeout_s if run_timeout_s is not None else messages * interval_s + 60
        _run(["docker", "exec", DEVICE, "iotsim-device"], timeout=timeout)
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
    """Instantes de envio e tamanho médio da carga útil das mensagens em regime.

    O regime exclui a primeira e a última mensagem (ver ``compute_metrics``).
    """
    path = out_dir / "device" / run_id / f"sent_{protocol}.csv"
    with path.open() as f:
        rows = [r for r in csv.DictReader(f) if r["device_id"].startswith(device_prefix)]
    failed = [r for r in rows if r["ok"] != "1"]
    if failed:
        raise RuntimeError(f"{len(failed)} envio(s) falharam: {failed[0]['error']}")
    rows.sort(key=lambda r: int(r["sent_ns"]))
    # A carga varia com o tamanho do ID e do número de sequência; usa a média do regime.
    steady = rows[1:-1]
    payload = sum(int(r["payload_bytes"]) for r in steady) / max(len(steady), 1)
    return [int(r["sent_ns"]) for r in rows], payload
