"""Passaporte de Amostragem (spec, seção 7).

Registra população, fonte, janela, método, seed, critérios, tamanho, estratos,
pesos, cobertura, perdas, comparação com o universo, riscos de viés,
limitações e aprovação. Regra fixa: **não se declara ausência de viés sem
evidência** — o passaporte recusa textos como "sem viés" quando não há
comparação com o universo que os sustente.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from adex_copiloto.estudo.evidencia import agora_iso

Metodo = Literal[
    "censo", "agregado_na_fonte", "aleatoria_simples", "sistematica", "estratificada", "conglomerados",
    "geografica", "temporal", "coorte", "conveniencia",
]

_SEM_VIES = re.compile(r"\b(sem|nenhum|não há|nao ha|ausência de|ausencia de|livre de)\s+vi[eé]s", re.IGNORECASE)


class ComparacaoUniverso(BaseModel):
    variavel: str
    psi: float | None = None  # índice de estabilidade populacional
    smd: float | None = None  # diferença média padronizada
    universo: dict[str, Any] = Field(default_factory=dict)
    amostra: dict[str, Any] = Field(default_factory=dict)
    situacao: Literal["ok", "atencao", "problema", "nao_avaliado"] = "nao_avaliado"


class Passaporte(BaseModel):
    id: str  # PA-01
    versao: int = 1
    populacao: str
    fonte: list[str]
    janela: str
    metodo: Metodo
    seed: int | None = None
    criterios: list[str] = Field(default_factory=list)
    tamanho: int | None = None
    tamanho_universo: int | None = None
    estratos: list[str] = Field(default_factory=list)
    pesos: str = "nenhum"
    cobertura: float | None = None  # fração do universo coberta
    perdas: list[str] = Field(default_factory=list)
    comparacao_universo: list[ComparacaoUniverso] = Field(default_factory=list)
    riscos_vies: list[str] = Field(default_factory=list)
    limitacoes: list[str] = Field(default_factory=list)
    justificativa: str = ""
    separacao: str = ""  # temporal/por grupo, quando o problema exige
    status: Literal["rascunho", "aprovado"] = "rascunho"
    aprovado_por: str = ""
    aprovado_em: str = ""
    sql_sha256: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _honestidade_sobre_vies(self) -> "Passaporte":
        avaliado = any(c.situacao != "nao_avaliado" for c in self.comparacao_universo)
        for texto in self.riscos_vies + self.limitacoes + [self.justificativa]:
            if _SEM_VIES.search(texto or "") and not avaliado:
                raise ValueError(
                    "O passaporte não pode declarar ausência de viés sem comparação com o universo."
                )
        if not self.riscos_vies:
            if self.metodo in ("censo", "agregado_na_fonte"):
                self.riscos_vies = ["Cobre o universo definido na carta; vieses da própria definição da população continuam possíveis."]
            else:
                self.riscos_vies = ["Viés não avaliado ainda: nenhuma ausência de viés é declarada."]
        if self.metodo == "conveniencia" and not any("conveni" in l.lower() for l in self.limitacoes):
            self.limitacoes.append("Amostra de conveniência: os resultados não se generalizam para o universo.")
        return self

    @property
    def aprovado(self) -> bool:
        return self.status == "aprovado"

    def aprovar(self, por: str) -> None:
        self.status, self.aprovado_por, self.aprovado_em = "aprovado", por, agora_iso()

    def para_handoff(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
