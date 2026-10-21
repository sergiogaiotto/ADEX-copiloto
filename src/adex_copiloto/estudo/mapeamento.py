"""Mapeamento: liga a carta aos dados (qual tabela é a população, qual coluna é a unidade...).

A descoberta (agente) propõe; o Python confere cada nome no catálogo, testa as
ligações com contagens e o dev confirma. Os blocos geram SQL a partir daqui, com
modelos fixos — a IA não escreve esse SQL.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from adex_copiloto.dados.catalogo import Catalogo

Granularidade = Literal["mes", "semana", "dia"]


class FiltroSimples(BaseModel):
    coluna: str
    operador: Literal["=", "!=", ">", ">=", "<", "<=", "in", "not in"] = "="
    valor: Any = None


class FonteDesfecho(BaseModel):
    nome: str = Field(pattern=r"^[a-z][a-z0-9_]{0,30}$")
    tabela: str
    chave_unidade: str
    coluna_data: str
    coluna_codigo: str | None = None
    codigos: list[str | int] = Field(default_factory=list)
    coluna_valor: str | None = None
    agregacao: Literal["contagem", "soma"] = "contagem"
    por: int = 1000
    descricao: str = ""
    principal: bool = False


class FonteTratamento(BaseModel):
    tabela: str
    chave_unidade: str
    coluna_data: str
    limiar: float = Field(default=0.5, gt=0, le=1)
    descricao: str = ""


class FonteAlvo(BaseModel):
    tabela: str
    chave_unidade: str
    coluna: str
    tipo: Literal["binario", "numerico"] = "binario"
    valor_positivo: str | int | None = None
    coluna_data: str | None = None
    descricao: str = ""


class Ligacao(BaseModel):
    de_tabela: str
    de_coluna: str
    para_tabela: str
    para_coluna: str
    taxa: float | None = None  # fração das linhas de 'de' que acham par em 'para'
    verificada: bool = False


class Mapeamento(BaseModel):
    populacao: str
    unidade: str
    tempo: str | None = None
    grupos: list[str] = Field(default_factory=list)
    filtros: list[FiltroSimples] = Field(default_factory=list)
    desfechos: list[FonteDesfecho] = Field(default_factory=list)
    tratamento: FonteTratamento | None = None
    alvo: FonteAlvo | None = None
    atributos: list[str] = Field(default_factory=list)
    ligacoes: list[Ligacao] = Field(default_factory=list)
    periodo_inicio: str | None = None  # AAAA-MM-DD
    periodo_fim: str | None = None  # AAAA-MM-DD (exclusivo)
    granularidade: Granularidade = "mes"
    status: Literal["rascunho", "confirmado"] = "rascunho"
    confirmado_por: str = ""

    @property
    def desfecho_principal(self) -> FonteDesfecho | None:
        if not self.desfechos:
            return None
        return next((d for d in self.desfechos if d.principal), self.desfechos[0])

    def tabelas(self) -> list[str]:
        t = [self.populacao] + [d.tabela for d in self.desfechos]
        if self.tratamento:
            t.append(self.tratamento.tabela)
        if self.alvo:
            t.append(self.alvo.tabela)
        return sorted({x.upper() for x in t})


def validar_mapeamento(m: Mapeamento, catalogo: Catalogo) -> list[str]:
    """Confere nomes no catálogo e papéis das colunas. Lista vazia = ok."""
    problemas: list[str] = []

    def coluna(tabela: str, col: str | None, papel: str, *, pode_pessoal: bool = False) -> None:
        if not col:
            return
        c = catalogo.coluna(tabela, col)
        if c is None:
            problemas.append(f"{papel}: a coluna {tabela}.{col} não existe no catálogo.")
        elif c.bloqueada and not pode_pessoal:
            problemas.append(f"{papel}: {tabela}.{col} é dado pessoal e não pode ser usado assim.")

    pop = catalogo.resolver(m.populacao)
    if pop is None:
        problemas.append(f"população: a tabela {m.populacao} não existe no catálogo.")
        return problemas
    if pop.acesso != "ok":
        problemas.append(f"população: sem acesso a {pop.nome_completo}.")
    coluna(pop.nome_completo, m.unidade, "unidade", pode_pessoal=True)
    # A unidade só conta como identificador de indivíduo se o catálogo a reconhecer como tal
    # (cliente, domicílio, linha...). Loja ou cidade como unidade não é dado de pessoa.
    coluna(pop.nome_completo, m.tempo, "tempo")
    for g in m.grupos:
        coluna(pop.nome_completo, g, "grupo")
        cg = catalogo.coluna(pop.nome_completo, g)
        if cg is not None and cg.identificador:
            problemas.append(f"grupo: {g} identifica indivíduos; agrupe por algo maior (cidade, mês, segmento).")
    for f in m.filtros:
        coluna(pop.nome_completo, f.coluna, "filtro")
    for a in m.atributos:
        coluna(pop.nome_completo, a, "atributo")
    for d in m.desfechos:
        t = catalogo.resolver(d.tabela)
        if t is None:
            problemas.append(f"desfecho '{d.nome}': a tabela {d.tabela} não existe no catálogo.")
            continue
        if t.acesso != "ok":
            problemas.append(f"desfecho '{d.nome}': sem acesso a {t.nome_completo}.")
        coluna(t.nome_completo, d.chave_unidade, f"desfecho '{d.nome}' (chave)", pode_pessoal=True)
        coluna(t.nome_completo, d.coluna_data, f"desfecho '{d.nome}' (data)")
        coluna(t.nome_completo, d.coluna_codigo, f"desfecho '{d.nome}' (código)")
        coluna(t.nome_completo, d.coluna_valor, f"desfecho '{d.nome}' (valor)")
        if d.agregacao == "soma" and not d.coluna_valor:
            problemas.append(f"desfecho '{d.nome}': agregação 'soma' exige coluna_valor.")
    if m.tratamento:
        t = catalogo.resolver(m.tratamento.tabela)
        if t is None:
            problemas.append(f"tratamento: a tabela {m.tratamento.tabela} não existe no catálogo.")
        else:
            coluna(t.nome_completo, m.tratamento.chave_unidade, "tratamento (chave)", pode_pessoal=True)
            coluna(t.nome_completo, m.tratamento.coluna_data, "tratamento (data)")
    if m.alvo:
        t = catalogo.resolver(m.alvo.tabela)
        if t is None:
            problemas.append(f"alvo: a tabela {m.alvo.tabela} não existe no catálogo.")
        else:
            coluna(t.nome_completo, m.alvo.chave_unidade, "alvo (chave)", pode_pessoal=True)
            coluna(t.nome_completo, m.alvo.coluna, "alvo")
    return problemas
