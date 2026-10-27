"""Gera as figuras e tabelas do artigo a partir das rodadas canônicas (analysis/runs.toml).

Uso::

    uv run python -m iotsim.experiments.report

Saída em ``results/report/``: figuras em PDF (artigo) e PNG (revisão), tabelas em CSV e
LaTeX (``booktabs``). Lê apenas os ``summary.csv``; não depende da pilha Docker.
"""

import argparse
import csv
import logging
import math
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # sem janela: gera arquivos
import matplotlib.pyplot as plt
from matplotlib.axes import Axes
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

log = logging.getLogger(__name__)

Row = dict[str, str]

# Paleta categórica validada (dataviz/validate_palette.js, modo claro: CVD ΔE ≥ 9,1 entre
# vizinhos). Três cores ficam abaixo de 3:1 sobre o fundo; por isso cada protocolo também
# tem marcador e hachura próprios e as séries são rotuladas diretamente ou em legenda —
# a identidade nunca depende só da cor (o artigo pode ser impresso em tons de cinza).
PROTOCOLS = ("mqtt", "coap", "http", "amqp", "ws")
LABEL = {"mqtt": "MQTT", "coap": "CoAP", "http": "HTTP", "amqp": "AMQP", "ws": "WebSocket"}
COLOR = {
    "mqtt": "#2a78d6",
    "coap": "#eb6834",
    "http": "#1baf7a",
    "amqp": "#eda100",
    "ws": "#e87ba4",
}
MARKER = {"mqtt": "o", "coap": "s", "http": "^", "amqp": "D", "ws": "v"}
HATCH = {"mqtt": "", "coap": "///", "http": "...", "amqp": "xx", "ws": "\\\\\\"}
INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#e4e3df"

SCENARIO_LABEL = {
    "mqtt-qos0": "MQTT QoS 0",
    "mqtt-qos1": "MQTT QoS 1",
    "mqtt-qos2": "MQTT QoS 2",
    "coap-non": "CoAP NON",
    "coap-con": "CoAP CON",
    "http": "HTTP",
    "amqp-presettled": "AMQP pré-liq.",
    "amqp-settled": "AMQP liquidado",
    "ws": "WebSocket",
}


@dataclass(frozen=True)
class Drop:
    run: str
    scenario: str
    devices: int
    reason: str


def _style() -> None:
    plt.rcParams.update(
        {
            "font.family": "serif",
            "font.size": 8,
            "axes.titlesize": 8,
            "axes.labelsize": 8,
            "xtick.labelsize": 7,
            "ytick.labelsize": 7,
            "legend.fontsize": 7,
            "axes.edgecolor": INK_2,
            "axes.labelcolor": INK,
            "xtick.color": INK_2,
            "ytick.color": INK_2,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "axes.axisbelow": True,
            "grid.color": GRID,
            "grid.linewidth": 0.5,
            "lines.linewidth": 1.5,
            "lines.markersize": 4,
            "legend.frameon": False,
            "savefig.bbox": "tight",
            "savefig.dpi": 300,
            "pdf.fonttype": 42,  # fontes embutidas como TrueType (exigência comum de anais)
        }
    )


def base_scenario(name: str) -> str:
    return name.removesuffix("-tls")


def is_tls(row: Row) -> bool:
    return row.get("tls", "False") == "True"


def read_rows(results: Path, runs: Iterable[str]) -> list[tuple[str, Row]]:
    rows: list[tuple[str, Row]] = []
    for run in runs:
        with (results / run / "summary.csv").open() as f:
            rows.extend((run, r) for r in csv.DictReader(f))
    return rows


def latest_per_key(rows: list[tuple[str, Row]], key: tuple[str, ...]) -> list[Row]:
    """Mantém, para cada chave, a linha da última rodada listada (ex.: MQTT refeito no E1)."""
    chosen: dict[tuple[str, ...], Row] = {}
    for _, row in rows:
        chosen[tuple(row[k] for k in key)] = row
    return list(chosen.values())


# ---------- tabelas ----------


def write_table(
    out: Path,
    name: str,
    header: list[str],
    rows: list[list[object]],
    caption: str,
    tex_header: list[str] | None = None,
) -> None:
    """Grava a tabela em CSV (dados) e em LaTeX ``booktabs`` (artigo)."""
    with (out / f"{name}.csv").open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)

    def cell(value: object) -> str:
        if isinstance(value, float):
            if math.isnan(value):
                return "--"
            # Vírgula decimal e espaço fino entre milhares (ABNT).
            return f"{value:,.1f}".replace(",", "@").replace(".", ",").replace("@", r"\,")
        return str(value).replace("%", r"\%").replace("_", r"\_")

    cols = tex_header or header
    lines = [
        r"\begin{table}[htbp]",
        r"\centering",
        rf"\caption{{{caption}}}",
        rf"\label{{tab:{name}}}",
        r"\small",
        r"\begin{tabular}{l" + "r" * (len(cols) - 1) + "}",
        r"\toprule",
        " & ".join(cols) + r" \\",
        r"\midrule",
        *[" & ".join(cell(v) for v in r) + r" \\" for r in rows],
        r"\bottomrule",
        r"\end{tabular}",
        r"\end{table}",
    ]
    (out / f"{name}.tex").write_text("\n".join(lines) + "\n")


# ---------- figuras ----------


def save(fig: Figure, out: Path, name: str) -> None:
    for ext in ("pdf", "png"):
        fig.savefig(out / f"{name}.{ext}")
    plt.close(fig)
    log.info("figura %s", name)


def _paired_bars(
    ax: Axes,
    labels: list[str],
    protocols: list[str],
    plain: list[float],
    tls: list[float],
) -> None:
    """Barras agrupadas por cenário: sem TLS (cheia) e com TLS (hachurada, contorno)."""
    x = list(range(len(labels)))
    width = 0.38
    for i, (proto, a, b) in enumerate(zip(protocols, plain, tls, strict=True)):
        ax.bar(i - width / 2, a, width, color=COLOR[proto], edgecolor="white", linewidth=0.8)
        ax.bar(
            i + width / 2,
            b,
            width,
            color="white",
            edgecolor=COLOR[proto],
            hatch="////",
            linewidth=1.0,
        )
    ax.set_xticks(x, labels, rotation=30, ha="right")
    ax.grid(axis="x", visible=False)
    # Legenda do par de barras (a identidade do protocolo está no rótulo do eixo x).
    ax.legend(
        handles=[
            Patch(facecolor=INK_2, edgecolor="white", label="sem TLS"),
            Patch(facecolor="white", edgecolor=INK_2, hatch="////", label="com TLS/DTLS"),
        ],
        loc="upper left",
    )


def _f(row: Row | None, key: str) -> float:
    """Valor numérico da coluna; NaN quando a linha ou o valor não existem."""
    if row is None:
        return math.nan
    value = row.get(key, "")
    return float(value) if value not in ("", "nan") else math.nan


E1_ORDER = (
    "mqtt-qos0",
    "mqtt-qos1",
    "coap-non",
    "coap-con",
    "http",
    "amqp-presettled",
    "amqp-settled",
    "ws",
)
# Quadro 2 da Nota 2: overhead de aplicação estimado (bytes além da carga útil).
QUADRO2 = {"mqtt-qos0": 7, "coap-non": 9, "http": 51, "amqp-presettled": 27, "ws": 6}


def _by_scenario(rows: list[Row]) -> dict[tuple[str, bool], Row]:
    return {(base_scenario(r["scenario"]), is_tls(r)): r for r in rows}


def report_e1(rows: list[Row], out: Path) -> None:
    by = _by_scenario(rows)
    labels = [SCENARIO_LABEL[s] for s in E1_ORDER]
    protos = [by[(s, False)]["protocol"] for s in E1_ORDER]

    fig, ax = plt.subplots(figsize=(3.5, 2.4))
    _paired_bars(
        ax,
        labels,
        protos,
        [_f(by[(s, False)], "wire_bytes_per_msg") for s in E1_ORDER],
        [_f(by[(s, True)], "wire_bytes_per_msg") for s in E1_ORDER],
    )
    ax.set_ylabel("bytes por mensagem na rede")
    save(fig, out, "e1_bytes_por_mensagem")

    fig, ax = plt.subplots(figsize=(3.5, 2.4))
    _paired_bars(
        ax,
        labels,
        protos,
        [_f(by[(s, False)], "setup_wire_bytes") for s in E1_ORDER],
        [_f(by[(s, True)], "setup_wire_bytes") for s in E1_ORDER],
    )
    ax.set_ylabel("bytes de estabelecimento")
    save(fig, out, "e1_estabelecimento")

    table = []
    for s in E1_ORDER:
        p, t = by[(s, False)], by[(s, True)]
        table.append(
            [
                SCENARIO_LABEL[s],
                float(QUADRO2[s]) if s in QUADRO2 else math.nan,
                _f(p, "up_overhead_per_msg"),
                _f(t, "up_overhead_per_msg"),
                _f(p, "packets_per_msg"),
                _f(p, "wire_bytes_per_msg"),
                _f(t, "wire_bytes_per_msg"),
                # Sem conexão (CoAP), o valor é ruído de ±1 B do tamanho da carga útil.
                max(0.0, _f(p, "setup_wire_bytes")),
                max(0.0, _f(t, "setup_wire_bytes")),
            ]
        )
    write_table(
        out,
        "e1",
        [
            "cenario",
            "overhead_quadro2",
            "overhead_app",
            "overhead_app_tls",
            "pacotes_msg",
            "bytes_rede",
            "bytes_rede_tls",
            "estab",
            "estab_tls",
        ],
        table,
        "Bytes por mensagem (E1): overhead de aplicação estimado (Q2: Quadro 2 da Nota 2, "
        r"tópico \texttt{s/t}) e medido (tópico \texttt{sensors/<id>/temperature}, 37 B), "
        "pacotes e bytes na rede por mensagem e custo de estabelecimento, sem e com TLS/DTLS. "
        "CoAP sem DTLS não estabelece conexão (0 B).",
        tex_header=[
            "Cenário",
            "Q2",
            "Ovh.",
            "Ovh. TLS",
            "Pac.",
            "Rede",
            "Rede TLS",
            "Estab.",
            "Estab. TLS",
        ],
    )


def report_e2(rows: list[Row], out: Path) -> None:
    by = _by_scenario(rows)
    labels = [SCENARIO_LABEL[s] for s in E1_ORDER]
    protos = [by[(s, False)]["protocol"] for s in E1_ORDER]

    fig, ax = plt.subplots(figsize=(3.5, 2.4))
    _paired_bars(
        ax,
        labels,
        protos,
        [_f(by[(s, False)], "wire_bytes_per_cycle") for s in E1_ORDER],
        [_f(by[(s, True)], "wire_bytes_per_cycle") for s in E1_ORDER],
    )
    ax.set_ylabel("bytes por ciclo")
    save(fig, out, "e2_bytes_por_ciclo")

    table = [
        [
            SCENARIO_LABEL[s],
            _f(by[(s, False)], "wire_bytes_per_cycle"),
            _f(by[(s, True)], "wire_bytes_per_cycle"),
            _f(by[(s, False)], "packets_per_cycle"),
            _f(by[(s, True)], "packets_per_cycle"),
            _f(by[(s, False)], "radio_ms_p50"),
            _f(by[(s, True)], "radio_ms_p50"),
        ]
        for s in E1_ORDER
    ]
    write_table(
        out,
        "e2",
        ["cenario", "bytes", "bytes_tls", "pacotes", "pacotes_tls", "radio_ms", "radio_ms_tls"],
        table,
        "Dispositivo sonolento (E2): bytes, pacotes e tempo de rádio (mediana, ms) por ciclo de "
        "conexão, envio e desconexão.",
        tex_header=["Cenário", "Bytes", "Bytes TLS", "Pac.", "Pac. TLS", "Rádio", "Rádio TLS"],
    )


E3_ORDER = (
    "mqtt-qos0",
    "mqtt-qos1",
    "mqtt-qos2",
    "coap-non",
    "coap-con",
    "http",
    "amqp-presettled",
    "amqp-settled",
    "ws",
)
# Um modo por protocolo nas figuras (≤ 5 séries; a tabela traz todos os modos).
E3_FIGURE = ("mqtt-qos1", "coap-con", "http", "amqp-settled", "ws")


def _line(ax: Axes, proto: str, xs: list[float], ys: list[float], label: str, **kw: object) -> None:
    ax.plot(
        xs,
        ys,
        color=COLOR[proto],
        marker=MARKER[proto],
        markeredgecolor="white",
        markeredgewidth=0.6,
        label=label,
        **kw,  # type: ignore[arg-type]
    )


def report_e3(rows: list[Row], out: Path) -> None:
    by = {(r["scenario"], float(r["loss_pct"])): r for r in rows}
    losses = sorted({float(r["loss_pct"]) for r in rows})

    fig, axes = plt.subplots(1, 2, figsize=(7.0, 2.4), sharex=True)
    for ax, metric, title in (
        (axes[0], "latency_ms_p95", "p95"),
        (axes[1], "latency_ms_p99", "p99"),
    ):
        for s in E3_FIGURE:
            proto = by[(s, 0.0)]["protocol"]
            _line(ax, proto, losses, [_f(by[(s, x)], metric) for x in losses], SCENARIO_LABEL[s])
        ax.set_yscale("log")
        ax.set_xticks(losses, [f"{x:g}%" for x in losses])
        ax.set_xlabel("perda de pacotes (cada sentido)")
        ax.set_ylabel(f"latência {title} (ms, log)")
    axes[1].legend(loc="upper left", bbox_to_anchor=(1.02, 1.0))
    save(fig, out, "e3_latencia")

    table = [
        [
            SCENARIO_LABEL[s],
            *[100 * _f(by[(s, x)], "delivery_ratio") for x in losses],
            *[_f(by[(s, x)], "latency_ms_p95") for x in losses],
        ]
        for s in E3_ORDER
    ]
    pct = [f"{x:g}%" for x in losses]
    write_table(
        out,
        "e3_e4",
        ["cenario", *[f"entrega_{p}" for p in pct], *[f"p95_ms_{p}" for p in pct]],
        table,
        "Entrega (\\%) e latência p95 (ms) sob perda de pacotes nos dois sentidos (E3/E4), "
        "sem TLS, 100 mensagens por combinação.",
        tex_header=["Cenário", *[f"Ent. {p}" for p in pct], *[f"p95 {p}" for p in pct]],
    )


E5_ORDER = ("mqtt-qos1", "coap-con", "http", "amqp-settled", "ws")


def apply_drops(rows: list[tuple[str, Row]], drops: list[Drop]) -> list[Row]:
    kept = []
    for run, row in rows:
        dropped = next(
            (
                d
                for d in drops
                if d.run == run
                and row["scenario"] == d.scenario
                and int(row["devices"]) == d.devices
            ),
            None,
        )
        if dropped is not None:
            log.info("E5: descartado %s %s (%s)", run, row["scenario"], dropped.reason)
            continue
        kept.append(row)
    return kept


def report_e5(rows: list[Row], out: Path) -> None:
    by = {(base_scenario(r["scenario"]), is_tls(r), int(r["devices"])): r for r in rows}
    scales = sorted({int(r["devices"]) for r in rows})

    fig, axes = plt.subplots(1, 2, figsize=(7.0, 2.4), sharex=True)
    for ax, metric, ylabel in (
        (axes[0], "server_cpu_pct", "CPU do servidor (% de um núcleo)"),
        (axes[1], "server_mem_mib_mean", "memória do servidor (MiB)"),
    ):
        for s in E5_ORDER:
            for tls in (False, True):
                pts = [(n, _f(by[(s, tls, n)], metric)) for n in scales if (s, tls, n) in by]
                proto = by[(s, False, scales[0])]["protocol"]
                _line(
                    ax,
                    proto,
                    [n for n, _ in pts],
                    [v for _, v in pts],
                    LABEL[proto] if not tls else f"{LABEL[proto]} (TLS)",
                    linestyle="--" if tls else "-",
                )
        ax.set_xscale("log")
        ax.set_xticks(scales, [str(n) for n in scales])
        ax.set_xlabel("dispositivos (1 leitura/s cada)")
        ax.set_ylabel(ylabel)
    handles = [
        Line2D([], [], color=COLOR[p], marker=MARKER[p], label=LABEL[p]) for p in PROTOCOLS
    ] + [
        Line2D([], [], color=INK_2, linestyle="-", label="sem TLS"),
        Line2D([], [], color=INK_2, linestyle="--", label="com TLS/DTLS"),
    ]
    axes[1].legend(handles=handles, loc="upper left", bbox_to_anchor=(1.02, 1.0))
    save(fig, out, "e5_escala")

    table = []
    for s in E5_ORDER:
        for tls in (False, True):
            r = {n: by.get((s, tls, n)) for n in scales}
            table.append(
                [
                    SCENARIO_LABEL[s] + (" (TLS)" if tls else ""),
                    *[_f(r[n], "server_cpu_pct") for n in scales],
                    *[_f(r[n], "server_mem_mib_mean") for n in scales],
                    *[_f(r[n], "latency_ms_p95") for n in scales],
                ]
            )
    write_table(
        out,
        "e5",
        [
            "cenario",
            *[f"cpu_pct_{n}" for n in scales],
            *[f"mem_mib_{n}" for n in scales],
            *[f"p95_ms_{n}" for n in scales],
        ],
        table,
        "Escala (E5): CPU (\\% de um núcleo) e memória (MiB) do servidor e latência p95 (ms) "
        "por número de dispositivos. Entrega de 100\\% em todos os pontos; AMQP limitado a "
        "100 dispositivos pelo cliente.",
        tex_header=[
            "Cenário",
            *[f"CPU {n}" for n in scales],
            *[f"Mem. {n}" for n in scales],
            *[f"p95 {n}" for n in scales],
        ],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=Path, default=Path("analysis/runs.toml"))
    parser.add_argument("--results", type=Path, default=Path("results"))
    parser.add_argument("--out", type=Path, default=Path("results/report"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("fontTools").setLevel(logging.WARNING)  # subconjunto de fontes do PDF

    config = tomllib.loads(args.runs.read_text())
    args.out.mkdir(parents=True, exist_ok=True)
    _style()

    report_e1(
        latest_per_key(read_rows(args.results, config["e1"]["runs"]), ("scenario",)), args.out
    )
    report_e2(
        latest_per_key(read_rows(args.results, config["e2"]["runs"]), ("scenario",)), args.out
    )
    report_e3(
        latest_per_key(read_rows(args.results, config["e3"]["runs"]), ("scenario", "loss_pct")),
        args.out,
    )
    drops = [Drop(**d) for d in config["e5"].get("drop", [])]
    report_e5(apply_drops(read_rows(args.results, config["e5"]["runs"]), drops), args.out)
    log.info("relatório em %s", args.out)


if __name__ == "__main__":
    main()
