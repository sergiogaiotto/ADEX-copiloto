"""Blocos de universo e engenharia de dados: painel agregado, perfil, qualidade e ligações."""

from __future__ import annotations

import pandas as pd
from pydantic import Field

from adex_copiloto.blocos import estatistica as M
from adex_copiloto.blocos import graficos, sql
from adex_copiloto.blocos.base import Bloco, ContextoBloco, Parametros, Precondicao, ResultadoBloco
from adex_copiloto.blocos.registro import registrar_nativo
from adex_copiloto.dados.porteiro import Modo
from adex_copiloto.estudo.evidencia import calcular_selo


@registrar_nativo
class PainelAgregado(Bloco):
    nome = "painel_agregado"
    versao = "1.0"
    papel = "Engenheiro de dados"
    titulo = "Painel agregado na fonte (grupo × período)"
    descricao = (
        "Monta, dentro do banco, o painel grupo × período com unidades, eventos de cada desfecho e unidades "
        "tratadas. Só agregados com grupo mínimo saem da fonte. É o universo do estudo."
    )
    produz = ("painel", "populacao")
    exige_mapeamento = ("grupos",)

    class Parametros(Parametros):
        desfechos: list[str] | None = Field(default=None, description="nomes dos desfechos; vazio = todos")
        incluir_tratamento: bool = True
        sem_filtro_de_codigos: bool = Field(default=False, description="ignora a lista de códigos do desfecho (sensibilidade)")

    def executar(self, ctx: ContextoBloco, p: Parametros) -> ResultadoBloco:
        r = ctx._resultado
        m = ctx.mapeamento
        q, b = sql.populacao_por_periodo(m)
        pop = ctx.consultar("populacao", q, b)
        desfechos = {}
        for d in m.desfechos:
            if p.desfechos and d.nome not in p.desfechos:
                continue
            q, b = sql.desfecho_por_periodo(m, d, pular_codigos=p.sem_filtro_de_codigos)
            desfechos[d.nome] = ctx.consultar(f"desfecho_{d.nome}", q, b)
        trat = None
        if p.incluir_tratamento and m.tratamento and m.tempo:
            q, b = sql.tratamento_por_periodo(m)
            trat = ctx.consultar("tratamento", q, b)
        if "periodo" not in pop:
            pop = pop.assign(periodo=pd.Timestamp("1970-01-01"))
        painel = M.montar_painel(pop, desfechos, trat, por=(m.desfecho_principal.por if m.desfecho_principal else 1000))
        r.dados.update({"painel": painel, "populacao": pop})
        r.tabelas["painel"] = painel
        n_grupos = painel["grupo"].nunique()
        n_periodos = painel["periodo"].nunique()
        m_celulas = ctx.metrica("celulas_painel", len(painel), descricao="células do painel (grupo × período)")
        m_grupos = ctx.metrica("grupos", n_grupos, descricao="grupos no painel")
        m_periodos = ctx.metrica("periodos", n_periodos, descricao="períodos no painel")
        m_unid = ctx.metrica("unidades_media", painel.groupby("periodo")["unidades"].sum().mean(), compacto=True,
                             descricao="unidades ativas por período (média)")
        partes = [f"O universo tem ⟦{m_celulas.id}⟧ células: ⟦{m_grupos.id}⟧ grupos × ⟦{m_periodos.id}⟧ períodos, "
                  f"com ⟦{m_unid.id}⟧ unidades por período em média."]
        for nome in desfechos:
            total = painel[f"ev_{nome}"].sum()
            mt = ctx.metrica(f"eventos_{nome}", total, compacto=True, descricao=f"eventos de '{nome}' no período")
            partes.append(f"Desfecho '{nome}': ⟦{mt.id}⟧ eventos no período.")
        ctx.afirmacao("fato", " ".join(partes), selo=calcular_selo(base="censo", natureza="descritiva"),
                      bases=[m_celulas.id, m_unid.id])
        if trat is not None:
            com_acao = painel.groupby("grupo")["fracao_tratada"].max()
            mt = ctx.metrica("grupos_com_acao", int((com_acao >= (m.tratamento.limiar if m.tratamento else 0.5)).sum()),
                             descricao="grupos que chegaram ao limiar de tratamento")
            partes.append(f"⟦{mt.id}⟧ grupos chegaram ao limiar da ação.")
        principal = m.desfecho_principal
        if principal and f"taxa_{principal.nome}" in painel and n_periodos > 1:
            serie = painel.groupby("periodo").apply(
                lambda d: (d[f"ev_{principal.nome}"].sum() / d["unidades"].sum()) * principal.por, include_groups=False
            )
            ctx.figura(
                graficos.linhas({principal.nome: serie}, titulo=f"{principal.nome} por {principal.por:,} unidades".replace(",", "."),
                                eixo_y=f"por {principal.por:,}".replace(",", ".")),
                nome="universo_desfecho", titulo=f"Evolução de {principal.nome}",
                descricao=f"Linha mensal da taxa de {principal.nome} por {principal.por} unidades, somando todos os grupos.",
            )
        resumo_ia = painel.groupby("periodo").agg(unidades=("unidades", "sum"), grupos=("grupo", "nunique")).reset_index()
        ctx.tabela_para_ia("universo_por_periodo", resumo_ia)
        r.resumo = " ".join(partes)
        r.premissas_ok = True
        return r

    def codigo(self, p, r) -> str:
        linhas = ['pop = consulta("{passo}", "populacao")']
        des = [c.nome for c in r.consultas if c.nome.startswith("desfecho_")]
        linhas.append("desfechos = {" + ", ".join(f'"{d[len("desfecho_"):]}": consulta("{{passo}}", "{d}")' for d in des) + "}")
        trat = any(c.nome == "tratamento" for c in r.consultas)
        linhas.append('trat = consulta("{passo}", "tratamento")' if trat else "trat = None")
        linhas.append("dados['painel'] = M.montar_painel(pop, desfechos, trat)")
        linhas.append("dados['populacao'] = pop")
        return "\n".join(linhas).replace("{passo}", r.passo)


@registrar_nativo
class Perfilamento(Bloco):
    nome = "perfilamento"
    versao = "1.0"
    papel = "Engenheiro de dados"
    titulo = "Perfil das colunas (nulos, distintos)"
    descricao = "Conta linhas, preenchidos e distintos por coluna, na fonte. Nenhum valor individual sai do banco."
    produz = ("perfil",)

    class Parametros(Parametros):
        tabela: str | None = Field(default=None, description="ESQUEMA.TABELA; vazio = tabela da população")
        colunas: list[str] | None = None
        max_colunas: int = Field(default=60, ge=1, le=200)

    def precondicoes(self, ctx, p):
        pre = super().precondicoes(ctx, p)
        ok = bool(p.tabela or (ctx.mapeamento and ctx.mapeamento.populacao))
        pre.append(Precondicao("tabela definida", ok, "" if ok else "informe tabela= ou faça a descoberta"))
        return pre

    def executar(self, ctx, p):
        r = ctx._resultado
        tabela = p.tabela or ctx.mapeamento.populacao
        ficha = ctx.fonte.catalogo().resolver(tabela)
        if ficha is None:
            r.status, r.erro = "falhou", f"tabela {tabela} não existe no catálogo"
            return r
        colunas = p.colunas or [c.nome for c in ficha.colunas]
        colunas = colunas[: p.max_colunas]
        linha = ctx.consultar("perfil", sql.perfil_tabela(ficha.nome_completo, colunas)).iloc[0].to_dict()
        perfil = M.perfil_de_contagens(linha, colunas)
        chaves = [ctx.mapeamento.unidade] if ctx.mapeamento and not ctx.mapeamento.tempo else []
        achados = M.checar_qualidade(perfil, chaves=chaves)
        r.dados["perfil"] = perfil
        r.tabelas.update({"perfil": perfil, "achados_qualidade": achados})
        ml = ctx.metrica("linhas_tabela", int(linha.get("linhas", 0)), compacto=True, descricao=f"linhas em {ficha.nome_completo}")
        mn = ctx.metrica("colunas_muitos_nulos", int((achados["achado"] == "muitos nulos").sum()),
                         descricao="colunas com mais de 20% de nulos")
        r.resumo = f"{ficha.nome_completo} tem ⟦{ml.id}⟧ linhas; ⟦{mn.id}⟧ colunas com muitos nulos."
        ctx.afirmacao("fato", r.resumo, selo="A", bases=[ml.id, mn.id])
        ctx.tabela_para_ia("perfil", perfil[["coluna", "nulos_pct", "unicidade"]])
        r.premissas_ok = True
        return r

    def codigo(self, p, r):
        return (f'linha = consulta("{r.passo}", "perfil").iloc[0].to_dict()\n'
                f"dados['perfil'] = M.perfil_de_contagens(linha, {list(r.dados['perfil']['coluna'])!r})")


@registrar_nativo
class Qualidade(Bloco):
    nome = "qualidade"
    versao = "1.0"
    papel = "Engenheiro de dados"
    titulo = "Qualidade: repetições e cobertura no tempo"
    descricao = (
        "Confere se cada unidade aparece uma vez por período e se há buracos no tempo. A contagem de "
        "repetições roda na fonte e só o total sai."
    )
    produz = ("qualidade",)
    exige_mapeamento = ("populacao",)

    class Parametros(Parametros):
        pass

    def executar(self, ctx, p):
        r = ctx._resultado
        m = ctx.mapeamento
        u = f"p.{sql.ident(m.unidade)}"
        tabela = sql.ident(m.populacao)
        if m.tempo:
            per = sql.trunc(f"p.{sql.ident(m.tempo)}", m.granularidade)
            q_rep = (f"SELECT COUNT(*) AS repetidos FROM (SELECT {u} AS u, {per} AS per, COUNT(*) AS c FROM {tabela} p "
                     f"GROUP BY {u}, {per} HAVING COUNT(*) > 1) x")
        else:
            q_rep = f"SELECT COUNT(*) AS repetidos FROM (SELECT {u} AS u, COUNT(*) AS c FROM {tabela} p GROUP BY {u} HAVING COUNT(*) > 1) x"
        repetidos = int(ctx.consultar("repetidos", q_rep, modo=Modo.DETALHE).iloc[0, 0])
        mr = ctx.metrica("unidades_repetidas", repetidos, descricao="unidades repetidas no mesmo período")
        textos = [f"Unidades repetidas no mesmo período: ⟦{mr.id}⟧."]
        buracos = []
        if m.tempo:
            q_cob = (f"SELECT {sql.trunc('p.' + sql.ident(m.tempo), m.granularidade)} AS periodo, COUNT(*) AS n "
                     f"FROM {tabela} p GROUP BY {sql.trunc('p.' + sql.ident(m.tempo), m.granularidade)}")
            cob = ctx.consultar("cobertura", q_cob).sort_values("periodo")
            cob["periodo"] = pd.to_datetime(cob["periodo"])
            if len(cob) > 2:
                freq = {"mes": "MS", "semana": "W-MON", "dia": "D"}[m.granularidade]
                esperado = pd.date_range(cob["periodo"].min(), cob["periodo"].max(), freq=freq)
                buracos = [d.date().isoformat() for d in esperado if d not in set(cob["periodo"])]
            mb = ctx.metrica("periodos_sem_dado", len(buracos), descricao="períodos sem nenhum dado no intervalo")
            textos.append(f"Períodos sem dado: ⟦{mb.id}⟧.")
            r.tabelas["cobertura"] = cob
        if repetidos:
            ctx.lacuna(f"{repetidos} unidades aparecem mais de uma vez no mesmo período em {m.populacao}.",
                       "contagens por unidade podem estar infladas; o painel usa COUNT(DISTINCT)")
        if buracos:
            ctx.lacuna(f"Há períodos sem dado em {m.populacao}: {', '.join(buracos[:6])}.", "séries e comparações nesses períodos ficam sem base")
        r.dados["qualidade"] = {"repetidos": repetidos, "buracos": buracos}
        r.resumo = " ".join(textos)
        ctx.afirmacao("fato", r.resumo, selo="A", bases=[mr.id])
        r.premissas_ok = repetidos == 0 and not buracos
        return r


@registrar_nativo
class Ligacoes(Bloco):
    nome = "ligacoes"
    versao = "1.0"
    papel = "Engenheiro de dados"
    titulo = "Ligações entre tabelas conferidas no banco"
    descricao = "Para cada ligação proposta, conta quantas linhas acham par do outro lado. Só contagens saem do banco."
    produz = ("ligacoes",)
    exige_mapeamento = ("ligacoes",)

    class Parametros(Parametros):
        taxa_minima: float = Field(default=0.9, ge=0, le=1)

    def executar(self, ctx, p):
        r = ctx._resultado
        linhas = []
        for i, lig in enumerate(ctx.mapeamento.ligacoes):
            q = sql.contagem_ligacao(lig.de_tabela, lig.de_coluna, lig.para_tabela, lig.para_coluna)
            d = ctx.consultar(f"ligacao_{i}", q).iloc[0]
            taxa = float(d["com_par"]) / float(d["linhas"]) if d["linhas"] else 0.0
            lig.taxa, lig.verificada = taxa, True
            linhas.append({"de": f"{lig.de_tabela}.{lig.de_coluna}", "para": f"{lig.para_tabela}.{lig.para_coluna}", "taxa": taxa})
            mt = ctx.metrica(f"taxa_ligacao_{i}", taxa, unidade="%", casas=1,
                             descricao=f"linhas de {lig.de_tabela} que acham par em {lig.para_tabela}")
            selo = "A" if taxa >= p.taxa_minima else "C"
            ctx.afirmacao("fato", f"{lig.de_tabela} ↔ {lig.para_tabela} por {lig.de_coluna}: ⟦{mt.id}⟧ das linhas têm par.",
                          selo=selo, bases=[mt.id])
            if taxa < p.taxa_minima:
                ctx.lacuna(f"A ligação {lig.de_tabela}.{lig.de_coluna} → {lig.para_tabela}.{lig.para_coluna} perde "
                           f"{(1 - taxa):.0%} das linhas.", "o que não liga some da análise; pode haver viés")
        tabela = pd.DataFrame(linhas)
        r.dados["ligacoes"] = tabela
        r.tabelas["ligacoes"] = tabela
        r.resumo = f"{len(linhas)} ligação(ões) conferida(s) com contagens no banco."
        r.premissas_ok = bool(len(tabela)) and bool((tabela["taxa"] >= p.taxa_minima).all())
        return r
