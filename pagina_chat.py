"""
pagina_chat.py
--------------
Aba "Assistente IA": interface Streamlit do agente de assistente.py.

Secrets (Streamlit Cloud > Advanced settings > Secrets, ou .streamlit/secrets.toml local):

    GROQ_API_KEY = "gsk_..."                 # obrigatório com o provedor padrão (gratuito: console.groq.com)
    LLM_PROVIDER = "groq"                    # opcional: "groq" (padrão) ou "bedrock"
    LLM_MODEL = "openai/gpt-oss-120b"        # opcional
    LIMITE_PERGUNTAS_SESSAO = 15             # opcional: protege a cota gratuita contra uso abusivo

    [connections.postgresql]                 # opcional: grava os logs na tabela agente_logs
    dialect = "postgresql"
    host = "..."  port = 5432  database = "..."  username = "..."  password = "..."

Com LLM_PROVIDER = "bedrock", as credenciais vêm da cadeia padrão da AWS (AWS_ACCESS_KEY_ID,
AWS_SECRET_ACCESS_KEY e AWS_DEFAULT_REGION nos secrets), de preferência de um usuário IAM com
chave permanente e só a permissão do Bedrock.
"""
import unicodedata

import streamlit as st

from assistente import criar_llm, montar_prompt, responder
from dados import consultar
from registro_logs import destino_configurado, salvar_log
EXEMPLOS = [
    "Qual o valor total gasto por ano?",
    "Quais as 5 UFs com maior valor em cirurgias do aparelho circulatório em 2025?",
    "Como evoluiu a quantidade de partos em Brasília mês a mês em 2025?",
]


def _segredo(chave: str, padrao=None):
    try:
        return st.secrets.get(chave, padrao)
    except Exception:  # sem secrets.toml
        return padrao


def _config_llm() -> dict:
    provedor = str(_segredo("LLM_PROVIDER", "groq")).lower()
    return {"provedor": provedor, "modelo": _segredo("LLM_MODEL"), "api_key": _segredo("GROQ_API_KEY"),
            "regiao": _segredo("AWS_DEFAULT_REGION", "us-east-1")}


@st.cache_resource(show_spinner=False)
def _llm(provedor: str, modelo, api_key, regiao):
    return criar_llm({"provedor": provedor, "modelo": modelo, "api_key": api_key, "regiao": regiao})


@st.cache_data(show_spinner=False)
def _prompt() -> str:
    return montar_prompt(consultar)


def _normalizar(t: str) -> str:
    t = "".join(c for c in unicodedata.normalize("NFD", t.lower()) if unicodedata.category(c) != "Mn")
    return " ".join(t.split())


@st.cache_data(ttl=6 * 3600, max_entries=200, show_spinner=False)
def _resposta_em_cache(pergunta_normalizada: str, pergunta: str, provedor: str, modelo, api_key, regiao):
    """Perguntas repetidas (sem histórico) não gastam a cota do provedor de novo."""
    return responder(pergunta, _llm(provedor, modelo, api_key, regiao), consultar, _prompt())


def _registrar(pergunta: str, resposta: str) -> None:
    """Grava a conversa e avisa na tela se o banco falhou (antes a falha era silenciosa)."""
    r = salvar_log(pergunta, resposta)
    if r.erro:
        st.toast(f"O log desta conversa não foi gravado no banco ({r.erro.split(':')[0]}); "
                 f"ficou no {r.destino}. Detalhes nos logs do app.", icon="⚠️")


def _mostrar_consultas(resp) -> None:
    if not resp.consultas:
        return
    with st.expander(f"🔎 {len(resp.consultas)} consulta(s) SQL executada(s) · {resp.segundos:.1f}s · {resp.modelo}"):
        for i, c in enumerate(resp.consultas, 1):
            st.code(c.sql, language="sql")
            st.caption(f"Consulta {i}: " + (f"erro — {c.erro}" if c.erro else f"{c.linhas} linha(s)"))


def render_chat_page() -> None:
    st.subheader("🤖 Assistente Virtual DATASUS")
    st.markdown(
        "Faça perguntas em português sobre as **quantidades** e os **valores** aprovados de procedimentos. "
        "O assistente escreve e executa consultas SQL nos mesmos dados do dashboard e mostra cada consulta "
        "usada, para você conferir de onde veio cada número."
    )
    st.caption("Exemplos: " + " · ".join(f"*{e}*" for e in EXEMPLOS))
    if destino_configurado() == "postgresql":
        st.caption("📝 As conversas são registradas na tabela `agente_logs` do PostgreSQL.")
    else:
        st.caption("📝 Sem `[connections.postgresql]` nos secrets: as conversas ficam num arquivo local, "
                   "que se perde quando o app reinicia.")

    cfg = _config_llm()
    if cfg["provedor"] == "groq" and not cfg["api_key"]:
        st.info("O assistente não está configurado neste ambiente: falta `GROQ_API_KEY` nos secrets "
                "(chave gratuita em console.groq.com). O restante do dashboard funciona normalmente.")
        return

    limite = int(_segredo("LIMITE_PERGUNTAS_SESSAO", 15))
    st.session_state.setdefault("chat_messages", [])
    st.session_state.setdefault("chat_perguntas", 0)

    for msg in st.session_state.chat_messages[-6:]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg.get("resposta") is not None:
                _mostrar_consultas(msg["resposta"])

    pergunta = st.chat_input("Ex: Qual o valor total gasto por ano?")
    if not pergunta:
        return
    if st.session_state.chat_perguntas >= limite:
        st.warning(f"Limite de {limite} perguntas por sessão atingido (protege a cota gratuita do modelo). "
                   "Recarregue a página para começar uma nova sessão.")
        return

    st.session_state.chat_perguntas += 1
    historico = [{"role": m["role"], "content": m["content"]} for m in st.session_state.chat_messages[-4:]]
    st.session_state.chat_messages.append({"role": "user", "content": pergunta})
    with st.chat_message("user"):
        st.markdown(pergunta)

    with st.chat_message("assistant"):
        try:
            with st.spinner("Consultando os dados..."):
                args = (cfg["provedor"], cfg["modelo"], cfg["api_key"], cfg["regiao"])
                if historico:
                    resp = responder(pergunta, _llm(*args), consultar, _prompt(), historico)
                else:
                    resp = _resposta_em_cache(_normalizar(pergunta), pergunta, *args)
        except Exception as exc:
            nome = type(exc).__name__
            if "RateLimit" in nome or "429" in str(exc):
                aviso = "A cota gratuita do modelo foi atingida no momento. Tente de novo em alguns minutos."
            elif "GraphRecursion" in nome:
                aviso = "A pergunta exigiu passos demais. Tente ser mais específico (período, local, procedimento)."
            else:
                aviso = "Não consegui responder agora. Tente reformular a pergunta."
            st.error(aviso)
            _registrar(pergunta, f"ERRO: {nome}: {exc}"[:2000])
            st.session_state.chat_messages.append({"role": "assistant", "content": aviso})
            return
        st.markdown(resp.texto)
        _mostrar_consultas(resp)
    st.session_state.chat_messages.append({"role": "assistant", "content": resp.texto, "resposta": resp})
    _registrar(pergunta, resp.texto)
