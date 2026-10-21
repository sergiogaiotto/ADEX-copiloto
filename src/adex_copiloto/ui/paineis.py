"""Painéis do notebook: HTML sempre; botões com ipywidgets quando disponíveis.

Tudo o que um botão faz também existe em código (o painel mostra o comando).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from adex_copiloto.llm.marcadores import TabelaMarcadores, preencher
from adex_copiloto.ui import html as H


@dataclass
class Acao:
    rotulo: str
    funcao: Callable[[], Any]
    comando: str
    principal: bool = False


@dataclass
class EntradaTexto:
    rotulo: str
    funcao: Callable[[str], Any]
    comando: str
    placeholder: str = ""


@dataclass
class Painel:
    html: str
    acoes: list[Acao] = field(default_factory=list)
    entrada: EntradaTexto | None = None
    dados: dict[str, Any] = field(default_factory=dict)

    def _repr_html_(self) -> str:
        cmds = [a.comando for a in self.acoes] + ([self.entrada.comando] if self.entrada else [])
        return self.html + (H.CSS + '<div class="adex" style="border-style:dashed">' + H.comandos(*cmds) + "</div>" if cmds else "")

    def _ipython_display_(self) -> None:  # pragma: no cover - visual
        from IPython.display import HTML, display

        try:
            import ipywidgets as w
        except ImportError:
            display(HTML(self._repr_html_()))
            return
        if not self.acoes and not self.entrada:
            display(HTML(self.html))
            return
        saida = w.Output()
        filhos: list[Any] = [w.HTML(self.html)]
        if self.entrada is not None:
            caixa = w.Textarea(placeholder=self.entrada.placeholder, layout=w.Layout(width="100%", height="110px"))
            botao = w.Button(description=self.entrada.rotulo, button_style="primary")

            def enviar(_):
                saida.clear_output()
                with saida:
                    display(self.entrada.funcao(caixa.value))

            botao.on_click(enviar)
            filhos += [caixa, botao]
        if self.acoes:
            botoes = []
            for a in self.acoes:
                b = w.Button(description=a.rotulo[:40], button_style="primary" if a.principal else "", tooltip=a.comando)

                def clicar(_, a=a):
                    saida.clear_output()
                    with saida:
                        display(a.funcao())

                b.on_click(clicar)
                botoes.append(b)
            filhos.append(w.HBox(botoes, layout=w.Layout(flex_flow="row wrap")))
        filhos.append(saida)
        display(w.VBox(filhos))

    def __repr__(self) -> str:
        import re

        texto = re.sub(r"<style>.*?</style>", "", self.html, flags=re.S)
        texto = re.sub(r"<[^>]+>", " ", texto)
        return re.sub(r"\s+", " ", texto).strip()[:2000]


def rodape_uso(telemetria, etapa: str | None = None) -> str:
    usos = [u for u in telemetria.usos if etapa is None or u.etapa == etapa]
    consultas = [c for c in telemetria.consultas if etapa is None or c.etapa == etapa]
    tokens = sum(u.tokens for u in usos)
    custo = sum(u.custo_usd or 0 for u in usos)
    return (f"Qwen: {len(usos)} chamada(s), {tokens:,} tokens".replace(",", ".") + (f", US$ {custo:.4f}" if custo else "")
            + f" · banco: {len(consultas)} consulta(s), {sum(c.linhas for c in consultas):,} linhas".replace(",", "."))


def afirmacoes_html(afirmacoes, marcadores: TabelaMarcadores) -> str:
    if not afirmacoes:
        return ""
    linhas = []
    for a in afirmacoes:
        rotulo = {"fato": "fato", "hipotese": "hipótese", "recomendacao": "recomendação"}[a.tipo]
        linhas.append([H.e(a.id), H.tag(rotulo), H.selo(a.selo), preencher(H.e(a.texto), marcadores, html=True)])
    return H.tabela(linhas, ["", "tipo", "selo", "afirmação"], html_ok=True)


def decisoes_html(decisoes) -> str:
    abertas = [d for d in decisoes if d.status == "aberta"]
    if not abertas:
        return ""
    partes = []
    for d in abertas:
        ops = " · ".join(f"<b>{H.e(o.codigo)}</b>) {H.e(o.rotulo)}" + (f" <span class='sub'>({H.e(o.consequencia)})</span>" if o.consequencia else "")
                         for o in d.opcoes)
        rec = f" <span class='tag'>recomendada: {H.e(d.recomendada)}</span>" if d.recomendada else ""
        partes.append(H.caixa(f"<b>{H.e(d.id)} · {H.e(d.titulo)}</b>{rec}<br>{H.e(d.pergunta)}<br>{ops}", "alerta"))
    return "<h4>Para você decidir</h4>" + "".join(partes)


def lacunas_html(lacunas) -> str:
    abertas = [l for l in lacunas if l.status == "aberta"]
    if not abertas:
        return ""
    return "<h4>Lacunas</h4>" + H.tabela([[l.id, l.descricao, l.impacto] for l in abertas], ["", "o que falta", "impacto"])
