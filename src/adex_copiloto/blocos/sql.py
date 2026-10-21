"""Modelos fixos de SQL (Oracle) gerados a partir do mapeamento.

A IA nunca escreve estes SQLs: eles saem de modelos com nomes conferidos no
catálogo e valores como binds. Mesmo assim, tudo passa pelo porteiro.
"""

from __future__ import annotations

import re
from typing import Any

from adex_copiloto.erros import BlocoErro
from adex_copiloto.estudo.mapeamento import FiltroSimples, FonteDesfecho, Mapeamento

_IDENT = re.compile(r"^[A-Za-z][A-Za-z0-9_$#]{0,127}$")
_TRUNC = {"mes": "MONTH", "semana": "IW", "dia": "DD"}


def ident(nome: str) -> str:
    """Identificador Oracle sem aspas, conferido (defesa extra contra injeção)."""
    partes = (nome or "").split(".")
    if not partes or any(not _IDENT.match(p) for p in partes) or len(partes) > 2:
        raise BlocoErro(f"Nome inválido para SQL: {nome!r}")
    return ".".join(p.upper() for p in partes)


def trunc(expr: str, granularidade: str) -> str:
    return f"TRUNC({expr}, '{_TRUNC[granularidade]}')"


def _filtros(filtros: list[FiltroSimples], alias: str, binds: dict[str, Any], prefixo: str = "f") -> list[str]:
    condicoes = []
    for i, f in enumerate(filtros):
        col = f"{alias}.{ident(f.coluna)}"
        if f.operador in ("in", "not in"):
            valores = f.valor if isinstance(f.valor, list) else [f.valor]
            nomes = []
            for j, v in enumerate(valores):
                nome = f"{prefixo}{i}_{j}"
                binds[nome] = v
                nomes.append(f":{nome}")
            condicoes.append(f"{col} {f.operador.upper()} ({', '.join(nomes)})")
        else:
            nome = f"{prefixo}{i}"
            binds[nome] = f.valor
            condicoes.append(f"{col} {f.operador} :{nome}")
    return condicoes


def _janela(m: Mapeamento, expr_data: str, binds: dict[str, Any]) -> list[str]:
    cond = []
    if m.periodo_inicio:
        binds["ini"] = m.periodo_inicio
        cond.append(f"{expr_data} >= TO_DATE(:ini, 'YYYY-MM-DD')")
    if m.periodo_fim:
        binds["fim"] = m.periodo_fim
        cond.append(f"{expr_data} < TO_DATE(:fim, 'YYYY-MM-DD')")
    return cond


def _where(condicoes: list[str]) -> str:
    return ("WHERE " + " AND ".join(condicoes)) if condicoes else ""


def populacao_por_periodo(m: Mapeamento) -> tuple[str, dict[str, Any]]:
    """Unidades distintas por grupo × período (o 'universo' agregado na fonte)."""
    if not m.grupos:
        raise BlocoErro("O painel precisa de pelo menos uma coluna de grupo (ex.: cidade) no mapeamento.")
    binds: dict[str, Any] = {}
    g = f"p.{ident(m.grupos[0])}"
    u = f"p.{ident(m.unidade)}"
    if m.tempo:
        per = trunc(f"p.{ident(m.tempo)}", m.granularidade)
        cond = _janela(m, f"p.{ident(m.tempo)}", binds) + _filtros(m.filtros, "p", binds)
        sql = (
            f"SELECT {g} AS grupo, {per} AS periodo, COUNT(DISTINCT {u}) AS unidades "
            f"FROM {ident(m.populacao)} p {_where(cond)} GROUP BY {g}, {per}"
        )
    else:
        cond = _filtros(m.filtros, "p", binds)
        sql = f"SELECT {g} AS grupo, COUNT(DISTINCT {u}) AS unidades FROM {ident(m.populacao)} p {_where(cond)} GROUP BY {g}"
    return sql, binds


def desfecho_por_periodo(m: Mapeamento, d: FonteDesfecho, *, pular_codigos: bool = False) -> tuple[str, dict[str, Any]]:
    """Eventos do desfecho por grupo × período, contados sobre a população (LEFT JOIN)."""
    binds: dict[str, Any] = {}
    g = f"p.{ident(m.grupos[0])}"
    u = f"p.{ident(m.unidade)}"
    chave = f"e.{ident(d.chave_unidade)}"
    data_e = f"e.{ident(d.coluna_data)}"
    medida = f"SUM({'e.' + ident(d.coluna_valor)})" if d.agregacao == "soma" and d.coluna_valor else f"COUNT({chave})"
    junta = [f"{chave} = {u}"]
    if d.coluna_codigo and d.codigos and not pular_codigos:
        nomes = []
        for j, c in enumerate(d.codigos):
            binds[f"c{j}"] = c
            nomes.append(f":c{j}")
        junta.append(f"e.{ident(d.coluna_codigo)} IN ({', '.join(nomes)})")
    if m.tempo:
        per_p = trunc(f"p.{ident(m.tempo)}", m.granularidade)
        junta.append(f"{trunc(data_e, m.granularidade)} = {per_p}")
        cond = _janela(m, f"p.{ident(m.tempo)}", binds) + _filtros(m.filtros, "p", binds)
        sql = (
            f"SELECT {g} AS grupo, {per_p} AS periodo, COUNT(DISTINCT {u}) AS unidades, {medida} AS eventos "
            f"FROM {ident(m.populacao)} p LEFT JOIN {ident(d.tabela)} e ON {' AND '.join(junta)} "
            f"{_where(cond)} GROUP BY {g}, {per_p}"
        )
    else:
        per_e = trunc(data_e, m.granularidade)
        cond = _janela(m, data_e, binds) + _filtros(m.filtros, "p", binds)
        sql = (
            f"SELECT {g} AS grupo, {per_e} AS periodo, COUNT(DISTINCT {u}) AS unidades, {medida} AS eventos "
            f"FROM {ident(m.populacao)} p JOIN {ident(d.tabela)} e ON {' AND '.join(junta)} "
            f"{_where(cond)} GROUP BY {g}, {per_e}"
        )
    return sql, binds


def tratamento_por_periodo(m: Mapeamento) -> tuple[str, dict[str, Any]]:
    """Unidades já tratadas (ação recebida) até o fim de cada período, por grupo."""
    if m.tratamento is None or not m.tempo:
        raise BlocoErro("O tratamento por período exige 'tratamento' e 'tempo' no mapeamento.")
    t = m.tratamento
    binds: dict[str, Any] = {}
    g = f"p.{ident(m.grupos[0])}"
    u = f"p.{ident(m.unidade)}"
    per = trunc(f"p.{ident(m.tempo)}", m.granularidade)
    limite = {"mes": f"ADD_MONTHS({per}, 1)", "semana": f"{per} + 7", "dia": f"{per} + 1"}[m.granularidade]
    cond = _janela(m, f"p.{ident(m.tempo)}", binds) + _filtros(m.filtros, "p", binds)
    sql = (
        f"SELECT {g} AS grupo, {per} AS periodo, COUNT(DISTINCT {u}) AS unidades, "
        f"COUNT(DISTINCT CASE WHEN tr.{ident(t.coluna_data)} < {limite} THEN {u} END) AS tratadas "
        f"FROM {ident(m.populacao)} p LEFT JOIN {ident(t.tabela)} tr ON tr.{ident(t.chave_unidade)} = {u} "
        f"{_where(cond)} GROUP BY {g}, {per}"
    )
    return sql, binds


def codigos_antes_depois(m: Mapeamento, d: FonteDesfecho, *, corte: str, grupos: list[Any] | None = None) -> tuple[str, dict[str, Any]]:
    """Contagem dos códigos do desfecho antes × depois do corte (células grandes, mais poder).

    Com ``grupos`` (ex.: cidades que nunca receberam a ação), conta só nesses grupos:
    lá a composição não muda por causa da ação, então mudança = mudança de registro.
    Só os códigos incluídos no desfecho entram (senão o próprio efeito parece mudança).
    """
    if not d.coluna_codigo:
        raise BlocoErro(f"O desfecho '{d.nome}' não tem coluna de código.")
    binds: dict[str, Any] = {"corte": corte}
    data_e = f"e.{ident(d.coluna_data)}"
    cond = _janela(m, data_e, binds)
    if d.codigos:
        nomes = []
        for j, c in enumerate(d.codigos):
            binds[f"c{j}"] = c
            nomes.append(f":c{j}")
        cond.append(f"e.{ident(d.coluna_codigo)} IN ({', '.join(nomes)})")
    juncao = ""
    if grupos and m.grupos:
        nomes_g = []
        for j, g in enumerate(grupos):
            binds[f"g{j}"] = g
            nomes_g.append(f":g{j}")
        juncao = f" JOIN {ident(m.populacao)} p ON p.{ident(m.unidade)} = e.{ident(d.chave_unidade)}"
        if m.tempo:
            juncao += f" AND {trunc('p.' + ident(m.tempo), m.granularidade)} = {trunc(data_e, m.granularidade)}"
        cond.append(f"p.{ident(m.grupos[0])} IN ({', '.join(nomes_g)})")
    janela = f"CASE WHEN {data_e} < TO_DATE(:corte, 'YYYY-MM-DD') THEN 'antes' ELSE 'depois' END"
    cod = f"e.{ident(d.coluna_codigo)}"
    sql = (f"SELECT {cod} AS codigo, {janela} AS janela, COUNT(*) AS n FROM {ident(d.tabela)} e{juncao} {_where(cond)} "
           f"GROUP BY {cod}, {janela}")
    return sql, binds


def perfil_tabela(tabela: str, colunas: list[str]) -> str:
    """Uma linha com COUNT(*), COUNT(col) e COUNT(DISTINCT col) por coluna."""
    partes = ["COUNT(*) AS linhas"]
    for i, c in enumerate(colunas):
        partes.append(f"COUNT(t.{ident(c)}) AS nn_{i}")
        partes.append(f"COUNT(DISTINCT t.{ident(c)}) AS dist_{i}")
    return f"SELECT {', '.join(partes)} FROM {ident(tabela)} t"


def contagem_ligacao(de_tabela: str, de_coluna: str, para_tabela: str, para_coluna: str) -> str:
    """Quantas linhas de 'de' acham par em 'para' (só contagens)."""
    return (
        f"SELECT COUNT(*) AS linhas, COUNT(b.{ident(para_coluna)}) AS com_par "
        f"FROM {ident(de_tabela)} a LEFT JOIN "
        f"(SELECT DISTINCT x.{ident(para_coluna)} FROM {ident(para_tabela)} x) b "
        f"ON b.{ident(para_coluna)} = a.{ident(de_coluna)}"
    )


def distribuicao(tabela: str, coluna: str, filtros: list[FiltroSimples] | None = None) -> tuple[str, dict[str, Any]]:
    binds: dict[str, Any] = {}
    cond = _filtros(filtros or [], "t", binds)
    c = f"t.{ident(coluna)}"
    return f"SELECT {c} AS categoria, COUNT(*) AS n FROM {ident(tabela)} t {_where(cond)} GROUP BY {c}", binds


def amostra_unidades(
    m: Mapeamento, *, percentual: float, seed: int, colunas: list[str], referencia: str | None = None,
    estrato: tuple[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Amostra Bernoulli reprodutível (SAMPLE ... SEED) da população, numa data de referência."""
    if not 0 < percentual <= 100:
        raise BlocoErro("percentual de amostra precisa estar entre 0 e 100")
    binds: dict[str, Any] = {}
    cols = [f"p.{ident(m.unidade)}"] + [f"p.{ident(c)}" for c in colunas if c.upper() != m.unidade.upper()]
    cond = _filtros(m.filtros, "p", binds)
    if referencia and m.tempo:
        binds["ref"] = referencia
        cond.append(f"{trunc('p.' + ident(m.tempo), m.granularidade)} = TO_DATE(:ref, 'YYYY-MM-DD')")
    if estrato:
        binds["estrato"] = estrato[1]
        cond.append(f"p.{ident(estrato[0])} = :estrato")
    pct = f"{percentual:.6f}".rstrip("0").rstrip(".")
    return (
        f"SELECT {', '.join(cols)} FROM {ident(m.populacao)} SAMPLE ({pct}) SEED ({int(seed)}) p {_where(cond)}",
        binds,
    )


def _cte_amostra(m: Mapeamento, *, percentual: float, seed: int, colunas: list[str], referencia: str | None,
                 binds: dict[str, Any]) -> tuple[str, list[str]]:
    cols = [ident(m.unidade)] + [ident(c) for c in colunas if c.upper() != m.unidade.upper()]
    cond = _filtros(m.filtros, "p", binds)
    if referencia and m.tempo:
        binds["ref"] = referencia
        cond.append(f"{trunc('p.' + ident(m.tempo), m.granularidade)} = TO_DATE(:ref, 'YYYY-MM-DD')")
    pct = f"{percentual:.6f}".rstrip("0").rstrip(".")
    cte = (f"WITH s AS (SELECT {', '.join('p.' + c for c in cols)} FROM {ident(m.populacao)} SAMPLE ({pct}) "
           f"SEED ({int(seed)}) p {_where(cond)})")
    return cte, cols


def amostra_com_alvo(m: Mapeamento, *, percentual: float, seed: int, referencia: str | None,
                     janela_meses: int = 3) -> tuple[str, dict[str, Any]]:
    """Unidades sorteadas + alvo (1/0 ou soma) olhando a janela DEPOIS da referência."""
    if m.alvo is None:
        raise BlocoErro("O mapeamento não tem alvo.")
    a = m.alvo
    binds: dict[str, Any] = {}
    cte, cols = _cte_amostra(m, percentual=percentual, seed=seed, colunas=[], referencia=referencia, binds=binds)
    u = ident(m.unidade)
    junta = [f"a.{ident(a.chave_unidade)} = s.{u}"]
    if a.coluna_data and referencia:
        binds["ref_alvo"] = referencia
        junta.append(f"a.{ident(a.coluna_data)} >= TO_DATE(:ref_alvo, 'YYYY-MM-DD')")
        junta.append(f"a.{ident(a.coluna_data)} < ADD_MONTHS(TO_DATE(:ref_alvo, 'YYYY-MM-DD'), {int(janela_meses)})")
    if a.tipo == "binario":
        if a.valor_positivo is not None:
            binds["positivo"] = a.valor_positivo
            medida = f"MAX(CASE WHEN a.{ident(a.coluna)} = :positivo THEN 1 ELSE 0 END)"
        else:
            medida = f"MAX(CASE WHEN a.{ident(a.chave_unidade)} IS NOT NULL THEN 1 ELSE 0 END)"
    else:
        medida = f"SUM(a.{ident(a.coluna)})"
    sql = (f"{cte} SELECT s.{u}, {medida} AS alvo FROM s LEFT JOIN {ident(a.tabela)} a ON {' AND '.join(junta)} "
           f"GROUP BY s.{u}")
    return sql, binds


def amostra_com_eventos(m: Mapeamento, d: FonteDesfecho, *, percentual: float, seed: int, referencia: str,
                        janela_meses: int = 3) -> tuple[str, dict[str, Any]]:
    """Unidades sorteadas + contagem de eventos na janela ANTES da referência (atributo sem vazamento)."""
    binds: dict[str, Any] = {"ref_ev": referencia}
    cte, _ = _cte_amostra(m, percentual=percentual, seed=seed, colunas=[], referencia=referencia, binds=binds)
    u = ident(m.unidade)
    data = f"e.{ident(d.coluna_data)}"
    junta = [f"e.{ident(d.chave_unidade)} = s.{u}",
             f"{data} < TO_DATE(:ref_ev, 'YYYY-MM-DD')",
             f"{data} >= ADD_MONTHS(TO_DATE(:ref_ev, 'YYYY-MM-DD'), -{int(janela_meses)})"]
    if d.coluna_codigo and d.codigos:
        nomes = []
        for j, c in enumerate(d.codigos):
            binds[f"c{j}"] = c
            nomes.append(f":c{j}")
        junta.append(f"e.{ident(d.coluna_codigo)} IN ({', '.join(nomes)})")
    sql = (f"{cte} SELECT s.{u}, COUNT(e.{ident(d.chave_unidade)}) AS ev_{ident(d.nome).lower()} FROM s "
           f"LEFT JOIN {ident(d.tabela)} e ON {' AND '.join(junta)} GROUP BY s.{u}")
    return sql, binds
