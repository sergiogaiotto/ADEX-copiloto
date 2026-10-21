"""Ferramentas seguras dos agentes: só metadados, fichas e validações.

Nenhuma ferramenta executa SQL nem código:
- ``propor_sql`` passa a consulta pelo porteiro e devolve o veredito como texto;
- ``sugerir_celula`` guarda o código para o dev ver na próxima célula.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import StructuredTool

from adex_copiloto.blocos.registro import RegistroBlocos
from adex_copiloto.dados.catalogo import Catalogo
from adex_copiloto.dados.porteiro import Porteiro
from adex_copiloto.estudo.plano import PassoPlano, Plano

LIMITE_TEXTO = 12_000


def _json(obj: Any) -> str:
    texto = json.dumps(obj, ensure_ascii=False, default=str)
    return texto if len(texto) <= LIMITE_TEXTO else texto[: LIMITE_TEXTO - 40] + '…"(cortado: refine a busca)"'


def ferramentas_descoberta(catalogo: Catalogo, porteiro: Porteiro | None = None) -> list[StructuredTool]:
    def listar_tabelas(esquema: str = "", termo: str = "", limite: int = 40) -> str:
        """Lista tabelas do catálogo (só metadados). Filtre por esquema e/ou termo do nome/comentário."""
        fichas = catalogo.buscar(termo, limite=limite) if termo else list(catalogo.tabelas.values())
        if esquema:
            fichas = [f for f in fichas if f.esquema == esquema.upper()]
        return _json([{"tabela": f.nome_completo, "linhas": f.linhas_estimadas, "comentario": f.comentario[:120],
                       "colunas": len(f.colunas), "acesso": f.acesso} for f in fichas[: max(1, min(limite, 200))]])

    def buscar_colunas(termo: str) -> str:
        """Procura colunas por parte do nome ou do comentário, em todas as tabelas."""
        return _json(catalogo.buscar_colunas(termo))

    def ler_ficha(tabela: str) -> str:
        """Ficha completa de uma tabela: colunas, tipos, comentários e marcas de dado pessoal."""
        f = catalogo.resolver(tabela)
        return _json(f.para_llm()) if f else _json({"erro": f"a tabela {tabela} não existe no catálogo"})

    ferramentas = [
        StructuredTool.from_function(listar_tabelas),
        StructuredTool.from_function(buscar_colunas),
        StructuredTool.from_function(ler_ficha),
    ]
    if porteiro is not None:
        def propor_sql(sql: str, objetivo: str = "") -> str:
            """Confere uma consulta no porteiro SEM executar. Devolve aprovada ou os motivos da recusa."""
            v = porteiro.avaliar(sql)
            return _json({"aprovada": v.aprovado, "motivos": v.motivos, "avisos": v.avisos,
                          "observacao": "nada foi executado; o dev decide se roda com %%adex sql"})

        ferramentas.append(StructuredTool.from_function(propor_sql))
    return ferramentas


def ferramentas_plano(
    registro: RegistroBlocos,
    catalogo: Catalogo,
    mapeamento: dict[str, Any] | None,
    tipo_pergunta: str,
    produzidos_antes: set[str],
    sugestoes: list[dict[str, str]],
) -> list[StructuredTool]:
    def listar_blocos(tipo: str = "") -> str:
        """Blocos de método registrados (nome, versão, para que tipo de pergunta, o que requerem e produzem)."""
        fichas = registro.listar(tipo or None)
        return _json([{k: f[k] for k in ("bloco", "versao", "titulo", "papel", "tipos_pergunta", "requer", "produz", "exige_mapeamento")}
                      for f in fichas])

    def ficha_bloco(nome: str) -> str:
        """Ficha completa de um bloco: descrição, parâmetros (com padrões) e pré-requisitos."""
        try:
            return _json(registro.obter(nome).ficha())
        except Exception as e:  # noqa: BLE001
            return _json({"erro": str(e)})

    def ver_mapeamento() -> str:
        """Mapeamento confirmado pelo dev: população, unidade, tempo, grupos, desfechos, tratamento, alvo."""
        return _json(mapeamento or {"erro": "sem mapeamento"})

    def ler_ficha(tabela: str) -> str:
        """Ficha de uma tabela do catálogo (só metadados)."""
        f = catalogo.resolver(tabela)
        return _json(f.para_llm()) if f else _json({"erro": f"a tabela {tabela} não existe"})

    def validar_plano(passos_json: str) -> str:
        """Confere um rascunho de passos (lista JSON de {id, bloco, versao, parametros}) no registro, sem rodar nada."""
        try:
            dados = json.loads(passos_json)
            passos = [PassoPlano(id=p["id"], bloco=p["bloco"], versao=p.get("versao", ""), parametros=p.get("parametros", {})) for p in dados]
        except Exception as e:  # noqa: BLE001
            return _json({"ok": False, "problemas": [f"JSON inválido: {e}"]})
        problemas = registro.validar_plano(Plano(passos=passos), produzidos_antes=produzidos_antes)
        return _json({"ok": not problemas, "problemas": problemas})

    def sugerir_celula(codigo: str, explicacao: str) -> str:
        """Sugere código para o dev colar na próxima célula. Nunca é executado pelo copiloto."""
        sugestoes.append({"codigo": codigo[:6000], "explicacao": explicacao[:600]})
        return _json({"ok": True, "observacao": "a sugestão aparece para o dev revisar; nada foi executado"})

    return [StructuredTool.from_function(f) for f in (listar_blocos, ficha_bloco, ver_mapeamento, ler_ficha, validar_plano, sugerir_celula)]
