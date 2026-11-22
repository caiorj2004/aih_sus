"""Camada de dados (DuckDB sobre Parquet): totais conferidos com o PostgreSQL de produção."""
import duckdb
import pytest

import dados


def test_totais_por_ano_batem_com_o_postgres():
    # mesmos números registrados em agente_logs quando o agente consultava o PostgreSQL do IESB
    qtd = dados.consultar("SELECT ano, SUM(total) AS t FROM aih_qtd GROUP BY ano ORDER BY ano")
    vl = dados.consultar("SELECT ano, ROUND(SUM(total), 2) AS t FROM aih_vl GROUP BY ano ORDER BY ano")
    assert qtd["t"].tolist() == [466_288_815, 495_089_831, 38_661_480]
    assert vl["t"].tolist() == [23_671_426_406.63, 25_730_966_451.49, 2_005_837_127.99]


def test_todo_municipio_do_sih_tem_uf():
    sem_uf = dados.consultar("""SELECT DISTINCT q.cod_municipio FROM aih_qtd q
                                LEFT JOIN municipios m USING (cod_municipio) WHERE m.uf_sigla IS NULL""")
    assert sem_uf.empty


def test_recorte_consolidado_e_delta():
    df = dados.load_consolidated_data(("2026",), ("Jan",), ("53",), ())
    assert df["uf_sigla"].unique().tolist() == ["DF"]
    assert dados.previous_period("2026", "Jan") == ("2025", "Dez")
    qtd, vl = dados.get_period_totals("2025", "Dez", ("53",), ())
    assert qtd > 0 and vl > 0


def test_banco_e_somente_leitura():
    with pytest.raises(duckdb.Error):
        dados.consultar("CREATE TABLE invasor (x INTEGER)")
