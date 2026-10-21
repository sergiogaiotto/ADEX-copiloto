"""Blocos de séries temporais e anomalias (sobre o painel agregado ou a amostra)."""

from __future__ import annotations

import numpy as np
import pandas as pd
from pydantic import Field

from adex_copiloto.blocos import estatistica as M
from adex_copiloto.blocos import graficos
from adex_copiloto.blocos.base import Bloco, Parametros, Precondicao
from adex_copiloto.blocos.registro import registrar_nativo


def _serie_total(ctx, desfecho: str | None, medida: str) -> tuple[pd.Series, str]:
    painel = ctx.dados["painel"]
    d = desfecho or (ctx.mapeamento.desfecho_principal.nome if ctx.mapeamento and ctx.mapeamento.desfecho_principal else None)
    if d is None:
        raise ValueError("defina o desfecho")
    if medida == "taxa":
        por = next((x.por for x in ctx.mapeamento.desfechos if x.nome == d), 1000)
        g = painel.groupby("periodo")
        serie = g[f"ev_{d}"].sum() / g["unidades"].sum() * por
    else:
        serie = painel.groupby("periodo")[f"ev_{d}"].sum()
    return serie.sort_index(), d


@registrar_nativo
class BacktestSeries(Bloco):
    nome = "backtest_series"
    versao = "1.0"
    papel = "Cientista de dados"
    titulo = "Previsão com backtest (origem móvel) contra o ingênuo sazonal"
    descricao = (
        "Testa modelos de previsão em várias origens no passado e compara com o ingênuo sazonal (repetir o mesmo "
        "mês do ano anterior). MASE < 1 = melhor que o ingênuo."
    )
    tipos_pergunta = frozenset({"previsao", "descricao"})
    requer = ("painel",)
    produz = ("backtest",)

    class Parametros(Parametros):
        desfecho: str | None = None
        medida: str = Field(default="eventos", description="'eventos' ou 'taxa'")
        horizonte: int = Field(default=3, ge=1, le=24)
        n_origens: int = Field(default=4, ge=1, le=12)
        sazonalidade: int = Field(default=12, ge=1, le=52)

    def precondicoes(self, ctx, p):
        pre = super().precondicoes(ctx, p)
        if "painel" in ctx.dados:
            n = ctx.dados["painel"]["periodo"].nunique()
            minimo = max(2 * p.sazonalidade, 8) + p.horizonte
            pre.append(Precondicao(f"histórico de pelo menos {minimo} períodos", n >= minimo, f"há {n}"))
        return pre

    def executar(self, ctx, p):
        r = ctx._resultado
        serie, d = _serie_total(ctx, p.desfecho, p.medida)
        r.dados["_bt_meta"] = {"desfecho": d}
        bt = M.backtest_series(serie.to_numpy(), horizonte=p.horizonte, n_origens=p.n_origens, sazonalidade=p.sazonalidade)
        if bt.empty:
            r.status, r.erro = "falhou", "nenhuma origem de backtest coube no histórico"
            return r
        resumo = bt.groupby("modelo").agg(mase=("mase", "mean"), smape=("smape", "mean"), origens=("origem", "count")).reset_index()
        melhor = resumo.sort_values("mase").iloc[0]
        r.dados["backtest"] = {"resumo": resumo, "detalhe": bt, "melhor": melhor["modelo"], "desfecho": d}
        r.tabelas["backtest"] = resumo
        mm = ctx.metrica("mase_melhor", melhor["mase"], casas=2, descricao=f"MASE médio do melhor modelo ({melhor['modelo']})")
        ms = ctx.metrica("smape_melhor", melhor["smape"], unidade="%", casas=1, descricao="erro percentual simétrico médio do melhor modelo")
        vence = melhor["modelo"] != "sazonal_ingenuo" and melhor["mase"] < resumo.set_index("modelo").loc["sazonal_ingenuo", "mase"]
        r.resumo = (f"No backtest, o melhor modelo ({melhor['modelo'].replace('_', ' ')}) teve MASE ⟦{mm.id}⟧ e erro ⟦{ms.id}⟧"
                    + (", melhor que o ingênuo sazonal." if vence else "; nenhum modelo venceu o ingênuo sazonal com folga."))
        rodadas = int(bt["origem"].nunique())  # as origens que couberam no histórico, não as pedidas
        if rodadas < p.n_origens:
            r.avisos.append(f"só {rodadas} de {p.n_origens} origens de backtest couberam no histórico.")
        ctx.afirmacao("fato", r.resumo, selo="A" if rodadas >= 3 else "B", bases=[mm.id, ms.id])
        ctx.figura(graficos.linhas({d: serie}, titulo=f"Série de {d}", eixo_y=p.medida), nome="serie", titulo=f"Série de {d}",
                   descricao=f"Série total de {d} por período usada no backtest.")
        ctx.tabela_para_ia("backtest", resumo.round(4))
        r.premissas_ok = True
        r.robustez_ok = rodadas >= 3
        return r

    def codigo(self, p, r):
        d = r.dados.get("_bt_meta", {}).get("desfecho")
        return (f"_s = M.serie_total(dados['painel'], desfecho={d!r}, medida={p.medida!r})\n"
                f"dados['backtest'] = M.backtest_series(_s.to_numpy(), horizonte={p.horizonte}, n_origens={p.n_origens}, "
                f"sazonalidade={p.sazonalidade})")


@registrar_nativo
class AnomaliasSerie(Bloco):
    nome = "anomalias_serie"
    versao = "1.0"
    papel = "Analista de dados"
    titulo = "Períodos fora do padrão (por grupo)"
    descricao = "Procura, em cada grupo, períodos fora do padrão pelo z robusto do resíduo (tira tendência e sazonalidade)."
    tipos_pergunta = frozenset({"anomalia", "descricao", "diagnostico"})
    requer = ("painel",)
    produz = ("anomalias",)

    class Parametros(Parametros):
        desfecho: str | None = None
        sazonalidade: int | None = 12
        limiar: float = Field(default=4.0, gt=2)

    def executar(self, ctx, p):
        r = ctx._resultado
        painel = ctx.dados["painel"]
        d = p.desfecho or ctx.mapeamento.desfecho_principal.nome
        y = f"taxa_{d}"
        tabela = M.anomalias_por_grupo(painel, y=y, sazonalidade=p.sazonalidade, limiar=p.limiar)
        r.dados["_an_meta"] = {"y": y}
        r.dados["anomalias"] = tabela
        r.tabelas["anomalias"] = tabela
        ma = ctx.metrica("anomalias", len(tabela), descricao="células grupo × período fora do padrão")
        mg = ctx.metrica("grupos_com_anomalia", tabela["grupo"].nunique() if len(tabela) else 0, descricao="grupos com algum período fora do padrão")
        r.resumo = f"⟦{ma.id}⟧ células fora do padrão em ⟦{mg.id}⟧ grupos."
        ctx.afirmacao("fato", r.resumo, selo="A", bases=[ma.id, mg.id])
        if len(tabela):
            ctx.afirmacao("hipotese", "Cada anomalia pode ser um problema real ou uma mudança de registro; confirme com o dono do dado.", selo="D")
        ctx.tabela_para_ia("anomalias", tabela.sort_values("z_robusto", key=np.abs, ascending=False), coluna_n="unidades", max_linhas=30)
        r.premissas_ok = True
        return r

    def codigo(self, p, r):
        y = r.dados.get("_an_meta", {}).get("y")
        return f"dados['anomalias'] = M.anomalias_por_grupo(dados['painel'], y={y!r}, sazonalidade={p.sazonalidade!r}, limiar={p.limiar})"


@registrar_nativo
class AnomaliasMultivariadas(Bloco):
    nome = "anomalias_multivariadas"
    versao = "1.0"
    papel = "Cientista de dados"
    titulo = "Unidades fora do padrão em várias variáveis (Isolation Forest)"
    descricao = "Pontua unidades da amostra pelo quanto fogem do padrão conjunto. Só a contagem e o perfil agregado saem para a IA."
    tipos_pergunta = frozenset({"anomalia"})
    requer = ("amostra",)
    produz = ("anomalias_unidades",)

    class Parametros(Parametros):
        x: list[str] = Field(min_length=2, max_length=40)
        contaminacao: float = Field(default=0.01, gt=0, lt=0.2)

    def executar(self, ctx, p):
        r = ctx._resultado
        df = ctx.dados["amostra"].rename(columns=str.lower)
        x = [c.lower() for c in p.x]
        pont = M.anomalias_multivariadas(df, x=x, contaminacao=p.contaminacao, seed=ctx.seed)
        corte = pont.quantile(1 - p.contaminacao)
        marcadas = df.loc[pont[pont >= corte].index]
        perfil = pd.DataFrame({"variavel": x, "media_geral": [df[c].mean() if pd.api.types.is_numeric_dtype(df[c]) else np.nan for c in x],
                               "media_anomalas": [marcadas[c].mean() if pd.api.types.is_numeric_dtype(marcadas[c]) else np.nan for c in x]})
        r.dados["anomalias_unidades"] = pont
        r.tabelas["perfil_anomalas"] = perfil
        mn = ctx.metrica("unidades_anomalas", len(marcadas), descricao="unidades marcadas como fora do padrão (ficam no kernel)")
        r.resumo = f"⟦{mn.id}⟧ unidades da amostra fogem do padrão conjunto."
        ctx.afirmacao("fato", r.resumo, selo="B", bases=[mn.id])
        if len(marcadas) >= ctx.config.data_policy.min_group_size:
            ctx.tabela_para_ia("perfil_anomalas", perfil.round(3))
        r.premissas_ok = True
        return r
