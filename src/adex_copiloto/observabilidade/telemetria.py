"""Telemetria do estudo: tempo, chamadas à IA, tokens, custo, consultas e falhas.

Uma única fachada alimenta vários destinos:
- **dossiê** do estudo (sempre; é a trilha de auditoria);
- **Langfuse** (quando o Admin habilita e há rota);
- **fila local mascarada** (quando o Langfuse está sem rota e ``on_unavailable = "fila"``).

O que vai para os destinos respeita ``langfuse.captura`` (Admin):
- ``metadados``: só nomes, tamanhos, tempos, tokens e custo;
- ``mascarado``: conteúdo com dados pessoais e segredos mascarados (padrão);
- ``completo``: conteúdo como está (segredos continuam mascarados sempre).
"""

from __future__ import annotations

import contextvars
import datetime as dt
import hashlib
import json
import logging
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from adex_copiloto.observabilidade.orcamento import Orcamento
from adex_copiloto.seguranca.redacao import mascarar

log = logging.getLogger("adex_copiloto.telemetria")


def agora() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def novo_id(bytes_: int = 8) -> str:
    return uuid.uuid4().hex[: bytes_ * 2]


@dataclass
class Observacao:
    id: str
    trace_id: str
    nome: str
    tipo: str = "span"  # span | generation | tool | agent | chain | guardrail | event
    etapa: str = ""
    pai_id: str | None = None
    inicio: dt.datetime = field(default_factory=agora)
    fim: dt.datetime | None = None
    entrada: Any = None
    saida: Any = None
    metadados: dict[str, Any] = field(default_factory=dict)
    modelo: str | None = None
    parametros: dict[str, Any] | None = None
    uso: dict[str, int] | None = None
    custo: dict[str, float] | None = None
    nivel: str = "DEFAULT"  # DEFAULT | WARNING | ERROR
    status: str | None = None

    @property
    def segundos(self) -> float:
        fim = self.fim or agora()
        return (fim - self.inicio).total_seconds()

    def finalizar(self, *, saida: Any = None, nivel: str | None = None, status: str | None = None, **metadados) -> None:
        if saida is not None:
            self.saida = saida
        if nivel:
            self.nivel = nivel
        if status:
            self.status = status
        self.metadados.update(metadados)

    def para_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["inicio"] = self.inicio.isoformat()
        d["fim"] = self.fim.isoformat() if self.fim else None
        d["segundos"] = round(self.segundos, 3)
        return d


@dataclass
class RegistroUso:
    em: str
    etapa: str
    nome: str
    perfil: str
    modelo: str
    tokens_entrada: int = 0
    tokens_saida: int = 0
    tokens_raciocinio: int = 0
    tokens_cache: int = 0
    custo_usd: float | None = None
    latencia_s: float = 0.0
    aceite: str = "ok"
    tentativa: int = 1
    caminho: str = "direto"  # direto | agente

    @property
    def tokens(self) -> int:
        return self.tokens_entrada + self.tokens_saida


@dataclass
class RegistroConsulta:
    em: str
    etapa: str
    fonte: str
    sql_sha256: str
    aprovado: bool
    linhas: int = 0
    segundos: float = 0.0
    custo_plano: float | None = None
    gb_estimados: float | None = None
    motivo: str = ""


class Destino(Protocol):
    def iniciar(self, obs: Observacao) -> None: ...
    def finalizar(self, obs: Observacao) -> None: ...
    def score(self, nome: str, valor: Any, tipo: str, comentario: str | None, obs: Observacao | None, trace_id: str) -> None: ...
    def flush(self) -> None: ...


_atual: contextvars.ContextVar[Observacao | None] = contextvars.ContextVar("adex_obs_atual", default=None)


class Telemetria:
    def __init__(
        self,
        estudo_id: str,
        orcamento: Orcamento,
        *,
        destinos: list[Destino] | None = None,
        captura: str = "mascarado",
        ao_registrar_uso=None,
        ao_registrar_consulta=None,
        ao_alerta=None,
        ancora=None,
    ) -> None:
        self.estudo_id = estudo_id
        self._ancora = ancora  # () -> dict: último hash do dossiê, para a trilha externa
        self.orcamento = orcamento
        self.destinos: list[Destino] = list(destinos or [])
        self.captura = captura
        self.usos: list[RegistroUso] = []
        self.consultas: list[RegistroConsulta] = []
        self.falhas: list[dict[str, Any]] = []
        self._ao_uso = ao_registrar_uso
        self._ao_consulta = ao_registrar_consulta
        self._ao_alerta = ao_alerta
        self._trava = threading.Lock()

    # --- conteúdo -------------------------------------------------------------------

    def preparar(self, conteudo: Any) -> Any:
        """Aplica o nível de captura do Admin ao conteúdo que sai para os destinos."""
        if conteudo is None:
            return None
        if self.captura == "metadados":
            bruto = json.dumps(conteudo, ensure_ascii=False, default=str)
            return {"tamanho": len(bruto), "sha256": hashlib.sha256(bruto.encode("utf-8")).hexdigest()[:16]}
        if self.captura == "completo":
            return mascarar(conteudo, pii=False)
        return mascarar(conteudo)

    def preparar_texto(self, texto: str | None, *, so_tipo: str = "") -> str | None:
        """Mensagens de erro e comentários: mesma regra de captura (em metadados, nada do conteúdo)."""
        if texto is None:
            return None
        if self.captura == "metadados":
            return so_tipo or None
        return mascarar(str(texto), pii=self.captura != "completo")

    # --- observações -------------------------------------------------------------------

    @property
    def atual(self) -> Observacao | None:
        return _atual.get()

    def trace_atual(self) -> str | None:
        obs = _atual.get()
        return obs.trace_id if obs else None

    @contextmanager
    def observar(
        self,
        nome: str,
        *,
        tipo: str = "span",
        etapa: str = "",
        entrada: Any = None,
        metadados: dict[str, Any] | None = None,
        modelo: str | None = None,
        parametros: dict[str, Any] | None = None,
        novo_trace: bool = False,
    ) -> Iterator[Observacao]:
        pai = None if novo_trace else _atual.get()
        if pai is None and self._ancora is not None:
            try:
                metadados = {**(metadados or {}), **self._ancora()}
            except Exception:  # noqa: BLE001
                pass
        obs = Observacao(
            id=novo_id(8),
            trace_id=pai.trace_id if pai else novo_id(16),
            nome=nome,
            tipo=tipo,
            etapa=etapa or (pai.etapa if pai else ""),
            pai_id=pai.id if pai else None,
            entrada=self.preparar(entrada),
            metadados={"estudo_id": self.estudo_id, **(metadados or {})},
            modelo=modelo,
            parametros=parametros,
        )
        for d in self.destinos:
            self._seguro(d.iniciar, obs)
        token = _atual.set(obs)
        inicio = time.perf_counter()
        try:
            yield obs
        except BaseException as e:
            obs.nivel = "ERROR"
            # o texto de uma exceção pode trazer dado do cliente ou segredo (KeyError com o valor, por exemplo)
            obs.status = self.preparar_texto(f"{type(e).__name__}: {e}"[:500], so_tipo=type(e).__name__)
            with self._trava:
                self.falhas.append({"em": agora().isoformat(), "nome": nome, "etapa": obs.etapa, "erro": obs.status})
            raise
        finally:
            _atual.reset(token)
            obs.fim = agora()
            obs.metadados.setdefault("segundos", round(time.perf_counter() - inicio, 3))
            if obs.saida is not None:
                obs.saida = self.preparar(obs.saida)
            for d in self.destinos:
                self._seguro(d.finalizar, obs)

    def comando(self, nome: str, *, entrada: Any = None, etapa: str = "") -> Any:
        """Raiz de um rastro: um comando do dev no notebook (frame, discover...)."""
        return self.observar(nome, tipo="chain", etapa=etapa, entrada=entrada, novo_trace=_atual.get() is None)

    def _seguro(self, func, *args) -> None:
        # Observabilidade nunca derruba o estudo.
        try:
            func(*args)
        except Exception as e:  # noqa: BLE001
            log.warning("destino de telemetria falhou: %s", type(e).__name__)

    # --- uso, consultas e notas ----------------------------------------------------------

    def registrar_uso(self, uso: RegistroUso, *, contar_chamada: bool = True) -> None:
        with self._trava:
            self.usos.append(uso)
        aviso = self.orcamento.registrar(uso.tokens, uso.custo_usd, contar_chamada=contar_chamada)
        if self._ao_uso:
            self._seguro(self._ao_uso, uso)
        if aviso and self._ao_alerta:
            self._seguro(self._ao_alerta, aviso)

    def registrar_consulta(self, consulta: RegistroConsulta) -> None:
        with self._trava:
            self.consultas.append(consulta)
        if self._ao_consulta:
            self._seguro(self._ao_consulta, consulta)

    def score(self, nome: str, valor: Any, *, tipo: str = "CATEGORICAL", comentario: str | None = None) -> None:
        obs = _atual.get()
        trace = obs.trace_id if obs else novo_id(16)
        comentario = self.preparar_texto(comentario)
        for d in self.destinos:
            self._seguro(d.score, nome, valor, tipo, comentario, obs, trace)

    def flush(self) -> None:
        for d in self.destinos:
            self._seguro(d.flush)

    # --- leitura --------------------------------------------------------------------------

    def resumo_custos(self) -> list[dict[str, Any]]:
        """Uso por etapa: chamadas, tokens, custo e tempo."""
        por: dict[str, dict[str, Any]] = {}
        for u in self.usos:
            linha = por.setdefault(
                u.etapa or "(sem etapa)",
                {"etapa": u.etapa or "(sem etapa)", "chamadas": 0, "tokens_entrada": 0, "tokens_saida": 0,
                 "tokens_raciocinio": 0, "custo_usd": 0.0, "segundos_ia": 0.0, "recusadas": 0},
            )
            linha["chamadas"] += 1
            linha["tokens_entrada"] += u.tokens_entrada
            linha["tokens_saida"] += u.tokens_saida
            linha["tokens_raciocinio"] += u.tokens_raciocinio
            linha["custo_usd"] += u.custo_usd or 0.0
            linha["segundos_ia"] += u.latencia_s
            linha["recusadas"] += 0 if u.aceite == "ok" else 1
        for c in self.consultas:
            linha = por.setdefault(
                c.etapa or "(sem etapa)",
                {"etapa": c.etapa or "(sem etapa)", "chamadas": 0, "tokens_entrada": 0, "tokens_saida": 0,
                 "tokens_raciocinio": 0, "custo_usd": 0.0, "segundos_ia": 0.0, "recusadas": 0},
            )
            linha["consultas"] = linha.get("consultas", 0) + 1
            linha["segundos_banco"] = round(linha.get("segundos_banco", 0.0) + c.segundos, 3)
            linha["linhas_lidas"] = linha.get("linhas_lidas", 0) + c.linhas
        return list(por.values())


class TelemetriaNula(Telemetria):
    """Para testes e usos fora de um estudo: conta, mas não envia nada."""

    def __init__(self, orcamento: Orcamento | None = None) -> None:
        from adex_copiloto.config.modelos import Orcamento as OrcamentoCfg

        super().__init__("sem-estudo", orcamento or Orcamento(OrcamentoCfg()))
