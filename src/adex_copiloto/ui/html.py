"""HTML dos painéis: autocontido, segue o tema do JupyterLab (claro/escuro) e é acessível.

Números calculados pelo Python aparecem destacados (``b.adex-num``) com a origem no
``title``. Selos A–D, severidades e origens têm rótulo em texto (não só cor).
"""

from __future__ import annotations

import base64
from html import escape
from typing import Any, Iterable

import pandas as pd

CSS = """
<style>
.adex{font-family:var(--jp-ui-font-family,system-ui,-apple-system,"Segoe UI",sans-serif);font-size:13px;line-height:1.45;
 color:var(--jp-ui-font-color0,#1b1f24);background:var(--jp-layout-color0,transparent);
 border:1px solid var(--jp-border-color2,#d0d7de);border-radius:8px;padding:12px 14px;margin:6px 0;max-width:1100px}
.adex h3{margin:0 0 4px;font-size:15px}.adex h4{margin:12px 0 4px;font-size:13px}
.adex .sub{color:var(--jp-ui-font-color2,#57606a);margin:0 0 8px}
.adex .linha{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.adex .tag{display:inline-block;border:1px solid var(--jp-border-color1,#afb8c1);border-radius:999px;padding:0 8px;font-size:11px;white-space:nowrap}
.adex .selo{font-weight:700;border-radius:4px;padding:0 5px;font-size:11px;border:1px solid currentColor}
.adex .selo-A{color:#1a7f37}.adex .selo-B{color:#0969da}.adex .selo-C{color:#9a6700}.adex .selo-D{color:#6e7781}
.adex .sev-bloqueio{color:#cf222e;font-weight:700}.adex .sev-alerta{color:#9a6700;font-weight:700}.adex .sev-nota{color:#57606a}
.adex .ok{color:#1a7f37}.adex .falha{color:#cf222e}.adex .aviso{color:#9a6700}
.adex table.t{border-collapse:collapse;margin:4px 0;width:auto;max-width:100%}
.adex table.t th,.adex table.t td{border-bottom:1px solid var(--jp-border-color2,#d0d7de);padding:3px 8px;text-align:left;vertical-align:top}
.adex table.t th{font-weight:600;color:var(--jp-ui-font-color1,#24292f)}
.adex b.adex-num{background:var(--jp-brand-color3,#ddf4ff);color:var(--jp-ui-font-color0,#0a3069);padding:0 3px;border-radius:3px}
.adex .caixa{border-left:3px solid var(--jp-brand-color1,#0969da);padding:6px 10px;margin:8px 0;background:var(--jp-layout-color1,rgba(9,105,218,.06))}
.adex .caixa.alerta{border-left-color:#bf8700}.adex .caixa.bloqueio{border-left-color:#cf222e}
.adex .rodape{color:var(--jp-ui-font-color2,#57606a);font-size:11px;margin-top:8px}
.adex code{font-family:var(--jp-code-font-family,ui-monospace,monospace);font-size:12px}
.adex img{max-width:100%;height:auto}
</style>
"""

ORIGENS = {"voce_disse": "você disse ✓", "voce_escolheu": "você escolheu", "sugerido": "sugerido · confirme", "padrao": "padrão"}


def e(t: Any) -> str:
    return escape("" if t is None else str(t))


def selo(s: str) -> str:
    return f'<span class="selo selo-{e(s)}" title="selo de evidência {e(s)}">{e(s)}</span>'


def tag(t: str, titulo: str = "") -> str:
    return f'<span class="tag" title="{e(titulo)}">{e(t)}</span>'


def tabela(linhas: Iterable[Iterable[Any]], cabecalho: list[str], *, html_ok: bool = False) -> str:
    th = "".join(f"<th>{e(c)}</th>" for c in cabecalho)
    corpo = []
    for l in linhas:
        corpo.append("<tr>" + "".join(f"<td>{c if html_ok else e(c)}</td>" for c in l) + "</tr>")
    return f'<table class="t"><thead><tr>{th}</tr></thead><tbody>{"".join(corpo)}</tbody></table>'


def df_html(df: pd.DataFrame, *, max_linhas: int = 30) -> str:
    if df is None or len(df) == 0:
        return '<p class="sub">(vazio)</p>'
    d = df.head(max_linhas).copy()
    for c in d.select_dtypes("number").columns:
        d[c] = d[c].map(lambda v: _num(v))
    for c in d.columns:
        if pd.api.types.is_datetime64_any_dtype(d[c]):
            d[c] = d[c].dt.strftime("%Y-%m-%d")
    html = d.to_html(index=False, classes="t", border=0, escape=True)
    extra = f'<p class="sub">mostrando {max_linhas} de {len(df)} linhas</p>' if len(df) > max_linhas else ""
    return html + extra


def _num(v: Any) -> str:
    from adex_copiloto.llm.marcadores import formatar

    try:
        if pd.isna(v):
            return "—"
    except (TypeError, ValueError):
        pass
    if isinstance(v, float):
        return formatar(v, casas=4 if abs(v) < 1 else 2)
    return formatar(v)


def figura(png: bytes, descricao: str, titulo: str = "") -> str:
    b64 = base64.b64encode(png).decode("ascii")
    leg = f"<figcaption class='sub'>{e(titulo)}</figcaption>" if titulo else ""
    return f'<figure style="margin:6px 0"><img src="data:image/png;base64,{b64}" alt="{e(descricao)}">{leg}</figure>'


def caixa(conteudo: str, tipo: str = "") -> str:
    return f'<div class="caixa {e(tipo)}">{conteudo}</div>'


def cartao(titulo: str, subtitulo: str, corpo: str, *, rodape: str = "", status: str = "") -> str:
    st = f" {tag(status)}" if status else ""
    rod = f'<div class="rodape">{rodape}</div>' if rodape else ""
    return f'{CSS}<div class="adex" role="region" aria-label="{e(titulo)}"><h3>{e(titulo)}{st}</h3><p class="sub">{subtitulo}</p>{corpo}{rod}</div>'


def comandos(*linhas: str) -> str:
    if not linhas:
        return ""
    itens = "".join(f"<li><code>{e(l)}</code></li>" for l in linhas)
    return f'<h4>Próximos comandos</h4><ul style="margin:0 0 0 18px;padding:0">{itens}</ul>'


def cartao_erro(erro) -> str:
    corpo = (f"<p>{e(erro.explicacao)}</p>" + caixa(f"<b>Como resolver:</b> {e(erro.como_resolver)}", "alerta")
             + f'<p class="sub"><code>{e(type(erro).__name__)}</code>: {e(erro.mensagem)}</p>')
    return cartao("Não deu certo", "o copiloto explica o que houve", corpo)
