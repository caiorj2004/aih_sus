"""Assistente: trava de SQL e laço do agente (com um modelo falso, sem gastar cota de API)."""
import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

import assistente
import dados


@pytest.mark.parametrize("sql", [
    "SELECT ano, SUM(total) FROM aih_qtd WHERE mes = 'Set' GROUP BY ano;",
    "WITH t AS (SELECT 1 AS x) SELECT * FROM t",
])
def test_sql_de_leitura_passa(sql):
    assert assistente.validar_sql(sql)


@pytest.mark.parametrize("sql", [
    "DROP VIEW aih_qtd", "SELECT 1; SELECT 2", "COPY aih_qtd TO 'x.csv'", "ATTACH 'x.db' AS y",
    "SELECT * FROM read_csv('/etc/passwd')", "SELECT getenv('GROQ_API_KEY')", "SELECT * FROM glob('*')",
])
def test_sql_perigoso_e_recusado(sql):
    with pytest.raises(assistente.SQLRecusado):
        assistente.validar_sql(sql)


class _ModeloFalso(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def test_agente_executa_consulta_e_devolve_resposta():
    roteiro = iter([
        AIMessage(content="", tool_calls=[{"name": "consultar_sql", "id": "1",
                                           "args": {"sql": "SELECT ano, SUM(total) AS t FROM aih_vl GROUP BY ano"}}]),
        AIMessage(content="", tool_calls=[{"name": "consultar_sql", "id": "2", "args": {"sql": "DROP VIEW aih_vl"}}]),
        AIMessage(content="Resposta final."),
    ])
    prompt = assistente.montar_prompt(dados.consultar)
    resp = assistente.responder("Valor por ano?", _ModeloFalso(messages=roteiro), dados.consultar, prompt)
    assert resp.texto == "Resposta final."
    assert resp.consultas[0].linhas == 3 and resp.consultas[0].erro is None
    assert "SQLRecusado" in resp.consultas[1].erro
