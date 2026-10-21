"""Catálogo de metadados: todas as fichas que o perfil do dev já acessa.

Serve três coisas:
- a descoberta (a IA lê fichas em lotes que cabem no contexto do Qwen);
- a conferência de nomes (o Python descarta tabela ou coluna que não existe);
- o porteiro de SQL (esquema para qualificar colunas e saber o que é pessoal).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable

from adex_copiloto.dados.fichas import FichaColuna, FichaTabela

CHARS_POR_TOKEN = 3.2


class Catalogo:
    def __init__(self, fichas: Iterable[FichaTabela] = ()) -> None:
        self.tabelas: dict[str, FichaTabela] = {}
        for f in fichas:
            self.adicionar(f)

    def adicionar(self, ficha: FichaTabela) -> None:
        self.tabelas[ficha.nome_completo] = ficha

    def __len__(self) -> int:
        return len(self.tabelas)

    def __contains__(self, nome: str) -> bool:
        return self.resolver(nome) is not None

    # --- consultas ----------------------------------------------------------------------

    def resolver(self, nome: str) -> FichaTabela | None:
        """Aceita 'ESQUEMA.TABELA' ou só 'TABELA' quando o nome é único."""
        alvo = (nome or "").strip().strip('"').upper()
        if alvo in self.tabelas:
            return self.tabelas[alvo]
        if "." not in alvo:
            achados = [f for f in self.tabelas.values() if f.nome == alvo]
            if len(achados) == 1:
                return achados[0]
        return None

    def coluna(self, tabela: str, coluna: str) -> FichaColuna | None:
        f = self.resolver(tabela)
        return f.coluna(coluna) if f else None

    def esquemas(self) -> list[str]:
        return sorted({f.esquema for f in self.tabelas.values()})

    def buscar(self, termo: str, limite: int = 20) -> list[FichaTabela]:
        """Busca simples por palavras em nomes e comentários (sem IA)."""
        palavras = [p for p in re.split(r"[\s_.,;:/-]+", termo.lower()) if len(p) >= 3]
        if not palavras:
            return []
        pontos: list[tuple[int, str]] = []
        for nome, f in self.tabelas.items():
            texto = " ".join(
                [f.nome, f.comentario] + [c.nome for c in f.colunas] + [c.comentario for c in f.colunas]
            ).lower()
            p = sum(texto.count(w) for w in palavras) + sum(3 for w in palavras if w in f.nome.lower())
            if p:
                pontos.append((p, nome))
        pontos.sort(key=lambda x: (-x[0], x[1]))
        return [self.tabelas[n] for _, n in pontos[:limite]]

    def buscar_colunas(self, termo: str, limite: int = 40) -> list[dict]:
        alvo = termo.lower()
        saida = []
        for f in self.tabelas.values():
            for c in f.colunas:
                if alvo in c.nome.lower() or (c.comentario and alvo in c.comentario.lower()):
                    saida.append({"tabela": f.nome_completo, "coluna": c.nome, "tipo": c.tipo, "pii": c.bloqueada})
                    if len(saida) >= limite:
                        return saida
        return saida

    # --- para a IA ------------------------------------------------------------------------

    def lotes_para_llm(self, tokens_por_lote: int, max_lotes: int | None = None) -> list[list[dict]]:
        """Divide as fichas em lotes que cabem no contexto (estimativa conservadora)."""
        lotes: list[list[dict]] = [[]]
        tamanho = 0
        for nome in sorted(self.tabelas):
            visao = self.tabelas[nome].para_llm()
            t = int(len(json.dumps(visao, ensure_ascii=False)) / CHARS_POR_TOKEN) + 2
            if lotes[-1] and tamanho + t > tokens_por_lote:
                lotes.append([])
                tamanho = 0
            lotes[-1].append(visao)
            tamanho += t
        if max_lotes is not None:
            lotes = lotes[:max_lotes]
        return [l for l in lotes if l]

    def resumo(self) -> dict:
        return {
            "tabelas": len(self.tabelas),
            "esquemas": self.esquemas(),
            "colunas": sum(len(f.colunas) for f in self.tabelas.values()),
            "colunas_bloqueadas": sum(1 for f in self.tabelas.values() for c in f.colunas if c.bloqueada),
        }

    # --- para o porteiro --------------------------------------------------------------------

    def esquema_sqlglot(self, tabelas: Iterable[str] | None = None) -> dict:
        """Mapa {ESQUEMA: {TABELA: {COLUNA: TIPO}}} para o qualify do sqlglot."""
        saida: dict[str, dict[str, dict[str, str]]] = {}
        alvo = None if tabelas is None else {t.upper() for t in tabelas}
        for nome, f in self.tabelas.items():
            if alvo is not None and nome not in alvo:
                continue
            saida.setdefault(f.esquema, {})[f.nome] = {c.nome: (c.tipo or "VARCHAR") for c in f.colunas}
        return saida

    def marcar_identificador(self, tabela: str, coluna: str, valor: bool = True) -> FichaColuna:
        c = self.coluna(tabela, coluna)
        if c is None:
            raise KeyError(f"{tabela}.{coluna} não existe no catálogo")
        c.identificador = valor
        return c

    def liberar(self, tabela: str, coluna: str) -> FichaColuna:
        """Libera uma coluna 'suspeita' (nunca uma 'sim'). Registre a decisão no dossiê."""
        c = self.coluna(tabela, coluna)
        if c is None:
            raise KeyError(f"{tabela}.{coluna} não existe no catálogo")
        if c.pii == "sim":
            raise PermissionError(f"{tabela}.{coluna} é dado pessoal ({c.motivo_pii}) e não pode ser liberada.")
        c.liberada = True
        return c
