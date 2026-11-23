"""
dados.py
--------
Camada de dados do dashboard e do assistente: DuckDB lendo os arquivos Parquet de data/.

Por que DuckDB + Parquet (e não PostgreSQL como fonte principal):
- O DuckDB consulta o Parquet direto do disco e só traz para a memória o resultado
  já filtrado e agregado. A versão anterior carregava as matrizes largas no pandas
  (e, no assistente, copiava tudo de novo para um SQLite em memória), o que estourava
  o limite de memória do Streamlit Community Cloud ("End of Service").
- O app abre sem depender de rede, de credenciais nem do banco do IESB.

Arquivos em data/ (gerados a partir do PostgreSQL por scripts/atualizar_dados.py):
    aih_qtd.parquet    quantidades aprovadas: ano, mes, cod_municipio, municipio, qtd_XXXX, total
    aih_vl.parquet     valores aprovados (R$): ano, mes, cod_municipio, municipio, vl_XXXX, total
    municipios.parquet cod_municipio (6 dígitos, como no TabNet), nome, UF, região, capital, lat/long (IBGE)
    dic_geral.csv      dicionário de grupos de procedimento: n (nome), s (sufixo do código, ex. "0406")

As views ficam num arquivo DuckDB temporário aberto em modo SOMENTE LEITURA: nem o
dashboard nem o assistente de IA conseguem alterar os dados.
"""

import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import duckdb
import pandas as pd
import streamlit as st

DATA_DIR = Path(__file__).parent / "data"
QTD_FILE = DATA_DIR / "aih_qtd.parquet"
VL_FILE = DATA_DIR / "aih_vl.parquet"
MUN_FILE = DATA_DIR / "municipios.parquet"
DIC_FILE = DATA_DIR / "dic_geral.csv"
DB_FILE = Path(tempfile.gettempdir()) / "aih_sus_views.duckdb"

EPSILON = 1e-9
MESES = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]
_MES_NUM: Dict[str, int] = {m.lower(): i + 1 for i, m in enumerate(MESES)}
_MES_NUM.update({"janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4, "maio": 5,
                 "junho": 6, "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10,
                 "novembro": 11, "dezembro": 12})


# ---------------------------------------------------------------------------
# Utilidades de período
# ---------------------------------------------------------------------------
def month_to_num(mes: str) -> int:
    """'Jan'/'janeiro'/'1' -> 1. Devolve 0 se não reconhecer."""
    try:
        return int(mes)
    except (ValueError, TypeError):
        return _MES_NUM.get(str(mes).strip().lower(), 0)


def previous_period(year: str, month: str) -> Optional[Tuple[str, str]]:
    """Período imediatamente anterior, no mesmo formato dos dados ('Jan' de 2026 -> 'Dez' de 2025)."""
    m = month_to_num(month)
    if m == 0:
        return None
    if m > 1:
        return str(year), MESES[m - 2]
    return str(int(year) - 1), MESES[11]


def calculate_average_ticket(total_value: float, total_quantity: float) -> float:
    return (total_value / total_quantity) if abs(total_quantity) >= EPSILON else 0.0


# ---------------------------------------------------------------------------
# Conexão (DuckDB somente leitura sobre os Parquet)
# ---------------------------------------------------------------------------
def criar_views(destino: Path = DB_FILE) -> Path:
    """Cria (ou recria) o arquivo DuckDB com views sobre os Parquet. Não copia os dados."""
    destino.unlink(missing_ok=True)
    with duckdb.connect(str(destino)) as con:
        con.execute(f"CREATE VIEW aih_qtd AS SELECT * FROM read_parquet('{QTD_FILE.as_posix()}')")
        con.execute(f"CREATE VIEW aih_vl AS SELECT * FROM read_parquet('{VL_FILE.as_posix()}')")
        con.execute(f"CREATE VIEW municipios AS SELECT * FROM read_parquet('{MUN_FILE.as_posix()}')")
        con.execute(f"CREATE VIEW dic_geral AS SELECT * FROM read_csv('{DIC_FILE.as_posix()}', "
                    "header=true, all_varchar=true)")
    return destino


@st.cache_resource(show_spinner=False)
def _conexao() -> duckdb.DuckDBPyConnection:
    caminho = criar_views()
    return duckdb.connect(str(caminho), read_only=True,
                          config={"memory_limit": "256MB", "threads": 1})  # cabe no 1 GB do Community Cloud


def consultar(sql: str, params: Optional[list] = None) -> pd.DataFrame:
    """Executa uma consulta num cursor próprio (um por chamada: seguro entre as threads do Streamlit)."""
    with _conexao().cursor() as cur:
        return cur.execute(sql, params or []).df()


# ---------------------------------------------------------------------------
# Metadados
# ---------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def get_procedure_columns() -> Tuple[List[str], List[str]]:
    """Colunas de procedimento existentes em cada matriz (qtd_* e vl_*), na ordem do arquivo."""
    qtd = [c for c in consultar("DESCRIBE aih_qtd")["column_name"] if c.startswith("qtd_")]
    vl = [c for c in consultar("DESCRIBE aih_vl")["column_name"] if c.startswith("vl_")]
    return qtd, vl


@st.cache_data(show_spinner=False)
def load_filter_options() -> Tuple[List[str], List[str], pd.DataFrame]:
    """(anos, meses em ordem de calendário, DataFrame de UFs com uf_codigo, uf_sigla, uf_nome)."""
    anos = consultar("SELECT DISTINCT ano FROM aih_qtd ORDER BY ano")["ano"].astype(str).tolist()
    meses = sorted(consultar("SELECT DISTINCT mes FROM aih_qtd")["mes"].tolist(), key=month_to_num)
    ufs = consultar("SELECT DISTINCT uf_codigo, uf_sigla, uf_nome FROM municipios ORDER BY uf_sigla")
    return anos, meses, ufs


@st.cache_data(show_spinner=False)
def ultimo_periodo() -> Tuple[str, str]:
    """(ano, mês) mais recente com dados, ex. ('2026', 'Jan'): o recorte com que o app abre."""
    periodos = consultar("SELECT DISTINCT ano, mes FROM aih_qtd")
    periodos["n"] = periodos["mes"].map(month_to_num)
    ano, mes, _ = periodos.sort_values(["ano", "n"]).iloc[-1]
    return str(ano), str(mes)


@st.cache_data(show_spinner=False, max_entries=32)
def load_municipality_options(selected_ufs: Tuple[str, ...]) -> pd.DataFrame:
    if not selected_ufs:
        return pd.DataFrame(columns=["cod_municipio", "municipio_nome"])
    marcadores = ", ".join("?" * len(selected_ufs))
    return consultar(f"""SELECT cod_municipio, municipio_nome FROM municipios
                         WHERE uf_codigo IN ({marcadores}) ORDER BY municipio_nome""", list(selected_ufs))


# ---------------------------------------------------------------------------
# Consultas do dashboard
# ---------------------------------------------------------------------------
def _filtros(anos, meses, ufs, municipios) -> Tuple[str, list]:
    partes, params = [], []
    for coluna, valores in (("q.ano", anos), ("q.mes", meses), ("m.uf_codigo", ufs),
                            ("q.cod_municipio", municipios)):
        if valores:
            partes.append(f"{coluna} IN ({', '.join('?' * len(valores))})")
            params.extend(str(v) for v in valores)
    return (" AND ".join(partes) or "TRUE"), params


# cache_resource devolve o MESMO DataFrame a cada chamada (cache_data devolve uma cópia nova a cada
# rerun); o app só lê esses DataFrames, nunca os altera. max_entries limita quantos recortes ficam em
# memória: antes o cache crescia sem limite a cada combinação de filtros.
@st.cache_resource(ttl=3600, max_entries=2, show_spinner=False)
def load_consolidated_data(selected_years: Tuple[str, ...], selected_months: Tuple[str, ...],
                           selected_ufs: Tuple[str, ...], selected_municipios: Tuple[str, ...]) -> pd.DataFrame:
    """Quantidades e valores lado a lado, por ano × mês × município, já com UF (uma linha por município/mês)."""
    if not selected_years or not selected_months:
        return pd.DataFrame()
    qtd_cols, vl_cols = get_procedure_columns()
    where, params = _filtros(selected_years, selected_months, selected_ufs, selected_municipios)
    sel_qtd = ", ".join(f"CAST(q.{c} AS INTEGER) AS {c}" for c in qtd_cols)
    sel_vl = ", ".join(f"v.{c}" for c in vl_cols)
    return consultar(f"""
        SELECT q.ano, q.mes, q.cod_municipio,
               m.municipio_nome, m.uf_codigo, m.uf_sigla, m.uf_nome,
               CAST(q.total AS BIGINT) AS total_qtd, v.total AS total_vl,
               {sel_qtd}, {sel_vl}
        FROM aih_qtd q
        JOIN aih_vl v USING (ano, mes, cod_municipio)
        JOIN municipios m ON m.cod_municipio = q.cod_municipio
        WHERE {where}""", params)


@st.cache_data(ttl=3600, max_entries=64, show_spinner=False)
def get_period_totals(year: str, month: str, selected_ufs: Tuple[str, ...],
                      selected_municipios: Tuple[str, ...]) -> Tuple[float, float]:
    """(quantidade total, valor total) de um único mês, com os mesmos filtros geográficos."""
    where, params = _filtros((year,), (month,), selected_ufs, selected_municipios)
    linha = consultar(f"""
        SELECT COALESCE(SUM(q.total), 0) AS qtd, COALESCE(SUM(v.total), 0) AS vl
        FROM aih_qtd q
        JOIN aih_vl v USING (ano, mes, cod_municipio)
        JOIN municipios m ON m.cod_municipio = q.cod_municipio
        WHERE {where}""", params).iloc[0]
    return float(linha["qtd"]), float(linha["vl"])
