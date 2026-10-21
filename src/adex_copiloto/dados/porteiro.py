"""Porteiro de SQL: nenhuma consulta chega ao banco sem passar por aqui.

Regras fixas (governança, não são parâmetro):

1. **Uma consulta, só leitura.** Exatamente um SELECT (ou WITH ... SELECT).
   DML, DDL, PL/SQL, ``FOR UPDATE``, flashback, ``SELECT INTO`` e db links são recusados.
2. **Lista de permissão de construções.** Só passam nós de SQL conhecidos e
   seguros (o sqlglot transforma, por exemplo, ``DBMS_RANDOM.VALUE()`` num nó
   tipado; uma lista de proibidos deixaria passar). Dicas ``/*+ ... */`` são removidas.
3. **Nomes conferidos no catálogo.** Toda tabela e toda coluna precisam existir
   (é a defesa contra nome inventado pela IA) e a tabela precisa estar acessível.
4. **Dado pessoal não sai.** Colunas bloqueadas só podem ser o argumento direto de
   ``COUNT``/``COUNT(DISTINCT)`` (também como resultado de um ``CASE`` contado) ou
   um dos lados de uma igualdade entre colunas (junção). Nunca em filtro com valor,
   ``CASE WHEN``, ``MIN``/``MAX`` ou outra função.
5. **Parametrizado.** Literais em comparações viram binds automaticamente; nenhum
   literal ou bind, em qualquer lugar da consulta (nem montado por ``||``), pode
   parecer dado pessoal (CPF, e-mail...).
6. **Modo agregado** (tudo o que a IA vê): a saída precisa ser agregada e cada
   grupo precisa ter pelo menos ``min_group_size`` linhas (e unidades distintas,
   quando a unidade é conhecida) — o porteiro acrescenta o ``HAVING``.
   Identificadores (cliente, domicílio) seguem a regra 4, com uma folga: dentro
   de CTE ou subconsulta podem ser repassados, agrupados ou particionados (sem
   transformação), porque a saída sempre passa pelo grupo mínimo. Subconsultas
   que leem tabelas de pessoas: no filtro, só por chave do indivíduo
   (``id IN (SELECT id ...)`` ou ``EXISTS`` correlacionado pelo id) ou agregadas
   (ganham o grupo mínimo); na lista do SELECT, só agregadas.
7. **Teto de linhas** sempre aplicado; custo do plano conferido pela fonte
   (``EXPLAIN PLAN`` no ADW) antes de executar.
"""

from __future__ import annotations

import datetime as dt
import decimal
import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import sqlglot
from sqlglot import ErrorLevel, exp
from sqlglot.errors import OptimizeError, ParseError, UnsupportedError
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import traverse_scope

from adex_copiloto.dados.catalogo import Catalogo
from adex_copiloto.erros import PorteiroRecusouErro
from adex_copiloto.seguranca.redacao import contem_pii, _cpf_valido

DIALETO_CANONICO = "oracle"
MAX_TAMANHO_SQL = 100_000
PREFIXO_RESERVADO = "adex_"


class Modo(str, Enum):
    AGREGADO = "agregado"  # resultado pode ir para a IA
    DETALHE = "detalhe"  # linhas ficam no kernel (amostra aprovada); nunca vão para a IA


def _classes(*nomes: str) -> tuple[type, ...]:
    return tuple(c for n in nomes if (c := getattr(exp, n, None)) is not None)


# Nós estruturais e operadores aceitos.
_ESTRUTURA = _classes(
    "Select", "Union", "Intersect", "Except", "Subquery", "CTE", "With", "From", "Join", "Where", "Group",
    "Having", "Order", "Ordered", "Limit", "Fetch", "Offset", "LimitOptions", "Alias", "TableAlias", "Table",
    "Column", "Identifier", "Star", "Dot", "Paren", "Tuple", "Literal", "Placeholder", "Null", "Boolean",
    "Distinct", "Window", "WindowSpec", "Interval", "DataType", "DataTypeParam", "Cast", "TryCast", "Case", "If",
    "Var", "Between", "In", "Like", "ILike", "Is", "Not", "And", "Or", "EQ", "NEQ", "GT", "GTE", "LT", "LTE",
    "Add", "Sub", "Mul", "Div", "IntDiv", "Mod", "Neg", "Concat", "DPipe", "Exists", "Any", "All", "TableSample",
    "Filter", "WithinGroup", "Nullif", "Coalesce", "IgnoreNulls", "RespectNulls", "Escape", "Kwarg",
)
# Funções aceitas (agregadas, janelas e escalares comuns).
_FUNCOES = _classes(
    "Count", "Sum", "Avg", "Min", "Max", "Stddev", "StddevPop", "StddevSamp", "Variance", "VariancePop", "Median",
    "PercentileCont", "PercentileDisc", "Quantile", "ApproxDistinct", "ApproxQuantile", "CountIf", "Corr",
    "CovarPop", "CovarSamp", "RegrSlope", "RegrIntercept", "RegrR2", "RegrCount", "Mode", "Skewness", "Kurtosis",
    "RowNumber", "Rank", "DenseRank", "Lag", "Lead", "FirstValue", "LastValue", "NthValue", "Ntile", "PercentRank",
    "CumeDist", "Grouping",
    "Abs", "Round", "Floor", "Ceil", "Pow", "Sqrt", "Ln", "Log", "Exp", "Sign", "Trunc", "Greatest", "Least",
    "Upper", "Lower", "Initcap", "Substring", "Length", "Trim", "Pad", "Replace", "Translate", "StrPosition",
    "RegexpLike", "RegexpSubstr", "RegexpReplace", "RegexpCount", "RegexpInstr", "Left", "Right", "Concat", "ConcatWs",
    "DateTrunc", "TimestampTrunc", "DatetimeTrunc", "TimeTrunc", "DateStrToDate", "StrToDate", "StrToTime",
    "TimeToStr", "TimeStrToTime", "TsOrDsToDate", "ToChar", "ToNumber", "Extract", "CurrentDate",
    "CurrentTimestamp", "Systimestamp", "AddMonths", "MonthsBetween", "LastDay", "NextDay", "DateAdd", "DateSub",
    "DateDiff", "TimestampAdd", "TimestampSub", "TimestampDiff", "Year", "Month", "Quarter", "Week", "Day",
    "DayOfWeek", "DayOfMonth", "DayOfYear", "Date", "Decode", "DecodeCase", "Nvl2", "WidthBucket",
)
# Nunca: travas, flashback, SELECT INTO, comandos, DML/DDL, geração de dados, leitura de arquivos.
_PROIBIDAS = _classes(
    "Lock", "Version", "Into", "Command", "Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter",
    "Transaction", "Commit", "Rollback", "Set", "Use", "Grant", "Revoke", "Copy", "LoadData", "Pragma",
    "Rand", "Randn", "Uuid", "ReadCSV", "ReadParquet", "Connect", "Prior", "Lateral", "Unnest", "Values",
    "Pivot", "Unpivot", "Changes", "Parameter", "SessionParameter", "UserDefinedFunction", "StoredProcedure",
    "Secret", "CurrentUser", "SessionUser", "CurrentSchema", "CurrentDatabase",
)
_FUNCOES_ANONIMAS = frozenset(
    {
        "NVL", "NVL2", "NULLIF", "DECODE", "TO_DATE", "TO_CHAR", "TO_NUMBER", "TO_TIMESTAMP", "ADD_MONTHS",
        "MONTHS_BETWEEN", "LAST_DAY", "NEXT_DAY", "TRUNC", "ROUND", "NUMTODSINTERVAL", "NUMTOYMINTERVAL", "INSTR",
        "LPAD", "RPAD", "LTRIM", "RTRIM", "SUBSTR", "REGEXP_LIKE", "REGEXP_SUBSTR", "REGEXP_COUNT", "SIGN", "MOD",
        "RATIO_TO_REPORT", "APPROX_COUNT_DISTINCT", "APPROX_MEDIAN", "APPROX_PERCENTILE", "STATS_MODE",
        "CORR", "COVAR_POP", "COVAR_SAMP", "BITAND", "WIDTH_BUCKET", "ISOWEEK", "TO_DSINTERVAL", "TO_YMINTERVAL",
    }
)
# Agregações que expõem valores individuais (listas): recusadas no modo agregado.
_AGREGA_LISTA = _classes("GroupConcat", "ArrayAgg", "ArrayConcatAgg", "JSONArrayAgg", "JSONObjectAgg", "ObjectAgg", "List")
_COMPARACOES = _classes("EQ", "NEQ", "GT", "GTE", "LT", "LTE", "Like", "ILike")


@dataclass
class Veredito:
    aprovado: bool
    motivos: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    sql: str = ""  # no dialeto da fonte
    sql_canonico: str = ""  # Oracle, para o dossiê e o handoff
    binds: dict[str, Any] = field(default_factory=dict)
    tabelas: list[str] = field(default_factory=list)
    colunas: list[str] = field(default_factory=list)
    modo: str = Modo.AGREGADO.value
    dialeto: str = DIALETO_CANONICO
    sha256: str = ""

    def exigir(self) -> "Veredito":
        if not self.aprovado:
            raise PorteiroRecusouErro(
                "O porteiro recusou a consulta:\n- " + "\n- ".join(self.motivos),
                detalhes={"motivos": self.motivos, "sha256": self.sha256},
            )
        return self

    def para_dict(self) -> dict[str, Any]:
        return {
            "aprovado": self.aprovado,
            "motivos": self.motivos,
            "avisos": self.avisos,
            "sql": self.sql_canonico,
            "binds": {k: _valor_json(v) for k, v in self.binds.items()},
            "tabelas": self.tabelas,
            "modo": self.modo,
            "sha256": self.sha256,
        }


def _valor_json(v: Any) -> Any:
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return float(v)
    return v


def _recusa(motivo: str, **extra) -> Veredito:
    return Veredito(aprovado=False, motivos=[motivo], **extra)


_ESCALARES = (str, int, float, bool, dt.date, dt.datetime, decimal.Decimal, type(None))


def _parece_pessoal(valor: Any) -> bool:
    if isinstance(valor, str):
        if contem_pii(valor):
            return True
        digitos = re.sub(r"\D", "", valor)
        return len(digitos) == 11 and _cpf_valido(digitos)
    if isinstance(valor, int) and not isinstance(valor, bool):
        s = str(valor)
        return len(s) == 11 and _cpf_valido(s)
    return False


class Porteiro:
    def __init__(
        self,
        catalogo: Catalogo,
        *,
        min_grupo: int = 50,
        max_linhas: int = 100_000,
        max_linhas_detalhe: int = 2_000_000,
        funcoes_extras: list[str] | None = None,
    ) -> None:
        self.catalogo = catalogo
        self.min_grupo = int(min_grupo)
        self.max_linhas = int(max_linhas)
        self.max_linhas_detalhe = int(max_linhas_detalhe)
        self.funcoes_anonimas = _FUNCOES_ANONIMAS | {f.upper() for f in (funcoes_extras or [])}

    # --- API ------------------------------------------------------------------------------

    def avaliar(
        self,
        sql: str,
        binds: dict[str, Any] | None = None,
        *,
        modo: Modo | str = Modo.AGREGADO,
        coluna_unidade: str | None = None,
        dialeto: str = DIALETO_CANONICO,
        max_linhas: int | None = None,
    ) -> Veredito:
        modo = Modo(modo)
        binds = dict(binds or {})
        try:
            return self._avaliar(sql, binds, modo, coluna_unidade, dialeto, max_linhas)
        except PorteiroRecusouErro:
            raise
        except Exception as e:  # noqa: BLE001 - qualquer surpresa é recusa (falha fechada)
            return _recusa(f"o porteiro não conseguiu analisar a consulta com segurança ({type(e).__name__}).")

    def _avaliar(self, sql, binds, modo, coluna_unidade, dialeto, max_linhas) -> Veredito:
        texto = (sql or "").strip().rstrip(";").strip()
        if not texto:
            return _recusa("consulta vazia.")
        if len(texto) > MAX_TAMANHO_SQL:
            return _recusa(f"consulta grande demais ({len(texto)} caracteres).")
        try:
            arvores = [a for a in sqlglot.parse(texto, read=DIALETO_CANONICO, error_level=ErrorLevel.RAISE) if a is not None]
        except ParseError as e:
            return _recusa(f"não entendi a consulta (sintaxe): {str(e).splitlines()[0][:200]}")
        if len(arvores) != 1:
            return _recusa("só uma consulta por vez (encontrei mais de um comando).")
        arvore = arvores[0]
        if not isinstance(arvore, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
            return _recusa(f"só consultas de leitura (SELECT) são aceitas; recebi {type(arvore).__name__.upper()}.")

        avisos: list[str] = []
        motivos: list[str] = []

        # 1) Lista de permissão de construções.
        hints = list(arvore.find_all(exp.Hint))
        for h in hints:
            h.pop()
        if hints:
            avisos.append("dicas de otimização (/*+ ... */) foram removidas.")
        for no in arvore.walk():
            if isinstance(no, _PROIBIDAS):
                motivos.append(f"construção proibida: {type(no).__name__} ({_trecho(no)}).")
            elif isinstance(no, exp.Anonymous):
                nome = (no.name or "").upper()
                if nome not in self.funcoes_anonimas:
                    motivos.append(f"função não permitida: {nome or _trecho(no)}.")
            elif isinstance(no, exp.Func) and not isinstance(no, _FUNCOES + _ESTRUTURA + _AGREGA_LISTA):
                motivos.append(f"função não permitida: {type(no).__name__} ({_trecho(no)}).")
            elif not isinstance(no, (exp.Func, *_ESTRUTURA, *_AGREGA_LISTA)):
                motivos.append(f"construção não permitida: {type(no).__name__} ({_trecho(no)}).")
            if isinstance(no, exp.TableSample) and not isinstance(no.parent, exp.Table):
                motivos.append("SAMPLE precisa vir logo depois do nome da tabela (FROM esquema.tabela SAMPLE (10) SEED (42) apelido).")
            if isinstance(no, exp.Identifier) and "@" in (no.name or ""):
                motivos.append(f"db link não é permitido ({no.name}).")
            if modo == Modo.AGREGADO and isinstance(no, _AGREGA_LISTA):
                motivos.append(f"{type(no).__name__} junta valores individuais e não é aceito no modo agregado.")
        if motivos:
            return Veredito(False, _unicos(motivos), avisos)

        # 2) Tabelas existem no catálogo e estão acessíveis.
        ctes = {c.alias_or_name.upper() for c in arvore.find_all(exp.CTE)}
        tabelas: dict[str, str] = {}  # alias -> nome completo
        for t in arvore.find_all(exp.Table):
            nome = t.name.upper()
            esquema = (t.db or "").upper()
            if not esquema and nome in ctes:
                continue
            if t.catalog:
                motivos.append(f"referência entre bancos não é permitida ({t.sql()}).")
                continue
            ficha = self.catalogo.resolver(f"{esquema}.{nome}" if esquema else nome)
            if ficha is None:
                motivos.append(f"tabela não existe no catálogo: {esquema + '.' if esquema else ''}{nome}.")
                continue
            if ficha.acesso != "ok":
                motivos.append(f"sem acesso a {ficha.nome_completo}: peça acesso ao dono da tabela.")
                continue
            if not esquema:
                t.set("db", exp.to_identifier(ficha.esquema))
            tabelas[(t.alias_or_name or nome).upper()] = ficha.nome_completo
        if motivos:
            return Veredito(False, _unicos(motivos), avisos)
        if not tabelas:
            return Veredito(False, ["a consulta não lê nenhuma tabela do catálogo."], avisos)

        # 3) Colunas existem (qualify com o esquema do catálogo).
        try:
            arvore = qualify(
                arvore,
                schema=self.catalogo.esquema_sqlglot(set(tabelas.values())),
                dialect=DIALETO_CANONICO,
                validate_qualify_columns=True,
                quote_identifiers=False,
                identify=False,
            )
        except OptimizeError as e:
            msg = str(e).split(". Line")[0]
            m = re.search(r"Column '([^']+)' could not be resolved", msg)
            if m:
                return Veredito(False, [f"coluna não existe nas tabelas consultadas: {m.group(1)}."], avisos)
            return Veredito(False, [f"não consegui conferir as colunas: {msg[:200]}."], avisos)
        # 4) Dado pessoal não sai. Cada coluna é resolvida no seu escopo (CTE, subconsulta),
        #    para que apelidos repetidos em escopos diferentes não confundam a origem.
        colunas_usadas: set[str] = set()
        tabelas = {}
        for escopo in traverse_scope(arvore):
            for alias, fonte in escopo.sources.items():
                if isinstance(fonte, exp.Table) and escopo.is_root:
                    tabelas[alias.upper()] = f"{fonte.db}.{fonte.name}".upper()
            for c in escopo.columns:
                fonte = _fonte_da_coluna(escopo, c)
                if not isinstance(fonte, exp.Table):
                    continue  # vem de CTE/subconsulta: conferida no escopo de origem
                nome_tab = f"{fonte.db}.{fonte.name}".upper()
                colunas_usadas.add(f"{nome_tab}.{c.name.upper()}")
                ficha_col = self.catalogo.coluna(nome_tab, c.name)
                if ficha_col is None or not ficha_col.bloqueada:
                    continue
                if _em_contagem(c) or _em_igualdade_de_colunas(c) or _em_teste_de_nulo(c):
                    continue
                motivos.append(
                    f"{nome_tab}.{ficha_col.nome} é dado pessoal ({ficha_col.motivo_pii or ficha_col.pii}) e só pode "
                    "aparecer direto em COUNT/COUNT(DISTINCT) ou numa junção por igualdade entre colunas "
                    "(nunca em filtro com valor, CASE WHEN, MIN/MAX ou outra função)."
                )
        if motivos:
            return Veredito(False, _unicos(motivos), avisos)

        # 5) Binds: literais em comparação viram bind; nada que pareça dado pessoal.
        motivos.extend(self._parametrizar(arvore, binds, avisos))
        if motivos:
            return Veredito(False, _unicos(motivos), avisos)

        # 6) Regras do modo.
        if modo == Modo.AGREGADO:
            motivos.extend(self._exigir_agregado(arvore))
            if motivos:
                return Veredito(False, _unicos(motivos), avisos)
            saidas = self._saidas_individuais(arvore)
            motivos.extend(self._conferir_individuos(arvore, saidas))
            problemas_sub, subconsultas_a_proteger = self._conferir_subconsultas(arvore, saidas)
            motivos.extend(problemas_sub)
            if motivos:
                return Veredito(False, _unicos(motivos), avisos)
            binds["adex_min_grupo"] = self.min_grupo
            avisos.extend(self._proteger_escopos(arvore, coluna_unidade, subconsultas_a_proteger))

        # 7) Teto de linhas (resultado agregado ou amostra aprovada).
        base = self.max_linhas if modo == Modo.AGREGADO else self.max_linhas_detalhe
        teto = min(base, max_linhas or base)
        arvore = self._aplicar_teto(arvore, teto, avisos)

        canonico = arvore.sql(dialect=DIALETO_CANONICO)
        try:
            final = (
                canonico
                if dialeto == DIALETO_CANONICO
                else _ajustar_dialeto(arvore.copy(), dialeto).sql(dialect=dialeto, unsupported_level=ErrorLevel.RAISE)
            )
        except UnsupportedError as e:
            return Veredito(False, [f"a consulta usa algo que a fonte '{dialeto}' não suporta: {e}"], avisos)
        sha = hashlib.sha256(
            (canonico + json.dumps({k: _valor_json(v) for k, v in sorted(binds.items())}, default=str)).encode("utf-8")
        ).hexdigest()
        return Veredito(
            aprovado=True,
            avisos=avisos,
            sql=final,
            sql_canonico=canonico,
            binds=binds,
            tabelas=sorted(set(tabelas.values())),
            colunas=sorted(colunas_usadas),
            modo=modo.value,
            dialeto=dialeto,
            sha256=sha,
        )

    # --- regras auxiliares ---------------------------------------------------------------------

    def _parametrizar(self, arvore: exp.Expression, binds: dict[str, Any], avisos: list[str]) -> list[str]:
        motivos: list[str] = []
        for nome, valor in binds.items():
            if nome.lower().startswith(PREFIXO_RESERVADO):
                motivos.append(f"o nome de bind :{nome} é reservado.")
            elif not isinstance(valor, _ESCALARES):
                motivos.append(f"o bind :{nome} precisa ser um valor simples (texto, número ou data).")
            elif _parece_pessoal(valor):
                motivos.append(f"o valor de :{nome} parece um dado pessoal; o copiloto não filtra indivíduos.")
        # Em qualquer lugar (CASE, funções, lista do SELECT), não só nos filtros; e também o
        # que é montado por concatenação de pedaços ('529.982' || '.247-25').
        for no in arvore.walk():
            if isinstance(no, exp.Literal):
                if _parece_pessoal(_valor_literal(no)):
                    motivos.append("um valor na consulta parece um dado pessoal; o copiloto não filtra indivíduos.")
            elif isinstance(no, _CONCATENACOES) and not isinstance(no.parent, _CONCATENACOES):
                texto = _dobrar_texto(no, binds)
                if texto is not None and _parece_pessoal(texto):
                    motivos.append("um valor montado na consulta parece um dado pessoal; o copiloto não filtra indivíduos.")
        if motivos:
            return motivos
        usados = {p.name for p in arvore.find_all(exp.Placeholder) if p.name}
        faltam = sorted(usados - set(binds))
        if faltam:
            motivos.append("faltam valores para: " + ", ".join(f":{n}" for n in faltam) + ".")
        if any(not p.name for p in arvore.find_all(exp.Placeholder)):
            motivos.append("use binds com nome (:nome), não '?'.")
        sobras = sorted(set(binds) - usados)
        if sobras:
            avisos.append("binds não usados: " + ", ".join(sobras) + ".")
        contador = 0
        for alvo in self._predicados(arvore):
            for lit in list(alvo.find_all(exp.Literal)):
                if not self._operando_de_comparacao(lit):
                    continue
                valor = _valor_literal(lit)
                if valor is None:
                    continue
                contador += 1
                nome = f"p{contador}"
                while nome in binds:
                    contador += 1
                    nome = f"p{contador}"
                binds[nome] = valor
                lit.replace(exp.Placeholder(this=nome))
        if contador:
            avisos.append(f"{contador} valor(es) fixo(s) viraram parâmetros (binds).")
        return motivos

    @staticmethod
    def _predicados(arvore: exp.Expression):
        for no in arvore.walk():
            if isinstance(no, (exp.Where, exp.Having)):
                yield no
            elif isinstance(no, exp.Join) and no.args.get("on") is not None:
                yield no.args["on"]

    @staticmethod
    def _operando_de_comparacao(lit: exp.Literal) -> bool:
        pai = lit.parent
        if isinstance(pai, exp.Neg):
            pai = pai.parent
        if isinstance(pai, _COMPARACOES):
            return True
        if isinstance(pai, exp.In) and lit in (pai.expressions or []):
            return True
        if isinstance(pai, exp.Between) and lit is not pai.this:
            return True
        return False

    @staticmethod
    def _exigir_agregado(arvore: exp.Expression) -> list[str]:
        if not isinstance(arvore, exp.Select):
            return ["no modo agregado, use uma única consulta agregada (UNION/INTERSECT/EXCEPT não são aceitos)."]
        if arvore.args.get("distinct") and not arvore.find(exp.AggFunc):
            return ["SELECT DISTINCT lista valores; no modo agregado, conte (COUNT) em vez de listar."]
        grupo = arvore.args.get("group")
        chaves = {g.sql(dialect=DIALETO_CANONICO) for g in (grupo.expressions if grupo else [])}
        colunas_grupo = {c.sql(dialect=DIALETO_CANONICO) for g in (grupo.expressions if grupo else []) for c in g.find_all(exp.Column)}
        problemas = []
        for proj in arvore.expressions:
            expr = proj.this if isinstance(proj, exp.Alias) else proj
            if isinstance(expr, (exp.Literal, exp.Placeholder, exp.Null)):
                continue
            if expr.find(exp.AggFunc):
                soltas = [
                    c for c in expr.find_all(exp.Column)
                    if not _dentro_de_agregado(c) and c.sql(dialect=DIALETO_CANONICO) not in colunas_grupo
                ]
                if soltas and grupo is None:
                    problemas.append(f"'{_trecho(proj)}' mistura coluna solta com agregação.")
                continue
            if grupo is None:
                problemas.append(
                    f"'{_trecho(proj)}' não está agregado: no modo agregado, use GROUP BY ou funções como COUNT/SUM."
                )
                continue
            if expr.sql(dialect=DIALETO_CANONICO) in chaves:
                continue
            soltas = [c for c in expr.find_all(exp.Column) if c.sql(dialect=DIALETO_CANONICO) not in colunas_grupo]
            if soltas:
                problemas.append(f"'{_trecho(proj)}' não está no GROUP BY nem dentro de uma agregação.")
        return problemas

    def _eh_individual(self, tabela: exp.Table, coluna: str) -> bool:
        ficha = self.catalogo.coluna(f"{tabela.db}.{tabela.name}", coluna)
        return bool(ficha and ficha.individual)

    def _eh_identificador(self, tabela: exp.Table, coluna: str) -> bool:
        ficha = self.catalogo.coluna(f"{tabela.db}.{tabela.name}", coluna)
        return bool(ficha and ficha.identificador)

    def _tabela_de_pessoas(self, tabela: exp.Table) -> bool:
        ficha = self.catalogo.resolver(f"{tabela.db}.{tabela.name}")
        return ficha is None or any(c.individual for c in ficha.colunas)

    def _coluna_individual(self, escopo, c: exp.Column, saidas: dict[int, list[tuple[str, bool]]]) -> bool:
        """A coluna identifica um indivíduo? Segue CTEs e subconsultas até a tabela de origem."""
        fonte = _fonte_da_coluna(escopo, c)
        if fonte is None:
            return False  # apelido da própria lista do SELECT, não coluna de tabela
        if isinstance(fonte, exp.Table):
            return self._eh_individual(fonte, c.name)
        saida = saidas.get(id(_expressao_da_fonte(fonte)))
        if saida is None:
            return True  # origem desconhecida: falha fechada
        return any(ind for nome, ind in saida if nome == c.name.upper())

    def _saidas_individuais(self, arvore: exp.Expression) -> dict[int, list[tuple[str, bool]]]:
        """Para cada escopo, quais colunas da sua saída repassam um identificador de indivíduo."""
        saidas: dict[int, list[tuple[str, bool]]] = {}
        for escopo in traverse_scope(arvore):
            sel = escopo.expression
            if isinstance(sel, exp.Select):
                lista = []
                for p in sel.expressions:
                    # Só a coluna repassada sem transformação carrega o indivíduo: qualquer outro uso
                    # (CASE, MAX, função) é recusado em _conferir_individuos; janela particionada pelo
                    # id (ROW_NUMBER) e contagem não identificam ninguém.
                    expr = p.this if isinstance(p, exp.Alias) else p
                    ind = isinstance(expr, exp.Column) and self._coluna_individual(escopo, expr, saidas)
                    lista.append((p.alias_or_name.upper(), ind))
                saidas[id(sel)] = lista
            elif isinstance(sel, exp.SetOperation if hasattr(exp, "SetOperation") else (exp.Union, exp.Intersect, exp.Except)):
                esq = saidas.get(id(_sem_parenteses(sel.this)))
                dir_ = saidas.get(id(_sem_parenteses(sel.expression)))
                if esq is None or dir_ is None or len(esq) != len(dir_):
                    continue  # desconhecida: _coluna_individual falha fechada
                saidas[id(sel)] = [(n, a or b) for (n, a), (_, b) in zip(esq, dir_)]
        return saidas

    def _conferir_individuos(self, arvore: exp.Expression, saidas: dict[int, list[tuple[str, bool]]]) -> list[str]:
        """Identificador de indivíduo só em contagem, junção, IS NULL ou como chave interna."""
        problemas = []
        for escopo in traverse_scope(arvore):
            for c in escopo.columns:
                if not self._coluna_individual(escopo, c, saidas):
                    continue
                pai = c.parent
                if _em_contagem(c) or _em_igualdade_de_colunas(c) or _em_teste_de_nulo(c):
                    continue
                if isinstance(pai, exp.In) and pai.this is c and pai.args.get("query") is not None:
                    continue  # semijunção: a subconsulta é conferida em _conferir_subconsultas
                sel = c.find_ancestor(exp.Select)
                na_saida = sel is arvore and (
                    isinstance(pai, exp.Group) or pai is sel
                    or (isinstance(pai, exp.Alias) and pai.parent is sel)
                )
                if sel is not arvore and sel is not None and _chave_interna(c, sel):
                    continue
                if na_saida:
                    problemas.append(
                        f"agrupar ou devolver {c.sql(dialect=DIALETO_CANONICO)} daria um resultado por cliente "
                        "ou domicílio; no modo agregado, agrupe por algo maior (cidade, mês, segmento)."
                    )
                else:
                    problemas.append(
                        f"{c.sql(dialect=DIALETO_CANONICO)} identifica cliente ou domicílio: no modo agregado, só pode "
                        f"aparecer em COUNT/COUNT(DISTINCT), em junção por igualdade entre colunas, em IS NULL ou como "
                        f"chave dentro de CTE/subconsulta (aqui está em: {_trecho(pai)})."
                    )
        return problemas

    def _conferir_subconsultas(
        self, arvore: exp.Expression, saidas: dict[int, list[tuple[str, bool]]]
    ) -> tuple[list[str], set[int]]:
        """Subconsultas (IN, EXISTS, escalares) que leem tabelas de pessoas.

        Uma subconsulta de filtro decide quais linhas entram; se ela pudesse olhar uma
        pessoa só (por CEP, idade e renda, por exemplo), o resultado agregado entregaria
        um fato sobre essa pessoa. Por isso, sobre tabelas de pessoas:

        - filtro por chave do indivíduo (``id IN (SELECT id ...)`` ou ``EXISTS``
          correlacionado pelo id): passa, porque a saída já conta unidades distintas;
        - subconsulta agregada: passa e ganha o grupo mínimo (devolvida aqui);
        - qualquer outra: recusada.
        """
        problemas: list[str] = []
        proteger: set[int] = set()
        le_pessoas: dict[int, bool] = {}
        for escopo in traverse_scope(arvore):
            sel = escopo.expression
            pessoas = any(
                (self._tabela_de_pessoas(s) if isinstance(s, exp.Table) else le_pessoas.get(id(_expressao_da_fonte(s)), True))
                for s in escopo.sources.values()
            )
            if not isinstance(sel, exp.Select):
                le_pessoas[id(sel)] = pessoas or any(
                    le_pessoas.get(id(e), True) for e in (_sem_parenteses(sel.this), _sem_parenteses(sel.expression))
                )
                continue
            le_pessoas[id(sel)] = pessoas
            tipo = getattr(escopo, "scope_type", None)
            if tipo is None or tipo.name != "SUBQUERY" or escopo.parent is None or not pessoas:
                continue
            agrega = sel.args.get("group") is not None or any(p.find(exp.AggFunc) for p in sel.expressions)
            lugar = _lugar_no_externo(sel, escopo.parent.expression)
            if lugar in ("where", "having", "joins") and self._filtro_por_individuo(escopo, sel, saidas):
                continue
            if agrega:
                proteger.add(id(sel))
                continue
            if lugar in ("where", "having", "joins"):
                problemas.append(
                    f"a subconsulta '{_trecho(sel)}' filtra por algo que não é a chave do cliente/domicílio e lê "
                    "uma tabela de pessoas: no modo agregado, filtre por chave (id IN (SELECT id ...)), "
                    "use EXISTS correlacionado pelo id ou agregue a subconsulta."
                )
            else:
                problemas.append(
                    f"a subconsulta '{_trecho(sel)}' devolve valores de linhas de uma tabela de pessoas: no modo "
                    "agregado, agregue a subconsulta (COUNT, SUM...) ou use uma junção com uma CTE agregada."
                )
        return problemas, proteger

    def _filtro_por_individuo(self, escopo, sel: exp.Select, saidas: dict[int, list[tuple[str, bool]]]) -> bool:
        externo = escopo.parent
        no = sel.parent
        if isinstance(no, exp.Subquery) and isinstance(no.parent, exp.In) and no.parent.args.get("query") is no:
            alvo = no.parent.this
            saida = saidas.get(id(sel)) or []
            return (
                isinstance(alvo, exp.Column)
                and self._coluna_individual(externo, alvo, saidas)
                and len(saida) == 1
                and saida[0][1]
            )
        if isinstance(no, exp.Exists):
            for eq in sel.find_all(exp.EQ):
                if eq.find_ancestor(exp.Select) is not sel:
                    continue
                a, b = eq.this, eq.expression
                if not (isinstance(a, exp.Column) and isinstance(b, exp.Column) and _em_igualdade_de_colunas(a)):
                    continue
                for dentro, fora in ((a, b), (b, a)):
                    if (
                        dentro.table in escopo.sources
                        and fora.table not in escopo.sources
                        and self._coluna_individual(escopo, dentro, saidas)
                        and self._coluna_individual(externo, fora, saidas)
                    ):
                        return True
        return False

    def _proteger_escopos(self, arvore: exp.Expression, coluna_unidade: str | None, subconsultas: set[int] | None = None) -> list[str]:
        """Põe o grupo mínimo em todo escopo que agrega linhas de tabela de verdade.

        Escopos que só leem resultados já protegidos (CTE ou subconsulta agregada)
        não precisam de regra nova: cada grupo de dentro já tem o mínimo de unidades.
        Subconsultas (IN, EXISTS, escalares) só ganham a regra quando
        :meth:`_conferir_subconsultas` pede (agregadas sobre tabelas de pessoas).
        """
        subconsultas = subconsultas or set()
        avisos: list[str] = []
        protegido: dict[int, bool] = {}
        ids_saida: dict[int, set[str]] = {}
        for escopo in traverse_scope(arvore):
            sel = escopo.expression
            tipo = getattr(escopo, "scope_type", None)
            if tipo is not None and tipo.name == "SUBQUERY" and id(sel) not in subconsultas:
                continue  # filtro por chave do indivíduo, ou não lê tabela de pessoas
            if not isinstance(sel, exp.Select):
                protegido[id(sel)] = False
                continue
            bases = {a: s for a, s in escopo.sources.items() if isinstance(s, exp.Table)}
            derivadas = {a: s for a, s in escopo.sources.items() if not isinstance(s, exp.Table)}
            agrega = sel.args.get("group") is not None or any(p.find(exp.AggFunc) for p in sel.expressions)
            derivadas_ok = all(protegido.get(id(getattr(d, "expression", d)), False) for d in derivadas.values())
            if agrega:
                if bases or not derivadas_ok:
                    unidade = self._unidade(escopo, bases, derivadas, ids_saida, coluna_unidade)
                    condicao: exp.Expression = exp.GTE(
                        this=exp.Count(this=exp.Star()), expression=exp.Placeholder(this="adex_min_grupo")
                    )
                    if unidade is not None:
                        condicao = exp.and_(
                            condicao,
                            exp.GTE(
                                this=exp.Count(this=exp.Distinct(expressions=[unidade])),
                                expression=exp.Placeholder(this="adex_min_grupo"),
                            ),
                        )
                    else:
                        avisos.append(
                            "não achei o identificador da unidade (cliente, domicílio...) num dos agrupamentos; "
                            "o grupo mínimo foi aplicado sobre linhas."
                        )
                    sel.having(condicao, append=True, copy=False)
                protegido[id(sel)] = True
                ids_saida[id(sel)] = set()
            else:
                ids: set[str] = set()
                for p in sel.expressions:
                    col = p.this if isinstance(p, exp.Alias) else p
                    if not isinstance(col, exp.Column):
                        continue
                    fonte = escopo.sources.get(col.table)
                    if isinstance(fonte, exp.Table) and self._eh_identificador(fonte, col.name):
                        ids.add(p.alias_or_name.upper())
                    elif fonte is not None and not isinstance(fonte, exp.Table):
                        if col.name.upper() in ids_saida.get(id(getattr(fonte, "expression", fonte)), set()):
                            ids.add(p.alias_or_name.upper())
                ids_saida[id(sel)] = ids
                protegido[id(sel)] = not bases and derivadas_ok
        return avisos

    def _unidade(self, escopo, bases, derivadas, ids_saida, coluna_unidade) -> exp.Column | None:
        if coluna_unidade:
            col = sqlglot.parse_one(coluna_unidade, read=DIALETO_CANONICO, into=exp.Column)
            alias = (col.table or "").upper()
            nome = col.name.upper()
            for a, s in bases.items():
                if (not alias or a.upper() == alias) and self.catalogo.coluna(f"{s.db}.{s.name}", nome):
                    return exp.column(nome, table=a)
            for a, s in derivadas.items():
                if (not alias or a.upper() == alias) and nome in ids_saida.get(id(getattr(s, "expression", s)), set()):
                    return exp.column(nome, table=a)
        for a, s in bases.items():
            ficha = self.catalogo.resolver(f"{s.db}.{s.name}")
            if ficha:
                for c in ficha.colunas:
                    if c.identificador:
                        return exp.column(c.nome, table=a)
        for a, s in derivadas.items():
            ids = ids_saida.get(id(getattr(s, "expression", s)), set())
            if ids:
                return exp.column(sorted(ids)[0], table=a)
        return None

    @staticmethod
    def _aplicar_teto(arvore: exp.Expression, teto: int, avisos: list[str]) -> exp.Expression:
        limite = arvore.args.get("limit")
        atual = None
        if isinstance(limite, exp.Fetch):
            contagem = limite.args.get("count")
            atual = int(contagem.this) if isinstance(contagem, exp.Literal) and not contagem.is_string else None
        elif isinstance(limite, exp.Limit):
            expressao = limite.expression
            atual = int(expressao.this) if isinstance(expressao, exp.Literal) and not expressao.is_string else None
        if limite is not None and atual is not None and atual <= teto:
            return arvore
        if limite is not None and atual is None:
            avisos.append("o limite de linhas da consulta não é um número fixo; apliquei o teto do Admin.")
        elif limite is not None:
            avisos.append(f"o limite pedido ({atual}) passa do teto; apliquei {teto}.")
        if isinstance(arvore, exp.Select):
            return arvore.limit(teto, dialect=DIALETO_CANONICO)
        return exp.select("*").from_(arvore.subquery("adex_q")).limit(teto, dialect=DIALETO_CANONICO)


# Unidades de TRUNC do Oracle -> DATE_TRUNC de outros motores.
_UNIDADES_TRUNC = {
    "IW": "WEEK", "WW": "WEEK", "W": "WEEK", "DD": "DAY", "DDD": "DAY", "J": "DAY", "DAY": "DAY",
    "MM": "MONTH", "MON": "MONTH", "MONTH": "MONTH", "RM": "MONTH", "Q": "QUARTER",
    "YYYY": "YEAR", "YYY": "YEAR", "YY": "YEAR", "Y": "YEAR", "YEAR": "YEAR", "SYYYY": "YEAR",
    "HH": "HOUR", "HH12": "HOUR", "HH24": "HOUR", "MI": "MINUTE",
}


def _ajustar_dialeto(arvore: exp.Expression, dialeto: str) -> exp.Expression:
    """Equivalências de semântica Oracle -> motor local (DuckDB)."""
    if dialeto != "duckdb":
        return arvore
    for no in arvore.find_all(exp.DateTrunc, exp.TimestampTrunc):
        unidade = no.args.get("unit")
        nome = (unidade.name if unidade is not None else "").upper()
        if nome in _UNIDADES_TRUNC:
            no.set("unit", exp.Literal.string(_UNIDADES_TRUNC[nome]))
    for no in arvore.find_all(exp.TableSample):
        if not no.args.get("method"):
            no.set("method", exp.var("BERNOULLI"))  # o padrão do DuckDB amostra por blocos
    return arvore


_CONCATENACOES = _classes("DPipe", "Concat", "ConcatWs")


def _valor_literal(lit: exp.Literal) -> Any:
    if lit.is_string:
        return lit.this
    try:
        return int(lit.this) if re.fullmatch(r"-?\d+", lit.this) else float(lit.this)
    except ValueError:
        return None


def _dobrar_texto(no: exp.Expression, binds: dict[str, Any]) -> str | None:
    """Junta uma concatenação feita só de literais e binds ('529.982' || :resto)."""
    if isinstance(no, exp.Paren):
        return _dobrar_texto(no.this, binds)
    if isinstance(no, exp.Literal):
        return str(no.this)
    if isinstance(no, exp.Placeholder):
        valor = binds.get(no.name)
        return None if valor is None or isinstance(valor, bool) else str(valor)
    if isinstance(no, _CONCATENACOES):
        partes = [no.this, no.expression] if isinstance(no, exp.DPipe) else list(no.expressions)
        textos = [_dobrar_texto(p, binds) for p in partes if p is not None]
        if isinstance(no, exp.ConcatWs) and textos:
            separador, *resto = textos
            return None if separador is None or None in resto else separador.join(resto)
        return None if None in textos else "".join(textos)
    return None


def _fonte_da_coluna(escopo, c: exp.Column):
    """Tabela ou escopo de origem da coluna, subindo para o escopo de fora (subconsulta correlacionada)."""
    atual = escopo
    while atual is not None:
        fonte = atual.sources.get(c.table)
        if fonte is not None:
            return fonte
        atual = atual.parent
    return None


def _expressao_da_fonte(fonte) -> exp.Expression:
    return getattr(fonte, "expression", fonte)


def _sem_parenteses(no: exp.Expression | None) -> exp.Expression | None:
    while isinstance(no, (exp.Subquery, exp.Paren)):
        no = no.this
    return no


def _eh(no: exp.Expression | None, alvo: exp.Expression) -> bool:
    return no is alvo


def _em_contagem(c: exp.Column) -> bool:
    """A coluna é o argumento direto de COUNT/COUNT(DISTINCT) (ou o resultado de um CASE contado)."""
    no, pai = c, c.parent
    if isinstance(pai, exp.If) and _eh(pai.args.get("true"), no) and isinstance(pai.parent, exp.Case):
        no, pai = pai.parent, pai.parent.parent
    elif isinstance(pai, exp.Case) and _eh(pai.args.get("default"), no):
        no, pai = pai, pai.parent
    if isinstance(pai, exp.Distinct) and any(e is no for e in pai.expressions):
        no, pai = pai, pai.parent
    if isinstance(pai, (exp.Count, exp.ApproxDistinct)) and _eh(pai.this, no):
        return True
    return isinstance(pai, exp.Anonymous) and (pai.name or "").upper() == "APPROX_COUNT_DISTINCT" and any(e is no for e in pai.expressions)


def _em_teste_de_nulo(c: exp.Column) -> bool:
    """``col IS NULL`` / ``col IS NOT NULL``: diz se há valor, nunca qual."""
    pai = c.parent
    return isinstance(pai, exp.Is) and pai.this is c and isinstance(pai.expression, exp.Null)


def _ate_o_predicado(no: exp.Expression) -> exp.Expression | None:
    """Sobe por AND e parênteses; devolve o WHERE/ON em que a condição está, se for só conjunção."""
    filho, pai = no, no.parent
    while isinstance(pai, (exp.And, exp.Paren)):
        filho, pai = pai, pai.parent
    if isinstance(pai, exp.Where):
        return pai
    if isinstance(pai, exp.Join) and _eh(pai.args.get("on"), filho):
        return pai
    return None


def _em_igualdade_de_colunas(c: exp.Column) -> bool:
    """``a.col = b.col`` (junção), ligada ao ON/WHERE só por AND."""
    pai = c.parent
    if not isinstance(pai, exp.EQ):
        return False
    outro = pai.expression if pai.this is c else pai.this
    return isinstance(outro, exp.Column) and _ate_o_predicado(pai) is not None


def _chave_interna(c: exp.Column, sel: exp.Select) -> bool:
    """Dentro de CTE/subconsulta: repassada, agrupada ou particionada sem transformação."""
    pai = c.parent
    if pai is sel and any(p is c for p in sel.expressions):
        return True
    if isinstance(pai, exp.Alias) and pai.parent is sel and any(p is pai for p in sel.expressions):
        return True
    if isinstance(pai, exp.Group) and pai.parent is sel:
        return True
    if isinstance(pai, exp.Window) and any(x is c for x in pai.args.get("partition_by") or []):
        return True
    return (
        isinstance(pai, exp.Ordered)
        and isinstance(pai.parent, exp.Order)
        and isinstance(pai.parent.parent, exp.Window)
    )


def _lugar_no_externo(sel: exp.Expression, externo: exp.Expression) -> str:
    """Em que parte do SELECT de fora a subconsulta está (expressions, where, having, joins...)."""
    filho, pai = sel, sel.parent
    while pai is not None and pai is not externo:
        filho, pai = pai, pai.parent
    return filho.arg_key if pai is externo else ""


def _dentro_de_agregado(c: exp.Column) -> bool:
    no = c.parent
    while no is not None and not isinstance(no, exp.Select):
        if isinstance(no, exp.AggFunc):
            return True
        no = no.parent
    return False


def _trecho(no: exp.Expression, n: int = 60) -> str:
    try:
        s = no.sql(dialect=DIALETO_CANONICO)
    except Exception:  # noqa: BLE001
        s = type(no).__name__
    return s if len(s) <= n else s[: n - 1] + "…"


def _unicos(itens: list[str]) -> list[str]:
    vistos, saida = set(), []
    for i in itens:
        if i not in vistos:
            vistos.add(i)
            saida.append(i)
    return saida
