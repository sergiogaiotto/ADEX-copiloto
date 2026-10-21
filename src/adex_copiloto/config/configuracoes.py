"""Configurações do Admin: retenção e governança, só alteráveis pelo Admin.

Como a garantia funciona (e onde ela termina):

1. **Fonte única.** O arquivo ``configuracoes.toml`` fica num prefixo do Object
   Storage em que só o grupo Admin escreve (política do IAM). Os notebooks só leem.
2. **Assinatura.** O arquivo é assinado com Ed25519 pela chave privada do Admin.
   A assinatura fica em ``configuracoes.toml.sig``. A chave pública vai dentro do
   arquivo e sua impressão (sha256) precisa bater com a **âncora** gravada no
   pacote (``recursos/ancora_admin.txt``) ou, se o pacote não tiver âncora, com a
   variável ``ADEX_ADMIN_ANCORA`` — só fora da OCI (uso local/desenvolvimento).
   Na OCI, a âncora do ambiente é ignorada: sem âncora no pacote, nada é confirmado.
3. **Local fixo e sem volta atrás.** Se o pacote trouxer ``recursos/admin_arquivo.txt``,
   esse é o local do arquivo (o ``admin.arquivo`` do dev é ignorado). ``validade_ate``
   é obrigatória, e uma versão mais antiga que a maior já aplicada neste ambiente é
   recusada (quem aponta para um arquivo antigo, ainda assinado, não volta no tempo).
4. **Recuo restritivo.** Sem arquivo, sem âncora, assinatura inválida, vencido
   ou com valor fora do esquema: valem os padrões do pacote, os mais restritivos.
   Na OCI, o copiloto nem abre sem Configurações do Admin confirmadas.
5. **Aplicação onde o dado mora.** A retenção é aplicada por cada armazenamento:
   rotina na máquina do Langfuse, lifecycle do bucket e limpeza local da lib.

Limite honesto: dentro do kernel, quem tem acesso pode alterar a biblioteca em
memória. A garantia real vem da permissão de escrita no prefixo do Admin, da
assinatura e da aplicação da retenção do lado de cada armazenamento. O dossiê de
cada estudo registra a situação e o sha256 das Configurações usadas.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import os
import tomllib
from importlib import resources
from pathlib import Path
from typing import Any

import tomli_w
from pydantic import ValidationError

from adex_copiloto.config.carregador import (
    EstadoAdmin,
    _definir,
    achatar,
    achatar_camada,
    eh_chave_admin,
)
from adex_copiloto.config.modelos import Config
from adex_copiloto.erros import ConfiguracaoAdminErro, PermissaoAdminErro

SUFIXO_ASSINATURA = ".sig"
ENV_ANCORA = "ADEX_ADMIN_ANCORA"
ENV_CHAVE_PRIVADA = "ADEX_ADMIN_CHAVE_PRIVADA"
VALIDADE_PADRAO_DIAS = 365
# Variáveis que o OCI Data Science define em sessões de notebook e jobs.
ENV_OCI = ("NB_SESSION_OCID", "JOB_RUN_OCID", "OCI_RESOURCE_PRINCIPAL_VERSION")


def em_oci(cfg: Config, env: dict[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    return cfg.geral.ambiente == "oci" or any(env.get(v) for v in ENV_OCI)


# --- Âncora de confiança ----------------------------------------------------------


def _recurso(nome: str) -> str:
    """Primeira linha útil de um arquivo de ``recursos/`` (vazio se não houver)."""
    try:
        texto = resources.files("adex_copiloto.recursos").joinpath(nome).read_text("utf-8")
    except (FileNotFoundError, ModuleNotFoundError):
        return ""
    for linha in texto.splitlines():
        linha = linha.strip()
        if linha and not linha.startswith("#"):
            return linha
    return ""


def ancora_do_pacote() -> str:
    return _recurso("ancora_admin.txt").lower()


def arquivo_do_pacote() -> str:
    """Local do arquivo do Admin fixado na build oficial (vazio = usa ``admin.arquivo``)."""
    return _recurso("admin_arquivo.txt")


def ancora_vigente(env: dict[str, str] | None = None, *, aceitar_ambiente: bool = True) -> tuple[str, str]:
    """Devolve (âncora, de onde veio). O pacote sempre tem precedência; o ambiente só fora da OCI."""
    env = os.environ if env is None else env
    do_pacote = ancora_do_pacote()
    if do_pacote:
        return do_pacote, "pacote"
    do_env = (env.get(ENV_ANCORA) or "").strip().lower()
    if do_env and aceitar_ambiente:
        return do_env, "ambiente (uso local)"
    return "", ""


def impressao_chave_publica(publica_raw: bytes) -> str:
    return hashlib.sha256(publica_raw).hexdigest()


# --- Chaves -------------------------------------------------------------------------


def gerar_par_chaves() -> tuple[bytes, bytes]:
    """Gera (privada_pem, publica_raw) Ed25519."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    privada = Ed25519PrivateKey.generate()
    pem = privada.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    publica = privada.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return pem, publica


def carregar_chave_privada(pem: bytes, senha: bytes | None = None):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    chave = serialization.load_pem_private_key(pem, password=senha)
    if not isinstance(chave, Ed25519PrivateKey):
        raise ConfiguracaoAdminErro("A chave privada do Admin precisa ser Ed25519.")
    return chave


def publica_de_privada(chave) -> bytes:
    from cryptography.hazmat.primitives import serialization

    return chave.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def assinar_bytes(conteudo: bytes, chave_privada) -> bytes:
    return base64.b64encode(chave_privada.sign(conteudo))


def verificar_assinatura(conteudo: bytes, assinatura_b64: bytes, publica_raw: bytes) -> bool:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        Ed25519PublicKey.from_public_bytes(publica_raw).verify(base64.b64decode(assinatura_b64.strip()), conteudo)
        return True
    except (InvalidSignature, ValueError):
        return False


# --- Leitura e verificação ---------------------------------------------------------------


def _ler(uri: str, cfg: Config) -> bytes:
    if uri.startswith("oci://"):
        from adex_copiloto.dados.armazenamento import ler_bytes_oci

        return ler_bytes_oci(uri, cfg)
    return Path(os.path.expandvars(uri)).expanduser().read_bytes()


def verificar_conteudo(
    conteudo: bytes,
    assinatura: bytes | None,
    *,
    ancora: str,
    origem: str = "",
    agora: dt.datetime | None = None,
) -> EstadoAdmin:
    """Confere um arquivo do Admin já lido. Nunca lança: devolve a situação."""
    estado = EstadoAdmin(origem=origem, sha256_arquivo=hashlib.sha256(conteudo).hexdigest())
    if not ancora:
        estado.situacao = "sem_ancora"
        estado.motivo = (
            "não há âncora de confiança (nem no pacote, nem em ADEX_ADMIN_ANCORA), então a assinatura "
            "não pode ser conferida."
        )
        return estado
    try:
        dados = tomllib.loads(conteudo.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        estado.situacao, estado.motivo = "invalido", f"arquivo ilegível ({e})."
        return estado
    meta = dados.pop("_meta", None)
    if not isinstance(meta, dict) or not meta.get("chave_publica"):
        estado.situacao, estado.motivo = "invalido", "falta a seção [_meta] com chave_publica."
        return estado
    try:
        publica = base64.b64decode(str(meta["chave_publica"]))
    except ValueError:
        estado.situacao, estado.motivo = "invalido", "chave_publica não está em base64."
        return estado
    estado.impressao_chave = impressao_chave_publica(publica)
    if estado.impressao_chave != ancora:
        estado.situacao = "invalido"
        estado.motivo = "a chave que assinou não é a do Admin (impressão diferente da âncora)."
        return estado
    if assinatura is None:
        estado.situacao, estado.motivo = "invalido", "arquivo sem assinatura (.sig)."
        return estado
    if not verificar_assinatura(conteudo, assinatura, publica):
        estado.situacao, estado.motivo = "invalido", "assinatura não confere (o arquivo foi alterado depois de assinado)."
        return estado
    validade = meta.get("validade_ate")
    if not validade:
        estado.situacao = "invalido"
        estado.motivo = "falta validade_ate em [_meta]: o Admin precisa dizer até quando estas Configurações valem."
        return estado
    else:
        try:
            limite = dt.datetime.fromisoformat(str(validade))
            if limite.tzinfo is None:
                limite = limite.replace(tzinfo=dt.timezone.utc)
            if (agora or dt.datetime.now(dt.timezone.utc)) > limite:
                estado.situacao, estado.motivo = "invalido", f"as Configurações venceram em {validade}."
                return estado
        except ValueError:
            estado.situacao, estado.motivo = "invalido", f"validade_ate inválida ({validade})."
            return estado
    valores = achatar_camada(dados)
    # Valores fora do esquema invalidam o arquivo inteiro (recuo restritivo).
    teste = Config().model_dump()
    for caminho, valor in valores.items():
        _definir(teste, caminho, valor)
    try:
        Config.model_validate(teste)
    except ValidationError as e:
        estado.situacao = "invalido"
        estado.motivo = "valor fora do esquema: " + "; ".join(
            ".".join(str(x) for x in err["loc"]) + f" ({err['msg']})" for err in e.errors()[:5]
        )
        return estado
    estado.situacao = "valido"
    estado.motivo = ""
    estado.valores = valores
    estado.versao = int(meta.get("versao", 0) or 0)
    estado.emitido_em = str(meta.get("emitido_em", ""))
    estado.emitido_por = str(meta.get("emitido_por", ""))
    return estado


def _arquivo_versoes(cfg: Config) -> Path:
    from adex_copiloto.config.carregador import diretorio_estado

    return diretorio_estado(cfg) / "admin" / "versoes_vistas.json"


def _conferir_versao(estado: EstadoAdmin, cfg: Config) -> EstadoAdmin:
    """Recusa voltar a uma versão mais antiga que a maior já aplicada neste ambiente."""
    caminho = _arquivo_versoes(cfg)
    try:
        vistas = json.loads(caminho.read_text(encoding="utf-8")) if caminho.exists() else {}
    except (OSError, ValueError):
        vistas = {}
    chave = estado.impressao_chave or ""
    maior = int(vistas.get(chave, 0) or 0)
    versao = int(estado.versao or 0)
    if versao < maior:
        return EstadoAdmin(
            situacao="invalido", origem=estado.origem, sha256_arquivo=estado.sha256_arquivo, versao=estado.versao,
            impressao_chave=estado.impressao_chave,
            motivo=(f"a versão {versao} é mais antiga que a v{maior} já aplicada neste ambiente "
                    "(um arquivo antigo, mesmo assinado, não vale de novo)."),
        )
    if versao > maior:
        vistas[chave] = versao
        try:
            caminho.parent.mkdir(parents=True, exist_ok=True)
            caminho.write_text(json.dumps(vistas, indent=1), encoding="utf-8")
        except OSError:
            pass
    return estado


def carregar_admin(cfg: Config, env: dict[str, str] | None = None) -> EstadoAdmin:
    """Lê e confere o arquivo do Admin (local fixo do pacote ou ``admin.arquivo``)."""
    env = os.environ if env is None else env
    na_oci = em_oci(cfg, env)
    uri = arquivo_do_pacote() or cfg.admin.arquivo.strip()
    if not uri:
        return EstadoAdmin()
    ancora, _ = ancora_vigente(env, aceitar_ambiente=not na_oci)
    try:
        conteudo = _ler(uri, cfg)
    except Exception as e:  # noqa: BLE001 - qualquer falha de leitura vira recuo restritivo
        return EstadoAdmin(situacao="invalido", motivo=f"não foi possível ler {uri} ({type(e).__name__}).", origem=uri)
    try:
        assinatura = _ler(uri + SUFIXO_ASSINATURA, cfg)
    except Exception:  # noqa: BLE001
        assinatura = None
    estado = verificar_conteudo(conteudo, assinatura, ancora=ancora, origem=uri)
    return _conferir_versao(estado, cfg) if estado.valido else estado


# --- Escrita (só Admin) -------------------------------------------------------------------


def montar_arquivo(
    valores_planos: dict[str, Any],
    chave_privada,
    *,
    versao: int,
    emitido_por: str,
    validade_ate: str | None = None,
    comentario: str = "",
) -> tuple[bytes, bytes]:
    """Gera (conteúdo, assinatura) de um arquivo de Configurações."""
    aninhado: dict[str, Any] = {}
    for caminho, valor in sorted(valores_planos.items()):
        _definir(aninhado, caminho, valor)
    teste = Config().model_dump()
    for caminho, valor in valores_planos.items():
        _definir(teste, caminho, valor)
    Config.model_validate(teste)  # erro aqui = o Admin digitou algo fora do esquema
    meta: dict[str, Any] = {
        "versao": versao,
        "emitido_em": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "emitido_por": emitido_por,
        "chave_publica": base64.b64encode(publica_de_privada(chave_privada)).decode("ascii"),
    }
    meta["validade_ate"] = validade_ate or (
        dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=VALIDADE_PADRAO_DIAS)
    ).isoformat(timespec="seconds")
    if comentario:
        meta["comentario"] = comentario
    corpo = {"_meta": meta, **aninhado}
    conteudo = tomli_w.dumps(corpo).encode("utf-8")
    return conteudo, assinar_bytes(conteudo, chave_privada)


def publicar(uri: str, conteudo: bytes, assinatura: bytes, cfg: Config) -> None:
    """Grava arquivo e assinatura. No Object Storage, o IAM decide quem pode escrever."""
    try:
        if uri.startswith("oci://"):
            from adex_copiloto.dados.armazenamento import escrever_bytes_oci

            escrever_bytes_oci(uri, conteudo, cfg)
            escrever_bytes_oci(uri + SUFIXO_ASSINATURA, assinatura, cfg)
        else:
            p = Path(os.path.expandvars(uri)).expanduser()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(conteudo)
            Path(str(p) + SUFIXO_ASSINATURA).write_bytes(assinatura)
    except PermissionError as e:
        raise PermissaoAdminErro(f"Sem permissão para gravar em {uri}.") from e
    except Exception as e:  # noqa: BLE001
        if "NotAuthorized" in type(e).__name__ or getattr(e, "status", None) in (401, 403, 404):
            raise PermissaoAdminErro(
                f"O Object Storage recusou a gravação em {uri}: só o grupo Admin escreve nesse prefixo."
            ) from e
        raise


def valores_admin_padrao() -> dict[str, Any]:
    """Todas as chaves do Admin com o padrão do pacote (para o modelo de arquivo)."""
    planas = achatar(Config().model_dump(mode="json"))
    return {k: v for k, v in planas.items() if eh_chave_admin(k)}


def verificar_grupo_admin(cfg: Config) -> tuple[bool | None, str]:
    """Melhor esforço: o usuário está no grupo Admin do IAM?

    Devolve ``(True|False|None, explicação)``. ``None`` quando não dá para saber
    (ex.: resource principal identifica a sessão, não a pessoa). Nesse caso a
    gravação continua protegida pela chave privada e pelo IAM do bucket.
    """
    if cfg.oci.autenticacao != "api_key":
        return None, "Com resource principal não dá para saber quem é a pessoa; valem a chave e o IAM."
    try:
        import oci  # type: ignore[import-not-found]

        config = oci.config.from_file(os.path.expanduser(cfg.oci.arquivo_config), cfg.oci.perfil_config)
        identidade = oci.identity.IdentityClient(config)
        usuario = config["user"]
        grupos = identidade.list_user_group_memberships(compartment_id=config["tenancy"], user_id=usuario).data
        nomes = set()
        for g in grupos:
            nomes.add(identidade.get_group(g.group_id).data.name)
        if cfg.admin.grupo_iam in nomes:
            return True, f"Você está no grupo {cfg.admin.grupo_iam}."
        return False, f"Você não está no grupo {cfg.admin.grupo_iam}."
    except Exception as e:  # noqa: BLE001
        return None, f"Não foi possível consultar o IAM ({type(e).__name__})."
