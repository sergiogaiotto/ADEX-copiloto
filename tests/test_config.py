from __future__ import annotations

import datetime as dt

import pytest

from adex_copiloto.config import carregar
from adex_copiloto.config.configuracoes import (
    carregar_admin,
    gerar_par_chaves,
    carregar_chave_privada,
    impressao_chave_publica,
    montar_arquivo,
    verificar_conteudo,
)
from adex_copiloto.config.modelos import Config
from adex_copiloto.erros import ConfigErro


def _admin(tmp_path, valores, *, versao=3, validade=None):
    pem, publica = gerar_par_chaves()
    chave = carregar_chave_privada(pem)
    conteudo, assinatura = montar_arquivo(valores, chave, versao=versao, emitido_por="admin@teste", validade_ate=validade)
    arq = tmp_path / "admin" / "configuracoes.toml"
    arq.parent.mkdir(parents=True, exist_ok=True)
    arq.write_bytes(conteudo)
    (arq.parent / "configuracoes.toml.sig").write_bytes(assinatura)
    return arq, impressao_chave_publica(publica), conteudo, assinatura


def test_padroes_sem_arquivos():
    efetiva = carregar()
    assert efetiva.config.hub.modelo == "qwen-latest"
    assert efetiva.config.hub.base_url == "https://hub-gpus.usto.re/v1"
    assert efetiva.admin.situacao == "ausente"
    assert efetiva.config.retencao.langfuse_dias == 90
    assert efetiva.config.langfuse.habilitado is False
    assert any("Admin" in a for a in efetiva.avisos)
    # perfis padrão: top_k sempre 20 e nada de "high"
    for nome, perfil in efetiva.config.perfis.items():
        assert perfil.top_k == 20, nome
        assert perfil.esforco in (None, "low", "medium", "xhigh")


def test_projeto_sobrescreve_e_registra_origem(ambiente_limpo):
    (ambiente_limpo / "adex.toml").write_text('[hub]\ntimeout_s = 240\n', encoding="utf-8")
    efetiva = carregar()
    assert efetiva.config.hub.timeout_s == 240
    assert efetiva.origem("hub.timeout_s").startswith("projeto:")


def test_projeto_nao_altera_chave_do_admin(ambiente_limpo):
    (ambiente_limpo / "adex.toml").write_text(
        '[retencao]\nlangfuse_dias = 9999\n[langfuse]\nhabilitado = true\n', encoding="utf-8"
    )
    efetiva = carregar()
    assert efetiva.config.retencao.langfuse_dias == 90
    assert efetiva.config.langfuse.habilitado is False
    assert any("retencao.langfuse_dias" in a and "só o Admin" in a for a in efetiva.avisos)


def test_impressoes_sao_do_admin_em_qualquer_secao(ambiente_limpo):
    (ambiente_limpo / "adex.toml").write_text(
        '[hub.chave]\nimpressao = "' + "a" * 64 + '"\nsalt = "x"\n', encoding="utf-8"
    )
    efetiva = carregar()
    assert efetiva.config.hub.chave.impressao == ""
    assert efetiva.config.hub.chave.salt == ""


def test_variavel_de_ambiente(monkeypatch):
    monkeypatch.setenv("ADEX__HUB__TIMEOUT_S", "200")
    monkeypatch.setenv("ADEX__ADW__ESQUEMAS", '["CAD", "OPS_RES"]')
    efetiva = carregar()
    assert efetiva.config.hub.timeout_s == 200
    assert efetiva.config.adw.esquemas == ["CAD", "OPS_RES"]
    assert efetiva.origem("hub.timeout_s") == "ambiente"


def test_dev_so_aperta(ambiente_limpo):
    (ambiente_limpo / "adex.toml").write_text(
        '[data_policy]\nmin_group_size = 100\n[orcamento]\ncusto_usd_por_estudo = 999\n', encoding="utf-8"
    )
    efetiva = carregar()
    assert efetiva.config.data_policy.min_group_size == 100
    assert "apertado" in efetiva.origem("data_policy.min_group_size")
    assert efetiva.config.orcamento.custo_usd_por_estudo == 25.0  # afrouxar é ignorado
    assert any("afrouxaria" in a for a in efetiva.avisos)


def test_dev_nao_afrouxa_grupo_minimo(ambiente_limpo):
    (ambiente_limpo / "adex.toml").write_text('[data_policy]\nmin_group_size = 10\n', encoding="utf-8")
    assert carregar().config.data_policy.min_group_size == 50


def test_chave_desconhecida_vira_erro(ambiente_limpo):
    (ambiente_limpo / "adex.toml").write_text('[hub]\ntimeout = 3\n', encoding="utf-8")
    with pytest.raises(ConfigErro) as e:
        carregar()
    assert "hub.timeout" in str(e.value)


def test_admin_valido_aplica_retencao(tmp_path, ambiente_limpo, monkeypatch):
    arq, ancora, *_ = _admin(tmp_path, {"retencao.langfuse_dias": 60, "langfuse.habilitado": True, "hub.timeout_s": 300})
    monkeypatch.setenv("ADEX_ADMIN_ANCORA", ancora)
    (ambiente_limpo / "adex.toml").write_text(f'[admin]\narquivo = "{arq.as_posix()}"\n', encoding="utf-8")
    efetiva = carregar()
    assert efetiva.admin.valido, efetiva.admin.motivo
    assert efetiva.config.retencao.langfuse_dias == 60
    assert efetiva.config.langfuse.habilitado is True
    assert efetiva.origem("retencao.langfuse_dias") == "admin v3"
    # chave comum vinda do Admin vale como padrão da organização e o projeto pode mudar
    assert efetiva.config.hub.timeout_s == 300


def test_projeto_sobrescreve_padrao_da_organizacao(tmp_path, ambiente_limpo, monkeypatch):
    arq, ancora, *_ = _admin(tmp_path, {"hub.timeout_s": 300})
    monkeypatch.setenv("ADEX_ADMIN_ANCORA", ancora)
    (ambiente_limpo / "adex.toml").write_text(
        f'[admin]\narquivo = "{arq.as_posix()}"\n[hub]\ntimeout_s = 90\n', encoding="utf-8"
    )
    assert carregar().config.hub.timeout_s == 90


def test_admin_adulterado_recua_para_restritivo(tmp_path, ambiente_limpo, monkeypatch):
    arq, ancora, conteudo, _ = _admin(tmp_path, {"retencao.langfuse_dias": 400})
    arq.write_bytes(conteudo.replace(b"400", b"900"))
    monkeypatch.setenv("ADEX_ADMIN_ANCORA", ancora)
    (ambiente_limpo / "adex.toml").write_text(f'[admin]\narquivo = "{arq.as_posix()}"\n', encoding="utf-8")
    efetiva = carregar()
    assert efetiva.admin.situacao == "invalido"
    assert "assinatura" in efetiva.admin.motivo
    assert efetiva.config.retencao.langfuse_dias == 90


def test_admin_com_outra_chave(tmp_path, ambiente_limpo, monkeypatch):
    arq, _, *_ = _admin(tmp_path, {"retencao.langfuse_dias": 30})
    _, outra = gerar_par_chaves()
    monkeypatch.setenv("ADEX_ADMIN_ANCORA", impressao_chave_publica(outra))
    (ambiente_limpo / "adex.toml").write_text(f'[admin]\narquivo = "{arq.as_posix()}"\n', encoding="utf-8")
    efetiva = carregar()
    assert efetiva.admin.situacao == "invalido"
    assert "não é a do Admin" in efetiva.admin.motivo


def test_admin_sem_ancora(tmp_path, ambiente_limpo):
    arq, *_ = _admin(tmp_path, {"retencao.langfuse_dias": 30})
    (ambiente_limpo / "adex.toml").write_text(f'[admin]\narquivo = "{arq.as_posix()}"\n', encoding="utf-8")
    efetiva = carregar()
    assert efetiva.admin.situacao == "sem_ancora"
    assert efetiva.config.retencao.langfuse_dias == 90


def test_admin_vencido(tmp_path):
    _, ancora, conteudo, assinatura = _admin(tmp_path, {"retencao.langfuse_dias": 30}, validade="2026-01-01T00:00:00+00:00")
    estado = verificar_conteudo(
        conteudo, assinatura, ancora=ancora, agora=dt.datetime(2026, 10, 21, tzinfo=dt.timezone.utc)
    )
    assert estado.situacao == "invalido" and "venceram" in estado.motivo


def test_admin_sem_assinatura(tmp_path):
    _, ancora, conteudo, _ = _admin(tmp_path, {"retencao.langfuse_dias": 30})
    assert verificar_conteudo(conteudo, None, ancora=ancora).situacao == "invalido"


def test_admin_fora_do_esquema_nao_assina():
    pem, _ = gerar_par_chaves()
    with pytest.raises(Exception):
        montar_arquivo({"retencao.langfuse_dias": -5}, carregar_chave_privada(pem), versao=1, emitido_por="x")


def test_admin_arquivo_inexistente_recua(ambiente_limpo):
    cfg = Config()
    cfg.admin.arquivo = str(ambiente_limpo / "nao-existe.toml")
    estado = carregar_admin(cfg)
    assert estado.situacao == "invalido"


def test_hash_da_config_estavel():
    a, b = carregar(), carregar()
    assert a.hash() == b.hash()
    assert len(a.linhas()) > 50
