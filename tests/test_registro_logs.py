"""Registro das conversas: sem banco configurado vai para o CSV; falha no banco é informada, não silenciosa."""
import csv

import registro_logs


def test_sem_postgres_grava_no_csv(tmp_path, monkeypatch):
    monkeypatch.setattr(registro_logs, "LOG_LOCAL", tmp_path / "logs.csv")
    monkeypatch.setattr(registro_logs, "config_postgres", lambda: None)
    r = registro_logs.salvar_log("pergunta", "resposta")
    assert (r.destino, r.erro) == ("arquivo local", None)
    linhas = list(csv.reader(open(tmp_path / "logs.csv", encoding="utf-8")))
    assert linhas[0] == ["data_hora", "pergunta", "resposta"] and linhas[1][1:] == ["pergunta", "resposta"]


def test_falha_no_postgres_e_informada_e_cai_no_csv(tmp_path, monkeypatch):
    monkeypatch.setattr(registro_logs, "LOG_LOCAL", tmp_path / "logs.csv")
    cfg = {"host": "127.0.0.1", "port": 1, "database": "x", "username": "u", "password": "segredo"}
    monkeypatch.setattr(registro_logs, "config_postgres", lambda: cfg)
    r = registro_logs.salvar_log("pergunta", "resposta")
    assert r.destino == "arquivo local"
    assert r.erro and r.erro.startswith("OperationalError")
    assert "segredo" not in r.erro
    assert (tmp_path / "logs.csv").exists()
