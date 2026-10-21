"""Blocos para julgar o efeito de uma ação implantada em ondas (diferença-em-diferenças escalonada)."""

from __future__ import annotations

import numpy as np
import pandas as pd
from pydantic import Field

from adex_copiloto.blocos import estatistica as M
from adex_copiloto.blocos import graficos
from adex_copiloto.blocos.base import Bloco, Parametros, Precondicao
from adex_copiloto.blocos.registro import registrar_nativo
from adex_copiloto.blocos.sql import codigos_antes_depois
from adex_copiloto.estudo.evidencia import calcular_selo

_TIPOS = frozenset({"efeito", "diagnostico"})


def _desfecho(ctx, nome: str | None):
    m = ctx.mapeamento
    d = next((x for x in m.desfechos if x.nome == nome), None) if nome else m.desfecho_principal
    if d is None:
        raise ValueError(f"desfecho '{nome}' não está no mapeamento")
    return d


def _excluidos(ctx, excluir: list[str] | None = None) -> list[str]:
    """Grupos fora da análise: os do parâmetro do passo e os da decisão sobre datas fora das ondas."""
    return sorted({str(g) for g in [*(excluir or []), *(ctx.dados.get("grupos_excluidos") or [])]})


def _painel_com_coortes(ctx, excluir: list[str] | None = None) -> pd.DataFrame:
    painel = ctx.dados["painel"]
    coortes = ctx.dados["coortes"][["grupo", "coorte"]]
    p = painel.drop(columns=[c for c in ("coorte",) if c in painel]).merge(coortes, on="grupo", how="left")
    excluidos = _excluidos(ctx, excluir)
    if excluidos:
        p = p[~p["grupo"].astype(str).isin(excluidos)]
    return p


def _codigo_painel(excluidos: list[str] | None) -> str:
    """Mesmo painel no pacote de handoff, com as mesmas exclusões do estudo."""
    linha = "_p = dados['painel'].merge(dados['coortes'][['grupo', 'coorte']], on='grupo', how='left')\n"
    if excluidos:
        linha += f"_p = _p[~_p['grupo'].astype(str).isin({sorted(excluidos)!r})]\n"
    return linha


def _config_efeito(ctx) -> dict:
    """Como o efeito principal foi estimado (placebo e sensibilidade repetem o mesmo jeito)."""
    efeito = ctx.dados.get("efeito") if "efeito" in ctx.dados else None
    cfg = (efeito or {}).get("config") or {}
    return {"peso": cfg.get("peso", "unidades"), "ignorar_primeiros": int(cfg.get("ignorar_primeiros", 0)),
            "janela_evento": cfg.get("janela_evento"), "excluidos": cfg.get("excluidos", _excluidos(ctx))}


@registrar_nativo
class DefinirTratamento(Bloco):
    nome = "definir_tratamento"
    versao = "1.0"
    papel = "Analista de dados"
    titulo = "Quando cada grupo passou a contar como tratado"
    descricao = (
        "Define a coorte de cada grupo: o primeiro período em que a fração de unidades com a ação atinge o "
        "limiar (padrão 50%). Compara as datas encontradas nos dados com as ondas que o dev declarou."
    )
    tipos_pergunta = _TIPOS
    requer = ("painel",)
    produz = ("coortes",)
    exige_mapeamento = ("tratamento",)

    class Parametros(Parametros):
        limiar: float | None = Field(default=None, gt=0, le=1, description="padrão: o do mapeamento")
        ondas_declaradas: list[str] = Field(default_factory=list, description="meses AAAA-MM que o dev declarou")
        excluir_grupos: list[str] = Field(default_factory=list)

    def executar(self, ctx, p):
        r = ctx._resultado
        limiar = p.limiar or ctx.mapeamento.tratamento.limiar
        painel = ctx.dados["painel"]
        coortes = M.coortes_por_limiar(painel, grupo="grupo", periodo="periodo", tratados="tratadas", unidades="unidades", limiar=limiar)
        if p.excluir_grupos:
            coortes = coortes[~coortes["grupo"].astype(str).isin(p.excluir_grupos)]
        coortes["mes"] = pd.to_datetime(coortes["coorte"]).dt.strftime("%Y-%m")
        declaradas = {x[:7] for x in p.ondas_declaradas}
        resumo = coortes.groupby(coortes["mes"].fillna("sem ação")).agg(grupos=("grupo", "count")).reset_index()
        resumo["bate_com_o_declarado"] = [
            (m in declaradas) if m != "sem ação" else True for m in resumo["mes"]
        ] if declaradas else None
        r.dados["coortes"] = coortes
        r.tabelas["datas_encontradas"] = resumo
        n_coortes = coortes["coorte"].nunique()
        mc = ctx.metrica("coortes", n_coortes, descricao="datas distintas de entrada da ação")
        mn = ctx.metrica("grupos_sem_acao", int(coortes["coorte"].isna().sum()), descricao="grupos que nunca chegaram ao limiar")
        r.resumo = f"Achei ⟦{mc.id}⟧ datas de entrada da ação nos dados; ⟦{mn.id}⟧ grupos nunca chegaram ao limiar."
        ctx.afirmacao("fato", r.resumo, selo="A", bases=[mc.id, mn.id])
        if declaradas:
            surpresas = resumo[(resumo["mes"] != "sem ação") & (~resumo["mes"].isin(declaradas))]
            if len(surpresas):
                grupos_s = coortes[coortes["mes"].isin(surpresas["mes"])]["grupo"].astype(str).tolist()
                ctx.decisao(
                    "Datas fora das ondas declaradas",
                    f"{len(grupos_s)} grupo(s) entraram em datas que não batem com as ondas que você disse "
                    f"({', '.join(sorted(surpresas['mes']))}). Como tratar?",
                    [("a", "Tratar cada data como uma coorte", "o método usa a data real de cada grupo"),
                     ("b", "Excluir esses grupos", "menos grupos, mas sem a dúvida sobre as datas"),
                     ("c", "Vou conferir numa fonte oficial", "o estudo espera a confirmação")],
                    recomendada="a", bloqueia=("plano",), dados={"grupos": grupos_s, "meses": sorted(surpresas["mes"])},
                )
        ctx.tabela_para_ia("datas_encontradas", resumo)
        r.premissas_ok = n_coortes >= 1
        return r

    def codigo(self, p, r):
        limiar = p.limiar
        return (f"dados['coortes'] = M.coortes_por_limiar(dados['painel'], grupo='grupo', periodo='periodo', "
                f"tratados='tratadas', unidades='unidades', limiar={limiar if limiar else 0.5})")


@registrar_nativo
class EstudoEventoPre(Bloco):
    nome = "estudo_evento_pre"
    versao = "1.0"
    papel = "Estatístico"
    titulo = "Tendências antes da ação e pico pré-ação"
    descricao = (
        "Testa se tratados e controles andavam juntos antes da ação (teste sup-t por bootstrap de grupos) e "
        "procura pico logo antes da ação, sinal de seleção por pico (regressão à média)."
    )
    tipos_pergunta = _TIPOS
    requer = ("painel", "coortes")
    produz = ("teste_pre",)

    class Parametros(Parametros):
        desfecho: str | None = None
        janela_pre: int = Field(default=6, ge=2, le=24)
        meses_pico: int = Field(default=3, ge=1, le=6, description="meses logo antes da ação comparados com a base")
        n_boot: int = Field(default=499, ge=99, le=4999)
        limiar_pico: float = Field(default=0.15, gt=0, description="variação relativa que conta como pico")

    def executar(self, ctx, p):
        r = ctx._resultado
        d = _desfecho(ctx, p.desfecho)
        excluidos = _excluidos(ctx)
        painel = _painel_com_coortes(ctx)
        y = f"taxa_{d.nome}"
        res = M.did_escalonado(painel, y=y, grupo="grupo", periodo="periodo", coorte="coorte", peso="unidades",
                               janela_evento=(-p.janela_pre, 0), n_boot=p.n_boot, seed=ctx.seed)
        pico = M.pico_pre_tratamento(painel, y=y, grupo="grupo", periodo="periodo", coorte="coorte", peso="unidades",
                                     janela_base=p.janela_pre, meses_pico=p.meses_pico, n_boot=p.n_boot, seed=ctx.seed)
        teste = res["teste_pre"]
        r.dados["teste_pre"] = {"teste": teste, "pico": pico, "evento": res["evento"], "desfecho": d.nome,
                                "limiar_pico": p.limiar_pico, "excluidos": excluidos}
        r.tabelas.update({"tendencias_pre": res["evento"], "pico_pre": pico})
        mp = ctx.metrica("p_tendencias_pre", teste["p_valor"], casas=3, descricao="p-valor do teste de tendências antes da ação")
        passou = bool(np.isfinite(teste["p_valor"]) and teste["p_valor"] >= 0.05)
        # Pico só conta com magnitude relevante E intervalo acima de zero (não é ruído).
        com_pico = pico[(pico["diferenca"] > p.limiar_pico) & (pico["ic_inf"] > 0)] if not pico.empty else pico
        textos = [f"Teste de tendências antes da ação: p = ⟦{mp.id}⟧ ({'passou' if passou else 'falhou'})."]
        for _, linha in com_pico.iterrows():
            mv = ctx.metrica(f"pico_{pd.Timestamp(linha['coorte']):%Y_%m}", linha["variacao_tratados"], unidade="%", sinal=True,
                             descricao=f"variação do desfecho logo antes da ação na coorte {pd.Timestamp(linha['coorte']):%m/%Y}")
            mc = ctx.metrica(f"pico_ctrl_{pd.Timestamp(linha['coorte']):%Y_%m}", linha["variacao_controles"], unidade="%", sinal=True,
                             descricao="mesma variação nos grupos ainda sem a ação")
            textos.append(f"Na coorte de {pd.Timestamp(linha['coorte']):%m/%Y}, o desfecho variou ⟦{mv.id}⟧ nos meses logo antes da ação, "
                          f"contra ⟦{mc.id}⟧ nos grupos sem a ação: sinal de seleção por pico.")
        r.resumo = " ".join(textos)
        selo = calcular_selo(base="censo", incerteza=True, premissas_ok=True, robustez_ok=None)
        ctx.afirmacao("fato", textos[0], selo=selo, bases=[mp.id])
        if len(com_pico):
            ctx.afirmacao("hipotese", "Os grupos podem ter recebido a ação por estarem num pico do desfecho (regressão à média).",
                          selo="B", bases=[m.id for m in r.metricas[1:]])
        if res["evento"] is not None and not res["evento"].empty:
            ctx.figura(graficos.evento(res["evento"], titulo="Antes da ação: tratados menos controles", eixo_y=f"{d.nome} (diferença)"),
                       nome="tendencias_pre", titulo="Tendências antes da ação",
                       descricao="Diferença entre tratados e controles em cada período antes da ação, com faixa de 95%.")
        r.premissas_ok = passou and not len(com_pico)
        if not passou or len(com_pico):
            r.status = "feito"
            r.avisos.append("tendências antes da ação não confirmadas: o efeito estimado perde força (selo) até a sensibilidade.")
        return r

    def codigo(self, p, r):
        d = r.dados["teste_pre"]["desfecho"]
        return (_codigo_painel(r.dados["teste_pre"].get("excluidos"))
                + f"dados['teste_pre'] = M.did_escalonado(_p, y='taxa_{d}', grupo='grupo', periodo='periodo', coorte='coorte', "
                f"peso='unidades', janela_evento=(-{p.janela_pre}, 0), n_boot={p.n_boot})['teste_pre']")


@registrar_nativo
class DidEscalonado(Bloco):
    nome = "did_escalonado"
    versao = "1.0"
    papel = "Estatístico"
    titulo = "Efeito da ação em ondas (diferença-em-diferenças escalonada)"
    descricao = (
        "Para cada grupo de cidades que recebeu a ação no mesmo período, compara a mudança nelas com a mudança, "
        "no mesmo intervalo, nos grupos que ainda não tinham recebido (Callaway & Sant'Anna). IC por bootstrap de grupos."
    )
    tipos_pergunta = _TIPOS
    requer = ("painel", "coortes")
    produz = ("efeito",)

    class Parametros(Parametros):
        desfecho: str | None = None
        ponderar_por: str = Field(default="unidades", description="'unidades' ou 'nenhum'")
        ignorar_primeiros: int = Field(default=0, ge=0, le=6, description="períodos logo após a ação a ignorar")
        janela_evento: list[int] = Field(default_factory=lambda: [-6, 12], min_length=2, max_length=2)
        n_boot: int = Field(default=499, ge=99, le=4999)
        excluir_grupos: list[str] = Field(default_factory=list)

    def precondicoes(self, ctx, p):
        pre = super().precondicoes(ctx, p)
        if "coortes" in ctx.dados:
            n = ctx.dados["coortes"]["coorte"].nunique()
            pre.append(Precondicao("pelo menos uma coorte tratada", n >= 1))
            nunca = ctx.dados["coortes"]["coorte"].isna().sum()
            pre.append(Precondicao("há grupos de comparação (sem a ação ou ainda sem a ação)", n >= 2 or nunca >= 1))
        return pre

    def executar(self, ctx, p):
        r = ctx._resultado
        d = _desfecho(ctx, p.desfecho)
        excluidos = _excluidos(ctx, p.excluir_grupos)
        painel = _painel_com_coortes(ctx, p.excluir_grupos)
        y = f"taxa_{d.nome}"
        peso = "unidades" if p.ponderar_por == "unidades" else None
        res = M.did_escalonado(painel, y=y, grupo="grupo", periodo="periodo", coorte="coorte", peso=peso,
                               janela_evento=tuple(p.janela_evento), n_boot=p.n_boot, seed=ctx.seed,
                               ignorar_primeiros=p.ignorar_primeiros)
        r.dados["efeito"] = {**res, "desfecho": d.nome,
                             "config": {"peso": peso, "ignorar_primeiros": p.ignorar_primeiros,
                                        "janela_evento": list(p.janela_evento), "excluidos": excluidos}}
        for aviso in res.get("avisos") or []:
            if aviso not in r.avisos:
                r.avisos.append(aviso)
        r.tabelas.update({"efeito_por_periodo": res["evento"], "att_gt": res["att_gt"]})
        base_pre = painel[(painel["coorte"].notna()) & (painel["periodo"] < painel["coorte"])]
        nivel = float(np.average(base_pre[y], weights=base_pre["unidades"])) if len(base_pre) else float("nan")
        ma = ctx.metrica("efeito_att", res["att"], casas=2, sinal=True, ic=(res["ic_inf"], res["ic_sup"]),
                         descricao=f"efeito médio da ação em {d.nome} por {d.por} unidades (ATT)")
        mi = ctx.metrica("efeito_ic_inf", res["ic_inf"], casas=2, sinal=True, descricao="limite inferior do IC de 95%")
        ms = ctx.metrica("efeito_ic_sup", res["ic_sup"], casas=2, sinal=True, descricao="limite superior do IC de 95%")
        relativo = res["att"] / nivel if nivel else float("nan")
        mr = ctx.metrica("efeito_relativo", relativo, unidade="%", sinal=True, descricao="efeito relativo ao nível antes da ação")
        pre_ok = ctx.dados.get("teste_pre", {}).get("teste", {}).get("p_valor")
        premissas = None if pre_ok is None else bool(pre_ok >= 0.05)
        robustez = ctx.dados.get("robustez_ok")
        selo = calcular_selo(base="censo", incerteza=np.isfinite(res["ic_inf"]), premissas_ok=premissas,
                             robustez_ok=robustez, exige_robustez=True)
        r.resumo = (f"Efeito médio da ação: ⟦{ma.id}⟧ em {d.nome} por {d.por} unidades (⟦{mr.id}⟧), "
                    f"com intervalo de 95% de ⟦{mi.id}⟧ a ⟦{ms.id}⟧.")
        ctx.afirmacao("fato", r.resumo, selo=selo, bases=[ma.id, mi.id, ms.id])
        if res["evento"] is not None and not res["evento"].empty:
            ctx.figura(graficos.evento(res["evento"], titulo=f"Efeito da ação em {d.nome}, por período", eixo_y=f"diferença por {d.por}"),
                       nome="efeito_evento", titulo="Efeito por período desde a ação",
                       descricao="Diferença entre tratados e controles em cada período antes e depois da ação, com faixa de 95%.")
        ctx.tabela_para_ia("efeito_por_periodo", res["evento"].round(4) if not res["evento"].empty else res["evento"])
        r.premissas_ok, r.robustez_ok = premissas, robustez
        return r

    def codigo(self, p, r):
        d = r.dados["efeito"]["desfecho"]
        return (_codigo_painel(r.dados["efeito"]["config"]["excluidos"])
                + f"dados['efeito'] = M.did_escalonado(_p, y='taxa_{d}', grupo='grupo', periodo='periodo', coorte='coorte', "
                f"peso={'unidades' if p.ponderar_por == 'unidades' else None!r}, janela_evento={tuple(p.janela_evento)!r}, "
                f"n_boot={p.n_boot}, ignorar_primeiros={p.ignorar_primeiros})")


@registrar_nativo
class Placebo(Bloco):
    nome = "placebo"
    versao = "1.0"
    papel = "Estatístico"
    titulo = "Placebo: a ação em datas falsas"
    descricao = "Finge que a ação aconteceu meses antes, usando só dados anteriores. Um 'efeito' aqui indica viés no método."
    tipos_pergunta = _TIPOS
    requer = ("painel", "coortes")
    produz = ("placebo",)

    class Parametros(Parametros):
        desfecho: str | None = None
        deslocamento: int = Field(default=6, ge=2, le=24)
        n_boot: int = Field(default=299, ge=99, le=4999)

    def executar(self, ctx, p):
        r = ctx._resultado
        d = _desfecho(ctx, p.desfecho)
        base = _config_efeito(ctx)
        # com o efeito já estimado, o placebo também confere equivalência (IC estreito perto de zero)
        referencia = float(ctx.dados["efeito"]["att"]) if "efeito" in ctx.dados else None
        res = M.placebo_temporal(_painel_com_coortes(ctx, base["excluidos"]), y=f"taxa_{d.nome}", grupo="grupo", periodo="periodo",
                                 coorte="coorte", peso=base["peso"], deslocamento=p.deslocamento, n_boot=p.n_boot, seed=ctx.seed,
                                 att_referencia=referencia)
        r.dados["placebo"] = {**res, "desfecho": d.nome, "excluidos": base["excluidos"], "peso": base["peso"]}
        mp = ctx.metrica("efeito_placebo", res["att_placebo"], casas=2, sinal=True, descricao="efeito 'placebo' (deveria ser perto de zero)")
        r.resumo = f"Placebo com a ação {p.deslocamento} períodos antes: ⟦{mp.id}⟧ ({'passou' if res['passou'] else 'NÃO passou'})."
        ctx.afirmacao("fato", r.resumo, selo="A" if res["passou"] else "B", bases=[mp.id])
        r.robustez_ok = res["passou"]
        ctx.dados["robustez_ok"] = bool(res["passou"]) and ctx.dados.get("robustez_ok", True)
        return r

    def codigo(self, p, r):
        pl = r.dados["placebo"]
        return (_codigo_painel(pl.get("excluidos"))
                + f"dados['placebo'] = M.placebo_temporal(_p, y='taxa_{pl['desfecho']}', grupo='grupo', periodo='periodo', coorte='coorte', "
                f"peso={pl.get('peso', 'unidades')!r}, deslocamento={p.deslocamento}, n_boot={p.n_boot})")


@registrar_nativo
class DesfechoEstavel(Bloco):
    nome = "desfecho_estavel"
    versao = "1.0"
    papel = "Analista de dados"
    titulo = "O desfecho foi medido do mesmo jeito o tempo todo?"
    descricao = (
        "Compara a composição dos códigos que formam o desfecho antes e depois de um corte (PSI). Códigos novos ou "
        "que sumiram mudam o que se mede e podem fingir um efeito. Só os códigos incluídos entram na conta."
    )
    produz = ("estabilidade",)

    class Parametros(Parametros):
        desfecho: str | None = None
        corte: str | None = Field(default=None, description="AAAA-MM-DD; padrão: primeira coorte")
        limiar_psi: float = Field(default=0.1, gt=0)

    def executar(self, ctx, p):
        r = ctx._resultado
        d = _desfecho(ctx, p.desfecho)
        controles = None
        corte = p.corte
        if "coortes" in ctx.dados:
            c = ctx.dados["coortes"]
            controles = c.loc[c["coorte"].isna(), "grupo"].tolist() or None
            if corte is None and c["coorte"].notna().any():
                corte = str(pd.to_datetime(c["coorte"]).dropna().median().date())
        m = ctx.mapeamento
        if corte is None and m.periodo_inicio and m.periodo_fim:
            ini, fim = pd.Timestamp(m.periodo_inicio), pd.Timestamp(m.periodo_fim)
            corte = str((ini + (fim - ini) / 2).date())
        if not corte:
            r.status, r.erro = "falhou", "informe corte='AAAA-MM-DD' (não há coortes nem período no mapeamento)"
            return r
        q, b = codigos_antes_depois(m, d, corte=corte, grupos=controles)
        contagens = ctx.consultar("codigos", q, b)
        if controles:
            r.avisos.append(f"estabilidade medida só nos {len(controles)} grupos sem a ação (lá a ação não muda a composição).")
        res = M.estabilidade_janelas(contagens, limiar_psi=p.limiar_psi)
        r.dados["estabilidade"] = res
        r.dados["_estab_meta"] = {"corte": corte}
        r.tabelas["codigos_antes_depois"] = res["tabela"]
        if res["situacao"] == "inconclusivo":
            r.resumo = (f"Estabilidade dos códigos antes × depois de {corte}: inconclusiva (dados insuficientes depois do "
                        "grupo mínimo).")
            ctx.lacuna("Não deu para conferir se os códigos do desfecho mudaram ao longo do tempo (poucos dados por célula).",
                       "a conclusão assume que o desfecho foi registrado do mesmo jeito")
            r.premissas_ok = None
            return r
        if res.get("categorias_indeterminadas"):
            r.avisos.append(f"códigos sem dados suficientes num dos lados (zero ou cortados pelo grupo mínimo): "
                            f"{res['categorias_indeterminadas'][:8]} — ficaram fora do PSI.")
        mp = ctx.metrica("psi_codigos", res["psi"], casas=3, descricao="PSI dos códigos do desfecho antes × depois do corte")
        r.resumo = f"Composição dos códigos antes × depois de {corte}: PSI ⟦{mp.id}⟧ ({'estável' if res['estavel'] else 'mudou'})."
        ctx.afirmacao("fato", r.resumo, selo="A", bases=[mp.id])
        if res["categorias_novas"] or res["categorias_sumiram"] or not res["estavel"]:
            ctx.decisao(
                "Códigos do desfecho mudaram",
                f"Códigos novos: {res['categorias_novas'][:8]}; que sumiram: {res['categorias_sumiram'][:8]}; PSI acima do "
                f"limiar: {not res['estavel']}. Seguir assim?",
                [("a", "Seguir e registrar o limite", "a conclusão cita a mudança de registro"),
                 ("b", "Rever o desfecho", "volte ao mapeamento e ao universo")],
                recomendada="a", bloqueia=("aceite",), dados={"novos": res["categorias_novas"], "sumiram": res["categorias_sumiram"]},
            )
        r.premissas_ok = res["estavel"]
        return r

    def codigo(self, p, r):
        corte = r.dados.get("_estab_meta", {}).get("corte")
        return f"dados['estabilidade'] = M.estabilidade_janelas(consulta('{r.passo}', 'codigos'), limiar_psi={p.limiar_psi})"


@registrar_nativo
class Sensibilidade(Bloco):
    nome = "sensibilidade"
    versao = "1.0"
    papel = "Estatístico"
    titulo = "Sensibilidade do efeito aos riscos da carta"
    descricao = (
        "Refaz o efeito em variações: ignorar os primeiros períodos após a ação, excluir grupos suspeitos e sem "
        "ponderação. Se o efeito muda de sinal, a conclusão é frágil."
    )
    tipos_pergunta = _TIPOS
    requer = ("painel", "coortes", "efeito")
    produz = ("sensibilidade",)

    class Parametros(Parametros):
        desfecho: str | None = None
        cenarios: list[str] = Field(default_factory=lambda: ["ignorar_primeiro", "sem_ponderacao", "tendencia_coorte"])
        excluir_grupos: list[str] = Field(default_factory=list)
        n_boot: int = Field(default=199, ge=99, le=2999)

    def executar(self, ctx, p):
        r = ctx._resultado
        d = _desfecho(ctx, p.desfecho)
        base = ctx.dados["efeito"]
        y = f"taxa_{d.nome}"
        cenarios = list(p.cenarios) + (["excluir_grupos"] if p.excluir_grupos and "excluir_grupos" not in p.cenarios else [])
        for nome in cenarios:
            if nome not in ("ignorar_primeiro", "sem_ponderacao", "excluir_grupos", "tendencia_coorte"):
                r.avisos.append(f"cenário desconhecido: {nome}")
        principal = _config_efeito(ctx)
        janela = tuple(principal["janela_evento"]) if principal["janela_evento"] else None
        tabela = M.cenarios_did(_painel_com_coortes(ctx, principal["excluidos"]), y=y, cenarios=cenarios,
                                excluir_grupos=p.excluir_grupos, base=base, n_boot=p.n_boot, seed=ctx.seed,
                                peso=principal["peso"], ignorar_primeiros=principal["ignorar_primeiros"], janela_evento=janela)
        r.dados["_sens_meta"] = {"desfecho": d.nome, "cenarios": cenarios, "principal": principal}
        estavel = M.sensibilidade_estavel(tabela)
        r.dados["sensibilidade"] = tabela
        r.tabelas["sensibilidade"] = tabela
        ids = []
        for _, linha in tabela.iterrows():
            m = ctx.metrica(f"sens_{linha['cenario']}", linha["att"], casas=2, sinal=True, descricao=f"efeito no cenário '{linha['cenario']}'")
            ids.append(m.id)
        r.resumo = ("O efeito mantém o sinal em todos os cenários: " if estavel else "O efeito MUDA de sinal entre cenários: ") + \
            ", ".join(f"{c} ⟦{i}⟧" for c, i in zip(tabela["cenario"], ids)) + "."
        ctx.afirmacao("fato", r.resumo, selo="A" if estavel else "B", bases=ids)
        ctx.figura(graficos.pontos_com_ic(list(tabela["cenario"]), list(tabela["att"]), list(tabela["ic_inf"]), list(tabela["ic_sup"]),
                                          titulo="Efeito em cada cenário", eixo_x=f"efeito por {d.por}"),
                   nome="sensibilidade", titulo="Sensibilidade do efeito", descricao="Efeito e intervalo de 95% em cada cenário de sensibilidade.")
        r.robustez_ok = estavel
        ctx.dados["robustez_ok"] = estavel and ctx.dados.get("robustez_ok", True)
        ctx.tabela_para_ia("sensibilidade", tabela)
        return r

    def codigo(self, p, r):
        meta = r.dados.get("_sens_meta", {})
        pr = meta.get("principal") or {}
        janela = tuple(pr["janela_evento"]) if pr.get("janela_evento") else None
        return (_codigo_painel(pr.get("excluidos"))
                + f"dados['sensibilidade'] = M.cenarios_did(_p, y='taxa_{meta.get('desfecho')}', cenarios={meta.get('cenarios')!r}, "
                f"excluir_grupos={p.excluir_grupos!r}, base=dados['efeito'], n_boot={p.n_boot}, peso={pr.get('peso', 'unidades')!r}, "
                f"ignorar_primeiros={int(pr.get('ignorar_primeiros', 0))}, janela_evento={janela!r})")
