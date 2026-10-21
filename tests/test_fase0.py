"""Fumaça da bateria da Fase 0 contra o hub falso: nenhuma prova quebra e a chave não vaza."""

from __future__ import annotations

import json

import pytest

from adex_copiloto.config.modelos import Config
from adex_copiloto.fase0 import provas  # noqa: F401
from adex_copiloto.fase0.bateria import REGISTRO, Bateria, resumo
from adex_copiloto.seguranca.segredos import Segredo
from falsos import completion, config_teste
from roteiro_wifi import hub_wifi

CHAVE = "chave-super-secreta-da-fase0-123456"


def _hub():
    hub = hub_wifi()
    hub.quando(r"Escreva UMA consulta Oracle", completion(json.dumps({"sql": "SELECT b.cod_cidade, COUNT(*) n FROM cad.base_fibra_mensal b GROUP BY b.cod_cidade"})))
    hub.quando(r"Classifique", completion(json.dumps({"tipo": "previsao", "itens": ["a", "b"], "certeza": 0.7}), reasoning="pensei"))
    return hub


@pytest.mark.parametrize("ids", [["A1", "A2", "A3", "A6", "A7", "A8", "A11", "A12", "A13"], ["B1", "B5", "B8"],
                                 ["D2", "D6", "D11", "D14"], ["E1", "E2", "E3", "E4", "E7", "E9", "E10"],
                                 ["F1", "F2", "F3", "F6", "F8", "F9", "F10"], ["G1", "G4", "G5", "G8"]])
def test_bateria_roda_sem_erro(tmp_path, monkeypatch, ids):
    cfg = config_teste()
    cfg.geral.diretorio_estado = str(tmp_path / "estado")
    hub = _hub()
    monkeypatch.setattr("socket.getaddrinfo", lambda *a, **k: [(None, None, None, None, ("10.0.0.5", 443))])
    if "A1" in ids:
        ids = [i for i in ids if i != "A1"]  # A1 abre TLS de verdade (rede): fica para o ambiente real
    b = Bateria(cfg, Segredo(CHAVE, "hub.chave", "teste"), http_client=hub.http(), repeticoes=2, saida=tmp_path / "res")
    resultados = b.rodar(ids=ids)
    erros = [(r.id, r.observacao, r.medido.get("rastro")) for r in resultados if r.situacao == "erro"]
    assert not erros, erros
    pasta = b.salvar(resultados)
    texto = (pasta / "resultados.json").read_text(encoding="utf-8") + (pasta / "resultados.md").read_text(encoding="utf-8")
    assert CHAVE not in texto
    assert sum(resumo(resultados).values()) == len(ids)


def test_registro_tem_os_blocos():
    assert {p.bloco for p in REGISTRO.values()} == {"A", "B", "C", "D", "E", "F", "G"}
    assert len(REGISTRO) >= 45
    pesadas = [p.id for p in REGISTRO.values() if p.pesada]
    assert set(pesadas) >= {"C4", "C5", "C9"}
