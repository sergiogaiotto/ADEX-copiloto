"""ADEX Copiloto: inteligência de dados assistida no Jupyter do OCI Data Science.

Uso típico::

    from adex_copiloto import AdexCopiloto

    adex = AdexCopiloto.from_env()
    estudo = adex.create_session(objective="...", mode="exploration")
    estudo.frame()

A IA (Qwen do GPU Hub) interpreta, planeja e explica. Python e SQL executam e
validam. Decisões relevantes passam por aprovação humana.
"""

from adex_copiloto._versao import __version__

__all__ = ["AdexCopiloto", "__version__", "load_ipython_extension"]


def __getattr__(nome: str):
    # Importação preguiçosa: `import adex_copiloto` fica leve no kernel.
    if nome == "AdexCopiloto":
        from adex_copiloto.api import AdexCopiloto

        return AdexCopiloto
    raise AttributeError(nome)


def load_ipython_extension(ipython) -> None:  # pragma: no cover - depende do IPython
    """Permite `%load_ext adex_copiloto` para ativar `%adex` e `%%adex sql`."""
    from adex_copiloto.ui.magics import registrar_magics

    registrar_magics(ipython)
