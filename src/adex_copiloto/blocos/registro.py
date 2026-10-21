"""Registro de blocos: nativos e criados pelo dev (``@adex.block``)."""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable
from typing import Any, get_type_hints

from pydantic import BaseModel, ConfigDict, create_model

from adex_copiloto.blocos.base import Bloco, ContextoBloco, Parametros, ResultadoBloco
from adex_copiloto.erros import BlocoErro
from adex_copiloto.estudo.plano import Plano

_VERSAO = re.compile(r"^\d+\.\d+(\.\d+)?$")


def _chave_versao(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


class RegistroBlocos:
    def __init__(self) -> None:
        self._blocos: dict[tuple[str, str], type[Bloco]] = {}

    def registrar(self, cls: type[Bloco], *, substituir: bool = False) -> type[Bloco]:
        if not cls.nome or not re.fullmatch(r"[a-z][a-z0-9_]{1,40}", cls.nome):
            raise BlocoErro(f"Nome de bloco inválido: {cls.nome!r} (use minúsculas e _).")
        if not _VERSAO.match(cls.versao):
            raise BlocoErro(f"Versão inválida em {cls.nome}: {cls.versao!r} (use 1.0 ou 1.0.0).")
        chave = (cls.nome, cls.versao)
        if chave in self._blocos and not substituir:
            raise BlocoErro(f"{cls.nome}@{cls.versao} já existe; versões publicadas não mudam — crie uma nova versão.")
        self._blocos[chave] = cls
        return cls

    def obter(self, nome: str, versao: str | None = None) -> Bloco:
        versoes = sorted((v for n, v in self._blocos if n == nome), key=_chave_versao)
        if not versoes:
            parecidos = [n for n, _ in self._blocos if nome[:4] in n]
            dica = f" Parecidos: {', '.join(sorted(set(parecidos)))}." if parecidos else ""
            raise BlocoErro(f"Bloco '{nome}' não existe no registro.{dica}")
        if versao:
            if versao not in versoes:
                raise BlocoErro(f"{nome}@{versao} não existe. Versões: {', '.join(versoes)}.")
            return self._blocos[(nome, versao)]()
        return self._blocos[(nome, versoes[-1])]()

    get = obter  # API do mock: adex.blocks.get("desfecho_estavel", "1.0")

    def existe(self, nome: str, versao: str | None = None) -> bool:
        try:
            self.obter(nome, versao)
            return True
        except BlocoErro:
            return False

    def listar(self, tipo_pergunta: str | None = None) -> list[dict[str, Any]]:
        ultimos: dict[str, type[Bloco]] = {}
        for (n, v), cls in self._blocos.items():
            if n not in ultimos or _chave_versao(v) > _chave_versao(ultimos[n].versao):
                ultimos[n] = cls
        fichas = [c.ficha() for c in ultimos.values()]
        if tipo_pergunta:
            fichas = [f for f in fichas if "qualquer" in f["tipos_pergunta"] or tipo_pergunta in f["tipos_pergunta"]]
        return sorted(fichas, key=lambda f: f["bloco"])

    def __len__(self) -> int:
        return len({n for n, _ in self._blocos})

    def validar_plano(self, plano: Plano, *, produzidos_antes: set[str] | None = None) -> list[str]:
        """Confere blocos, versões, parâmetros e a ordem dos dados. Fixa a versão quando vier vazia."""
        problemas = []
        disponiveis = set(produzidos_antes or set())
        ids = set()
        for p in plano.passos:
            if p.id in ids:
                problemas.append(f"passo '{p.id}' repetido.")
            ids.add(p.id)
            try:
                b = self.obter(p.bloco, p.versao or None)
            except BlocoErro as e:
                problemas.append(f"passo '{p.id}': {e.mensagem}")
                continue
            p.versao = b.versao
            p.papel = p.papel or b.papel
            try:
                b.validar_parametros(p.parametros)
            except BlocoErro as e:
                problemas.append(f"passo '{p.id}': {e.mensagem}")
            faltam = [r for r in b.requer if r not in disponiveis]
            if faltam:
                problemas.append(f"passo '{p.id}' ({b.nome}) precisa de {', '.join(faltam)} produzido(s) antes.")
            disponiveis.update(b.produz)
        return problemas


REGISTRO = RegistroBlocos()


def registrar_nativo(cls: type[Bloco]) -> type[Bloco]:
    return REGISTRO.registrar(cls)


def bloco_do_dev(
    *,
    nome: str,
    versao: str = "1.0",
    papel: str = "Analista de dados",
    titulo: str = "",
    descricao: str = "",
    tipos_pergunta: tuple[str, ...] = (),
    requer: tuple[str, ...] = (),
    produz: tuple[str, ...] = (),
    registro: RegistroBlocos | None = None,
) -> Callable[[Callable[..., Any]], type[Bloco]]:
    """Decorador ``@adex.block``: transforma uma função em bloco registrado.

    A função recebe ``ctx`` e os parâmetros tipados; os tipos viram o esquema estrito::

        @adex.block(nome="minha_medida", versao="1.0", requer=("painel",), produz=("minha_tabela",))
        def minha_medida(ctx, janela: int = 3) -> dict:
            df = ctx.dados["painel"]
            ...
            return {"minha_tabela": resultado}
    """

    def decorar(func: Callable[..., Any]) -> type[Bloco]:
        dicas = get_type_hints(func)
        assinatura = inspect.signature(func)
        campos: dict[str, Any] = {}
        for i, (pnome, param) in enumerate(assinatura.parameters.items()):
            if i == 0:
                continue  # ctx
            tipo = dicas.get(pnome, Any)
            padrao = param.default if param.default is not inspect.Parameter.empty else ...
            campos[pnome] = (tipo, padrao)
        modelo = create_model(f"Parametros_{nome}", __base__=Parametros, **campos)

        def executar(self, ctx: ContextoBloco, p: BaseModel) -> ResultadoBloco:
            saida = func(ctx, **p.model_dump())
            r = ctx._resultado
            if isinstance(saida, ResultadoBloco):
                return saida
            if isinstance(saida, dict):
                r.dados.update(saida)
                r.resumo = r.resumo or f"Bloco do dev '{nome}' rodou e produziu: {', '.join(saida) or 'nada'}."
            return r

        cls = type(
            f"BlocoDev_{nome}",
            (Bloco,),
            {
                "nome": nome, "versao": versao, "papel": papel, "titulo": titulo or nome,
                "descricao": descricao or (func.__doc__ or "").strip().split("\n")[0],
                "tipos_pergunta": frozenset(tipos_pergunta), "requer": tuple(requer), "produz": tuple(produz),
                "origem": "dev", "Parametros": modelo, "executar": executar,
            },
        )
        (registro if registro is not None else REGISTRO).registrar(cls)
        cls.funcao = staticmethod(func)  # type: ignore[attr-defined]
        return cls

    return decorar
