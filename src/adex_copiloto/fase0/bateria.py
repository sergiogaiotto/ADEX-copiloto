"""Bateria executável da Fase 0 (homologação do GPU Hub), com os ids de docs/fase0/.

Princípios:
- a chave nunca aparece: tudo o que é gravado passa pela redação;
- cada prova mede EFEITO (o vLLM aceita e ignora campos desconhecidos em silêncio);
- provas caras (contexto longo, vazão) só com ``--pesado``;
- provas que dependem da OCI rodam quando configuradas; senão ficam "puladas" com o motivo;
- o resultado sai em ``fase0-resultados/<data-hora>/`` (JSON + Markdown), para o
  relatório de homologação e para calibrar ``[hub]`` e ``[perfis]``.
"""

from __future__ import annotations

import datetime as dt
import json
import statistics
import time
import traceback
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx

from adex_copiloto.config.modelos import Config
from adex_copiloto.llm.perfis import montar_requisicao
from adex_copiloto.seguranca.redacao import mascarar
from adex_copiloto.seguranca.segredos import Segredo

SITUACOES = ("aprovado", "parcial", "reprovado", "informativo", "pulado", "erro")


@dataclass
class ResultadoProva:
    id: str
    nome: str
    situacao: str
    medido: dict[str, Any] = field(default_factory=dict)
    observacao: str = ""
    segundos: float = 0.0
    recomendacao: str = ""


@dataclass
class Prova:
    id: str
    nome: str
    bloco: str
    funcao: Callable[["Bateria"], ResultadoProva]
    pesada: bool = False


REGISTRO: dict[str, Prova] = {}


def prova(id_: str, nome: str, *, pesada: bool = False):
    def decorar(func):
        REGISTRO[id_] = Prova(id_, nome, id_[0], func, pesada)
        return func

    return decorar


class Bateria:
    def __init__(self, cfg: Config, chave: Segredo | None, *, http_client: httpx.Client | None = None, pesado: bool = False,
                 repeticoes: int = 3, saida: Path | None = None) -> None:
        if chave is not None:
            from adex_copiloto.seguranca.segredos import conferir_endpoint_hub

            conferir_endpoint_hub(cfg)
        self.cfg = cfg
        self.chave = chave
        self.http = http_client or httpx.Client(verify=cfg.hub.ca_bundle or True, proxy=cfg.hub.proxy or None,
                                                timeout=httpx.Timeout(cfg.hub.timeout_s, connect=cfg.hub.timeout_conexao_s))
        self.pesado = pesado
        self.repeticoes = max(1, repeticoes)
        self.saida = saida or Path("fase0-resultados") / dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        self.url = cfg.hub.base_url.rstrip("/")
        self.memoria: dict[str, Any] = {}

    # --- chamadas cruas -----------------------------------------------------------------------

    def cabecalhos(self, com_chave: bool = True) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if com_chave and self.chave is not None:
            h["Authorization"] = f"Bearer {self.chave.revelar()}"
        return h

    def chat(self, corpo: dict[str, Any], *, com_chave: bool = True, timeout: float | None = None) -> dict[str, Any]:
        """POST /chat/completions cru. Devolve status, json, cabeçalhos úteis e tempo."""
        corpo = {"model": self.cfg.hub.modelo, **corpo}
        inicio = time.perf_counter()
        try:
            r = self.http.post(f"{self.url}/chat/completions", json=corpo, headers=self.cabecalhos(com_chave),
                               timeout=timeout or self.cfg.hub.timeout_s)
        except httpx.HTTPError as e:
            return {"status": None, "erro": type(e).__name__, "segundos": round(time.perf_counter() - inicio, 3)}
        segundos = round(time.perf_counter() - inicio, 3)
        try:
            dados = r.json()
        except ValueError:
            dados = {"texto": r.text[:500]}
        uteis = {k: v for k, v in r.headers.items() if k.lower().startswith(("x-litellm", "x-ratelimit", "x-request", "retry-after"))}
        return {"status": r.status_code, "json": dados, "headers": uteis, "segundos": segundos}

    def mensagem(self, texto: str, *, sistema: str | None = None) -> list[dict[str, Any]]:
        msgs = [{"role": "system", "content": sistema}] if sistema else []
        return msgs + [{"role": "user", "content": texto}]

    def perfil(self, nome: str, **ajustes) -> dict[str, Any]:
        req = montar_requisicao(self.cfg.perfis[nome], self.cfg.hub, **ajustes)
        extra = req.pop("extra_body")
        return {**req, **extra}

    @staticmethod
    def uso(r: dict[str, Any]) -> dict[str, Any]:
        j = r.get("json") or {}
        u = j.get("usage") or {}
        det = u.get("completion_tokens_details") or {}
        escolha = (j.get("choices") or [{}])[0]
        msg = escolha.get("message") or {}
        raciocinio = msg.get("reasoning_content") or msg.get("reasoning") or ""
        return {"prompt": u.get("prompt_tokens"), "completion": u.get("completion_tokens"), "reasoning_tokens": det.get("reasoning_tokens"),
                "finish": escolha.get("finish_reason"), "texto": (msg.get("content") or "")[:300], "tem_raciocinio": bool(raciocinio),
                "campo_raciocinio": "reasoning_content" if msg.get("reasoning_content") else ("reasoning" if msg.get("reasoning") else None),
                "tamanho_raciocinio": len(raciocinio), "modelo": j.get("model")}

    # --- execução ----------------------------------------------------------------------------------

    def rodar(self, *, blocos: list[str] | None = None, ids: list[str] | None = None) -> list[ResultadoProva]:
        from adex_copiloto.fase0 import provas  # noqa: F401 - registra as provas

        alvo = [p for p in REGISTRO.values() if (not ids or p.id in ids) and (not blocos or p.bloco in blocos)]
        resultados = []
        for p in sorted(alvo, key=lambda x: (x.bloco, int("".join(ch for ch in x.id[1:] if ch.isdigit()) or 0))):
            if p.pesada and not self.pesado:
                resultados.append(ResultadoProva(p.id, p.nome, "pulado", observacao="prova pesada: rode com --pesado"))
                continue
            inicio = time.perf_counter()
            try:
                r = p.funcao(self)
            except Exception as e:  # noqa: BLE001
                r = ResultadoProva(p.id, p.nome, "erro", observacao=f"{type(e).__name__}: {str(e)[:300]}",
                                   medido={"rastro": traceback.format_exc(limit=3)[-1500:]})
            r.segundos = round(time.perf_counter() - inicio, 2)
            r.medido = mascarar(json.loads(json.dumps(r.medido, default=str)))
            r.observacao = mascarar(r.observacao)
            resultados.append(r)
        return resultados

    def salvar(self, resultados: list[ResultadoProva]) -> Path:
        self.saida.mkdir(parents=True, exist_ok=True)
        dados = {"gerado_em": dt.datetime.now(dt.timezone.utc).isoformat(), "hub": self.url, "modelo": self.cfg.hub.modelo,
                 "pesado": self.pesado, "repeticoes": self.repeticoes, "resultados": [asdict(r) for r in resultados]}
        (self.saida / "resultados.json").write_text(json.dumps(mascarar(dados), ensure_ascii=False, indent=1), encoding="utf-8")
        linhas = ["# Fase 0 · resultados da bateria", "", f"Hub: `{self.url}` · modelo `{self.cfg.hub.modelo}` · {dados['gerado_em']}", "",
                  "| id | prova | situação | observação | s |", "|---|---|---|---|---|"]
        for r in resultados:
            obs = (r.observacao or "").replace("|", "/").replace("\n", " ")[:220]
            linhas.append(f"| {r.id} | {r.nome} | **{r.situacao}** | {obs} | {r.segundos} |")
        recs = [f"- **{r.id}**: {r.recomendacao}" for r in resultados if r.recomendacao]
        if recs:
            linhas += ["", "## Recomendações de configuração", *recs]
        (self.saida / "resultados.md").write_text("\n".join(linhas) + "\n", encoding="utf-8")
        return self.saida


def resumo(resultados: list[ResultadoProva]) -> dict[str, int]:
    contagem = {s: 0 for s in SITUACOES}
    for r in resultados:
        contagem[r.situacao] = contagem.get(r.situacao, 0) + 1
    return contagem


def p50_p95(valores: list[float]) -> dict[str, float]:
    if not valores:
        return {}
    ordenados = sorted(valores)
    return {"p50": round(statistics.median(ordenados), 3), "p95": round(ordenados[max(0, int(len(ordenados) * 0.95) - 1)], 3),
            "n": len(ordenados)}
