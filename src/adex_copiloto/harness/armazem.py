"""Armazém de dados do estudo (DataFrames e resultados entre passos), sem pickle.

O estado do grafo (LangGraph) guarda só JSON pequeno. Tabelas ficam aqui:
- DataFrame/Series -> Parquet;
- dicionários e listas -> JSON, com DataFrames aninhados em Parquet separado;
- modelos pydantic (ex.: Passaporte) -> JSON com o nome do tipo.

Sem pickle de propósito: abrir um estudo nunca executa código vindo de arquivo.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import threading
from collections.abc import Iterator, MutableMapping
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from pydantic import BaseModel

_TIPOS_PYDANTIC: dict[str, type[BaseModel]] = {}


def registrar_tipo(cls: type[BaseModel]) -> type[BaseModel]:
    _TIPOS_PYDANTIC[cls.__name__] = cls
    return cls


def _seguro(nome: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", nome)[:120]


class ArmazemDados(MutableMapping):
    def __init__(self, pasta: Path) -> None:
        self.pasta = Path(pasta)
        self.pasta.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, Any] = {}
        self._trava = threading.Lock()

    # --- serialização ------------------------------------------------------------------

    def _salvar_valor(self, base: str, valor: Any) -> Any:
        if isinstance(valor, pd.DataFrame):
            arq = f"{base}.parquet"
            df = valor.copy()
            df.columns = ["_".join(map(str, c)) if isinstance(c, tuple) else str(c) for c in df.columns]
            df.to_parquet(self.pasta / arq)  # o pyarrow guarda o índice quando não é o padrão
            return {"__df__": arq}
        if isinstance(valor, pd.Series):
            return {"__serie__": self._salvar_valor(base, valor.to_frame(name=str(valor.name or "valor")).reset_index()),
                    "nome": str(valor.name or "valor")}
        if isinstance(valor, BaseModel):
            registrar_tipo(type(valor))
            return {"__modelo__": type(valor).__name__, "dados": valor.model_dump(mode="json")}
        if isinstance(valor, dict):
            return {str(k): self._salvar_valor(f"{base}.{_seguro(str(k))}", v) for k, v in valor.items()}
        if isinstance(valor, (list, tuple)):
            return [self._salvar_valor(f"{base}.{i}", v) for i, v in enumerate(valor)]
        if isinstance(valor, np.ndarray):
            return {"__array__": valor.tolist()}
        if isinstance(valor, (np.integer,)):
            return int(valor)
        if isinstance(valor, (np.floating,)):
            return float(valor)
        if isinstance(valor, (np.bool_,)):
            return bool(valor)
        if isinstance(valor, (pd.Timestamp, dt.datetime, dt.date)):
            return {"__data__": pd.Timestamp(valor).isoformat()}
        if valor is pd.NaT:
            return None
        return valor

    def _carregar_valor(self, valor: Any) -> Any:
        if isinstance(valor, dict):
            if "__df__" in valor:
                df = pd.read_parquet(self.pasta / valor["__df__"])
                return df
            if "__serie__" in valor:
                df = self._carregar_valor(valor["__serie__"])
                nome = valor["nome"]
                indice = [c for c in df.columns if c != nome]
                return df.set_index(indice[0])[nome] if indice else df[nome]
            if "__modelo__" in valor:
                cls = _TIPOS_PYDANTIC.get(valor["__modelo__"])
                return cls.model_validate(valor["dados"]) if cls else valor["dados"]
            if "__array__" in valor:
                return np.asarray(valor["__array__"])
            if "__data__" in valor:
                return pd.Timestamp(valor["__data__"])
            return {k: self._carregar_valor(v) for k, v in valor.items()}
        if isinstance(valor, list):
            return [self._carregar_valor(v) for v in valor]
        return valor

    # --- MutableMapping ----------------------------------------------------------------

    def _indice(self, chave: str) -> Path:
        return self.pasta / f"{_seguro(chave)}.json"

    def __setitem__(self, chave: str, valor: Any) -> None:
        with self._trava:
            self._cache[chave] = valor
            estrutura = self._salvar_valor(_seguro(chave), valor)
            tmp = self._indice(chave).with_suffix(".tmp")
            tmp.write_text(json.dumps(estrutura, ensure_ascii=False, default=str), encoding="utf-8")
            tmp.replace(self._indice(chave))

    def __getitem__(self, chave: str) -> Any:
        with self._trava:
            if chave in self._cache:
                return self._cache[chave]
            arq = self._indice(chave)
            if not arq.exists():
                raise KeyError(chave)
            valor = self._carregar_valor(json.loads(arq.read_text(encoding="utf-8")))
            self._cache[chave] = valor
            return valor

    def __delitem__(self, chave: str) -> None:
        with self._trava:
            self._cache.pop(chave, None)
            arq = self._indice(chave)
            if arq.exists():
                arq.unlink()
            for p in self.pasta.glob(f"{_seguro(chave)}.*.parquet"):
                p.unlink()
            p = self.pasta / f"{_seguro(chave)}.parquet"
            if p.exists():
                p.unlink()

    def __iter__(self) -> Iterator[str]:
        nomes = {p.stem for p in self.pasta.glob("*.json")} | set(self._cache)
        return iter(sorted(nomes))

    def __len__(self) -> int:
        return len(list(iter(self)))

    def __contains__(self, chave: object) -> bool:
        return isinstance(chave, str) and (chave in self._cache or self._indice(chave).exists())

    def update(self, outros=(), /, **kw) -> None:  # type: ignore[override]
        itens = dict(outros, **kw)
        for k, v in itens.items():
            self[k] = v


def _registrar_padroes() -> None:
    from adex_copiloto.estudo.mapeamento import Mapeamento
    from adex_copiloto.estudo.passaporte import Passaporte

    for cls in (Passaporte, Mapeamento):
        registrar_tipo(cls)


_registrar_padroes()
