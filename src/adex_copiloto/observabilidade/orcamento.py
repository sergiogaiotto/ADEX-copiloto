"""Orçamento de IA por estudo (limites do Admin): tokens, custo e chamadas.

A verificação acontece **antes** de cada chamada, com a estimativa de tokens
(entrada + teto de saída). Assim o limite nunca é ultrapassado por muito.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from adex_copiloto.config.modelos import Orcamento as OrcamentoCfg
from adex_copiloto.erros import OrcamentoEsgotadoErro


@dataclass
class Orcamento:
    limites: OrcamentoCfg
    tokens: int = 0
    custo_usd: float = 0.0
    chamadas: int = 0
    _alertado: bool = False
    _trava: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def verificar(self, tokens_previstos: int = 0) -> None:
        lim = self.limites
        with self._trava:
            if self.chamadas + 1 > lim.chamadas_por_estudo:
                raise OrcamentoEsgotadoErro(
                    f"Limite de chamadas à IA do estudo atingido ({lim.chamadas_por_estudo}).",
                    detalhes=self.situacao(),
                )
            if self.tokens + tokens_previstos > lim.tokens_por_estudo:
                raise OrcamentoEsgotadoErro(
                    f"Esta chamada passaria do limite de tokens do estudo ({lim.tokens_por_estudo:,}).".replace(",", "."),
                    detalhes=self.situacao(),
                )
            if self.custo_usd >= lim.custo_usd_por_estudo:
                raise OrcamentoEsgotadoErro(
                    f"Limite de custo do estudo atingido (US$ {lim.custo_usd_por_estudo:.2f}).",
                    detalhes=self.situacao(),
                )

    def registrar(self, tokens: int, custo_usd: float | None, *, contar_chamada: bool = True) -> str | None:
        """Soma o uso. Devolve um aviso quando cruza o limiar de alerta."""
        with self._trava:
            self.tokens += max(0, int(tokens or 0))
            self.custo_usd += float(custo_usd or 0.0)
            if contar_chamada:
                self.chamadas += 1
            if not self._alertado and self._fracao_maxima() >= self.limites.alerta_em:
                self._alertado = True
                return (
                    f"O estudo já usou {self._fracao_maxima():.0%} do orçamento de IA definido pelo Admin. "
                    "Veja estudo.costs()."
                )
        return None

    def _fracao_maxima(self) -> float:
        lim = self.limites
        return max(
            self.tokens / max(1, lim.tokens_por_estudo),
            self.custo_usd / max(1e-9, lim.custo_usd_por_estudo),
            self.chamadas / max(1, lim.chamadas_por_estudo),
        )

    def situacao(self) -> dict:
        lim = self.limites
        return {
            "tokens": self.tokens,
            "tokens_limite": lim.tokens_por_estudo,
            "custo_usd": round(self.custo_usd, 4),
            "custo_usd_limite": lim.custo_usd_por_estudo,
            "chamadas": self.chamadas,
            "chamadas_limite": lim.chamadas_por_estudo,
            "uso_maximo": round(self._fracao_maxima(), 4),
        }

    def restaurar(self, tokens: int, custo_usd: float, chamadas: int) -> None:
        with self._trava:
            self.tokens, self.custo_usd, self.chamadas = int(tokens), float(custo_usd), int(chamadas)
