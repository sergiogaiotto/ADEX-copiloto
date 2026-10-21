"""Descoberta de dados: quais tabelas respondem à carta (só metadados).

Dois modos (``agente.deep_nodes`` contém "descobrir" = agente):
- **agente**: Deep Agent com ferramentas de catálogo (listar, buscar, ler ficha);
- **direto**: o Qwen lê o catálogo em lotes grandes (contexto longo de ~196K) e
  depois propõe o mapeamento; é também o recuo quando o agente falha.

Em ambos, o Python confere cada nome no catálogo, pede um reparo com os
problemas listados e descarta o que continuar inválido (com aviso e lacuna).
Os códigos do desfecho (ex.: motivos de chamado) são classificados pelo Qwen a
partir do dicionário (sem dados de clientes), em lotes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

from adex_copiloto.dados.catalogo import Catalogo
from adex_copiloto.dados.porteiro import Modo
from adex_copiloto.erros import AdexErro, AgenteErro
from adex_copiloto.estudo.carta import Carta
from adex_copiloto.estudo.mapeamento import (
    FonteAlvo,
    FonteDesfecho,
    FonteTratamento,
    Ligacao,
    Mapeamento,
    validar_mapeamento,
)
from adex_copiloto.llm import prompts

log = logging.getLogger("adex_copiloto.descoberta")

Papel = Literal["populacao", "desfecho", "tratamento", "alvo", "dicionario", "atributo", "comparacao", "outro"]


class CandidataTabela(BaseModel):
    tabela: str = Field(max_length=130)
    papel: Papel
    para_que: str = Field(max_length=300)
    colunas: list[str] = Field(default_factory=list, max_length=20)


class LoteDescoberta(BaseModel):
    candidatas: list[CandidataTabela] = Field(default_factory=list, max_length=25)


class DesfechoProposto(BaseModel):
    nome: str = Field(max_length=30)
    tabela: str
    chave_unidade: str
    coluna_data: str
    coluna_codigo: str | None = None
    tabela_dicionario: str | None = None
    coluna_dicionario_codigo: str | None = None
    coluna_dicionario_descricao: str | None = None
    descricao: str = Field(default="", max_length=300)


class TratamentoProposto(BaseModel):
    tabela: str
    chave_unidade: str
    coluna_data: str
    descricao: str = Field(default="", max_length=300)


class AlvoProposto(BaseModel):
    tabela: str
    chave_unidade: str
    coluna: str
    tipo: Literal["binario", "numerico"] = "binario"
    valor_positivo: str | None = None
    coluna_data: str | None = None


class LigacaoProposta(BaseModel):
    de_tabela: str
    de_coluna: str
    para_tabela: str
    para_coluna: str


class PropostaMapeamento(BaseModel):
    populacao: str
    unidade: str
    tempo: str | None = None
    grupos: list[str] = Field(default_factory=list, max_length=3)
    atributos: list[str] = Field(default_factory=list, max_length=30)
    desfechos: list[DesfechoProposto] = Field(default_factory=list, max_length=4)
    tratamento: TratamentoProposto | None = None
    alvo: AlvoProposto | None = None
    ligacoes: list[LigacaoProposta] = Field(default_factory=list, max_length=8)
    periodo_inicio: str | None = Field(default=None, description="AAAA-MM-DD")
    periodo_fim: str | None = Field(default=None, description="AAAA-MM-DD, exclusivo")
    candidatas: list[CandidataTabela] = Field(default_factory=list, max_length=20)
    lacunas: list[str] = Field(default_factory=list, max_length=8)


class DecisaoCodigo(BaseModel):
    codigo: str = Field(max_length=40)
    decisao: Literal["incluir", "excluir", "duvida"]
    motivo: str = Field(default="", max_length=200)


class ClassificacaoCodigos(BaseModel):
    codigos: list[DecisaoCodigo] = Field(min_length=1, max_length=120)


@dataclass
class ResultadoDescoberta:
    mapeamento: Mapeamento | None
    candidatas: list[dict[str, Any]]
    lacunas: list[str]
    avisos: list[str]
    codigos: dict[str, list[dict[str, Any]]] = field(default_factory=dict)  # desfecho -> classificação
    modo: str = "direto"


def _nome(n: str | None) -> str | None:
    return n.strip().strip('"').upper() if n else n


def converter(proposta: PropostaMapeamento) -> Mapeamento:
    return Mapeamento(
        populacao=_nome(proposta.populacao),
        unidade=_nome(proposta.unidade),
        tempo=_nome(proposta.tempo),
        grupos=[_nome(g) for g in proposta.grupos],
        atributos=[_nome(a) for a in proposta.atributos],
        desfechos=[
            FonteDesfecho(
                nome=_slug(d.nome), tabela=_nome(d.tabela), chave_unidade=_nome(d.chave_unidade), coluna_data=_nome(d.coluna_data),
                coluna_codigo=_nome(d.coluna_codigo), descricao=d.descricao, principal=i == 0,
            )
            for i, d in enumerate(proposta.desfechos)
        ],
        tratamento=FonteTratamento(tabela=_nome(proposta.tratamento.tabela), chave_unidade=_nome(proposta.tratamento.chave_unidade),
                                   coluna_data=_nome(proposta.tratamento.coluna_data), descricao=proposta.tratamento.descricao)
        if proposta.tratamento else None,
        alvo=FonteAlvo(tabela=_nome(proposta.alvo.tabela), chave_unidade=_nome(proposta.alvo.chave_unidade), coluna=_nome(proposta.alvo.coluna),
                       tipo=proposta.alvo.tipo, valor_positivo=proposta.alvo.valor_positivo, coluna_data=_nome(proposta.alvo.coluna_data))
        if proposta.alvo else None,
        ligacoes=[Ligacao(de_tabela=_nome(l.de_tabela), de_coluna=_nome(l.de_coluna), para_tabela=_nome(l.para_tabela),
                          para_coluna=_nome(l.para_coluna)) for l in proposta.ligacoes],
        periodo_inicio=proposta.periodo_inicio, periodo_fim=proposta.periodo_fim,
    )


def _slug(texto: str) -> str:
    import re
    import unicodedata

    t = unicodedata.normalize("NFKD", texto or "desfecho").encode("ascii", "ignore").decode("ascii").lower()
    t = re.sub(r"[^a-z0-9]+", "_", t).strip("_") or "desfecho"
    if not t[0].isalpha():
        t = "d_" + t
    return t[:30]


def limpar_invalidos(m: Mapeamento, catalogo: Catalogo) -> tuple[Mapeamento | None, list[str]]:
    """Remove partes inválidas do mapeamento. Devolve (mapeamento ou None, avisos)."""
    avisos = []
    if catalogo.resolver(m.populacao) is None or catalogo.coluna(m.populacao, m.unidade) is None:
        return None, ["a população ou a unidade propostas não existem no catálogo."]
    pop = m.populacao

    def ok(t, c):
        return c is None or catalogo.coluna(t, c) is not None

    if not ok(pop, m.tempo):
        avisos.append(f"coluna de tempo {m.tempo} descartada (não existe).")
        m.tempo = None
    m.grupos = [g for g in m.grupos if ok(pop, g) and not (catalogo.coluna(pop, g) and catalogo.coluna(pop, g).identificador)]
    m.atributos = [a for a in m.atributos if ok(pop, a) and not catalogo.coluna(pop, a).bloqueada]
    validos = []
    for d in m.desfechos:
        if catalogo.resolver(d.tabela) and all(ok(d.tabela, c) for c in (d.chave_unidade, d.coluna_data, d.coluna_codigo)):
            validos.append(d)
        else:
            avisos.append(f"desfecho '{d.nome}' descartado (tabela ou coluna inexistente).")
    m.desfechos = validos
    if m.tratamento and not (catalogo.resolver(m.tratamento.tabela) and ok(m.tratamento.tabela, m.tratamento.chave_unidade)
                             and ok(m.tratamento.tabela, m.tratamento.coluna_data)):
        avisos.append("tratamento descartado (tabela ou coluna inexistente).")
        m.tratamento = None
    if m.alvo and not (catalogo.resolver(m.alvo.tabela) and ok(m.alvo.tabela, m.alvo.coluna)):
        avisos.append("alvo descartado (tabela ou coluna inexistente).")
        m.alvo = None
    m.ligacoes = [l for l in m.ligacoes if ok(l.de_tabela, l.de_coluna) and ok(l.para_tabela, l.para_coluna)
                  and catalogo.resolver(l.de_tabela) and catalogo.resolver(l.para_tabela)]
    return m, avisos


def _pedido(carta: Carta, pista: str, extra: dict[str, Any]) -> str:
    return prompts.dados({"carta": carta.para_llm(), "pista_do_dev": pista or "(nenhuma)", **extra}, "descoberta")


def descobrir_direto(cliente, catalogo: Catalogo, carta: Carta, *, pista: str, cfg, lente: dict | None = None) -> tuple[PropostaMapeamento, list[str]]:
    avisos: list[str] = []
    lotes = catalogo.lotes_para_llm(cfg.descoberta.tokens_por_lote, cfg.descoberta.max_lotes)
    total_lotes = len(catalogo.lotes_para_llm(cfg.descoberta.tokens_por_lote))
    if total_lotes > len(lotes):
        avisos.append(f"o catálogo tem {total_lotes} lotes; li {len(lotes)} (descoberta.max_lotes). Use uma pista para focar.")
    candidatas: dict[str, CandidataTabela] = {}
    for i, lote in enumerate(lotes):
        msgs = prompts.mensagens("descobrir", _pedido(carta, pista, {"lote": i + 1, "de": len(lotes), "fichas": lote,
                                                                     "lente": lente or {}}), pista=pista or "(nenhuma)")
        resp = cliente.completar("pensar", msgs, etapa="descobrir", nome=f"descobrir:lote{i + 1}", esquema=LoteDescoberta)
        for c in resp.objeto.candidatas:
            f = catalogo.resolver(c.tabela)
            if f is None:
                avisos.append(f"a IA citou {c.tabela}, que não existe no catálogo: descartada.")
                continue
            candidatas.setdefault(f.nome_completo, c.model_copy(update={"tabela": f.nome_completo}))
    if not candidatas:
        raise AdexErro("A descoberta não achou tabelas candidatas para a carta.",
                       como_resolver="Dê uma pista (esquema ou nome de tabela) com estudo.discover(hint='...').")
    fichas = [catalogo.tabelas[n].para_llm() for n in candidatas]
    msgs = prompts.mensagens(
        "descobrir",
        _pedido(carta, pista, {"candidatas": [c.model_dump() for c in candidatas.values()], "fichas_das_candidatas": fichas,
                               "tarefa": "proponha o mapeamento final"}),
        pista=pista or "(nenhuma)",
    )
    resp = cliente.completar("pensar", msgs, etapa="descobrir", nome="descobrir:mapeamento", esquema=PropostaMapeamento)
    proposta: PropostaMapeamento = resp.objeto
    if not proposta.candidatas:
        proposta.candidatas = list(candidatas.values())
    return proposta, avisos


def descobrir_agente(cliente_chat, catalogo: Catalogo, carta: Carta, *, pista: str, cfg, porteiro=None, callbacks=None) -> PropostaMapeamento:
    from adex_copiloto.agentes.deep import criar_agente, rodar_agente
    from adex_copiloto.agentes.ferramentas import ferramentas_descoberta

    agente = criar_agente(
        cfg, cliente_chat, nome="descoberta", prompt_sistema=prompts.sistema("descobrir", pista=pista or "(nenhuma)"),
        ferramentas=ferramentas_descoberta(catalogo, porteiro), esquema_resposta=PropostaMapeamento,
    )
    resumo = {"carta": carta.para_llm(), "catalogo": catalogo.resumo(), "pista": pista or "(nenhuma)"}
    return rodar_agente(agente, prompts.dados(resumo, "descoberta"), cfg=cfg, callbacks=callbacks)


def reparar(cliente, proposta: PropostaMapeamento, problemas: list[str], catalogo: Catalogo, carta: Carta, pista: str) -> PropostaMapeamento:
    nomes = sorted({proposta.populacao.upper(), *(d.tabela.upper() for d in proposta.desfechos)}
                   | ({proposta.tratamento.tabela.upper()} if proposta.tratamento else set()))
    fichas = [catalogo.resolver(n).para_llm() for n in nomes if catalogo.resolver(n)]
    msgs = prompts.mensagens(
        "descobrir",
        _pedido(carta, pista, {"proposta_anterior": proposta.model_dump(), "problemas_encontrados_pelo_python": problemas,
                               "fichas": fichas, "tarefa": "corrija só o que os problemas apontam"}),
        pista=pista or "(nenhuma)",
    )
    return cliente.completar("pensar", msgs, etapa="descobrir", nome="descobrir:reparo", esquema=PropostaMapeamento).objeto


def classificar_codigos(cliente, fonte, catalogo: Catalogo, d: DesfechoProposto, definicao: str, *, etapa: str = "descobrir",
                        telemetria=None, lote: int = 80) -> list[dict[str, Any]]:
    """Lê o dicionário (sem clientes) e classifica cada código: incluir, excluir ou dúvida."""
    if not (d.tabela_dicionario and d.coluna_dicionario_codigo and d.coluna_dicionario_descricao):
        return []
    ficha = catalogo.resolver(d.tabela_dicionario)
    if ficha is None or any(c.individual for c in ficha.colunas) or (ficha.linhas_estimadas or 0) > 5000:
        return []
    from adex_copiloto.blocos.sql import ident

    sql = (f"SELECT t.{ident(d.coluna_dicionario_codigo)} AS codigo, t.{ident(d.coluna_dicionario_descricao)} AS descricao "
           f"FROM {ident(ficha.nome_completo)} t")
    df = fonte.consultar(sql, modo=Modo.DETALHE, etapa=etapa, telemetria=telemetria, max_linhas=5000).df
    saida = []
    for i in range(0, len(df), lote):
        parte = df.iloc[i : i + lote]
        corpo = prompts.dados(parte.astype(str).to_dict(orient="records"), "dicionario")
        msgs = prompts.mensagens("classificar_dicionario", corpo, definicao=definicao)
        resp = cliente.completar("rapido", msgs, etapa=etapa, nome="classificar_codigos", esquema=ClassificacaoCodigos)
        validos = set(parte["codigo"].astype(str))
        vistos = set()
        for c in resp.objeto.codigos:
            if c.codigo in validos and c.codigo not in vistos:
                vistos.add(c.codigo)
                descricao = parte.loc[parte["codigo"].astype(str) == c.codigo, "descricao"].iloc[0]
                saida.append({"codigo": c.codigo, "descricao": str(descricao), "decisao": c.decisao, "motivo": c.motivo})
        for faltou in sorted(validos - vistos):
            descricao = parte.loc[parte["codigo"].astype(str) == faltou, "descricao"].iloc[0]
            saida.append({"codigo": faltou, "descricao": str(descricao), "decisao": "duvida", "motivo": "a IA não classificou"})
    return saida


def descobrir(cliente, chat_agente, fonte, carta: Carta, *, pista: str, cfg, telemetria, modo_agente: bool, lente: dict | None = None,
              callbacks=None) -> ResultadoDescoberta:
    catalogo = fonte.catalogo()
    avisos: list[str] = []
    modo = "agente" if modo_agente else "direto"
    proposta = None
    if modo_agente:
        try:
            proposta = descobrir_agente(chat_agente, catalogo, carta, pista=pista, cfg=cfg, porteiro=fonte.porteiro(), callbacks=callbacks)
        except AgenteErro as e:
            avisos.append(f"o agente de descoberta parou ({e.mensagem}); usei o modo direto.")
            modo = "direto (recuo)"
    if proposta is None:
        proposta, av = descobrir_direto(cliente, catalogo, carta, pista=pista, cfg=cfg, lente=lente)
        avisos.extend(av)
    mapeamento = converter(proposta)
    problemas = validar_mapeamento(mapeamento, catalogo)
    if problemas:
        try:
            proposta = reparar(cliente, proposta, problemas, catalogo, carta, pista)
            mapeamento = converter(proposta)
            problemas = validar_mapeamento(mapeamento, catalogo)
        except AdexErro as e:
            avisos.append(f"o reparo do mapeamento falhou ({e.mensagem[:120]}).")
    if problemas:
        avisos.extend(problemas)
        mapeamento, av = limpar_invalidos(mapeamento, catalogo)
        avisos.extend(av)
    codigos: dict[str, list[dict[str, Any]]] = {}
    if mapeamento is not None:
        definicao = carta.valor("desfecho")
        propostos = {_slug(x.nome): x for x in proposta.desfechos}
        for d in mapeamento.desfechos:
            dp = propostos.get(d.nome)
            if dp is None:
                continue
            try:
                classes = classificar_codigos(cliente, fonte, catalogo, dp, definicao, telemetria=telemetria)
            except AdexErro as e:
                avisos.append(f"não consegui classificar os códigos de '{d.nome}' ({e.mensagem[:120]}).")
                classes = []
            if classes:
                codigos[d.nome] = classes
                d.codigos = [_codigo_tipado(c["codigo"]) for c in classes if c["decisao"] == "incluir"]
    lacunas = list(proposta.lacunas)
    for c in proposta.candidatas:
        f = catalogo.resolver(c.tabela)
        if f is not None and f.acesso != "ok":
            lacunas.append(f"Sem acesso a {f.nome_completo} ({c.para_que}): peça acesso ao dono da tabela.")
    candidatas = []
    for c in proposta.candidatas:
        f = catalogo.resolver(c.tabela)
        if f is None:
            continue
        candidatas.append({"tabela": f.nome_completo, "papel": c.papel, "para_que": c.para_que, "linhas": f.linhas_estimadas,
                           "acesso": f.acesso, "colunas_pessoais": [x.nome for x in f.colunas if x.bloqueada]})
    return ResultadoDescoberta(mapeamento=mapeamento, candidatas=candidatas, lacunas=lacunas, avisos=avisos, codigos=codigos, modo=modo)


def _codigo_tipado(c: str) -> str | int:
    try:
        return int(c)
    except ValueError:
        return c
