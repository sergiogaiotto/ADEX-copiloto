"""Modelos de configuração do ADEX Copiloto.

Os padrões aqui são de **pior caso**: valores seguros até a Fase 0 (homologação do
GPU Hub) e a governança confirmarem outros. Cada estudo grava no dossiê a
configuração efetiva que usou.

Seções marcadas como "Admin" só podem ser alteradas pelo módulo de
Configurações (arquivo assinado). Ver ``config.carregador.CHAVES_ADMIN``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _Base(BaseModel):
    """Base estrita: chave desconhecida é erro (pega erro de digitação cedo)."""

    # nan/inf nunca são valores válidos: "nan" passaria por qualquer comparação de teto.
    model_config = ConfigDict(extra="forbid", validate_assignment=True, allow_inf_nan=False)


class RefSegredo(_Base):
    """Onde um segredo mora e como conferir que é o valor homologado.

    O valor nunca fica na configuração. Ela guarda só ``sha256(salt || valor)``
    (``impressao``) e o ``salt``, ambos definidos pelo Admin.
    """

    fonte: Literal["vault", "dpapi", "env", "prompt"] = "env"
    env: str = ""
    arquivo_dpapi: str = ""
    vault_secret_ocid: str = ""
    impressao: str = ""
    salt: str = ""

    @field_validator("impressao")
    @classmethod
    def _hex(cls, v: str) -> str:
        v = v.strip().lower()
        if v and (len(v) != 64 or any(c not in "0123456789abcdef" for c in v)):
            raise ValueError("impressao deve ser um sha256 em hexadecimal (64 caracteres)")
        return v


class Geral(_Base):
    ambiente: Literal["local", "oci"] = "local"
    diretorio_estado: str = "~/.adex"
    fuso: str = "America/Sao_Paulo"
    explicar_erros: bool = True
    destinos_locais_autorizados: list[str] = Field(default_factory=list)


class Hub(_Base):
    """GPU Hub (LiteLLM + vLLM). Só os modelos do hub são usados."""

    base_url: str = "https://hub-gpus.usto.re/v1"
    base_url_impressao: str = ""  # Admin: sha256(salt || base_url)
    base_url_salt: str = ""  # Admin
    modelo: str = "qwen-latest"
    modelo_impressao: str = ""  # Admin: impressão do que o hub diz sobre o modelo
    modelo_reserva: str = ""  # Admin: vazio = sem reserva
    chave: RefSegredo = Field(
        default_factory=lambda: RefSegredo(
            fonte="dpapi",
            env="ADEX_HUB_API_KEY",
            arquivo_dpapi="%LOCALAPPDATA%/adex-copiloto/secrets/gpu-hub.dpapi",
        )
    )
    contexto_max: int = 196_608
    contexto_util: int = 131_072
    saida_max: int = 8_192
    campo_saida: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"
    esforco_em: Literal["chat_template_kwargs", "topo", "ambos"] = "chat_template_kwargs"
    saida_estruturada: Literal["json_schema", "json_object", "prompt"] = "json_schema"
    visao: bool = False
    timeout_s: float = 180.0
    timeout_conexao_s: float = 10.0
    tentativas: int = Field(default=3, ge=1, le=6)
    espera_base_s: float = 2.0
    espera_max_s: float = 30.0
    paralelismo: int = Field(default=1, ge=1, le=16)
    cabecalho_custo: str = "x-litellm-response-cost"
    reenviar_raciocinio: bool = False
    verificar_modelo: bool = True
    ca_bundle: str = ""
    proxy: str = ""


class PerfilConfig(_Base):
    """Um jeito de chamar o Qwen. Corpo de requisição completo, sem merge raso."""

    raciocinio: bool
    # "high" não existe no Qwen3.8 (o hub devolve 400): o tipo já impede esse valor.
    esforco: Literal["low", "medium", "xhigh"] | None = None
    orcamento_raciocinio: int | None = None
    temperature: float
    top_p: float
    top_k: int = 20
    min_p: float = 0.0
    presence_penalty: float = 0.0
    max_tokens: int = 4_096
    timeout_s: float | None = None
    descricao: str = ""


def _perfis_padrao() -> dict[str, PerfilConfig]:
    sem = dict(temperature=0.7, top_p=0.8, top_k=20, min_p=0.0)
    com = dict(temperature=1.0, top_p=0.95, top_k=20, min_p=0.0, presence_penalty=0.0)
    return {
        "rapido": PerfilConfig(
            raciocinio=False, presence_penalty=0.0, max_tokens=2_048, **sem,
            descricao="Respostas curtas e JSON simples (classificar, extrair, resumir).",
        ),
        "acao": PerfilConfig(
            raciocinio=True, esforco="low", orcamento_raciocinio=2_048, max_tokens=8_192, **com,
            descricao="Agentes que chamam ferramentas (descoberta, plano).",
        ),
        "pensar": PerfilConfig(
            raciocinio=True, esforco="medium", orcamento_raciocinio=6_144, max_tokens=8_192, **com,
            descricao="Enquadrar, montar carta e plano: pede raciocínio de verdade.",
        ),
        "critico": PerfilConfig(
            raciocinio=True, esforco="medium", orcamento_raciocinio=6_144, max_tokens=8_192, **com,
            descricao="Crítico (chamada separada) e avaliador (LLM como juiz).",
        ),
        "visao": PerfilConfig(
            raciocinio=False, presence_penalty=0.0, max_tokens=1_024, **sem,
            descricao="Ler gráficos em PNG (só se hub.visao=true depois da Fase 0).",
        ),
        "narrar": PerfilConfig(
            raciocinio=False, presence_penalty=1.5, max_tokens=3_072, **sem,
            descricao="Texto em volta dos números marcados ⟦M-xx⟧.",
        ),
    }


class AvaliacaoLangfuse(_Base):
    """LLM como juiz com o próprio Qwen (Admin)."""

    habilitada: bool = True
    amostragem: float = Field(default=0.2, ge=0.0, le=1.0)
    perfil: str = "critico"
    criterios: list[str] = Field(
        default_factory=lambda: ["fidelidade_numeros", "clareza", "aderencia_carta"]
    )


class Langfuse(_Base):
    """Máquina do Langfuse (fora deste ambiente). Seção inteira só do Admin."""

    habilitado: bool = False
    server_version: str = "4.49"
    sdk_major: Literal["auto", "4", "5"] = "auto"
    data_location: str = "Brasil"
    project: str = "adex-copiloto"
    environment: str = "exploracao"
    rota: Literal["ok", "pendente", "desconhecida"] = "pendente"
    on_unavailable: Literal["fila", "desligado"] = "fila"
    captura: Literal["metadados", "mascarado", "completo"] = "mascarado"
    amostragem: float = Field(default=1.0, ge=0.0, le=1.0)
    timeout_s: int = Field(default=5, ge=1, le=60)
    flush_interval_s: float = 5.0
    base_url: RefSegredo = Field(
        default_factory=lambda: RefSegredo(fonte="env", env="ADEX_LANGFUSE_BASE_URL")
    )
    public_key: RefSegredo = Field(
        default_factory=lambda: RefSegredo(fonte="env", env="ADEX_LANGFUSE_PUBLIC_KEY")
    )
    secret_key: RefSegredo = Field(
        default_factory=lambda: RefSegredo(fonte="env", env="ADEX_LANGFUSE_SECRET_KEY")
    )
    avaliacao: AvaliacaoLangfuse = Field(default_factory=AvaliacaoLangfuse)


class Oci(_Base):
    autenticacao: Literal["resource_principal", "api_key", "instance_principal"] = "resource_principal"
    arquivo_config: str = "~/.oci/config"
    perfil_config: str = "DEFAULT"
    regiao: str = ""
    tenancy_ocid: str = ""
    compartimento_ocid: str = ""


class DataScience(_Base):
    projeto_ocid: str = ""
    conda_slug: str = ""
    conda_uri: str = ""
    limite_celula_min: int = 30
    job_shape: str = ""
    job_log_group_ocid: str = ""


class Adw(_Base):
    habilitado: bool = False
    dsn: str = ""
    usuario: str = ""
    senha: RefSegredo = Field(default_factory=lambda: RefSegredo(fonte="vault", env="ADEX_ADW_SENHA"))
    wallet_dir: str = ""
    wallet_senha: RefSegredo = Field(
        default_factory=lambda: RefSegredo(fonte="vault", env="ADEX_ADW_WALLET_SENHA")
    )
    protocolo: Literal["mtls", "tls"] = "mtls"
    esquemas: list[str] = Field(default_factory=list)
    tabelas_permitidas: list[str] = Field(default_factory=list)
    arraysize: int = 1_000
    call_timeout_ms: int = 120_000
    custo_max_plano: float = 1e7
    sessao_somente_leitura_26ai: bool = False


class ObjectStorage(_Base):
    namespace: str = ""
    bucket_estudos: str = ""
    prefixo_estudos: str = "adex/estudos/"
    destinos_autorizados: list[str] = Field(default_factory=list)
    fontes_parquet: list[str] = Field(default_factory=list)


class Vault(_Base):
    cofre_ocid: str = ""
    chave_mestra_ocid: str = ""


class CatalogoModelos(_Base):
    projeto_ocid: str = ""
    compartimento_ocid: str = ""
    version_set: str = ""


class Agente(_Base):
    deep_nodes: list[Literal["descobrir", "plano"]] = Field(default_factory=lambda: ["descobrir", "plano"])
    recursion_limit: int = Field(default=60, ge=10, le=400)
    max_chamadas_modelo: int = Field(default=24, ge=1, le=200)
    max_chamadas_ferramenta: int = Field(default=48, ge=1, le=400)
    max_subagentes: int = Field(default=4, ge=0, le=10)
    entry_points_permitidos: list[str] = Field(default_factory=list)  # Admin


class Checkpoint(_Base):
    caminho: str = "{estado}/estudos/{estudo}/checkpoints.sqlite"  # um arquivo por estudo (retenção por estudo)
    chave: RefSegredo = Field(
        default_factory=lambda: RefSegredo(
            fonte="dpapi",
            env="ADEX_CHECKPOINT_KEY",
            arquivo_dpapi="%LOCALAPPDATA%/adex-copiloto/secrets/checkpoint.dpapi",
        )
    )
    gerar_chave_se_ausente: bool = True
    snapshot_storage: bool = True


class DataPolicy(_Base):
    """Política de dados (Admin). ``min_group_size`` só pode subir."""

    min_group_size: int = Field(default=50, ge=5)
    max_linhas_resultado: int = 100_000
    max_linhas_amostra: int = 2_000_000
    teto_gb_lidos: float = 50.0
    max_celulas_para_llm: int = 2_000
    padroes_pii_extra: list[str] = Field(default_factory=list)
    bloquear_comentarios_pii: bool = True
    funcoes_sql_extras: list[str] = Field(default_factory=list)  # nomes extras aceitos pelo porteiro


class Orcamento(_Base):
    """Limites de uso da IA por estudo (Admin). O dev só pode apertar."""

    tokens_por_estudo: int = 3_000_000
    custo_usd_por_estudo: float = 25.0
    chamadas_por_estudo: int = 600
    alerta_em: float = Field(default=0.8, ge=0.1, le=1.0)


class Fingerprint(_Base):
    on_change: Literal["bloquear", "avisar"] = "bloquear"


class Retencao(_Base):
    """Retenção por armazenamento (Admin). Valores sugeridos, pendentes de aval da governança."""

    langfuse_dias: int = Field(default=90, ge=1)
    checkpoint_dias_pos_handoff: int = Field(default=30, ge=1)
    snapshot_dias: int = Field(default=180, ge=1)
    fila_local_dias: int = Field(default=7, ge=1)
    dossie_local_dias: int = Field(default=365, ge=1)
    handoff_dias: int = Field(default=0, ge=0)  # 0 = definido pela equipe de produção


class Admin(_Base):
    arquivo: str = ""  # caminho local ou oci://bucket@namespace/prefixo/configuracoes.toml
    grupo_iam: str = "adex-admins"


class Descoberta(_Base):
    max_tabelas: int = 3_000
    tokens_por_lote: int = 60_000
    max_lotes: int = 6
    contagens_ligacao: bool = True


class Config(_Base):
    geral: Geral = Field(default_factory=Geral)
    hub: Hub = Field(default_factory=Hub)
    perfis: dict[str, PerfilConfig] = Field(default_factory=_perfis_padrao)
    langfuse: Langfuse = Field(default_factory=Langfuse)
    oci: Oci = Field(default_factory=Oci)
    datascience: DataScience = Field(default_factory=DataScience)
    adw: Adw = Field(default_factory=Adw)
    objectstorage: ObjectStorage = Field(default_factory=ObjectStorage)
    vault: Vault = Field(default_factory=Vault)
    catalogo_modelos: CatalogoModelos = Field(default_factory=CatalogoModelos)
    agente: Agente = Field(default_factory=Agente)
    checkpoint: Checkpoint = Field(default_factory=Checkpoint)
    data_policy: DataPolicy = Field(default_factory=DataPolicy)
    orcamento: Orcamento = Field(default_factory=Orcamento)
    fingerprint: Fingerprint = Field(default_factory=Fingerprint)
    retencao: Retencao = Field(default_factory=Retencao)
    admin: Admin = Field(default_factory=Admin)
    descoberta: Descoberta = Field(default_factory=Descoberta)

    @field_validator("perfis")
    @classmethod
    def _perfis_obrigatorios(cls, v: dict[str, PerfilConfig]) -> dict[str, PerfilConfig]:
        base = _perfis_padrao()
        faltam = [p for p in base if p not in v]
        for p in faltam:
            v[p] = base[p]
        return v
