"""Regressões da revisão adversarial de governança e segurança.

Cada teste reproduz uma falha que a revisão confirmou (com o script do ataque) e
confere que ela não volta.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import logging

import httpx
import pytest
import tomli_w

from adex_copiloto.config import carregar
from adex_copiloto.config.configuracoes import (
    assinar_bytes,
    carregar_chave_privada,
    gerar_par_chaves,
    impressao_chave_publica,
    montar_arquivo,
    publica_de_privada,
    verificar_conteudo,
)
from adex_copiloto.config.modelos import Config, Hub
from adex_copiloto.erros import ConfiguracaoAdminErro, ImpressaoDivergenteErro, IntegridadeErro
from adex_copiloto.estudo.dossie import Dossie
from adex_copiloto.observabilidade import retencao
from adex_copiloto.observabilidade.orcamento import Orcamento
from adex_copiloto.observabilidade.telemetria import Telemetria
from adex_copiloto.seguranca.redacao import instalar_filtro_logging, mascarar, mascarar_texto
from adex_copiloto.seguranca.segredos import Segredo, calcular_impressao, conferir_endpoint_hub
from falsos import chave_teste, telemetria_teste


class _Par:
    def __init__(self) -> None:
        pem, publica = gerar_par_chaves()
        self.chave = carregar_chave_privada(pem)
        self.ancora = impressao_chave_publica(publica)

    def publicar(self, pasta, valores, *, versao=3, validade=None, nome="configuracoes.toml"):
        conteudo, assinatura = montar_arquivo(valores, self.chave, versao=versao, emitido_por="admin@teste", validade_ate=validade)
        arq = pasta / "admin" / nome
        arq.parent.mkdir(parents=True, exist_ok=True)
        arq.write_bytes(conteudo)
        (arq.parent / (nome + ".sig")).write_bytes(assinatura)
        return arq


def _apontar(ambiente, arq, extra=""):
    (ambiente / "adex.toml").write_text(f'[admin]\narquivo = "{arq.as_posix()}"\n{extra}', encoding="utf-8")


# --- 1. trocar a seção inteira não passa por cima do Admin ---------------------------------------


def test_secao_inteira_em_json_nao_troca_impressao(monkeypatch):
    falsa = "https://evil.example/v1"
    monkeypatch.setenv("ADEX__HUB", json.dumps({"base_url": falsa, "base_url_impressao": calcular_impressao(falsa, "x"),
                                                "base_url_salt": "x"}))
    efetiva = carregar()
    assert efetiva.config.hub.base_url == falsa  # chave comum: o dev pode mudar
    assert efetiva.config.hub.base_url_impressao == "" and efetiva.config.hub.base_url_salt == ""
    assert any("hub.base_url_impressao" in a and "só o Admin" in a for a in efetiva.avisos)
    with pytest.raises(ImpressaoDivergenteErro):
        conferir_endpoint_hub(efetiva.config)  # a chave nunca iria para o endereço falso


def test_secao_inteira_nao_libera_plugins_nem_troca_impressao_da_chave(monkeypatch):
    monkeypatch.setenv("ADEX__AGENTE", json.dumps({"entry_points_permitidos": ["plugin_malicioso"]}))
    monkeypatch.setenv("ADEX__HUB__CHAVE", json.dumps({"impressao": "b" * 64, "salt": "y"}))
    cfg = carregar().config
    assert cfg.agente.entry_points_permitidos == Config().agente.entry_points_permitidos
    assert cfg.hub.chave.impressao == "" and cfg.hub.chave.salt == ""


def test_secao_vazia_no_toml_nao_zera_o_admin(tmp_path, ambiente_limpo, monkeypatch):
    par = _Par()
    arq = par.publicar(tmp_path, {"hub.modelo_impressao": "c" * 64})
    monkeypatch.setenv("ADEX_ADMIN_ANCORA", par.ancora)
    _apontar(ambiente_limpo, arq, "hub = {}\n")
    efetiva = carregar()
    assert efetiva.admin.valido, efetiva.admin.motivo
    assert efetiva.config.hub.modelo_impressao == "c" * 64


def test_objeto_pydantic_no_codigo_nao_troca_chave_do_admin(tmp_path, ambiente_limpo, monkeypatch):
    par = _Par()
    arq = par.publicar(tmp_path, {"hub.modelo_impressao": "d" * 64})
    monkeypatch.setenv("ADEX_ADMIN_ANCORA", par.ancora)
    _apontar(ambiente_limpo, arq)
    efetiva = carregar(sobrescritas={"hub": Hub(modelo_impressao="", timeout_s=42)})
    assert efetiva.config.hub.modelo_impressao == "d" * 64
    assert efetiva.config.hub.timeout_s == 42
    linha = next(l for l in efetiva.linhas() if l["chave"] == "hub.modelo_impressao")
    assert linha["so_admin"] and linha["origem"] == "admin v3"


def test_proxy_e_ca_sao_do_admin(ambiente_limpo):
    (ambiente_limpo / "adex.toml").write_text('[hub]\nproxy = "http://intercepta:8080"\nca_bundle = "ca.pem"\n', encoding="utf-8")
    cfg = carregar().config
    assert cfg.hub.proxy == "" and cfg.hub.ca_bundle == ""


# --- 2. nan não afrouxa teto ------------------------------------------------------------------------


def test_nan_nao_afrouxa_orcamento_nem_teto(monkeypatch):
    monkeypatch.setenv("ADEX__ORCAMENTO__CUSTO_USD_POR_ESTUDO", "nan")
    monkeypatch.setenv("ADEX__DATA_POLICY__TETO_GB_LIDOS", "nan")
    efetiva = carregar()
    assert efetiva.config.orcamento.custo_usd_por_estudo == Config().orcamento.custo_usd_por_estudo
    assert efetiva.config.data_policy.teto_gb_lidos == Config().data_policy.teto_gb_lidos
    assert sum("valor inválido" in a for a in efetiva.avisos) == 2
    with pytest.raises(Exception):
        Config().orcamento.custo_usd_por_estudo = float("nan")  # o esquema recusa nan/inf


# --- 3. a chave só vai para endereço homologado, em todo ponto de uso ---------------------------------


def test_lista_de_modelos_nao_envia_chave_para_endereco_nao_homologado():
    from adex_copiloto.llm.impressao_digital import consultar_modelos

    pedidos = []
    http = httpx.Client(transport=httpx.MockTransport(lambda r: pedidos.append(r) or httpx.Response(200, json={"data": []})))
    cfg = Config()
    cfg.hub.base_url = "http://127.0.0.1:9/v1"
    with pytest.raises(ImpressaoDivergenteErro):
        consultar_modelos(cfg, chave_teste(), http_client=http)
    assert pedidos == []


def test_cliente_e_chat_conferem_o_endereco():
    from adex_copiloto.llm.chat_langchain import criar_chat_qwen
    from adex_copiloto.llm.cliente import ClienteHub

    cfg = Config()
    cfg.hub.base_url = "http://127.0.0.1:9/v1"
    with pytest.raises(ImpressaoDivergenteErro):
        ClienteHub(cfg, chave_teste(), telemetria_teste(cfg))
    with pytest.raises(ImpressaoDivergenteErro):
        criar_chat_qwen(cfg, chave_teste(), "acao")


# --- 4. âncora, validade e volta a arquivo antigo -------------------------------------------------------


def test_validade_ate_e_obrigatoria():
    pem, _ = gerar_par_chaves()
    chave = carregar_chave_privada(pem)
    meta = {"versao": 1, "emitido_em": "2026-10-01T00:00:00+00:00", "emitido_por": "x",
            "chave_publica": base64.b64encode(publica_de_privada(chave)).decode()}
    conteudo = tomli_w.dumps({"_meta": meta, "retencao": {"langfuse_dias": 30}}).encode()
    estado = verificar_conteudo(conteudo, assinar_bytes(conteudo, chave),
                                ancora=impressao_chave_publica(publica_de_privada(chave)))
    assert estado.situacao == "invalido" and "validade_ate" in estado.motivo


def test_arquivo_antigo_assinado_nao_volta(tmp_path, ambiente_limpo, monkeypatch):
    par = _Par()
    v3 = par.publicar(tmp_path, {"langfuse.captura": "metadados"}, versao=3, nome="v3.toml")
    v1 = par.publicar(tmp_path, {"langfuse.captura": "completo"}, versao=1, nome="v1.toml")
    monkeypatch.setenv("ADEX_ADMIN_ANCORA", par.ancora)
    _apontar(ambiente_limpo, v3)
    assert carregar().admin.valido
    _apontar(ambiente_limpo, v1)
    efetiva = carregar()
    assert efetiva.admin.situacao == "invalido" and "mais antiga" in efetiva.admin.motivo
    assert efetiva.config.langfuse.captura == Config().langfuse.captura


def test_na_oci_a_ancora_do_ambiente_nao_vale(tmp_path, ambiente_limpo, monkeypatch):
    par = _Par()
    arq = par.publicar(tmp_path, {"retencao.langfuse_dias": 30})
    monkeypatch.setenv("ADEX_ADMIN_ANCORA", par.ancora)  # qualquer dev geraria a própria
    monkeypatch.setenv("NB_SESSION_OCID", "ocid1.datasciencenotebooksession.oc1..teste")
    _apontar(ambiente_limpo, arq)
    efetiva = carregar()
    assert efetiva.admin.situacao == "sem_ancora"
    from adex_copiloto import AdexCopiloto

    with pytest.raises(ConfiguracaoAdminErro):
        AdexCopiloto.from_env(verificar_hub=False)


# --- 5. Langfuse e fila: erro e comentário respeitam a captura ------------------------------------------


class _Destino:
    def __init__(self) -> None:
        self.obs, self.notas = [], []

    def iniciar(self, obs): ...

    def finalizar(self, obs):
        self.obs.append(obs)

    def score(self, nome, valor, tipo, comentario, obs, trace_id):
        self.notas.append(comentario)

    def flush(self): ...


@pytest.mark.parametrize("captura", ["mascarado", "metadados"])
def test_erro_e_comentario_mascarados(captura):
    destino = _Destino()
    t = Telemetria("EST-1", Orcamento(Config().orcamento), destinos=[destino], captura=captura)
    with pytest.raises(KeyError):
        with t.observar("passo"):
            raise KeyError("cliente joao@x.com cpf 529.982.247-25")
    t.score("decisao", "D-01:a", comentario="liguei para joao@x.com")
    status = destino.obs[0].status
    assert "joao@x.com" not in status and "529.982.247-25" not in status
    assert "joao@x.com" not in (destino.notas[0] or "")
    if captura == "metadados":
        assert status == "KeyError" and destino.notas[0] is None


# --- 6. dossiê: corte do fim e cadeia refeita sem a chave -----------------------------------------------


def _dossie(pasta, chave=b"k" * 32, n=4):
    d = Dossie(pasta, chave=chave)
    for i in range(n):
        d.registrar("uso_llm", {"tokens_entrada": 100, "custo_usd": 0.5, "i": i}, ator="sistema")
    return d


def test_dossie_com_chave_reabre_e_detecta_corte_do_fim(tmp_path):
    d = _dossie(tmp_path / "EST-1")
    assert len(Dossie(tmp_path / "EST-1", chave=b"k" * 32)) == 4
    linhas = d.caminho.read_text(encoding="utf-8").splitlines()
    d.caminho.write_text("\n".join(linhas[:2]) + "\n", encoding="utf-8")  # apaga os 2 últimos usos
    with pytest.raises(IntegridadeErro, match="faltam eventos"):
        Dossie(tmp_path / "EST-1", chave=b"k" * 32)


def test_dossie_refeito_sem_a_chave_e_detectado(tmp_path):
    d = _dossie(tmp_path / "EST-1")
    # o atacante recria a cadeia (e o selo) sem os usos, com sha256 puro
    d.caminho.unlink()
    d.caminho_selo.unlink()
    Dossie(tmp_path / "EST-1").registrar("estudo_criado", {"objetivo": "x"}, ator="dev")
    with pytest.raises(IntegridadeErro):
        Dossie(tmp_path / "EST-1", chave=b"k" * 32)


def test_dossie_sem_selo_e_recusado(tmp_path):
    d = _dossie(tmp_path / "EST-1")
    d.caminho_selo.unlink()
    with pytest.raises(IntegridadeErro, match="selo"):
        Dossie(tmp_path / "EST-1", chave=b"k" * 32)


def test_dossie_aceita_queda_entre_evento_e_selo(tmp_path):
    d = _dossie(tmp_path / "EST-1", n=2)
    selo_antigo = d.caminho_selo.read_text(encoding="utf-8")
    d.registrar("decisao", {"id": "D-01"}, ator="dev")
    d.caminho_selo.write_text(selo_antigo, encoding="utf-8")  # o selo ficou um evento atrás
    assert len(Dossie(tmp_path / "EST-1", chave=b"k" * 32)) == 3


# --- 7. redação ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("texto,segredo", [
    ('{"api_key": "abcd1234efgh5678"}', "abcd1234efgh5678"),
    ("cpf 529982247-25", "529982247-25"),
    ("cpf 529 982 247 25", "529 982 247 25"),
    ("fone (11) 9 8765-4321", "8765-4321"),
    ("fone +55-11-98765-4321", "98765-4321"),
    ("fone (011) 98765-4321", "98765-4321"),
    ("fone 98765-4321", "98765-4321"),
    ("aws AKIAIOSFODNN7EXAMPLE", "AKIAIOSFODNN7EXAMPLE"),
    ("jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N", "eyJhbGciOiJIUzI1NiJ9"),
    ("git ghp_abcdefghijklmnopqrstuvwxyz0123", "ghp_abcdefghijklmnopqrstuvwxyz0123"),
    ("Authorization: Basic dXNlcjpwYXNzd29yZDEyMw==", "dXNlcjpwYXNzd29yZDEyMw=="),
])
def test_redacao_pega_formatos(texto, segredo):
    assert segredo not in mascarar_texto(texto)


@pytest.mark.parametrize("texto", ["1234567890", "pedido 2026102112", "R$ 1.234.567.890", "123.456.789.012", "2024-2025",
                                   "cidade 3304557 com 12.345 clientes"])
def test_redacao_nao_mascara_numero_de_negocio(texto):
    assert mascarar_texto(texto) == texto


def test_redacao_de_estruturas():
    saida = mascarar({"senha": "abc", "token": 1234567, "x@y.com": 1, "conj": {"a@b.com"}, "b": b"cpf 529.982.247-25"})
    assert saida["senha"] == "▇▇▇" and saida["token"] == "▇▇▇"
    assert "x@y.com" not in saida and saida["conj"] == {"▇▇▇"}
    assert b"529" not in saida["b"]


def test_logs_de_loggers_filhos_e_tracebacks_saem_mascarados(caplog):
    instalar_filtro_logging()
    log = logging.getLogger("adex_copiloto.langfuse")
    with caplog.at_level(logging.INFO, logger="adex_copiloto.langfuse"):
        log.info("cliente %s", "joao@x.com")
        try:
            raise ValueError("cpf 529.982.247-25")
        except ValueError:
            log.exception("falhou")
    texto = caplog.text + "".join(r.exc_text or "" for r in caplog.records)
    assert "joao@x.com" not in texto and "529.982.247-25" not in texto


# --- 8. retenção ---------------------------------------------------------------------------------------


def test_retencao_nao_quebra_com_data_sem_fuso_e_apaga_checkpoint_configurado(tmp_path):
    cfg = Config()
    cfg.checkpoint.caminho = "{estado}/ck/{estudo}.sqlite"
    est = tmp_path / "estudos" / "EST-1"
    est.mkdir(parents=True)
    ck = tmp_path / "ck" / "EST-1.sqlite"
    ck.parent.mkdir()
    for s in ("", "-wal", "-shm"):
        (ck.parent / (ck.name + s)).write_bytes(b"x")
    velho = (dt.datetime.now() - dt.timedelta(days=60)).isoformat()  # sem fuso
    (est / "meta.json").write_text(json.dumps({"handoff_em": velho, "atualizado_em": "lixo"}), encoding="utf-8")
    rel = retencao.aplicar_local(cfg, tmp_path)
    assert len(rel["checkpoints"]) == 3 and not any(ck.parent.iterdir())


def test_retencao_so_apaga_copia_local_entregue_com_snapshot(tmp_path):
    cfg = Config()
    antigo = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=cfg.retencao.dossie_local_dias + 5)).isoformat()
    for nome, meta in {"EST-1": {"snapshot_ok": True, "snapshot_portao": "plano"},
                       "EST-2": {"snapshot_ok": True, "snapshot_portao": "handoff"}}.items():
        p = tmp_path / "estudos" / nome
        p.mkdir(parents=True)
        (p / "meta.json").write_text(json.dumps({**meta, "atualizado_em": antigo}), encoding="utf-8")
    rel = retencao.aplicar_local(cfg, tmp_path)
    assert [p.split("\\")[-1].split("/")[-1] for p in rel["dossies_locais"]] == ["EST-2"]


def test_purga_do_langfuse_recusa_dias_invalidos():
    with pytest.raises(ValueError):
        retencao.purgar_langfuse(Config(), dias=0)
    with pytest.raises(ValueError):
        retencao.purgar_langfuse(Config(), dias=-1)
