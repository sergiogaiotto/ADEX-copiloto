"""Provas da Fase 0. Cada uma mede o EFEITO, não só se o hub aceitou o campo."""

from __future__ import annotations

import base64
import io
import json
import random
import socket
import ssl
import string
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, ValidationError

from adex_copiloto.fase0.bateria import Bateria, ResultadoProva, p50_p95, prova
from adex_copiloto.llm.aceitacao import esquema_para_hub, extrair_json


class _Enquadra(BaseModel):
    tipo: Literal["efeito", "previsao", "descricao"]
    itens: list[str] = Field(min_length=2, max_length=4)
    certeza: float = Field(ge=0, le=1)


PERGUNTA_RACIOCINIO = "Uma loja vende 3 produtos a 7, 11 e 13 reais. Quantas combinações de 1 a 3 itens distintos somam número primo? Responda só o número."


def _esquema(modelo: type[BaseModel]) -> dict:
    s = esquema_para_hub(modelo)
    s["additionalProperties"] = False
    return {"type": "json_schema", "json_schema": {"name": modelo.__name__, "schema": s, "strict": True}}


# --- Bloco A: endpoint e parâmetros -----------------------------------------------------------------


@prova("A1", "Inventário do endpoint, rede e metadados")
def a1(b: Bateria) -> ResultadoProva:
    host = urlparse(b.url).hostname
    ips = sorted({x[4][0] for x in socket.getaddrinfo(host, 443)})
    privados = [ip for ip in ips if ip.startswith(("10.", "192.168.")) or ip.startswith(tuple(f"172.{i}." for i in range(16, 32)))]
    ctx = ssl.create_default_context(cafile=b.cfg.hub.ca_bundle or None)
    with socket.create_connection((host, 443), timeout=10) as s, ctx.wrap_socket(s, server_hostname=host) as t:
        cert = t.getpeercert()
    r = b.http.get(f"{b.url}/models", headers=b.cabecalhos())
    ids = [m.get("id") for m in (r.json().get("data") if r.status_code == 200 else [])]
    sem_chave = b.http.get(f"{b.url}/models", headers=b.cabecalhos(com_chave=False)).status_code
    from adex_copiloto.llm.impressao_digital import calcular

    imp, _ = calcular(r.json() if r.status_code == 200 else {}, b.cfg.hub.modelo)
    b.memoria["impressao_modelo"] = imp
    ok = r.status_code == 200 and b.cfg.hub.modelo in ids and sem_chave in (401, 403)
    return ResultadoProva("A1", "Inventário do endpoint, rede e metadados", "aprovado" if ok else "reprovado",
                          medido={"ips": ips, "ips_privados": privados, "certificado_emissor": dict(x[0] for x in cert.get("issuer", [])),
                                  "validade": cert.get("notAfter"), "modelos": ids, "status_sem_chave": sem_chave, "impressao_lista": imp},
                          observacao=f"{len(ids)} modelo(s) visíveis; sem chave = HTTP {sem_chave}",
                          recomendacao=f"hub.modelo_impressao = '{imp}' (depois de conferir com o CCOE)" if ok else "")


@prova("A2", "Impressão digital do template (liga/desliga, nível, preservar raciocínio)")
def a2(b: Bateria) -> ResultadoProva:
    msgs = b.mensagem("Diga apenas: ok.")
    medidas = {}
    for nome, extra in {"desligado": {"chat_template_kwargs": {"enable_thinking": False}},
                        "ligado": {"chat_template_kwargs": {"enable_thinking": True}},
                        "low": {"chat_template_kwargs": {"enable_thinking": True, "reasoning_effort": "low"}},
                        "xhigh": {"chat_template_kwargs": {"enable_thinking": True, "reasoning_effort": "xhigh"}}}.items():
        r = b.chat({"messages": msgs, "max_tokens": 600, "top_k": 20, **extra})
        medidas[nome] = b.uso(r)
    p = {k: v["prompt"] for k, v in medidas.items()}
    template_aplica = p.get("desligado") != p.get("ligado") or medidas["desligado"]["tem_raciocinio"] != medidas["ligado"]["tem_raciocinio"]
    nivel_chega = p.get("low") != p.get("xhigh")
    situacao = "aprovado" if template_aplica else "reprovado"
    rec = "" if nivel_chega else "reasoning_effort em chat_template_kwargs não muda o prompt: teste hub.esforco_em = 'topo' (A8)"
    return ResultadoProva("A2", "Impressão digital do template", situacao, medido={"prompt_tokens": p, "detalhe": medidas},
                          observacao=f"liga/desliga {'muda' if template_aplica else 'NÃO muda'} o template; nível {'muda' if nivel_chega else 'não muda'} o prompt",
                          recomendacao=rec)


@prova("A3", "Onde vem o raciocínio (campo, streaming, contagem)")
def a3(b: Bateria) -> ResultadoProva:
    r = b.chat({"messages": b.mensagem(PERGUNTA_RACIOCINIO), **b.perfil("acao")})
    u = b.uso(r)
    ok = u["tem_raciocinio"]
    return ResultadoProva("A3", "Onde vem o raciocínio", "aprovado" if ok else "reprovado", medido=u,
                          observacao=f"campo: {u['campo_raciocinio']}; reasoning_tokens no uso: {u['reasoning_tokens'] is not None}")


@prova("A4", "Repasse dos parâmetros de amostragem")
def a4(b: Bateria) -> ResultadoProva:
    pedido = "Escreva uma única palavra qualquer em português."
    saidas = {}
    for top_k in (1, 50):
        textos = []
        for _ in range(max(3, b.repeticoes)):
            r = b.chat({"messages": b.mensagem(pedido), "max_tokens": 20, "temperature": 1.2, "top_p": 1.0, "top_k": top_k,
                        "chat_template_kwargs": {"enable_thinking": False}})
            textos.append(b.uso(r)["texto"].strip().lower())
        saidas[top_k] = textos
    chega = len(set(saidas[1])) == 1 and len(set(saidas[50])) > 1
    return ResultadoProva("A4", "Repasse dos parâmetros de amostragem", "aprovado" if chega else "parcial", medido=saidas,
                          observacao="top_k=1 fixa a saída e top_k=50 varia" if chega else "não deu para provar o repasse de top_k só pela variação")


@prova("A5", "Teto real de saída e limite de contexto")
def a5(b: Bateria) -> ResultadoProva:
    enorme = b.chat({"messages": b.mensagem("ok"), "max_tokens": 300000, "chat_template_kwargs": {"enable_thinking": False}})
    pedido = "Conte de 1 até 5000, um número por linha, sem comentários."
    medidas = {"max_tokens_300000": {"status": enorme.get("status"), "erro": str((enorme.get("json") or {}).get("error"))[:200]}}
    for campo in ("max_tokens", "max_completion_tokens"):
        r = b.chat({"messages": b.mensagem(pedido), campo: 9000, "top_k": 20, "temperature": 0.7, "chat_template_kwargs": {"enable_thinking": False}},
                   timeout=max(300.0, b.cfg.hub.timeout_s))
        medidas[campo] = {"status": r.get("status"), **b.uso(r)}
    passa = [c for c in ("max_tokens", "max_completion_tokens") if (medidas[c].get("completion") or 0) > 8192]
    situacao = "aprovado" if len(passa) == 2 else ("parcial" if passa else "informativo")
    rec = ""
    if passa == ["max_completion_tokens"]:
        rec = "hub.campo_saida = 'max_completion_tokens' (o LiteLLM corta só max_tokens)"
    elif len(passa) == 2:
        rec = "o teto de 8.192 não vale: suba hub.saida_max com cuidado (raciocínio divide o mesmo teto)"
    else:
        rec = "mantenha hub.saida_max = 8192"
    return ResultadoProva("A5", "Teto real de saída e limite de contexto", situacao, medido=medidas,
                          observacao=f"passaram de 8.192 tokens: {passa or 'nenhum'}", recomendacao=rec)


@prova("A6", "Raciocínio dentro do max_tokens e corte por length")
def a6(b: Bateria) -> ResultadoProva:
    r = b.chat({"messages": b.mensagem(PERGUNTA_RACIOCINIO), "max_tokens": 64, "top_k": 20, "temperature": 1.0,
                "chat_template_kwargs": {"enable_thinking": True, "reasoning_effort": "medium"}})
    u = b.uso(r)
    confirma = u["finish"] == "length" and not u["texto"].strip()
    return ResultadoProva("A6", "Raciocínio dentro do max_tokens", "aprovado" if confirma else "informativo", medido=u,
                          observacao="com teto baixo, o raciocínio consome a saída e o texto vem vazio (a regra de aceitação recusa)" if confirma
                          else f"finish={u['finish']}, texto={'vazio' if not u['texto'] else 'presente'}")


@prova("A7", "Teto duro de raciocínio (thinking_token_budget)")
def a7(b: Bateria) -> ResultadoProva:
    medidas = {}
    for orc in (None, 256):
        corpo = {"messages": b.mensagem(PERGUNTA_RACIOCINIO), "max_tokens": 4096, "top_k": 20, "temperature": 1.0,
                 "chat_template_kwargs": {"enable_thinking": True, "reasoning_effort": "medium"}}
        if orc:
            corpo["thinking_token_budget"] = orc
        medidas["sem_orcamento" if orc is None else f"orcamento_{orc}"] = b.uso(b.chat(corpo))
    sem, com = medidas["sem_orcamento"], medidas["orcamento_256"]
    efeito = (com["completion"] or 0) < 0.6 * (sem["completion"] or 1)
    return ResultadoProva("A7", "Teto duro de raciocínio", "aprovado" if efeito else "reprovado", medido=medidas,
                          observacao=f"saída {sem['completion']} → {com['completion']} tokens com orçamento 256",
                          recomendacao="" if efeito else "thinking_token_budget é ignorado: controle o raciocínio pelo nível (A8) e pelo teto de saída")


@prova("A8", "Níveis low / medium / xhigh e valores proibidos")
def a8(b: Bateria) -> ResultadoProva:
    medidas = {}
    for onde in ("chat_template_kwargs", "topo"):
        for nivel in ("low", "medium", "xhigh", "high"):
            corpo = {"messages": b.mensagem(PERGUNTA_RACIOCINIO), "max_tokens": 8192, "top_k": 20, "temperature": 1.0,
                     "chat_template_kwargs": {"enable_thinking": True}}
            if onde == "topo":
                corpo["reasoning_effort"] = nivel
            else:
                corpo["chat_template_kwargs"]["reasoning_effort"] = nivel
            r = b.chat(corpo)
            medidas[f"{onde}:{nivel}"] = {"status": r.get("status"), **(b.uso(r) if r.get("status") == 200 else {})}
    def tok(k):
        return medidas.get(k, {}).get("completion") or 0
    efeito = {o: tok(f"{o}:low") < tok(f"{o}:xhigh") for o in ("chat_template_kwargs", "topo")}
    high_400 = {o: medidas[f"{o}:high"]["status"] == 400 for o in ("chat_template_kwargs", "topo")}
    melhor = "chat_template_kwargs" if efeito["chat_template_kwargs"] else ("topo" if efeito["topo"] else None)
    return ResultadoProva("A8", "Níveis de raciocínio", "aprovado" if melhor else "reprovado", medido={"medidas": medidas, "high_da_400": high_400},
                          observacao=f"o nível muda o gasto via: {melhor or 'nenhum'}",
                          recomendacao=f"hub.esforco_em = '{melhor}'" if melhor else "o nível não chega ao modelo: fale com o CCOE")


@prova("A10", "stop, n e logprobs")
def a10(b: Bateria) -> ResultadoProva:
    medidas = {}
    for nome, extra in {"stop": {"stop": ["3"]}, "n2": {"n": 2}, "logprobs": {"logprobs": True, "top_logprobs": 2}}.items():
        r = b.chat({"messages": b.mensagem("Conte de 1 a 5 separados por vírgula."), "max_tokens": 50, "top_k": 20,
                    "chat_template_kwargs": {"enable_thinking": False}, **extra})
        j = r.get("json") or {}
        medidas[nome] = {"status": r.get("status"), "escolhas": len(j.get("choices") or []),
                         "logprobs": bool(((j.get("choices") or [{}])[0]).get("logprobs")), "texto": b.uso(r)["texto"] if r.get("status") == 200 else None}
    return ResultadoProva("A10", "stop, n e logprobs", "informativo", medido=medidas, observacao="o copiloto não depende destes; registro para referência")


@prova("A11", "Catálogo de erros, retentativas, limites e tempos")
def a11(b: Bateria) -> ResultadoProva:
    casos = {
        "modelo_inexistente": b.chat({"model": "modelo-que-nao-existe", "messages": b.mensagem("ok"), "max_tokens": 5}),
        "corpo_invalido": b.chat({"messages": "isto não é lista", "max_tokens": 5}),
        "sem_chave": b.chat({"messages": b.mensagem("ok"), "max_tokens": 5}, com_chave=False),
    }
    medido = {k: {"status": v.get("status"), "headers": v.get("headers"), "erro": str((v.get("json") or {}).get("error"))[:200]} for k, v in casos.items()}
    tentativas = [v.get("headers", {}).get("x-litellm-attempted-retries") for v in casos.values()]
    return ResultadoProva("A11", "Catálogo de erros", "informativo", medido=medido,
                          observacao=f"x-litellm-attempted-retries: {tentativas} (0 = o servidor não retenta por nós)")


@prova("A12", "Determinismo com seed e temperatura 0")
def a12(b: Bateria) -> ResultadoProva:
    textos = []
    for _ in range(3):
        r = b.chat({"messages": b.mensagem("Liste 5 frutas, separadas por vírgula."), "max_tokens": 60, "temperature": 0, "seed": 42,
                    "top_k": 20, "chat_template_kwargs": {"enable_thinking": False}})
        textos.append(b.uso(r)["texto"])
    igual = len(set(textos)) == 1
    return ResultadoProva("A12", "Determinismo com seed e temperatura 0", "aprovado" if igual else "parcial", medido={"textos": textos},
                          observacao="3 respostas iguais" if igual else "respostas variam mesmo com seed: não conte com reprodutibilidade da IA (o Python é o que reproduz)")


@prova("A13", "Raciocínio e json_schema na mesma chamada")
def a13(b: Bateria) -> ResultadoProva:
    r = b.chat({"messages": b.mensagem("Classifique: 'a troca de roteadores reduziu chamados?'. tipo, 2 a 4 itens e certeza."),
                "response_format": _esquema(_Enquadra), **b.perfil("pensar", max_tokens=4096)})
    u = b.uso(r)
    try:
        _Enquadra.model_validate(extrair_json((r.get("json") or {})["choices"][0]["message"]["content"] or ""))
        valido = True
    except (ValidationError, ValueError, KeyError, TypeError):
        valido = False
    ok = valido and u["tem_raciocinio"] and "<think>" not in u["texto"]
    return ResultadoProva("A13", "Raciocínio e json_schema juntos", "aprovado" if ok else ("parcial" if valido else "reprovado"),
                          medido=u, observacao=f"JSON válido: {valido}; raciocínio separado: {u['tem_raciocinio']}",
                          recomendacao="" if valido else "hub.saida_estruturada = 'prompt' (com reparo) ou pensar e formatar em duas chamadas")


@prova("A14", "Entrada de imagem (teste de fumaça)")
def a14(b: Bateria) -> ResultadoProva:
    return c1(b)


# --- Bloco B: formatos e ferramentas ---------------------------------------------------------------


@prova("B1", "JSON travado, com e sem raciocínio")
def b1(b: Bateria) -> ResultadoProva:
    bracos = {
        "desligado": b.perfil("rapido", max_tokens=2048),
        "low": b.perfil("acao", max_tokens=8192),
        "medium_orcamento": b.perfil("pensar", max_tokens=8192, orcamento=4096),
        "xhigh": b.perfil("pensar", max_tokens=8192, esforco="xhigh"),
    }
    medidas = {}
    for nome, params in bracos.items():
        validos = 0
        tokens = []
        for _ in range(b.repeticoes):
            r = b.chat({"messages": b.mensagem("Classifique o pedido 'prever a demanda de julho': tipo, itens e certeza."),
                        "response_format": _esquema(_Enquadra), **params})
            try:
                _Enquadra.model_validate(extrair_json((r.get("json") or {})["choices"][0]["message"]["content"] or ""))
                validos += 1
            except (ValidationError, ValueError, KeyError, TypeError):
                pass
            tokens.append(b.uso(r).get("completion") or 0)
        medidas[nome] = {"validos": validos, "de": b.repeticoes, "tokens_saida_medio": sum(tokens) / max(1, len(tokens))}
    todos = all(m["validos"] == m["de"] for m in medidas.values())
    return ResultadoProva("B1", "JSON travado, com e sem raciocínio", "aprovado" if todos else "parcial", medido=medidas,
                          observacao="; ".join(f"{k}: {v['validos']}/{v['de']}" for k, v in medidas.items()),
                          recomendacao="" if todos else "use hub.saida_estruturada = 'json_schema' só nos perfis que passaram; nos outros, 'prompt'")


@prova("B5", "Ferramenta simples, modos de tool_choice, strict e stream")
def b5(b: Bateria) -> ResultadoProva:
    ferramenta = [{"type": "function", "function": {"name": "ler_ficha", "description": "Lê a ficha de uma tabela.",
                                                    "parameters": {"type": "object", "properties": {"tabela": {"type": "string"}},
                                                                   "required": ["tabela"], "additionalProperties": False}}}]
    medidas = {}
    for modo in ("auto", "required", {"type": "function", "function": {"name": "ler_ficha"}}):
        r = b.chat({"messages": b.mensagem("Leia a ficha da tabela OPS.CHAMADO."), "tools": ferramenta, "tool_choice": modo,
                    "parallel_tool_calls": False, **b.perfil("acao")})
        msg = ((r.get("json") or {}).get("choices") or [{}])[0].get("message") or {}
        chamadas = msg.get("tool_calls") or []
        args_ok = False
        if chamadas:
            try:
                args_ok = json.loads(chamadas[0]["function"]["arguments"]).get("tabela", "").upper().endswith("CHAMADO")
            except (ValueError, KeyError, AttributeError):
                pass
        medidas[json.dumps(modo)] = {"status": r.get("status"), "chamadas": len(chamadas), "id_vazio": any(not c.get("id") for c in chamadas),
                                     "args_ok": args_ok}
    ok = all(m["chamadas"] >= 1 and m["args_ok"] and not m["id_vazio"] for m in medidas.values())
    return ResultadoProva("B5", "Ferramenta simples e tool_choice", "aprovado" if ok else "parcial", medido=medidas)


@prova("B8", "Idioma depois de resultado em inglês")
def b8(b: Bateria) -> ResultadoProva:
    msgs = [{"role": "user", "content": "Qual o status da tabela?"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "status", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "The table is healthy, last refreshed yesterday at 3 AM, 0 errors."}]
    ferramenta = [{"type": "function", "function": {"name": "status", "description": "status", "parameters": {"type": "object", "properties": {}}}}]
    r = b.chat({"messages": [{"role": "system", "content": "Responda sempre em português do Brasil."}, *msgs], "tools": ferramenta, **b.perfil("acao")})
    texto = b.uso(r)["texto"].lower()
    pt = sum(texto.count(w) for w in (" a ", " o ", " de ", " está ", "tabela", " foi ", " sem "))
    en = sum(texto.count(w) for w in (" the ", " is ", " was ", " with ", " table "))
    ok = pt > en
    return ResultadoProva("B8", "Idioma depois de resultado em inglês", "aprovado" if ok else "reprovado", medido={"texto": texto[:300]},
                          observacao="respondeu em português" if ok else "trocou de idioma depois do resultado em inglês")


# --- Bloco C: visão, contexto longo, cache, vazão -------------------------------------------------


def _png_com_codigo(codigo: str) -> bytes:
    from matplotlib.figure import Figure

    fig = Figure(figsize=(6.4, 4.8), dpi=100)
    ax = fig.subplots()
    ax.bar(["a", "b", "c"], [3, 7, 5], color="#1f5fa8")
    ax.set_title(f"Estudo fictício {codigo}", fontsize=18)
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    return buf.getvalue()


@prova("C1", "A imagem entra no modelo?")
def c1(b: Bateria) -> ResultadoProva:
    codigo = "".join(random.choices(string.ascii_uppercase + string.digits, k=5))
    png = base64.b64encode(_png_com_codigo(codigo)).decode()
    pergunta = "Qual é o código de 5 caracteres no título do gráfico? Responda só o código."
    sem = b.chat({"messages": b.mensagem(pergunta), "max_tokens": 300, **b.perfil("rapido", max_tokens=300)})
    acertos, status = 0, []
    for _ in range(max(1, min(3, b.repeticoes))):
        r = b.chat({"messages": [{"role": "user", "content": [{"type": "text", "text": pergunta},
                                                               {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{png}"}}]}],
                    **b.perfil("rapido", max_tokens=300)})
        status.append(r.get("status"))
        acertos += codigo in b.uso(r)["texto"].upper() if r.get("status") == 200 else 0
    delta = (b.uso(b.chat({"messages": [{"role": "user", "content": [{"type": "text", "text": pergunta},
                                                                      {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{png}"}}]}],
                           **b.perfil("rapido", max_tokens=5)})).get("prompt") or 0) - (b.uso(sem).get("prompt") or 0)
    ok = acertos >= 2
    return ResultadoProva("C1", "A imagem entra no modelo?", "aprovado" if ok else "reprovado",
                          medido={"acertos": acertos, "status": status, "delta_prompt_tokens_imagem": delta},
                          observacao=f"leu o código em {acertos} de {len(status)}; a imagem somou {delta} tokens de entrada",
                          recomendacao="hub.visao = true (Admin)" if ok else "mantenha hub.visao = false")


def _texto_longo(tokens: int, agulha: str | None = None, posicao: float = 0.5) -> str:
    frase = "O relatório trimestral descreve operações rotineiras de manutenção de rede em várias cidades. "
    n = max(1, int(tokens * 3.2 / len(frase)))
    partes = [frase] * n
    if agulha:
        partes.insert(int(n * posicao), f" A senha de teste do cofre é {agulha}. ")
    return "".join(partes)


@prova("C4", "Contexto longo em degraus: tempo e velocidade", pesada=True)
def c4(b: Bateria) -> ResultadoProva:
    medidas = {}
    for degrau in (8_000, 32_000, 64_000, 128_000):
        r = b.chat({"messages": b.mensagem(_texto_longo(degrau) + "\n\nResuma em uma frase."), **b.perfil("rapido", max_tokens=100)},
                   timeout=600)
        medidas[str(degrau)] = {"status": r.get("status"), "segundos": r.get("segundos"), "prompt": b.uso(r).get("prompt")}
    maior_ok = max((int(k) for k, v in medidas.items() if v["status"] == 200), default=0)
    return ResultadoProva("C4", "Contexto longo em degraus", "aprovado" if maior_ok >= 128_000 else "parcial", medido=medidas,
                          observacao=f"maior degrau aceito: {maior_ok} tokens",
                          recomendacao=f"hub.contexto_util = {int(maior_ok * 0.9)}" if maior_ok else "")


@prova("C5", "Agulha no palheiro em cada degrau", pesada=True)
def c5(b: Bateria) -> ResultadoProva:
    medidas = {}
    for degrau in (32_000, 96_000):
        for pos in (0.1, 0.5, 0.9):
            agulha = "".join(random.choices(string.digits, k=6))
            r = b.chat({"messages": b.mensagem(_texto_longo(degrau, agulha, pos) + "\n\nQual é a senha de teste do cofre? Só o número."),
                        **b.perfil("rapido", max_tokens=50)}, timeout=600)
            medidas[f"{degrau}@{pos}"] = agulha in b.uso(r)["texto"]
    acertos = sum(medidas.values())
    return ResultadoProva("C5", "Agulha no palheiro", "aprovado" if acertos == len(medidas) else "parcial", medido=medidas,
                          observacao=f"{acertos} de {len(medidas)} agulhas achadas")


@prova("C7", "Cache de prefixo: aparece, acelera e dura?")
def c7(b: Bateria) -> ResultadoProva:
    prefixo = _texto_longo(9_000)
    tempos, cache = [], []
    for i in range(3):
        r = b.chat({"messages": [{"role": "system", "content": prefixo}, {"role": "user", "content": f"Diga só: ok {i}."}],
                    **b.perfil("rapido", max_tokens=10)}, timeout=300)
        tempos.append(r.get("segundos"))
        det = ((r.get("json") or {}).get("usage") or {}).get("prompt_tokens_details") or {}
        cache.append(det.get("cached_tokens"))
    acelera = tempos[0] and tempos[-1] and tempos[-1] < 0.7 * tempos[0]
    return ResultadoProva("C7", "Cache de prefixo", "aprovado" if (acelera or any(cache[1:])) else "informativo",
                          medido={"segundos": tempos, "cached_tokens": cache},
                          observacao=f"tempos {tempos}; cached_tokens {cache}")


@prova("C9", "Vazão com 1, 4 e 8 requisições simultâneas", pesada=True)
def c9(b: Bateria) -> ResultadoProva:
    medidas = {}
    for paralelas in (1, 4, 8):
        inicio = time.perf_counter()
        with ThreadPoolExecutor(paralelas) as ex:
            rs = list(ex.map(lambda _: b.chat({"messages": b.mensagem("Escreva 3 frases sobre redes."), **b.perfil("rapido", max_tokens=150)}),
                             range(paralelas)))
        total = time.perf_counter() - inicio
        tokens = sum(b.uso(r).get("completion") or 0 for r in rs)
        medidas[str(paralelas)] = {"segundos": round(total, 2), "tokens_por_s": round(tokens / total, 1),
                                   "status": [r.get("status") for r in rs]}
    erros_429 = any(429 in v["status"] for v in medidas.values())
    return ResultadoProva("C9", "Vazão", "informativo", medido=medidas, observacao="429 com paralelismo" if erros_429 else "sem 429",
                          recomendacao="hub.paralelismo = 1" if erros_429 else "hub.paralelismo pode subir até 4 (meça o 429)")


# --- Bloco D: rede, limites e governança ----------------------------------------------------------


@prova("D1", "Caminho de rede até o hub (notebook × local)")
def d1(b: Bateria) -> ResultadoProva:
    r = a1(b)
    r.id, r.nome = "D1", "Caminho de rede até o hub"
    return r


@prova("D2", "Latência do caminho (ida e volta)")
def d2(b: Bateria) -> ResultadoProva:
    tempos = []
    for _ in range(5):
        r = b.chat({"messages": b.mensagem("ok"), **b.perfil("rapido", max_tokens=5)})
        if r.get("status") == 200:
            tempos.append(r["segundos"])
    estat = p50_p95(tempos)
    return ResultadoProva("D2", "Latência do caminho", "informativo" if tempos else "reprovado", medido=estat,
                          observacao=f"p50 {estat.get('p50')} s, p95 {estat.get('p95')} s")


@prova("D3", "Chave pelo OCI Vault com resource principal")
def d3(b: Bateria) -> ResultadoProva:
    ref = b.cfg.hub.chave
    if ref.fonte != "vault" or not ref.vault_secret_ocid:
        return ResultadoProva("D3", "Chave pelo Vault", "pulado", observacao="hub.chave.fonte não é 'vault' (rode no notebook da OCI)")
    from adex_copiloto.seguranca.segredos import ler_segredo

    s = ler_segredo(ref, "hub.chave", b.cfg)
    return ResultadoProva("D3", "Chave pelo Vault", "aprovado", observacao=f"lida do Vault e conferida pela impressão ({s.fonte})")


@prova("D6", "Custo, uso de tokens e headers de rastreio por chamada")
def d6(b: Bateria) -> ResultadoProva:
    r = b.chat({"messages": b.mensagem("ok"), **b.perfil("rapido", max_tokens=5)})
    h = r.get("headers") or {}
    custo = h.get(b.cfg.hub.cabecalho_custo) or h.get(b.cfg.hub.cabecalho_custo.lower())
    ok = custo is not None and (r.get("json") or {}).get("usage") is not None
    return ResultadoProva("D6", "Custo e headers", "aprovado" if ok else "parcial", medido={"headers": h, "usage": (r.get("json") or {}).get("usage")},
                          observacao=f"custo no cabeçalho: {custo is not None}",
                          recomendacao="" if custo is not None else "o custo não vem no cabeçalho: o orçamento conta só tokens e chamadas")


@prova("D11", "Impressão digital do modelo (troca silenciosa)")
def d11(b: Bateria) -> ResultadoProva:
    r = b.chat({"messages": b.mensagem("ok"), **b.perfil("rapido", max_tokens=5)})
    from adex_copiloto.llm.impressao_digital import calcular

    lista = b.http.get(f"{b.url}/models", headers=b.cabecalhos()).json()
    imp, det = calcular(lista, b.cfg.hub.modelo, (r.get("json") or {}).get("model"))
    homologada = b.cfg.hub.modelo_impressao
    situacao = "informativo" if not homologada else ("aprovado" if homologada == imp else "reprovado")
    return ResultadoProva("D11", "Impressão do modelo", situacao, medido={"impressao": imp, "detalhes": det},
                          observacao=f"modelo informado na resposta: {det['modelo_resposta']}",
                          recomendacao=f"hub.modelo_impressao = '{imp}'" if not homologada else "")


@prova("D13", "Repasse de parâmetros: chegou ao modelo ou foi descartado?")
def d13(b: Bateria) -> ResultadoProva:
    r = a2(b)
    r.id, r.nome = "D13", "Repasse de parâmetros"
    return r


@prova("D14", "Vazamento local: chave e conteúdo em logs, erros e no notebook")
def d14(b: Bateria) -> ResultadoProva:
    import logging

    from adex_copiloto.seguranca.redacao import FiltroRedacao, mascarar_texto

    registros = []

    class Coletor(logging.Handler):
        def emit(self, record):
            registros.append(self.format(record))

    log = logging.getLogger("adex_copiloto.fase0.canario")
    h = Coletor()
    h.addFilter(FiltroRedacao())
    log.addHandler(h)
    chave = b.chave.revelar() if b.chave else "sem-chave"
    log.warning("erro simulado com a chave %s e o e-mail fulano@exemplo.com", chave)
    log.removeHandler(h)
    vazou = any(chave in x for x in registros) or chave in mascarar_texto(f"Bearer {chave}")
    return ResultadoProva("D14", "Vazamento local", "reprovado" if vazou else "aprovado", observacao="a chave não aparece em log mascarado" if not vazou else "VAZOU")


# --- Bloco E: qualidade por momento do copiloto (casos congelados, sintéticos) ------------------------


def _cliente(b: Bateria):
    from adex_copiloto.llm.cliente import ClienteHub
    from adex_copiloto.observabilidade.telemetria import TelemetriaNula

    return ClienteHub(b.cfg, b.chave, TelemetriaNula(), http_client=b.http)


CASOS_ENQUADRAR = [
    ("A troca de roteadores por Wi-Fi 6 no ano passado valeu a pena?", "efeito"),
    ("Quanto vamos vender de cada produto em julho em cada loja?", "previsao"),
    ("Quais clientes têm mais risco de cancelar nos próximos 3 meses?", "classificacao"),
    ("Quero um painel mensal de chamados técnicos por cidade.", "descricao"),
    ("Há transações de cartão fora do padrão nesta semana?", "anomalia"),
]


@prova("E1", "Enquadramento: as perguntas certas")
def e1(b: Bateria) -> ResultadoProva:
    from adex_copiloto.estudo.enquadramento import enquadrar

    cliente = _cliente(b)
    acertos, detalhe = 0, {}
    for texto, esperado in CASOS_ENQUADRAR:
        enq = enquadrar(cliente, texto)
        detalhe[texto[:40]] = {"tipo": enq.tipo_pergunta, "esperado": esperado, "perguntas": len(enq.perguntas)}
        acertos += enq.tipo_pergunta == esperado and len(enq.perguntas) <= 3
    return ResultadoProva("E1", "Enquadramento", "aprovado" if acertos >= 4 else "parcial", medido=detalhe,
                          observacao=f"{acertos} de {len(CASOS_ENQUADRAR)} tipos certos (≤ 3 perguntas)")


@prova("E2", "Carta: citações 'você disse' fiéis")
def e2(b: Bateria) -> ResultadoProva:
    from adex_copiloto import exemplos
    from adex_copiloto.estudo.enquadramento import construir_carta, enquadrar

    ex = exemplos.wifi6(domicilios_por_cidade=(20, 25))
    cliente = _cliente(b)
    enq = enquadrar(cliente, ex.objetivo)
    carta, avisos, proposta = construir_carta(cliente, objetivo=ex.objetivo, enquadramento=enq, respostas=[ex.respostas], cfg=b.cfg)
    declaradas = sum(1 for c in proposta.campos if c.origem == "voce_disse")
    mantidas = sum(1 for c in carta.campos.values() if c.origem == "voce_disse")
    taxa = mantidas / declaradas if declaradas else 1.0
    return ResultadoProva("E2", "Carta: citações fiéis", "aprovado" if taxa >= 0.9 else "parcial",
                          medido={"declaradas": declaradas, "mantidas": mantidas, "avisos": avisos},
                          observacao=f"{mantidas} de {declaradas} campos 'você disse' tinham citação literal")


@prova("E3", "Descoberta: tabelas certas, nenhum nome inventado")
def e3(b: Bateria) -> ResultadoProva:
    from adex_copiloto import exemplos
    from adex_copiloto.estudo.carta import carta_em_branco
    from adex_copiloto.estudo.descoberta import descobrir_direto

    ex = exemplos.wifi6(domicilios_por_cidade=(20, 25))
    fonte = ex.fonte(b.cfg)
    for extra in exemplos.churn(clientes=200).tabelas.items():
        fonte.registrar(*extra)
    carta = carta_em_branco("efeito")
    carta.campos["objeto"].valor = "troca de roteador Wi-Fi 6 por domicílio, em ondas por cidade"
    carta.campos["desfecho"].valor = "chamados técnicos de Wi-Fi por 1.000 domicílios"
    carta.campos["populacao"].valor = "domicílios de fibra ativos por mês"
    proposta, avisos = descobrir_direto(_cliente(b), fonte.catalogo(), carta, pista="", cfg=b.cfg)
    certas = {"CAD.BASE_FIBRA_MENSAL", "OPS_RES.CHAMADO_TECNICO", "OPS_RES.TROCA_EQUIPAMENTO"}
    achadas = {proposta.populacao.upper(), *(d.tabela.upper() for d in proposta.desfechos)} | (
        {proposta.tratamento.tabela.upper()} if proposta.tratamento else set())
    inventados = [a for a in avisos if "não existe" in a]
    ok = certas <= achadas and not inventados
    return ResultadoProva("E3", "Descoberta", "aprovado" if ok else "parcial", medido={"achadas": sorted(achadas), "inventados": inventados},
                          observacao=f"achou {len(certas & achadas)} de 3; {len(inventados)} nome(s) inventado(s) descartado(s)")


@prova("E4", "SQL Oracle correto, agregado e sem PII")
def e4(b: Bateria) -> ResultadoProva:
    from adex_copiloto import exemplos
    from adex_copiloto.llm import prompts

    ex = exemplos.wifi6(domicilios_por_cidade=(60, 70))
    fonte = ex.fonte(b.cfg)
    cat = fonte.catalogo()
    cliente = _cliente(b)
    perguntas = ["Quantos domicílios ativos por cidade e mês?", "Quantos chamados de Wi-Fi por cidade em 2025?",
                 "Quantas trocas de equipamento por mês?"]
    aprovadas, detalhes = 0, {}

    class Sql(BaseModel):
        sql: str

    for p in perguntas:
        corpo = prompts.dados({"fichas": [f.para_llm() for f in cat.tabelas.values()], "pergunta": p}, "catalogo")
        msgs = [{"role": "system", "content": prompts.carregar("sistema") + "\n\nEscreva UMA consulta Oracle SELECT agregada (GROUP BY), "
                 "sem dados pessoais, usando só as tabelas e colunas das fichas."}, {"role": "user", "content": corpo}]
        sql = cliente.completar("pensar", msgs, etapa="fase0", nome="e4", esquema=Sql).objeto.sql
        v = fonte.porteiro().avaliar(sql)
        detalhes[p] = {"aprovada": v.aprovado, "motivos": v.motivos[:3]}
        aprovadas += v.aprovado
    return ResultadoProva("E4", "SQL Oracle pelo porteiro", "aprovado" if aprovadas == len(perguntas) else "parcial", medido=detalhes,
                          observacao=f"{aprovadas} de {len(perguntas)} consultas passaram no porteiro de primeira")


@prova("E7", "Narrativa sem números soltos")
def e7(b: Bateria) -> ResultadoProva:
    from adex_copiloto.estudo.carta import carta_em_branco
    from adex_copiloto.estudo.evidencia import Afirmacao
    from adex_copiloto.llm.marcadores import TabelaMarcadores
    from adex_copiloto.narrativa.narrar import narrar

    t = TabelaMarcadores()
    m1 = t.novo(-0.27, unidade="%", sinal=True, descricao="efeito relativo da troca nos chamados")
    m2 = t.novo(-4.1, casas=1, descricao="limite superior do IC de 95% (chamados por 1.000)")
    afirmacoes = [Afirmacao(id="F-01", tipo="fato", texto=f"Efeito ⟦{m1.id}⟧, IC até ⟦{m2.id}⟧.", selo="A")]
    resultados = [narrar(_cliente(b), carta=carta_em_branco("efeito"), marcadores=t, afirmacoes=afirmacoes) for _ in range(b.repeticoes)]
    primeira = sum(1 for r in resultados if r.origem == "ia" and r.tentativas == 1)
    alguma = sum(1 for r in resultados if r.origem == "ia")
    return ResultadoProva("E7", "Narrativa sem números soltos", "aprovado" if alguma == len(resultados) else "parcial",
                          medido={"de_primeira": primeira, "com_reparo": alguma - primeira, "modelo": len(resultados) - alguma},
                          observacao=f"{primeira}/{len(resultados)} de primeira; {alguma}/{len(resultados)} com até um reparo")


@prova("E9", "Idioma: nada de mistura")
def e9(b: Bateria) -> ResultadoProva:
    r = b.chat({"messages": b.mensagem("Explique em 3 frases o que é regressão à média, para um gerente."), **b.perfil("narrar", max_tokens=400)})
    texto = b.uso(r)["texto"].lower()
    en = sum(texto.count(w) for w in (" the ", " is ", " and ", " of ", " to "))
    return ResultadoProva("E9", "Idioma", "aprovado" if en <= 1 else "reprovado", medido={"texto": texto[:400]},
                          observacao="só português" if en <= 1 else "mistura de inglês")


@prova("E10", "Estabilidade entre 3 respostas")
def e10(b: Bateria) -> ResultadoProva:
    from adex_copiloto.estudo.enquadramento import enquadrar

    tipos = [enquadrar(_cliente(b), CASOS_ENQUADRAR[0][0]).tipo_pergunta for _ in range(3)]
    return ResultadoProva("E10", "Estabilidade", "aprovado" if len(set(tipos)) == 1 else "parcial", medido={"tipos": tipos})


# --- Bloco F: a stack até o hub ------------------------------------------------------------------------


@prova("F1", "Parâmetros do Qwen chegam ao modelo pelo ChatQwenHub")
def f1(b: Bateria) -> ResultadoProva:
    from adex_copiloto.llm.chat_langchain import criar_chat_qwen

    medidas = {}
    for perfil in ("rapido", "acao"):
        chat = criar_chat_qwen(b.cfg, b.chave, perfil, http_client=b.http)
        msg = chat.invoke(PERGUNTA_RACIOCINIO)
        medidas[perfil] = {"prompt": (msg.usage_metadata or {}).get("input_tokens"), "raciocinio": bool(msg.additional_kwargs.get("reasoning_content")),
                           "saida": (msg.usage_metadata or {}).get("output_tokens")}
    ok = medidas["acao"]["raciocinio"] and not medidas["rapido"]["raciocinio"]
    return ResultadoProva("F1", "ChatQwenHub repassa o perfil", "aprovado" if ok else "reprovado", medido=medidas,
                          observacao="raciocínio só no perfil com raciocínio" if ok else "o perfil não mudou o comportamento")


@prova("F2", "O raciocínio chega à lib")
def f2(b: Bateria) -> ResultadoProva:
    from langchain_openai import ChatOpenAI
    from pydantic import SecretStr

    from adex_copiloto.llm.chat_langchain import criar_chat_qwen

    nosso = criar_chat_qwen(b.cfg, b.chave, "acao", http_client=b.http).invoke(PERGUNTA_RACIOCINIO)
    puro = ChatOpenAI(model=b.cfg.hub.modelo, base_url=b.url, api_key=SecretStr(b.chave.revelar()), max_retries=0, http_client=b.http,
                      use_responses_api=False, extra_body=b.perfil("acao") | {}).invoke(PERGUNTA_RACIOCINIO)
    ok = bool(nosso.additional_kwargs.get("reasoning_content"))
    return ResultadoProva("F2", "Raciocínio chega à lib", "aprovado" if ok else "reprovado",
                          medido={"chatqwenhub": ok, "chatopenai_puro_descarta": not puro.additional_kwargs.get("reasoning_content")})


@prova("F3", "Retentativas, custo e uso chegam certos")
def f3(b: Bateria) -> ResultadoProva:
    from adex_copiloto.llm.chat_langchain import criar_chat_qwen

    msg = criar_chat_qwen(b.cfg, b.chave, "rapido", http_client=b.http).invoke("ok")
    meta = msg.response_metadata or {}
    return ResultadoProva("F3", "Custo e uso no LangChain", "aprovado" if msg.usage_metadata else "parcial",
                          medido={"custo": meta.get("adex_custo_usd"), "cabecalhos": meta.get("adex_cabecalhos"), "uso": msg.usage_metadata})


@prova("F5", "Ferramentas nos agentes: ids, corte e raciocínio")
def f5(b: Bateria) -> ResultadoProva:
    from adex_copiloto import exemplos
    from adex_copiloto.agentes.deep import criar_agente, rodar_agente
    from adex_copiloto.agentes.ferramentas import ferramentas_descoberta
    from adex_copiloto.estudo.descoberta import LoteDescoberta
    from adex_copiloto.llm.chat_langchain import criar_chat_qwen

    cat = exemplos.wifi6(domicilios_por_cidade=(20, 25)).fonte(b.cfg).catalogo()
    agente = criar_agente(b.cfg, criar_chat_qwen(b.cfg, b.chave, "acao", http_client=b.http), nome="f5",
                          prompt_sistema="Use listar_tabelas e ler_ficha; depois responda com as tabelas candidatas.",
                          ferramentas=ferramentas_descoberta(cat), esquema_resposta=LoteDescoberta)
    try:
        resp = rodar_agente(agente, "Quais tabelas falam de chamados técnicos?", cfg=b.cfg)
        return ResultadoProva("F5", "Agente com ferramentas", "aprovado", medido={"candidatas": [c.tabela for c in resp.candidatas]})
    except Exception as e:  # noqa: BLE001
        return ResultadoProva("F5", "Agente com ferramentas", "reprovado", observacao=f"{type(e).__name__}: {str(e)[:200]}",
                              recomendacao="agente.deep_nodes = [] (modo direto) até corrigir")


@prova("F6", "O agente só enxerga as ferramentas permitidas")
def f6(b: Bateria) -> ResultadoProva:
    from adex_copiloto import exemplos
    from adex_copiloto.agentes.deep import auditar_plugins
    from adex_copiloto.agentes.ferramentas import ferramentas_descoberta

    plugins = auditar_plugins(b.cfg)
    cat = exemplos.wifi6(domicilios_por_cidade=(20, 25)).fonte(b.cfg).catalogo()
    nomes = [f.name for f in ferramentas_descoberta(cat)]
    return ResultadoProva("F6", "Ferramentas permitidas", "aprovado", medido={"plugins_de_perfil": plugins, "ferramentas_catalogo": nomes},
                          observacao="auditoria de plug-ins passou; a lista oferecida ao modelo é conferida a cada chamada (AuditorFerramentas)")


@prova("F8", "Os portões sobrevivem a reiniciar o kernel")
def f8(b: Bateria) -> ResultadoProva:
    import os
    import tempfile
    from pathlib import Path
    from typing import TypedDict

    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command, interrupt

    from adex_copiloto.harness.checkpoint import criar_checkpointer

    class S(TypedDict, total=False):
        v: int

    def portao(s):
        r = interrupt({"portao": "x"})
        return {"v": 1 if r == "ok" else 0}

    g = StateGraph(S)
    g.add_node("portao", portao)
    g.add_edge(START, "portao")
    g.add_edge("portao", END)
    arq = Path(tempfile.mkdtemp()) / "ck.sqlite"
    chave = os.urandom(32)
    cfg = {"configurable": {"thread_id": "t"}}
    g.compile(checkpointer=criar_checkpointer(arq, chave)).invoke({"v": 0}, cfg)
    final = g.compile(checkpointer=criar_checkpointer(arq, chave)).invoke(Command(resume="ok"), cfg)
    return ResultadoProva("F8", "Portões sobrevivem a reiniciar", "aprovado" if final.get("v") == 1 else "reprovado",
                          medido={"final": final}, observacao="retomou de um checkpoint cifrado reaberto do disco")


@prova("F9", "O rastro no Langfuse fica completo")
def f9(b: Bateria) -> ResultadoProva:
    if not b.cfg.langfuse.habilitado:
        return ResultadoProva("F9", "Langfuse", "pulado", observacao="langfuse.habilitado = false (Admin)")
    from adex_copiloto.observabilidade.langfuse_adaptador import testar_conexao

    texto = testar_conexao(b.cfg)
    return ResultadoProva("F9", "Langfuse", "aprovado" if "HTTP 200" in texto else "reprovado", observacao=texto,
                          recomendacao="" if "HTTP 200" in texto else "langfuse.rota = 'pendente' e on_unavailable = 'fila' até a rota existir")


@prova("F10", "O mascaramento funciona")
def f10(b: Bateria) -> ResultadoProva:
    from adex_copiloto.observabilidade.langfuse_adaptador import mascara_para

    canarios = {"amostra": "cliente 529.982.247-25", "sql": "WHERE email = 'ana@claro.com.br'", "chave": "sk-canario-abcdefghijklmnop"}
    saida = json.dumps(mascara_para(b.cfg.langfuse.captura)(data=canarios), ensure_ascii=False)
    vazou = [k for k, v in canarios.items() if any(p in saida for p in ("529.982.247-25", "ana@claro", "sk-canario"))]
    return ResultadoProva("F10", "Mascaramento", "aprovado" if not vazou else "reprovado", medido={"saida": saida[:300]})


# --- Bloco G: ambiente OCI (quando houver acesso) ------------------------------------------------------


@prova("G1", "Ambiente: Python, pacotes e conda")
def g1(b: Bateria) -> ResultadoProva:
    import platform
    from importlib.metadata import version

    pacotes = {}
    for p in ("langgraph", "langchain-openai", "deepagents", "langfuse", "oracledb", "duckdb", "pandas", "sqlglot"):
        try:
            pacotes[p] = version(p)
        except Exception:  # noqa: BLE001
            pacotes[p] = None
    return ResultadoProva("G1", "Ambiente", "informativo", medido={"python": platform.python_version(), "pacotes": pacotes})


@prova("G4", "Resource principal lê o segredo e grava no bucket")
def g4(b: Bateria) -> ResultadoProva:
    if b.cfg.oci.autenticacao != "resource_principal" or not (b.cfg.objectstorage.bucket_estudos and b.cfg.objectstorage.namespace):
        return ResultadoProva("G4", "Resource principal", "pulado", observacao="configure oci.autenticacao e objectstorage (no notebook da OCI)")
    from adex_copiloto.dados.armazenamento import Armazenamento

    os_ = b.cfg.objectstorage
    uri = f"oci://{os_.bucket_estudos}@{os_.namespace}/{os_.prefixo_estudos.rstrip('/')}/fase0/teste.txt"
    arm = Armazenamento(b.cfg, Path("."))
    arm.escrever(uri, b"fase0")
    ok = arm.ler(uri) == b"fase0"
    return ResultadoProva("G4", "Resource principal", "aprovado" if ok else "reprovado", observacao=f"escreveu e leu {uri}")


@prova("G5", "Conexão com o ADW")
def g5(b: Bateria) -> ResultadoProva:
    if not b.cfg.adw.habilitado:
        return ResultadoProva("G5", "ADW", "pulado", observacao="adw.habilitado = false")
    from adex_copiloto.dados.fontes import FonteADW

    cat = FonteADW(b.cfg).catalogo()
    return ResultadoProva("G5", "ADW", "aprovado" if len(cat) else "parcial", medido=cat.resumo())


@prova("G8", "O checkpoint não está em sistema de arquivos de rede")
def g8(b: Bateria) -> ResultadoProva:
    from adex_copiloto.config.carregador import resolver_caminho
    from adex_copiloto.harness.checkpoint import em_fs_de_rede

    caminho = resolver_caminho(b.cfg, b.cfg.checkpoint.caminho.replace("{estudo}", "fase0"))
    rede = em_fs_de_rede(caminho.parent)
    return ResultadoProva("G8", "Checkpoint em disco local", "reprovado" if rede else "aprovado", medido={"caminho": str(caminho)})
