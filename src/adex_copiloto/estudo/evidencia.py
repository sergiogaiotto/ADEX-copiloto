"""Evidência: fatos, hipóteses, recomendações, selos A–D, lacunas e decisões.

Selos (regra fixa, aplicada pelo Python; a IA nunca dá selo acima de D):

- **A**: calculado no universo (censo) ou em amostra probabilística aprovada,
  com incerteza quantificada, premissas checadas e robustez confirmada
  (quando o tipo de pergunta exige, ex.: placebo num estudo de efeito).
- **B**: calculado com incerteza quantificada, mas com premissa ou robustez
  ainda não confirmada; ou padrão consistente nos dados que apoia uma hipótese.
- **C**: descritivo ou correlacional, sem incerteza quantificada ou sem
  controle de premissas; ou amostra de conveniência.
- **D**: não verificado: dito pelo dev, sugerido pela IA, hipótese sem dado.

Uma recomendação herda o menor selo entre as bases em que se apoia.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, Field

Selo = Literal["A", "B", "C", "D"]
TipoAfirmacao = Literal["fato", "hipotese", "recomendacao"]
Base = Literal["censo", "amostra_probabilistica", "amostra_conveniencia", "dev", "ia", "nenhuma"]

_ORDEM: dict[str, int] = {"A": 0, "B": 1, "C": 2, "D": 3}


def agora_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def menor_selo(*selos: str) -> Selo:
    validos = [s for s in selos if s in _ORDEM]
    if not validos:
        return "D"
    return max(validos, key=lambda s: _ORDEM[s])  # type: ignore[return-value]


def calcular_selo(
    *,
    base: Base,
    incerteza: bool = False,
    premissas_ok: bool | None = None,
    robustez_ok: bool | None = None,
    exige_robustez: bool = False,
    natureza: Literal["descritiva", "inferencial"] = "inferencial",
    limite: Selo | None = None,
) -> Selo:
    """Regra única dos selos. ``None`` = não avaliado (conta como não confirmado).

    ``descritiva``: contagens e médias. No censo são exatas (A); numa amostra
    probabilística valem A com incerteza e B sem; por conveniência, C.
    ``inferencial``: comparações, efeitos e modelos (regra completa abaixo).
    ``limite``: o selo máximo possível (ex.: "B" quando a premissa só "não foi
    rejeitada" por um teste sem poder, ou com poucos grupos).
    """
    selo = _selo_sem_limite(base=base, incerteza=incerteza, premissas_ok=premissas_ok, robustez_ok=robustez_ok,
                            exige_robustez=exige_robustez, natureza=natureza)
    return menor_selo(selo, limite) if limite else selo


def _selo_sem_limite(*, base, incerteza, premissas_ok, robustez_ok, exige_robustez, natureza) -> Selo:
    if base in ("dev", "ia", "nenhuma"):
        return "D"
    if natureza == "descritiva":
        if base == "censo":
            return "A"
        if base == "amostra_probabilistica":
            return "A" if incerteza else "B"
        return "C"
    if base == "amostra_conveniencia" or not incerteza:
        return "C"
    if premissas_ok is False:
        return "C"
    robustez_ok_final = robustez_ok is True or (not exige_robustez and robustez_ok is None)
    if premissas_ok is True and robustez_ok_final:
        return "A"
    return "B"


class HistoricoSelo(BaseModel):
    em: str = Field(default_factory=agora_iso)
    de: Selo
    para: Selo
    motivo: str


class Afirmacao(BaseModel):
    id: str  # F-01, H-01, R-01
    tipo: TipoAfirmacao
    texto: str  # pode conter marcadores ⟦M-xx⟧
    selo: Selo = "D"
    bases: list[str] = Field(default_factory=list)  # ids de métricas, afirmações ou artefatos
    origem: Literal["python", "ia", "dev"] = "python"
    bloco: str = ""  # bloco@versão que sustentou
    criada_em: str = Field(default_factory=agora_iso)
    historico: list[HistoricoSelo] = Field(default_factory=list)

    def mudar_selo(self, novo: Selo, motivo: str, *, por: str = "python") -> None:
        if por == "ia" and novo != "D":
            raise ValueError("A IA não pode atribuir selo acima de D.")
        if novo != self.selo:
            self.historico.append(HistoricoSelo(de=self.selo, para=novo, motivo=motivo))
            self.selo = novo


class Lacuna(BaseModel):
    id: str  # L-01
    descricao: str
    impacto: str = ""
    status: Literal["aberta", "fechada", "aceita"] = "aberta"
    criada_em: str = Field(default_factory=agora_iso)
    resolucao: str = ""


class OpcaoDecisao(BaseModel):
    codigo: str
    rotulo: str
    consequencia: str = ""


class Decisao(BaseModel):
    id: str  # D-01
    titulo: str
    pergunta: str
    opcoes: list[OpcaoDecisao]
    recomendada: str | None = None
    origem: str = ""  # bloco, crítico, descoberta, dev
    bloqueia: list[str] = Field(default_factory=list)  # portões: carta, passaporte, plano, aceite, handoff
    status: Literal["aberta", "decidida"] = "aberta"
    escolha: str | None = None
    motivo: str = ""
    autor: str = ""
    em: str | None = None
    dados: dict[str, Any] = Field(default_factory=dict)

    def decidir(self, codigo: str, motivo: str, autor: str) -> None:
        if codigo not in {o.codigo for o in self.opcoes}:
            raise ValueError(f"{self.id}: opção {codigo!r} não existe ({', '.join(o.codigo for o in self.opcoes)}).")
        if not motivo or len(motivo.strip()) < 3:
            raise ValueError(f"{self.id}: diga o motivo da decisão (reason='...').")
        self.status, self.escolha, self.motivo, self.autor, self.em = "decidida", codigo, motivo.strip(), autor, agora_iso()

    @property
    def opcao_escolhida(self) -> OpcaoDecisao | None:
        return next((o for o in self.opcoes if o.codigo == self.escolha), None)


class Metrica(BaseModel):
    """Número calculado pelo Python; a IA só vê o marcador e a descrição."""

    id: str  # M-01 (igual ao marcador)
    nome: str
    valor: float | int | str | None
    texto: str  # já formatado em pt-BR
    unidade: str = ""
    descricao: str = ""
    ic: tuple[float, float] | None = None
    n: int | None = None
    bloco: str = ""
    etapa: str = ""


class Registro(BaseModel):
    """Contadores de ids por tipo (F, H, R, L, D, M, PA, C)."""

    contadores: dict[str, int] = Field(default_factory=dict)

    def proximo(self, prefixo: str) -> str:
        n = self.contadores.get(prefixo, 0) + 1
        self.contadores[prefixo] = n
        return f"{prefixo}-{n:02d}"
