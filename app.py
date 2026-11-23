"""
app.py
------
Dashboard interativo AIH SUS (DATASUS) — Streamlit.

Os dados vêm dos arquivos Parquet de data/, consultados com DuckDB (dados.py).
O app não depende de banco remoto nem de credenciais; só o Assistente IA usa um
segredo (a chave do provedor do modelo, veja pagina_chat.py).
"""

from typing import Optional

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from dados import (
    calculate_average_ticket,
    get_period_totals,
    get_procedure_columns,
    load_consolidated_data,
    load_filter_options,
    load_municipality_options,
    month_to_num,
    previous_period,
    ultimo_periodo,
)
from pagina_chat import render_chat_page

# ---------------------------------------------------------------------------
# Configuração da página
# ---------------------------------------------------------------------------
st.set_page_config(page_title="Dashboard AIH SUS", page_icon="📊", layout="wide")

BR_CURRENCY_TRANS = str.maketrans({",": ".", ".": ","})


def format_currency(value: float) -> str:
    return f"R$ {value:,.2f}".translate(BR_CURRENCY_TRANS)


def br_format(value, decimals: int = 0) -> str:
    """Format a number using Brazilian locale (. thousands, , decimal)."""
    if pd.isna(value):
        return ""
    try:
        return f"{float(value):,.{decimals}f}".translate(BR_CURRENCY_TRANS)
    except (ValueError, TypeError):
        return str(value)


def format_delta(current: float, previous: float) -> Optional[str]:
    if abs(previous) < 1e-9:
        return None
    delta = ((current - previous) / previous) * 100
    return f"{delta:+.2f}%"


# ---------------------------------------------------------------------------
# Dicionário de labels — baseado no relatório de definição de dados
# ---------------------------------------------------------------------------
_PROC_NAMES = {
    "0101": "Ações coletivas/individuais em saúde",
    "0201": "Coleta de material",
    "0202": "Diagnóstico em laboratório clínico",
    "0203": "Diagnóstico por anatomia patológica e citopatologia",
    "0204": "Diagnóstico por radiologia",
    "0205": "Diagnóstico por ultrassonografia",
    "0206": "Diagnóstico por tomografia",
    "0207": "Diagnóstico por ressonância magnética",
    "0208": "Diagnóstico por medicina nuclear in vivo",
    "0209": "Diagnóstico por endoscopia",
    "0210": "Diagnóstico por radiologia intervencionista",
    "0211": "Métodos diagnósticos em especialidades",
    "0212": "Diagnóstico e procedimentos especiais em hemoterapia",
    "0213": "Diagnóstico em vigilância epidemiológica e ambiental",
    "0214": "Diagnóstico por teste rápido",
    "0301": "Consultas / Atendimentos / Acompanhamentos",
    "0302": "Fisioterapia",
    "0303": "Tratamentos clínicos (outras especialidades)",
    "0304": "Tratamento em oncologia",
    "0305": "Tratamento em nefrologia",
    "0306": "Hemoterapia",
    "0307": "Tratamentos odontológicos",
    "0308": "Tratamento de lesões, envenenamentos e outros, decorrentes de causas externas",
    "0309": "Terapias especializadas",
    "0310": "Parto e nascimento",
    "0311": "Cuidados paliativos",
    "0401": "Pequenas cirurgias e cirurgias de pele, tecido subcutâneo e mucosa",
    "0402": "Cirurgia de glândulas endócrinas",
    "0403": "Cirurgia do sistema nervoso central e periférico",
    "0404": "Cirurgia das vias aéreas superiores, da face, da cabeça e do pescoço",
    "0405": "Cirurgia do aparelho da visão",
    "0406": "Cirurgia do aparelho circulatório",
    "0407": "Cirurgia do aparelho digestivo, órgãos anexos e parede abdominal",
    "0408": "Cirurgia do sistema osteomuscular",
    "0409": "Cirurgia do aparelho geniturinário",
    "0410": "Cirurgia de mama",
    "0411": "Cirurgia obstétrica",
    "0412": "Cirurgia torácica",
    "0413": "Cirurgia reparadora",
    "0414": "Bucomaxilofacial",
    "0415": "Outras cirurgias",
    "0416": "Cirurgia em oncologia",
    "0417": "Anestesiologia",
    "0418": "Cirurgia em nefrologia",
    "0501": "Coleta e exames para fins de doação de órgãos, tecidos e células e de transplante",
    "0502": "Avaliação de morte encefálica",
    "0503": "Ações relacionadas à doação de órgãos e tecidos para transplante",
    "0504": "Processamento de tecidos para transplante",
    "0505": "Transplante de órgãos, tecidos e células",
    "0506": "Acompanhamento e intercorrências no pré e pós-transplante",
    "0603": "Medicamentos de âmbito hospitalar e urgência",
    "0702": "Órteses, próteses e materiais especiais relacionados ao ato cirúrgico",
    "0801": "Ações relacionadas ao estabelecimento",
    "0802": "Ações relacionadas ao atendimento",
}

COLUMN_LABELS: dict = {
    "ano": "Ano de competência",
    "mes": "Mês de competência",
    "cod_municipio": "Município (Cód. IBGE)",
    "municipio": "Município",
    "municipio_nome": "Município",
    "total": "Totalizador do período",
    "total_qtd": "Total de Procedimentos (Qtd)",
    "total_vl": "Valor Total Repassado (R$)",
    **{f"qtd_{code}": f"Qtd – {name}" for code, name in _PROC_NAMES.items()},
    **{f"vl_{code}": f"Vl – {name}" for code, name in _PROC_NAMES.items()},
}


def col_label(col: str) -> str:
    """Retorna o label legível para uma coluna, usando o dicionário de dados."""
    return COLUMN_LABELS.get(col, col)


# Colunas de procedimento lidas do próprio Parquet (algumas existem só numa das matrizes:
# qtd_0417 só em valores; vl_0101, vl_0213 e vl_0311 só em quantidades).
_QTD_PROC_COLS, _VL_PROC_COLS = get_procedure_columns()


# ---------------------------------------------------------------------------
# Título e descrição
# ---------------------------------------------------------------------------
st.title("📊 Dashboard AIH SUS (DATASUS)")
st.caption("Análise de Autorizações de Internação Hospitalar com filtros hierárquicos.")

# ---------------------------------------------------------------------------
# Abas principais — Introdução sempre visível, dados carregados sob demanda
# ---------------------------------------------------------------------------
tab_intro, tab_raw, tab_kpis, tab_charts, tab_chat = st.tabs(
    [
        "🏠 Introdução",
        "Lista dos Dados Armazenados",
        "Estatísticas Descritivas",
        "Gráficos Analíticos",
        "🤖 Assistente IA",
    ]
)

# ── 🏠 Introdução ─────────────────────────────────────────────────────────────
with tab_intro:
    st.header("Sobre o Projeto")
    st.markdown(
        """
        Este painel interativo é o produto final de um pipeline de engenharia de dados
        desenvolvido para analisar as **Autorizações de Internação Hospitalar (AIH)**
        disponibilizadas pelo DATASUS, órgão do Ministério da Saúde responsável pela
        gestão das informações do Sistema Único de Saúde (SUS).

        Os dados exibidos aqui representam os procedimentos hospitalares realizados
        nos municípios brasileiros, consolidando **quantidades aprovadas** e
        **valores financeiros repassados** mês a mês, por município e por grupo de
        procedimento médico.
        """
    )

    st.subheader("🔬 Origem dos Dados")
    st.markdown(
        """
        A fonte primária é o **SIH/SUS** (Sistema de Informações Hospitalares),
        acessado via a interface **TabNet** do DATASUS. Por não existir uma API
        oficial para extração massiva, foi desenvolvido um **web scraper assíncrono**
        com a biblioteca `Playwright` (Python) capaz de simular a interação humana
        com o formulário TabNet — selecionando períodos, grupos de procedimentos
        e formatos de exportação — para coletar os dados de 25 meses históricos.
        """
    )

    st.subheader("⚙️ Pipeline de Engenharia de Dados")
    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown(
            """
            **1 · Extração (Scraping)**
            - Robô `Playwright` assíncrono
            - Iteração sobre 25 meses e 2 métricas (Quantidade e Valor)
            - Captura via `page.expect_popup()` e leitura da tag `<pre>`
            - Tratamento de anti-padrões do TabNet: IDs duplicados, event dispatching manual
            """
        )
    with col2:
        st.markdown(
            """
            **2 · Staging (SQLite)**
            - Armazenamento intermediário sem latência de rede
            - Tipagem dinâmica (*Manifest Typing*) para ingestão tolerante a falhas
            - Estratégia *table-per-period*: 50 tabelas isoladas (25 meses × 2 métricas)
            - Tratamento via `Pandas`: remoção de "IGNORADO", extração de código IBGE, limpeza de hífens e vírgulas
            """
        )
    with col3:
        st.markdown(
            """
            **3 · Produção (PostgreSQL)**
            - Migração das matrizes `aih_qtd` e `aih_vl` consolidadas
            - Tipagem forte: `CHAR` para dimensões, `INTEGER` para contagens, `NUMERIC(20,4)` para valores
            - Credenciais gerenciadas via *Secrets* do Streamlit Cloud / Google Colab
            - Conexão via `SQLAlchemy` com o SGBD do IESB
            """
        )

    st.subheader("📱 Sobre este Aplicativo")
    st.markdown(
        """
        O dashboard foi construído com **Streamlit** e consulta os dados com **DuckDB**
        direto de arquivos **Parquet** versionados no repositório (`data/`), exportados
        do PostgreSQL de produção por `scripts/atualizar_dados.py`. O DuckDB lê do disco
        só as colunas e linhas do recorte pedido, então o app abre rápido e cabe na memória
        do Streamlit Community Cloud, sem depender de rede nem de credenciais.

        **Funcionalidades disponíveis:**
        - **Filtros hierárquicos** na barra lateral: Ano → Mês → UF → Município
        - **KPIs** com delta em relação ao período imediatamente anterior
        - **Lista dos dados** com export para CSV
        - **Estatísticas descritivas** (média, desvio padrão, percentis, assimetria, curtose) por coluna
        - **Gráficos analíticos**: série temporal, ranking Top 10, scatter plot, treemap e heatmap sazonal
        - **Assistente IA**: perguntas em linguagem natural respondidas com consultas SQL (Groq, plano gratuito),
          mostrando cada consulta executada
        """
    )

    st.info(
        "💡 Para explorar os dados, aplique os filtros na barra lateral "
        "e navegue pelas abas ao lado.",
        icon="👈",
    )

# ---------------------------------------------------------------------------
# Carregamento inicial (DuckDB + Parquet)
# ---------------------------------------------------------------------------
_db_error: Optional[Exception] = None
_years: list = []
_months: list = []
_uf_options = None
try:
    _years, _months, _uf_options = load_filter_options()
except Exception as exc:  # arquivos de data/ ausentes ou corrompidos
    _db_error = exc

# ---------------------------------------------------------------------------
# Sidebar — Filtros
# ---------------------------------------------------------------------------
selected_years: list = []
selected_months: list = []
selected_ufs: tuple = ()
selected_municipios: tuple = ()

with st.sidebar:
    if st.button("🔄 Limpar cache"):
        st.cache_data.clear()
        st.rerun()

    if _db_error is None and _years and _months and _uf_options is not None:
        st.header("Filtros")
        # Filtros independentes (sem formulário): cada mudança atualiza o painel na hora, e a lista de
        # municípios acompanha a UF escolhida. O app abre no mês mais recente com dados, no DF.
        _ano_recente, _mes_recente = ultimo_periodo()
        uf_label_to_code = {
            f"{row.uf_sigla} — {row.uf_nome}": row.uf_codigo
            for row in _uf_options.itertuples(index=False)
        }
        _uf_labels = list(uf_label_to_code.keys())
        _uf_default = [lbl for lbl in _uf_labels if lbl.startswith("DF ")] or _uf_labels[:1]

        selected_years = st.multiselect("Ano", options=_years, default=[_ano_recente], key="f_ano")
        selected_months = st.multiselect("Mês", options=_months, default=[_mes_recente], key="f_mes")
        selected_uf_labels = st.multiselect("Unidade da Federação (UF)", options=_uf_labels,
                                            default=_uf_default, key="f_uf")
        selected_ufs = tuple(uf_label_to_code[lbl] for lbl in selected_uf_labels)

        municipio_options_df = load_municipality_options(selected_ufs)
        municipio_label_to_code = {
            f"{row.municipio_nome} ({row.cod_municipio})": row.cod_municipio
            for row in municipio_options_df.itertuples(index=False)
        }
        selected_mun_labels = st.multiselect("Município (opcional; vazio = todos da UF)",
                                             options=list(municipio_label_to_code.keys()),
                                             default=[], key="f_mun")
        selected_municipios = tuple(municipio_label_to_code[label] for label in selected_mun_labels)

    st.caption(
        "ℹ️ Os gráficos **Ranking Top 10**, **Scatter Plot** e **Heatmap Sazonal** sempre exibem "
        "todos os municípios da UF selecionada, independentemente deste filtro."
    )


def _render_db_unavailable() -> None:
    """Mensagem de erro quando os dados não puderam ser lidos."""
    if _db_error is not None:
        st.error("Não foi possível ler os dados de `data/` (arquivos Parquet). "
                 "Gere-os com `python scripts/atualizar_dados.py` (veja o README).")
        st.exception(_db_error)
    elif not _years or not _months:
        st.warning("Não há dados disponíveis.")


# ---------------------------------------------------------------------------
# Validação dos filtros obrigatórios
# ---------------------------------------------------------------------------
selected_years_tuple = tuple(sorted(selected_years))
selected_months_tuple = tuple(sorted(selected_months, key=month_to_num))

# Um único carregamento do recorte, compartilhado pelas três abas de dados (antes cada aba
# carregava a própria cópia do mesmo DataFrame).
_pode_carregar = bool(_db_error is None and _years and selected_years_tuple and selected_months_tuple
                      and selected_ufs)
df_recorte = (load_consolidated_data(selected_years_tuple, selected_months_tuple, selected_ufs,
                                     selected_municipios) if _pode_carregar else pd.DataFrame())

# ── A) Raw Data ──────────────────────────────────────────────────────────────
with tab_raw:
    if _db_error is not None or not _years:
        _render_db_unavailable()
    elif not selected_years_tuple or not selected_months_tuple:
        st.warning("Selecione ao menos um ano e um mês para continuar.")
    elif not selected_ufs:
        st.info("Selecione ao menos uma **Unidade da Federação (UF)** na barra lateral para carregar os dados.")
    else:
        df = df_recorte

        if df.empty:
            st.warning("Nenhum dado encontrado para os filtros selecionados.")
        else:
            # KPIs
            # Último mês que existe no recorte (ex.: com 2026 selecionado, Jan/2026; antes pegava "Dez" de 2026,
            # que não existe, e o delta ficava vazio)
            _periodos = df[["ano", "mes"]].drop_duplicates()
            _periodos = _periodos.assign(_n=_periodos["mes"].map(month_to_num)).sort_values(["ano", "_n"])
            current_year, current_month = _periodos.iloc[-1][["ano", "mes"]]
            prev_period = previous_period(current_year, current_month)

            current_qtd, current_vl = get_period_totals(
                current_year, current_month, selected_ufs, selected_municipios
            )
            if prev_period:
                prev_year, prev_month = prev_period
                prev_qtd, prev_vl = get_period_totals(
                    prev_year, prev_month, selected_ufs, selected_municipios
                )
                delta_caption = (
                    f"Delta: {current_month}/{current_year} em relação a {prev_month}/{prev_year}, "
                    "com os mesmos filtros geográficos."
                )
            else:
                prev_qtd, prev_vl = 0.0, 0.0
                delta_caption = "Delta indisponível: não há período anterior válido para o recorte atual."

            kpi_total_qtd = float(df["total_qtd"].sum())
            kpi_total_vl = float(df["total_vl"].sum())
            kpi_ticket_medio = calculate_average_ticket(kpi_total_vl, kpi_total_qtd)
            current_ticket_medio = calculate_average_ticket(current_vl, current_qtd)
            prev_ticket = calculate_average_ticket(prev_vl, prev_qtd)

            kpi1, kpi2, kpi3 = st.columns(3)
            kpi1.metric(
                "Total de Procedimentos",
                f"{kpi_total_qtd:,.0f}".replace(",", "."),
                delta=format_delta(current_qtd, prev_qtd),
            )
            kpi2.metric(
                "Valor Total Repassado",
                format_currency(kpi_total_vl),
                delta=format_delta(current_vl, prev_vl),
            )
            kpi3.metric(
                "Valor Médio por Procedimento",
                format_currency(kpi_ticket_medio),
                delta=format_delta(current_ticket_medio, prev_ticket),
            )

            st.subheader("Dados consolidados após filtros")
            # Formatar em texto cada célula de um recorte grande (Brasil inteiro: ~139 mil linhas x 110 colunas)
            # criava milhões de strings e estourava a memória. A tabela mostra as primeiras linhas; o CSV traz tudo.
            _MAX_LINHAS_TABELA = 1_000
            _df_display = df.assign(_mes_num=df["mes"].map(month_to_num)).sort_values(
                ["ano", "_mes_num", "municipio_nome"]).drop(columns=["_mes_num"]).head(_MAX_LINHAS_TABELA)
            if len(df) > _MAX_LINHAS_TABELA:
                st.caption(f"Mostrando as primeiras {_MAX_LINHAS_TABELA:,} de {len(df):,} linhas. "
                           "Baixe o CSV para ver o recorte completo.".replace(",", "."))
            _df_display_fmt = _df_display
            for _col in _df_display_fmt.select_dtypes(include="number").columns:
                if _col == "ano":
                    continue
                _decimals = 2 if (_col.startswith("vl_") or _col == "total_vl") else 0
                _df_display_fmt[_col] = _df_display_fmt[_col].apply(br_format, decimals=_decimals)
            st.dataframe(_df_display_fmt, width="stretch")

            _filtros_csv = (selected_years_tuple, selected_months_tuple, selected_ufs, selected_municipios)
            st.download_button(
                label="📥 Baixar dados filtrados em CSV",
                # gerado só quando o botão é clicado (antes era montado em memória a cada interação)
                data=lambda: load_consolidated_data(*_filtros_csv).to_csv(index=False).encode("utf-8"),
                file_name="aih_filtrado.csv",
                mime="text/csv",
            )

            # ── Dicionário de variáveis ────────────────────────────────────
            st.subheader("Dicionário de Variáveis")
            _meta_rows = [
                {"Variável": k, "Descrição": v}
                for k, v in COLUMN_LABELS.items()
                if k not in ("municipio_nome", "total_qtd", "total_vl")
            ]
            # Separar metadados, quantidades e valores em seções distintas
            _meta_ctrl = [r for r in _meta_rows if not r["Variável"].startswith(("qtd_", "vl_"))]
            _meta_qtd = [r for r in _meta_rows if r["Variável"].startswith("qtd_")]
            _meta_vl = [r for r in _meta_rows if r["Variável"].startswith("vl_")]

            st.caption("**Colunas de controle e metadados** (comuns às duas matrizes)")
            st.dataframe(
                pd.DataFrame(_meta_ctrl),
                width="stretch",
                hide_index=True,
            )
            st.caption("**Matriz de Quantidades** (`aih_qtd`) — colunas `qtd_*`")
            st.dataframe(
                pd.DataFrame(_meta_qtd),
                width="stretch",
                hide_index=True,
            )
            st.caption("**Matriz de Valores Financeiros** (`aih_vl`) — colunas `vl_*`")
            st.dataframe(
                pd.DataFrame(_meta_vl),
                width="stretch",
                hide_index=True,
            )

# ── B) Estatísticas Descritivas ───────────────────────────────────────────────
with tab_kpis:
    if _db_error is not None or not _years:
        _render_db_unavailable()
    elif not selected_years_tuple or not selected_months_tuple:
        st.warning("Selecione ao menos um ano e um mês para continuar.")
    elif not selected_ufs:
        st.info("Selecione ao menos uma **Unidade da Federação (UF)** na barra lateral para carregar os dados.")
    else:
        df_stats = df_recorte

        if df_stats.empty:
            st.warning("Nenhum dado encontrado para os filtros selecionados.")
        else:
            st.subheader("Resumo Estatístico")

            qtd_proc_cols, vl_proc_cols = _QTD_PROC_COLS, _VL_PROC_COLS

            # Filtra apenas colunas que realmente existem no dataframe
            all_metric_cols = (
                ["total_qtd", "total_vl"]
                + [c for c in qtd_proc_cols if c in df_stats.columns]
                + [c for c in vl_proc_cols if c in df_stats.columns]
            )

            # Cálculo das estatísticas
            stats_df = df_stats[all_metric_cols]
            
            _base = stats_df.describe().T
            _extra = pd.DataFrame({
                "variance": stats_df.var(numeric_only=True),
                "skewness": stats_df.skew(numeric_only=True),
                "kurtosis": stats_df.kurt(numeric_only=True),
            }, index=stats_df.columns)
            
            _stats_display = _base.join(_extra).map(lambda v: br_format(v, 2))
            _stats_display.index = [col_label(c) for c in _stats_display.index]
            st.dataframe(_stats_display, width="stretch")

            st.markdown("**Indicadores de referência do recorte atual:**")
            c1, c2, c3 = st.columns(3)
            c1.write(f"- UFs no recorte: **{df_stats['uf_sigla'].nunique()}**")
            c2.write(f"- Municípios no recorte: **{df_stats['cod_municipio'].nunique()}**")
            c3.write(f"- Períodos no recorte: **{df_stats[['ano', 'mes']].drop_duplicates().shape[0]}**")

            st.caption(
                "ℹ️ A coluna **count** na tabela acima indica o número de registros (linhas) que compõem o grupo."
            )

# ── Gráficos Analíticos ────────────────────────────────────────────────────
with tab_charts:
    if _db_error is not None or not _years:
        _render_db_unavailable()
    elif not selected_years_tuple or not selected_months_tuple:
        st.warning("Selecione ao menos um ano e um mês para continuar.")
    elif not selected_ufs:
        st.info("Selecione ao menos uma **Unidade da Federação (UF)** na barra lateral para carregar os dados.")
    else:
        df_charts = df_recorte
        # Ranking, scatter e heatmap usam todos os municípios da(s) UF(s). Sem filtro de município,
        # é o mesmo recorte: reaproveita o DataFrame em vez de manter uma segunda cópia na memória.
        df_charts_all = (
            load_consolidated_data(selected_years_tuple, selected_months_tuple, selected_ufs, ())
            if selected_municipios else df_charts
        )

        if df_charts.empty:
            st.warning("Nenhum dado encontrado para os filtros selecionados.")
        else:
            df = df_charts           # Dados filtrados por município
            df_all = df_charts_all   # Dados globais (UF ou Geral)

            # --- COLUNAS DE PROCEDIMENTO ---
            qtd_proc_cols, vl_proc_cols = _QTD_PROC_COLS, _VL_PROC_COLS

            # Filtra apenas o que existe no DataFrame para evitar erros nos Selectboxes
            avail_qtd_cols = ["total_qtd"] + [c for c in qtd_proc_cols if c in df_all.columns]
            avail_vl_cols = ["total_vl"] + [c for c in vl_proc_cols if c in df_all.columns]
            
            # 1. Evolução da quantidade de procedimentos
            st.subheader("1) Evolução da quantidade de procedimentos")
            serie_qtd_col = st.selectbox(
                "Variável de quantidade",
                options=avail_qtd_cols,
                format_func=col_label,
                key="serie_qtd",
            )

            # Valida que a coluna selecionada existe no df atual
            if serie_qtd_col not in df.columns:
                serie_qtd_col = "total_qtd" if "total_qtd" in df.columns else df.columns[0]

            series_qtd = (
                df.groupby(["ano", "mes"], as_index=False)[[serie_qtd_col]]
                .sum()
            )
            series_qtd["_mes_num"] = series_qtd["mes"].apply(month_to_num)
            series_qtd = series_qtd[series_qtd["_mes_num"] > 0].copy()
            series_qtd = series_qtd.sort_values(["ano", "_mes_num"])
            series_qtd["periodo"] = pd.to_datetime(
                series_qtd["ano"].astype(str) + "-" + series_qtd["_mes_num"].astype(str).str.zfill(2) + "-01"
            )

            fig_qtd = go.Figure()
            fig_qtd.add_trace(
                go.Scatter(
                    x=series_qtd["periodo"],
                    y=series_qtd[serie_qtd_col],
                    mode="lines+markers",
                    name=col_label(serie_qtd_col),
                )
            )
            fig_qtd.update_layout(
                yaxis=dict(title=col_label(serie_qtd_col)),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                margin=dict(l=10, r=10, t=20, b=10),
            )
            st.plotly_chart(fig_qtd, width="stretch")

            # 2. Evolução do valor dos procedimentos
            st.subheader("2) Evolução do valor dos procedimentos")
            serie_vl_col = st.selectbox(
                "Variável de valor",
                options=avail_vl_cols,
                format_func=col_label,
                key="serie_vl",
            )

            # Valida que a coluna selecionada existe no df atual
            if serie_vl_col not in df.columns:
                serie_vl_col = "total_vl" if "total_vl" in df.columns else df.columns[0]

            series_vl = (
                df.groupby(["ano", "mes"], as_index=False)[[serie_vl_col]]
                .sum()
            )
            series_vl["_mes_num"] = series_vl["mes"].apply(month_to_num)
            series_vl = series_vl[series_vl["_mes_num"] > 0].copy()
            series_vl = series_vl.sort_values(["ano", "_mes_num"])
            series_vl["periodo"] = pd.to_datetime(
                series_vl["ano"].astype(str) + "-" + series_vl["_mes_num"].astype(str).str.zfill(2) + "-01"
            )

            fig_vl = go.Figure()
            fig_vl.add_trace(
                go.Scatter(
                    x=series_vl["periodo"],
                    y=series_vl[serie_vl_col],
                    mode="lines+markers",
                    name=col_label(serie_vl_col),
                )
            )
            fig_vl.update_layout(
                yaxis=dict(title=col_label(serie_vl_col)),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                margin=dict(l=10, r=10, t=20, b=10),
            )
            st.plotly_chart(fig_vl, width="stretch")
			
            # 3. Ranking Top 10
            st.subheader("3) Ranking Top 10")
            st.caption("ℹ️ Este gráfico sempre inclui todos os municípios, independentemente do filtro de município.")
            
            has_uf_col = "uf_nome" in df_all.columns

            if not has_uf_col:
                rank_level = "Município"
                st.caption("ℹ️ A informação de UF não foi retornada pelo banco. Ranking disponível apenas por Município.")
            else:
                rank_level = st.radio("Nível do ranking", ["Município", "UF"], horizontal=True)

            all_rank_cols = avail_qtd_cols + [c for c in avail_vl_cols if c not in avail_qtd_cols]
            rank_metric = st.selectbox(
                "Métrica",
                options=all_rank_cols,
                format_func=col_label,
                key="rank_metric",
            )

            if rank_level == "UF" and has_uf_col:
                cat_col = "uf_nome"
            else:
                if "municipio_nome" in df_all.columns:
                    cat_col = "municipio_nome"
                else:
                    cat_col = df_all.columns[0]

            ranking = (
                df_all.groupby(cat_col, as_index=False)[rank_metric]
                .sum()
                .sort_values(rank_metric, ascending=False)
                .head(10)
            )

            fig_rank = px.bar(
                ranking,
                x=cat_col,
                y=rank_metric,
                labels={cat_col: "", rank_metric: col_label(rank_metric)},
                text_auto=True,
            )
            fig_rank.update_layout(xaxis_tickangle=-40)
            st.plotly_chart(fig_rank, width="stretch")

            # 4. Scatter Plot
            st.subheader("4) Scatter Plot")
            st.caption("ℹ️ Este gráfico sempre inclui todos os municípios, independentemente do filtro de município.")
            sc_c1, sc_c2, sc_c3 = st.columns(3)
            with sc_c1:
                scatter_x = st.selectbox(
                    "Eixo X (Quantidade)",
                    options=avail_qtd_cols,
                    format_func=col_label,
                    key="scatter_x",
                )
            with sc_c2:
                scatter_y = st.selectbox(
                    "Eixo Y (Valor)",
                    options=avail_vl_cols,
                    format_func=col_label,
                    key="scatter_y",
                )
            with sc_c3:
                corr_method = st.radio(
                    "Correlação",
                    options=["Pearson", "Spearman"],
                    horizontal=True,
                    key="corr_method",
                )

            if scatter_x not in df_all.columns:
                scatter_x = "total_qtd" if "total_qtd" in df_all.columns else df_all.columns[0]
            if scatter_y not in df_all.columns:
                scatter_y = "total_vl" if "total_vl" in df_all.columns else df_all.columns[0]

            group_cols = []
            if 'cod_municipio' in df_all.columns:
                group_cols.append('cod_municipio')
            if 'municipio_nome' in df_all.columns:
                group_cols.append('municipio_nome')

            hover_fields = ["municipio_nome"] if "municipio_nome" in df_all.columns else []
            if 'uf_nome' in df_all.columns:
                group_cols.append('uf_nome')
                hover_fields.append('uf_nome')
            elif 'uf_codigo' in df_all.columns:
                group_cols.append('uf_codigo')

            scatter_df = (
                df_all.groupby(group_cols, as_index=False)[[scatter_x, scatter_y]]
                .sum()
                .sort_values(scatter_y, ascending=False)
            )

            fig_scatter = px.scatter(
                scatter_df,
                x=scatter_x, y=scatter_y,
                hover_data=hover_fields,
                labels={scatter_x: col_label(scatter_x), scatter_y: col_label(scatter_y)},
            )
            
            st.plotly_chart(fig_scatter, width="stretch")

            _CORR_METHOD_MAP = {"Pearson": "pearson", "Spearman": "spearman"}
            _cm = _CORR_METHOD_MAP[corr_method]

            _CORR_SAMPLE_LIMIT = 5_000
            _corr_source = scatter_df
            if len(scatter_df) > _CORR_SAMPLE_LIMIT:
                _corr_source = scatter_df.sample(n=_CORR_SAMPLE_LIMIT, random_state=42)
                st.caption(
                    f"⚠️ Amostra de {_CORR_SAMPLE_LIMIT:,} pontos usada para o cálculo de correlação "
                    f"(dataset completo tem {len(scatter_df):,} linhas)."
                )

            _x_series = pd.to_numeric(_corr_source[scatter_x], errors="coerce").dropna()
            _y_series = pd.to_numeric(_corr_source[scatter_y], errors="coerce").dropna()
            _common_idx = _x_series.index.intersection(_y_series.index)
            _x_series = _x_series.loc[_common_idx]
            _y_series = _y_series.loc[_common_idx]

            _corr_val = None
            if len(_x_series) >= 2:
                if _cm == "spearman":
                    _corr_val = _x_series.rank().corr(_y_series.rank())
                else:
                    _corr_val = _x_series.corr(_y_series)

            if _corr_val is not None and pd.notna(_corr_val):
                st.caption(
                    f"Correlação de {corr_method} entre {col_label(scatter_x)} e "
                    f"{col_label(scatter_y)}: **{_corr_val:.3f}**"
                )
            elif len(_x_series) < 2:
                st.caption("Dados insuficientes para calcular correlação (mínimo 2 pontos).")

            st.markdown(
                """
                **Pearson**: mede a correlação linear entre duas variáveis contínuas. 
                Assume distribuição normal e é sensível a outliers. 
                Ideal quando a relação esperada é linear e os dados não têm desvios extremos.

                **Spearman**: mede a correlação entre as *ordens (ranks)* das variáveis, 
                sendo não-paramétrico e robusto a outliers. 
                Recomendado quando os dados têm distribuição assimétrica ou relação monotônica não-linear.
                """
            )

            # 5. Treemap — Distribuição por categorias de procedimento
            st.subheader("5) Treemap — Distribuição por categorias de procedimento")
            treemap_mode = st.selectbox(
                "Analisar categorias de",
                options=["Quantidade (qtd_*)", "Valor (vl_*)"],
                key="treemap_mode",
            )

            candidate_cols = qtd_proc_cols if treemap_mode.startswith("Quantidade") else vl_proc_cols
            available_cols = [col for col in candidate_cols if col in df.columns]

            if available_cols:
                treemap_selected = st.multiselect(
                    "Colunas a incluir (colunas removidas são agrupadas em 'Outros')",
                    options=available_cols,
                    default=available_cols[:6],
                    format_func=col_label,
                    key="treemap_cols",
                )
                if treemap_selected:
                    all_col_totals = (
                        df[available_cols]
                        .apply(pd.to_numeric, errors="coerce")
                        .fillna(0)
                        .sum()
                    )
                    valid_selected = [c for c in treemap_selected if c in all_col_totals.index]
                    selected_totals = all_col_totals[valid_selected].sort_values(ascending=False)
                    outros_value = float(all_col_totals.drop(index=valid_selected).sum())
                    rows = [(col_label(c), float(v)) for c, v in selected_totals.items()]
                    if outros_value > 0:
                        rows.append(("Outros", outros_value))
                    treemap_data = pd.DataFrame(
                        {
                            "categoria": [r[0] for r in rows],
                            "valor": [r[1] for r in rows],
                        }
                    )
                    treemap_data["valor_fmt"] = treemap_data["valor"].apply(br_format)
                    fig_treemap = px.treemap(
                        treemap_data,
                        path=["categoria"],
                        values="valor",
                        custom_data=["valor_fmt"],
                    )
                    fig_treemap.update_traces(
                        textinfo="label+percent root",
                        hovertemplate="<b>%{label}</b><br>Valor: %{customdata[0]}<br>%{percentRoot:.1%}<extra></extra>",
                    )
                    st.plotly_chart(fig_treemap, width="stretch")
                else:
                    st.info("Selecione ao menos uma coluna para exibir o treemap.")
            else:
                st.info("Não foram encontradas colunas de categorias de procedimento no recorte atual.")

            # 6. Heatmap Sazonal
            st.subheader("6) Heatmap Sazonal")
            st.caption(
                "Mapa de calor mês × ano (ou mês × UF) para identificar sazonalidade e quebras de padrão histórico.\n\n"
                "ℹ️ Este gráfico sempre inclui todos os municípios, independentemente do filtro de município."
            )
            _MONTH_ABBR_PT = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun",
                               "Jul", "Ago", "Set", "Out", "Nov", "Dez"]

            hm_c1, hm_c2 = st.columns(2)
            with hm_c1:
                hm_metric = st.selectbox(
                    "Métrica",
                    options=avail_qtd_cols + [c for c in avail_vl_cols if c not in avail_qtd_cols],
                    format_func=col_label,
                    key="hm_metric",
                )
            with hm_c2:
                _hm_y_opts = ["Ano", "UF"]
                hm_y_axis = st.radio(
                    "Eixo Y",
                    options=_hm_y_opts,
                    horizontal=True,
                    key="hm_y_axis",
                )

            _hm_df = df_all[["ano", "mes", "uf_nome", hm_metric]].copy()  # só as colunas usadas, não a matriz inteira
            _hm_df["_mes_num"] = _hm_df["mes"].apply(month_to_num)
            _hm_df = _hm_df[_hm_df["_mes_num"] > 0].copy()
            _hm_df["_mes_abbr"] = _hm_df["_mes_num"].apply(
                lambda n: _MONTH_ABBR_PT[n - 1] if 1 <= n <= 12 else str(n)
            )

            if hm_metric not in _hm_df.columns:
                hm_metric = "total_qtd" if "total_qtd" in _hm_df.columns else "total_vl"

            if hm_y_axis == "UF" and "uf_nome" in _hm_df.columns:
                _hm_group_col = "uf_nome"
            else:
                _hm_group_col = "ano"

            _hm_pivot = (
                _hm_df.groupby([_hm_group_col, "_mes_num", "_mes_abbr"], as_index=False)[hm_metric]
                .sum()
                .pivot_table(index=_hm_group_col, columns="_mes_num", values=hm_metric, aggfunc="sum")
            )
            _hm_pivot = _hm_pivot.reindex(columns=range(1, 13), fill_value=0)
            _hm_pivot.columns = _MONTH_ABBR_PT

            _hm_z_fmt = [[br_format(v) for v in row] for row in _hm_pivot.values]
            fig_heatmap = go.Figure(
                go.Heatmap(
                    z=_hm_pivot.values,
                    x=_MONTH_ABBR_PT,
                    y=[str(int(float(v))) if _hm_group_col == "ano" else str(v) for v in _hm_pivot.index],
                    colorscale="YlOrRd",
                    customdata=_hm_z_fmt,
                    hovertemplate="%{y} — %{x}: %{customdata}<extra></extra>",
                )
            )
            fig_heatmap.update_layout(
                xaxis_title="Mês",
                yaxis_title=hm_y_axis,
                yaxis=dict(type="category"),
                margin=dict(l=10, r=10, t=20, b=10),
            )
            st.plotly_chart(fig_heatmap, width="stretch")

# ── 🤖 Assistente IA (Text-to-SQL) ─────────────────────────────────────────
with tab_chat:
    render_chat_page()
