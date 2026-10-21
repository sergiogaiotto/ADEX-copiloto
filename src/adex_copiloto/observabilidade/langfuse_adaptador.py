"""Langfuse (máquina própria, fora deste ambiente): rastros, custos e notas.

Parâmetros da máquina ficam nas Configurações do Admin (seção ``langfuse``):
servidor 4.49 (por isso SDK 4.x: o 5 exige servidor ≥ 4.59), dados no Brasil,
projeto dedicado, URL e chaves pk/sk com sha256 + salt, captura e o que fazer
sem rota (``on_unavailable``).

Garantias:
- ``base_url`` é obrigatório e conferido pela impressão: o SDK, sozinho, cai na
  nuvem da Europa (``cloud.langfuse.com``);
- a máscara do Admin (``langfuse.captura``) vale para TUDO o que o SDK envia,
  inclusive o que vem do LangGraph/LangChain pelo CallbackHandler;
- sem rota até a máquina do Langfuse, os spans (já mascarados) e as notas vão
  para uma fila local com retenção (``retencao.fila_local_dias``) e podem ser
  reenviados depois com :func:`reenviar_fila`.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import logging
import threading
from pathlib import Path
from typing import Any

import httpx

from adex_copiloto.config.modelos import Config
from adex_copiloto.erros import ConfigErro
from adex_copiloto.observabilidade.telemetria import Observacao
from adex_copiloto.seguranca.redacao import mascarar
from adex_copiloto.seguranca.segredos import Segredo, conferir_url, ler_segredo

log = logging.getLogger("adex_copiloto.langfuse")

_TIPOS = {"span": "span", "generation": "generation", "tool": "tool", "agent": "agent", "chain": "chain",
          "guardrail": "guardrail", "event": "event", "evaluator": "evaluator"}
VERSAO_MINIMA_SDK5 = (4, 59)


def _versao(texto: str) -> tuple[int, ...]:
    partes = []
    for p in str(texto).split("."):
        num = "".join(ch for ch in p if ch.isdigit())
        partes.append(int(num) if num else 0)
    return tuple(partes)


def sdk_major_efetivo(cfg: Config) -> int:
    """Major do SDK compatível com o servidor (4.49 -> 4)."""
    if cfg.langfuse.sdk_major != "auto":
        return int(cfg.langfuse.sdk_major)
    return 5 if _versao(cfg.langfuse.server_version)[:2] >= VERSAO_MINIMA_SDK5 else 4


def sdk_instalado() -> int | None:
    try:
        from langfuse import __version__  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        try:
            from importlib.metadata import version

            __version__ = version("langfuse")
        except Exception:  # noqa: BLE001
            return None
    return _versao(__version__)[0]


def mascara_para(captura: str):
    """Função ``mask`` do Langfuse conforme o nível de captura do Admin."""

    def mascara(*, data: Any, **_: Any) -> Any:
        if data is None:
            return None
        if captura == "metadados":
            bruto = json.dumps(data, ensure_ascii=False, default=str)
            return {"tamanho": len(bruto)}
        if captura == "completo":
            return mascarar(data, pii=False)
        return mascarar(data)

    return mascara


def ler_credenciais(cfg: Config) -> tuple[Segredo, Segredo, str]:
    lf = cfg.langfuse
    url = ler_segredo(lf.base_url, "langfuse.base_url", cfg, exigir_impressao=False, normalizar=lambda v: v.rstrip("/"))
    conferir_url(url.revelar(), lf.base_url.impressao, lf.base_url.salt, "langfuse.base_url")
    pk = ler_segredo(lf.public_key, "langfuse.public_key", cfg)
    sk = ler_segredo(lf.secret_key, "langfuse.secret_key", cfg)
    return pk, sk, url.revelar()


def testar_conexao(cfg: Config, *, url: str | None = None, timeout: float | None = None) -> str:
    """Saúde da máquina do Langfuse (sem autenticação)."""
    if url is None:
        _, _, url = ler_credenciais(cfg)
    try:
        r = httpx.get(url.rstrip("/") + "/api/public/health", timeout=timeout or cfg.langfuse.timeout_s)
        return f"Langfuse respondeu HTTP {r.status_code} ({r.text[:120]})"
    except httpx.HTTPError as e:
        return f"Langfuse inacessível ({type(e).__name__}): sem rota até a máquina?"


def _alcancavel(url: str, timeout: float) -> bool:
    try:
        r = httpx.get(url.rstrip("/") + "/api/public/health", timeout=timeout)
        return r.status_code < 500
    except httpx.HTTPError:
        return False


# --- fila local (sem rota) ----------------------------------------------------------------


class FilaLocal:
    """Arquivos JSONL por dia em ``<estado>/fila_langfuse``; tudo já mascarado."""

    def __init__(self, pasta: Path) -> None:
        self.pasta = Path(pasta)
        self.pasta.mkdir(parents=True, exist_ok=True)
        self._trava = threading.Lock()

    def _arquivo(self) -> Path:
        return self.pasta / f"fila-{dt.date.today().isoformat()}.jsonl"

    def guardar(self, registro: dict[str, Any]) -> None:
        linha = json.dumps(registro, ensure_ascii=False, default=str)
        with self._trava, open(self._arquivo(), "a", encoding="utf-8") as f:
            f.write(linha + "\n")

    def pendentes(self) -> list[Path]:
        return sorted(self.pasta.glob("fila-*.jsonl"))

    def tamanho(self) -> int:
        total = 0
        for p in self.pendentes():
            with open(p, encoding="utf-8") as f:
                total += sum(1 for _ in f)
        return total

    def purgar(self, dias: int) -> list[str]:
        limite = dt.date.today() - dt.timedelta(days=dias)
        apagados = []
        for p in self.pendentes():
            try:
                dia = dt.date.fromisoformat(p.stem.removeprefix("fila-"))
            except ValueError:
                continue
            if dia < limite:
                p.unlink(missing_ok=True)
                apagados.append(p.name)
        return apagados


def _span_para_dict(span) -> dict[str, Any]:
    ctx = span.context
    pai = span.parent
    escopo = getattr(span, "instrumentation_scope", None)
    return {
        "tipo": "span",
        "nome": span.name,
        "trace_id": f"{ctx.trace_id:032x}",
        "span_id": f"{ctx.span_id:016x}",
        "pai": f"{pai.span_id:016x}" if pai is not None else None,
        "inicio": span.start_time,
        "fim": span.end_time,
        "atributos": dict(span.attributes or {}),
        "recurso": dict(span.resource.attributes) if span.resource else {},
        "status": span.status.status_code.name if span.status else "UNSET",
        "status_descricao": span.status.description if span.status else None,
        "escopo": [escopo.name, escopo.version] if escopo else ["langfuse-sdk", None],
    }


def _dict_para_span(d: dict[str, Any]):
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import ReadableSpan
    from opentelemetry.sdk.util.instrumentation import InstrumentationScope
    from opentelemetry.trace import SpanContext, SpanKind, Status, StatusCode, TraceFlags

    ctx = SpanContext(int(d["trace_id"], 16), int(d["span_id"], 16), is_remote=False, trace_flags=TraceFlags(1))
    pai = SpanContext(int(d["trace_id"], 16), int(d["pai"], 16), is_remote=False, trace_flags=TraceFlags(1)) if d.get("pai") else None
    return ReadableSpan(
        name=d["nome"], context=ctx, parent=pai, resource=Resource(d.get("recurso") or {}),
        attributes=d.get("atributos") or {}, kind=SpanKind.INTERNAL,
        status=Status(StatusCode[d.get("status", "UNSET")], d.get("status_descricao")),
        start_time=d["inicio"], end_time=d["fim"],
        instrumentation_scope=InstrumentationScope(*(d.get("escopo") or ["langfuse-sdk", None])),
    )


def _exportador_otlp(url: str, pk: str, sk: str, timeout: int):
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    auth = "Basic " + base64.b64encode(f"{pk}:{sk}".encode()).decode("ascii")
    return OTLPSpanExporter(
        endpoint=url.rstrip("/") + "/api/public/otel/v1/traces",
        headers={"Authorization": auth, "x-langfuse-public-key": pk, "x-langfuse-sdk-name": "python"},
        timeout=timeout,
    )


class ExportadorComFila:
    """SpanExporter: tenta enviar; sem rota, guarda na fila local (os spans já vêm mascarados)."""

    def __init__(self, interno, fila: FilaLocal, *, tentar: bool = True) -> None:
        self.interno = interno
        self.fila = fila
        self.tentar = tentar

    def export(self, spans):
        from opentelemetry.sdk.trace.export import SpanExportResult

        if self.tentar:
            try:
                r = self.interno.export(spans)
                if r == SpanExportResult.SUCCESS:
                    return r
            except Exception:  # noqa: BLE001
                pass
            self.tentar = False  # sem rota: para de tentar até o próximo reenvio manual
        for s in spans:
            self.fila.guardar(_span_para_dict(s))
        return SpanExportResult.SUCCESS

    def shutdown(self):
        try:
            self.interno.shutdown()
        except Exception:  # noqa: BLE001
            pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


# --- destino de telemetria ---------------------------------------------------------------------


class DestinoLangfuse:
    """Leva as observações da :class:`Telemetria` para o Langfuse (ou para a fila)."""

    def __init__(self, cfg: Config, *, estudo_id: str, pasta_fila: Path, cliente: Any | None = None, alcancavel: bool | None = None) -> None:
        lf = cfg.langfuse
        self.cfg = cfg
        self.estudo_id = estudo_id
        self.fila = FilaLocal(pasta_fila)
        self._abertos: dict[str, Any] = {}
        self._trava = threading.Lock()
        if cliente is not None:
            self.cliente, self.url, self.alcancavel = cliente, "teste", True if alcancavel is None else alcancavel
            return
        instalado = sdk_instalado()
        esperado = sdk_major_efetivo(cfg)
        if instalado is not None and instalado != esperado:
            raise ConfigErro(
                f"O servidor do Langfuse é {lf.server_version} e pede o SDK {esperado}.x, mas está instalado o {instalado}.x.",
                como_resolver=f"Instale langfuse>={esperado},<{esperado + 1} no conda do ADEX.",
            )
        pk, sk, url = ler_credenciais(cfg)
        self.url = url
        self.alcancavel = _alcancavel(url, min(3.0, lf.timeout_s)) if lf.rota != "pendente" else False
        if not self.alcancavel and lf.on_unavailable == "desligado":
            raise ConfigErro("Langfuse sem rota e on_unavailable = 'desligado'.", como_resolver="Os rastros ficam só no dossiê.")
        from langfuse import Langfuse

        exportador = ExportadorComFila(_exportador_otlp(url, pk.revelar(), sk.revelar(), lf.timeout_s), self.fila, tentar=self.alcancavel)
        from adex_copiloto._versao import __version__

        self.cliente = Langfuse(
            public_key=pk.revelar(), secret_key=sk.revelar(), base_url=url, timeout=lf.timeout_s,
            environment=lf.environment, release=f"adex-copiloto-{__version__}", sample_rate=lf.amostragem,
            mask=mascara_para(lf.captura), flush_interval=lf.flush_interval_s, span_exporter=exportador,
            tracing_enabled=True,
        )

    # interface Destino -------------------------------------------------------------------

    def iniciar(self, obs: Observacao) -> None:
        tipo = _TIPOS.get(obs.tipo, "span")
        kwargs: dict[str, Any] = {"name": obs.nome, "as_type": tipo, "input": obs.entrada, "metadata": {"etapa": obs.etapa, **obs.metadados}}
        if tipo == "generation":
            kwargs["model"] = obs.modelo
            kwargs["model_parameters"] = {k: v for k, v in (obs.parametros or {}).items() if isinstance(v, (str, int, float, bool))}
        with self._trava:
            pai = self._abertos.get(obs.pai_id) if obs.pai_id else None
        if pai is not None:
            handle = pai.start_observation(**kwargs)
        else:
            handle = self.cliente.start_observation(trace_context={"trace_id": obs.trace_id}, **kwargs)
            span = getattr(handle, "_otel_span", None)
            if span is not None:
                span.set_attribute("session.id", self.estudo_id)
                span.set_attribute("langfuse.trace.name", obs.nome)
        with self._trava:
            self._abertos[obs.id] = handle

    def finalizar(self, obs: Observacao) -> None:
        with self._trava:
            handle = self._abertos.pop(obs.id, None)
        if handle is None:
            return
        atualizacao: dict[str, Any] = {"output": obs.saida, "metadata": obs.metadados}
        if obs.nivel != "DEFAULT":
            atualizacao["level"] = obs.nivel
        if obs.status:
            atualizacao["status_message"] = obs.status
        if obs.tipo == "generation":
            if obs.uso:
                atualizacao["usage_details"] = {k: int(v) for k, v in obs.uso.items() if v is not None}
            if obs.custo:
                atualizacao["cost_details"] = obs.custo
        handle.update(**atualizacao)
        handle.end()

    def score(self, nome: str, valor: Any, tipo: str, comentario: str | None, obs: Observacao | None, trace_id: str) -> None:
        handle = self._abertos.get(obs.id) if obs else None
        registro = {"tipo": "score", "nome": nome, "valor": valor, "data_type": tipo, "comentario": comentario,
                    "trace_id": getattr(handle, "trace_id", None) or trace_id, "observation_id": getattr(handle, "id", None),
                    "sessao": self.estudo_id, "em": dt.datetime.now(dt.timezone.utc).isoformat()}
        if not self.alcancavel:
            self.fila.guardar(mascarar(registro))
            return
        self.cliente.create_score(
            name=nome, value=valor, trace_id=registro["trace_id"], observation_id=registro["observation_id"],
            data_type=tipo, comment=mascarar(comentario) if comentario else None,
        )

    def flush(self) -> None:
        self.cliente.flush()

    # integração LangChain/LangGraph -----------------------------------------------------------

    def callback(self, obs: Observacao | None):
        """CallbackHandler ligado ao rastro atual (spans do grafo e do agente aninham no comando)."""
        from langfuse.langchain import CallbackHandler

        contexto = None
        if obs is not None:
            handle = self._abertos.get(obs.id)
            if handle is not None:
                contexto = {"trace_id": handle.trace_id, "parent_span_id": handle.id}
        return CallbackHandler(trace_context=contexto) if contexto else CallbackHandler()


def reenviar_fila(cfg: Config, pasta_fila: Path) -> dict[str, int]:
    """Reenvia o que ficou na fila (rode quando a rota até o Langfuse existir)."""
    pk, sk, url = ler_credenciais(cfg)
    if not _alcancavel(url, cfg.langfuse.timeout_s):
        return {"enviados": 0, "pendentes": FilaLocal(pasta_fila).tamanho(), "erro": 1}
    fila = FilaLocal(pasta_fila)
    exportador = _exportador_otlp(url, pk.revelar(), sk.revelar(), cfg.langfuse.timeout_s)
    from langfuse import Langfuse

    cliente = Langfuse(public_key=pk.revelar(), secret_key=sk.revelar(), base_url=url, tracing_enabled=False)
    enviados = 0
    for arquivo in fila.pendentes():
        spans, notas = [], []
        with open(arquivo, encoding="utf-8") as f:
            for linha in f:
                if not linha.strip():
                    continue
                d = json.loads(linha)
                (spans if d.get("tipo") == "span" else notas).append(d)
        from opentelemetry.sdk.trace.export import SpanExportResult

        if spans and exportador.export([_dict_para_span(d) for d in spans]) != SpanExportResult.SUCCESS:
            break
        for n in notas:
            cliente.create_score(name=n["nome"], value=n["valor"], trace_id=n.get("trace_id"),
                                 observation_id=n.get("observation_id"), data_type=n.get("data_type"),
                                 comment=n.get("comentario"))
        cliente.flush()
        enviados += len(spans) + len(notas)
        arquivo.unlink(missing_ok=True)  # enviado: a cópia local mascarada não fica guardada
    return {"enviados": enviados, "pendentes": fila.tamanho(), "erro": 0}
