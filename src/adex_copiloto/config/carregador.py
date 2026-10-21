"""Carregador de configuração em camadas, com trava do Admin.

Ordem (a última vence), para chaves comuns:

    padrão do pacote < Admin (padrões da organização) < projeto < local < ambiente < código

Para chaves do Admin (retenção, Langfuse, política de dados, orçamento,
impressões digitais...):

    padrão do pacote < Admin assinado

Nenhuma camada abaixo do Admin consegue mudá-las. Algumas aceitam só ser
**apertadas** pelo dev (ex.: ``data_policy.min_group_size`` só sobe). Se o
arquivo do Admin não puder ser confirmado, valem os padrões do pacote, que são
os mais restritivos.
"""

from __future__ import annotations

import copy
import functools
import hashlib
import json
import math
import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

from adex_copiloto.config.modelos import Config
from adex_copiloto.erros import ConfigErro

# Prefixos (seção inteira ou chave exata) que só o Admin altera.
CHAVES_ADMIN_PREFIXOS: tuple[str, ...] = (
    "retencao",
    "langfuse",
    "data_policy",
    "orcamento",
    "fingerprint",
    "hub.modelo_impressao",
    "hub.modelo_reserva",
    "hub.base_url_impressao",
    "hub.base_url_salt",
    "hub.proxy",
    "hub.ca_bundle",
    "agente.entry_points_permitidos",
    "adw.custo_max_plano",
    "objectstorage.destinos_autorizados",
)
# Qualquer impressão digital ou salt, em qualquer seção, é do Admin.
SUFIXOS_ADMIN: tuple[str, ...] = (".impressao", ".salt")

_ORDEM_CAPTURA = {"metadados": 0, "mascarado": 1, "completo": 2}

# Chaves do Admin que o dev pode apenas tornar mais restritivas.
REGRAS_MONOTONICAS: dict[str, Callable[[Any, Any], Any]] = {
    "data_policy.min_group_size": max,
    "data_policy.max_linhas_resultado": min,
    "data_policy.max_linhas_amostra": min,
    "data_policy.teto_gb_lidos": min,
    "data_policy.max_celulas_para_llm": min,
    "orcamento.tokens_por_estudo": min,
    "orcamento.custo_usd_por_estudo": min,
    "orcamento.chamadas_por_estudo": min,
    "langfuse.captura": lambda a, b: a if _ORDEM_CAPTURA[a] <= _ORDEM_CAPTURA[b] else b,
    "adw.custo_max_plano": min,
}

ENV_PREFIXO = "ADEX__"


def eh_chave_admin(caminho: str) -> bool:
    caminho = caminho.lower()
    if caminho.endswith(SUFIXOS_ADMIN):
        return True
    return any(caminho == p or caminho.startswith(p + ".") for p in CHAVES_ADMIN_PREFIXOS)


@functools.lru_cache(maxsize=1)
def _chaves_admin_planas() -> frozenset[str]:
    planas = achatar(Config().model_dump())
    return frozenset(k for k in planas if eh_chave_admin(k)) | frozenset(CHAVES_ADMIN_PREFIXOS)


def toca_chave_admin(caminho: str) -> bool:
    """A chave é do Admin ou é uma seção que contém chave do Admin (trocá-la inteira passaria por cima)."""
    caminho = caminho.lower()
    if eh_chave_admin(caminho):
        return True
    return any(a.startswith(caminho + ".") for a in _chaves_admin_planas())


def achatar(d: Mapping[str, Any], prefixo: str = "") -> dict[str, Any]:
    """{'a': {'b': 1}} -> {'a.b': 1}. Listas são folhas."""
    saida: dict[str, Any] = {}
    for k, v in d.items():
        caminho = f"{prefixo}{k}"
        if isinstance(v, Mapping) and v:
            saida.update(achatar(v, caminho + "."))
        else:
            saida[caminho] = v
    return saida


def achatar_camada(d: Mapping[str, Any], prefixo: str = "") -> dict[str, Any]:
    """Achata uma camada de configuração até as folhas.

    Diferente de :func:`achatar`, desmonta objetos pydantic e dicionários vindos de JSON
    (``ADEX__HUB='{...}'``) e ignora seções vazias (``hub = {}``): trocar uma seção
    inteira de uma vez passaria por cima das chaves do Admin que estão dentro dela.
    """
    saida: dict[str, Any] = {}
    for k, v in d.items():
        caminho = f"{prefixo}{k}"
        if isinstance(v, BaseModel):
            v = v.model_dump()
        if isinstance(v, Mapping):
            if v:
                saida.update(achatar_camada(v, caminho + "."))
            continue  # seção vazia: nada a mudar
        saida[caminho] = v
    return saida


def _definir(d: dict[str, Any], caminho: str, valor: Any) -> None:
    partes = caminho.split(".")
    alvo = d
    for p in partes[:-1]:
        atual = alvo.get(p)
        if not isinstance(atual, dict):
            atual = {}
            alvo[p] = atual
        alvo = atual
    alvo[partes[-1]] = valor


def _obter(d: Mapping[str, Any], caminho: str, padrao: Any = None) -> Any:
    alvo: Any = d
    for p in caminho.split("."):
        if not isinstance(alvo, Mapping) or p not in alvo:
            return padrao
        alvo = alvo[p]
    return alvo


def ler_toml(caminho: Path) -> dict[str, Any]:
    try:
        with open(caminho, "rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigErro(
            f"Arquivo de configuração inválido: {caminho} ({e})",
            como_resolver="Corrija a sintaxe TOML no ponto indicado.",
        ) from e


def ler_env(env: Mapping[str, str]) -> dict[str, Any]:
    """ADEX__HUB__TIMEOUT_S=200 -> {'hub.timeout_s': '200'} (pydantic converte o tipo)."""
    saida: dict[str, Any] = {}
    for nome, bruto in env.items():
        if not nome.upper().startswith(ENV_PREFIXO):
            continue
        caminho = nome[len(ENV_PREFIXO):].lower().replace("__", ".")
        valor: Any = bruto
        texto = bruto.strip()
        if texto[:1] in ("[", "{"):
            try:
                valor = json.loads(texto)
            except json.JSONDecodeError as e:
                raise ConfigErro(f"Variável {nome} não é um JSON válido: {e}") from e
        saida[caminho] = valor
    return saida


@dataclass
class EstadoAdmin:
    """Situação do arquivo de Configurações do Admin."""

    situacao: str = "ausente"  # ausente | valido | invalido | sem_ancora
    motivo: str = "Nenhum arquivo do Admin configurado (admin.arquivo vazio)."
    origem: str = ""
    versao: int | None = None
    emitido_em: str | None = None
    emitido_por: str | None = None
    sha256_arquivo: str | None = None
    impressao_chave: str | None = None
    valores: dict[str, Any] = field(default_factory=dict)

    @property
    def valido(self) -> bool:
        return self.situacao == "valido"

    def resumo(self) -> str:
        if self.valido:
            return (
                f"Configurações do Admin v{self.versao} confirmadas (assinatura válida, emitidas "
                f"por {self.emitido_por} em {self.emitido_em})."
            )
        return f"Configurações do Admin não confirmadas: {self.motivo} Valem os padrões mais restritivos."


@dataclass
class ConfigEfetiva:
    config: Config
    origens: dict[str, str]
    admin: EstadoAdmin
    avisos: list[str] = field(default_factory=list)
    arquivos: list[str] = field(default_factory=list)

    def origem(self, caminho: str) -> str:
        if caminho in self.origens:
            return self.origens[caminho]
        prefixo = caminho + "."
        filhos = {v for k, v in self.origens.items() if k.startswith(prefixo)}
        if len(filhos) == 1:
            return filhos.pop()
        return "várias" if filhos else "padrão"

    def valor(self, caminho: str) -> Any:
        return _obter(self.config.model_dump(), caminho)

    def hash(self) -> str:
        bruto = json.dumps(self.config.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(bruto.encode("utf-8")).hexdigest()

    def linhas(self) -> list[dict[str, Any]]:
        planas = achatar(self.config.model_dump(mode="json"))
        linhas = []
        for caminho in sorted(planas):
            linhas.append(
                {
                    "chave": caminho,
                    "valor": planas[caminho],
                    "origem": self.origens.get(caminho, "padrão"),
                    "so_admin": eh_chave_admin(caminho),
                }
            )
        return linhas


def _arquivo_projeto(env: Mapping[str, str], arquivo: str | os.PathLike | None) -> Path | None:
    if arquivo:
        p = Path(arquivo).expanduser()
        if not p.exists():
            raise ConfigErro(f"Arquivo de configuração não encontrado: {p}")
        return p
    if env.get("ADEX_CONFIG"):
        p = Path(env["ADEX_CONFIG"]).expanduser()
        if not p.exists():
            raise ConfigErro(f"ADEX_CONFIG aponta para um arquivo que não existe: {p}")
        return p
    for candidato in (Path.cwd() / "adex.toml", Path("~/.adex/adex.toml").expanduser()):
        if candidato.exists():
            return candidato
    return None


def _arquivo_local(env: Mapping[str, str], local: str | os.PathLike | None) -> Path | None:
    if local:
        p = Path(local).expanduser()
        return p if p.exists() else None
    if env.get("ADEX_CONFIG_LOCAL"):
        p = Path(env["ADEX_CONFIG_LOCAL"]).expanduser()
        return p if p.exists() else None
    p = Path.cwd() / "config" / "local.toml"
    return p if p.exists() else None


def _validar(dados: dict[str, Any], origens: dict[str, str]) -> Config:
    try:
        return Config.model_validate(dados)
    except ValidationError as e:
        linhas = []
        for err in e.errors():
            caminho = ".".join(str(x) for x in err["loc"])
            linhas.append(f"- {caminho}: {err['msg']} (origem: {origens.get(caminho, 'padrão')})")
        raise ConfigErro(
            "Configuração inválida:\n" + "\n".join(linhas),
            como_resolver="Corrija os valores listados no arquivo ou variável indicada.",
        ) from e


CarregadorAdmin = Callable[[Config], EstadoAdmin]


def carregar(
    *,
    arquivo: str | os.PathLike | None = None,
    local: str | os.PathLike | None = None,
    env: Mapping[str, str] | None = None,
    sobrescritas: Mapping[str, Any] | None = None,
    carregador_admin: CarregadorAdmin | None = None,
) -> ConfigEfetiva:
    """Monta a configuração efetiva, registrando a origem de cada valor."""
    env = os.environ if env is None else env
    padrao = Config().model_dump()
    dados = copy.deepcopy(padrao)
    origens: dict[str, str] = {}
    avisos: list[str] = []
    arquivos: list[str] = []
    apertos: list[tuple[str, Any, str]] = []

    camadas: list[tuple[str, dict[str, Any]]] = []
    p_proj = _arquivo_projeto(env, arquivo)
    if p_proj:
        camadas.append((f"projeto:{p_proj}", achatar_camada(ler_toml(p_proj))))
        arquivos.append(str(p_proj))
    p_local = _arquivo_local(env, local)
    if p_local:
        camadas.append((f"local:{p_local}", achatar_camada(ler_toml(p_local))))
        arquivos.append(str(p_local))
    planas_env = achatar_camada(ler_env(env))
    if planas_env:
        camadas.append(("ambiente", planas_env))
    if sobrescritas:
        camadas.append(("código", achatar_camada(dict(sobrescritas))))

    def aplicar_camadas(alvo: dict[str, Any], destino_origens: dict[str, str], registrar: bool) -> None:
        for nome_camada, planas in camadas:
            for caminho, valor in planas.items():
                if toca_chave_admin(caminho):
                    if caminho.lower() in REGRAS_MONOTONICAS:
                        if registrar:
                            apertos.append((caminho, valor, nome_camada))
                    elif registrar:
                        avisos.append(
                            f"{caminho} definido em '{nome_camada}' foi ignorado: só o Admin altera esse valor."
                        )
                    continue
                _definir(alvo, caminho, valor)
                destino_origens[caminho] = nome_camada

    # 1) Sem o Admin, para descobrir onde está o arquivo dele e como autenticar na OCI.
    preliminar = copy.deepcopy(padrao)
    origens_preliminares: dict[str, str] = {}
    aplicar_camadas(preliminar, origens_preliminares, registrar=False)
    cfg_preliminar = _validar(preliminar, origens_preliminares)

    # 2) Admin: valida assinatura e âncora; se falhar, fica tudo no padrão restritivo.
    if carregador_admin is None:
        from adex_copiloto.config.configuracoes import carregar_admin as carregador_admin
    estado = carregador_admin(cfg_preliminar)
    if estado.valido:
        for caminho, valor in estado.valores.items():
            _definir(dados, caminho, valor)
            origens[caminho] = f"admin v{estado.versao}"
    else:
        avisos.append(estado.resumo())

    # 3) Camadas comuns por cima (chaves do Admin ficam de fora).
    aplicar_camadas(dados, origens, registrar=True)

    # 4) Apertos permitidos ao dev (nunca afrouxam).
    for caminho, valor, nome_camada in apertos:
        caminho = caminho.lower()
        atual = _obter(dados, caminho)
        try:
            regra = REGRAS_MONOTONICAS[caminho]
            convertido = type(atual)(valor) if not isinstance(atual, str) else valor
            # nan passaria pela regra (min(nan, x) devolve nan) e desligaria o teto
            if isinstance(convertido, float) and not math.isfinite(convertido):
                raise ValueError("valor não finito")
            novo = regra(convertido, atual)
        except (TypeError, ValueError, KeyError):
            avisos.append(f"{caminho} em '{nome_camada}' tem valor inválido ({valor!r}); ignorado.")
            continue
        if novo != atual:
            _definir(dados, caminho, novo)
            origens[caminho] = f"{nome_camada} (apertado)"
        elif valor != atual:
            avisos.append(
                f"{caminho}={valor!r} em '{nome_camada}' afrouxaria a regra do Admin ({atual!r}); ignorado."
            )

    cfg = _validar(dados, origens)
    return ConfigEfetiva(config=cfg, origens=origens, admin=estado, avisos=avisos, arquivos=arquivos)


def diretorio_estado(cfg: Config) -> Path:
    p = Path(os.path.expandvars(cfg.geral.diretorio_estado)).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    return p


def resolver_caminho(cfg: Config, modelo: str) -> Path:
    texto = modelo.replace("{estado}", str(diretorio_estado(cfg)))
    return Path(os.path.expandvars(texto)).expanduser()
