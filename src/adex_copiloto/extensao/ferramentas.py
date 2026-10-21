"""Ferramentas do dev (``@adex.tool``): funções Python que a IA pode pedir para chamar.

Antes de a IA poder usar uma ferramenta, testes de contrato rodam **sem IA**:

1. assinatura tipada vira esquema estrito de entrada;
2. lê só o que declarou em ``reads=[...]`` (via ``adex.io.open``);
3. não escreve em lugar nenhum;
4. saída sem dados pessoais (nomes de coluna e valores);
5. mesmo resultado em duas execuções;
6. coerente com o painel do estudo (quando há coluna de grupo em comum) — aviso.

Depois, a IA só chama a função com parâmetros validados e não pode mudar o código.
Limite declarado: o kernel não é isolado; a garantia está no que chega à IA (saída
conferida, mascarada e limitada) e no que fica registrado no dossiê.
"""

from __future__ import annotations

import builtins
import inspect
import io
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, get_type_hints

import pandas as pd
from pydantic import BaseModel, ConfigDict, ValidationError, create_model

from adex_copiloto.dados.fichas import classificar_coluna
from adex_copiloto.erros import FerramentaErro, LeituraNaoDeclaradaErro
from adex_copiloto.extensao.io import escopo_ferramenta
from adex_copiloto.seguranca.redacao import contem_pii, mascarar

MAX_LINHAS_IA = 200


@dataclass
class ResultadoTeste:
    nome: str
    ok: bool | None  # None = aviso
    detalhe: str = ""


@dataclass
class FerramentaDev:
    nome: str
    funcao: Callable[..., Any]
    reads: list[str]
    returns: dict[str, str]
    privacy: Literal["aggregate", "internal"] = "aggregate"
    scope: Literal["study", "team"] = "study"
    exemplo: dict[str, Any] = field(default_factory=dict)
    versao: int = 1
    entrada: type[BaseModel] | None = None
    testes: list[ResultadoTeste] = field(default_factory=list)
    aprovada: bool = False
    codigo: str = ""

    @property
    def descricao(self) -> str:
        return (inspect.getdoc(self.funcao) or self.nome).strip().split("\n")[0]

    def esquema_ia(self) -> dict[str, Any]:
        return {"type": "function", "function": {"name": self.nome, "description": self.descricao[:300],
                                                 "parameters": self.entrada.model_json_schema() if self.entrada else {"type": "object"}}}


class _Proibido(Exception):
    pass


def _modelo_entrada(nome: str, func: Callable[..., Any]) -> type[BaseModel]:
    dicas = get_type_hints(func)
    campos: dict[str, Any] = {}
    for pnome, param in inspect.signature(func).parameters.items():
        if pnome not in dicas:
            raise FerramentaErro(f"'{nome}': o parâmetro '{pnome}' precisa de tipo (ex.: cidades: list[str]).")
        campos[pnome] = (dicas[pnome], param.default if param.default is not inspect.Parameter.empty else ...)
    return create_model(f"Entrada_{nome}", __config__=ConfigDict(extra="forbid"), **campos)


class RegistroFerramentas:
    def __init__(self, io_obj, dossie=None) -> None:
        self.io = io_obj
        self.dossie = dossie
        self.ferramentas: dict[str, FerramentaDev] = {}

    # --- registro -------------------------------------------------------------------------

    def registrar(self, *, name: str, reads: list[str] | None = None, returns: dict[str, str] | None = None,
                  privacy: str = "aggregate", scope: str = "study", exemplo: dict[str, Any] | None = None):
        def decorar(func: Callable[..., Any]) -> Callable[..., Any]:
            anterior = self.ferramentas.get(name)
            f = FerramentaDev(nome=name, funcao=func, reads=list(reads or []), returns=dict(returns or {}), privacy=privacy,  # type: ignore[arg-type]
                              scope=scope, exemplo=dict(exemplo or {}), versao=(anterior.versao + 1) if anterior else 1)  # type: ignore[arg-type]
            try:
                f.codigo = inspect.getsource(func)
            except (OSError, TypeError):
                f.codigo = ""
            f.entrada = _modelo_entrada(name, func)
            self.ferramentas[name] = f
            if self.dossie is not None:
                self.dossie.registrar("ferramenta_registrada", {"nome": name, "versao": f.versao, "reads": f.reads,
                                                                "returns": f.returns, "privacy": privacy, "scope": scope,
                                                                "codigo": f.codigo[:20000]}, ator="dev")
            func.adex_ferramenta = f  # type: ignore[attr-defined]
            return func

        return decorar

    def obter(self, nome: str) -> FerramentaDev:
        if nome not in self.ferramentas:
            raise FerramentaErro(f"Ferramenta '{nome}' não registrada. Registradas: {', '.join(self.ferramentas) or 'nenhuma'}.")
        return self.ferramentas[nome]

    # --- execução protegida -------------------------------------------------------------------

    def _executar(self, f: FerramentaDev, args: dict[str, Any]) -> tuple[Any, list[str]]:
        import threading

        entrada = f.entrada.model_validate(args) if f.entrada else None
        abrir_original, io_original = builtins.open, io.open
        thread_ferramenta = threading.get_ident()

        def abrir_so_leitura(arquivo, modo="r", *a, **kw):
            # Só a thread da ferramenta é vigiada: o copiloto (dossiê, fila) segue gravando normalmente.
            if threading.get_ident() == thread_ferramenta and any(m in str(modo) for m in ("w", "a", "x", "+")):
                raise _Proibido(f"tentou escrever em {arquivo}")
            return abrir_original(arquivo, modo, *a, **kw)

        builtins.open = abrir_so_leitura  # type: ignore[assignment]
        io.open = abrir_so_leitura  # type: ignore[assignment]
        try:
            with escopo_ferramenta(f.reads) as lidas:
                saida = f.funcao(**(entrada.model_dump() if entrada else {}))
        finally:
            builtins.open, io.open = abrir_original, io_original
        return saida, list(lidas)

    def _conferir_saida(self, f: FerramentaDev, saida: Any) -> list[str]:
        problemas = []
        if not isinstance(saida, pd.DataFrame):
            return ["a saída precisa ser um pandas.DataFrame"]
        extras = [c for c in saida.columns if f.returns and c not in f.returns]
        if extras:
            problemas.append(f"colunas não declaradas em returns: {extras}")
        for c in saida.columns:
            classe, motivo = classificar_coluna(str(c))
            if classe == "sim":
                problemas.append(f"coluna '{c}' parece dado pessoal ({motivo})")
        amostra = saida.head(500)
        for c in amostra.columns:
            if amostra[c].dtype == object or pd.api.types.is_string_dtype(amostra[c]):
                achados = {p for v in amostra[c].dropna().astype(str) for p in contem_pii(v)}
                if achados:
                    problemas.append(f"coluna '{c}' tem valores que parecem dados pessoais ({', '.join(sorted(achados))})")
        return problemas

    def testar(self, nome: str, *, painel: pd.DataFrame | None = None, coluna_grupo: str | None = None, **args) -> list[ResultadoTeste]:
        """Roda os testes de contrato (sem IA). A ferramenta só fica disponível se todos passarem."""
        f = self.obter(nome)
        exemplo = {**f.exemplo, **args}
        r: list[ResultadoTeste] = []
        try:
            esquema = f.entrada.model_json_schema() if f.entrada else {}
            r.append(ResultadoTeste("Assinatura tipada vira esquema estrito", True, f"{len(esquema.get('properties', {}))} parâmetro(s)"))
        except Exception as e:  # noqa: BLE001
            r.append(ResultadoTeste("Assinatura tipada vira esquema estrito", False, str(e)))
        saida1 = None
        try:
            saida1, lidas = self._executar(f, exemplo)
            r.append(ResultadoTeste("Lê só o que declarou", True, f"{len(lidas)} leitura(s) declarada(s)"))
            r.append(ResultadoTeste("Não escreve em lugar nenhum", True))
        except LeituraNaoDeclaradaErro as e:
            r.append(ResultadoTeste("Lê só o que declarou", False, e.mensagem))
        except _Proibido as e:
            r.append(ResultadoTeste("Lê só o que declarou", True))
            r.append(ResultadoTeste("Não escreve em lugar nenhum", False, str(e)))
        except ValidationError as e:
            r.append(ResultadoTeste("Exemplo de entrada válido", False, f"passe exemplo= no @adex.tool ou argumentos no test(): {e.errors()[:2]}"))
        except Exception as e:  # noqa: BLE001
            r.append(ResultadoTeste("Execução do exemplo", False, f"{type(e).__name__}: {str(e)[:200]}"))
        if saida1 is not None:
            problemas = self._conferir_saida(f, saida1)
            r.append(ResultadoTeste("Saída sem dados pessoais e só com o declarado", not problemas, "; ".join(problemas)))
            try:
                saida2, _ = self._executar(f, exemplo)
                igual = isinstance(saida2, pd.DataFrame) and saida1.reset_index(drop=True).equals(saida2.reset_index(drop=True))
                r.append(ResultadoTeste("Mesmo resultado em 2 execuções", igual, "" if igual else "as duas execuções diferem"))
            except Exception as e:  # noqa: BLE001
                r.append(ResultadoTeste("Mesmo resultado em 2 execuções", False, f"{type(e).__name__}"))
            if painel is not None and coluna_grupo and isinstance(saida1, pd.DataFrame):
                comuns = [c for c in saida1.columns if c.lower() in (coluna_grupo.lower(), "grupo")]
                if comuns:
                    valores = set(saida1[comuns[0]].astype(str))
                    conhecidos = set(painel["grupo"].astype(str))
                    fora = sorted(valores - conhecidos)
                    r.append(ResultadoTeste("Coerente com os dados do painel", None if fora else True,
                                            f"{len(fora)} valor(es) não existem no painel: {fora[:5]}" if fora else ""))
        f.testes = r
        f.aprovada = all(t.ok is not False for t in r) and any(t.nome.startswith("Mesmo resultado") and t.ok for t in r)
        if self.dossie is not None:
            self.dossie.registrar("teste_contrato", {"nome": nome, "versao": f.versao, "aprovada": f.aprovada,
                                                     "testes": [t.__dict__ for t in r]})
        return r

    def chamar(self, nome: str, args: dict[str, Any]) -> pd.DataFrame:
        """Chamada pedida pela IA: só para ferramentas aprovadas; saída conferida e limitada."""
        f = self.obter(nome)
        if not f.aprovada:
            raise FerramentaErro(f"'{nome}' ainda não passou nos testes de contrato: rode estudo.tools.test('{nome}').")
        try:
            saida, lidas = self._executar(f, args)
        except ValidationError as e:
            raise FerramentaErro(f"Argumentos inválidos para '{nome}': {e.errors()[:3]}") from e
        problemas = self._conferir_saida(f, saida)
        if problemas:
            raise FerramentaErro(f"A saída de '{nome}' foi bloqueada: " + "; ".join(problemas))
        if self.dossie is not None:
            self.dossie.registrar("ferramenta_chamada", {"nome": nome, "versao": f.versao, "args": mascarar(json.loads(json.dumps(args, default=str))),
                                                         "lidas": lidas, "linhas": len(saida)}, ator="ia")
        return saida.head(MAX_LINHAS_IA)

    def esquemas_ia(self) -> list[dict[str, Any]]:
        return [f.esquema_ia() for f in self.ferramentas.values() if f.aprovada]
