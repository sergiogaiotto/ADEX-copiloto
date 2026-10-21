"""Fixtures comuns: ambiente isolado, sem rede e sem segredos reais."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))
sys.path.insert(0, str(RAIZ / "tests"))


@pytest.fixture(autouse=True)
def ambiente_limpo(tmp_path, monkeypatch):
    """Cada teste roda numa pasta vazia, sem variáveis ADEX_* herdadas."""
    for nome in list(os.environ):
        if nome.upper().startswith(("ADEX_", "LANGFUSE_", "LANGGRAPH_AES_KEY", "NB_SESSION_OCID", "JOB_RUN_OCID",
                                    "OCI_RESOURCE_PRINCIPAL")):
            monkeypatch.delenv(nome, raising=False)
    casa = tmp_path / "casa"
    casa.mkdir()
    monkeypatch.setenv("HOME", str(casa))
    monkeypatch.setenv("USERPROFILE", str(casa))
    trabalho = tmp_path / "trabalho"
    trabalho.mkdir()
    monkeypatch.chdir(trabalho)
    monkeypatch.setenv("ADEX__GERAL__DIRETORIO_ESTADO", str(tmp_path / "estado"))
    yield trabalho
