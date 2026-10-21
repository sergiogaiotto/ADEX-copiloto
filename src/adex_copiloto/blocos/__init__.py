"""Blocos de método registrados (o coração do produto).

Importar este pacote registra os blocos nativos em :data:`REGISTRO`.
"""

from adex_copiloto.blocos import amostragem, bi, causal, inferencia, modelos, series, universo  # noqa: F401
from adex_copiloto.blocos.base import Bloco, ContextoBloco, Precondicao, ResultadoBloco, rodar_bloco
from adex_copiloto.blocos.registro import REGISTRO, RegistroBlocos, bloco_do_dev

__all__ = ["REGISTRO", "Bloco", "ContextoBloco", "Precondicao", "RegistroBlocos", "ResultadoBloco", "bloco_do_dev", "rodar_bloco"]
