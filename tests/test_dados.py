from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from adex_copiloto.config.modelos import Config
from adex_copiloto.dados.armazenamento import Armazenamento, partes_oci
from adex_copiloto.dados.fontes import FonteLocal
from adex_copiloto.erros import DestinoNaoAutorizadoErro, PorteiroRecusouErro
from falsos import telemetria_teste


@pytest.fixture
def fonte():
    rng = np.random.default_rng(1)
    n = 3000
    base = pd.DataFrame(
        {
            "id_dom": np.arange(n),
            "cpf": [f"{i:011d}" for i in range(n)],
            "cod_cidade": rng.choice(["C1", "C2", "C3"], n),
            "mes": pd.to_datetime("2025-01-01") + pd.to_timedelta(rng.integers(0, 90, n), unit="D"),
        }
    )
    chamados = pd.DataFrame({"id_dom": rng.integers(0, n, 5000), "cod_motivo": rng.choice([10, 20, 30], 5000)})
    f = FonteLocal.de_dataframes(Config(), {"CAD.BASE": base, "OPS.CHAMADO": chamados}, comentarios={"CAD.BASE": "base mensal"})
    f.registrar("CAMPO.VISITA", pd.DataFrame({"id_dom": [1, 2]}))
    f.negar_acesso("CAMPO.VISITA")
    return f


def test_catalogo_local(fonte):
    cat = fonte.catalogo()
    assert set(cat.tabelas) == {"CAD.BASE", "OPS.CHAMADO", "CAMPO.VISITA"}
    base = cat.resolver("CAD.BASE")
    assert base.comentario == "base mensal"
    assert base.coluna("CPF").bloqueada and base.coluna("ID_DOM").identificador
    assert cat.resolver("CAMPO.VISITA").acesso == "sem_acesso"
    lotes = cat.lotes_para_llm(tokens_por_lote=50)
    assert sum(len(l) for l in lotes) == 3


def test_consulta_agregada_local(fonte):
    tel = telemetria_teste()
    r = fonte.consultar(
        "SELECT b.cod_cidade, COUNT(*) n FROM cad.base b GROUP BY b.cod_cidade ORDER BY b.cod_cidade",
        etapa="universo", telemetria=tel,
    )
    assert list(r.df.columns) == ["cod_cidade", "n"]
    assert r.df["n"].sum() == 3000
    assert tel.consultas[-1].aprovado and tel.consultas[-1].linhas == 3


def test_grupo_minimo_corta_grupos_pequenos(fonte):
    cfg = fonte.cfg
    cfg.data_policy.min_group_size = 1200
    r = fonte.consultar("SELECT b.cod_cidade, COUNT(*) n FROM cad.base b GROUP BY b.cod_cidade")
    assert (r.df["n"] >= 1200).all()


def test_recusa_registrada(fonte):
    tel = telemetria_teste()
    with pytest.raises(PorteiroRecusouErro):
        fonte.consultar("SELECT cpf FROM cad.base", telemetria=tel)
    assert tel.consultas[-1].aprovado is False


def test_detalhe_fica_no_teto_de_amostra(fonte):
    fonte.cfg.data_policy.max_linhas_amostra = 100
    r = fonte.consultar("SELECT b.id_dom, b.cod_cidade FROM cad.base b", modo="detalhe")
    assert len(r.df) == 100


def test_armazenamento_so_destinos_autorizados(tmp_path):
    cfg = Config()
    arm = Armazenamento(cfg, tmp_path / "estado")
    destino = tmp_path / "estado" / "estudos" / "x.json"
    arm.escrever(destino, b"{}")
    assert destino.read_bytes() == b"{}"
    with pytest.raises(DestinoNaoAutorizadoErro):
        arm.escrever(tmp_path / "fora" / "x.json", b"{}")
    with pytest.raises(DestinoNaoAutorizadoErro):
        arm.escrever("oci://outro@ns/x.json", b"{}")
    cfg.objectstorage.namespace, cfg.objectstorage.bucket_estudos = "ns", "estudos"
    assert Armazenamento(cfg, tmp_path).autorizado("oci://estudos@ns/adex/estudos/EST-1/dossie.jsonl")
    assert partes_oci("oci://b@n/a/b.txt") == ("b", "n", "a/b.txt")


class _CursorFalso:
    def __init__(self, con):
        self.con, self.description, self._linhas = con, None, []
        self.arraysize = self.prefetchrows = 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, binds=None):
        self.con.log.append(sql.split()[0:3])
        s = sql.upper()
        if "FROM ALL_TABLES" in s:
            self._linhas = [("CAD", "BASE", 1000, "base")]
        elif "FROM ALL_TAB_COLUMNS" in s:
            self._linhas = [("CAD", "BASE", "COD_CIDADE", "VARCHAR2", "N", None), ("CAD", "BASE", "ID_DOM", "NUMBER", "N", None)]
        elif "FROM PLAN_TABLE" in s:
            self._linhas = [(self.con.custo, 2e9, 10)]
        elif s.startswith("SELECT"):
            self.description = [("COD_CIDADE",), ("N",)]
            self._linhas = [("C1", 600), ("C2", 400)]

    def fetchall(self):
        return self._linhas

    def fetchone(self):
        return self._linhas[0] if self._linhas else None


class _ConexaoFalsa:
    def __init__(self, custo):
        self.log, self.custo, self.call_timeout, self.rollbacks = [], custo, 0, 0

    def cursor(self):
        return _CursorFalso(self)

    def rollback(self):
        self.rollbacks += 1

    def ping(self):
        pass


def test_fonte_adw_sequencia_e_custo(monkeypatch):
    import sys
    import types

    from adex_copiloto.dados.fontes import FonteADW
    from adex_copiloto.seguranca.segredos import Segredo

    con = _ConexaoFalsa(custo=1000)
    falso = types.SimpleNamespace(connect=lambda **kw: con)
    monkeypatch.setitem(sys.modules, "oracledb", falso)
    cfg = Config()
    cfg.adw.dsn, cfg.adw.usuario, cfg.adw.protocolo, cfg.adw.esquemas = "adw_low", "leitor", "tls", ["CAD"]
    fonte = FonteADW(cfg, senha=Segredo("x" * 8, "adw.senha", "teste"))
    r = fonte.consultar("SELECT b.cod_cidade, COUNT(*) n FROM cad.base b GROUP BY b.cod_cidade")
    assert list(r.df["n"]) == [600, 400]
    comandos = [" ".join(c) for c in con.log]
    i_explain = next(i for i, c in enumerate(comandos) if c.startswith("EXPLAIN"))
    i_ro = next(i for i, c in enumerate(comandos) if c.startswith("SET TRANSACTION READ"))
    assert i_explain < i_ro and con.rollbacks >= 2
    con.custo = 1e12
    with pytest.raises(PorteiroRecusouErro) as e:
        fonte.consultar("SELECT b.cod_cidade, COUNT(*) n FROM cad.base b GROUP BY b.cod_cidade")
    assert "custo" in str(e.value)
