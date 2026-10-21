from __future__ import annotations

import datetime as dt
import json

import httpx
import pytest

from adex_copiloto.config.modelos import Config
from adex_copiloto.critico.checagens import EntradaCritica, bloqueia_aceite, checar, reavaliar_selos
from adex_copiloto.critico.critico import criticar
from adex_copiloto.estudo.carta import carta_em_branco
from adex_copiloto.estudo.evidencia import Afirmacao, Decisao, Lacuna, OpcaoDecisao, Registro
from adex_copiloto.estudo.plano import PassoPlano, Plano
from adex_copiloto.llm.marcadores import TabelaMarcadores
from adex_copiloto.narrativa.narrar import narrar
from adex_copiloto.observabilidade import retencao
from adex_copiloto.observabilidade.langfuse_adaptador import (
    DestinoLangfuse,
    ExportadorComFila,
    FilaLocal,
    _dict_para_span,
    mascara_para,
    sdk_major_efetivo,
)
from adex_copiloto.observabilidade.telemetria import Telemetria
from adex_copiloto.observabilidade.orcamento import Orcamento
from falsos import FakeHub, completion, config_teste, telemetria_teste
from test_llm import _cliente


def _carta_aprovada(tipo="efeito"):
    c = carta_em_branco(tipo)
    for nome in c.campos:
        c.campos[nome].valor = "x"
    c.campos["criterios_pronto"].valor = "Efeito com intervalo de 95%; placebo; sensibilidade"
    c.aprovar("ana")
    return c


def _entrada(**kw):
    base = dict(carta=_carta_aprovada(), plano=None, resultados={}, afirmacoes=[], decisoes=[], lacunas=[], passaporte=None)
    base.update(kw)
    return EntradaCritica(**base)


def test_criterios_da_carta_viram_bloqueio():
    e = _entrada(resultados={"efeito": {"bloco": "did_escalonado@1.0", "status": "feito", "metricas": [{"nome": "efeito_att", "ic": None}]}})
    achados = checar(e, Registro())
    regras = {a.regra for a in achados}
    assert {"criterio_placebo", "criterio_sensibilidade", "criterio_ic", "tendencias_pre"} <= regras
    assert bloqueia_aceite(achados, [])


def test_passo_falhou_e_decisao_aberta_bloqueiam():
    d = Decisao(id="D-01", titulo="t", pergunta="?", opcoes=[OpcaoDecisao(codigo="a", rotulo="A")], bloqueia=["aceite"])
    plano = Plano(passos=[PassoPlano(id="universo", bloco="painel_agregado", versao="1.0", status="pendente")])
    e = _entrada(resultados={"x": {"bloco": "b@1.0", "status": "falhou", "erro": "boom"}}, decisoes=[d], plano=plano)
    regras = {a.regra for a in checar(e, Registro())}
    assert {"passo_falhou", "decisao_aberta", "plano_incompleto"} <= regras


def test_alerta_aceito_por_decisao_nao_bloqueia():
    e = _entrada(lacunas=[Lacuna(id="L-01", descricao="sem calendário oficial", impacto="pode haver viés na conclusão")],
                 carta=_carta_aprovada("descricao"))
    achados = checar(e, Registro())
    alerta = next(a for a in achados if a.regra == "lacuna")
    assert alerta.severidade == "alerta" and bloqueia_aceite(achados, [])
    dec = Decisao(id="D-09", titulo="aceitar", pergunta="?", opcoes=[OpcaoDecisao(codigo="aceitar", rotulo="Aceitar")],
                  dados={"achado": alerta.id})
    dec.decidir("aceitar", "risco conhecido e registrado", "ana")
    assert bloqueia_aceite(achados, [dec]) == []


def test_reavaliar_selo_do_efeito():
    a = Afirmacao(id="F-03", tipo="fato", texto="efeito ⟦M-01⟧", selo="B", bloco="did_escalonado@1.0")
    # sem o poder do teste prévio reportado, "não rejeitou" não basta para A
    mud = reavaliar_selos([a], {"teste_pre_p": 0.4, "placebo_passou": True, "sensibilidade_estavel": True})
    assert a.selo == "B" and mud == []
    mud = reavaliar_selos([a], {"teste_pre_p": 0.4, "teste_pre_poder_ok": True, "placebo_passou": True,
                                "sensibilidade_estavel": True})
    assert a.selo == "A" and mud == ["F-03: B → A"]
    reavaliar_selos([a], {"teste_pre_p": 0.4, "placebo_passou": False, "sensibilidade_estavel": True})
    assert a.selo == "B"


def test_critico_ia_tira_numeros_e_nao_duplica():
    resposta = json.dumps({"achados": [
        {"severidade": "alerta", "texto": "O efeito de 12,5% pode vir da sazonalidade.", "refere": "efeito", "o_que_fazer": "ver"},
        {"severidade": "nota", "texto": "A carta não está aprovada.", "refere": "carta"},
    ], "resumo": "ok"})
    hub = FakeHub().em_seguida(completion(resposta))
    cli, _ = _cliente(hub)
    e = _entrada()
    from adex_copiloto.critico.checagens import Achado

    achados_py = checar(e, Registro()) + [Achado(id="C-99", severidade="bloqueio", texto="A carta não está aprovada.")]
    ia, _ = criticar(cli, e, TabelaMarcadores(), achados_py, Registro())
    assert len(ia) == 1 and "12,5" not in ia[0].texto and "número removido" in ia[0].texto
    assert ia[0].origem == "ia" and ia[0].aceitavel


def test_narrativa_refaz_e_preenche():
    tab = TabelaMarcadores()
    m = tab.novo(-0.27, unidade="%", sinal=True, descricao="efeito relativo")
    hub = FakeHub().em_seguida(
        completion(json.dumps({"texto": "Os chamados caíram 27% com a troca."})),
        completion(json.dumps({"texto": f"Os chamados mudaram ⟦{m.id}⟧ depois da troca na onda 4."})),
    )
    cli, _ = _cliente(hub)
    carta = _carta_aprovada()
    carta.campos["pergunta_decisao"].valor = "Vale fazer a onda 4 em 2027?"
    n = narrar(cli, carta=carta, marcadores=tab, afirmacoes=[])
    assert n.origem == "ia" and n.tentativas == 2
    assert n.texto == "Os chamados mudaram -27% depois da troca na onda 4."
    assert "adex-num" in n.html


def test_narrativa_recua_para_texto_modelo():
    tab = TabelaMarcadores()
    m = tab.novo(1353, descricao="células")
    hub = FakeHub().em_seguida(*(completion(json.dumps({"texto": "Foram 1.353 células."})) for _ in range(2)))
    cli, _ = _cliente(hub)
    a = Afirmacao(id="F-01", tipo="fato", texto=f"O universo tem ⟦{m.id}⟧ células.", selo="A")
    n = narrar(cli, carta=_carta_aprovada(), marcadores=tab, afirmacoes=[a])
    assert n.origem == "modelo" and "1.353 células" in n.texto and n.problemas


# --- observabilidade --------------------------------------------------------------------


def test_sdk_pelo_servidor():
    cfg = Config()
    assert sdk_major_efetivo(cfg) == 4  # 4.49
    cfg.langfuse.server_version = "4.60.1"
    assert sdk_major_efetivo(cfg) == 5
    cfg.langfuse.sdk_major = "4"
    assert sdk_major_efetivo(cfg) == 4


def test_mascaras_por_captura():
    dado = {"t": "cliente ana@x.com", "k": "sk-abcdefghijklmnop1234"}
    assert "ana@x.com" not in json.dumps(mascara_para("mascarado")(data=dado))
    assert mascara_para("metadados")(data=dado) == {"tamanho": len(json.dumps(dado, ensure_ascii=False))}
    completo = mascara_para("completo")(data=dado)
    assert "ana@x.com" in completo["t"] and "sk-abcdef" not in completo["k"]  # segredo sai sempre


class _Handle:
    def __init__(self, log, nome, trace_id="t" * 32, id_="s" * 16):
        self.log, self.nome, self.trace_id, self.id = log, nome, trace_id, id_

    def start_observation(self, **kw):
        self.log.append(("filho", self.nome, kw["name"], kw["as_type"]))
        return _Handle(self.log, kw["name"], self.trace_id, "f" * 16)

    def update(self, **kw):
        self.log.append(("update", self.nome, sorted(kw)))

    def end(self):
        self.log.append(("end", self.nome))


class _ClienteLF:
    def __init__(self):
        self.log, self.notas = [], []

    def start_observation(self, **kw):
        self.log.append(("raiz", kw["name"], kw["trace_context"]))
        return _Handle(self.log, kw["name"])

    def create_score(self, **kw):
        self.notas.append(kw)

    def flush(self):
        self.log.append(("flush",))


def test_destino_langfuse_aninha_e_registra(tmp_path):
    lf = _ClienteLF()
    destino = DestinoLangfuse(Config(), estudo_id="EST-1", pasta_fila=tmp_path / "fila", cliente=lf, alcancavel=True)
    tel = Telemetria("EST-1", Orcamento(Config().orcamento), destinos=[destino])
    with tel.comando("frame", entrada={"objetivo": "x"}):
        with tel.observar("geracao", tipo="generation", modelo="qwen-latest") as obs:
            obs.uso = {"input": 10, "output": 5}
            obs.finalizar(saida="ok")
        tel.score("aprovacao_carta", "aprovada")
    assert lf.log[0][0] == "raiz" and any(x[0] == "filho" and x[3] == "generation" for x in lf.log)
    assert lf.notas and lf.notas[0]["name"] == "aprovacao_carta"


def test_destino_sem_rota_vai_para_fila(tmp_path):
    lf = _ClienteLF()
    destino = DestinoLangfuse(Config(), estudo_id="EST-1", pasta_fila=tmp_path / "fila", cliente=lf, alcancavel=False)
    destino.score("x", 1, "NUMERIC", "comentário com ana@x.com", None, "a" * 32)
    linhas = (list((tmp_path / "fila").glob("fila-*.jsonl"))[0]).read_text(encoding="utf-8").splitlines()
    assert json.loads(linhas[0])["tipo"] == "score" and "ana@x.com" not in linhas[0] and not lf.notas


def test_exportador_guarda_spans_e_reconstroi(tmp_path):
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExportResult

    class SemRota:
        def export(self, spans):
            raise ConnectionError("sem rota")

        def shutdown(self):
            pass

    fila = FilaLocal(tmp_path / "fila")
    exp = ExportadorComFila(SemRota(), fila)
    provedor = TracerProvider()
    provedor.add_span_processor(SimpleSpanProcessor(exp))
    with provedor.get_tracer("t").start_as_current_span("pai") as s:
        s.set_attribute("langfuse.observation.input", "mascarado")
        with provedor.get_tracer("t").start_as_current_span("filho"):
            pass
    assert fila.tamanho() == 2 and exp.tentar is False
    d = json.loads(fila.pendentes()[0].read_text(encoding="utf-8").splitlines()[0])
    span = _dict_para_span(d)
    assert span.name == "filho" and span.parent is not None
    assert exp.export([span]) == SpanExportResult.SUCCESS and fila.tamanho() == 3


def test_retencao_local(tmp_path):
    cfg = Config()
    fila = tmp_path / "fila_langfuse"
    fila.mkdir()
    velho = fila / f"fila-{(dt.date.today() - dt.timedelta(days=30)).isoformat()}.jsonl"
    novo = fila / f"fila-{dt.date.today().isoformat()}.jsonl"
    velho.write_text("{}\n")
    novo.write_text("{}\n")
    est = tmp_path / "estudos" / "EST-1"
    est.mkdir(parents=True)
    (est / "checkpoints.sqlite").write_bytes(b"x")
    quando = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=45)).isoformat()
    (est / "meta.json").write_text(json.dumps({"handoff_em": quando}), encoding="utf-8")
    rel = retencao.aplicar_local(cfg, tmp_path, executar=False)
    assert rel["fila_langfuse"] == [velho.name] and velho.exists()
    rel = retencao.aplicar_local(cfg, tmp_path)
    assert not velho.exists() and novo.exists() and not (est / "checkpoints.sqlite").exists()


def test_lifecycle_e_purga_langfuse(monkeypatch):
    cfg = Config()
    cfg.objectstorage.prefixo_estudos = "adex/estudos/"
    itens = retencao.politica_lifecycle_bucket(cfg)
    assert itens[0]["timeAmount"] == 180 and itens[0]["objectNameFilter"]["inclusionPrefixes"] == ["adex/estudos/"]
    from adex_copiloto.seguranca.segredos import Segredo

    monkeypatch.setattr(
        "adex_copiloto.observabilidade.langfuse_adaptador.ler_credenciais",
        lambda c: (Segredo("pk-lf-1234567890", "pk", "t"), Segredo("sk-lf-1234567890", "sk", "t"), "https://lf.teste"),
    )
    apagados = []

    def responder(req: httpx.Request):
        if req.method == "GET":
            pagina = int(req.url.params["page"])
            return httpx.Response(200, json={"data": [{"id": f"t{pagina}a"}, {"id": f"t{pagina}b"}], "meta": {"totalPages": 2}})
        apagados.extend(json.loads(req.content)["traceIds"])
        return httpx.Response(200, json={})

    http = httpx.Client(transport=httpx.MockTransport(responder))
    r = retencao.purgar_langfuse(cfg, executar=False, cliente=http)
    assert r["encontrados"] == 4 and r["apagados"] == 0 and not apagados
    r = retencao.purgar_langfuse(cfg, executar=True, cliente=http, lote=3)
    assert r["apagados"] == 4 and sorted(apagados) == ["t1a", "t1b", "t2a", "t2b"]
