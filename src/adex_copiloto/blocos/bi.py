"""Blocos de BI: medidas e KPIs com granularidade consistente, reconciliação e decomposição."""

from __future__ import annotations

from typing import Literal

import pandas as pd
from pydantic import BaseModel, Field

from adex_copiloto.blocos import estatistica as M
from adex_copiloto.blocos import graficos, sql
from adex_copiloto.blocos.base import Bloco, Parametros, Precondicao
from adex_copiloto.blocos.registro import registrar_nativo
from adex_copiloto.estudo.mapeamento import FiltroSimples


class Medida(BaseModel):
    nome: str = Field(pattern=r"^[a-z][a-z0-9_]{0,30}$")
    tipo: Literal["soma", "contagem", "contagem_distinta", "razao"]
    coluna: str | None = None
    numerador: str | None = None  # nome de outra medida
    denominador: str | None = None
    descricao: str = ""


def _sql_medidas(tabela: str, dimensoes: list[str], medidas: list[Medida], filtros: list[FiltroSimples],
                 coluna_data: str | None, granularidade: str | None) -> tuple[str, dict]:
    binds: dict = {}
    dims = [f"t.{sql.ident(d)}" for d in dimensoes]
    sel = [f"{d} AS {sql.ident(nome).lower()}" for d, nome in zip(dims, dimensoes)]
    grupo = list(dims)
    if coluna_data and granularidade:
        per = sql.trunc(f"t.{sql.ident(coluna_data)}", granularidade)
        sel.append(f"{per} AS periodo")
        grupo.append(per)
    for m in medidas:
        if m.tipo == "soma":
            sel.append(f"SUM(t.{sql.ident(m.coluna)}) AS {m.nome}")
        elif m.tipo == "contagem":
            sel.append(f"COUNT({'t.' + sql.ident(m.coluna) if m.coluna else '*'}) AS {m.nome}")
        elif m.tipo == "contagem_distinta":
            sel.append(f"COUNT(DISTINCT t.{sql.ident(m.coluna)}) AS {m.nome}")
    cond = sql._filtros(filtros, "t", binds)
    texto = f"SELECT {', '.join(sel)} FROM {sql.ident(tabela)} t {sql._where(cond)}"
    if grupo:
        texto += f" GROUP BY {', '.join(grupo)}"
    return texto, binds


@registrar_nativo
class MedidasKpi(Bloco):
    nome = "medidas_kpi"
    versao = "1.0"
    papel = "BI"
    titulo = "Medidas e KPIs por dimensão, com reconciliação"
    descricao = (
        "Calcula medidas declaradas (soma, contagem, contagem distinta, razão) por dimensões, na fonte. Razões são "
        "soma/soma no nível pedido (nunca média de razões). Confere se as partes somam o total."
    )
    tipos_pergunta = frozenset({"descricao", "diagnostico"})
    produz = ("kpis",)

    class Parametros(Parametros):
        tabela: str
        dimensoes: list[str] = Field(default_factory=list, max_length=4)
        medidas: list[Medida] = Field(min_length=1, max_length=20)
        filtros: list[FiltroSimples] = Field(default_factory=list)
        coluna_data: str | None = None
        granularidade: Literal["mes", "semana", "dia"] | None = None

    def precondicoes(self, ctx, p):
        pre = super().precondicoes(ctx, p)
        nomes = {m.nome for m in p.medidas}
        for m in p.medidas:
            if m.tipo == "razao":
                ok = m.numerador in nomes and m.denominador in nomes
                pre.append(Precondicao(f"razão '{m.nome}' aponta para medidas existentes", ok))
            elif m.tipo in ("soma", "contagem_distinta"):
                pre.append(Precondicao(f"medida '{m.nome}' tem coluna", bool(m.coluna)))
        return pre

    def executar(self, ctx, p):
        r = ctx._resultado
        base = [m for m in p.medidas if m.tipo != "razao"]
        q, b = _sql_medidas(p.tabela, p.dimensoes, base, p.filtros, p.coluna_data, p.granularidade)
        partes = ctx.consultar("medidas", q, b)
        q_t, b_t = _sql_medidas(p.tabela, [], base, p.filtros, None, None)
        total = ctx.consultar("medidas_total", q_t, b_t)
        dims = [d.lower() for d in p.dimensoes] + (["periodo"] if p.coluna_data and p.granularidade else [])
        especificacao = {}
        for m in p.medidas:
            if m.tipo == "razao":
                especificacao[m.nome] = {"tipo": "razao", "numerador": m.numerador, "denominador": m.denominador}
            else:
                especificacao[m.nome] = {"tipo": "soma", "coluna": m.nome}
        kpis = M.calcular_kpis(partes, especificacao, dims)
        aditivas = [m.nome for m in base if m.tipo in ("soma", "contagem")]
        rec = M.reconciliar(total, partes, aditivas) if aditivas else pd.DataFrame()
        nao_aditivas = [m.nome for m in base if m.tipo == "contagem_distinta"]
        r.dados["kpis"] = kpis
        r.tabelas.update({"kpis": kpis, "reconciliacao": rec})
        ids = []
        for m in p.medidas:
            if m.tipo == "razao":
                num = float(total[m.numerador].sum()) if m.numerador in total else float("nan")
                den = float(total[m.denominador].sum()) if m.denominador in total else float("nan")
                valor = num / den if den else float("nan")
            else:
                valor = float(total[m.nome].iloc[0])
            mm = ctx.metrica(f"kpi_{m.nome}", valor, compacto=m.tipo != "razao", casas=3 if m.tipo == "razao" else None,
                             descricao=f"{m.nome} no total" + (f" ({m.descricao})" if m.descricao else ""))
            ids.append(mm.id)
        bate = bool(rec.empty or rec["bate"].all())
        if not bate and not rec.empty:
            falta = rec.loc[~rec["bate"]].iloc[0]
            fracao = abs(falta["diferenca"]) / abs(falta["total"]) if falta["total"] else float("nan")
            r.avisos.append(
                f"as partes somam {fracao:.1%} a menos que o total em '{falta['medida']}': grupos com menos de "
                f"{ctx.config.data_policy.min_group_size} unidades são omitidos pela regra de grupo mínimo."
            )
        r.resumo = "Totais: " + ", ".join(f"{m.nome} ⟦{i}⟧" for m, i in zip(p.medidas, ids)) + "." + \
            (" As partes somam o total." if bate else " ATENÇÃO: as partes não somam o total.")
        if nao_aditivas:
            r.avisos.append(f"{', '.join(nao_aditivas)} são contagens distintas: não somam entre grupos (não reconciliar por soma).")
        ctx.afirmacao("fato", r.resumo, selo="A" if bate else "C", bases=ids)
        ctx.tabela_para_ia("kpis", kpis.round(4))
        if p.dimensoes and len(kpis) <= 30:
            primeira = p.medidas[0].nome
            ctx.figura(graficos.barras(kpis[p.dimensoes[0].lower()].astype(str), kpis[primeira], titulo=f"{primeira} por {p.dimensoes[0].lower()}",
                                       eixo_y=primeira), nome="kpis", titulo=f"{primeira} por {p.dimensoes[0].lower()}",
                       descricao=f"Barras de {primeira} para cada valor de {p.dimensoes[0].lower()}.")
        r.premissas_ok = bate
        return r

    def codigo(self, p, r):
        esp = {m.nome: ({"tipo": "razao", "numerador": m.numerador, "denominador": m.denominador} if m.tipo == "razao"
                        else {"tipo": "soma", "coluna": m.nome}) for m in p.medidas}
        dims = [d.lower() for d in p.dimensoes] + (["periodo"] if p.coluna_data and p.granularidade else [])
        return (f'_partes = consulta("{r.passo}", "medidas")\n'
                f"dados['kpis'] = M.calcular_kpis(_partes, {esp!r}, {dims!r})")


@registrar_nativo
class DecomposicaoVariacao(Bloco):
    nome = "decomposicao_variacao"
    versao = "1.0"
    papel = "Analista de dados"
    titulo = "Por que a taxa mudou? Efeito mix × efeito taxa"
    descricao = (
        "Separa a variação de uma taxa total entre dois períodos em mudança de composição (mix de segmentos) e "
        "mudança de desempenho dentro dos segmentos."
    )
    tipos_pergunta = frozenset({"diagnostico", "descricao"})
    produz = ("decomposicao",)

    class Parametros(Parametros):
        tabela: str
        segmento: str
        numerador: str
        denominador: str
        coluna_data: str
        inicio_a: str
        fim_a: str
        inicio_b: str
        fim_b: str

    def executar(self, ctx, p):
        r = ctx._resultado
        seg = f"t.{sql.ident(p.segmento)}"
        data = f"t.{sql.ident(p.coluna_data)}"
        resultados = []
        for rotulo, ini, fim in (("a", p.inicio_a, p.fim_a), ("b", p.inicio_b, p.fim_b)):
            q = (f"SELECT {seg} AS segmento, SUM(t.{sql.ident(p.numerador)}) AS num, SUM(t.{sql.ident(p.denominador)}) AS den "
                 f"FROM {sql.ident(p.tabela)} t WHERE {data} >= TO_DATE(:ini, 'YYYY-MM-DD') AND {data} < TO_DATE(:fim, 'YYYY-MM-DD') "
                 f"GROUP BY {seg}")
            resultados.append(ctx.consultar(f"periodo_{rotulo}", q, {"ini": ini, "fim": fim}))
        res = M.decomposicao_mix_taxa(resultados[0], resultados[1], segmento="segmento", numerador="num", denominador="den")
        r.dados["decomposicao"] = res
        r.tabelas["por_segmento"] = res["por_segmento"]
        mv = ctx.metrica("variacao_total", res["variacao"], casas=4, sinal=True, descricao="variação da taxa total entre os períodos")
        mm = ctx.metrica("efeito_mix", res["efeito_mix"], casas=4, sinal=True, descricao="parte da variação explicada pela composição")
        mt = ctx.metrica("efeito_taxa", res["efeito_taxa"], casas=4, sinal=True, descricao="parte da variação explicada pelo desempenho")
        r.resumo = f"A taxa total variou ⟦{mv.id}⟧: ⟦{mm.id}⟧ por mudança de composição e ⟦{mt.id}⟧ por desempenho nos segmentos."
        ctx.afirmacao("fato", r.resumo, selo="A", bases=[mv.id, mm.id, mt.id])
        ctx.tabela_para_ia("por_segmento", res["por_segmento"].round(4))
        r.premissas_ok = True
        return r
