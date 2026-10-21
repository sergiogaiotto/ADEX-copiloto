"""Plano do estudo: jeitos de responder, método composto por blocos e pré-mortem.

O agente de plano propõe; o Python confere no registro de blocos (nome, versão,
parâmetros e pré-condições) e descarta o que não existir. Tudo o que o painel
faz também existe em código::

    plano = estudo.plan(alternatives=True)
    plano.add_step("codigos_estaveis", adex.blocks.get("desfecho_estavel", "1.0"), before="efeito")
    plano.set("efeito", ponderar_por="domicilios")
    plano.diff()
"""

from __future__ import annotations

import copy
from typing import Any, Literal

from pydantic import BaseModel, Field

from adex_copiloto.estudo.evidencia import Selo, agora_iso

StatusPasso = Literal["pendente", "pronta", "feito", "falhou", "aguarda_decisao", "pulado"]


# --- Esquema que o agente preenche --------------------------------------------------------


class ParametroProposto(BaseModel):
    nome: str = Field(max_length=60)
    valor: str | float | int | bool | list[Any] | dict[str, Any] | None = None


class PassoProposto(BaseModel):
    id: str = Field(max_length=40)
    bloco: str = Field(max_length=60)
    versao: str = Field(default="", max_length=10)
    papel: str = Field(default="", max_length=40)
    parametros: list[ParametroProposto] = Field(default_factory=list, max_length=20)
    decisao_humana_se_falhar: bool = False
    por_que: str = Field(default="", max_length=400)


class JeitoProposto(BaseModel):
    codigo: str = Field(max_length=4)
    titulo: str = Field(max_length=120)
    descricao: str = Field(max_length=600)
    responde: str = Field(max_length=400)
    nao_responde: str = Field(max_length=400)
    forca_maxima: Literal["A", "B", "C", "D"]
    recomendado: bool = False


class RiscoProposto(BaseModel):
    risco: str = Field(max_length=400)
    protecao: str = Field(max_length=400)
    passo: str = Field(default="", max_length=40)


class PropostaPlano(BaseModel):
    jeitos: list[JeitoProposto] = Field(min_length=1, max_length=4)
    escolhido: str = Field(max_length=4)
    passos: list[PassoProposto] = Field(min_length=1, max_length=20)
    pre_mortem: list[RiscoProposto] = Field(default_factory=list, max_length=8)


# --- Plano no estudo --------------------------------------------------------------------------


class PassoPlano(BaseModel):
    id: str
    bloco: str
    versao: str
    papel: str = ""
    parametros: dict[str, Any] = Field(default_factory=dict)
    decisao_humana_se_falhar: bool = False
    por_que: str = ""
    status: StatusPasso = "pendente"
    nota: str = ""
    carta_versao: int | None = None  # versão da carta usada na última execução


class JeitoResponder(BaseModel):
    codigo: str
    titulo: str
    descricao: str
    responde: str
    nao_responde: str
    forca_maxima: Selo
    recomendado: bool = False


class ItemPreMortem(BaseModel):
    risco: str
    protecao: str
    passo: str = ""
    origem: Literal["ia", "python"] = "ia"


class MudancaPlano(BaseModel):
    em: str = Field(default_factory=agora_iso)
    acao: str
    passo: str
    detalhe: str = ""


class Plano(BaseModel):
    versao: int = 1
    carta_versao: int = 1
    jeitos: list[JeitoResponder] = Field(default_factory=list)
    escolhido: str = ""
    passos: list[PassoPlano] = Field(default_factory=list)
    pre_mortem: list[ItemPreMortem] = Field(default_factory=list)
    origem: str = "agente"
    status: Literal["rascunho", "aprovado"] = "rascunho"
    aprovado_por: str = ""
    aprovado_em: str = ""
    mudancas: list[MudancaPlano] = Field(default_factory=list)
    avisos: list[str] = Field(default_factory=list)
    base_aprovada: list[dict[str, Any]] | None = None  # passos na última aprovação (para o diff)

    @property
    def aprovado(self) -> bool:
        return self.status == "aprovado"

    def passo(self, id_: str) -> PassoPlano:
        for p in self.passos:
            if p.id == id_:
                return p
        raise KeyError(f"passo '{id_}' não existe no plano ({', '.join(p.id for p in self.passos)})")

    def _mudou(self, acao: str, passo: str, detalhe: str = "") -> None:
        if self.aprovado:
            self.status = "rascunho"
            self.versao += 1
            self.aprovado_por = self.aprovado_em = ""
        self.mudancas.append(MudancaPlano(acao=acao, passo=passo, detalhe=detalhe))

    def add_step(
        self,
        id_: str,
        bloco: Any,
        *,
        before: str | None = None,
        after: str | None = None,
        papel: str = "",
        **parametros: Any,
    ) -> PassoPlano:
        """Inclui um passo. ``bloco`` pode ser o objeto do registro ou 'nome@versão'."""
        if any(p.id == id_ for p in self.passos):
            raise ValueError(f"já existe um passo '{id_}'")
        nome, versao, papel_bloco = _ref_bloco(bloco)
        novo = PassoPlano(id=id_, bloco=nome, versao=versao, papel=papel or papel_bloco, parametros=parametros)
        if before:
            pos = self.passos.index(self.passo(before))
        elif after:
            pos = self.passos.index(self.passo(after)) + 1
        else:
            pos = len(self.passos)
        self.passos.insert(pos, novo)
        self._mudou("incluiu", id_, f"{nome}@{versao}")
        return novo

    def set(self, id_: str, **parametros: Any) -> PassoPlano:
        p = self.passo(id_)
        antes = dict(p.parametros)
        p.parametros.update(parametros)
        p.status = "pendente" if p.status == "feito" else p.status
        self._mudou("ajustou", id_, f"{antes} → {p.parametros}")
        return p

    def remove_step(self, id_: str) -> None:
        p = self.passo(id_)
        self.passos.remove(p)
        self._mudou("removeu", id_)

    def aprovar(self, por: str) -> None:
        self.status, self.aprovado_por, self.aprovado_em = "aprovado", por, agora_iso()
        self.base_aprovada = [p.model_dump() for p in self.passos]

    def diff(self) -> list[str]:
        """O que mudou desde a última aprovação (ou desde a proposta)."""
        base = self.base_aprovada
        if base is None:
            return [f"{m.acao} {m.passo}: {m.detalhe}".strip(": ") for m in self.mudancas]
        atuais = {p.id: p.model_dump() for p in self.passos}
        antigos = {p["id"]: p for p in base}
        saida = []
        for id_ in atuais.keys() - antigos.keys():
            saida.append(f"+ {id_} ({atuais[id_]['bloco']}@{atuais[id_]['versao']})")
        for id_ in antigos.keys() - atuais.keys():
            saida.append(f"- {id_}")
        for id_ in atuais.keys() & antigos.keys():
            if atuais[id_]["parametros"] != antigos[id_]["parametros"]:
                saida.append(f"~ {id_}: {antigos[id_]['parametros']} → {atuais[id_]['parametros']}")
        return sorted(saida)

    def copia(self) -> "Plano":
        return copy.deepcopy(self)


def _ref_bloco(bloco: Any) -> tuple[str, str, str]:
    if isinstance(bloco, str):
        nome, _, versao = bloco.partition("@")
        return nome, versao, ""
    nome = getattr(bloco, "nome", None)
    versao = getattr(bloco, "versao", "")
    if not nome:
        raise TypeError("bloco precisa ser um bloco registrado ou 'nome@versão'")
    return nome, versao, getattr(bloco, "papel", "")


def plano_de_proposta(proposta: PropostaPlano, carta_versao: int, origem: str = "agente") -> Plano:
    jeitos = [JeitoResponder(**j.model_dump()) for j in proposta.jeitos]
    passos = []
    for p in proposta.passos:
        params = {pp.nome: pp.valor for pp in p.parametros}
        passos.append(
            PassoPlano(
                id=p.id, bloco=p.bloco, versao=p.versao, papel=p.papel, parametros=params,
                decisao_humana_se_falhar=p.decisao_humana_se_falhar, por_que=p.por_que,
            )
        )
    pre = [ItemPreMortem(risco=r.risco, protecao=r.protecao, passo=r.passo) for r in proposta.pre_mortem]
    return Plano(carta_versao=carta_versao, jeitos=jeitos, escolhido=proposta.escolhido, passos=passos, pre_mortem=pre, origem=origem)
