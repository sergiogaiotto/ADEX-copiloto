"""Porta de entrada: ``AdexCopiloto``.

    from adex_copiloto import AdexCopiloto
    adex = AdexCopiloto.from_env()
    estudo = adex.create_session(objective="...", mode="exploration")
    estudo.frame()

Sem código: ``adex.start()`` abre o mesmo fluxo num painel.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
from pathlib import Path
from typing import Any

from adex_copiloto.blocos import REGISTRO, bloco_do_dev
from adex_copiloto.config import carregar
from adex_copiloto.config.carregador import ConfigEfetiva, diretorio_estado, resolver_caminho
from adex_copiloto.config.modelos import Hub
from adex_copiloto.dados.armazenamento import Armazenamento
from adex_copiloto.erros import AdexErro, ConfigErro, ConfiguracaoAdminErro, EstudoErro
from adex_copiloto.estudo.dossie import Dossie
from adex_copiloto.estudo.sessao import Estudo
from adex_copiloto.extensao.ferramentas import RegistroFerramentas
from adex_copiloto.extensao.io import IO_
from adex_copiloto.extensao.playbooks import Atalhos
from adex_copiloto.harness.armazem import ArmazemDados
from adex_copiloto.harness.checkpoint import chave_aes, criar_checkpointer
from adex_copiloto.harness.grafo import construir
from adex_copiloto.harness.nos import Nos, Servicos, semente, usuario_atual
from adex_copiloto.observabilidade.orcamento import Orcamento
from adex_copiloto.observabilidade.retencao import aplicar_local
from adex_copiloto.observabilidade.telemetria import Telemetria
from adex_copiloto.seguranca.redacao import instalar_filtro_logging
from adex_copiloto.seguranca.segredos import Segredo, conferir_endpoint_hub, ler_segredo
from adex_copiloto.ui import html as H
from adex_copiloto.ui.paineis import Acao, EntradaTexto, Painel

log = logging.getLogger("adex_copiloto")

URL_OFICIAL = Hub().base_url


class AdexCopiloto:
    def __init__(
        self,
        efetiva: ConfigEfetiva,
        *,
        chave: Segredo | None = None,
        http_client=None,
        cliente_openai=None,
        fonte=None,
        aplicar_retencao: bool = True,
    ) -> None:
        self.efetiva = efetiva
        self.config = efetiva.config
        self.pasta_estado = diretorio_estado(self.config)
        self._chave = chave
        self._http = http_client
        self._oa = cliente_openai
        self._fonte_padrao = fonte
        self.armazenamento = Armazenamento(self.config, self.pasta_estado)
        self.io = IO_(self.armazenamento)
        self.atalhos = Atalhos(self.pasta_estado)
        self.blocks = REGISTRO
        self.ferramentas = RegistroFerramentas(self.io)
        self.lenses = _Lentes(self)
        self.tools = _ToolsAdex(self)
        self.avisos = list(efetiva.avisos)
        self.situacao_modelo = None
        self.retencao_aplicada = aplicar_local(self.config, self.pasta_estado) if aplicar_retencao else {}

    # --- criação ----------------------------------------------------------------------------------

    @classmethod
    def from_env(cls, arquivo: str | None = None, *, verificar_hub: bool = True, **sobrescritas) -> "AdexCopiloto":
        """Carrega configuração (padrão < Admin < projeto < local < ambiente < código) e confere segredos."""
        efetiva = carregar(arquivo=arquivo, sobrescritas=sobrescritas or None)
        instalar_filtro_logging()
        cfg = efetiva.config
        from adex_copiloto.config.configuracoes import em_oci

        if em_oci(cfg) and not efetiva.admin.valido:
            raise ConfiguracaoAdminErro(
                "Na OCI, o copiloto só abre com as Configurações do Admin confirmadas: " + efetiva.admin.motivo,
                explicacao=("Sem elas, valeriam os padrões do pacote, que podem ser mais frouxos que os da "
                            "organização (retenção, captura no Langfuse, grupo mínimo)."),
                como_resolver="Peça ao Admin para publicar (ou renovar) as Configurações: adex-configuracoes publicar.",
            )
        conferir_endpoint_hub(cfg)
        chave = ler_segredo(cfg.hub.chave, "hub.chave", cfg)
        inst = cls(efetiva, chave=chave)
        if verificar_hub and cfg.hub.verificar_modelo:
            try:
                inst.verificar_modelo()
            except AdexErro as e:
                if type(e).__name__ == "ModeloTrocadoErro":
                    raise
                inst.avisos.append(f"Não consegui conferir o modelo agora ({e.mensagem[:160]}).")
        return inst

    def verificar_modelo(self):
        from adex_copiloto.llm.impressao_digital import calcular, consultar_modelos, verificar

        info = consultar_modelos(self.config, self._chave, http_client=self._http)
        imp, det = calcular(info, self.config.hub.modelo)
        self.situacao_modelo = verificar(self.config, imp, det)
        if self.situacao_modelo.situacao != "confere":
            self.avisos.append(self.situacao_modelo.texto())
        return self.situacao_modelo

    # --- sessões --------------------------------------------------------------------------------------

    def _novo_id(self) -> str:
        ano = dt.date.today().year
        pasta = self.pasta_estado / "estudos"
        pasta.mkdir(parents=True, exist_ok=True)
        existentes = [int(m.group(1)) for p in pasta.iterdir() if (m := re.fullmatch(rf"EST-{ano}-(\d{{4}})", p.name))]
        return f"EST-{ano}-{(max(existentes) + 1 if existentes else 1):04d}"

    def _fonte(self, data_sources: Any):
        from adex_copiloto.dados.fontes import FonteADW, FonteDados

        if isinstance(data_sources, FonteDados):
            return data_sources
        if data_sources in (None, "descobrir"):
            if self._fonte_padrao is not None:
                return self._fonte_padrao
            return FonteADW(self.config) if self.config.adw.habilitado else None
        if isinstance(data_sources, (list, tuple)):
            cfg = self.config.model_copy(deep=True)
            cfg.adw.esquemas = [s.upper() for s in data_sources if "." not in s] or cfg.adw.esquemas
            cfg.adw.tabelas_permitidas = [s.upper() for s in data_sources if "." in s]
            if not cfg.adw.esquemas and cfg.adw.tabelas_permitidas:
                cfg.adw.esquemas = sorted({t.split(".")[0] for t in cfg.adw.tabelas_permitidas})
            return FonteADW(cfg) if cfg.adw.habilitado else None
        raise EstudoErro("data_sources deve ser 'descobrir', uma lista de esquemas/tabelas ou uma fonte (ex.: exemplo.fonte(adex.config)).")

    def _servicos(self, estudo_id: str, fonte) -> tuple[Servicos, Any]:
        cfg = self.config
        pasta = self.pasta_estado / "estudos" / estudo_id
        pasta.mkdir(parents=True, exist_ok=True)
        chave_ck = chave_aes(cfg, self.pasta_estado)
        dossie = Dossie(pasta, chave=chave_ck)
        avisos: list[str] = []
        telemetria = Telemetria(
            estudo_id, Orcamento(cfg.orcamento), captura=cfg.langfuse.captura,
            ao_registrar_uso=lambda u: dossie.registrar("uso_llm", u.__dict__, ator="sistema"),
            ao_registrar_consulta=lambda c: dossie.registrar("consulta_recusada", c.__dict__, ator="sistema") if not c.aprovado else None,
            ao_alerta=lambda a: avisos.append(a),
            ancora=lambda: {"dossie_seq": len(dossie), "dossie_hash": dossie.ultimo_hash},
        )
        usos = list(dossie.eventos("uso_llm"))
        if usos:
            telemetria.orcamento.restaurar(
                sum(int(u["dados"].get("tokens_entrada", 0)) + int(u["dados"].get("tokens_saida", 0)) for u in usos),
                sum(float(u["dados"].get("custo_usd") or 0) for u in usos), len(usos),
            )
        langfuse = None
        if cfg.langfuse.habilitado:
            try:
                from adex_copiloto.observabilidade.langfuse_adaptador import DestinoLangfuse

                langfuse = DestinoLangfuse(cfg, estudo_id=estudo_id, pasta_fila=self.pasta_estado / "fila_langfuse")
                telemetria.destinos.append(langfuse)
            except AdexErro as e:
                avisos.append(f"Langfuse indisponível: {e.mensagem}")
                dossie.registrar("langfuse_indisponivel", {"motivo": e.mensagem}, ator="sistema")
        cliente = self._cliente(telemetria)
        ferramentas = self.ferramentas
        ferramentas.dossie = dossie
        servicos = Servicos(
            estudo_id=estudo_id, efetiva=self.efetiva, pasta=pasta, dossie=dossie, armazem=ArmazemDados(pasta / "dados"),
            telemetria=telemetria, cliente=cliente, registro=REGISTRO, atalhos=self.atalhos, fonte=fonte,
            fabrica_chat=self._fabrica_chat(), langfuse=langfuse, armazenamento=self.armazenamento, ferramentas_dev=ferramentas,
        )
        caminho = Path(str(resolver_caminho(cfg, cfg.checkpoint.caminho)).replace("{estudo}", estudo_id))
        grafo = construir(Nos(servicos), criar_checkpointer(caminho, chave_ck))
        servicos.avisos_inicio = avisos  # type: ignore[attr-defined]
        return servicos, grafo

    def _cliente(self, telemetria):
        from adex_copiloto.llm.cliente import ClienteHub

        if self._chave is None and self._oa is None:
            raise ConfigErro("Sem chave do hub: use AdexCopiloto.from_env().")
        return ClienteHub(self.config, self._chave or Segredo("sem-chave", "hub.chave", "teste"), telemetria,
                          http_client=self._http, cliente_openai=self._oa)

    def _fabrica_chat(self):
        from adex_copiloto.llm.chat_langchain import criar_chat_qwen

        chave = self._chave or Segredo("sem-chave", "hub.chave", "teste")

        def fabrica(perfil: str, callbacks=None):
            return criar_chat_qwen(self.config, chave, perfil, http_client=self._http, callbacks=callbacks)

        return fabrica

    def create_session(
        self,
        objective: str,
        vertical: str | None = None,
        data_sources: Any = "descobrir",
        playbook: str | None = None,
        mode: str = "exploration",
    ) -> Estudo:
        """Cria um estudo novo. Nenhum dado é lido e nenhuma IA é chamada até ``estudo.frame()``."""
        if not objective or len(objective.strip()) < 8:
            raise EstudoErro("Descreva o objetivo em uma frase (o que você quer decidir ou entender).")
        if mode not in ("exploration",):
            raise EstudoErro("Só o modo 'exploration' existe no MVP; produção é outra equipe (handoff).")
        estudo_id = self._novo_id()
        servicos, grafo = self._servicos(estudo_id, self._fonte(data_sources))
        entrada = {"estudo_id": estudo_id, "objetivo": objective.strip(), "vertical": vertical, "modo": mode,
                   "criado_em": dt.datetime.now(dt.timezone.utc).isoformat(), "seed": semente(estudo_id), "status": "em_andamento",
                   "playbook_pedido": playbook, "textos_dev": [objective.strip()], "resultados": {}, "afirmacoes": [], "decisoes": [],
                   "lacunas": [], "metricas": {}, "ids": {}, "portoes": [], "validade": {}}
        servicos.dossie.registrar("estudo_criado", {"objetivo": objective, "vertical": vertical, "modo": mode, "playbook": playbook,
                                                    "config_sha256": self.efetiva.hash(), "admin": self.efetiva.admin.situacao,
                                                    "avisos_config": self.efetiva.avisos, "usuario": usuario_atual()}, ator="dev")
        (servicos.pasta / "meta.json").write_text(json.dumps({"estudo_id": estudo_id, "objetivo": objective, "entrada": entrada,
                                                              "criado_em": entrada["criado_em"]}, ensure_ascii=False, indent=1), encoding="utf-8")
        grafo.update_state({"configurable": {"thread_id": estudo_id}}, entrada, as_node="__start__")
        return self._ativar(Estudo(servicos, grafo, ferramentas=self.ferramentas))

    def _ativar(self, estudo: Estudo) -> Estudo:
        from adex_copiloto.ui.magics import usar

        usar(estudo, self)
        return estudo

    def resume(self, estudo_id: str, *, data_sources: Any = "descobrir") -> Estudo:
        """Retoma um estudo (outra sessão do kernel): estado, dossiê e orçamento continuam."""
        pasta = self.pasta_estado / "estudos" / estudo_id
        if not (pasta / "meta.json").exists():
            raise EstudoErro(f"Estudo {estudo_id} não encontrado em {self.pasta_estado / 'estudos'}.")
        servicos, grafo = self._servicos(estudo_id, self._fonte(data_sources))
        servicos.dossie.registrar("estudo_retomado", {"usuario": usuario_atual()}, ator="dev")
        return self._ativar(Estudo(servicos, grafo, ferramentas=self.ferramentas))

    def studies(self) -> list[dict[str, Any]]:
        pasta = self.pasta_estado / "estudos"
        saida = []
        for p in sorted(pasta.glob("EST-*")) if pasta.exists() else []:
            meta = p / "meta.json"
            if meta.exists():
                d = json.loads(meta.read_text(encoding="utf-8"))
                saida.append({k: d.get(k) for k in ("estudo_id", "objetivo", "status", "etapa", "atualizado_em")})
        return saida

    # --- extensão -------------------------------------------------------------------------------------

    def tool(self, *, name: str, reads: list[str] | None = None, returns: dict[str, str] | None = None,
             privacy: str = "aggregate", scope: str = "study", exemplo: dict[str, Any] | None = None):
        """Decorador ``@adex.tool``: registra uma ferramenta do dev (testes de contrato antes do uso)."""
        return self.ferramentas.registrar(name=name, reads=reads, returns=returns, privacy=privacy, scope=scope, exemplo=exemplo)

    def block(self, **kw):
        """Decorador ``@adex.block``: registra um bloco de método do dev (versão imutável)."""
        return bloco_do_dev(**kw)

    def exemplo(self, nome: str, **kw):
        from adex_copiloto import exemplos

        return exemplos.carregar(nome, **kw)

    # --- painéis ----------------------------------------------------------------------------------------

    def start(self) -> Painel:
        """Painel sem código: escreva a pergunta e o copiloto enquadra (nenhum dado é lido)."""

        def comecar(texto: str):
            estudo = self.create_session(objective=texto)
            self._ultimo = estudo
            return estudo.frame()

        corpo = ("<p>Escreva sua pergunta do seu jeito. O copiloto resume o que entendeu e faz no máximo 3 perguntas. "
                 "Nenhum dado é lido nesse passo.</p>" + "".join(H.caixa(H.e(a), "alerta") for a in self.avisos[:5]))
        return Painel(H.cartao("ADEX Copiloto", "novo estudo, do zero", corpo),
                      entrada=EntradaTexto("Começar", comecar, "estudo = adex.create_session(objective='...'); estudo.frame()",
                                           placeholder="Ex.: A troca de roteadores por Wi-Fi 6 no ano passado valeu a pena?"))

    def admin(self) -> Painel:
        """Configurações (retenção e governança): leitura para todos; edição só para o Admin."""
        from adex_copiloto.config.configuracoes import ancora_vigente, verificar_grupo_admin

        eh_admin, motivo = verificar_grupo_admin(self.config)
        ancora, origem_ancora = ancora_vigente()
        linhas = [[("🔒 " if l["so_admin"] else "") + l["chave"], json.dumps(l["valor"], ensure_ascii=False)[:80], l["origem"]]
                  for l in self.efetiva.linhas() if l["so_admin"] or l["chave"].startswith(("retencao", "langfuse", "hub."))]
        corpo = (H.caixa(H.e(self.efetiva.admin.resumo()), "" if self.efetiva.admin.valido else "alerta")
                 + f"<p class='sub'>Âncora da assinatura: {H.e(ancora[:16] + '…' if ancora else 'nenhuma')} ({H.e(origem_ancora or '—')}). "
                   f"Grupo Admin: {H.e(self.config.admin.grupo_iam)} — {H.e(motivo)}</p>"
                 + H.tabela(linhas, ["chave (🔒 = só Admin)", "valor", "origem"])
                 + "<h4>Como o Admin altera</h4><ol><li><code>adex-configuracoes modelo --saida configuracoes.toml</code></li>"
                   "<li>edite os valores (retenção, Langfuse, política de dados, orçamento)</li>"
                   "<li><code>adex-configuracoes assinar configuracoes.toml --chave-privada admin.pem</code></li>"
                   "<li><code>adex-configuracoes publicar configuracoes.toml --destino oci://…/adex/admin/configuracoes.toml</code></li></ol>"
                   "<p class='sub'>A garantia vem da assinatura, da permissão de escrita no prefixo do Admin e da retenção aplicada por "
                   "cada armazenamento. Dentro do kernel, a biblioteca pode ser alterada em memória: por isso o dossiê registra o "
                   "sha256 das Configurações usadas.</p>")
        modo = "edição (Admin)" if eh_admin else "somente leitura"
        return Painel(H.cartao("Configurações", modo, corpo))


class _Lentes:
    def __init__(self, adex: AdexCopiloto) -> None:
        self._a = adex

    def list(self) -> list[str]:
        return sorted(self._a.atalhos.lentes)

    def draft(self, estudo: Estudo, nome: str, *, titulo: str = "") -> Painel:
        """Rascunho de lente a partir de um estudo (glossário, KPIs, tabelas, riscos); precisa de curadoria."""
        from adex_copiloto.extensao.playbooks import Lente
        from adex_copiloto.harness import estado as E

        st = estudo._estado()
        m = E.mapeamento(st)
        carta = E.carta(st)
        lente = Lente(
            nome=nome, titulo=titulo or nome, escopo="estudo", curado=False,
            kpis=[d.descricao or d.nome for d in (m.desfechos if m else [])],
            tabelas_comuns=m.tabelas() if m else [],
            riscos_tipicos=[a.texto for a in E.afirmacoes(st) if a.tipo == "hipotese"][:8],
            glossario={},
        )
        caminho = self._a.atalhos.salvar_lente(lente)
        estudo._s.dossie.registrar("lente_rascunho", {"nome": nome, "caminho": str(caminho)}, ator="dev")
        return Painel(H.cartao("Lente (rascunho)", H.e(nome), f"<p>Salva em <code>{H.e(caminho)}</code>. "
                               f"{len(lente.tabelas_comuns)} tabelas, {len(lente.riscos_tipicos)} riscos. Precisa de curadoria antes de "
                               f"ser oferecida à equipe.</p><p class='sub'>Pergunta de origem: {H.e(carta.valor('pergunta_decisao') if carta else '')}</p>"))


class _ToolsAdex:
    def __init__(self, adex: AdexCopiloto) -> None:
        self._a = adex

    def scaffold(self, nome: str, descricao: str = "") -> Painel:
        """Pede ao Qwen um RASCUNHO de ferramenta e coloca na próxima célula. Nunca executa."""
        from adex_copiloto.llm import prompts
        from adex_copiloto.observabilidade.telemetria import TelemetriaNula

        cliente = self._a._cliente(TelemetriaNula(Orcamento(self._a.config.orcamento)))
        msgs = prompts.mensagens("rascunho_ferramenta", prompts.dados(descricao or nome, "dev"), nome=nome, descricao=descricao or nome)
        resp = cliente.completar("pensar", msgs, etapa="extensao", nome="scaffold")
        codigo = resp.texto.strip()
        codigo = re.sub(r"^```(?:python)?\s*|```\s*$", "", codigo, flags=re.M).strip()
        colocado = False
        try:
            from IPython import get_ipython

            ip = get_ipython()
            if ip is not None:
                ip.set_next_input("# RASCUNHO do Qwen: revise antes de rodar\n" + codigo, replace=False)
                colocado = True
        except Exception:  # noqa: BLE001
            pass
        aviso = "O rascunho está na próxima célula." if colocado else "Copie o rascunho abaixo para uma célula."
        return Painel(H.cartao(f"Rascunho de ferramenta: {nome}", "nunca executado pelo copiloto",
                               f"<p>{aviso} Revise, rode e depois teste com <code>estudo.tools.test('{H.e(nome)}')</code>.</p>"
                               f"<pre><code>{H.e(codigo)}</code></pre>"), dados={"codigo": codigo})
