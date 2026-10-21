"""Magics do Jupyter: ``%adex`` e ``%%adex sql`` (``%load_ext adex_copiloto``).

- ``%adex status`` · ``%adex custos`` · ``%adex chat <texto>`` · ``%adex explicar`` (último erro)
- ``%%adex sql [--modo agregado|detalhe] [--bind nome=valor ...] [--para variavel]``
  O SQL escrito pelo dev passa pelo MESMO porteiro que o da IA.
"""

from __future__ import annotations

import shlex
from typing import Any

from adex_copiloto.erros import AdexErro

_ESTADO: dict[str, Any] = {"estudo": None, "adex": None}


def usar(estudo=None, adex=None) -> None:
    """Diz às magics qual estudo usar (o último criado é usado por padrão)."""
    if estudo is not None:
        _ESTADO["estudo"] = estudo
    if adex is not None:
        _ESTADO["adex"] = adex


def _estudo():
    e = _ESTADO.get("estudo")
    if e is None:
        raise AdexErro("Nenhum estudo ativo para as magics.", como_resolver="from adex_copiloto.ui.magics import usar; usar(estudo)")
    return e


def _valor(texto: str) -> Any:
    for conv in (int, float):
        try:
            return conv(texto)
        except ValueError:
            pass
    return texto


def registrar_magics(ipython) -> None:  # pragma: no cover - depende do IPython
    from IPython.core.magic import Magics, cell_magic, line_magic, magics_class
    from IPython.display import display

    @magics_class
    class AdexMagics(Magics):
        @line_magic("adex")
        def adex_linha(self, linha: str):
            partes = linha.strip().split(maxsplit=1)
            cmd = partes[0] if partes else "status"
            resto = partes[1] if len(partes) > 1 else ""
            if cmd == "status":
                return _estudo().show()
            if cmd == "custos":
                return _estudo().costs()
            if cmd == "chat":
                return _estudo().chat(resto)
            if cmd == "explicar":
                from adex_copiloto.ui.explicador import explicar_ultimo

                return explicar_ultimo(self.shell)
            return f"Comandos: status, custos, chat <texto>, explicar. Recebi: {cmd}"

        @cell_magic("adex")
        def adex_celula(self, linha: str, celula: str):
            args = shlex.split(linha)
            if not args or args[0] != "sql":
                return "Use %%adex sql na primeira linha da célula."
            modo, binds, destino = "agregado", {}, None
            i = 1
            while i < len(args):
                if args[i] == "--modo":
                    modo, i = args[i + 1], i + 2
                elif args[i] == "--bind":
                    nome, _, valor = args[i + 1].partition("=")
                    binds[nome] = _valor(valor)
                    i += 2
                elif args[i] == "--para":
                    destino, i = args[i + 1], i + 2
                else:
                    i += 1
            return executar_sql(celula, binds=binds, modo=modo, destino=destino, ns=self.shell.user_ns, mostrar=display)

    ipython.register_magics(AdexMagics)


def executar_sql(sql: str, *, binds: dict | None = None, modo: str = "agregado", destino: str | None = None, ns: dict | None = None,
                 mostrar=None):
    """Roda SQL do dev pelo porteiro (registrado no dossiê do estudo ativo)."""
    e = _estudo()
    fonte = e._s.fonte
    if fonte is None:
        raise AdexErro("O estudo não tem fonte de dados.")
    r = fonte.consultar(sql, binds or {}, modo=modo, etapa="sql_do_dev", telemetria=e._s.telemetria)
    e._s.dossie.registrar("consulta_dev", {"sql": r.veredito.sql_canonico, "binds": list(r.veredito.binds), "sha256": r.veredito.sha256,
                                           "modo": r.veredito.modo, "linhas": r.linhas}, ator="dev")
    if destino and ns is not None:
        ns[destino] = r.df
    if mostrar is not None and r.veredito.avisos:
        mostrar("Porteiro: " + " ".join(r.veredito.avisos))
    return r.df
