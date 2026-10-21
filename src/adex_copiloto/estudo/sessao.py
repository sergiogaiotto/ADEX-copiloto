"""``Estudo``: o objeto que o dev usa no notebook (uma sessão = um estudo).

Cada método avança o grafo do estudo até a próxima espera ou portão e devolve um
painel. A ordem é garantida pelo grafo: um passo fora de ordem é recusado com
explicação; voltar a uma etapa anterior é permitido; pular um portão, nunca.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from typing import Any

from langgraph.types import Command

from adex_copiloto.critico.checagens import Achado, bloqueia_aceite
from adex_copiloto.erros import AdexErro, DecisaoPendenteErro, EstudoErro, ForaDeOrdemErro, PortaoPendenteErro
from adex_copiloto.estudo.carta import NOMES_CAMPOS, ROTULOS
from adex_copiloto.estudo.mapeamento import Mapeamento, validar_mapeamento
from adex_copiloto.harness import estado as E
from adex_copiloto.harness.nos import Servicos
from adex_copiloto.llm import prompts
from adex_copiloto.llm.marcadores import preencher
from adex_copiloto.ui import html as H
from adex_copiloto.ui.paineis import (
    Acao,
    EntradaTexto,
    Painel,
    afirmacoes_html,
    decisoes_html,
    lacunas_html,
    rodape_uso,
)

log = logging.getLogger("adex_copiloto.estudo")

ORDEM = [
    "enquadrar", "entrevista", "carta", "portao_carta", "aguardar_descoberta", "descobrir", "aguardar_universo", "universo",
    "portao_passaporte", "aguardar_plano", "planejar", "portao_plano", "aguardar_etapas", "etapas", "critico", "portao_aceite",
    "aguardar_handoff", "handoff", "portao_handoff",
]
ESPERAS = {"descobrir": ("aguardar_descoberta", "descobrir"), "universo": ("aguardar_universo", "universo"),
           "plano": ("aguardar_plano", "planejar"), "etapas": ("aguardar_etapas", "etapas"), "handoff": ("aguardar_handoff", "handoff")}
EXPLICA = {
    "entrevista": "o copiloto espera suas respostas às perguntas do enquadramento: estudo.frame(answers='...')",
    "portao_carta": "a carta do estudo espera aprovação: estudo.charter().approve()",
    "aguardar_descoberta": "o próximo passo é descobrir os dados: estudo.discover(hint='...')",
    "aguardar_universo": "o próximo passo é montar o universo: estudo.run('universo')",
    "portao_passaporte": "o Passaporte de Amostragem espera aprovação: estudo.passport().approve()",
    "aguardar_plano": "o próximo passo é planejar: estudo.plan()",
    "portao_plano": "o plano espera aprovação: estudo.plan_view().approve()",
    "aguardar_etapas": "o plano está aprovado: rode as etapas com estudo.run() e depois estudo.review()",
    "portao_aceite": "o estudo espera seu aceite: estudo.accept(reason='...') (ou iterate()/discard())",
    "aguardar_handoff": "o estudo foi aceito: gere o pacote com estudo.export_handoff()",
    "portao_handoff": "o pacote de handoff espera aprovação para publicar: estudo.handoff().approve()",
}


class Estudo:
    def __init__(self, servicos: Servicos, grafo, *, ferramentas=None) -> None:
        self._s = servicos
        self._grafo = grafo
        self.id = servicos.estudo_id
        self.pasta = servicos.pasta
        self._cfg_grafo = {"configurable": {"thread_id": self.id}}
        self.tools = _Ferramentas(self, ferramentas)

    # --- grafo ----------------------------------------------------------------------------------

    def _estado(self) -> dict:
        return self._grafo.get_state(self._cfg_grafo).values or {}

    def _snapshot(self):
        return self._grafo.get_state(self._cfg_grafo)

    def _pendente(self) -> tuple[str | None, dict | None]:
        """(nó onde o estudo está, valor da espera/portão)."""
        st = self._snapshot()
        for t in st.tasks:
            if t.interrupts:
                return t.name, t.interrupts[0].value
        if st.next:
            nome = st.next[0]
            if nome in ESPERAS_OU_PORTOES:  # espera sem interrupção visível (depois de update_state): rematerializa
                self._grafo.invoke(None, self._cfg_grafo, durability="sync")
                return self._pendente()
            return nome, None
        return None, None

    def _invocar(self, entrada: Any, nome: str) -> dict:
        with self._s.telemetria.comando(nome):
            self._grafo.invoke(entrada, self._cfg_grafo, durability="sync")
        self._s.telemetria.flush()
        self._meta()
        return self._estado()

    def _atualizar(self, valores: dict, *, as_node: str | None = None) -> None:
        if as_node is not None and self._estado().get("status") in ("aceito", "iterando"):
            valores = {**valores, "status": "em_andamento"}  # voltar a uma etapa reabre o estudo
        self._grafo.update_state(self._cfg_grafo, valores, as_node=as_node)
        self._pendente()

    def _exigir_aberto(self) -> None:
        status = self._estado().get("status")
        if status in ("descartado", "entregue"):
            raise ForaDeOrdemErro(
                f"O estudo {self.id} já foi {status}: não dá para mudar mais nada nele.",
                como_resolver="Para continuar a investigação, crie um estudo novo (adex.create_session).",
            )

    def _posicao(self) -> int:
        no, _ = self._pendente()
        return ORDEM.index(no) if no in ORDEM else len(ORDEM)

    def _comando(self, espera: str, payload: dict, nome: str) -> dict:
        """Executa um passo que espera comando; permite voltar a ele se já passou."""
        aguardar, trabalho = ESPERAS[espera]
        self._exigir_aberto()
        payload = E.normalizar_payload(payload)
        no, valor = self._pendente()
        if no == aguardar and valor is not None:
            return self._invocar(Command(resume=payload), nome)
        if no == trabalho:  # o passo falhou antes: refaz com o comando novo
            self._grafo.update_state(self._cfg_grafo, {"comando": payload}, as_node=aguardar)
            return self._invocar(None, nome)
        if no is not None and ORDEM.index(no) > ORDEM.index(aguardar):
            valores: dict[str, Any] = {"comando": payload}
            if self._estado().get("status") in ("aceito", "iterando"):
                valores["status"] = "em_andamento"
            self._grafo.update_state(self._cfg_grafo, valores, as_node=aguardar)
            self._s.dossie.registrar("volta", {"de": no, "para": trabalho}, ator="dev")
            return self._invocar(None, nome)
        if no is None:
            raise ForaDeOrdemErro(f"O estudo terminou (situação: {self._estado().get('status') or 'concluído'}).")
        raise ForaDeOrdemErro(
            f"Ainda não dá para '{espera}': {EXPLICA.get(no, f'o estudo está em {no}')}.",
            como_resolver=EXPLICA.get(no, ""),
        )

    def _meta(self) -> None:
        p = self.pasta / "meta.json"
        dados = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        st = self._estado()
        dados.update({"estudo_id": self.id, "objetivo": st.get("objetivo"), "status": st.get("status", "em_andamento"),
                      "etapa": st.get("etapa"), "atualizado_em": dt.datetime.now(dt.timezone.utc).isoformat()})
        p.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")

    # --- 1-2. enquadrar e carta -------------------------------------------------------------------

    def frame(self, answers: str | None = None) -> Painel:
        """Enquadra o pedido (sem ler dados). Com ``answers``, monta a carta do estudo."""
        no, _ = self._pendente()
        if no == "enquadrar":
            self._invocar(None, "frame")
            no, _ = self._pendente()
        if answers is None:
            return self._painel_enquadramento()
        if no == "entrevista":
            self._invocar(Command(resume={"respostas": answers}), "frame:respostas")
        elif no == "carta":
            textos = [*(self._estado().get("textos_dev") or []), answers]
            self._grafo.update_state(self._cfg_grafo, {"textos_dev": textos}, as_node="entrevista")
            self._invocar(None, "frame:respostas")
        else:
            raise ForaDeOrdemErro("A carta já foi montada.", como_resolver="Para mudar, use estudo.charter().edit(campo, valor).")
        return self.charter()

    def charter(self) -> "CartaView":
        if not self._estado().get("carta"):
            raise ForaDeOrdemErro("Ainda não há carta.", como_resolver="Rode estudo.frame() e responda com estudo.frame(answers='...').")
        return CartaView(self)

    # --- 3. descoberta ------------------------------------------------------------------------------

    def discover(self, hint: str | None = None) -> Painel:
        """Descobre quais tabelas respondem à carta (só metadados)."""
        self._exigir_carta_aprovada()
        self._comando("descobrir", {"comando": "descobrir", "pista": hint or ""}, "discover")
        return self._painel_descoberta()

    def mapping(self) -> "MapeamentoView":
        if not self._estado().get("mapeamento"):
            raise ForaDeOrdemErro("Ainda não há mapeamento.", como_resolver="Rode estudo.discover().")
        return MapeamentoView(self)

    def confirm_mapping(self, reason: str = "conferido no painel de descoberta") -> Painel:
        st = self._estado()
        d = next((d for d in E.decisoes(st) if d.dados.get("efeito") == "confirmar_mapeamento" and d.status == "aberta"), None)
        if d is not None:
            return self.decide(d.id, "a", reason=reason)
        m = E.mapeamento(st)
        if m is None:
            raise EstudoErro("Ainda não há mapeamento.", como_resolver="Rode estudo.discover().")
        if m.status == "confirmado":
            raise EstudoErro("O mapeamento já está confirmado.")
        # depois de "Revisar" ou de mapping().set(): confirma a versão editada
        self._exigir_aberto()
        m.status, m.confirmado_por = "confirmado", self._s.usuario
        self._s.dossie.registrar("mapeamento_confirmado", {"motivo": reason}, ator="dev")
        self._atualizar({"mapeamento": E.dump(m)})
        return Painel(H.cartao("Mapeamento confirmado", "", f"<p>Motivo registrado: {H.e(reason)}</p><p>Próximo: estudo.run('universo')</p>"))

    # --- 4. universo e passaporte ----------------------------------------------------------------------

    def run(self, step: str | list[str] | None = None, **params) -> Painel:
        """``run('universo')`` monta o universo; ``run()`` ou ``run('passo')`` roda etapas do plano.

        ``run('passo', parametro=...)`` muda o plano aprovado: gera uma versão nova, que precisa
        ser aprovada antes de rodar (o plano aprovado nunca muda por baixo).
        """
        params = E.normalizar_payload(params)
        if step == "universo":
            self._exigir_carta_aprovada()
            abertas = E.decisoes_abertas(self._estado(), "universo")
            if abertas:
                raise DecisaoPendenteErro(
                    "Antes do universo: " + "; ".join(f"{d.id} ({d.titulo})" for d in abertas),
                    como_resolver=f"estudo.decide('{abertas[0].id}', '{abertas[0].recomendada or abertas[0].opcoes[0].codigo}', reason='...')",
                )
            self._comando("universo", {"comando": "universo", "ondas": params.get("ondas")}, "run:universo")
            return self._painel_universo()
        plano = E.plano(self._estado())
        if params:
            if not isinstance(step, str):
                raise EstudoErro("Parâmetros só valem para um passo: estudo.run('passo', parametro=valor).")
            if plano is None or step not in {x.id for x in plano.passos}:
                raise EstudoErro(f"Passo fora do plano: {step}.")
            atual = plano.passo(step).parametros
            if any(atual.get(k) != v for k, v in params.items()):
                vista = self.plan_view().set(step, **params)
                return Painel(H.cartao(
                    "O plano mudou", f"v{vista.plano.versao} · aguardando aprovação",
                    f"<p>Mudar parâmetro de <b>{H.e(step)}</b> muda o plano aprovado. Revise com "
                    "<code>estudo.plan_view().diff()</code>, aprove com <code>estudo.plan_view().approve()</code> "
                    f"e rode de novo com <code>estudo.run('{H.e(step)}')</code>.</p>"))
        if plano is None or not plano.aprovado:
            raise PortaoPendenteErro("O plano precisa estar aprovado antes das etapas.", como_resolver="estudo.plan() e depois aprove o plano.")
        passos = [step] if isinstance(step, str) else (list(step) if step else None)
        if passos:
            desconhecidos = [p for p in passos if p not in {x.id for x in plano.passos}]
            if desconhecidos:
                raise EstudoErro(f"Passos fora do plano: {desconhecidos}. Passos: {[x.id for x in plano.passos]}")
        payload = {"comando": "etapas", "passos": passos}
        antes = set((self._estado().get("resultados") or {}).keys())
        self._comando("etapas", payload, f"run:{step or 'todas'}")
        st = self._estado()
        novos = [p for p in (passos or [x.id for x in E.plano(st).passos]) if p in (st.get("resultados") or {})
                 and (p not in antes or passos)]
        return self._painel_resultados(novos or passos or [])

    def passport(self) -> "PassaporteView":
        if not self._estado().get("passaporte"):
            raise ForaDeOrdemErro("Ainda não há passaporte.", como_resolver="Rode estudo.run('universo').")
        return PassaporteView(self)

    # --- 5. plano --------------------------------------------------------------------------------------

    def plan(self, alternatives: bool = True, *, playbook: str | None = None, reuse: bool = False) -> "PlanoView":
        """Monta o plano (jeitos de responder + método por blocos + pré-mortem)."""
        no, _ = self._pendente()
        if no == "portao_passaporte":
            raise PortaoPendenteErro("O Passaporte de Amostragem espera aprovação.", como_resolver="estudo.passport().approve()")
        self._comando("plano", {"comando": "plano", "alternativas": alternatives, "playbook": playbook, "reutilizar": reuse}, "plan")
        return PlanoView(self)

    def plan_view(self) -> "PlanoView":
        return PlanoView(self)

    # --- 7. crítica, aceite e handoff ------------------------------------------------------------------

    def review(self) -> Painel:
        """Crítico (Python + Qwen em chamada separada) e narrativa com números marcados."""
        self._comando("etapas", {"comando": "revisar"}, "review")
        return self._painel_critica()

    def accept(self, reason: str) -> Painel:
        no, _ = self._pendente()
        if no != "portao_aceite":
            raise ForaDeOrdemErro(f"O aceite vem depois da revisão: {EXPLICA.get(no, no)}.")
        st = self._estado()
        pendentes = bloqueia_aceite([Achado.model_validate(a) for a in st.get("achados") or []], E.decisoes(st))
        if pendentes:
            raise PortaoPendenteErro(
                "Ainda há bloqueios: " + "; ".join(f"{a.id} ({a.severidade}): {a.texto[:80]}" for a in pendentes[:6]),
                como_resolver="Corrija os bloqueios do Python ou registre a decisão de aceitar cada alerta (estudo.decide).",
            )
        self._invocar(Command(resume={"acao": "aceitar", "motivo": reason, "por": self._s.usuario}), "accept")
        return self.show()

    def discard(self, reason: str) -> Painel:
        self._exigir_no("portao_aceite")
        self._invocar(Command(resume={"acao": "descartar", "motivo": reason}), "discard")
        return self.show()

    def iterate(self) -> Painel:
        self._exigir_no("portao_aceite")
        self._invocar(Command(resume={"acao": "iterar"}), "iterate")
        return self.show()

    def export_handoff(self, destino: str | None = None, *, incluir_dados: str = "nenhum") -> "HandoffView":
        """Gera o pacote independente (spec, seção 14). Publicar exige aprovação: .approve()."""
        if incluir_dados not in ("nenhum", "agregados"):
            raise EstudoErro("incluir_dados deve ser 'nenhum' ou 'agregados' (só resultados agregados com grupo mínimo).")
        self._comando("handoff", {"comando": "handoff", "destino": destino, "incluir_dados": incluir_dados}, "export_handoff")
        return HandoffView(self)

    def handoff(self) -> "HandoffView":
        return HandoffView(self)

    # --- decisões, conversa, custos --------------------------------------------------------------------

    def decide(self, decision_id: str, option: str, reason: str) -> Painel:
        st = self._estado()
        decisoes = E.decisoes(st)
        d = next((x for x in decisoes if x.id == decision_id), None)
        if d is None:
            raise EstudoErro(f"Decisão {decision_id} não existe. Abertas: {[x.id for x in decisoes if x.status == 'aberta']}")
        if d.status == "decidida":
            raise EstudoErro(f"{decision_id} já foi decidida ({d.escolha}).")
        self._exigir_aberto()
        d.decidir(option, reason, self._s.usuario)
        valores: dict[str, Any] = {"decisoes": E.dump(decisoes)}
        efeito = d.dados.get("efeito")
        m = E.mapeamento(st)
        if efeito == "confirmar_mapeamento" and option == "a" and m is not None:
            m.status, m.confirmado_por = "confirmado", self._s.usuario
            valores["mapeamento"] = E.dump(m)
        elif efeito == "codigos_duvida" and option == "a" and m is not None:
            for des in m.desfechos:
                if des.nome == d.dados.get("desfecho"):
                    des.codigos = list(dict.fromkeys([*des.codigos, *d.dados.get("codigos", [])]))
            valores["mapeamento"] = E.dump(m)
        if d.titulo == E.TITULO_DATAS_FORA:
            # sempre recalculada da última decisão (o pacote de handoff usa a mesma regra)
            self._s.armazem["grupos_excluidos"] = E.grupos_excluidos({"decisoes": valores["decisoes"]})
        self._s.dossie.registrar("decisao", d.model_dump(mode="json"), ator="dev")
        try:
            self._s.telemetria.score("decisao", f"{d.id}:{option}", comentario=reason)
        except Exception:  # noqa: BLE001
            pass
        self._atualizar(valores)
        return Painel(H.cartao(f"{d.id} decidida", H.e(d.titulo), f"<p>Escolha: <b>{H.e(d.opcao_escolhida.rotulo)}</b> · motivo: {H.e(reason)}</p>"))

    def decisions(self) -> Painel:
        st = self._estado()
        linhas = [[d.id, d.titulo, d.status, d.opcao_escolhida.rotulo if d.opcao_escolhida else "", ", ".join(d.bloqueia)] for d in E.decisoes(st)]
        return Painel(H.cartao("Decisões", f"estudo {H.e(self.id)}", H.tabela(linhas, ["", "decisão", "situação", "escolha", "bloqueia"])))

    def chat(self, texto: str) -> Painel:
        """Conversa sobre o estudo. A IA pode pedir ferramentas do dev aprovadas; nada é executado sem regra."""
        st = self._estado()
        marcadores = E.marcadores(st)
        resumo = {"carta": E.carta(st).para_llm() if E.carta(st) else None, "etapa": st.get("etapa"),
                  "resultados": {p: r.get("resumo") for p, r in (st.get("resultados") or {}).items()},
                  "numeros": marcadores.para_llm(), "decisoes_abertas": [d.titulo for d in E.decisoes_abertas(st)]}
        if self.tools.registro:
            self.tools.registro.dossie = self._s.dossie
        ferramentas = self.tools.registro.esquemas_ia() if self.tools.registro else []
        msgs = prompts.mensagens("conversa", prompts.dados(resumo, "estudo") + "\n\nPergunta do dev:\n" + prompts.dados(texto, "dev"),
                                 ferramentas=", ".join(f["function"]["name"] for f in ferramentas) or "nenhuma")
        textos_dev = [*(st.get("textos_dev") or []), texto]
        self._s.dossie.registrar("conversa", {"pergunta": texto}, ator="dev")
        resultados_ferramentas = []
        with self._s.telemetria.comando("chat"):
            for _ in range(3):
                resp = self._s.cliente.completar("rapido" if not ferramentas else "acao", msgs, etapa="conversa", nome="chat",
                                                 ferramentas=ferramentas or None)
                if not resp.chamadas_ferramenta:
                    break
                chamada = resp.chamadas_ferramenta[0]
                try:
                    args = json.loads(chamada["argumentos"] or "{}")
                    saida = self.tools.registro.chamar(chamada["nome"], args)
                    conteudo = prompts.dados(saida.to_dict(orient="records"), f"ferramenta:{chamada['nome']}")
                    resultados_ferramentas.append((chamada["nome"], saida))
                except AdexErro as e:
                    conteudo = f"A ferramenta recusou: {e.mensagem}"
                msgs = [*msgs, {"role": "assistant", "content": "", "tool_calls": [{"id": chamada["id"], "type": "function",
                                "function": {"name": chamada["nome"], "arguments": chamada["argumentos"]}}]},
                        {"role": "tool", "tool_call_id": chamada["id"], "content": conteudo}]
        self._atualizar({"textos_dev": textos_dev})
        corpo = f"<p>{preencher(H.e(resp.texto), marcadores, html=True)}</p>"
        for nome, df in resultados_ferramentas:
            corpo += f"<h4>Resultado de {H.e(nome)}</h4>" + H.df_html(df)
        return Painel(H.cartao("Conversa com o estudo", H.e(texto[:120]), corpo, rodape=rodape_uso(self._s.telemetria, "conversa")))

    def costs(self) -> Painel:
        linhas = self._s.telemetria.resumo_custos()
        orc = self._s.telemetria.orcamento.situacao()
        corpo = H.tabela([[l["etapa"], l["chamadas"], l["tokens_entrada"], l["tokens_saida"], l["tokens_raciocinio"],
                           f"{l['custo_usd']:.4f}", f"{l['segundos_ia']:.1f}", l.get("consultas", 0), l.get("linhas_lidas", 0)] for l in linhas],
                         ["etapa", "chamadas", "tokens entrada", "tokens saída", "raciocínio", "US$", "s IA", "consultas", "linhas"])
        corpo += H.caixa(f"Orçamento do Admin: {orc['tokens']:,} de {orc['tokens_limite']:,} tokens · {orc['chamadas']} de "
                         f"{orc['chamadas_limite']} chamadas · US$ {orc['custo_usd']:.2f} de {orc['custo_usd_limite']:.2f}".replace(",", "."))
        return Painel(H.cartao("Custos do estudo", f"estudo {H.e(self.id)} (sessão atual + histórico do dossiê)", corpo))

    def show(self) -> Painel:
        st = self._estado()
        no, _ = self._pendente()
        carta = E.carta(st)
        portoes = H.tabela([[p["portao"], p["acao"], p["por"], p["em"]] for p in st.get("portoes") or []], ["portão", "ação", "por", "em"])
        corpo = (f"<p><b>Objetivo:</b> {H.e(st.get('objetivo'))}</p>"
                 f"<p><b>Situação:</b> {H.tag(st.get('status', 'em andamento'))} · <b>agora:</b> {H.e(EXPLICA.get(no, no or 'concluído'))}</p>"
                 + (f"<p><b>Carta:</b> v{carta.versao} ({'aprovada' if carta.aprovada else 'rascunho'})</p>" if carta else "")
                 + "<h4>Portões</h4>" + portoes + decisoes_html(E.decisoes(st)) + lacunas_html(E.lacunas(st)))
        return Painel(H.cartao(f"Estudo {self.id}", "visão geral", corpo, rodape=rodape_uso(self._s.telemetria)))

    def save_as_playbook(self, nome: str, *, titulo: str = "", escopo: str = "estudo") -> Painel:
        from adex_copiloto.extensao.playbooks import playbook_do_estudo

        st = self._estado()
        plano = E.plano(st)
        if plano is None:
            raise ForaDeOrdemErro("Não há plano para virar atalho.")
        pb = playbook_do_estudo(nome, carta=E.carta(st), plano=plano, mapeamento=E.mapeamento(st), estudo_id=self.id, titulo=titulo,
                                escopo=escopo)
        caminho = self._s.atalhos.salvar_playbook(pb)
        self._s.dossie.registrar("playbook_salvo", {"nome": nome, "caminho": str(caminho), "escopo": escopo}, ator="dev")
        aviso = "Para a equipe, o atalho precisa de curadoria antes de ser oferecido (curado = false)." if escopo != "estudo" else ""
        return Painel(H.cartao("Atalho salvo", H.e(nome), f"<p>{len(pb.passos)} passos · {len(pb.condicoes)} condições de aderência.</p>"
                                + (H.caixa(H.e(aviso), "alerta") if aviso else "")))

    def liberar_coluna(self, tabela: str, coluna: str, *, reason: str) -> Painel:
        if self._s.fonte is None:
            raise EstudoErro("Sem fonte de dados.")
        c = self._s.fonte.catalogo().liberar(tabela, coluna)
        self._s.dossie.registrar("coluna_liberada", {"tabela": tabela, "coluna": coluna, "motivo": reason, "pii": c.pii}, ator="dev")
        return Painel(H.cartao("Coluna liberada", f"{H.e(tabela)}.{H.e(coluna)}", f"<p>Motivo registrado: {H.e(reason)}</p>"))

    # --- verificações ------------------------------------------------------------------------------------

    def _exigir_carta_aprovada(self) -> None:
        carta = E.carta(self._estado())
        if carta is None or not carta.aprovada:
            raise PortaoPendenteErro("A carta do estudo precisa estar aprovada antes de olhar os dados.",
                                     como_resolver="estudo.charter().approve()")

    def _exigir_no(self, no: str) -> None:
        atual, _ = self._pendente()
        if atual != no:
            raise ForaDeOrdemErro(f"Isto só vale em '{no}': {EXPLICA.get(atual, atual)}.")

    # --- painéis --------------------------------------------------------------------------------------------

    def _painel_enquadramento(self) -> Painel:
        st = self._estado()
        enq = st.get("enquadramento") or {}
        linhas = [[i["item"], i["o_que_entendi"], {"alta": "alta", "media": "média", "baixa": "baixa", "nao_sei": "—"}[i["certeza"]]]
                  for i in enq.get("entendimento", [])]
        perguntas = "".join(f"<li><b>{H.e(p['pergunta'])}</b><br><span class='sub'>Por que pergunto: {H.e(p['por_que_pergunto'])}</span>"
                            + (f"<br>{' · '.join(H.tag(o) for o in p.get('opcoes', []))}" if p.get("opcoes") else "") + "</li>"
                            for p in enq.get("perguntas", []))
        atalhos = H.tabela([[a["titulo"], f"{a['aderencia']:.2f}".replace(".", ","), "serve" if a["serve"] else a["por_que_nao"]]
                            for a in st.get("atalhos") or []], ["atalho", "aderência", "por que (não) serve"])
        corpo = (f"<p class='sub'>Área: {H.e(st.get('vertical') or 'não definida')} · Dados lidos: nenhum · Caminho: do zero</p>"
                 f"<p>{H.e(enq.get('resumo', ''))}</p><h4>Entendi assim</h4>"
                 + H.tabela(linhas, ["item", "o que entendi", "certeza"])
                 + (f"<h4>Antes de abrir qualquer tabela</h4><ol>{perguntas}</ol>" if perguntas else "")
                 + "<h4>Atalhos (opcional)</h4>" + atalhos
                 + "<p class='sub'>Aderência = parte das condições do atalho que batem com o pedido, contada pelo Python. "
                   "Só ofereço atalho acima de 0,70.</p>")
        return Painel(
            H.cartao(f"Novo estudo {self.id}", "enquadramento · nenhum dado lido", corpo, rodape=rodape_uso(self._s.telemetria, "enquadrar")),
            entrada=EntradaTexto("Responder", lambda t: self.frame(answers=t), "estudo.frame(answers='''...''')",
                                 placeholder="Responda do seu jeito, numa rodada só."),
            acoes=[Acao("Prefiro preencher a carta direto", lambda: self.frame(answers=""), "estudo.frame(answers='')")],
        )

    def _painel_descoberta(self) -> Painel:
        st = self._estado()
        d = st.get("descoberta") or {}
        m = st.get("mapeamento") or {}
        cand = H.tabela([[c["tabela"], c["para_que"], H._num(c["linhas"]) if c.get("linhas") is not None else "—",
                          "✓" if c["acesso"] == "ok" else "sem acesso — peça ao dono",
                          ", ".join(c.get("colunas_pessoais") or []) + (" bloqueadas" if c.get("colunas_pessoais") else "")]
                         for c in d.get("candidatas", [])], ["tabela", "para quê na carta", "linhas", "acesso", "dados pessoais"])
        lig = st.get("resultados", {}).get("ligacoes", {})
        codigos = ""
        for nome, lista in (d.get("codigos") or {}).items():
            codigos += f"<h4>O que conta como '{H.e(nome)}'?</h4>" + H.tabela(
                [[c["codigo"], c["descricao"], c["decisao"], c["motivo"]] for c in lista[:40]], ["código", "descrição", "decisão", "motivo"])
        mapa = H.tabela([["população", m.get("populacao")], ["unidade", m.get("unidade")], ["tempo", m.get("tempo")],
                         ["grupos", ", ".join(m.get("grupos") or [])],
                         ["desfechos", ", ".join(f"{x['nome']} ({x['tabela']})" for x in m.get("desfechos") or [])],
                         ["tratamento", (m.get("tratamento") or {}).get("tabela")], ["alvo", (m.get("alvo") or {}).get("tabela")]],
                        ["papel", "proposto"]) if m else "<p>Sem mapeamento válido.</p>"
        avisos = "".join(H.caixa(H.e(a), "alerta") for a in d.get("avisos", []))
        corpo = (f"<p>Li os metadados do catálogo (nome, tipo, comentário mascarado e contagem de linhas). Nunca valores. Modo: "
                 f"{H.e(d.get('modo'))}.</p>" + cand + "<h4>Mapeamento proposto</h4>" + mapa
                 + (f"<h4>Ligações conferidas no banco</h4><p>{preencher(H.e(lig.get('resumo', '')), E.marcadores(st), html=True)}</p>" if lig else "")
                 + codigos + avisos + decisoes_html(E.decisoes(st)) + lacunas_html(E.lacunas(st)))
        aberta = next((x for x in E.decisoes(st) if x.dados.get("efeito") == "confirmar_mapeamento" and x.status == "aberta"), None)
        acoes = [Acao("Confirmar tabelas e códigos", lambda: self.confirm_mapping(), "estudo.confirm_mapping()", principal=True)] if aberta else []
        return Painel(H.cartao("Descoberta de dados", "só metadados", corpo, rodape=rodape_uso(self._s.telemetria, "descobrir")), acoes=acoes)

    def _figuras(self, r: dict) -> str:
        saida = ""
        for f in r.get("figuras") or []:
            p = self.pasta / f["arquivo"]
            if p.exists():
                saida += H.figura(p.read_bytes(), f["descricao"], f["titulo"])
        return saida

    def _bloco_resultado(self, passo: str, st: dict) -> str:
        r = (st.get("resultados") or {}).get(passo)
        if not r:
            return ""
        marcadores = E.marcadores(st)
        status = {"feito": "<span class='ok'>feito</span>", "falhou": "<span class='falha'>falhou</span>",
                  "aguarda_decisao": "<span class='aviso'>espera decisão</span>"}.get(r["status"], r["status"])
        tabelas = ""
        chave = f"tabelas_ia__{passo}"
        if chave in self._s.armazem:
            for nome, df in self._s.armazem[chave].items():
                tabelas += f"<h4>{H.e(nome.replace('_', ' '))}</h4>" + H.df_html(df, max_linhas=15)
        erro = H.caixa(H.e(r.get("erro")), "bloqueio") if r.get("erro") else ""
        avisos = "".join(f"<li>{H.e(a)}</li>" for a in r.get("avisos", []))
        return (f"<h4>{H.e(passo)} · <code>{H.e(r['bloco'])}</code> · {status}</h4>"
                f"<p>{preencher(H.e(r.get('resumo', '')), marcadores, html=True)}</p>" + erro + self._figuras(r) + tabelas
                + (f"<ul class='sub'>{avisos}</ul>" if avisos else ""))

    def _painel_universo(self) -> Painel:
        st = self._estado()
        corpo = "".join(self._bloco_resultado(p, st) for p in ("universo", "qualidade", "tratamento", "plano_amostral"))
        pa = E.passaporte(st)
        acoes = []
        if pa is not None:
            corpo += H.caixa(f"<b>Passaporte de Amostragem {H.e(pa.id)}</b>: {H.e(pa.metodo.replace('_', ' '))}. {H.e(pa.justificativa)}")
            acoes.append(Acao("Aprovar passaporte", lambda: self.passport().approve(), "estudo.passport().approve()", principal=True))
        corpo += decisoes_html(E.decisoes(st)) + lacunas_html(E.lacunas(st))
        return Painel(H.cartao("Universo do estudo", "agregado na fonte", corpo, rodape=rodape_uso(self._s.telemetria, "universo")), acoes=acoes)

    def _painel_resultados(self, passos: list[str]) -> Painel:
        st = self._estado()
        corpo = "".join(self._bloco_resultado(p, st) for p in passos)
        afirmacoes = [a for a in E.afirmacoes(st) if a.bloco and any(st["resultados"].get(p, {}).get("bloco") == a.bloco for p in passos)]
        corpo += afirmacoes_html(afirmacoes, E.marcadores(st)) + decisoes_html(E.decisoes(st))
        pendentes = [p.id for p in E.plano(st).passos if p.status not in ("feito", "pulado")]
        acoes = [Acao("Rodar o resto do plano", lambda: self.run(), "estudo.run()")] if pendentes else [
            Acao("Revisar (crítico)", lambda: self.review(), "estudo.review()", principal=True)]
        return Painel(H.cartao("Etapas do plano", f"pendentes: {', '.join(pendentes) or 'nenhuma'}", corpo,
                               rodape=rodape_uso(self._s.telemetria, "etapas")), acoes=acoes)

    def _painel_critica(self) -> Painel:
        st = self._estado()
        achados = [Achado.model_validate(a) for a in st.get("achados") or []]
        linhas = [[a.id, f"<span class='sev-{a.severidade}'>{a.severidade}</span>", H.e(a.origem), H.e(a.texto), H.e(a.o_que_fazer)] for a in achados]
        narr = st.get("narrativa") or {}
        pendentes = bloqueia_aceite(achados, E.decisoes(st))
        corpo = (f"<h4>Conclusão ({'escrita pela IA, números pelo Python' if narr.get('origem') == 'ia' else 'texto-modelo sem IA'})</h4>"
                 f"<div>{narr.get('html', '')}</div><h4>Crítico</h4>" + H.tabela(linhas, ["", "severidade", "origem", "achado", "o que fazer"], html_ok=True)
                 + afirmacoes_html(E.afirmacoes(st), E.marcadores(st)) + decisoes_html(E.decisoes(st)))
        acoes = [Acao("Aceitar o estudo", lambda: self.accept(reason="aceito no painel"), "estudo.accept(reason='...')", principal=True)] if not pendentes else []
        sub = "sem bloqueios" if not pendentes else f"{len(pendentes)} pendência(s) antes do aceite"
        return Painel(H.cartao("Revisão do estudo", sub, corpo, rodape=rodape_uso(self._s.telemetria, "critico")), acoes=acoes)

    def __repr__(self) -> str:
        no, _ = self._pendente()
        return f"Estudo({self.id}, agora: {EXPLICA.get(no, no or 'concluído')})"

    def _repr_html_(self) -> str:
        return self.show()._repr_html_()


ESPERAS_OU_PORTOES = {"entrevista", "portao_carta", "aguardar_descoberta", "aguardar_universo", "portao_passaporte", "aguardar_plano",
                      "portao_plano", "aguardar_etapas", "portao_aceite", "aguardar_handoff", "portao_handoff"}


class CartaView:
    def __init__(self, estudo: Estudo) -> None:
        self._e = estudo

    @property
    def carta(self):
        return E.carta(self._e._estado())

    def approve(self) -> Painel:
        no, _ = self._e._pendente()
        if no != "portao_carta":
            raise ForaDeOrdemErro(f"A carta não está esperando aprovação ({EXPLICA.get(no, no)}).")
        try:
            self.carta.model_copy(deep=True).aprovar("conferencia")  # campos vazios: erro claro antes do portão
        except ValueError as e:
            raise EstudoErro(str(e), como_resolver="estudo.charter().edit('campo', 'valor')") from None
        self._e._invocar(Command(resume={"acao": "aprovar", "por": self._e._s.usuario}), "charter:approve")
        no, _ = self._e._pendente()
        return Painel(H.cartao("Carta aprovada", f"v{self.carta.versao}", f"<p>Próximo: {H.e(EXPLICA.get(no, no))}</p>"),
                      acoes=[Acao("Descobrir os dados", lambda: self._e.discover(), "estudo.discover(hint='...')", principal=True)]
                      if no == "aguardar_descoberta" else [])

    def edit(self, campo: str, valor: str, *, reason: str = "") -> "CartaView":
        if campo not in ROTULOS:
            raise EstudoErro(f"Campo desconhecido: {campo}. Campos: {', '.join(NOMES_CAMPOS)}")
        self._e._exigir_aberto()
        if not str(valor or "").strip():
            raise EstudoErro(f"O campo '{ROTULOS[campo]}' não pode ficar vazio.")
        st = self._e._estado()
        carta = E.carta(st)
        estava_aprovada = carta.aprovada
        if not carta.editar(campo, valor, motivo=reason):
            return self  # mesmo valor: nada muda, nenhum portão é reaberto
        textos = [*(st.get("textos_dev") or []), valor]
        self._e._s.dossie.registrar("carta_editada", {"campo": campo, "versao": carta.versao, "motivo": reason}, ator="dev")
        self._e._atualizar({"carta": carta.model_dump(mode="json"), "textos_dev": textos}, as_node="carta" if estava_aprovada else None)
        return self

    def _repr_html_(self) -> str:
        return self.painel()._repr_html_()

    def _ipython_display_(self) -> None:  # pragma: no cover - visual
        self.painel()._ipython_display_()

    def painel(self) -> Painel:
        st = self._e._estado()
        carta = self.carta
        frases = "".join(H.caixa(f"<b>Uma frase sua muda o método:</b> “{H.e(f.trecho)}”. {H.e(f.explicacao)}") for f in carta.frases_que_mudam_metodo)
        linhas = []
        for i, nome in enumerate(NOMES_CAMPOS, 1):
            c = carta.campos.get(nome)
            origem = H.ORIGENS.get(c.origem, c.origem) if c else ""
            aviso = f"<br><span class='aviso'>{H.e(c.aviso)}</span>" if c and c.aviso else ""
            linhas.append([str(i), H.e(ROTULOS[nome]), H.e(c.valor if c else "") + aviso, H.e(origem)])
        avisos = "".join(H.caixa(H.e(a), "alerta") for a in st.get("avisos_carta") or [])
        hip = afirmacoes_html([a for a in E.afirmacoes(st) if a.tipo == "hipotese"], E.marcadores(st))
        corpo = (frases + H.tabela(linhas, ["#", "campo", "conteúdo", "origem"], html_ok=True) + avisos + hip
                 + "<p class='sub'>Mudar a carta depois de aprovada gera uma versão nova e reabre só as etapas que dependem do campo.</p>")
        status = "aprovada" if carta.aprovada else "rascunho · aguardando aprovação"
        acoes = [] if carta.aprovada else [Acao("Aprovar a carta", self.approve, "estudo.charter().approve()", principal=True)]
        return Painel(H.cartao(f"Carta do estudo v{carta.versao}", status, corpo, rodape=rodape_uso(self._e._s.telemetria, "carta")),
                      acoes=acoes + [Acao("Editar um campo", lambda: Painel(H.cartao("Editar", "use o código",
                                     "<p><code>estudo.charter().edit('desfecho', '...')</code></p>")), "estudo.charter().edit('campo', 'valor')")])


class MapeamentoView:
    def __init__(self, estudo: Estudo) -> None:
        self._e = estudo

    @property
    def mapeamento(self) -> Mapeamento:
        return E.mapeamento(self._e._estado())

    def set(self, **campos) -> "MapeamentoView":
        m = self.mapeamento
        dados = {**m.model_dump(), **campos}
        novo = Mapeamento.model_validate(dados)
        if self._e._s.fonte is not None:
            problemas = validar_mapeamento(novo, self._e._s.fonte.catalogo())
            if problemas:
                raise EstudoErro("Mapeamento inválido: " + "; ".join(problemas))
        self._e._exigir_aberto()
        novo.status = "rascunho"
        self._e._s.dossie.registrar("mapeamento_editado", {"campos": sorted(campos)}, ator="dev")
        valores: dict[str, Any] = {"mapeamento": E.dump(novo)}
        if self._e._posicao() > ORDEM.index("aguardar_universo"):
            # universo, passaporte e plano foram feitos com o mapeamento antigo: refazer a partir do universo
            valores["validade"] = {k: v for k, v in (self._e._estado().get("validade") or {}).items() if k not in ("universo", "plano")}
            self._e._atualizar(valores, as_node="descobrir")
        else:
            self._e._atualizar(valores)
        return self

    def _repr_html_(self) -> str:
        m = self.mapeamento
        return H.cartao("Mapeamento", m.status, "<pre>" + H.e(json.dumps(m.model_dump(mode="json"), ensure_ascii=False, indent=1)) + "</pre>")


class PassaporteView:
    def __init__(self, estudo: Estudo) -> None:
        self._e = estudo

    def approve(self) -> Painel:
        no, _ = self._e._pendente()
        if no != "portao_passaporte":
            raise ForaDeOrdemErro(f"O passaporte não está esperando aprovação ({EXPLICA.get(no, no)}).")
        abertas = E.decisoes_abertas(self._e._estado(), "passaporte")
        if abertas:
            raise DecisaoPendenteErro("Antes do passaporte: " + "; ".join(f"{d.id} ({d.titulo})" for d in abertas))
        self._e._invocar(Command(resume={"acao": "aprovar", "por": self._e._s.usuario}), "passport:approve")
        return Painel(H.cartao("Passaporte aprovado", "", "<p>Próximo: estudo.plan()</p>"),
                      acoes=[Acao("Planejar", lambda: self._e.plan(), "estudo.plan()", principal=True)])

    def _repr_html_(self) -> str:
        pa = E.passaporte(self._e._estado())
        linhas = [[k, json.dumps(v, ensure_ascii=False, default=str)[:300]] for k, v in pa.model_dump(mode="json").items()]
        return H.cartao(f"Passaporte de Amostragem {pa.id}", pa.status, H.tabela(linhas, ["campo", "valor"]))


class PlanoView:
    def __init__(self, estudo: Estudo) -> None:
        self._e = estudo

    @property
    def plano(self):
        return E.plano(self._e._estado())

    def _salvar(self, plano) -> None:
        self._e._exigir_aberto()
        estava_aprovado = (self.plano or plano).aprovado
        self._e._s.dossie.registrar("plano_editado", {"versao": plano.versao, "mudancas": [m.model_dump() for m in plano.mudancas[-3:]]}, ator="dev")
        reposicionar = estava_aprovado and self._e._posicao() > ORDEM.index("portao_plano")
        self._e._atualizar({"plano": plano.model_dump(mode="json")}, as_node="planejar" if reposicionar else None)

    def add_step(self, id_: str, bloco, *, before: str | None = None, after: str | None = None, **parametros) -> "PlanoView":
        plano = self.plano
        plano.add_step(id_, bloco, before=before, after=after, **parametros)
        problemas = self._e._s.registro.validar_plano(plano, produzidos_antes=set(self._e._s.armazem))
        if problemas:
            raise EstudoErro("O passo novo deixa o plano inválido: " + "; ".join(problemas))
        self._salvar(plano)
        return self

    def set(self, id_: str, **parametros) -> "PlanoView":
        plano = self.plano
        plano.set(id_, **parametros)
        b = self._e._s.registro.obter(plano.passo(id_).bloco, plano.passo(id_).versao or None)
        b.validar_parametros(plano.passo(id_).parametros)
        self._salvar(plano)
        return self

    def remove_step(self, id_: str) -> "PlanoView":
        plano = self.plano
        plano.remove_step(id_)
        self._salvar(plano)
        return self

    def diff(self) -> list[str]:
        return self.plano.diff()

    def approve(self) -> Painel:
        no, _ = self._e._pendente()
        if no != "portao_plano":
            raise ForaDeOrdemErro(f"O plano não está esperando aprovação ({EXPLICA.get(no, no)}).")
        abertas = E.decisoes_abertas(self._e._estado(), "plano")
        if abertas:
            raise DecisaoPendenteErro("Antes de aprovar o plano: " + "; ".join(f"{d.id} ({d.titulo})" for d in abertas),
                                      como_resolver=f"estudo.decide('{abertas[0].id}', '...', reason='...')")
        problemas = self._e._s.registro.validar_plano(self.plano, produzidos_antes=set(self._e._s.armazem))
        if problemas:
            raise EstudoErro("O plano tem problemas: " + "; ".join(problemas))
        self._e._invocar(Command(resume={"acao": "aprovar", "por": self._e._s.usuario}), "plan:approve")
        return Painel(H.cartao("Plano aprovado", f"v{self.plano.versao}", "<p>Próximo: estudo.run()</p>"),
                      acoes=[Acao("Rodar o plano", lambda: self._e.run(), "estudo.run()", principal=True)])

    def _repr_html_(self) -> str:
        return self.painel()._repr_html_()

    def _ipython_display_(self) -> None:  # pragma: no cover - visual
        self.painel()._ipython_display_()

    def painel(self) -> Painel:
        plano = self.plano
        jeitos = H.tabela([[f"<b>{H.e(j.codigo)}</b> · {H.e(j.titulo)}" + (" <span class='tag'>recomendado</span>" if j.recomendado else ""),
                            H.e(j.responde), H.e(j.nao_responde), H.selo(j.forca_maxima)] for j in plano.jeitos],
                          ["jeito de responder", "responde", "não responde", "força máxima (estimativa)"], html_ok=True) if plano.jeitos else ""
        passos = H.tabela([[str(i), H.e(p.id), f"<code>{H.e(p.bloco)}@{H.e(p.versao)}</code>", H.e(p.papel), H.e(p.status),
                            H.e(json.dumps(p.parametros, ensure_ascii=False)[:120])] for i, p in enumerate(plano.passos, 1)],
                          ["", "passo", "bloco", "especialista", "situação", "parâmetros"], html_ok=True)
        pre = H.tabela([[H.e(x.risco), H.e(x.protecao), H.e(x.origem)] for x in plano.pre_mortem], ["como pode falhar", "proteção", "origem"])
        avisos = "".join(H.caixa(H.e(a), "alerta") for a in plano.avisos)
        corpo = jeitos + f"<h4>Método composto ({H.e(plano.origem)})</h4>" + passos + "<h4>Pré-mortem</h4>" + pre + avisos
        acoes = [] if plano.aprovado else [Acao("Aprovar o plano", self.approve, "estudo.plan_view().approve()", principal=True)]
        return Painel(H.cartao(f"Plano v{plano.versao}", "aprovado" if plano.aprovado else "aguardando aprovação", corpo,
                               rodape=rodape_uso(self._e._s.telemetria, "plano")), acoes=acoes)


class HandoffView:
    def __init__(self, estudo: Estudo) -> None:
        self._e = estudo

    def register_model(self) -> Painel:
        """Registra o pacote publicado no Model Catalog (metadados ≤ 32 KB; artefato imutável)."""
        from pathlib import Path

        from adex_copiloto.handoff.model_catalog import registrar

        st = self._e._estado()
        info = st.get("handoff") or {}
        if not info.get("zip"):
            raise ForaDeOrdemErro("Publique o pacote antes: estudo.handoff().approve().")
        manifesto = json.loads((Path(info["pasta"]) / "manifest.json").read_text(encoding="utf-8"))
        manifesto["sha256"] = info.get("manifesto_sha256", "")
        r = registrar(self._e._s.cfg, st, Path(info["zip"]), manifesto)
        self._e._s.dossie.registrar("model_catalog", r, ator="dev")
        return Painel(H.cartao("Registrado no Model Catalog", H.e(r.get("display_name")), f"<p><code>{H.e(r.get('model_id'))}</code></p>"))

    def approve(self) -> Painel:
        no, _ = self._e._pendente()
        if no != "portao_handoff":
            raise ForaDeOrdemErro(f"O pacote não está esperando aprovação ({EXPLICA.get(no, no)}).")
        self._e._invocar(Command(resume={"acao": "aprovar", "por": self._e._s.usuario}), "handoff:approve")
        info = self._e._estado().get("handoff") or {}
        return Painel(H.cartao("Pacote publicado", H.e(info.get("publicado_em", "")), f"<p>sha256 do zip: <code>{H.e(info.get('zip_sha256', ''))}</code></p>"))

    def _repr_html_(self) -> str:
        info = self._e._estado().get("handoff") or {}
        v = info.get("verificacao") or {}
        corpo = (f"<p>Pasta: <code>{H.e(info.get('pasta'))}</code> · {info.get('arquivos', 0)} arquivos · dados: {H.e(info.get('incluir_dados'))}</p>"
                 + H.caixa(("✓ Independente do copiloto" if v.get("ok") else "✗ Problemas: " + H.e("; ".join(v.get("problemas", []))))
                           + (" · pipeline offline executado" if v.get("executado") else "")))
        return H.cartao("Pacote de handoff", "aguardando aprovação para publicar" if not info.get("publicado_em") else "publicado", corpo)


class _Ferramentas:
    def __init__(self, estudo: Estudo, registro) -> None:
        self._e = estudo
        self.registro = registro

    def test(self, nome: str, **args) -> Painel:
        self.registro.dossie = self._e._s.dossie  # eventos vão para o dossiê deste estudo
        painel = self._e._s.armazem.get("painel") if "painel" in self._e._s.armazem else None
        m = E.mapeamento(self._e._estado())
        r = self.registro.testar(nome, painel=painel, coluna_grupo=(m.grupos[0] if m and m.grupos else None), **args)
        linhas = [[t.nome, "✓" if t.ok else ("⚠" if t.ok is None else "✗"), t.detalhe] for t in r]
        f = self.registro.obter(nome)
        estado = "aprovada: a IA pode pedir esta ferramenta" if f.aprovada else "reprovada: a IA não pode usar"
        return Painel(H.cartao(f"Ferramenta {nome} v{f.versao}", estado, H.tabela(linhas, ["teste de contrato (sem IA)", "", "detalhe"])
                               + "<p class='sub'>Limite declarado: o kernel não é isolado; a garantia está no que chega à IA e no que fica registrado.</p>"))

    def list(self) -> list[str]:
        return list(self.registro.ferramentas) if self.registro else []
