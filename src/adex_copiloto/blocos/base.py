"""Base dos blocos de método: o coração do produto.

Um bloco é um método registrado, versionado, com parâmetros validados e
pré-condições. Ele roda em Python (e SQL por modelos fixos via porteiro), nunca
em código escrito pela IA. Cada execução devolve números (como marcadores
⟦M-xx⟧), afirmações com selo, decisões para o dev, figuras, tabelas e a linha de
código equivalente para o pacote de handoff.
"""

from __future__ import annotations

import io
import json
import logging
import traceback
from dataclasses import dataclass, field
from typing import Any, ClassVar

import pandas as pd
from pydantic import BaseModel, ConfigDict, ValidationError

from adex_copiloto.config.modelos import Config
from adex_copiloto.dados.porteiro import Modo
from adex_copiloto.erros import BlocoErro
from adex_copiloto.estudo.carta import Carta
from adex_copiloto.estudo.evidencia import Afirmacao, Decisao, Lacuna, Metrica, OpcaoDecisao, Registro, Selo
from adex_copiloto.estudo.mapeamento import Mapeamento
from adex_copiloto.estudo.passaporte import Passaporte
from adex_copiloto.llm.marcadores import TabelaMarcadores, formatar
from adex_copiloto.observabilidade.telemetria import Telemetria

log = logging.getLogger("adex_copiloto.blocos")


class Parametros(BaseModel):
    model_config = ConfigDict(extra="forbid")


@dataclass
class Precondicao:
    descricao: str
    ok: bool
    detalhe: str = ""
    bloqueante: bool = True


@dataclass
class Figura:
    nome: str
    titulo: str
    descricao: str  # texto alternativo (acessibilidade e IA)
    png: bytes


@dataclass
class ConsultaFeita:
    nome: str
    sql: str
    binds: dict[str, Any]
    sha256: str
    modo: str
    linhas: int
    segundos: float
    df: Any = field(default=None, repr=False)  # só para consultas agregadas (seguras para guardar)


@dataclass
class ResultadoBloco:
    bloco: str
    versao: str
    passo: str
    status: str = "feito"  # feito | falhou | aguarda_decisao
    resumo: str = ""  # texto com marcadores, escrito pelo Python
    metricas: list[Metrica] = field(default_factory=list)
    afirmacoes: list[Afirmacao] = field(default_factory=list)
    decisoes: list[Decisao] = field(default_factory=list)
    lacunas: list[Lacuna] = field(default_factory=list)
    figuras: list[Figura] = field(default_factory=list)
    tabelas: dict[str, pd.DataFrame] = field(default_factory=dict)
    tabelas_ia: dict[str, pd.DataFrame] = field(default_factory=dict)
    dados: dict[str, Any] = field(default_factory=dict)
    consultas: list[ConsultaFeita] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    premissas_ok: bool | None = None
    robustez_ok: bool | None = None
    codigo: str = ""
    erro: str = ""

    def resumo_para_dossie(self) -> dict[str, Any]:
        return {
            "bloco": f"{self.bloco}@{self.versao}",
            "passo": self.passo,
            "status": self.status,
            "resumo": self.resumo,
            "metricas": [m.model_dump(mode="json") for m in self.metricas],
            "afirmacoes": [a.model_dump(mode="json") for a in self.afirmacoes],
            "decisoes": [d.id for d in self.decisoes],
            "lacunas": [l.id for l in self.lacunas],
            "consultas": [c.sha256 for c in self.consultas],
            "avisos": self.avisos,
            "premissas_ok": self.premissas_ok,
            "robustez_ok": self.robustez_ok,
            "erro": self.erro,
        }


@dataclass
class ContextoBloco:
    estudo_id: str
    carta: Carta
    config: Config
    telemetria: Telemetria
    marcadores: TabelaMarcadores
    ids: Registro
    mapeamento: Mapeamento | None = None
    fonte: Any = None  # FonteDados
    dados: dict[str, Any] = field(default_factory=dict)
    passaporte: Passaporte | None = None
    seed: int = 42
    etapa: str = ""
    passo: str = ""
    _resultado: ResultadoBloco | None = None

    # --- dados ------------------------------------------------------------------------------

    def consultar(
        self,
        nome: str,
        sql: str,
        binds: dict[str, Any] | None = None,
        *,
        modo: Modo | str = Modo.AGREGADO,
        coluna_unidade: str | None = None,
        max_linhas: int | None = None,
    ) -> pd.DataFrame:
        if self.fonte is None:
            raise BlocoErro("Este passo precisa de uma fonte de dados configurada.")
        r = self.fonte.consultar(
            sql, binds, modo=modo, coluna_unidade=coluna_unidade, etapa=self.etapa, telemetria=self.telemetria,
            max_linhas=max_linhas,
        )
        if self._resultado is not None:
            self._resultado.consultas.append(
                ConsultaFeita(nome=nome, sql=r.veredito.sql_canonico, binds=dict(r.veredito.binds),
                              sha256=r.veredito.sha256, modo=r.veredito.modo, linhas=r.linhas, segundos=round(r.segundos, 3),
                              df=r.df if r.veredito.modo == "agregado" else None)
            )
            for aviso in r.veredito.avisos:
                if aviso not in self._resultado.avisos:
                    self._resultado.avisos.append(aviso)
        return r.df

    # --- evidência --------------------------------------------------------------------------

    def metrica(
        self,
        nome: str,
        valor: float | int | str | None,
        *,
        descricao: str,
        unidade: str = "",
        casas: int | None = None,
        compacto: bool = False,
        sinal: bool = False,
        ic: tuple[float, float] | None = None,
        n: int | None = None,
    ) -> Metrica:
        if hasattr(valor, "item") and not isinstance(valor, (str, bytes)):
            valor = valor.item()  # numpy -> Python
        texto = formatar(valor, casas=casas, unidade=unidade, compacto=compacto, sinal=sinal) if not isinstance(valor, str) else valor
        origem = f"{self._resultado.bloco}@{self._resultado.versao}" if self._resultado else ""
        vm = self.marcadores.novo(valor, texto=texto, descricao=descricao, unidade=unidade, origem=origem)
        m = Metrica(id=vm.id, nome=nome, valor=valor if not isinstance(valor, float) or valor == valor else None,
                    texto=texto, unidade=unidade, descricao=descricao, ic=ic, n=n, bloco=origem, etapa=self.etapa)
        if self._resultado is not None:
            self._resultado.metricas.append(m)
        return m

    def afirmacao(self, tipo: str, texto: str, *, selo: Selo, bases: list[str] | tuple[str, ...] = (), id_: str | None = None) -> Afirmacao:
        prefixo = {"fato": "F", "hipotese": "H", "recomendacao": "R"}[tipo]
        origem = f"{self._resultado.bloco}@{self._resultado.versao}" if self._resultado else ""
        a = Afirmacao(id=id_ or self.ids.proximo(prefixo), tipo=tipo, texto=texto, selo=selo, bases=list(bases),
                      origem="python", bloco=origem)
        if self._resultado is not None:
            self._resultado.afirmacoes.append(a)
        return a

    def decisao(
        self,
        titulo: str,
        pergunta: str,
        opcoes: list[tuple[str, str, str]],
        *,
        recomendada: str | None = None,
        bloqueia: tuple[str, ...] = ("plano",),
        dados: dict[str, Any] | None = None,
    ) -> Decisao:
        d = Decisao(
            id=self.ids.proximo("D"), titulo=titulo, pergunta=pergunta,
            opcoes=[OpcaoDecisao(codigo=c, rotulo=r, consequencia=q) for c, r, q in opcoes],
            recomendada=recomendada, origem=self.passo or self.etapa, bloqueia=list(bloqueia), dados=dados or {},
        )
        if self._resultado is not None:
            self._resultado.decisoes.append(d)
        return d

    def lacuna(self, descricao: str, impacto: str = "") -> Lacuna:
        l = Lacuna(id=self.ids.proximo("L"), descricao=descricao, impacto=impacto)
        if self._resultado is not None:
            self._resultado.lacunas.append(l)
        return l

    def figura(self, fig, *, nome: str, titulo: str, descricao: str) -> Figura:
        buffer = io.BytesIO()
        fig.savefig(buffer, format="png", dpi=110, bbox_inches="tight")
        try:
            import matplotlib.pyplot as plt

            plt.close(fig)
        except Exception:  # noqa: BLE001
            pass
        f = Figura(nome=nome, titulo=titulo, descricao=descricao, png=buffer.getvalue())
        if self._resultado is not None:
            self._resultado.figuras.append(f)
        return f

    def tabela_para_ia(self, nome: str, df: pd.DataFrame, *, coluna_n: str | None = None, max_linhas: int = 60) -> pd.DataFrame:
        """Tabela que a IA pode ver: só agregados com grupo mínimo e tamanho limitado."""
        t = df.copy()
        numericas = t.select_dtypes("number").columns
        if len(numericas):
            t[numericas] = t[numericas].round(4)
        if coluna_n and coluna_n in t:
            t = t[t[coluna_n] >= self.config.data_policy.min_group_size]
        limite_celulas = self.config.data_policy.max_celulas_para_llm
        linhas = max(1, min(max_linhas, limite_celulas // max(1, t.shape[1])))
        t = t.head(linhas)
        if self._resultado is not None:
            self._resultado.tabelas_ia[nome] = t
        return t


class Bloco:
    """Classe base. Subclasses definem os atributos e ``executar``."""

    nome: ClassVar[str] = ""
    versao: ClassVar[str] = "1.0"
    papel: ClassVar[str] = "Analista de dados"
    titulo: ClassVar[str] = ""
    descricao: ClassVar[str] = ""
    tipos_pergunta: ClassVar[frozenset[str]] = frozenset()  # vazio = serve para qualquer tipo
    requer: ClassVar[tuple[str, ...]] = ()  # chaves em ctx.dados
    produz: ClassVar[tuple[str, ...]] = ()
    exige_mapeamento: ClassVar[tuple[str, ...]] = ()  # ex.: ("tempo", "tratamento")
    origem: ClassVar[str] = "nativo"

    class Parametros(Parametros):
        pass

    # --- contrato --------------------------------------------------------------------------

    @classmethod
    def validar_parametros(cls, dados: dict[str, Any] | None) -> BaseModel:
        try:
            return cls.Parametros.model_validate(dados or {})
        except ValidationError as e:
            partes = [f"{'.'.join(str(x) for x in err['loc']) or '(raiz)'}: {err['msg']}" for err in e.errors()]
            raise BlocoErro(f"Parâmetros inválidos para {cls.nome}@{cls.versao}: " + "; ".join(partes)) from e

    def precondicoes(self, ctx: ContextoBloco, p: BaseModel) -> list[Precondicao]:
        saida = []
        for chave in self.requer:
            saida.append(Precondicao(f"dados de '{chave}' disponíveis", chave in ctx.dados,
                                     "" if chave in ctx.dados else f"rode antes o passo que produz '{chave}'"))
        for campo in self.exige_mapeamento:
            ok = ctx.mapeamento is not None and bool(getattr(ctx.mapeamento, campo, None))
            saida.append(Precondicao(f"mapeamento tem '{campo}'", ok, "" if ok else f"defina '{campo}' no mapeamento (descoberta)"))
        if self.tipos_pergunta and ctx.carta.tipo_pergunta not in self.tipos_pergunta:
            saida.append(Precondicao(
                f"tipo de pergunta compatível ({', '.join(sorted(self.tipos_pergunta))})", False,
                f"a carta é do tipo '{ctx.carta.tipo_pergunta}'", bloqueante=False,
            ))
        return saida

    def executar(self, ctx: ContextoBloco, p: BaseModel) -> ResultadoBloco:  # pragma: no cover - abstrato
        raise NotImplementedError

    def codigo(self, p: BaseModel, r: ResultadoBloco) -> str:
        """Linhas Python do pipeline de handoff (usam só ``adex_metodos`` e pandas)."""
        return f"# {self.nome}@{self.versao}: ver sql/ e reports/ (sem código executável equivalente)"

    @classmethod
    def ficha(cls) -> dict[str, Any]:
        esquema = cls.Parametros.model_json_schema()
        params = {}
        for nome, prop in (esquema.get("properties") or {}).items():
            params[nome] = {k: v for k, v in prop.items() if k in ("type", "default", "enum", "description", "minimum", "maximum", "anyOf")}
        return {
            "bloco": cls.nome, "versao": cls.versao, "papel": cls.papel, "titulo": cls.titulo, "descricao": cls.descricao,
            "tipos_pergunta": sorted(cls.tipos_pergunta) or ["qualquer"], "requer": list(cls.requer),
            "produz": list(cls.produz), "exige_mapeamento": list(cls.exige_mapeamento),
            "parametros": params, "obrigatorios": esquema.get("required", []), "origem": cls.origem,
        }


def rodar_bloco(bloco: Bloco, ctx: ContextoBloco, parametros: dict[str, Any] | None, *, passo: str,
                decisao_humana_se_falhar: bool = False) -> ResultadoBloco:
    """Valida, confere pré-condições, executa com telemetria e nunca deixa exceção escapar crua."""
    r = ResultadoBloco(bloco=bloco.nome, versao=bloco.versao, passo=passo)
    ctx._resultado, ctx.passo = r, passo
    try:
        p = bloco.validar_parametros(parametros)
        pre = bloco.precondicoes(ctx, p)
        falhas = [x for x in pre if not x.ok and x.bloqueante]
        for x in pre:
            if not x.ok and not x.bloqueante:
                r.avisos.append(f"{x.descricao}: {x.detalhe}")
        if falhas:
            texto = "; ".join(f"{x.descricao} ({x.detalhe})" if x.detalhe else x.descricao for x in falhas)
            r.status = "aguarda_decisao" if decisao_humana_se_falhar else "falhou"
            r.erro = f"pré-condições não atendidas: {texto}"
            if decisao_humana_se_falhar:
                ctx.decisao(
                    f"{passo}: pré-condição falhou", f"O passo '{passo}' não pode rodar como planejado: {texto}. O que fazer?",
                    [("a", "Ajustar o plano", "o plano volta para revisão"),
                     ("b", "Seguir mesmo assim, registrando o limite", "o resultado perde força (selo)"),
                     ("c", "Parar o estudo aqui", "o estudo encerra com o que já existe")],
                    recomendada="a", bloqueia=("aceite",),
                )
            return r
        with ctx.telemetria.observar(f"bloco:{bloco.nome}", tipo="tool", etapa=ctx.etapa,
                                     entrada={"passo": passo, "parametros": p.model_dump(mode="json")},
                                     metadados={"bloco": f"{bloco.nome}@{bloco.versao}"}) as obs:
            saida = bloco.executar(ctx, p)
            obs.finalizar(saida={"status": saida.status, "metricas": [m.id for m in saida.metricas]})
        if saida is not r:  # o bloco pode devolver o próprio resultado preenchido
            for campo in ("status", "resumo", "premissas_ok", "robustez_ok"):
                setattr(r, campo, getattr(saida, campo))
            for campo in ("metricas", "afirmacoes", "decisoes", "lacunas", "figuras", "consultas", "avisos"):
                for item in getattr(saida, campo):
                    if item not in getattr(r, campo):
                        getattr(r, campo).append(item)
            r.tabelas.update(saida.tabelas)
            r.tabelas_ia.update(saida.tabelas_ia)
            r.dados.update(saida.dados)
        ctx.dados.update(r.dados)
        r.codigo = bloco.codigo(p, r)
        return r
    except BlocoErro as e:
        r.status, r.erro = "falhou", e.mensagem
        return r
    except Exception as e:  # noqa: BLE001 - erro do bloco vira resultado "falhou" com explicação
        log.exception("bloco %s falhou", bloco.nome)
        r.status = "falhou"
        r.erro = f"{type(e).__name__}: {str(e)[:400]}"
        r.avisos.append("detalhe técnico: " + traceback.format_exc(limit=2).splitlines()[-1][:300])
        return r
    finally:
        ctx._resultado = None


def json_curto(obj: Any, limite: int = 400) -> str:
    s = json.dumps(obj, ensure_ascii=False, default=str)
    return s if len(s) <= limite else s[: limite - 1] + "…"
