"""Gera os arquivos de data/ a partir do PostgreSQL de produção (ou de exports CSV das tabelas).

O dashboard e o assistente leem só os arquivos de data/ (via DuckDB). Rode este script quando
o banco receber meses novos; depois faça commit dos arquivos atualizados.

    # direto do PostgreSQL (mesmas credenciais do bloco [connections.postgresql] dos secrets)
    python scripts/atualizar_dados.py --postgres "postgresql+psycopg2://usuario:senha@host:5432/banco"

    # ou a partir de CSVs exportados das tabelas (aih_qtd.csv, aih_vl.csv, municipios_ibge.csv,
    # unidade_federacao.csv, dic_geral.csv), por exemplo pelo DBeaver
    python scripts/atualizar_dados.py --csv-dir pasta_dos_exports

Tabelas usadas no PostgreSQL: aih_qtd, aih_vl, municipios_ibge, unidade_federacao, dic_geral.
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

RAIZ = Path(__file__).resolve().parents[1]
DATA = RAIZ / "data"

# Municípios que existem no SIH/SUS mas ainda não estavam na tabela municipios_ibge do banco.
# O código de UF são os 2 primeiros dígitos do código IBGE.
MUNICIPIOS_EXTRAS = [
    {"cod_municipio": "510183", "municipio_nome": "Boa Esperança do Norte"},  # MT, instalado em 2025
]


def ler_tabelas(postgres: str | None, csv_dir: Path | None) -> dict:
    nomes = ["municipios_ibge", "unidade_federacao", "dic_geral"]
    if postgres:
        from sqlalchemy import create_engine
        eng = create_engine(postgres)
        tabelas = {n: pd.read_sql_table(n, eng) for n in nomes}
        tabelas["aih_qtd"] = pd.read_sql_table("aih_qtd", eng)
        tabelas["aih_vl"] = pd.read_sql_table("aih_vl", eng)
        return tabelas
    tabelas = {n: pd.read_csv(csv_dir / f"{n}.csv", dtype=str) for n in nomes}
    for n in ("aih_qtd", "aih_vl"):
        caminho = csv_dir / f"{n}.csv"
        if caminho.exists():
            tabelas[n] = pd.read_csv(caminho, dtype={"ano": str, "mes": str, "cod_municipio": str, "municipio": str})
    return tabelas


def montar_municipios(mun: pd.DataFrame, ufs: pd.DataFrame) -> pd.DataFrame:
    mun = mun.astype(str)
    ufs = ufs.astype(str)
    df = pd.DataFrame({
        "cod_municipio": mun["codigo_municipio"].str.strip(),
        "cod_municipio_ibge": mun["codigo_municipio_dv"].str.strip(),
        "municipio_nome": mun["nome_municipio"].str.strip(),
        "uf_codigo": mun["uf_codigo"].str.strip(),
        "capital": mun["municipio_capital"].str.strip().eq("Sim"),
        "regiao_nome": mun["regiao_nome"].str.strip(),
        "latitude": pd.to_numeric(mun["latitude"], errors="coerce"),
        "longitude": pd.to_numeric(mun["longitude"], errors="coerce"),
    })
    extras = pd.DataFrame(MUNICIPIOS_EXTRAS)
    extras = extras[~extras["cod_municipio"].isin(df["cod_municipio"])]
    if len(extras):
        extras["uf_codigo"] = extras["cod_municipio"].str[:2]
        extras["capital"] = False
        regioes = df.drop_duplicates("uf_codigo").set_index("uf_codigo")["regiao_nome"]
        extras["regiao_nome"] = extras["uf_codigo"].map(regioes)
        df = pd.concat([df, extras], ignore_index=True)
    # sigla e nome da UF vêm da tabela unidade_federacao (co_uf_prova = código IBGE da UF)
    uf = ufs.rename(columns={"co_uf_prova": "uf_codigo", "sg_uf_prova": "uf_sigla", "nome_estado": "uf_nome"})
    uf["uf_codigo"] = uf["uf_codigo"].str.strip()
    nomes_mun = mun.drop_duplicates("uf_codigo").set_index("uf_codigo")["uf_nome"].str.strip()
    uf["uf_nome"] = uf["uf_codigo"].map(nomes_mun).fillna(uf["uf_nome"])  # municipios_ibge tem os nomes com acento
    df = df.merge(uf[["uf_codigo", "uf_sigla", "uf_nome"]], on="uf_codigo", how="left")
    if df["uf_sigla"].isna().any():
        sys.exit(f"Municípios sem UF: {df.loc[df['uf_sigla'].isna(), 'cod_municipio'].tolist()}")
    return df[["cod_municipio", "cod_municipio_ibge", "municipio_nome", "uf_codigo", "uf_sigla", "uf_nome",
               "regiao_nome", "capital", "latitude", "longitude"]].sort_values("cod_municipio")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    origem = ap.add_mutually_exclusive_group(required=True)
    origem.add_argument("--postgres", help="URL SQLAlchemy do PostgreSQL")
    origem.add_argument("--csv-dir", type=Path, help="pasta com os CSVs exportados das tabelas")
    a = ap.parse_args()

    t = ler_tabelas(a.postgres, a.csv_dir)
    municipios = montar_municipios(t["municipios_ibge"], t["unidade_federacao"])
    municipios.to_parquet(DATA / "municipios.parquet", index=False)
    t["dic_geral"][["n", "s"]].to_csv(DATA / "dic_geral.csv", index=False)
    print(f"municipios.parquet: {len(municipios)} municípios em {municipios['uf_sigla'].nunique()} UFs")

    for nome in ("aih_qtd", "aih_vl"):
        if nome not in t:
            print(f"{nome}: sem export, mantendo data/{nome}.parquet")
            continue
        df = t[nome]
        for chave in ("ano", "mes", "cod_municipio", "municipio"):
            df[chave] = df[chave].astype(str).str.strip()
        df.to_parquet(DATA / f"{nome}.parquet", index=False, compression="zstd")
        print(f"{nome}.parquet: {len(df)} linhas, anos {sorted(df['ano'].unique())}")

    sem_cadastro = set(pd.read_parquet(DATA / "aih_qtd.parquet", columns=["cod_municipio"])["cod_municipio"]) \
        - set(municipios["cod_municipio"])
    if sem_cadastro:
        print(f"ATENÇÃO: municípios do SIH sem cadastro (inclua em MUNICIPIOS_EXTRAS): {sorted(sem_cadastro)}")


if __name__ == "__main__":
    main()
