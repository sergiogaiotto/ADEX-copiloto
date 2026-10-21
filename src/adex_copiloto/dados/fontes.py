"""Fontes de dados: ADW (python-oracledb) e local (DuckDB).

Toda consulta passa pelo :class:`~adex_copiloto.dados.porteiro.Porteiro` e por
:meth:`FonteDados.consultar`, que confere custo, executa em modo só leitura,
mede tempo e volume e registra tudo na telemetria.

A fonte local (DuckDB) existe para desenvolver e testar fora da OCI: ela recebe
DataFrames ou arquivos (Parquet/CSV) com nomes ``ESQUEMA.TABELA`` e aceita o
mesmo SQL Oracle (o porteiro traduz).
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import re
import threading
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from adex_copiloto.config.modelos import Config
from adex_copiloto.dados.catalogo import Catalogo
from adex_copiloto.dados.fichas import FichaTabela, montar_ficha
from adex_copiloto.dados.porteiro import Modo, Porteiro, Veredito
from adex_copiloto.erros import FonteIndisponivelErro, PorteiroRecusouErro
from adex_copiloto.observabilidade.telemetria import RegistroConsulta, Telemetria, agora

log = logging.getLogger("adex_copiloto.dados")


@dataclass
class CustoPlano:
    custo: float | None
    bytes: float | None
    linhas: float | None

    @property
    def gb(self) -> float | None:
        return None if self.bytes is None else self.bytes / 1e9


@dataclass
class ResultadoConsulta:
    df: pd.DataFrame
    veredito: Veredito
    segundos: float
    custo: CustoPlano | None

    @property
    def linhas(self) -> int:
        return len(self.df)


class FonteDados(ABC):
    nome: str = "fonte"
    dialeto: str = "oracle"

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._catalogo: Catalogo | None = None
        self._trava = threading.Lock()

    # --- a implementar ------------------------------------------------------------------

    @abstractmethod
    def _carregar_catalogo(self) -> Catalogo: ...

    @abstractmethod
    def _executar(self, sql: str, binds: dict[str, Any]) -> pd.DataFrame: ...

    def explicar(self, veredito: Veredito) -> CustoPlano | None:
        return None

    def fechar(self) -> None:  # pragma: no cover - depende da fonte
        pass

    # --- comum ---------------------------------------------------------------------------

    def catalogo(self, *, recarregar: bool = False) -> Catalogo:
        with self._trava:
            if self._catalogo is None or recarregar:
                self._catalogo = self._carregar_catalogo()
            return self._catalogo

    def porteiro(self) -> Porteiro:
        dp = self.cfg.data_policy
        return Porteiro(
            self.catalogo(),
            min_grupo=dp.min_group_size,
            max_linhas=dp.max_linhas_resultado,
            max_linhas_detalhe=dp.max_linhas_amostra,
            funcoes_extras=dp.funcoes_sql_extras,
        )

    def consultar(
        self,
        sql: str,
        binds: dict[str, Any] | None = None,
        *,
        modo: Modo | str = Modo.AGREGADO,
        coluna_unidade: str | None = None,
        etapa: str = "",
        telemetria: Telemetria | None = None,
        max_linhas: int | None = None,
        porteiro: Porteiro | None = None,
    ) -> ResultadoConsulta:
        porteiro = porteiro or self.porteiro()
        modo = Modo(modo)
        veredito = porteiro.avaliar(
            sql, binds, modo=modo, coluna_unidade=coluna_unidade, dialeto=self.dialeto, max_linhas=max_linhas
        )
        registro = RegistroConsulta(
            em=agora().isoformat(), etapa=etapa, fonte=self.nome, sql_sha256=veredito.sha256, aprovado=veredito.aprovado,
            motivo="; ".join(veredito.motivos)[:500],
        )
        if not veredito.aprovado:
            if telemetria:
                telemetria.registrar_consulta(registro)
            veredito.exigir()
        custo = self.explicar(veredito)
        if custo is not None:
            registro.custo_plano, registro.gb_estimados = custo.custo, custo.gb
            motivo = self._custo_excessivo(custo)
            if motivo:
                registro.aprovado, registro.motivo = False, motivo
                if telemetria:
                    telemetria.registrar_consulta(registro)
                raise PorteiroRecusouErro(
                    f"O porteiro recusou a consulta pelo custo: {motivo}",
                    como_resolver="Filtre o período ou as cidades, ou agregue mais antes de juntar tabelas.",
                    detalhes={"custo": custo.custo, "gb": custo.gb},
                )
        contexto = telemetria.observar(f"consulta:{self.nome}", tipo="tool", etapa=etapa,
                                       entrada={"sql": veredito.sql_canonico, "binds": list(veredito.binds)},
                                       metadados={"sha256": veredito.sha256, "modo": modo.value}) if telemetria else None
        inicio = time.perf_counter()
        try:
            if contexto:
                with contexto as obs:
                    df = self._executar(veredito.sql, veredito.binds)
                    obs.finalizar(saida={"linhas": len(df), "colunas": list(df.columns)})
            else:
                df = self._executar(veredito.sql, veredito.binds)
        except PorteiroRecusouErro:
            raise
        except Exception as e:  # noqa: BLE001
            registro.aprovado, registro.motivo = False, f"falha na execução: {type(e).__name__}"
            if telemetria:
                telemetria.registrar_consulta(registro)
            raise FonteIndisponivelErro(f"A fonte '{self.nome}' falhou ao executar a consulta ({type(e).__name__}: {str(e)[:200]}).") from e
        segundos = time.perf_counter() - inicio
        df.columns = [str(c).lower() for c in df.columns]
        registro.linhas, registro.segundos = len(df), round(segundos, 3)
        if telemetria:
            telemetria.registrar_consulta(registro)
        return ResultadoConsulta(df=df, veredito=veredito, segundos=segundos, custo=custo)

    def _custo_excessivo(self, custo: CustoPlano) -> str:
        if custo.custo is not None and custo.custo > self.cfg.adw.custo_max_plano:
            return f"custo do plano {custo.custo:,.0f} acima do teto {self.cfg.adw.custo_max_plano:,.0f}".replace(",", ".")
        teto_gb = self.cfg.data_policy.teto_gb_lidos
        if custo.gb is not None and custo.gb > teto_gb:
            return f"leitura estimada de {custo.gb:.1f} GB acima do teto de {teto_gb:.0f} GB".replace(".", ",")
        return ""


# --- Fonte local (DuckDB) ----------------------------------------------------------------


_NOME_TABELA = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$")


class FonteLocal(FonteDados):
    """DuckDB em memória com tabelas ESQUEMA.TABELA a partir de DataFrames ou arquivos."""

    nome = "local"
    dialeto = "duckdb"

    def __init__(self, cfg: Config, *, comentarios: dict[str, str] | None = None) -> None:
        super().__init__(cfg)
        import duckdb

        self._con = duckdb.connect(database=":memory:")
        self._con.execute("SET threads TO 2")
        self._comentarios = {k.upper(): v for k, v in (comentarios or {}).items()}
        self._acesso_negado: set[str] = set()
        self._trava_exec = threading.Lock()

    @classmethod
    def de_dataframes(cls, cfg: Config, tabelas: dict[str, pd.DataFrame], **kw) -> "FonteLocal":
        fonte = cls(cfg, **kw)
        for nome, df in tabelas.items():
            fonte.registrar(nome, df)
        return fonte

    def registrar(self, nome: str, dados: pd.DataFrame | str | os.PathLike, *, comentario: str = "") -> None:
        if not _NOME_TABELA.match(nome):
            raise ValueError(f"nome de tabela precisa ser ESQUEMA.TABELA: {nome!r}")
        esquema, tabela = nome.upper().split(".")
        with self._trava_exec:
            self._con.execute(f'CREATE SCHEMA IF NOT EXISTS "{esquema}"')
            if isinstance(dados, pd.DataFrame):
                self._con.register("_adex_tmp", dados)
                self._con.execute(f'CREATE OR REPLACE TABLE "{esquema}"."{tabela}" AS SELECT * FROM _adex_tmp')
                self._con.unregister("_adex_tmp")
            else:
                caminho = str(Path(dados)).replace("'", "''")
                leitor = "read_parquet" if caminho.lower().endswith(".parquet") else "read_csv_auto"
                self._con.execute(f"CREATE OR REPLACE VIEW \"{esquema}\".\"{tabela}\" AS SELECT * FROM {leitor}('{caminho}')")
        if comentario:
            self._comentarios[nome.upper()] = comentario
        self._catalogo = None

    def negar_acesso(self, nome: str) -> None:
        """Simula uma tabela que aparece no catálogo mas sem permissão (para testes e demonstrações)."""
        self._acesso_negado.add(nome.upper())
        self._catalogo = None

    def _carregar_catalogo(self) -> Catalogo:
        with self._trava_exec:
            colunas = self._con.execute(
                "SELECT table_schema, table_name, column_name, data_type, is_nullable "
                "FROM information_schema.columns WHERE table_schema NOT IN ('main', 'information_schema', 'pg_catalog') "
                "ORDER BY table_schema, table_name, ordinal_position"
            ).fetchall()
            tamanhos = {
                (s.upper(), t.upper()): n
                for s, t, n in self._con.execute(
                    "SELECT schema_name, table_name, estimated_size FROM duckdb_tables()"
                ).fetchall()
            }
        por_tabela: dict[tuple[str, str], list[tuple[str, str, str, bool]]] = {}
        for s, t, c, tipo, nulo in colunas:
            por_tabela.setdefault((s.upper(), t.upper()), []).append((c, tipo, "", nulo == "YES"))
        fichas = []
        for (s, t), cols in por_tabela.items():
            nome = f"{s}.{t}"
            fichas.append(
                montar_ficha(
                    s, t, cols, comentario=self._comentarios.get(nome, ""), linhas=tamanhos.get((s, t)),
                    fonte="local", acesso="sem_acesso" if nome in self._acesso_negado else "ok",
                    extras_pii=self.cfg.data_policy.padroes_pii_extra,
                )
            )
        return Catalogo(fichas)

    def _executar(self, sql: str, binds: dict[str, Any]) -> pd.DataFrame:
        with self._trava_exec:
            cur = self._con.cursor()
            try:
                return cur.execute(sql, binds or None).fetchdf()
            finally:
                cur.close()


# --- ADW (python-oracledb, modo thin) ---------------------------------------------------------


class FonteADW(FonteDados):
    nome = "adw"
    dialeto = "oracle"

    def __init__(self, cfg: Config, *, senha=None, wallet_senha=None) -> None:
        super().__init__(cfg)
        self._senha = senha
        self._wallet_senha = wallet_senha
        self._con = None

    def _conectar(self):
        if self._con is not None:
            try:
                self._con.ping()
                return self._con
            except Exception:  # noqa: BLE001
                self._con = None
        try:
            import oracledb  # type: ignore[import-not-found]
        except ImportError as e:
            raise FonteIndisponivelErro(
                "python-oracledb não está instalado.", como_resolver="Instale o extra: pip install adex-copiloto[oci]"
            ) from e
        a = self.cfg.adw
        if not a.dsn or not a.usuario:
            raise FonteIndisponivelErro("adw.dsn e adw.usuario precisam estar configurados.")
        if self._senha is None:
            from adex_copiloto.seguranca.segredos import ler_segredo

            self._senha = ler_segredo(a.senha, "adw.senha", self.cfg)
        kwargs: dict[str, Any] = {"user": a.usuario, "password": self._senha.revelar(), "dsn": a.dsn}
        if a.protocolo == "mtls":
            if not a.wallet_dir:
                raise FonteIndisponivelErro("adw.protocolo = 'mtls' exige adw.wallet_dir.")
            if self._wallet_senha is None:
                from adex_copiloto.seguranca.segredos import ler_segredo

                self._wallet_senha = ler_segredo(a.wallet_senha, "adw.wallet_senha", self.cfg)
            kwargs.update(
                config_dir=os.path.expanduser(a.wallet_dir),
                wallet_location=os.path.expanduser(a.wallet_dir),
                wallet_password=self._wallet_senha.revelar(),
            )
        try:
            con = oracledb.connect(**kwargs)
        except Exception as e:  # noqa: BLE001
            raise FonteIndisponivelErro(
                f"Não foi possível conectar ao ADW ({type(e).__name__}).",
                como_resolver=(
                    "Confira dsn/serviço (prefira o LOW), wallet e a rede da sessão: a rede default do Data "
                    "Science não alcança ADW privado."
                ),
            ) from e
        con.call_timeout = a.call_timeout_ms
        with con.cursor() as cur:
            cur.execute("ALTER SESSION SET NLS_DATE_FORMAT = 'YYYY-MM-DD'")
            cur.execute("ALTER SESSION SET NLS_TIMESTAMP_FORMAT = 'YYYY-MM-DD HH24:MI:SS'")
            if a.sessao_somente_leitura_26ai:
                cur.execute("ALTER SESSION SET READ_ONLY = TRUE")
        self._con = con
        return con

    def _carregar_catalogo(self) -> Catalogo:
        a = self.cfg.adw
        if not a.esquemas:
            raise FonteIndisponivelErro("adw.esquemas está vazio: diga quais esquemas o copiloto pode conhecer.")
        con = self._conectar()
        donos = {f"o{i}": e.upper() for i, e in enumerate(a.esquemas)}
        lista = ", ".join(f":{k}" for k in donos)
        permitidas = {t.upper() for t in a.tabelas_permitidas}
        with con.cursor() as cur:
            cur.arraysize = a.arraysize
            cur.execute(
                f"""SELECT t.owner, t.table_name, t.num_rows, c.comments
                    FROM all_tables t
                    LEFT JOIN all_tab_comments c ON c.owner = t.owner AND c.table_name = t.table_name
                    WHERE t.owner IN ({lista})""",
                donos,
            )
            tabelas = {(o, t): (n, com) for o, t, n, com in cur.fetchall()}
            cur.execute(
                f"""SELECT col.owner, col.table_name, col.column_name, col.data_type, col.nullable, cc.comments
                    FROM all_tab_columns col
                    LEFT JOIN all_col_comments cc
                      ON cc.owner = col.owner AND cc.table_name = col.table_name AND cc.column_name = col.column_name
                    WHERE col.owner IN ({lista})
                    ORDER BY col.owner, col.table_name, col.column_id""",
                donos,
            )
            colunas: dict[tuple[str, str], list[tuple[str, str, str, bool]]] = {}
            for o, t, c, tipo, nulo, com in cur.fetchall():
                colunas.setdefault((o, t), []).append((c, tipo, com or "", nulo == "Y"))
        fichas: list[FichaTabela] = []
        for (o, t), (n, com) in tabelas.items():
            if permitidas and f"{o}.{t}" not in permitidas:
                continue
            fichas.append(
                montar_ficha(o, t, colunas.get((o, t), []), comentario=com or "", linhas=n, fonte="adw",
                             extras_pii=self.cfg.data_policy.padroes_pii_extra)
            )
        if len(fichas) > self.cfg.descoberta.max_tabelas:
            log.warning("catálogo com %s tabelas; a descoberta usará lotes", len(fichas))
        return Catalogo(fichas)

    def explicar(self, veredito: Veredito) -> CustoPlano | None:
        con = self._conectar()
        sid = "adex_" + uuid.uuid4().hex[:20]
        with con.cursor() as cur:
            try:
                try:
                    cur.execute(f"EXPLAIN PLAN SET STATEMENT_ID = '{sid}' FOR {veredito.sql}")
                except Exception:  # noqa: BLE001 - algumas versões pedem os binds
                    cur.execute(f"EXPLAIN PLAN SET STATEMENT_ID = '{sid}' FOR {veredito.sql}", veredito.binds)
                cur.execute(
                    "SELECT MAX(cost), MAX(bytes), MAX(cardinality) FROM plan_table WHERE statement_id = :s AND id = 0",
                    {"s": sid},
                )
                custo, bytes_, linhas = cur.fetchone() or (None, None, None)
                cur.execute("DELETE FROM plan_table WHERE statement_id = :s", {"s": sid})
            finally:
                con.rollback()
        return CustoPlano(
            custo=float(custo) if custo is not None else None,
            bytes=float(bytes_) if bytes_ is not None else None,
            linhas=float(linhas) if linhas is not None else None,
        )

    def _executar(self, sql: str, binds: dict[str, Any]) -> pd.DataFrame:
        con = self._conectar()
        a = self.cfg.adw
        try:
            with con.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                cur.arraysize = a.arraysize
                cur.prefetchrows = a.arraysize + 1
                cur.execute(sql, {k: _bind_oracle(v) for k, v in (binds or {}).items()})
                nomes = [d[0] for d in cur.description]
                linhas = cur.fetchall()
            return pd.DataFrame.from_records(linhas, columns=nomes)
        finally:
            con.rollback()

    def fechar(self) -> None:
        if self._con is not None:
            try:
                self._con.close()
            finally:
                self._con = None


def _bind_oracle(v: Any) -> Any:
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, dt.datetime) and v.tzinfo is not None:
        return v.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return v
