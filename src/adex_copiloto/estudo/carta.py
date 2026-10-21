"""Carta do estudo: o contrato do caso, com 10 campos e a origem de cada um.

- A IA propõe; o Python confere cada citação "você disse" contra o texto do dev
  (cópia literal, depois de normalizar espaços e caixa). Citação que não existe
  rebaixa o campo para "sugerido" e gera um aviso.
- Nenhuma etapa de dados roda antes da aprovação da carta.
- Mudar a carta depois de aprovada gera a v2 e reabre só as etapas que
  dependem do campo alterado (ver ``DEPENDENCIAS``).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from adex_copiloto.estudo.evidencia import agora_iso

CAMPOS: list[tuple[str, str]] = [
    ("pergunta_decisao", "Pergunta de decisão"),
    ("objeto", "Objeto do estudo"),
    ("unidade_analise", "Unidade de análise"),
    ("populacao", "População"),
    ("desfecho", "Desfecho"),
    ("horizonte_comparacao", "Horizonte e comparação"),
    ("restricoes", "Restrições"),
    ("criterios_pronto", "Critérios de pronto"),
    ("fora_escopo", "Fora do escopo"),
    ("riscos_conhecidos", "Riscos conhecidos"),
]
NOMES_CAMPOS = [c for c, _ in CAMPOS]
ROTULOS = dict(CAMPOS)

TipoPergunta = Literal[
    "efeito", "previsao", "classificacao", "regressao", "descricao", "diagnostico", "segmentacao", "anomalia"
]
Origem = Literal["voce_disse", "voce_escolheu", "sugerido", "padrao"]
Certeza = Literal["alta", "media", "baixa", "nao_sei"]

# Etapas que precisam ser refeitas quando um campo muda.
DEPENDENCIAS: dict[str, set[str]] = {
    "descobrir": {"objeto", "unidade_analise", "populacao", "desfecho", "horizonte_comparacao"},
    "universo": {"unidade_analise", "populacao", "horizonte_comparacao", "restricoes", "desfecho"},
    "plano": set(NOMES_CAMPOS),
    "etapas": set(NOMES_CAMPOS) - {"fora_escopo"},
    "critico": set(NOMES_CAMPOS),
}


# --- Esquemas que o Qwen preenche ---------------------------------------------------------


class ItemEntendimento(BaseModel):
    item: str = Field(max_length=80)
    o_que_entendi: str = Field(max_length=400)
    certeza: Certeza


class PerguntaEnquadramento(BaseModel):
    pergunta: str = Field(max_length=400)
    por_que_pergunto: str = Field(max_length=400)
    opcoes: list[str] = Field(default_factory=list, max_length=4)


class Enquadramento(BaseModel):
    """Saída do passo 'enquadrar' (nenhum dado lido)."""

    resumo: str = Field(max_length=600)
    tipo_pergunta: TipoPergunta
    duvida_tipo: str = Field(default="", max_length=300)
    entendimento: list[ItemEntendimento] = Field(min_length=1, max_length=10)
    perguntas: list[PerguntaEnquadramento] = Field(default_factory=list, max_length=3)


class CampoProposto(BaseModel):
    campo: Literal[
        "pergunta_decisao", "objeto", "unidade_analise", "populacao", "desfecho",
        "horizonte_comparacao", "restricoes", "criterios_pronto", "fora_escopo", "riscos_conhecidos",
    ]
    valor: str = Field(max_length=1200)
    origem: Origem
    citacoes: list[str] = Field(default_factory=list, max_length=6)


class FraseMetodo(BaseModel):
    trecho: str = Field(max_length=300)
    explicacao: str = Field(max_length=800)
    risco_id: str = Field(default="", max_length=10)


class HipoteseProposta(BaseModel):
    # Sem 'pattern' no esquema: nem todo motor de decodificação guiada aceita regex.
    id: str = Field(max_length=10)
    texto: str = Field(max_length=500)

    @field_validator("id")
    @classmethod
    def _formato(cls, v: str) -> str:
        m = re.fullmatch(r"\s*H\s*-?\s*(\d{1,3})\s*", v or "")
        if not m:
            raise ValueError("id de hipótese deve ser H-01, H-02...")
        return f"H-{int(m.group(1)):02d}"


class PropostaCarta(BaseModel):
    """Saída do passo 'carta'."""

    campos: list[CampoProposto] = Field(min_length=10, max_length=10)
    frases_que_mudam_metodo: list[FraseMetodo] = Field(default_factory=list, max_length=4)
    hipoteses: list[HipoteseProposta] = Field(default_factory=list, max_length=8)
    lacunas: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("campos")
    @classmethod
    def _todos_os_campos(cls, v: list[CampoProposto]) -> list[CampoProposto]:
        nomes = [c.campo for c in v]
        faltam = [n for n in NOMES_CAMPOS if n not in nomes]
        repetidos = {n for n in nomes if nomes.count(n) > 1}
        if faltam or repetidos:
            raise ValueError(f"a carta precisa dos 10 campos, cada um uma vez (faltam {faltam}, repetidos {sorted(repetidos)})")
        return v


# --- Carta no estudo -------------------------------------------------------------------------


class CampoCarta(BaseModel):
    valor: str
    origem: Origem = "sugerido"
    citacoes: list[str] = Field(default_factory=list)
    confirmado: bool = False
    aviso: str = ""


class VersaoCarta(BaseModel):
    versao: int
    em: str
    campos_alterados: list[str]
    motivo: str = ""


class Carta(BaseModel):
    versao: int = 1
    tipo_pergunta: TipoPergunta = "descricao"
    campos: dict[str, CampoCarta]
    status: Literal["rascunho", "aprovada"] = "rascunho"
    aprovada_por: str = ""
    aprovada_em: str = ""
    historico: list[VersaoCarta] = Field(default_factory=list)
    frases_que_mudam_metodo: list[FraseMetodo] = Field(default_factory=list)

    def valor(self, campo: str) -> str:
        return self.campos[campo].valor if campo in self.campos else ""

    @property
    def aprovada(self) -> bool:
        return self.status == "aprovada"

    def preenchidos(self) -> int:
        return sum(1 for c in NOMES_CAMPOS if self.campos.get(c) and self.campos[c].valor.strip())

    def editar(self, campo: str, valor: str, *, origem: Origem = "voce_escolheu", motivo: str = "") -> list[str]:
        """Muda um campo. Se a carta já estava aprovada, abre uma versão nova (rascunho)."""
        if campo not in ROTULOS:
            raise KeyError(f"campo desconhecido: {campo}. Campos: {', '.join(NOMES_CAMPOS)}")
        atual = self.campos.get(campo)
        if atual is not None and atual.valor == valor:
            return []
        self.campos[campo] = CampoCarta(valor=valor, origem=origem, confirmado=True)
        if self.aprovada:
            self.versao += 1
            self.status = "rascunho"
            self.aprovada_por = self.aprovada_em = ""
            self.historico.append(VersaoCarta(versao=self.versao, em=agora_iso(), campos_alterados=[campo], motivo=motivo))
        elif self.historico and self.historico[-1].versao == self.versao:
            if campo not in self.historico[-1].campos_alterados:
                self.historico[-1].campos_alterados.append(campo)
        return [campo]

    def aprovar(self, por: str) -> None:
        vazios = [ROTULOS[c] for c in NOMES_CAMPOS if not self.valor(c).strip()]
        if vazios:
            raise ValueError("Preencha antes de aprovar: " + ", ".join(vazios))
        self.status, self.aprovada_por, self.aprovada_em = "aprovada", por, agora_iso()
        for c in self.campos.values():
            c.confirmado = True

    def campos_alterados_desde(self, versao: int) -> set[str]:
        alterados: set[str] = set()
        for h in self.historico:
            if h.versao > versao:
                alterados.update(h.campos_alterados)
        return alterados

    def para_llm(self) -> dict:
        return {
            "versao": self.versao,
            "tipo_pergunta": self.tipo_pergunta,
            "campos": {c: self.valor(c) for c in NOMES_CAMPOS},
        }


def etapas_afetadas(campos: set[str]) -> list[str]:
    return [etapa for etapa, deps in DEPENDENCIAS.items() if deps & campos]


# --- Conferência das citações ------------------------------------------------------------------


def _normalizar(texto: str) -> str:
    t = unicodedata.normalize("NFC", texto or "").casefold()
    t = re.sub(r"[‘’“”\"'`´]", "", t)
    return re.sub(r"\s+", " ", t).strip(" .,;:")


def citacao_existe(citacao: str, textos_dev: list[str]) -> bool:
    alvo = _normalizar(citacao)
    if len(alvo) < 3:
        return False
    return any(alvo in _normalizar(t) for t in textos_dev)


def montar_carta(proposta: PropostaCarta, textos_dev: list[str], tipo_pergunta: TipoPergunta) -> tuple[Carta, list[str]]:
    """Converte a proposta da IA em carta, conferindo as citações. Devolve (carta, avisos)."""
    avisos: list[str] = []
    campos: dict[str, CampoCarta] = {}
    for cp in proposta.campos:
        origem = cp.origem
        aviso = ""
        validas = [c for c in cp.citacoes if citacao_existe(c, textos_dev)]
        invalidas = [c for c in cp.citacoes if c not in validas]
        if origem == "voce_disse":
            if not validas:
                origem = "sugerido"
                aviso = "a IA disse que veio do seu texto, mas não achei o trecho: virou sugestão"
                avisos.append(f"{ROTULOS[cp.campo]}: {aviso}.")
            elif invalidas:
                aviso = f"{len(invalidas)} citação(ões) não encontrada(s) no seu texto foram descartadas"
                avisos.append(f"{ROTULOS[cp.campo]}: {aviso}.")
        campos[cp.campo] = CampoCarta(valor=cp.valor.strip(), origem=origem, citacoes=validas, aviso=aviso)
    frases = [f for f in proposta.frases_que_mudam_metodo if citacao_existe(f.trecho, textos_dev)]
    if len(frases) < len(proposta.frases_que_mudam_metodo):
        avisos.append("uma 'frase que muda o método' não estava no seu texto e foi descartada.")
    carta = Carta(tipo_pergunta=tipo_pergunta, campos=campos, frases_que_mudam_metodo=frases)
    return carta, avisos


def carta_em_branco(tipo: TipoPergunta = "descricao") -> Carta:
    return Carta(tipo_pergunta=tipo, campos={c: CampoCarta(valor="", origem="sugerido") for c in NOMES_CAMPOS})
