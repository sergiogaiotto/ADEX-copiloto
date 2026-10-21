from __future__ import annotations

import pytest

from adex_copiloto.dados.catalogo import Catalogo
from adex_copiloto.dados.fichas import classificar_coluna, montar_ficha
from adex_copiloto.dados.porteiro import Modo, Porteiro
from adex_copiloto.erros import PorteiroRecusouErro


@pytest.fixture
def catalogo():
    return Catalogo(
        [
            montar_ficha(
                "CAD", "BASE_FIBRA",
                [("ID_DOM", "NUMBER", "domicílio", False), ("CPF", "VARCHAR2", "", True),
                 ("COD_CIDADE", "VARCHAR2", "código IBGE", False), ("DT_REF", "DATE", "", False),
                 ("NOME", "VARCHAR2", "", True), ("STATUS", "VARCHAR2", "", False)],
                linhas=312_000_000,
            ),
            montar_ficha(
                "OPS", "CHAMADO",
                [("ID_DOM", "NUMBER", "", False), ("MOTIVO", "NUMBER", "", False), ("DT_ABERTURA", "DATE", "", False)],
                linhas=48_000_000,
            ),
            montar_ficha("CAMPO", "VISITA", [("ID_DOM", "NUMBER", "", False)], acesso="sem_acesso"),
        ]
    )


@pytest.fixture
def porteiro(catalogo):
    return Porteiro(catalogo, min_grupo=50, max_linhas=1000)


def test_classificacao_pii():
    assert classificar_coluna("NR_CPF")[0] == "sim"
    assert classificar_coluna("EMAIL_CONTATO")[0] == "sim"
    assert classificar_coluna("NOME")[0] == "suspeita"
    assert classificar_coluna("NOME_CIDADE")[0] == "nao"
    assert classificar_coluna("COD_CIDADE")[0] == "nao"
    assert classificar_coluna("X1", "Telefone do titular")[0] == "sim"


def test_agregado_valido_ganha_having_e_teto(porteiro):
    v = porteiro.avaliar(
        "SELECT b.cod_cidade, TRUNC(b.dt_ref, 'MONTH') mes, COUNT(*) n FROM cad.base_fibra b "
        "WHERE b.status = 'ATIVO' GROUP BY b.cod_cidade, TRUNC(b.dt_ref, 'MONTH')",
        coluna_unidade="id_dom",
    ).exigir()
    assert "HAVING COUNT(*) >= :adex_min_grupo" in v.sql
    assert "COUNT(DISTINCT" in v.sql
    assert "FETCH FIRST 1000 ROWS ONLY" in v.sql
    assert v.binds["adex_min_grupo"] == 50
    assert v.binds["p1"] == "ATIVO" and "'ATIVO'" not in v.sql
    assert v.tabelas == ["CAD.BASE_FIBRA"]
    assert v.sha256 and len(v.sha256) == 64


def test_juncao_por_chave(porteiro):
    v = porteiro.avaliar(
        "SELECT c.motivo, COUNT(DISTINCT b.id_dom) FROM cad.base_fibra b JOIN ops.chamado c ON c.id_dom = b.id_dom GROUP BY c.motivo"
    )
    assert v.aprovado, v.motivos


def test_pii_so_em_count_ou_juncao(porteiro):
    assert porteiro.avaliar("SELECT COUNT(DISTINCT cpf) FROM cad.base_fibra").aprovado
    v = porteiro.avaliar("SELECT cpf, COUNT(*) FROM cad.base_fibra GROUP BY cpf")
    assert not v.aprovado and any("dado pessoal" in m for m in v.motivos)
    v = porteiro.avaliar("SELECT * FROM cad.base_fibra", modo=Modo.DETALHE)
    assert not v.aprovado and any("CPF" in m for m in v.motivos)
    v = porteiro.avaliar("SELECT COUNT(*) FROM cad.base_fibra WHERE cpf = :x", {"x": "1"})
    assert not v.aprovado
    # suspeita (NOME) também é bloqueada até ser liberada
    assert not porteiro.avaliar("SELECT nome, COUNT(*) FROM cad.base_fibra GROUP BY nome").aprovado


def test_pii_em_cte_tambem_e_bloqueada(porteiro):
    v = porteiro.avaliar(
        "WITH x AS (SELECT cpf AS k, cod_cidade FROM cad.base_fibra) SELECT cod_cidade, COUNT(*) FROM x GROUP BY cod_cidade"
    )
    assert not v.aprovado


def test_liberar_suspeita(catalogo):
    catalogo.liberar("CAD.BASE_FIBRA", "NOME")
    assert Porteiro(catalogo).avaliar("SELECT nome, COUNT(*) FROM cad.base_fibra GROUP BY nome").aprovado
    with pytest.raises(PermissionError):
        catalogo.liberar("CAD.BASE_FIBRA", "CPF")


@pytest.mark.parametrize(
    "sql,trecho",
    [
        ("DELETE FROM cad.base_fibra", "leitura"),
        ("SELECT a FROM t; DROP TABLE t", "uma consulta"),
        ("BEGIN NULL; END;", "sintaxe"),
        ("SELECT COUNT(*) FROM cad.base_fibra FOR UPDATE", "Lock"),
        ("SELECT COUNT(*) FROM cad.base_fibra@link", "db link"),
        ("SELECT DBMS_RANDOM.VALUE() FROM cad.base_fibra", "Rand"),
        ("SELECT COUNT(*) FROM TABLE(dbms_xplan.display)", "não permitida"),
        ("SELECT COUNT(*) FROM cad.inventada", "não existe"),
        ("SELECT COUNT(inventada) FROM cad.base_fibra", "coluna não existe"),
        ("SELECT COUNT(*) FROM campo.visita", "sem acesso"),
        ("SELECT cod_cidade FROM cad.base_fibra", "não está agregado"),
        ("SELECT DISTINCT cod_cidade FROM cad.base_fibra", "DISTINCT"),
        ("SELECT COUNT(*) FROM cad.base_fibra UNION SELECT COUNT(*) FROM ops.chamado", "UNION"),
        ("SELECT LISTAGG(cod_cidade, ',') WITHIN GROUP (ORDER BY cod_cidade) FROM cad.base_fibra", "valores individuais"),
        ("SELECT COUNT(*) FROM cad.base_fibra AS OF TIMESTAMP SYSTIMESTAMP - 1", "Version"),
        ("SELECT COUNT(*) FROM cad.base_fibra WHERE status = '529.982.247-25'", "pessoal"),
        ("SELECT COUNT(*) FROM cad.base_fibra WHERE id_dom = 52998224725", "pessoal"),
        ("SELECT cod_cidade, COUNT(*) FROM cad.base_fibra WHERE status = :st GROUP BY cod_cidade", "faltam valores"),
    ],
)
def test_recusas(porteiro, sql, trecho):
    v = porteiro.avaliar(sql)
    assert not v.aprovado, sql
    assert any(trecho.lower() in m.lower() for m in v.motivos), (sql, v.motivos)
    with pytest.raises(PorteiroRecusouErro):
        v.exigir()


def test_bind_reservado_e_bind_pessoal(porteiro):
    assert not porteiro.avaliar("SELECT COUNT(*) FROM cad.base_fibra WHERE status = :adex_x", {"adex_x": "a"}).aprovado
    assert not porteiro.avaliar("SELECT COUNT(*) FROM cad.base_fibra WHERE status = :s", {"s": "ana@x.com"}).aprovado
    assert not porteiro.avaliar("SELECT COUNT(*) FROM cad.base_fibra WHERE status = :s", {"s": [1, 2]}).aprovado


def test_hint_removido(porteiro):
    v = porteiro.avaliar("SELECT /*+ PARALLEL(64) */ COUNT(*) FROM cad.base_fibra")
    assert v.aprovado and "PARALLEL" not in v.sql and v.avisos


def test_detalhe_sem_pii_com_teto(catalogo):
    porteiro = Porteiro(catalogo, max_linhas=10, max_linhas_detalhe=1000)
    v = porteiro.avaliar("SELECT id_dom, cod_cidade FROM cad.base_fibra FETCH FIRST 5000 ROWS ONLY", modo="detalhe").exigir()
    assert "FETCH FIRST 1000 ROWS ONLY" in v.sql
    assert "HAVING" not in v.sql


def test_nome_sem_esquema_resolvido_quando_unico(porteiro):
    v = porteiro.avaliar("SELECT COUNT(*) FROM chamado").exigir()
    assert v.tabelas == ["OPS.CHAMADO"]


def test_transpila_para_duckdb(porteiro):
    v = porteiro.avaliar(
        "SELECT cod_cidade, COUNT(*) n FROM cad.base_fibra WHERE dt_ref >= DATE '2024-01-01' GROUP BY cod_cidade",
        dialeto="duckdb",
    ).exigir()
    assert "$adex_min_grupo" in v.sql and "LIMIT 1000" in v.sql
    assert ":adex_min_grupo" in v.sql_canonico


def test_construcao_nao_suportada_na_fonte(porteiro):
    v = porteiro.avaliar(
        "SELECT TO_CHAR(dt_ref, 'YYYY-MM') m, COUNT(*) FROM cad.base_fibra GROUP BY TO_CHAR(dt_ref, 'YYYY-MM')",
        dialeto="duckdb",
    )
    assert not v.aprovado and "não suporta" in v.motivos[0]


def test_identificador_nao_sai_no_agregado(porteiro):
    v = porteiro.avaliar("SELECT c.id_dom, COUNT(*) FROM ops.chamado c GROUP BY c.id_dom")
    assert not v.aprovado and any("por cliente ou domicílio" in m for m in v.motivos)


def test_grupo_minimo_conta_unidades_distintas_automaticamente(porteiro):
    v = porteiro.avaliar("SELECT c.motivo, COUNT(*) n FROM ops.chamado c GROUP BY c.motivo").exigir()
    assert "COUNT(DISTINCT C.ID_DOM) >= :adex_min_grupo" in v.sql


def test_cte_agregada_protegida_por_dentro(porteiro):
    v = porteiro.avaliar(
        "WITH m AS (SELECT b.cod_cidade, COUNT(*) n FROM cad.base_fibra b GROUP BY b.cod_cidade) "
        "SELECT m.cod_cidade, SUM(m.n) FROM m GROUP BY m.cod_cidade"
    ).exigir()
    dentro, fora = v.sql.split(") SELECT", 1)
    assert "COUNT(DISTINCT B.ID_DOM) >= :adex_min_grupo" in dentro
    assert "HAVING" not in fora  # a saída só lê grupos já protegidos


def test_cte_por_domicilio_nao_vaza(porteiro):
    # agrupar por domicílio dentro da CTE: cada grupo tem 1 domicílio e é cortado pelo HAVING
    v = porteiro.avaliar(
        "WITH d AS (SELECT c.id_dom, COUNT(*) n FROM ops.chamado c GROUP BY c.id_dom) "
        "SELECT AVG(d.n) media FROM d"
    ).exigir()
    assert "COUNT(DISTINCT C.ID_DOM) >= :adex_min_grupo" in v.sql


def test_cte_sem_agregacao_repasse_identificador(porteiro):
    v = porteiro.avaliar(
        "WITH x AS (SELECT c.id_dom, c.motivo FROM ops.chamado c) SELECT x.motivo, COUNT(*) FROM x GROUP BY x.motivo"
    ).exigir()
    assert "COUNT(DISTINCT X.ID_DOM) >= :adex_min_grupo" in v.sql


def test_filtro_por_subconsulta_nao_muda_sentido(porteiro):
    v = porteiro.avaliar(
        "SELECT b.cod_cidade, COUNT(*) FROM cad.base_fibra b WHERE b.id_dom IN "
        "(SELECT c.id_dom FROM ops.chamado c GROUP BY c.id_dom HAVING COUNT(*) > 3) GROUP BY b.cod_cidade"
    ).exigir()
    assert v.sql.count("adex_min_grupo") == 2  # só no escopo de saída (COUNT(*) e COUNT DISTINCT)


# --- reforço: contextos de uso de dado pessoal e de identificador ------------------------------------


@pytest.fixture
def porteiro_ondas(catalogo):
    """Catálogo com uma tabela de dimensão (sem coluna de pessoa)."""
    cat = Catalogo([*catalogo.tabelas.values(), montar_ficha("ENG", "ONDAS", [("COD_CIDADE", "VARCHAR2", "", False),
                                                                      ("ONDA", "VARCHAR2", "", False)])])
    return Porteiro(cat, min_grupo=50, max_linhas=1000)


@pytest.mark.parametrize(
    "sql,binds,trecho",
    [
        # dado pessoal dentro de CASE WHEN, LIKE ou MIN, mesmo contado
        ("SELECT COUNT(CASE WHEN b.cpf = :x THEN 1 END) FROM cad.base_fibra b", {"x": "123"}, "dado pessoal"),
        ("SELECT COUNT(CASE WHEN b.cpf LIKE '123%' THEN 1 END) FROM cad.base_fibra b", None, "dado pessoal"),
        ("SELECT b.cod_cidade, MIN(b.cpf) FROM cad.base_fibra b GROUP BY b.cod_cidade", None, "dado pessoal"),
        # referência correlacionada a dado pessoal do escopo de fora
        ("SELECT COUNT(*) FROM cad.base_fibra b WHERE EXISTS (SELECT 1 FROM ops.chamado c "
         "WHERE c.id_dom = b.id_dom AND b.cpf LIKE '123%')", None, "dado pessoal"),
        # identificador fora de contagem/junção no modo agregado
        ("SELECT b.cod_cidade, MAX(b.id_dom) FROM cad.base_fibra b GROUP BY b.cod_cidade", None, "identifica"),
        ("SELECT b.cod_cidade, COUNT(*) FROM cad.base_fibra b WHERE b.id_dom = 123 GROUP BY b.cod_cidade", None, "identifica"),
        ("SELECT MOD(b.id_dom, 10) g, COUNT(*) FROM cad.base_fibra b GROUP BY MOD(b.id_dom, 10)", None, "identifica"),
        ("SELECT b.cod_cidade, COUNT(*) FROM cad.base_fibra b GROUP BY b.cod_cidade ORDER BY MAX(b.id_dom)", None, "identifica"),
        ("SELECT COUNT(CASE WHEN c.id_dom = 7 THEN 1 END) FROM ops.chamado c", None, "identifica"),
        # identificador repassado por CTE e devolvido na saída
        ("WITH s AS (SELECT c.id_dom AS u FROM ops.chamado c) SELECT s.u, COUNT(*) FROM s GROUP BY s.u", None,
         "por cliente ou domicílio"),
        ("WITH s AS (SELECT c.id_dom AS u FROM ops.chamado c UNION ALL SELECT b.id_dom FROM cad.base_fibra b) "
         "SELECT s.u, COUNT(*) FROM s GROUP BY s.u", None, "por cliente ou domicílio"),
        # valor pessoal fora dos filtros, ou montado por concatenação
        ("SELECT b.cod_cidade, SUM(CASE WHEN b.status = '529.982.247-25' THEN 1 ELSE 0 END) FROM cad.base_fibra b "
         "GROUP BY b.cod_cidade", None, "pessoal"),
        ("SELECT COUNT(*) FROM cad.base_fibra b WHERE b.status = '529.982' || '.247-25'", None, "pessoal"),
        ("SELECT COUNT(*) FROM cad.base_fibra b WHERE b.status = :a || :b", {"a": "529.982", "b": ".247-25"}, "pessoal"),
        # subconsultas sobre tabela de pessoas que podem isolar alguém
        ("SELECT b.cod_cidade, COUNT(*) FROM cad.base_fibra b WHERE b.cod_cidade IN (SELECT x.cod_cidade "
         "FROM cad.base_fibra x WHERE x.status = 'X' AND x.dt_ref = DATE '2025-01-01') GROUP BY b.cod_cidade", None, "subconsulta"),
        ("SELECT b.cod_cidade, COUNT(*) FROM cad.base_fibra b WHERE EXISTS (SELECT 1 FROM ops.chamado c "
         "WHERE c.motivo = 3114 AND c.dt_abertura = DATE '2025-01-02') GROUP BY b.cod_cidade", None, "subconsulta"),
        ("SELECT b.cod_cidade, COUNT(*) FROM cad.base_fibra b WHERE EXISTS (SELECT 1 FROM cad.base_fibra x "
         "WHERE x.cod_cidade = b.cod_cidade AND x.status = 'Y') GROUP BY b.cod_cidade", None, "subconsulta"),
        ("SELECT b.cod_cidade, COUNT(*) FROM cad.base_fibra b WHERE b.cod_cidade = (SELECT x.cod_cidade "
         "FROM cad.base_fibra x WHERE x.status = 'Z' FETCH FIRST 1 ROWS ONLY) GROUP BY b.cod_cidade", None, "subconsulta"),
    ],
)
def test_reforco_recusas(porteiro, sql, binds, trecho):
    v = porteiro.avaliar(sql, binds)
    assert not v.aprovado, sql
    assert any(trecho.lower() in m.lower() for m in v.motivos), (sql, v.motivos)


@pytest.mark.parametrize(
    "sql",
    [
        # usos legítimos que os modelos de SQL dos blocos fazem
        "SELECT b.cod_cidade, COUNT(DISTINCT CASE WHEN b.status = 'A' THEN b.id_dom END) n FROM cad.base_fibra b GROUP BY b.cod_cidade",
        "SELECT APPROX_COUNT_DISTINCT(b.cpf) FROM cad.base_fibra b",
        "SELECT COUNT(*) FROM cad.base_fibra b WHERE b.cpf IS NULL",
        "SELECT c.motivo, COUNT(*) FROM ops.chamado c JOIN cad.base_fibra b ON b.id_dom = c.id_dom AND b.status = 'A' GROUP BY c.motivo",
        "SELECT b.status, COUNT(*) FROM cad.base_fibra b JOIN cad.base_fibra b2 ON b2.cpf = b.cpf AND b2.status = 'A' GROUP BY b.status",
        "SELECT b.cod_cidade, COUNT(*) FROM cad.base_fibra b WHERE EXISTS (SELECT 1 FROM ops.chamado c "
        "WHERE c.id_dom = b.id_dom AND c.motivo = 3114) GROUP BY b.cod_cidade",
        "WITH u AS (SELECT c.id_dom, c.motivo, ROW_NUMBER() OVER (PARTITION BY c.id_dom ORDER BY c.dt_abertura) rn "
        "FROM ops.chamado c) SELECT u.motivo, COUNT(*) FROM u WHERE u.rn = 1 GROUP BY u.motivo",
        "SELECT COUNT(*) linhas, COUNT(x.id_dom) com_par FROM ops.chamado c LEFT JOIN "
        "(SELECT DISTINCT b.id_dom FROM cad.base_fibra b) x ON x.id_dom = c.id_dom",
    ],
)
def test_reforco_usos_legitimos(porteiro, sql):
    v = porteiro.avaliar(sql)
    assert v.aprovado, (sql, v.motivos)


def test_subconsulta_agregada_ganha_grupo_minimo(porteiro):
    v = porteiro.avaliar(
        "SELECT b.cod_cidade, COUNT(*) FROM cad.base_fibra b WHERE b.cod_cidade IN (SELECT x.cod_cidade "
        "FROM cad.base_fibra x WHERE x.status = 'X' GROUP BY x.cod_cidade) GROUP BY b.cod_cidade"
    ).exigir()
    assert v.sql.count("adex_min_grupo") == 4  # saída e subconsulta: COUNT(*) e COUNT(DISTINCT)
    v = porteiro.avaliar(
        "SELECT b.cod_cidade, COUNT(*) n, (SELECT AVG(c.motivo) FROM ops.chamado c WHERE c.dt_abertura > DATE '2025-01-01') m "
        "FROM cad.base_fibra b GROUP BY b.cod_cidade"
    ).exigir()
    assert v.sql.count("adex_min_grupo") == 4


def test_subconsulta_sobre_dimensao_passa_sem_regra_nova(porteiro_ondas):
    v = porteiro_ondas.avaliar(
        "SELECT b.cod_cidade, COUNT(*) FROM cad.base_fibra b WHERE b.cod_cidade IN "
        "(SELECT o.cod_cidade FROM eng.ondas o WHERE o.onda = '1') GROUP BY b.cod_cidade"
    ).exigir()
    assert v.sql.count("adex_min_grupo") == 2


def test_detalhe_aceita_filtro_por_identificador_mas_nao_valor_pessoal(porteiro):
    assert porteiro.avaliar("SELECT b.id_dom, b.cod_cidade FROM cad.base_fibra b WHERE b.id_dom = 123", modo="detalhe").aprovado
    v = porteiro.avaliar("SELECT b.id_dom FROM cad.base_fibra b WHERE b.status = '529.982.247-25'", modo="detalhe")
    assert not v.aprovado
