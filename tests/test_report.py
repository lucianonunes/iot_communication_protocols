from pathlib import Path

from iotsim.experiments.report import Drop, apply_drops, latest_per_key, write_table


def test_latest_run_wins_per_key() -> None:
    rows = [
        ("old", {"scenario": "mqtt-qos0", "v": "1"}),
        ("old", {"scenario": "http", "v": "2"}),
        ("new", {"scenario": "mqtt-qos0", "v": "3"}),  # refeito: substitui a rodada antiga
    ]
    by = {r["scenario"]: r["v"] for r in latest_per_key(rows, ("scenario",))}
    assert by == {"mqtt-qos0": "3", "http": "2"}


def test_apply_drops_removes_only_the_invalid_point() -> None:
    rows = [
        ("r1", {"scenario": "coap-con", "devices": "10"}),
        ("r1", {"scenario": "coap-con", "devices": "100"}),
        ("r2", {"scenario": "coap-con", "devices": "10"}),  # o ponto refeito
        ("r1", {"scenario": "coap-con-tls", "devices": "10"}),  # outro cenário: mantido
    ]
    kept = apply_drops(rows, [Drop("r1", "coap-con", 10, "repouso")])
    assert kept == [rows[1][1], rows[2][1], rows[3][1]]


def test_write_table_formats_decimals_for_latex(tmp_path: Path) -> None:
    write_table(
        tmp_path,
        "t",
        ["a", "b"],
        [["x_1", 1234.56], ["y", float("nan")]],
        "Legenda",
        tex_header=["Cenário", "Ent. 5%"],
    )
    tex = (tmp_path / "t.tex").read_text()
    assert r"Cenário & Ent. 5\% \\" in tex  # % no cabeçalho seria comentário LaTeX
    assert r"x\_1 & 1\,234,6 \\" in tex
    assert r"y & -- \\" in tex
    assert (tmp_path / "t.csv").read_text().splitlines()[1] == "x_1,1234.56"
