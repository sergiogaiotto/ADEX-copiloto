"""Atalhos: playbooks (métodos prontos) e lentes (contexto de uma área).

O caminho principal é "do zero": o dev chega com uma pergunta e o copiloto monta
o método com blocos. Atalhos só aparecem quando servem de verdade:
**aderência ≥ 0,70**, contada por regras em Python (nunca estimada pela IA).

Regras de aderência (sem ``eval``):

- ``tipo_pergunta``: ``valores = ["efeito", ...]``
- ``campo_contem`` / ``campo_nao_contem``: ``campo = "objeto"``, ``termos = [...]``
- ``texto_contem``: termos no texto do dev (objetivo e respostas)
- ``mapeamento_tem``: ``chave = "tratamento" | "alvo" | "tempo" | "desfechos"``
"""

from __future__ import annotations

import re
import tomllib
import unicodedata
from importlib import resources
from pathlib import Path
from typing import Any, Literal

import tomli_w
from pydantic import BaseModel, Field

from adex_copiloto.estudo.carta import Carta
from adex_copiloto.estudo.mapeamento import Mapeamento
from adex_copiloto.estudo.plano import PassoPlano, Plano

LIMIAR_ADERENCIA = 0.70


class Condicao(BaseModel):
    descricao: str
    tipo: Literal["tipo_pergunta", "campo_contem", "campo_nao_contem", "texto_contem", "mapeamento_tem"]
    valores: list[str] = Field(default_factory=list)
    campo: str = ""
    termos: list[str] = Field(default_factory=list)
    chave: str = ""
    peso: float = 1.0


class PassoPlaybook(BaseModel):
    id: str
    bloco: str
    versao: str = ""
    papel: str = ""
    parametros: dict[str, Any] = Field(default_factory=dict)
    decisao_humana_se_falhar: bool = False


class Playbook(BaseModel):
    nome: str
    versao: str = "1.0"
    titulo: str
    descricao: str = ""
    escopo: Literal["nativo", "equipe", "estudo"] = "nativo"
    curado: bool = True
    condicoes: list[Condicao]
    passos: list[PassoPlaybook]
    origem_estudo: str = ""

    def plano(self, carta_versao: int) -> Plano:
        return Plano(
            carta_versao=carta_versao, origem=f"playbook:{self.nome}@{self.versao}", escolhido="A",
            passos=[PassoPlano(**p.model_dump()) for p in self.passos],
        )


class Lente(BaseModel):
    nome: str
    titulo: str
    glossario: dict[str, str] = Field(default_factory=dict)
    kpis: list[str] = Field(default_factory=list)
    tabelas_comuns: list[str] = Field(default_factory=list)
    riscos_tipicos: list[str] = Field(default_factory=list)
    escopo: Literal["nativo", "equipe", "estudo"] = "nativo"
    curado: bool = True

    def para_llm(self) -> dict[str, Any]:
        return self.model_dump(include={"titulo", "glossario", "kpis", "tabelas_comuns", "riscos_tipicos"})


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"\s+", " ", t)


def avaliar_condicao(c: Condicao, *, tipo: str | None, carta: Carta | None, texto: str, mapeamento: Mapeamento | None) -> bool:
    if c.tipo == "tipo_pergunta":
        return tipo in c.valores
    if c.tipo in ("campo_contem", "campo_nao_contem"):
        valor = _norm(carta.valor(c.campo)) if carta is not None else ""
        if not valor:
            return False  # sem informação: conta como não atendida (conservador)
        tem = any(_norm(t) in valor for t in c.termos)
        return tem if c.tipo == "campo_contem" else not tem
    if c.tipo == "texto_contem":
        return any(_norm(t) in _norm(texto) for t in c.termos)
    if c.tipo == "mapeamento_tem":
        return bool(mapeamento is not None and getattr(mapeamento, c.chave, None))
    return False


def aderencia(p: Playbook, *, tipo: str | None, carta: Carta | None = None, texto: str = "",
              mapeamento: Mapeamento | None = None) -> tuple[float, list[str]]:
    """Fração (ponderada) das condições atendidas e a lista das que falharam."""
    total = sum(c.peso for c in p.condicoes) or 1.0
    ok, falhas = 0.0, []
    for c in p.condicoes:
        if avaliar_condicao(c, tipo=tipo, carta=carta, texto=texto, mapeamento=mapeamento):
            ok += c.peso
        else:
            falhas.append(c.descricao)
    return round(ok / total, 2), falhas


class Atalhos:
    """Catálogo de playbooks e lentes: nativos (pacote) + do dev/equipe (pasta local)."""

    def __init__(self, pasta_usuario: Path | None = None) -> None:
        self.pasta_usuario = Path(pasta_usuario) if pasta_usuario else None
        self.playbooks: dict[str, Playbook] = {}
        self.lentes: dict[str, Lente] = {}
        self._carregar_nativos()
        if self.pasta_usuario:
            self._carregar_pasta(self.pasta_usuario)

    def _carregar_nativos(self) -> None:
        base = resources.files("adex_copiloto.recursos")
        for arq in base.joinpath("playbooks").iterdir():
            if arq.name.endswith(".toml"):
                pb = Playbook.model_validate(tomllib.loads(arq.read_text("utf-8")))
                self.playbooks[pb.nome] = pb
        for arq in base.joinpath("lentes").iterdir():
            if arq.name.endswith(".toml"):
                le = Lente.model_validate(tomllib.loads(arq.read_text("utf-8")))
                self.lentes[le.nome] = le

    def _carregar_pasta(self, pasta: Path) -> None:
        for arq in sorted((pasta / "playbooks").glob("*.toml")) if (pasta / "playbooks").exists() else []:
            pb = Playbook.model_validate(tomllib.loads(arq.read_text(encoding="utf-8")))
            self.playbooks[pb.nome] = pb
        for arq in sorted((pasta / "lentes").glob("*.toml")) if (pasta / "lentes").exists() else []:
            le = Lente.model_validate(tomllib.loads(arq.read_text(encoding="utf-8")))
            self.lentes[le.nome] = le

    def ranquear(self, *, tipo: str | None, carta: Carta | None = None, texto: str = "",
                 mapeamento: Mapeamento | None = None) -> list[dict[str, Any]]:
        linhas = []
        for pb in self.playbooks.values():
            a, falhas = aderencia(pb, tipo=tipo, carta=carta, texto=texto, mapeamento=mapeamento)
            linhas.append({"atalho": pb.nome, "titulo": pb.titulo, "aderencia": a, "serve": a >= LIMIAR_ADERENCIA,
                           "por_que_nao": "; ".join(falhas[:2]), "escopo": pb.escopo, "curado": pb.curado})
        return sorted(linhas, key=lambda l: -l["aderencia"])

    def lente(self, vertical: str | None) -> Lente | None:
        if not vertical:
            return None
        return self.lentes.get(_norm(vertical).replace(" ", "_"))

    def salvar_playbook(self, pb: Playbook, pasta: Path | None = None) -> Path:
        destino = Path(pasta or self.pasta_usuario or ".") / "playbooks"
        destino.mkdir(parents=True, exist_ok=True)
        arq = destino / f"{pb.nome}.toml"
        arq.write_text(tomli_w.dumps(pb.model_dump(mode="json", exclude_none=True)), encoding="utf-8")
        self.playbooks[pb.nome] = pb
        return arq

    def salvar_lente(self, le: Lente, pasta: Path | None = None) -> Path:
        destino = Path(pasta or self.pasta_usuario or ".") / "lentes"
        destino.mkdir(parents=True, exist_ok=True)
        arq = destino / f"{le.nome}.toml"
        arq.write_text(tomli_w.dumps(le.model_dump(mode="json", exclude_none=True)), encoding="utf-8")
        self.lentes[le.nome] = le
        return arq


def playbook_do_estudo(nome: str, *, carta: Carta, plano: Plano, mapeamento: Mapeamento | None, estudo_id: str,
                       titulo: str = "", escopo: str = "estudo") -> Playbook:
    """Transforma o método de um estudo num atalho reutilizável (rascunho até curadoria)."""
    condicoes = [Condicao(descricao=f"pergunta do tipo '{carta.tipo_pergunta}'", tipo="tipo_pergunta",
                          valores=[carta.tipo_pergunta], peso=2.0)]
    if mapeamento is not None:
        for chave, descricao in (("tratamento", "há uma ação com data por unidade"), ("tempo", "há eixo de tempo"),
                                 ("alvo", "há um alvo por unidade")):
            if getattr(mapeamento, chave, None):
                condicoes.append(Condicao(descricao=descricao, tipo="mapeamento_tem", chave=chave))
        if mapeamento.grupos:
            condicoes.append(Condicao(descricao="a decisão é por grupo (ex.: cidade)", tipo="campo_contem",
                                      campo="unidade_analise", termos=[g.lower() for g in mapeamento.grupos] + ["cidade", "grupo", "regiao"]))
    passos = [PassoPlaybook(id=p.id, bloco=p.bloco, versao=p.versao, papel=p.papel, parametros=p.parametros,
                            decisao_humana_se_falhar=p.decisao_humana_se_falhar) for p in plano.passos]
    return Playbook(nome=nome, titulo=titulo or nome.replace("_", " "), descricao=f"Criado a partir do estudo {estudo_id}.",
                    escopo=escopo, curado=False, condicoes=condicoes, passos=passos, origem_estudo=estudo_id)
