"""Blocos de inferência: comparação de grupos com incerteza e correção para múltiplos testes."""

from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd
from pydantic import Field

from adex_copiloto.blocos import estatistica as M
from adex_copiloto.blocos import graficos, sql
from adex_copiloto.blocos.base import Bloco, Parametros, Precondicao
from adex_copiloto.blocos.registro import registrar_nativo
from adex_copiloto.estudo.evidencia import calcular_selo
from adex_copiloto.estudo.mapeamento import FiltroSimples


@registrar_nativo
class TesteGrupos(Bloco):
    nome = "teste_grupos"
    versao = "1.0"
    papel = "Estatístico"
    titulo = "Os grupos diferem? (com intervalo e correção para vários testes)"
    descricao = (
        "Compara cada grupo com uma referência. Proporções podem vir agregadas da fonte; médias vêm da amostra "
        "aprovada. Usa Welch ou Mann-Whitney conforme as premissas e corrige p-valores por Benjamini-Hochberg."
    )
    tipos_pergunta = frozenset({"descricao", "diagnostico", "regressao", "classificacao", "efeito"})
    produz = ("comparacao",)

    class Parametros(Parametros):
        origem: Literal["agregado", "amostra"] = "agregado"
        grupo: str
        medida: str = Field(description="coluna 0/1 (proporção) ou numérica (média)")
        binaria: bool = True
        referencia: str | None = None
        tabela: str | None = Field(default=None, description="no modo agregado; padrão: população")
        filtros: list[FiltroSimples] = Field(default_factory=list)
        confianca: float = Field(default=0.95, gt=0.5, lt=1)

    def precondicoes(self, ctx, p):
        pre = super().precondicoes(ctx, p)
        if p.origem == "amostra":
            pre.append(Precondicao("amostra aprovada disponível", "amostra" in ctx.dados))
        elif not p.binaria:
            pre.append(Precondicao("médias exigem a amostra", False, "use origem='amostra' para medidas numéricas"))
        return pre

    def executar(self, ctx, p):
        r = ctx._resultado
        if p.origem == "agregado":
            tabela = p.tabela or ctx.mapeamento.populacao
            binds: dict = {}
            cond = sql._filtros(p.filtros, "t", binds)
            g = f"t.{sql.ident(p.grupo)}"
            q = (f"SELECT {g} AS grupo, SUM(CASE WHEN t.{sql.ident(p.medida)} = 1 THEN 1 ELSE 0 END) AS sucessos, "
                 f"COUNT(*) AS total FROM {sql.ident(tabela)} t {sql._where(cond)} GROUP BY {g}")
            agg = ctx.consultar("proporcoes", q, binds)
            tabela_res = M.comparar_proporcoes_agregadas(agg, referencia=p.referencia, confianca=p.confianca)
            referencia = tabela_res["referencia"].iloc[0] if len(tabela_res) else p.referencia
            base = "censo"
        else:
            df = ctx.dados["amostra"].rename(columns=str.lower)
            tabela_res = M.comparar_grupos(df, grupo=p.grupo.lower(), medida=p.medida.lower(), referencia=p.referencia,
                                           binaria=p.binaria, confianca=p.confianca)
            referencia = tabela_res["referencia"].iloc[0] if len(tabela_res) else p.referencia
            base = "amostra_probabilistica" if (ctx.passaporte and ctx.passaporte.metodo != "conveniencia") else "amostra_conveniencia"
        r.dados["comparacao"] = tabela_res
        r.tabelas["comparacao"] = tabela_res
        n_sig = int(tabela_res["significativo"].sum()) if len(tabela_res) else 0
        ms = ctx.metrica("grupos_diferentes", n_sig, descricao=f"grupos que diferem da referência '{referencia}' após correção")
        mt = ctx.metrica("grupos_comparados", len(tabela_res), descricao="grupos comparados com a referência")
        r.resumo = f"⟦{ms.id}⟧ de ⟦{mt.id}⟧ grupos diferem da referência '{referencia}' depois da correção para vários testes."
        selo = calcular_selo(base=base, incerteza=True, premissas_ok=True, robustez_ok=None)
        ctx.afirmacao("fato", r.resumo, selo=selo, bases=[ms.id, mt.id])
        if len(tabela_res) and len(tabela_res) <= 25:
            ctx.figura(graficos.pontos_com_ic(tabela_res["grupo"].astype(str), tabela_res["diferenca"], tabela_res["ic_inf"],
                                              tabela_res["ic_sup"], titulo=f"Diferença para '{referencia}'", eixo_x="diferença"),
                       nome="comparacao", titulo="Comparação com a referência",
                       descricao="Diferença de cada grupo para a referência, com intervalo de 95% (sem correção no intervalo).")
        ctx.tabela_para_ia("comparacao", tabela_res, coluna_n="n")
        r.premissas_ok = True
        return r

    def codigo(self, p, r):
        if p.origem == "agregado":
            return (f"dados['comparacao'] = M.comparar_proporcoes_agregadas(consulta('{r.passo}', 'proporcoes'), "
                    f"referencia={p.referencia!r}, confianca={p.confianca})")
        return (f"dados['comparacao'] = M.comparar_grupos(dados['amostra'].rename(columns=str.lower), grupo={p.grupo.lower()!r}, "
                f"medida={p.medida.lower()!r}, referencia={p.referencia!r}, binaria={p.binaria}, confianca={p.confianca})")
