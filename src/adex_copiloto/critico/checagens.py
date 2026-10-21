"""Crítico determinístico: regras em Python que a IA não pode remover.

Cada regra devolve achados com severidade:
- ``bloqueio``: o estudo não pode ser aceito assim (a conclusão seria enganosa);
- ``alerta``: aceitar exige decisão explícita do dev, registrada no dossiê;
- ``nota``: registrado, sem bloquear.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel

from adex_copiloto.estudo.carta import Carta
from adex_copiloto.estudo.evidencia import Afirmacao, Decisao, Lacuna, Registro, calcular_selo, menor_selo
from adex_copiloto.estudo.passaporte import Passaporte
from adex_copiloto.estudo.plano import Plano

Severidade = Literal["bloqueio", "alerta", "nota"]


class Achado(BaseModel):
    id: str
    severidade: Severidade
    texto: str
    refere: str = ""
    o_que_fazer: str = ""
    origem: Literal["python", "ia"] = "python"
    regra: str = ""
    aceitavel: bool = False  # o dev pode aceitar o risco com uma decisão registrada


@dataclass
class EntradaCritica:
    carta: Carta
    plano: Plano | None
    resultados: dict[str, dict[str, Any]]  # passo -> resumo_para_dossie
    afirmacoes: list[Afirmacao]
    decisoes: list[Decisao]
    lacunas: list[Lacuna]
    passaporte: Passaporte | None
    flags: dict[str, Any] = field(default_factory=dict)  # teste_pre_p, pico, placebo_passou, sensibilidade_estavel...
    consultas_antes_da_carta: int = 0
    tem_amostra: bool = False
    separacao: str = ""


def _blocos_feitos(e: EntradaCritica) -> set[str]:
    return {r["bloco"].split("@")[0] for r in e.resultados.values() if r.get("status") == "feito"}


def checar(e: EntradaCritica, ids: Registro) -> list[Achado]:
    achados: list[Achado] = []

    def add(sev: Severidade, texto: str, regra: str, *, refere: str = "", fazer: str = "", aceitavel: bool = False) -> None:
        achados.append(Achado(id=ids.proximo("C"), severidade=sev, texto=texto, refere=refere, o_que_fazer=fazer,
                              regra=regra, aceitavel=aceitavel))

    feitos = _blocos_feitos(e)
    tipo = e.carta.tipo_pergunta
    criterios = (e.carta.valor("criterios_pronto") or "").lower()

    if not e.carta.aprovada:
        add("bloqueio", "A carta do estudo não está aprovada.", "carta_aprovada", refere="carta", fazer="Aprove a carta (ou a versão nova).")
    if e.consultas_antes_da_carta:
        add("alerta", "Houve consultas a dados antes da aprovação da carta: o desfecho pode ter sido escolhido depois de ver os dados.",
            "desfecho_antes_dos_dados", refere="carta", fazer="Registre por que isso não afeta a escolha do desfecho.", aceitavel=True)
    for passo, r in e.resultados.items():
        if r.get("status") == "falhou":
            add("bloqueio", f"O passo '{passo}' falhou: {r.get('erro', '')[:200]}", "passo_falhou", refere=passo,
                fazer="Ajuste o plano ou os parâmetros e rode de novo.")
        elif r.get("status") == "aguarda_decisao":
            add("bloqueio", f"O passo '{passo}' espera uma decisão sua.", "passo_aguarda", refere=passo, fazer="Responda a decisão aberta.")
    if e.plano is not None:
        faltam = [p.id for p in e.plano.passos if p.status in ("pendente", "pronta")]
        if faltam:
            add("bloqueio", f"Passos do plano ainda não rodaram: {', '.join(faltam)}.", "plano_incompleto", refere="plano",
                fazer="Rode os passos que faltam ou tire-os do plano (gera nova versão).")
    for d in e.decisoes:
        if d.status == "aberta" and "aceite" in d.bloqueia:
            add("bloqueio", f"{d.id} ({d.titulo}) está em aberto.", "decisao_aberta", refere=d.id, fazer=f"estudo.decide('{d.id}', ...)")
    if e.tem_amostra and not (e.passaporte and e.passaporte.aprovado):
        add("bloqueio", "Há amostra em uso sem Passaporte de Amostragem aprovado.", "passaporte", refere="passaporte",
            fazer="Aprove o passaporte ou refaça a amostra.")
    if e.passaporte and e.passaporte.metodo == "conveniencia":
        add("alerta", "A amostra é de conveniência: o resultado não vale para o universo.", "conveniencia", refere=e.passaporte.id,
            aceitavel=True)

    if tipo in ("efeito", "diagnostico") and "did_escalonado" in feitos:
        if "estudo_evento_pre" not in feitos:
            add("alerta", "O efeito foi estimado sem testar as tendências antes da ação.", "tendencias_pre", refere="efeito",
                fazer="Inclua o bloco estudo_evento_pre.", aceitavel=True)
        elif e.flags.get("teste_pre_p") is not None and e.flags["teste_pre_p"] < 0.05:
            add("alerta", "Tratados e controles não andavam juntos antes da ação: o efeito pode misturar outra diferença.",
                "tendencias_pre_falhou", refere="efeito", fazer="Veja a sensibilidade; considere outro método ou coorte.", aceitavel=True)
        if e.flags.get("pico"):
            mitigado = "sensibilidade" in feitos and e.flags.get("sensibilidade_estavel")
            add("alerta" if not mitigado else "nota",
                "Há sinal de seleção por pico antes da ação (regressão à média)" + (": a sensibilidade não muda o sinal." if mitigado else "."),
                "pico_pre", refere="H-pico", fazer="Rode a sensibilidade sem os grupos da coorte com pico.", aceitavel=True)
        if ("placebo" in criterios or "placebo" in feitos) and "placebo" not in feitos:
            add("bloqueio", "A carta pede placebo e ele não rodou.", "criterio_placebo", refere="criterios_pronto")
        if "placebo" in feitos and e.flags.get("placebo_passou") is False:
            # Alerta aceitável, não bloqueio: num estudo correto o placebo falha ~5% das vezes (o próprio nível do
            # teste); travar para sempre levaria a trocar o deslocamento até passar. O selo do efeito já cai.
            add("alerta", "O placebo NÃO passou: o método acha 'efeito' onde não houve ação.", "placebo_falhou", refere="placebo",
                fazer="Reveja o desenho (coortes, controles, desfecho); se mantiver, registre o motivo.", aceitavel=True)
        elif "placebo" in feitos and e.flags.get("placebo_equivalente") is False:
            add("nota", "O placebo contém zero, mas o intervalo é largo demais para confirmar o método.", "placebo_largo",
                refere="placebo", fazer="Mais períodos antes da ação ou mais grupos estreitam o intervalo.")
        if e.flags.get("estabilidade_ok") is False:
            add("alerta", "Os códigos que formam o desfecho mudaram ao longo do tempo: parte do 'efeito' pode ser mudança de registro.",
                "desfecho_instavel", refere="estabilidade", fazer="Veja a estabilidade dos códigos e ajuste o desfecho.", aceitavel=True)
        if e.flags.get("poucos_grupos"):
            add("nota", "Poucos grupos ou coortes pequenas: o intervalo e o teste de tendências ficam menos confiáveis.",
                "poucos_grupos", refere="efeito")
        if "sensibilidade" in criterios and "sensibilidade" not in feitos:
            add("bloqueio", "A carta pede sensibilidade e ela não rodou.", "criterio_sensibilidade", refere="criterios_pronto")
        if e.flags.get("sensibilidade_estavel") is False:
            add("alerta", "O efeito muda de sinal entre cenários de sensibilidade: conclusão frágil.", "sensibilidade_instavel",
                refere="sensibilidade", aceitavel=True)
    if any(k in criterios for k in ("intervalo", " ic", "95%")):
        efeitos = [m for r in e.resultados.values() for m in r.get("metricas", []) if m.get("nome", "").startswith("efeito_att")]
        if efeitos and any(not m.get("ic") for m in efeitos):
            add("bloqueio", "A carta pede intervalo de confiança e o efeito saiu sem intervalo.", "criterio_ic", refere="efeito")

    if tipo in ("classificacao", "previsao"):
        if "classificacao" in feitos and e.separacao.startswith("aleat") and e.flags.get("tem_tempo"):
            add("alerta", "A validação foi aleatória num problema com tempo: o desempenho pode estar otimista.", "validacao_temporal",
                refere="separacao", fazer="Use separacao com tipo='temporal'.", aceitavel=True)
        if e.flags.get("auc") is not None and e.flags["auc"] <= 0.55:
            add("alerta", "O modelo quase não supera o baseline.", "baseline", refere="classificacao", aceitavel=True)
        if e.flags.get("vazamento"):
            add("bloqueio", f"Variáveis suspeitas de vazamento: {e.flags['vazamento']}.", "vazamento", refere="classificacao",
                fazer="Confirme a remoção na decisão aberta.")

    for a in e.afirmacoes:
        if a.tipo == "recomendacao":
            bases = [x for x in e.afirmacoes if x.id in a.bases]
            if bases and menor_selo(*[b.selo for b in bases]) == "D":
                add("alerta", f"{a.id} recomenda com base em hipótese não verificada (selo D).", "recomendacao_fraca", refere=a.id,
                    aceitavel=True)
    for l in e.lacunas:
        if l.status == "aberta":
            sev: Severidade = "alerta" if re.search(r"conclus|vi[eé]s|some|perde", l.impacto or "", re.I) else "nota"
            add(sev, f"{l.id} em aberto: {l.descricao}", "lacuna", refere=l.id, aceitavel=True)
    return achados


def robustez_do_efeito(flags: dict[str, Any]) -> bool | None:
    """Placebo E sensibilidade confirmam o efeito? ``None`` = ainda não dá para dizer.

    - ausência não é aprovação: só um dos dois rodou → ``None``;
    - qualquer reprovação (placebo, sensibilidade ou estabilidade do desfecho) → ``False``;
    - placebo que "passou" com IC largo (não equivalente) não confirma nada → ``None``.
    """
    placebo, sens, estab = flags.get("placebo_passou"), flags.get("sensibilidade_estavel"), flags.get("estabilidade_ok")
    if placebo is False or sens is False or estab is False:
        return False
    if placebo is True and sens is True:
        return None if flags.get("placebo_equivalente") is False else True
    return None


def limite_do_efeito(flags: dict[str, Any]) -> str | None:
    """Selo máximo do efeito: B quando o teste prévio não tem poder reportado ou há poucos grupos.

    "Não rejeitar" tendências com um teste fraco não confirma a premissa (medido: tendência
    diferencial com viés de +0,50 passou no teste em 93% das vezes e levou selo A em 79%).
    """
    sem_poder = flags.get("teste_pre_p") is not None and flags.get("teste_pre_poder_ok") is not True
    return "B" if (sem_poder or flags.get("poucos_grupos")) else None


def reavaliar_selos(afirmacoes: list[Afirmacao], flags: dict[str, Any]) -> list[str]:
    """Depois de placebo e sensibilidade, o selo do efeito pode subir (ou cair). Devolve as mudanças."""
    mudancas = []
    premissas = flags.get("teste_pre_p")
    premissas_ok = None if premissas is None else bool(premissas >= 0.05 and not flags.get("pico_nao_mitigado"))
    robustez_ok = robustez_do_efeito(flags)
    limite = limite_do_efeito(flags)
    for a in afirmacoes:
        if a.bloco.startswith("did_escalonado@") and a.tipo == "fato":
            novo = calcular_selo(base="censo", incerteza=True, premissas_ok=premissas_ok, robustez_ok=robustez_ok, exige_robustez=True,
                                 limite=limite)
            if novo != a.selo:
                mudancas.append(f"{a.id}: {a.selo} → {novo}")
                a.mudar_selo(novo, "reavaliado com tendências, placebo e sensibilidade")
    return mudancas


def bloqueia_aceite(achados: list[Achado], decisoes: list[Decisao]) -> list[Achado]:
    """Achados que ainda impedem o aceite.

    - bloqueio do Python: só sai corrigindo (não há "aceitar o risco");
    - alerta (e bloqueio sugerido pela IA, que pode errar): sai com uma decisão
      "aceitar" registrada, com motivo, no dossiê.
    """
    aceitos = {d.dados.get("achado") for d in decisoes if d.status == "decidida" and d.escolha == "aceitar"}
    saida = []
    for a in achados:
        if a.severidade == "bloqueio" and a.origem == "python":
            saida.append(a)
        elif a.severidade in ("bloqueio", "alerta") and a.id not in aceitos:
            saida.append(a)
    return saida
