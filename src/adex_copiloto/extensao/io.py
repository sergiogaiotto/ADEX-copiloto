"""``adex.io``: leitura de arquivos pelas ferramentas do dev, com rastreio do que foi lido.

Dentro de uma ferramenta registrada, ``adex.io.open`` só abre caminhos declarados
em ``reads=[...]`` (por prefixo). O copiloto registra cada leitura no dossiê.
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Iterator

from adex_copiloto.erros import LeituraNaoDeclaradaErro

_declaradas: contextvars.ContextVar[tuple[str, ...] | None] = contextvars.ContextVar("adex_leituras_declaradas", default=None)
_lidas: contextvars.ContextVar[list[str] | None] = contextvars.ContextVar("adex_leituras_feitas", default=None)


def _normalizar(caminho: str) -> str:
    texto = str(caminho)
    if texto.startswith("oci://"):
        return texto
    return str(Path(texto).expanduser().resolve()).replace("\\", "/")


@contextmanager
def escopo_ferramenta(declaradas: list[str]) -> Iterator[list[str]]:
    lidas: list[str] = []
    t1 = _declaradas.set(tuple(_normalizar(d) for d in declaradas))
    t2 = _lidas.set(lidas)
    try:
        yield lidas
    finally:
        _declaradas.reset(t1)
        _lidas.reset(t2)


class IO_:
    """Objeto exposto como ``adex.io``."""

    def __init__(self, armazenamento=None) -> None:
        self.armazenamento = armazenamento

    def open(self, caminho: str) -> IO[bytes]:
        alvo = _normalizar(caminho)
        declaradas = _declaradas.get()
        if declaradas is not None and not any(alvo == d or alvo.startswith(d.rstrip("/") + "/") for d in declaradas):
            raise LeituraNaoDeclaradaErro(f"A ferramenta tentou ler {caminho}, que não está em reads=[...].")
        lidas = _lidas.get()
        if lidas is not None:
            lidas.append(alvo)
        if self.armazenamento is not None:
            return self.armazenamento.abrir(caminho)
        if alvo.startswith("oci://"):
            raise LeituraNaoDeclaradaErro("Leitura do Object Storage sem configuração de armazenamento.")
        return open(alvo, "rb")
