"""
assistente.py
-------------
Agente de IA (text-to-SQL) sobre os dados do dashboard, independente do Streamlit.

- Modelo: Groq (plano gratuito, chave que não expira) por padrão; AWS Bedrock opcional.
  A versão anterior usava credenciais temporárias da AWS (STS, com AWS_SESSION_TOKEN),
  que expiravam e precisavam ser renovadas à mão no console.
- Agente: `langchain.agents.create_agent` com tool calling (o modelo chama a ferramenta
  `consultar_sql` com argumentos estruturados). Substitui o agente ReAct por texto
  (`zero-shot-react-description`), mais lento e sujeito a erros de parsing.
- Segurança: banco DuckDB aberto em modo somente leitura + validação do SQL (uma única
  instrução SELECT/WITH, sem funções que leem arquivos) + LIMIT automático.
"""
import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Callable, List, Optional

import pandas as pd

MAX_LINHAS = 200          # linhas devolvidas ao modelo por consulta
MAX_CARACTERES = 6000     # tamanho máximo do resultado enviado ao modelo
MAX_PASSOS = 12           # limite de passos do agente (cada consulta = 2 passos)

_PROIBIDO = re.compile(
    r"\b(insert|update|delete|drop|create|alter|attach|detach|copy|export|import|install|load|"
    r"pragma|set|reset|call|checkpoint|vacuum|use|grant|revoke|truncate)\b"
    r"|\b(read_\w+|glob|sniff_csv|parquet_\w+|query_table|query|getenv|current_setting|duckdb_\w+|"
    r"pragma_\w+|which_secret|load_extension)\s*\(",   # getenv: os secrets do Streamlit viram variáveis de ambiente
    re.IGNORECASE,
)


class SQLRecusado(ValueError):
    pass


def validar_sql(sql: str) -> str:
    """Aceita uma única consulta de leitura (SELECT/WITH). Devolve o SQL limpo ou levanta SQLRecusado."""
    limpo = re.sub(r"--[^\n]*|/\*.*?\*/", " ", sql, flags=re.S).strip().rstrip(";").strip()
    if not limpo:
        raise SQLRecusado("Consulta vazia.")
    sem_textos = re.sub(r"'(?:[^']|'')*'", "''", limpo)  # 'Set' (setembro) é dado, não comando
    if ";" in sem_textos:
        raise SQLRecusado("Envie uma única instrução SQL por vez.")
    if not re.match(r"(?is)^(select|with)\b", sem_textos):
        raise SQLRecusado("Só são permitidas consultas de leitura (SELECT ou WITH).")
    if m := _PROIBIDO.search(sem_textos):
        raise SQLRecusado(f"Comando ou função não permitida: {m.group(0).strip()}")
    return limpo


@dataclass
class RegistroConsulta:
    sql: str
    linhas: Optional[int] = None
    erro: Optional[str] = None


@dataclass
class Resposta:
    texto: str
    consultas: List[RegistroConsulta] = field(default_factory=list)
    segundos: float = 0.0
    modelo: str = ""


def _sem_acento(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn")


def montar_prompt(consultar: Callable[[str], pd.DataFrame]) -> str:
    """Prompt de sistema com o esquema real e o dicionário de procedimentos (evita consultas exploratórias)."""
    qtd = [c for c in consultar("DESCRIBE aih_qtd")["column_name"] if c.startswith("qtd_")]
    vl = [c for c in consultar("DESCRIBE aih_vl")["column_name"] if c.startswith("vl_")]
    periodos = consultar("SELECT ano, list(DISTINCT mes) AS meses FROM aih_qtd GROUP BY ano ORDER BY ano")
    dic = consultar("SELECT s, n FROM dic_geral ORDER BY s")
    sufixos_qtd = {c[4:] for c in qtd}
    sufixos_vl = {c[3:] for c in vl}

    linhas_dic = []
    for s, n in dic.itertuples(index=False):
        nota = "" if (s in sufixos_qtd and s in sufixos_vl) else (
            " [só valor]" if s in sufixos_vl else " [só quantidade]" if s in sufixos_qtd else " [sem coluna]")
        linhas_dic.append(f"- {s}: {n}{nota}")
    linhas_periodo = [f"- {a}: {len(m)} mês(es) ({', '.join(sorted(m, key=lambda x: ['Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez'].index(x)))})"
                      for a, m in periodos.itertuples(index=False)]

    return f"""Você é um analista de dados do SUS. Responde perguntas sobre procedimentos hospitalares
do SIH/SUS (DATASUS) consultando um banco DuckDB com a ferramenta `consultar_sql`.

TABELAS (SQL no dialeto DuckDB):
- aih_qtd: quantidade aprovada de procedimentos. Colunas: ano (texto, ex. '2025'), mes (texto: 'Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez'),
  cod_municipio (texto, 6 dígitos), municipio (nome em maiúsculas), total (soma de todos os grupos) e uma coluna qtd_XXXX por grupo de procedimento.
- aih_vl: valor aprovado em reais. Mesmas chaves (ano, mes, cod_municipio, municipio), total e uma coluna vl_XXXX por grupo.
- municipios: cod_municipio (junta com as tabelas acima), municipio_nome (com acentos), uf_sigla (ex. 'DF'), uf_nome, regiao_nome, capital (boolean).
Uma linha = um município em um mês. Para UF ou região, faça JOIN com municipios por cod_municipio.

PERÍODOS DISPONÍVEIS:
{chr(10).join(linhas_periodo)}
Anos com menos de 12 meses estão incompletos: avise isso ao comparar anos.

GRUPOS DE PROCEDIMENTO (sufixo XXXX das colunas qtd_XXXX / vl_XXXX):
{chr(10).join(linhas_dic)}

REGRAS:
1. Pergunta geral ("total", "quanto foi gasto", sem citar procedimento): use a coluna total.
2. Pergunta sobre um tipo de procedimento: escolha o(s) grupo(s) da lista acima e use qtd_XXXX ou vl_XXXX.
   Se a pergunta for vaga (ex. "problemas do coração"), diga quais grupos você usou e por quê.
3. Brasília é o único município do Distrito Federal (uf_sigla = 'DF').
4. Nunca invente números: todo número da resposta deve vir de uma consulta. Se uma consulta der erro, corrija e tente de novo.
5. Some no SQL (SUM, GROUP BY) em vez de trazer linhas soltas; resultados são limitados a {MAX_LINHAS} linhas.
6. Responda em português do Brasil, com números no formato brasileiro (1.234.567,89 e R$ para valores),
   dizendo o período e o recorte geográfico considerados. Os dados são de procedimentos aprovados, não de pessoas.
"""


def criar_ferramenta(consultar: Callable[[str], pd.DataFrame], registro: List[RegistroConsulta]):
    from langchain_core.tools import tool

    @tool
    def consultar_sql(sql: str) -> str:
        """Executa UMA consulta SQL de leitura (SELECT ou WITH, dialeto DuckDB) nas tabelas aih_qtd,
        aih_vl e municipios e devolve o resultado em CSV (no máximo 200 linhas)."""
        item = RegistroConsulta(sql=sql)
        registro.append(item)
        try:
            limpo = validar_sql(sql)
            df = consultar(f"SELECT * FROM ({limpo}) AS consulta LIMIT {MAX_LINHAS + 1}")
        except Exception as exc:  # o erro volta para o modelo corrigir a consulta
            item.erro = f"{type(exc).__name__}: {exc}"[:500]
            return f"ERRO: {item.erro}"
        item.linhas = min(len(df), MAX_LINHAS)
        aviso = f"\n(resultado cortado em {MAX_LINHAS} linhas; agregue mais)" if len(df) > MAX_LINHAS else ""
        csv = df.head(MAX_LINHAS).to_csv(index=False)
        if len(csv) > MAX_CARACTERES:
            csv = csv[:MAX_CARACTERES] + "\n...(cortado)"
        return csv + aviso

    return consultar_sql


def criar_llm(config: dict):
    """config: provedor ('groq' ou 'bedrock'), modelo e credenciais (vindas dos secrets)."""
    provedor = config.get("provedor", "groq")
    if provedor == "groq":
        from langchain_groq import ChatGroq
        return ChatGroq(model=config.get("modelo") or "openai/gpt-oss-120b", api_key=config["api_key"],
                        temperature=0, max_retries=2, timeout=60)
    if provedor == "bedrock":
        from langchain_aws import ChatBedrockConverse
        return ChatBedrockConverse(model=config.get("modelo") or "us.amazon.nova-lite-v1:0",
                                   region_name=config.get("regiao", "us-east-1"), temperature=0)
    raise ValueError(f"Provedor desconhecido: {provedor}")


def responder(pergunta: str, llm, consultar: Callable[[str], pd.DataFrame], prompt: str,
              historico: Optional[list] = None) -> Resposta:
    from langchain.agents import create_agent

    registro: List[RegistroConsulta] = []
    agente = create_agent(llm, tools=[criar_ferramenta(consultar, registro)], system_prompt=prompt)
    mensagens = list(historico or []) + [{"role": "user", "content": pergunta}]
    inicio = time.time()
    saida = agente.invoke({"messages": mensagens}, config={"recursion_limit": MAX_PASSOS})
    texto = saida["messages"][-1].content
    if isinstance(texto, list):  # alguns provedores devolvem blocos de conteúdo
        texto = "".join(b.get("text", "") for b in texto if isinstance(b, dict))
    return Resposta(texto=texto.strip(), consultas=registro, segundos=time.time() - inicio,
                    modelo=getattr(llm, "model_name", None) or getattr(llm, "model", None) or getattr(llm, "model_id", ""))
