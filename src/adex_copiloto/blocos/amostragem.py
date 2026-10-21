"""Blocos de amostragem: plano amostral, amostra reprodutível, representatividade e separação.

Regra do especialista de amostragem (spec, seção 7): primeiro conhecer o universo,
preferir agregação na fonte para universos grandes e só amostrar quando o método
precisa de linhas por unidade. Toda amostra gera um Passaporte de Amostragem.
"""

from __future__ import annotations

import pandas as pd
from pydantic import Field

from adex_copiloto.blocos import estatistica as M
from adex_copiloto.blocos import sql
from adex_copiloto.blocos.base import Bloco, Parametros, Precondicao
from adex_copiloto.blocos.registro import registrar_nativo
from adex_copiloto.dados.porteiro import Modo
from adex_copiloto.estudo.passaporte import ComparacaoUniverso, Passaporte

# Tipos de pergunta que precisam de linhas por unidade (modelos individuais).
PRECISAM_UNIDADE = {"classificacao", "regressao", "segmentacao"}


@registrar_nativo
class PlanoAmostral(Bloco):
    nome = "plano_amostral"
    versao = "1.0"
    papel = "Amostragem"
    titulo = "Censo, agregado na fonte ou amostra?"
    descricao = (
        "Decide, com o tamanho do universo e o tipo de pergunta, se o estudo usa o universo agregado na "
        "fonte, o censo ou uma amostra (e de que tamanho). Gera o rascunho do Passaporte de Amostragem."
    )
    requer = ("populacao",)
    produz = ("passaporte",)

    class Parametros(Parametros):
        margem: float = Field(default=0.01, gt=0, lt=0.5, description="margem de erro desejada para proporções")
        confianca: float = Field(default=0.95, gt=0.5, lt=1)
        estratos: list[str] = Field(default_factory=list, description="colunas para estratificar (ex.: segmento)")
        minimo_por_estrato: int = Field(default=200, ge=0)
        teto_linhas: int | None = Field(default=None, description="padrão: data_policy.max_linhas_amostra")

    def executar(self, ctx, p):
        r = ctx._resultado
        m = ctx.mapeamento
        pop = ctx.dados["populacao"]
        universo = int(pop.groupby("periodo")["unidades"].sum().max()) if "periodo" in pop else int(pop["unidades"].sum())
        teto = p.teto_linhas or ctx.config.data_policy.max_linhas_amostra
        tipo = ctx.carta.tipo_pergunta
        precisa_unidade = tipo in PRECISAM_UNIDADE
        mu = ctx.metrica("universo_unidades", universo, compacto=True, descricao="unidades no universo (maior período)")
        if not precisa_unidade:
            metodo, tamanho, justificativa = (
                "agregado_na_fonte", None,
                "O método trabalha com agregados (grupo × período); o painel na fonte cobre 100% do universo sem trazer linhas.",
            )
        else:
            n = M.tamanho_amostra_proporcao(p.margem, confianca=p.confianca, populacao=universo)
            if p.estratos:
                n = max(n, p.minimo_por_estrato * 10)
            n = min(n, teto, universo)
            if n >= universo * 0.9 and universo <= teto:
                metodo, tamanho = "censo", universo
                justificativa = "O universo cabe no teto de linhas: usar todas as unidades evita erro amostral."
            else:
                metodo = "estratificada" if p.estratos else "aleatoria_simples"
                tamanho = int(n)
                justificativa = (
                    f"Amostra {'estratificada' if p.estratos else 'aleatória simples'} de {tamanho} unidades: margem de "
                    f"{p.margem:.1%} para proporções com {p.confianca:.0%} de confiança."
                )
        passaporte = Passaporte(
            id=ctx.ids.proximo("PA"),
            populacao=ctx.carta.valor("populacao") or m.populacao,
            fonte=m.tabelas(),
            janela=f"{m.periodo_inicio or 'início'} a {m.periodo_fim or 'fim'}",
            metodo=metodo,
            seed=ctx.seed if metodo not in ("censo", "agregado_na_fonte") else None,
            tamanho=tamanho,
            tamanho_universo=universo,
            estratos=p.estratos,
            cobertura=1.0 if metodo in ("censo", "agregado_na_fonte") else (tamanho / universo if universo else None),
            criterios=[f"{f.coluna} {f.operador} {f.valor}" for f in m.filtros],
            justificativa=justificativa,
            sql_sha256=[c.sha256 for c in r.consultas],
        )
        r.dados["passaporte"] = passaporte
        ctx.passaporte = passaporte
        texto = f"Universo de ⟦{mu.id}⟧ unidades. Recomendação: {metodo.replace('_', ' ')}. {justificativa}"
        if tamanho:
            mt = ctx.metrica("tamanho_amostra", tamanho, compacto=True, descricao="tamanho recomendado da amostra")
            texto += f" Tamanho: ⟦{mt.id}⟧."
        r.resumo = texto
        ctx.afirmacao("recomendacao", texto, selo="B" if precisa_unidade else "A", bases=[mu.id])
        return r


@registrar_nativo
class AmostraUnidades(Bloco):
    nome = "amostra_unidades"
    versao = "1.0"
    papel = "Amostragem"
    titulo = "Amostra reprodutível de unidades (SAMPLE ... SEED)"
    descricao = (
        "Sorteia unidades na fonte com amostragem Bernoulli reprodutível (mesma seed, mesma amostra). As linhas "
        "ficam no kernel e nunca vão para a IA. Exige passaporte aprovado."
    )
    requer = ("passaporte",)
    produz = ("amostra",)

    class Parametros(Parametros):
        colunas: list[str] = Field(default_factory=list, description="atributos da população a trazer")
        referencia: str | None = Field(default=None, description="período de referência AAAA-MM-DD (população mensal)")
        percentual: float | None = Field(default=None, gt=0, le=100, description="padrão: calculado do passaporte")
        com_alvo: bool = Field(default=True, description="traz o alvo do mapeamento (janela depois da referência)")
        eventos_como_atributos: list[str] = Field(default_factory=list, description="desfechos contados ANTES da referência")
        janela_meses: int = Field(default=3, ge=1, le=24)

    def precondicoes(self, ctx, p):
        pre = super().precondicoes(ctx, p)
        pa = ctx.dados.get("passaporte")
        pre.append(Precondicao("passaporte aprovado", bool(pa and pa.aprovado), "aprove o passaporte antes de sortear"))
        return pre

    def executar(self, ctx, p):
        r = ctx._resultado
        m = ctx.mapeamento
        pa: Passaporte = ctx.dados["passaporte"]
        colunas = p.colunas or list(dict.fromkeys(m.atributos + m.grupos))
        if pa.metodo in ("censo",):
            pct = 100.0
        else:
            pct = p.percentual or min(100.0, 100.0 * (pa.tamanho or 0) / max(1, pa.tamanho_universo or 1) * 1.05)
        partes = []
        estratos_partes: list[str] = []
        if pa.metodo == "estratificada" and pa.estratos:
            q, b = sql.distribuicao(m.populacao, pa.estratos[0], m.filtros)
            dist = ctx.consultar("estratos", q, b).set_index("categoria")["n"]
            alocacao = M.alocacao_estratificada(dist, int(pa.tamanho or 0), minimo=200)
            for i, (estrato, n_e) in enumerate(alocacao.items()):
                if n_e <= 0:
                    continue
                pct_e = min(100.0, 100.0 * n_e / max(1, dist[estrato]) * 1.05)
                q, b = sql.amostra_unidades(m, percentual=pct_e, seed=ctx.seed + i, colunas=colunas,
                                            referencia=p.referencia, estrato=(pa.estratos[0], estrato))
                parte = ctx.consultar(f"amostra_estrato_{i}", q, b, modo=Modo.DETALHE)
                # Peso amostral = unidades do estrato no universo ÷ sorteadas: o mínimo por estrato
                # sobre-representa os pequenos, e médias sem peso saem tortas (medido: 12,6% contra 11,3%).
                parte["peso_amostral"] = float(dist[estrato]) / max(1, len(parte))
                partes.append(parte)
                estratos_partes.append(str(estrato))
        else:
            q, b = sql.amostra_unidades(m, percentual=pct, seed=ctx.seed, colunas=colunas, referencia=p.referencia)
            partes.append(ctx.consultar("amostra", q, b, modo=Modo.DETALHE))
        amostra = pd.concat(partes, ignore_index=True) if partes else pd.DataFrame()
        extras = (p.com_alvo and m.alvo is not None) or bool(p.eventos_como_atributos)
        if extras and pa.metodo == "estratificada":
            r.avisos.append("alvo e eventos por unidade ainda não são trazidos em amostra estratificada; use colunas da população.")
        elif extras:
            chave = m.unidade.lower()
            if p.com_alvo and m.alvo is not None:
                if m.alvo.tabela.upper() == m.populacao.upper() and m.alvo.coluna.lower() in amostra:
                    amostra = amostra.rename(columns={m.alvo.coluna.lower(): "alvo"})
                else:
                    q, b = sql.amostra_com_alvo(m, percentual=pct, seed=ctx.seed, referencia=p.referencia, janela_meses=p.janela_meses)
                    alvo = ctx.consultar("amostra_alvo", q, b, modo=Modo.DETALHE)
                    amostra = amostra.merge(alvo, on=chave, how="left")
            for nome in p.eventos_como_atributos:
                d = next((x for x in m.desfechos if x.nome == nome), None)
                if d is None or not p.referencia:
                    r.avisos.append(f"evento '{nome}' ignorado (desfecho inexistente ou sem referencia=).")
                    continue
                q, b = sql.amostra_com_eventos(m, d, percentual=pct, seed=ctx.seed, referencia=p.referencia, janela_meses=p.janela_meses)
                ev = ctx.consultar(f"amostra_ev_{nome}", q, b, modo=Modo.DETALHE)
                amostra = amostra.merge(ev, on=chave, how="left")
        if "peso_amostral" not in amostra and len(amostra):
            amostra["peso_amostral"] = float(pa.tamanho_universo or len(amostra)) / len(amostra)
        if estratos_partes:
            r.avisos.append("amostra estratificada: cada unidade tem peso_amostral (inverso da fração do estrato); "
                            "médias e proporções da população precisam usar esse peso.")
        r.dados["amostra"] = amostra
        r.tabelas["amostra_colunas"] = pd.DataFrame({"coluna": list(amostra.columns)})
        pa.tamanho = len(amostra)
        r.dados["_amostra_meta"] = {"consultas": [c.nome for c in r.consultas], "chave": m.unidade.lower(),
                                    "estratos_partes": estratos_partes, "universo": int(pa.tamanho_universo or 0)}
        mt = ctx.metrica("amostra_obtida", len(amostra), compacto=True, descricao="unidades sorteadas (ficam só no kernel)")
        r.resumo = f"Amostra sorteada na fonte com seed {ctx.seed}: ⟦{mt.id}⟧ unidades (linhas ficam no kernel)."
        ctx.afirmacao("fato", r.resumo, selo="A", bases=[mt.id])
        return r

    def codigo(self, p, r):
        meta = r.dados.get("_amostra_meta", {})
        nomes = meta.get("consultas", [])
        base = [n for n in nomes if n == "amostra" or n.startswith("amostra_estrato_")]
        extras = [n for n in nomes if n == "amostra_alvo" or n.startswith("amostra_ev_")]
        linhas = ["_partes = [" + ", ".join(f"consulta('{r.passo}', '{n}')" for n in base) + "]"]
        if meta.get("estratos_partes"):
            linhas += [f"_dist = consulta('{r.passo}', 'estratos').assign(categoria=lambda d: d['categoria'].astype(str)).set_index('categoria')['n']",
                       f"for _parte, _e in zip(_partes, {meta['estratos_partes']!r}):",
                       "    _parte['peso_amostral'] = float(_dist[_e]) / max(1, len(_parte))"]
        linhas.append("dados['amostra'] = pd.concat(_partes, ignore_index=True)")
        if not meta.get("estratos_partes"):
            linhas.append(f"dados['amostra']['peso_amostral'] = float({meta.get('universo') or 0} or len(dados['amostra'])) / max(1, len(dados['amostra']))")
        for n in extras:
            linhas.append(f"dados['amostra'] = dados['amostra'].merge(consulta('{r.passo}', '{n}'), on={meta.get('chave')!r}, how='left')")
        if "amostra_alvo" in extras:
            linhas.append("if 'alvo' in dados['amostra']: dados['amostra']['alvo'] = dados['amostra']['alvo'].fillna(0)")
        return "\n".join(linhas)


@registrar_nativo
class Representatividade(Bloco):
    nome = "representatividade"
    versao = "1.0"
    papel = "Amostragem"
    titulo = "A amostra parece o universo?"
    descricao = "Compara a amostra com o universo (PSI por categoria) nas colunas de grupo e estrato. Atualiza o passaporte."
    requer = ("amostra", "passaporte")
    produz = ("representatividade",)

    class Parametros(Parametros):
        variaveis: list[str] = Field(default_factory=list, description="padrão: grupos + estratos")

    def executar(self, ctx, p):
        r = ctx._resultado
        m = ctx.mapeamento
        pa: Passaporte = ctx.dados["passaporte"]
        amostra: pd.DataFrame = ctx.dados["amostra"]
        variaveis = p.variaveis or list(dict.fromkeys(m.grupos[:1] + pa.estratos))
        universo = {}
        for i, v in enumerate(variaveis):
            q, b = sql.distribuicao(m.populacao, v, m.filtros)
            dist = ctx.consultar(f"universo_{i}", q, b)
            universo[v.lower()] = dist.set_index("categoria")["n"]
        amostra_l = amostra.rename(columns=str.lower)
        tabela = M.representatividade(universo, amostra_l, pesos="peso_amostral" if "peso_amostral" in amostra_l else None)
        pa.comparacao_universo = [
            ComparacaoUniverso(variavel=l.variavel, psi=l.psi, situacao=l.situacao) for l in tabela.itertuples()
        ]
        problemas = tabela[tabela["situacao"] != "ok"]
        pa.riscos_vies = [f"{l.variavel}: distribuição difere do universo (PSI {l.psi:.2f})" for l in problemas.itertuples()] or \
            [f"Sem diferença relevante nas variáveis comparadas ({', '.join(variaveis)}); outras variáveis não foram avaliadas."]
        r.dados["representatividade"] = tabela
        r.tabelas["representatividade"] = tabela
        mp = ctx.metrica("psi_maximo", float(tabela["psi"].max()) if len(tabela) else float("nan"), casas=3,
                         descricao="maior PSI entre amostra e universo")
        r.resumo = f"Maior diferença entre amostra e universo: PSI ⟦{mp.id}⟧ ({'ok' if problemas.empty else 'atenção'})."
        ctx.afirmacao("fato", r.resumo, selo="A", bases=[mp.id])
        r.premissas_ok = problemas.empty
        return r


@registrar_nativo
class Separacao(Bloco):
    nome = "separacao"
    versao = "1.0"
    papel = "Cientista de dados"
    titulo = "Separar treino e teste sem vazamento"
    descricao = (
        "Separa treino e teste por tempo (o teste é sempre depois) ou por grupo inteiro (o mesmo grupo nunca "
        "fica dos dois lados). Separação aleatória só quando nem tempo nem grupo importam."
    )
    requer = ("amostra",)
    produz = ("treino", "teste")

    class Parametros(Parametros):
        tipo: str = Field(default="temporal", description="temporal | grupo | aleatoria")
        coluna: str | None = Field(default=None, description="coluna de data (temporal) ou de grupo")
        corte: str | None = Field(default=None, description="data de corte AAAA-MM-DD (temporal)")
        fracao_teste: float = Field(default=0.25, gt=0, lt=0.9)

    def executar(self, ctx, p):
        r = ctx._resultado
        df: pd.DataFrame = ctx.dados["amostra"]
        if p.tipo == "temporal":
            coluna = (p.coluna or (ctx.mapeamento.tempo or "")).lower()
            if coluna not in df:
                r.status, r.erro = "falhou", f"coluna de data '{coluna}' não está na amostra"
                return r
            corte = p.corte or str(pd.to_datetime(df[coluna]).quantile(1 - p.fracao_teste).date())
            treino, teste = M.separar_temporal(df, coluna, corte)
            descricao = f"por tempo, corte em {corte}"
        elif p.tipo == "grupo":
            coluna = (p.coluna or ctx.mapeamento.grupos[0]).lower()
            treino, teste = M.separar_por_grupo(df, coluna, p.fracao_teste, seed=ctx.seed)
            descricao = f"por grupo inteiro ({coluna})"
        else:
            teste = df.sample(frac=p.fracao_teste, random_state=ctx.seed)
            treino = df.drop(teste.index)
            descricao = "aleatória"
            if ctx.mapeamento and ctx.mapeamento.tempo:
                r.avisos.append("há coluna de tempo: a separação aleatória pode vazar informação do futuro.")
        r.dados.update({"treino": treino, "teste": teste})
        r.dados["_sep_meta"] = {"tipo": p.tipo, "coluna": locals().get("coluna"), "corte": locals().get("corte")}
        if ctx.passaporte:
            ctx.passaporte.separacao = descricao
        mt, me = (ctx.metrica("n_treino", len(treino), descricao="linhas de treino"),
                  ctx.metrica("n_teste", len(teste), descricao="linhas de teste"))
        r.resumo = f"Separação {descricao}: ⟦{mt.id}⟧ no treino e ⟦{me.id}⟧ no teste."
        ctx.afirmacao("fato", r.resumo, selo="A", bases=[mt.id, me.id])
        r.premissas_ok = p.tipo != "aleatoria" or not (ctx.mapeamento and ctx.mapeamento.tempo)
        return r

    def codigo(self, p, r):
        meta = r.dados.get("_sep_meta", {})
        if meta.get("tipo") == "temporal":
            return f"dados['treino'], dados['teste'] = M.separar_temporal(dados['amostra'], {meta.get('coluna')!r}, {meta.get('corte')!r})"
        if meta.get("tipo") == "grupo":
            return (f"dados['treino'], dados['teste'] = M.separar_por_grupo(dados['amostra'], {meta.get('coluna')!r}, "
                    f"{p.fracao_teste}, seed=SEED)")
        return ("dados['teste'] = dados['amostra'].sample(frac=" + str(p.fracao_teste) + ", random_state=SEED)\n"
                "dados['treino'] = dados['amostra'].drop(dados['teste'].index)")
