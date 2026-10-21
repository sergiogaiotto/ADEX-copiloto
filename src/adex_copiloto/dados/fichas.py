"""Fichas de tabelas e colunas: o que a IA pode ver sobre os dados.

A IA vê só metadados: nome, tipo, comentário (mascarado) e contagem de linhas.
Nunca vê valores, mínimos, máximos ou histogramas.

Cada coluna recebe uma classificação de dado pessoal:
- ``sim``: padrão forte (CPF, e-mail, telefone, endereço, nascimento...);
- ``suspeita``: padrão ambíguo (ex.: ``NOME`` sozinho pode ser nome de cidade);
- ``nao``.

``sim`` e ``suspeita`` ficam bloqueadas na saída de consultas. Uma ``suspeita``
pode ser liberada pelo dev com uma decisão registrada no dossiê; ``sim`` não.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

from adex_copiloto.seguranca.redacao import mascarar_texto

ClassePII = Literal["nao", "suspeita", "sim"]

_FORTES: list[tuple[str, str]] = [
    ("cpf", r"(^|_)(cpf|nr_cpf|num_cpf|cpf_cnpj|cpfcnpj)($|_)"),
    ("cnpj", r"(^|_)cnpj($|_)"),
    ("rg", r"(^|_)(rg|nr_rg|num_rg)($|_)"),
    ("email", r"(^|_)e_?mail|email($|_)"),
    ("telefone", r"(^|_)(fone|telefone|celular|msisdn|whatsapp|ddd_fone|nr_tel|num_tel|tel)($|_)"),
    ("nome_pessoa", r"(^|_)(nome|nm)_(cliente|titular|pessoa|contato|responsavel|mae|pai|usuario|assinante|completo|social)($|_)"),
    ("endereco", r"(^|_)(endereco|logradouro|end_completo|complemento|numero_residencia|nr_endereco)($|_)"),
    ("cep", r"(^|_)(cep|nr_cep)($|_)"),
    ("geolocalizacao", r"(^|_)(latitude|longitude|lat|lon|lng|geoloc|geo_loc|coordenada)($|_)"),
    ("nascimento", r"(nascimento|dt_nasc|data_nasc|birth)"),
    ("documento", r"(^|_)(passaporte|cnh|titulo_eleitor|pis|nis|ctps|nit)($|_)"),
    ("cartao", r"(^|_)(cartao|nr_cartao|card_number|pan)($|_)"),
    ("senha", r"(senha|password|pwd|token_acesso)"),
    ("ip", r"(^|_)(ip|ip_address|endereco_ip|ip_origem)($|_)"),
    ("dispositivo", r"(^|_)(imei|iccid|imsi|mac_address|mac)($|_)"),
    ("placa", r"(^|_)placa($|_)"),
    ("biometria", r"(biometria|face_id|digital_hash)"),
]
_AMBIGUOS: list[tuple[str, str]] = [
    ("nome", r"^(nome|nm|nome_[a-z]+|nm_[a-z]+)$"),
    ("bairro", r"(^|_)bairro($|_)"),
    ("numero", r"^(numero|nr|num)$"),
    ("conta", r"(^|_)(nr_conta|num_conta|conta_corrente|agencia)($|_)"),
    ("matricula", r"(^|_)(matricula|login|usuario_rede)($|_)"),
]
_COMENTARIO_FORTE = re.compile(
    r"\b(cpf|cnpj|e-?mail|telefone|celular|endere[cç]o|data de nascimento|dado pessoal|dados pessoais|lgpd|sens[ií]vel)\b",
    re.IGNORECASE,
)


# "nome_<coisa>" de entidades que não são pessoas: não é dado pessoal.
_NAO_PESSOA = re.compile(
    r"^(nome|nm)_(cidade|municipio|uf|estado|pais|regiao|bairro_ibge|produto|plano|oferta|loja|filial|canal|"
    r"segmento|categoria|campanha|tabela|coluna|arquivo|equipamento|modelo|fabricante|fornecedor|empresa|"
    r"operadora|servico|motivo|area|departamento|diretoria|onda|projeto)$"
)


_IDENTIFICADOR = re.compile(
    r"^(id|cd|cod|nr|num|seq|sk)_(cliente|domicilio|dom|assinante|contrato|conta|linha|usuario|pessoa|titular|"
    r"instalacao|terminal|acesso|residencia|consumidor|customer|user|account|contato|cartao)$"
    r"|^(msisdn|contrato|nr_linha|cliente_id|customer_id|user_id|account_id|id_dom|id_cliente|id_usuario|id_pessoa)$"
    r"|(_id_cliente|_cliente_id|_id_usuario|_id_dom)$"
)


def eh_identificador(nome: str) -> bool:
    """Coluna que identifica um indivíduo (cliente, domicílio, linha...), mesmo pseudônima."""
    return bool(_IDENTIFICADOR.search(nome.strip().lower()))


def classificar_coluna(nome: str, comentario: str = "", extras: list[str] | None = None) -> tuple[ClassePII, str]:
    n = nome.strip().lower()
    if _NAO_PESSOA.match(n) and not (comentario and _COMENTARIO_FORTE.search(comentario)):
        return "nao", ""
    for rotulo, padrao in _FORTES:
        if re.search(padrao, n):
            return "sim", rotulo
    for padrao in extras or []:
        if re.search(padrao, n, re.IGNORECASE):
            return "sim", "padrão do Admin"
    if comentario and _COMENTARIO_FORTE.search(comentario):
        return "sim", "comentário indica dado pessoal"
    for rotulo, padrao in _AMBIGUOS:
        if re.search(padrao, n):
            return "suspeita", rotulo
    return "nao", ""


class FichaColuna(BaseModel):
    nome: str
    tipo: str = ""
    comentario: str = ""
    anulavel: bool = True
    pii: ClassePII = "nao"
    motivo_pii: str = ""
    liberada: bool = False  # suspeita liberada por decisão do dev
    identificador: bool = False  # identifica um indivíduo (unidade para a regra de grupo mínimo)

    @property
    def bloqueada(self) -> bool:
        return self.pii == "sim" or (self.pii == "suspeita" and not self.liberada)

    @property
    def individual(self) -> bool:
        """Não pode sair na consulta agregada (pessoal ou identificador)."""
        return self.bloqueada or self.identificador


class FichaTabela(BaseModel):
    esquema: str
    nome: str
    comentario: str = ""
    linhas_estimadas: int | None = None
    colunas: list[FichaColuna] = Field(default_factory=list)
    fonte: str = "adw"  # adw | local | objectstorage
    acesso: Literal["ok", "sem_acesso"] = "ok"
    dono: str = ""
    uri: str = ""  # para fontes de arquivo (parquet)

    @property
    def nome_completo(self) -> str:
        return f"{self.esquema}.{self.nome}".upper()

    def coluna(self, nome: str) -> FichaColuna | None:
        alvo = nome.upper()
        return next((c for c in self.colunas if c.nome.upper() == alvo), None)

    def para_llm(self) -> dict:
        """Visão compacta para a IA: só metadados, comentários mascarados."""
        cols = []
        for c in self.colunas:
            item = [c.nome, c.tipo]
            if c.comentario:
                item.append(c.comentario[:160])
            if c.bloqueada:
                item.append("[pii: bloqueada na saída]")
            elif c.identificador:
                item.append("[identificador: só em COUNT ou junção]")
            cols.append(item)
        d: dict = {"tabela": self.nome_completo, "colunas": cols}
        if self.comentario:
            d["comentario"] = self.comentario[:300]
        if self.linhas_estimadas is not None:
            d["linhas"] = self.linhas_estimadas
        if self.acesso != "ok":
            d["acesso"] = "sem acesso (pedir ao dono)"
        return d


def montar_ficha(
    esquema: str,
    nome: str,
    colunas: list[tuple[str, str, str, bool]],
    *,
    comentario: str = "",
    linhas: int | None = None,
    fonte: str = "adw",
    acesso: str = "ok",
    extras_pii: list[str] | None = None,
    mascarar_comentarios: bool = True,
    uri: str = "",
) -> FichaTabela:
    """Cria a ficha a partir de (nome, tipo, comentário, anulável) de cada coluna."""
    fichas = []
    for col_nome, tipo, coment, anulavel in colunas:
        classe, motivo = classificar_coluna(col_nome, coment or "", extras_pii)
        fichas.append(
            FichaColuna(
                nome=col_nome.upper(),
                tipo=(tipo or "").upper(),
                comentario=mascarar_texto(coment or "") if mascarar_comentarios else (coment or ""),
                anulavel=bool(anulavel),
                pii=classe,
                motivo_pii=motivo,
                identificador=eh_identificador(col_nome) or classe == "sim" and motivo in ("cpf", "cnpj", "telefone", "email"),
            )
        )
    return FichaTabela(
        esquema=esquema.upper(),
        nome=nome.upper(),
        comentario=mascarar_texto(comentario or "") if mascarar_comentarios else (comentario or ""),
        linhas_estimadas=linhas,
        colunas=fichas,
        fonte=fonte,
        acesso=acesso,  # type: ignore[arg-type]
        uri=uri,
    )
