"""Blocos de modelagem exploratória: regressão linear e não linear, classificação, vazamento e segmentos.

Modelos rodam no kernel, sobre a amostra aprovada no passaporte. Só métricas e
tabelas agregadas (com grupo mínimo) chegam à IA. Todo modelo é comparado com um
baseline; sem ganho sobre o baseline, não há modelo.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from pydantic import Field

from adex_copiloto.blocos import estatistica as M
from adex_copiloto.blocos import graficos
from adex_copiloto.blocos.base import Bloco, Parametros, Precondicao
from adex_copiloto.blocos.registro import registrar_nativo
from adex_copiloto.estudo.evidencia import calcular_selo


def _base(ctx) -> str:
    pa = ctx.passaporte
    if pa is None:
        return "amostra_conveniencia"
    return "censo" if pa.metodo == "censo" else ("amostra_conveniencia" if pa.metodo == "conveniencia" else "amostra_probabilistica")


def _dados(ctx, fonte: str) -> pd.DataFrame:
    return ctx.dados[fonte].rename(columns=str.lower)


@registrar_nativo
class RegressaoLinear(Bloco):
    nome = "regressao_linear"
    versao = "1.0"
    papel = "Estatístico"
    titulo = "Regressão linear com erro-padrão robusto"
    descricao = "Associações ajustadas entre uma medida e fatores, com erro-padrão robusto (HC3) ou por grupo, e diagnóstico de colinearidade."
    tipos_pergunta = frozenset({"regressao", "diagnostico", "descricao"})
    produz = ("regressao",)

    class Parametros(Parametros):
        dados: str = Field(default="amostra", description="'amostra' ou 'painel'")
        y: str
        x: list[str] = Field(min_length=1, max_length=40)
        cluster: str | None = None
        pesos: str | None = None

    def precondicoes(self, ctx, p):
        pre = super().precondicoes(ctx, p)
        pre.append(Precondicao(f"dados '{p.dados}' disponíveis", p.dados in ctx.dados))
        return pre

    def executar(self, ctx, p):
        r = ctx._resultado
        df = _dados(ctx, p.dados)
        res = M.ajustar_ols(df, y=p.y.lower(), x=[c.lower() for c in p.x], cluster=p.cluster.lower() if p.cluster else None,
                            pesos=p.pesos.lower() if p.pesos else None)
        coef = res["coeficientes"]
        r.dados["regressao"] = res
        r.tabelas.update({"coeficientes": coef, "vif": res["vif"].rename("vif").reset_index()})
        mr = ctx.metrica("r2", res["r2"], casas=3, descricao="R² do modelo linear")
        mn = ctx.metrica("n_regressao", res["n"], compacto=True, descricao="observações usadas na regressão")
        colineares = res["vif"][res["vif"] > 10].index.tolist() if len(res["vif"]) else []
        sig = coef[(coef["termo"] != "const") & (coef["p_valor"] < 0.05)]
        ms = ctx.metrica("termos_significativos", len(sig), descricao="fatores com associação significativa (p < 0,05)")
        r.resumo = (f"Regressão com ⟦{mn.id}⟧ observações: R² ⟦{mr.id}⟧; ⟦{ms.id}⟧ fatores com associação significativa "
                    f"(erro-padrão {res['erro_padrao']}).")
        selo = calcular_selo(base=_base(ctx), incerteza=True, premissas_ok=not colineares, robustez_ok=None)
        ctx.afirmacao("fato", r.resumo, selo=selo, bases=[mr.id, mn.id, ms.id])
        ctx.afirmacao("hipotese", "Associações de regressão não são, sozinhas, prova de causa.", selo="D")
        if colineares:
            r.avisos.append(f"colinearidade forte (VIF > 10): {', '.join(colineares)}")
        r.avisos.extend(res.get("avisos", []))
        ctx.tabela_para_ia("coeficientes", coef.round(4))
        r.premissas_ok = not colineares
        return r

    def codigo(self, p, r):
        return (f"dados['regressao'] = M.ajustar_ols(dados['{p.dados}'].rename(columns=str.lower), y={p.y.lower()!r}, "
                f"x={[c.lower() for c in p.x]!r}, cluster={p.cluster.lower() if p.cluster else None!r})")


@registrar_nativo
class RegressaoNaoLinear(Bloco):
    nome = "regressao_nao_linear"
    versao = "1.0"
    papel = "Cientista de dados"
    titulo = "Regressão não linear (boosting) contra baseline"
    descricao = "Boosting de árvores com validação cruzada, comparado com o baseline (média). Mostra a importância por permutação."
    tipos_pergunta = frozenset({"regressao", "previsao", "diagnostico"})
    produz = ("modelo_regressao",)

    class Parametros(Parametros):
        dados: str = "amostra"
        y: str
        x: list[str] = Field(min_length=1, max_length=80)
        folds: int = Field(default=5, ge=2, le=10)

    def executar(self, ctx, p):
        r = ctx._resultado
        res = M.ajustar_gbm_regressao(_dados(ctx, p.dados), y=p.y.lower(), x=[c.lower() for c in p.x], folds=p.folds, seed=ctx.seed)
        r.dados["modelo_regressao"] = res
        r.tabelas["importancia"] = res["importancia"]
        mg = ctx.metrica("ganho_baseline", res["ganho_sobre_baseline"], unidade="%", descricao="redução do erro absoluto contra o baseline")
        m2 = ctx.metrica("r2_cv", res["r2_cv"], casas=3, descricao="R² na validação cruzada")
        bom = res["ganho_sobre_baseline"] > 0.05
        com_ic = bool(np.isfinite(res.get("ganho_ic_inf", float("nan"))))
        bases = [mg.id, m2.id]
        if com_ic:
            mi = ctx.metrica("ganho_ic_inf", res["ganho_ic_inf"], unidade="%", descricao="limite inferior do IC de 95% do ganho (dobras)")
            mx = ctx.metrica("ganho_ic_sup", res["ganho_ic_sup"], unidade="%", descricao="limite superior do IC de 95% do ganho (dobras)")
            bases += [mi.id, mx.id]
        r.resumo = (f"O boosting reduz o erro em ⟦{mg.id}⟧ contra o baseline (R² validado ⟦{m2.id}⟧"
                    + (f"; IC de 95% do ganho entre as dobras: ⟦{bases[2]}⟧ a ⟦{bases[3]}⟧" if com_ic else "") + ")."
                    + ("" if bom else " Ganho pequeno: o modelo não compensa."))
        # Sem checagem de premissas (ex.: estabilidade no tempo), o teto é B, mesmo com incerteza.
        ctx.afirmacao("fato", r.resumo, selo=calcular_selo(base=_base(ctx), incerteza=com_ic, premissas_ok=None, robustez_ok=None),
                      bases=bases)
        top = res["importancia"].head(10)
        ctx.figura(graficos.barras(top["variavel"], top["importancia"], titulo="Importância por permutação", eixo_y="queda no desempenho"),
                   nome="importancia", titulo="Importância das variáveis", descricao="Quanto o desempenho cai ao embaralhar cada variável.")
        ctx.tabela_para_ia("importancia", top.round(4))
        r.premissas_ok = bom
        return r


@registrar_nativo
class Classificacao(Bloco):
    nome = "classificacao"
    versao = "1.0"
    papel = "Cientista de dados"
    titulo = "Classificação com baseline, validação separada e calibração"
    descricao = (
        "Treina logística e boosting no treino e avalia no teste separado (por tempo ou por grupo). Mostra AUC, "
        "PR-AUC, Brier, calibração e lift por decil, sempre contra o baseline."
    )
    tipos_pergunta = frozenset({"classificacao", "previsao"})
    requer = ("treino", "teste")
    produz = ("modelo_classificacao",)

    class Parametros(Parametros):
        y: str = "alvo"
        x: list[str] = Field(min_length=1, max_length=120)

    def precondicoes(self, ctx, p):
        pre = super().precondicoes(ctx, p)
        if "treino" in ctx.dados:
            y = ctx.dados["treino"].rename(columns=str.lower).get(p.y.lower())
            ok = y is not None and y.nunique() == 2
            pre.append(Precondicao("alvo binário com as duas classes no treino", bool(ok)))
        return pre

    def executar(self, ctx, p):
        r = ctx._resultado
        tr, te = _dados(ctx, "treino"), _dados(ctx, "teste")
        x = [c.lower() for c in p.x]
        vaz = M.checar_vazamento(tr, y=p.y.lower(), x=x)
        suspeitas = vaz[vaz["suspeita"]]["variavel"].tolist() if len(vaz) else []
        res = M.ajustar_classificador(tr, te, y=p.y.lower(), x=[c for c in x if c not in suspeitas], seed=ctx.seed)
        r.dados["modelo_classificacao"] = res
        r.tabelas.update({"metricas": res["metricas"], "calibracao": res["calibracao"], "lift": res["lift"], "vazamento": vaz})
        met = res["metricas"].set_index("modelo")
        melhor = res["melhor"]
        ma = ctx.metrica("auc", met.loc[melhor, "auc"], casas=3, descricao=f"AUC do melhor modelo ({melhor}) no teste")
        mb = ctx.metrica("taxa_base", met.loc["baseline", "taxa_base"], unidade="%", casas=1, descricao="taxa de positivos no teste")
        ml = ctx.metrica("lift_decil1", float(res["lift"].iloc[0]["lift"]), casas=2, descricao="lift do primeiro decil (os 10% de maior risco)")
        r.resumo = (f"Melhor modelo: {melhor}, AUC ⟦{ma.id}⟧ no teste separado; taxa base ⟦{mb.id}⟧; "
                    f"os 10% de maior risco concentram ⟦{ml.id}⟧ vezes a taxa base.")
        if suspeitas:
            r.resumo += f" Variáveis removidas por suspeita de vazamento: {', '.join(suspeitas)}."
            ctx.decisao("Possível vazamento", f"As variáveis {suspeitas} preveem o alvo bem demais (podem vir do futuro). Confirmar a remoção?",
                        [("a", "Remover (recomendado)", "o modelo fica mais honesto"), ("b", "Manter", "o resultado pode estar inflado")],
                        recomendada="a", bloqueia=("aceite",), dados={"variaveis": suspeitas})
        auc_ok = met.loc[melhor, "auc"] > 0.55
        selo = calcular_selo(base=_base(ctx), incerteza=False, premissas_ok=not suspeitas, robustez_ok=None)
        ctx.afirmacao("fato", r.resumo, selo=selo, bases=[ma.id, mb.id, ml.id])
        lift = res["lift"]
        ctx.figura(graficos.barras(lift["decil"].astype(str), lift["lift"], titulo="Lift por decil de risco", eixo_y="vezes a taxa base", destaque=0),
                   nome="lift", titulo="Lift por decil", descricao="Quantas vezes a taxa base cada decil de risco concentra.")
        ctx.tabela_para_ia("metricas", res["metricas"].round(4))
        ctx.tabela_para_ia("lift", lift.round(4), coluna_n="n")
        r.premissas_ok = auc_ok and not suspeitas
        return r

    def codigo(self, p, r):
        return (f"dados['modelo_classificacao'] = M.ajustar_classificador(dados['treino'].rename(columns=str.lower), "
                f"dados['teste'].rename(columns=str.lower), y={p.y.lower()!r}, x={[c.lower() for c in p.x]!r})")


@registrar_nativo
class ChecagemVazamento(Bloco):
    nome = "checagem_vazamento"
    versao = "1.0"
    papel = "Engenheiro de ML"
    titulo = "Checagem de vazamento"
    descricao = "Procura variáveis que sozinhas preveem o alvo bem demais (AUC isolada ≥ 0,95): sinal de informação do futuro."
    tipos_pergunta = frozenset({"classificacao", "previsao"})
    produz = ("vazamento",)

    class Parametros(Parametros):
        dados: str = "treino"
        y: str = "alvo"
        x: list[str] = Field(min_length=1)
        limiar_auc: float = Field(default=0.95, gt=0.5, le=1)

    def executar(self, ctx, p):
        r = ctx._resultado
        vaz = M.checar_vazamento(_dados(ctx, p.dados), y=p.y.lower(), x=[c.lower() for c in p.x], limiar_auc=p.limiar_auc)
        r.dados["vazamento"] = vaz
        r.tabelas["vazamento"] = vaz
        n = int(vaz["suspeita"].sum()) if len(vaz) else 0
        mv = ctx.metrica("variaveis_suspeitas", n, descricao="variáveis suspeitas de vazamento")
        r.resumo = f"⟦{mv.id}⟧ variáveis suspeitas de vazamento."
        ctx.afirmacao("fato", r.resumo, selo="A", bases=[mv.id])
        r.premissas_ok = n == 0
        return r


@registrar_nativo
class Clusterizacao(Bloco):
    nome = "clusterizacao"
    versao = "1.0"
    papel = "Cientista de dados"
    titulo = "Segmentos parecidos (k-médias)"
    descricao = "Agrupa unidades parecidas com variáveis padronizadas; escolhe o número de segmentos pela silhueta. Só perfis agregados saem."
    tipos_pergunta = frozenset({"segmentacao", "descricao"})
    produz = ("segmentos",)

    class Parametros(Parametros):
        dados: str = "amostra"
        x: list[str] = Field(min_length=2, max_length=40)
        k_min: int = Field(default=2, ge=2)
        k_max: int = Field(default=8, le=15)

    def executar(self, ctx, p):
        r = ctx._resultado
        df = _dados(ctx, p.dados)
        res = M.segmentar_kmeans(df, x=[c.lower() for c in p.x], k_min=p.k_min, k_max=p.k_max, seed=ctx.seed)
        perfil = res["perfil"]
        perfil.columns = ["_".join(c) for c in perfil.columns]
        perfil = perfil.reset_index()
        tamanho = perfil.filter(like="_size").iloc[:, 0] if len(perfil.filter(like="_size").columns) else pd.Series(dtype=float)
        perfil["n"] = tamanho
        r.dados["segmentos"] = {"k": res["k"], "perfil": perfil, "rotulos": pd.Series(res["rotulos"], index=res["indice"])}
        r.tabelas["perfil_segmentos"] = perfil
        mk = ctx.metrica("k_segmentos", res["k"], descricao="número de segmentos escolhido")
        msil = ctx.metrica("silhueta", res["silhuetas"][res["k"]], casas=3, descricao="silhueta (0 a 1; maior = segmentos mais separados)")
        r.resumo = f"⟦{mk.id}⟧ segmentos, silhueta ⟦{msil.id}⟧."
        ctx.afirmacao("fato", r.resumo, selo=calcular_selo(base=_base(ctx), natureza="descritiva"), bases=[mk.id, msil.id])
        ctx.tabela_para_ia("perfil_segmentos", perfil.round(3), coluna_n="n")
        r.premissas_ok = res["silhuetas"][res["k"]] >= 0.25
        return r
