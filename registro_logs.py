"""
registro_logs.py
----------------
Registro das conversas do assistente (pergunta e resposta) na tabela agente_logs do PostgreSQL,
com um CSV local como reserva.

A versão anterior usava `st.connection("postgresql")` e, quando a gravação falhava, só fazia
um print: o usuário via a resposta normalmente e o log se perdia sem aviso. Aqui:
- o engine é montado explicitamente a partir do bloco [connections.postgresql] dos secrets,
  com timeout de conexão (um banco inacessível não trava a resposta);
- o resultado da gravação é devolvido para a interface, que mostra onde o log foi parar e,
  se o banco falhou, o motivo (sem expor credenciais);
- a falha também vai para o log do servidor (Streamlit Cloud > Manage app > Logs).
"""
import csv
import datetime
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import streamlit as st
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

LOG_LOCAL = Path(__file__).parent / "logs" / "agente_logs.csv"
_CAMPOS = ("host", "database", "username", "password")
_log = logging.getLogger("aih_sus.logs")


@dataclass
class ResultadoLog:
    destino: str               # "postgresql", "arquivo local" ou "nenhum"
    erro: Optional[str] = None  # motivo da falha no PostgreSQL, se houve


def config_postgres() -> Optional[dict]:
    """Bloco [connections.postgresql] dos secrets, ou None se não existir ou estiver incompleto."""
    try:
        cfg = dict(st.secrets["connections"]["postgresql"])
    except Exception:
        return None
    return cfg if all(cfg.get(c) for c in _CAMPOS) else None


def destino_configurado() -> str:
    return "postgresql" if config_postgres() else "arquivo local"


@st.cache_resource(show_spinner=False)
def _engine(host: str, port: int, database: str, username: str, password: str):
    url = URL.create("postgresql+psycopg2", username=username, password=password,
                     host=host, port=port, database=database)
    eng = create_engine(url, pool_pre_ping=True, pool_size=1, max_overflow=1,
                        connect_args={"connect_timeout": 5})
    with eng.begin() as con:  # uma vez por processo
        con.execute(text("""CREATE TABLE IF NOT EXISTS agente_logs (
                                id SERIAL PRIMARY KEY, data_hora TIMESTAMP, pergunta TEXT, resposta TEXT)"""))
    return eng


def _gravar_postgres(cfg: dict, agora, pergunta: str, resposta: str) -> None:
    eng = _engine(str(cfg["host"]), int(cfg.get("port", 5432)), str(cfg["database"]),
                  str(cfg["username"]), str(cfg["password"]))
    with eng.begin() as con:
        con.execute(text("INSERT INTO agente_logs (data_hora, pergunta, resposta) VALUES (:dh, :p, :r)"),
                    {"dh": agora, "p": pergunta, "r": resposta})


def _gravar_csv(agora, pergunta: str, resposta: str) -> bool:
    try:
        LOG_LOCAL.parent.mkdir(exist_ok=True)
        novo = not LOG_LOCAL.exists()
        with LOG_LOCAL.open("a", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            if novo:
                w.writerow(["data_hora", "pergunta", "resposta"])
            w.writerow([agora.isoformat(timespec="seconds"), pergunta, resposta])
        return True
    except OSError as exc:
        _log.warning("Log local falhou: %s", exc)
        return False


def salvar_log(pergunta: str, resposta: str) -> ResultadoLog:
    """Grava no PostgreSQL se configurado; se não houver configuração ou a gravação falhar, no CSV local."""
    agora = datetime.datetime.now()
    cfg = config_postgres()
    erro = None
    if cfg:
        try:
            _gravar_postgres(cfg, agora, pergunta, resposta)
            return ResultadoLog("postgresql")
        except Exception as exc:
            # a mensagem do driver traz host/porta, nunca a senha
            erro = f"{type(exc).__name__}: {str(exc).strip().splitlines()[0][:200]}"
            _log.warning("Log no PostgreSQL falhou: %s", erro)
            _engine.clear()  # próxima tentativa recria o engine (ex.: secrets corrigidos)
    destino = "arquivo local" if _gravar_csv(agora, pergunta, resposta) else "nenhum"
    return ResultadoLog(destino, erro)
