"""Nós do grafo do estudo (LangGraph).

Regras de construção:
- portões (``portao_*``) e esperas (``aguardar_*``) só chamam ``interrupt()`` e
  aplicam a resposta de forma determinística: na retomada o nó roda de novo do
  início, então ali não há IA nem SQL;
- nós de trabalho fazem uma coisa, gravam no dossiê e devolvem JSON ao estado;
- tabelas e objetos grandes vão para o armazém do estudo (Parquet/JSON).
"""

from __future__ import annotations

import datetime as dt
import getpass
import hashlib
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from langgraph.types import interrupt

from adex_copiloto.blocos import ContextoBloco, rodar_bloco
from adex_copiloto.blocos.registro import RegistroBlocos
from adex_copiloto.config.carregador import ConfigEfetiva
from adex_copiloto.critico.checagens import EntradaCritica, bloqueia_aceite, checar, reavaliar_selos
from adex_copiloto.erros import AdexErro
from adex_copiloto.estudo.carta import CampoCarta, Carta, etapas_afetadas
from adex_copiloto.estudo.datas import extrair_meses
from adex_copiloto.estudo.descoberta import descobrir
from adex_copiloto.estudo.dossie import Dossie
from adex_copiloto.estudo.enquadramento import construir_carta, enquadrar
from adex_copiloto.estudo.evidencia import Afirmacao, Decisao, Lacuna, OpcaoDecisao
from adex_copiloto.estudo.mapeamento import Mapeamento
from adex_copiloto.estudo.planejamento import planejar
from adex_copiloto.extensao.playbooks import LIMIAR_ADERENCIA, Atalhos
from adex_copiloto.harness import estado as E
from adex_copiloto.harness.armazem import ArmazemDados
from adex_copiloto.observabilidade.telemetria import Telemetria

log = logging.getLogger("adex_copiloto.harness")

PORTOES = ("carta", "passaporte", "plano", "aceite", "handoff")


def usuario_atual() -> str:
    return os.environ.get("ADEX_USUARIO") or getpass.getuser()


@dataclass
class Servicos:
    estudo_id: str
    efetiva: ConfigEfetiva
    pasta: Path
    dossie: Dossie
    armazem: ArmazemDados
    telemetria: Telemetria
    cliente: Any
    registro: RegistroBlocos
    atalhos: Atalhos
    fonte: Any = None
    fabrica_chat: Callable[[str], Any] | None = None
    langfuse: Any = None
    armazenamento: Any = None
    ferramentas_dev: Any = None
    usuario: str = field(default_factory=usuario_atual)

    @property
    def cfg(self):
        return self.efetiva.config

    def callbacks(self) -> list[Any]:
        if self.langfuse is None:
            return []
        try:
            return [self.langfuse.callback(self.telemetria.atual)]
        except Exception:  # noqa: BLE001
            return []

    def chat_agente(self, etapa: str):
        if self.fabrica_chat is None:
            return None
        from adex_copiloto.agentes.deep import contador_para

        return self.fabrica_chat("acao", callbacks=[contador_para(self.telemetria, self.cfg, etapa)])


class Nos:
    def __init__(self, s: Servicos) -> None:
        self.s = s

    # --- utilidades --------------------------------------------------------------------

    def _no(self, nome: str, func: Callable[[dict], dict], estado: dict) -> dict:
        with self.s.telemetria.observar(f"no:{nome}", tipo="chain", etapa=nome) as obs:
            saida = func(estado)
            obs.finalizar(saida={"chaves": sorted(saida)})
        return {**saida, "etapa": nome}

    def _registro_portao(self, estado: dict, portao: str, acao: str, extra: dict | None = None) -> list[dict]:
        item = {"portao": portao, "acao": acao, "por": self.s.usuario, "em": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                **(extra or {})}
        self.s.dossie.registrar("portao", item, ator="dev")
        try:
            self.s.telemetria.score(f"portao_{portao}", acao, tipo="CATEGORICAL", comentario=(extra or {}).get("motivo"))
        except Exception:  # noqa: BLE001
            pass
        return [*(estado.get("portoes") or []), item]

    def _snapshot(self, portao: str) -> None:
        cfg = self.s.cfg
        if not (cfg.checkpoint.snapshot_storage and cfg.objectstorage.bucket_estudos and cfg.objectstorage.namespace and self.s.armazenamento):
            return
        from adex_copiloto.harness.checkpoint import snapshot

        quando = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S")
        os_ = cfg.objectstorage
        uri = f"oci://{os_.bucket_estudos}@{os_.namespace}/{os_.prefixo_estudos.rstrip('/')}/{self.s.estudo_id}/snapshots/{quando}-{portao}.zip"
        try:
            self.s.armazenamento.escrever(uri, snapshot(self.s.pasta))
            self.s.dossie.registrar("snapshot", {"uri": uri, "portao": portao})
        except Exception as e:  # noqa: BLE001
            self.s.dossie.registrar("snapshot_falhou", {"portao": portao, "erro": type(e).__name__})
            return
        import json

        meta = self.s.pasta / "meta.json"
        try:
            dados = json.loads(meta.read_text(encoding="utf-8")) if meta.exists() else {}
            dados.update({"snapshot_ok": True, "snapshot_em": dt.datetime.now(dt.timezone.utc).isoformat(),
                          "snapshot_portao": portao, "snapshot_uri": uri})
            meta.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
        except (OSError, ValueError):
            pass

    def _contexto(self, estado: dict, etapa: str) -> ContextoBloco:
        excluidos = E.grupos_excluidos(estado)
        if list(self.s.armazem.get("grupos_excluidos") or []) != excluidos:
            self.s.armazem["grupos_excluidos"] = excluidos
        return ContextoBloco(
            estudo_id=self.s.estudo_id, carta=E.carta(estado), config=self.s.cfg, telemetria=self.s.telemetria,
            marcadores=E.marcadores(estado), ids=E.ids(estado), mapeamento=E.mapeamento(estado), fonte=self.s.fonte,
            dados=self.s.armazem, passaporte=E.passaporte(estado), seed=int(estado.get("seed", 42)), etapa=etapa,
        )

    def _executar_passo(self, estado: dict, ctx: ContextoBloco, passo: str, bloco: str, versao: str | None,
                        parametros: dict[str, Any], *, decisao_humana: bool = False) -> dict[str, Any]:
        b = self.s.registro.obter(bloco, versao or None)
        r = rodar_bloco(b, ctx, parametros, passo=passo, decisao_humana_se_falhar=decisao_humana)
        figuras = []
        for f in r.figuras:
            info = self.s.dossie.artefato(f"{passo}_{f.nome}.png", f.png, tipo="image/png", descricao=f.descricao, etapa=ctx.etapa)
            figuras.append({"titulo": f.titulo, "descricao": f.descricao, "arquivo": info["caminho"], "sha256": info["sha256"]})
        if r.tabelas:
            self.s.armazem[f"tabelas__{passo}"] = r.tabelas
        if r.tabelas_ia:
            self.s.armazem[f"tabelas_ia__{passo}"] = r.tabelas_ia
        consultas = []
        for c in r.consultas:
            item = {"passo": passo, "nome": c.nome, "sql": c.sql, "binds": {k: E.valor_json(v) for k, v in c.binds.items()},
                    "sha256": c.sha256, "modo": c.modo, "linhas": c.linhas, "segundos": c.segundos}
            self.s.dossie.registrar("consulta", item)
            consultas.append(item)
            if c.df is not None:
                self.s.armazem[f"consulta__{passo}__{c.nome}"] = c.df  # agregado: base do handoff com dados
        resumo = r.resumo_para_dossie()
        self.s.dossie.registrar("bloco", resumo)
        if "passaporte" in r.dados:
            estado["passaporte"] = E.dump(r.dados["passaporte"])
        # Rodar o passo de novo substitui o que ele gerou antes (afirmações, lacunas e decisões
        # ainda abertas). Uma decisão já tomada continua valendo se o passo pedir a mesma coisa.
        itens = dict(estado.get("itens_por_passo") or {})
        antes = itens.get(passo) or {}
        ant_af, ant_dec, ant_lac = (set(antes.get(k) or []) for k in ("afirmacoes", "decisoes", "lacunas"))
        decisoes_antes = estado.get("decisoes") or []
        decididas = {_chave_decisao(d): d["id"] for d in decisoes_antes if d.get("id") in ant_dec and d.get("status") == "decidida"}
        novas_af, novas_lac = E.dump(r.afirmacoes), E.dump(r.lacunas)
        novas_dec = [d for d in E.dump(r.decisoes) if _chave_decisao(d) not in decididas]
        estado["afirmacoes"] = [a for a in (estado.get("afirmacoes") or []) if a.get("id") not in ant_af] + novas_af
        estado["decisoes"] = [d for d in decisoes_antes if not (d.get("id") in ant_dec and d.get("status") == "aberta")] + novas_dec
        estado["lacunas"] = [l for l in (estado.get("lacunas") or []) if l.get("id") not in ant_lac] + novas_lac
        itens[passo] = {"afirmacoes": [a["id"] for a in novas_af], "lacunas": [l["id"] for l in novas_lac],
                        "decisoes": [*decididas.values(), *(d["id"] for d in novas_dec)]}
        estado["itens_por_passo"] = itens
        estado["metricas"] = E.dump_marcadores(ctx.marcadores)
        estado["ids"] = dict(ctx.ids.contadores)
        resultados = dict(estado.get("resultados") or {})
        resultados[passo] = {**resumo, "figuras": figuras, "codigo": r.codigo, "consultas": consultas,
                             "carta_versao": ctx.carta.versao, "parametros": parametros}
        estado["resultados"] = resultados
        return resultados[passo]

    # --- 1. enquadrar -------------------------------------------------------------------

    def enquadrar(self, estado: dict) -> dict:
        def f(st):
            lente = self.s.atalhos.lente(st.get("vertical"))
            enq = enquadrar(self.s.cliente, st["objetivo"], vertical=st.get("vertical"), lente=lente.para_llm() if lente else None,
                            modo=st.get("modo", "exploration"))
            atalhos = self.s.atalhos.ranquear(tipo=enq.tipo_pergunta, texto=st["objetivo"])
            self.s.dossie.registrar("enquadramento", {"enquadramento": enq.model_dump(), "atalhos": atalhos}, ator="ia")
            return {"enquadramento": enq.model_dump(), "atalhos": atalhos, "textos_dev": [st["objetivo"]]}

        return self._no("enquadrar", f, estado)

    def entrevista(self, estado: dict) -> dict:
        resposta = interrupt({"tipo": "pergunta", "etapa": "entrevista", "perguntas": (estado.get("enquadramento") or {}).get("perguntas", [])})
        respostas = str((resposta or {}).get("respostas") or "").strip()
        textos = list(estado.get("textos_dev") or [])
        if respostas:
            textos.append(respostas)
            self.s.dossie.registrar("respostas", {"texto": respostas}, ator="dev")
        return {"textos_dev": textos, "etapa": "entrevista"}

    # --- 2. carta -----------------------------------------------------------------------

    def carta(self, estado: dict) -> dict:
        def f(st):
            from adex_copiloto.estudo.carta import Enquadramento

            enq = Enquadramento.model_validate(st["enquadramento"]) if st.get("enquadramento") else None
            lente = self.s.atalhos.lente(st.get("vertical"))
            carta, avisos, proposta = construir_carta(
                self.s.cliente, objetivo=st["objetivo"], enquadramento=enq, respostas=(st.get("textos_dev") or [])[1:],
                cfg=self.s.cfg, lente=lente.para_llm() if lente else None,
            )
            ids = E.ids(st)
            afirmacoes = E.afirmacoes(st)
            for h in proposta.hipoteses:
                afirmacoes.append(Afirmacao(id=ids.proximo("H"), tipo="hipotese", texto=f"{h.id}: {h.texto}", selo="D", origem="dev"))
            lacunas = E.lacunas(st) + [Lacuna(id=ids.proximo("L"), descricao=l) for l in proposta.lacunas]
            self.s.dossie.registrar("carta", {"versao": carta.versao, "carta": carta.model_dump(mode="json"), "avisos": avisos}, ator="ia")
            return {"carta": carta.model_dump(mode="json"), "avisos_carta": avisos, "afirmacoes": E.dump(afirmacoes),
                    "lacunas": E.dump(lacunas), "ids": ids.contadores}

        return self._no("carta", f, estado)

    def portao_carta(self, estado: dict) -> dict:
        carta = E.carta(estado)
        r = interrupt({"tipo": "portao", "portao": "carta", "versao": carta.versao})
        acao = (r or {}).get("acao", "aprovar")
        if acao != "aprovar":
            return {"etapa": "portao_carta"}
        carta.aprovar((r or {}).get("por") or self.s.usuario)
        portoes = self._registro_portao(estado, "carta", "aprovado", {"versao": carta.versao})
        self._snapshot("carta")
        validade = dict(estado.get("validade") or {})
        validade["carta"] = carta.versao
        saida = {"carta": carta.model_dump(mode="json"), "portoes": portoes, "validade": validade, "etapa": "portao_carta"}
        if estado.get("status") in ("aceito", "iterando"):
            saida["status"] = "em_andamento"  # a carta mudou: o aceite anterior não vale mais
        return saida

    # --- 3. descoberta -------------------------------------------------------------------

    def aguardar_descoberta(self, estado: dict) -> dict:
        r = interrupt({"tipo": "comando", "espera": "descobrir"})
        return {"comando": r or {}, "etapa": "aguardar_descoberta"}

    def descobrir(self, estado: dict) -> dict:
        def f(st):
            if self.s.fonte is None:
                raise AdexErro("Não há fonte de dados configurada para a descoberta.",
                               como_resolver="Passe data_sources= (ex.: uma FonteLocal) ou configure [adw].")
            carta = E.carta(st)
            pista = str((st.get("comando") or {}).get("pista") or "")
            lente = self.s.atalhos.lente(st.get("vertical"))
            with self.s.telemetria.observar("descoberta", tipo="agent", etapa="descobrir") as obs:
                res = descobrir(
                    self.s.cliente, self.s.chat_agente("descobrir"), self.s.fonte, carta, pista=pista, cfg=self.s.cfg,
                    telemetria=self.s.telemetria, modo_agente="descobrir" in self.s.cfg.agente.deep_nodes,
                    lente=lente.para_llm() if lente else None, callbacks=self.s.callbacks(),
                )
                obs.finalizar(saida={"modo": res.modo, "candidatas": len(res.candidatas)})
            st = dict(st)
            textos = list(st.get("textos_dev") or [])
            if pista:
                textos.append(pista)
            ids = E.ids(st)
            lacunas = E.lacunas(st) + [Lacuna(id=ids.proximo("L"), descricao=l) for l in res.lacunas]
            decisoes = E.decisoes(st)
            if res.mapeamento is not None:
                decisoes.append(Decisao(
                    id=ids.proximo("D"), titulo="Confirmar o mapeamento",
                    pergunta="As tabelas e colunas propostas respondem à carta?",
                    opcoes=[OpcaoDecisao(codigo="a", rotulo="Confirmar", consequencia="o universo é montado com este mapeamento"),
                            OpcaoDecisao(codigo="b", rotulo="Revisar", consequencia="edite com estudo.mapping().set(...) e confirme")],
                    recomendada="a", origem="descobrir", bloqueia=["universo"], dados={"efeito": "confirmar_mapeamento"},
                ))
                for nome, codigos in res.codigos.items():
                    duvidas = [c for c in codigos if c["decisao"] == "duvida"]
                    if duvidas:
                        decisoes.append(Decisao(
                            id=ids.proximo("D"), titulo=f"Códigos em dúvida em '{nome}'",
                            pergunta=f"{len(duvidas)} código(s) ficaram em dúvida. Entram no desfecho?",
                            opcoes=[OpcaoDecisao(codigo="a", rotulo="Incluir as dúvidas", consequencia="desfecho mais amplo"),
                                    OpcaoDecisao(codigo="b", rotulo="Manter fora", consequencia="desfecho mais estrito")],
                            recomendada="b", origem="descobrir", bloqueia=["universo"],
                            dados={"efeito": "codigos_duvida", "desfecho": nome, "codigos": [c["codigo"] for c in duvidas]},
                        ))
            st.update({"mapeamento": E.dump(res.mapeamento), "lacunas": E.dump(lacunas), "decisoes": E.dump(decisoes),
                       "ids": ids.contadores, "textos_dev": textos,
                       "descoberta": {"candidatas": res.candidatas, "lacunas": res.lacunas, "avisos": res.avisos,
                                      "codigos": res.codigos, "modo": res.modo, "pista": pista}})
            if res.mapeamento is not None and res.mapeamento.ligacoes and self.s.cfg.descoberta.contagens_ligacao:
                ctx = self._contexto(st, "descobrir")
                self._executar_passo(st, ctx, "ligacoes", "ligacoes", None, {})
                st["mapeamento"] = E.dump(ctx.mapeamento)
            self.s.dossie.registrar("descoberta", st["descoberta"] | {"mapeamento": st["mapeamento"]}, ator="ia")
            validade = {k: v for k, v in (st.get("validade") or {}).items() if k not in ("universo", "plano")}
            validade["descobrir"] = carta.versao
            st["validade"] = validade
            return {k: st[k] for k in ("mapeamento", "lacunas", "decisoes", "ids", "textos_dev", "descoberta", "validade",
                                       "resultados", "afirmacoes", "metricas", "itens_por_passo") if k in st}

        return self._no("descobrir", f, estado)

    # --- 4. universo + passaporte ----------------------------------------------------------

    def aguardar_universo(self, estado: dict) -> dict:
        r = interrupt({"tipo": "comando", "espera": "universo"})
        return {"comando": r or {}, "etapa": "aguardar_universo"}

    def universo(self, estado: dict) -> dict:
        def f(st):
            st = dict(st)
            m = E.mapeamento(st)
            if m is None:
                raise AdexErro("Não há mapeamento para montar o universo.", como_resolver="Rode estudo.discover().")
            if m.status != "confirmado":
                raise AdexErro("O mapeamento ainda não foi confirmado.",
                               como_resolver="Confira o painel da descoberta e rode estudo.confirm_mapping().")
            cmd = st.get("comando") or {}
            ondas = cmd.get("ondas") or extrair_meses(" ".join(st.get("textos_dev") or []))
            passos = [("universo", "painel_agregado", {}), ("qualidade", "qualidade", {})]
            if m.tratamento and m.tempo:
                passos.append(("tratamento", "definir_tratamento", {"ondas_declaradas": ondas}))
            passos.append(("plano_amostral", "plano_amostral", {}))
            ctx = self._contexto(st, "universo")
            for passo, bloco, params in passos:
                r = self._executar_passo(st, ctx, passo, bloco, None, params)
                if r["status"] != "feito" and passo == "universo":
                    break
            validade = {k: v for k, v in (st.get("validade") or {}).items() if k != "plano"}
            validade["universo"] = ctx.carta.versao
            st["validade"] = validade
            return {k: st[k] for k in ("resultados", "afirmacoes", "decisoes", "lacunas", "metricas", "ids", "passaporte", "validade",
                                       "itens_por_passo") if k in st}

        return self._no("universo", f, estado)

    def portao_passaporte(self, estado: dict) -> dict:
        pa = E.passaporte(estado)
        r = interrupt({"tipo": "portao", "portao": "passaporte", "id": pa.id if pa else None})
        if (r or {}).get("acao", "aprovar") != "aprovar":
            return {"etapa": "portao_passaporte"}
        saida: dict[str, Any] = {"etapa": "portao_passaporte"}
        if pa is not None:
            pa.aprovar((r or {}).get("por") or self.s.usuario)
            self.s.armazem["passaporte"] = pa
            saida["passaporte"] = E.dump(pa)
        saida["portoes"] = self._registro_portao(estado, "passaporte", "aprovado", {"id": pa.id if pa else None})
        self._snapshot("passaporte")
        return saida

    # --- 5. plano --------------------------------------------------------------------------------

    def aguardar_plano(self, estado: dict) -> dict:
        r = interrupt({"tipo": "comando", "espera": "plano"})
        return {"comando": r or {}, "etapa": "aguardar_plano"}

    def planejar(self, estado: dict) -> dict:
        def f(st):
            carta = E.carta(st)
            cmd = st.get("comando") or {}
            if cmd.get("reutilizar") and st.get("plano"):
                plano = E.plano(st)
                for p in plano.passos:
                    p.status, p.nota = "pendente", "dados refeitos: rodar de novo"
                plano.status, plano.versao = "rascunho", plano.versao + 1
                plano.avisos = self.s.registro.validar_plano(plano, produzidos_antes=set(self.s.armazem))
                self.s.dossie.registrar("plano", {"plano": plano.model_dump(mode="json"), "modo": "reutilizado"}, ator="dev")
                validade = dict(st.get("validade") or {})
                validade["plano"] = carta.versao
                return {"plano": plano.model_dump(mode="json"), "validade": validade}
            nome_pb = cmd.get("playbook")
            playbook = self.s.atalhos.playbooks.get(nome_pb) if nome_pb else None
            if nome_pb and playbook is None:
                raise AdexErro(f"Atalho '{nome_pb}' não existe.", como_resolver=f"Opções: {', '.join(self.s.atalhos.playbooks)}")
            ranking = self.s.atalhos.ranquear(tipo=carta.tipo_pergunta, carta=carta, texto=" ".join(st.get("textos_dev") or []),
                                              mapeamento=E.mapeamento(st))
            servem = [a for a in ranking if a["aderencia"] >= LIMIAR_ADERENCIA]
            atalho_texto = ("Um atalho com aderência suficiente existe: " + servem[0]["titulo"] + ". Use-o só se servir.") if servem else ""
            produzidos = set(self.s.armazem)
            catalogo = self.s.fonte.catalogo() if self.s.fonte is not None else None
            with self.s.telemetria.observar("plano", tipo="agent", etapa="plano") as obs:
                res = planejar(
                    self.s.cliente, self.s.chat_agente("plano"), carta=carta, mapeamento=E.mapeamento(st),
                    afirmacoes=E.afirmacoes(st), decisoes=E.decisoes(st), produzidos=produzidos, registro=self.s.registro,
                    catalogo=catalogo, cfg=self.s.cfg, modo_agente="plano" in self.s.cfg.agente.deep_nodes and playbook is None,
                    playbook=playbook, atalho_texto=atalho_texto, callbacks=self.s.callbacks(),
                )
                obs.finalizar(saida={"modo": res.modo, "passos": len(res.plano.passos)})
            plano = res.plano
            feitos = {(r["bloco"], str(r.get("parametros") or {})): p for p, r in (st.get("resultados") or {}).items() if r.get("status") == "feito"}
            for p in plano.passos:
                chave = (f"{p.bloco}@{p.versao}", str(p.parametros or {}))
                if chave in feitos:
                    p.status, p.nota = "feito", f"reaproveitado de '{feitos[chave]}'"
            plano.avisos = res.avisos
            self.s.dossie.registrar("plano", {"plano": plano.model_dump(mode="json"), "modo": res.modo, "avisos": res.avisos}, ator="ia")
            validade = dict(st.get("validade") or {})
            validade["plano"] = carta.versao
            saida = {"plano": plano.model_dump(mode="json"), "sugestoes": [*(st.get("sugestoes") or []), *res.sugestoes],
                     "atalhos": ranking, "validade": validade}
            if st.get("status") in ("aceito", "iterando"):
                saida["status"] = "em_andamento"
            return saida

        return self._no("plano", f, estado)

    def portao_plano(self, estado: dict) -> dict:
        plano = E.plano(estado)
        r = interrupt({"tipo": "portao", "portao": "plano", "versao": plano.versao if plano else None})
        if (r or {}).get("acao", "aprovar") != "aprovar" or plano is None:
            return {"etapa": "portao_plano"}
        plano.aprovar((r or {}).get("por") or self.s.usuario)
        portoes = self._registro_portao(estado, "plano", "aprovado", {"versao": plano.versao})
        self._snapshot("plano")
        return {"plano": plano.model_dump(mode="json"), "portoes": portoes, "etapa": "portao_plano"}

    # --- 6. etapas ----------------------------------------------------------------------------------

    def aguardar_etapas(self, estado: dict) -> dict:
        plano = E.plano(estado)
        pendentes = [p.id for p in plano.passos if p.status not in ("feito", "pulado")] if plano else []
        r = interrupt({"tipo": "comando", "espera": "etapas", "pendentes": pendentes})
        return {"comando": r or {}, "etapa": "aguardar_etapas"}

    def etapas(self, estado: dict) -> dict:
        def f(st):
            st = dict(st)
            plano = E.plano(st)
            cmd = st.get("comando") or {}
            pedidos = cmd.get("passos")
            # Parâmetro novo muda o plano aprovado: isso passa por plan_view().set() e nova aprovação.
            ctx = self._contexto(st, "etapas")
            alvo = [p for p in plano.passos if (pedidos is None and p.status not in ("feito", "pulado")) or (pedidos and p.id in pedidos)]
            for p in alvo:
                abertas = [d for d in E.decisoes(st) if d.status == "aberta" and "etapas" in d.bloqueia]
                if abertas:
                    p.status, p.nota = "aguarda_decisao", f"espera {', '.join(d.id for d in abertas)}"
                    break
                r = self._executar_passo(st, ctx, p.id, p.bloco, p.versao, p.parametros, decisao_humana=p.decisao_humana_se_falhar)
                p.status = r["status"] if r["status"] in ("feito", "falhou", "aguarda_decisao") else "falhou"
                p.carta_versao = ctx.carta.versao
                p.nota = r.get("erro", "")[:300]
                if p.status != "feito":
                    break
            st["plano"] = plano.model_dump(mode="json")
            return {k: st[k] for k in ("plano", "resultados", "afirmacoes", "decisoes", "lacunas", "metricas", "ids", "passaporte",
                                       "itens_por_passo") if k in st}

        return self._no("etapas", f, estado)

    # --- 7. crítico + narrativa ------------------------------------------------------------------------

    def _flags(self, estado: dict) -> dict[str, Any]:
        a = self.s.armazem
        flags: dict[str, Any] = {}
        tp = a.get("teste_pre") if "teste_pre" in a else None
        if isinstance(tp, dict):
            flags["teste_pre_p"] = (tp.get("teste") or {}).get("p_valor")
            pico = tp.get("pico")
            limiar = float(tp.get("limiar_pico") or 0.15)
            if pico is not None and hasattr(pico, "empty") and not pico.empty and "ic_inf" in pico:
                flags["pico"] = bool(((pico["diferenca"] > limiar) & (pico["ic_inf"] > 0)).any())
        # Poder do teste prévio: o do efeito principal (relativo ao ATT do estudo); senão, o do bloco de tendências.
        ef = a.get("efeito") if "efeito" in a else None
        poder = ((ef or {}).get("teste_pre") or {}).get("poder_ok") if isinstance(ef, dict) else None
        if poder is None and isinstance(tp, dict):
            poder = (tp.get("teste") or {}).get("poder_ok")
        if poder is not None:
            flags["teste_pre_poder_ok"] = bool(poder)
        if "placebo" in a:
            flags["placebo_passou"] = bool(a["placebo"].get("passou"))
            if a["placebo"].get("equivalente") is not None:
                flags["placebo_equivalente"] = bool(a["placebo"]["equivalente"])
        if "sensibilidade" in a:
            from adex_copiloto.blocos.estatistica import sensibilidade_estavel

            flags["sensibilidade_estavel"] = sensibilidade_estavel(a["sensibilidade"])
        if flags.get("pico") and not flags.get("sensibilidade_estavel"):
            flags["pico_nao_mitigado"] = True
        if "modelo_classificacao" in a:
            met = a["modelo_classificacao"]["metricas"].set_index("modelo")
            flags["auc"] = float(met.loc[a["modelo_classificacao"]["melhor"], "auc"])
        if "vazamento" in a and len(a["vazamento"]):
            v = a["vazamento"]
            flags["vazamento"] = v[v["suspeita"]]["variavel"].tolist() or None
        if "estabilidade" in a and isinstance(a["estabilidade"], dict):
            estavel = a["estabilidade"].get("estavel")
            flags["estabilidade_ok"] = None if estavel is None else bool(estavel)
        if "coortes" in a and hasattr(a["coortes"], "groupby"):
            co = a["coortes"]
            tratadas = co[co["coorte"].notna()].groupby("coorte")["grupo"].nunique()
            flags["poucos_grupos"] = bool(co["grupo"].nunique() < 20 or (len(tratadas) and tratadas.min() < 5))
        m = E.mapeamento(estado)
        flags["tem_tempo"] = bool(m and m.tempo)
        return flags

    def critico(self, estado: dict) -> dict:
        def f(st):
            from adex_copiloto.critico.critico import criticar
            from adex_copiloto.narrativa.narrar import narrar
            from adex_copiloto.observabilidade.avaliacao import avaliar_texto, deve_avaliar

            ids = E.ids(st)
            flags = self._flags(st)
            afirmacoes = E.afirmacoes(st)
            mudancas = reavaliar_selos(afirmacoes, flags)
            consultas_antes, carta_aprovada = 0, False
            for ev in self.s.dossie.eventos():
                if ev["tipo"] == "portao" and ev["dados"].get("portao") == "carta":
                    carta_aprovada = True
                elif ev["tipo"] == "consulta" and not carta_aprovada:
                    consultas_antes += 1
            entrada = EntradaCritica(
                carta=E.carta(st), plano=E.plano(st), resultados=st.get("resultados") or {}, afirmacoes=afirmacoes,
                decisoes=E.decisoes(st), lacunas=E.lacunas(st), passaporte=E.passaporte(st), flags=flags,
                consultas_antes_da_carta=consultas_antes, tem_amostra="amostra" in self.s.armazem,
                separacao=(E.passaporte(st).separacao if E.passaporte(st) else ""),
            )
            achados = checar(entrada, ids)
            marcadores = E.marcadores(st)
            resumo_ia, avisos = "", []
            try:
                achados_ia, resumo_ia = criticar(self.s.cliente, entrada, marcadores, achados, ids)
                achados += achados_ia
            except AdexErro as e:
                avisos.append(f"o crítico da IA não respondeu ({e.mensagem[:160]}); valem as checagens do Python.")
            decisoes = E.decisoes(st)
            por_chave = {d.dados["chave_achado"]: d for d in decisoes if d.dados.get("chave_achado")}
            for a in achados:
                chave = f"{a.regra}:{a.refere}:{a.texto[:60]}"
                if not (a.aceitavel and a.severidade in ("alerta", "bloqueio")):
                    continue
                existente = por_chave.get(chave)
                if existente is not None and (existente.status == "aberta" or existente.escolha == "aceitar"):
                    # Mesmo risco numa revisão nova: a decisão (aberta ou de aceitar) vale para o achado novo.
                    existente.dados["achado"] = a.id
                    continue
                # risco novo, ou que continua depois de "ajustar": pergunta de novo
                decisoes.append(Decisao(
                    id=ids.proximo("D"), titulo=f"Risco apontado ({a.id})", pergunta=a.texto,
                    opcoes=[OpcaoDecisao(codigo="aceitar", rotulo="Aceitar o risco e registrar", consequencia="o risco entra nas limitações"),
                            OpcaoDecisao(codigo="ajustar", rotulo="Ajustar o estudo", consequencia="volte ao plano ou às etapas")],
                    recomendada="ajustar" if a.severidade == "bloqueio" else None, origem=f"critico:{a.origem}",
                    bloqueia=["aceite"], dados={"achado": a.id, "chave_achado": chave},
                ))
            textos_python = [r.get("resumo", "") for r in (st.get("resultados") or {}).values()]
            narrativa = narrar(self.s.cliente, carta=E.carta(st), marcadores=marcadores, afirmacoes=afirmacoes, textos_python=textos_python)
            if deve_avaliar(self.s.cfg):
                try:
                    avaliar_texto(self.s.cliente, self.s.telemetria, self.s.cfg, texto_marcado=narrativa.texto_marcado,
                                  marcadores=marcadores, carta=E.carta(st))
                except AdexErro as e:
                    avisos.append(f"o juiz (Qwen) não avaliou a narrativa ({e.mensagem[:120]}).")
            self.s.dossie.registrar("critica", {"achados": [a.model_dump() for a in achados], "selos": mudancas, "resumo_ia": resumo_ia,
                                                "narrativa": narrativa.texto_marcado, "origem_narrativa": narrativa.origem}, ator="python")
            return {"achados": [a.model_dump() for a in achados], "afirmacoes": E.dump(afirmacoes), "decisoes": E.dump(decisoes),
                    "ids": ids.contadores, "resumo_critico": resumo_ia,
                    "narrativa": {"texto_marcado": narrativa.texto_marcado, "texto": narrativa.texto, "html": narrativa.html,
                                  "origem": narrativa.origem, "problemas": narrativa.problemas},
                    "avisos": [*(st.get("avisos") or []), *avisos, *[f"selo reavaliado: {m}" for m in mudancas]]}

        return self._no("critico", f, estado)

    def portao_aceite(self, estado: dict) -> dict:
        from adex_copiloto.critico.checagens import Achado

        r = interrupt({"tipo": "portao", "portao": "aceite"})
        acao = (r or {}).get("acao", "aceitar")
        if acao == "aceitar":
            pendentes = bloqueia_aceite([Achado.model_validate(a) for a in estado.get("achados") or []], E.decisoes(estado))
            if pendentes:
                return {"etapa": "portao_aceite", "avisos": [*(estado.get("avisos") or []), "aceite recusado: há bloqueios pendentes"]}
            portoes = self._registro_portao(estado, "aceite", "aceito", {"motivo": (r or {}).get("motivo", "")})
            self._snapshot("aceite")
            return {"portoes": portoes, "status": "aceito", "etapa": "portao_aceite"}
        if acao == "descartar":
            portoes = self._registro_portao(estado, "aceite", "descartado", {"motivo": (r or {}).get("motivo", "")})
            return {"portoes": portoes, "status": "descartado", "etapa": "portao_aceite"}
        return {"status": "iterando", "etapa": "portao_aceite"}

    # --- 8. handoff ----------------------------------------------------------------------------------------

    def aguardar_handoff(self, estado: dict) -> dict:
        r = interrupt({"tipo": "comando", "espera": "handoff"})
        return {"comando": r or {}, "etapa": "aguardar_handoff"}

    def handoff(self, estado: dict) -> dict:
        def f(st):
            from adex_copiloto.handoff.exportar import exportar_pacote

            cmd = st.get("comando") or {}
            problemas = _pendencias_handoff(st)
            if problemas:
                raise AdexErro("O pacote não pode ser gerado: " + "; ".join(problemas) + ".",
                               como_resolver="Volte à etapa indicada, aprove-a e aceite o estudo de novo.")
            info = exportar_pacote(self.s, st, destino=cmd.get("destino"), incluir_dados=cmd.get("incluir_dados", "nenhum"))
            self.s.dossie.registrar("handoff", info)
            return {"handoff": info}

        return self._no("handoff", f, estado)

    def portao_handoff(self, estado: dict) -> dict:
        r = interrupt({"tipo": "portao", "portao": "handoff", "pasta": (estado.get("handoff") or {}).get("pasta")})
        if (r or {}).get("acao", "aprovar") != "aprovar":
            return {"etapa": "portao_handoff"}
        from adex_copiloto.handoff.exportar import publicar_pacote

        info = publicar_pacote(self.s, dict(estado.get("handoff") or {}))
        portoes = self._registro_portao(estado, "handoff", "aprovado", {"destino": info.get("publicado_em")})
        self._snapshot("handoff")
        return {"handoff": info, "portoes": portoes, "status": "entregue", "etapa": "portao_handoff"}

    # --- rotas --------------------------------------------------------------------------------------------

    @staticmethod
    def rota_portao_carta(estado: dict) -> str:
        carta = E.carta(estado)
        if carta is None or not carta.aprovada:
            return "portao_carta"
        validade = estado.get("validade") or {}
        alterados = set()
        for etapa in ("descobrir", "universo", "plano"):
            if etapa in validade:
                alterados |= carta.campos_alterados_desde(validade[etapa])
        afetadas = set(etapas_afetadas(alterados)) if alterados else set()
        if "descobrir" not in validade or "descobrir" in afetadas:
            return "aguardar_descoberta"
        m = E.mapeamento(estado)
        if "universo" not in validade or "universo" in afetadas or m is None or m.status != "confirmado":
            return "aguardar_universo"
        pa = estado.get("passaporte")
        if pa is not None and pa.get("status") != "aprovado":
            return "portao_passaporte"  # nunca pular o portão do passaporte
        plano = E.plano(estado)
        if "plano" not in validade or "plano" in afetadas or plano is None:
            return "aguardar_plano"
        if not plano.aprovado:
            return "portao_plano"
        return "aguardar_etapas"

    @staticmethod
    def rota_portao(destino_ok: str, portao: str) -> Callable[[dict], str]:
        def rota(estado: dict) -> str:
            return destino_ok if estado.get("etapa") == f"portao_{portao}" and _aprovado(estado, portao) else f"portao_{portao}"

        return rota

    @staticmethod
    def rota_aguardar_etapas(estado: dict) -> str:
        return "critico" if (estado.get("comando") or {}).get("comando") == "revisar" else "etapas"

    @staticmethod
    def rota_portao_aceite(estado: dict) -> str:
        status = estado.get("status")
        if status == "aceito":
            return "aguardar_handoff"
        if status == "descartado":
            return "__end__"
        if status == "iterando":
            return "aguardar_plano"
        return "portao_aceite"

    @staticmethod
    def rota_portao_handoff(estado: dict) -> str:
        return "__end__" if estado.get("status") == "entregue" else "portao_handoff"


def _chave_decisao(d: dict) -> tuple[str, str]:
    import json

    dados = {k: v for k, v in (d.get("dados") or {}).items()}
    return d.get("titulo", ""), json.dumps(dados, sort_keys=True, ensure_ascii=False, default=str)


def _pendencias_handoff(estado: dict) -> list[str]:
    """O pacote só sai de estudo aceito, com plano, passaporte e mapeamento aprovados e todas as etapas rodadas."""
    problemas = []
    if estado.get("status") != "aceito":
        problemas.append(f"o estudo não está aceito (situação: {estado.get('status') or 'em andamento'})")
    plano = E.plano(estado)
    if plano is None or not plano.aprovado:
        problemas.append("o plano não está aprovado")
    elif faltam := [p.id for p in plano.passos if p.status in ("pendente", "aguarda_decisao")]:
        problemas.append("etapas do plano sem rodar: " + ", ".join(faltam))
    pa = E.passaporte(estado)
    if pa is not None and pa.status != "aprovado":
        problemas.append("o Passaporte de Amostragem não está aprovado")
    m = E.mapeamento(estado)
    if m is not None and m.status != "confirmado":
        problemas.append("o mapeamento não está confirmado")
    return problemas


def _aprovado(estado: dict, portao: str) -> bool:
    if portao == "passaporte":
        pa = estado.get("passaporte")
        return pa is None or pa.get("status") == "aprovado"
    if portao == "plano":
        return bool(estado.get("plano") and estado["plano"].get("status") == "aprovado")
    return True


def semente(estudo_id: str) -> int:
    return int(hashlib.sha256(estudo_id.encode()).hexdigest()[:8], 16) % 2_000_000_000
