"""Estado do grafo do estudo: só JSON (seguro para o checkpoint cifrado e estrito).

Tabelas e objetos grandes ficam no :class:`~adex_copiloto.harness.armazem.ArmazemDados`.
Funções aqui convertem entre o estado (dicts) e os objetos de domínio.
"""

from __future__ import annotations

import json
from typing import Any, TypedDict

from adex_copiloto.estudo.carta import Carta
from adex_copiloto.estudo.evidencia import Afirmacao, Decisao, Lacuna, Registro
from adex_copiloto.estudo.mapeamento import Mapeamento
from adex_copiloto.estudo.passaporte import Passaporte
from adex_copiloto.estudo.plano import Plano
from adex_copiloto.llm.marcadores import TabelaMarcadores, ValorMarcado


class EstadoEstudo(TypedDict, total=False):
    estudo_id: str
    objetivo: str
    vertical: str | None
    modo: str
    criado_em: str
    seed: int
    textos_dev: list[str]
    enquadramento: dict | None
    atalhos: list[dict]
    playbook_pedido: str | None
    carta: dict | None
    avisos_carta: list[str]
    descoberta: dict | None
    mapeamento: dict | None
    passaporte: dict | None
    plano: dict | None
    sugestoes: list[dict]
    resultados: dict[str, dict]
    afirmacoes: list[dict]
    decisoes: list[dict]
    lacunas: list[dict]
    metricas: dict[str, dict]
    ids: dict[str, int]
    achados: list[dict]
    resumo_critico: str
    narrativa: dict | None
    handoff: dict | None
    etapa: str
    comando: dict | None
    validade: dict[str, int]
    status: str
    portoes: list[dict]
    avisos: list[str]
    itens_por_passo: dict[str, dict]  # passo -> ids de afirmações, decisões e lacunas que ele gerou


# --- leitura ------------------------------------------------------------------------------


def carta(s: dict) -> Carta | None:
    return Carta.model_validate(s["carta"]) if s.get("carta") else None


def plano(s: dict) -> Plano | None:
    return Plano.model_validate(s["plano"]) if s.get("plano") else None


def mapeamento(s: dict) -> Mapeamento | None:
    return Mapeamento.model_validate(s["mapeamento"]) if s.get("mapeamento") else None


def passaporte(s: dict) -> Passaporte | None:
    return Passaporte.model_validate(s["passaporte"]) if s.get("passaporte") else None


def decisoes(s: dict) -> list[Decisao]:
    return [Decisao.model_validate(d) for d in s.get("decisoes") or []]


def afirmacoes(s: dict) -> list[Afirmacao]:
    return [Afirmacao.model_validate(a) for a in s.get("afirmacoes") or []]


def lacunas(s: dict) -> list[Lacuna]:
    return [Lacuna.model_validate(l) for l in s.get("lacunas") or []]


def marcadores(s: dict) -> TabelaMarcadores:
    t = TabelaMarcadores()
    for mid, v in (s.get("metricas") or {}).items():
        t.valores[mid] = ValorMarcado(**v)
    return t


def ids(s: dict) -> Registro:
    return Registro(contadores=dict(s.get("ids") or {}))


# --- escrita --------------------------------------------------------------------------------


def dump(obj: Any) -> Any:
    if obj is None:
        return None
    if isinstance(obj, list):
        return [dump(x) for x in obj]
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    return obj


def valor_json(v: Any) -> Any:
    """Converte números do numpy e NaN para valores JSON simples."""
    try:
        import numpy as np

        if isinstance(v, np.generic):
            v = v.item()
    except ImportError:  # pragma: no cover
        pass
    if isinstance(v, float) and v != v:
        return None
    return v


def dump_marcadores(t: TabelaMarcadores) -> dict[str, dict]:
    return {
        mid: {"id": v.id, "valor": valor_json(v.valor), "texto": v.texto, "descricao": v.descricao, "unidade": v.unidade,
              "origem": v.origem}
        for mid, v in t.valores.items()
    }


TITULO_DATAS_FORA = "Datas fora das ondas declaradas"


def grupos_excluidos(s: dict) -> list[str]:
    """Grupos fora da análise pela decisão mais recente sobre datas fora das ondas.

    Calculado sempre a partir do estado (e não guardado à parte), para que o armazém,
    o estudo e o código do pacote de handoff usem a mesma exclusão.
    """
    ultima = None
    for d in decisoes(s):
        if d.titulo == TITULO_DATAS_FORA and d.status == "decidida":
            ultima = d
    if ultima is None or ultima.escolha != "b":
        return []
    return sorted({str(g) for g in ultima.dados.get("grupos", [])})


def normalizar_payload(obj: Any) -> Any:
    """Comando do dev em JSON simples (numpy vira número, datas viram texto ISO).

    O checkpoint é estrito: sem isto, ``np.int64`` quebra a gravação e ``pd.Timestamp``
    muda de tipo sem aviso.
    """

    def padrao(v: Any) -> Any:
        convertido = valor_json(v)
        if convertido is not v:
            return convertido
        if hasattr(v, "isoformat"):
            return v.isoformat()
        if hasattr(v, "tolist"):
            return v.tolist()
        raise TypeError(f"valor que não vira JSON no comando: {type(v).__name__}")

    return json.loads(json.dumps(obj, default=padrao))


def decisoes_abertas(s: dict, portao: str | None = None) -> list[Decisao]:
    return [d for d in decisoes(s) if d.status == "aberta" and (portao is None or portao in d.bloqueia)]
