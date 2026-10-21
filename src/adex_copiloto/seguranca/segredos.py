"""Segredos: o valor mora num local seguro; a configuração guarda só a impressão.

Padrão único para a chave do hub, as chaves e a URL do Langfuse, a senha do ADW e
a chave dos checkpoints:

- o valor vem de ``fonte``: OCI Vault (resource principal), DPAPI (Windows),
  variável de ambiente (fallback local) ou digitação (getpass);
- a configuração guarda ``impressao = sha256(salt || valor)`` e o ``salt``;
- ao carregar, a lib recalcula a impressão e compara em tempo constante. Se não
  bater, recusa. Se bater, usa só em memória: o valor nunca é impresso nem logado.

Rotação: valor novo exige salt novo e impressão nova, revisados pelo Admin.
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import hmac
import os
import secrets
import sys
import threading
from pathlib import Path

from adex_copiloto.config.modelos import Config, RefSegredo
from adex_copiloto.erros import ImpressaoDivergenteErro, SegredoErro

# Valores carregados nesta sessão: a redação mascara qualquer ocorrência deles.
_VALORES_SENSIVEIS: set[str] = set()
_TRAVA = threading.Lock()


def registrar_sensivel(valor: str) -> None:
    if valor and len(valor) >= 6:
        with _TRAVA:
            _VALORES_SENSIVEIS.add(valor)


def valores_sensiveis() -> tuple[str, ...]:
    with _TRAVA:
        return tuple(sorted(_VALORES_SENSIVEIS, key=len, reverse=True))


def novo_salt() -> str:
    return secrets.token_hex(16)


def calcular_impressao(valor: str, salt: str) -> str:
    """sha256(salt || valor), em hexadecimal."""
    return hashlib.sha256(salt.encode("utf-8") + valor.encode("utf-8")).hexdigest()


def confere(valor: str, impressao: str, salt: str) -> bool:
    return hmac.compare_digest(calcular_impressao(valor, salt), impressao.strip().lower())


class Segredo:
    """Valor sensível em memória. ``repr``/``str`` nunca mostram o valor."""

    __slots__ = ("_valor", "nome", "fonte")

    def __init__(self, valor: str, nome: str, fonte: str) -> None:
        self._valor = valor
        self.nome = nome
        self.fonte = fonte
        registrar_sensivel(valor)

    def revelar(self) -> str:
        return self._valor

    def __repr__(self) -> str:
        return f"Segredo({self.nome}, fonte={self.fonte}, valor=***)"

    __str__ = __repr__

    def __reduce__(self):  # impede serializar o valor por acidente (pickle/checkpoint)
        raise TypeError("Segredos não podem ser serializados.")


# --- DPAPI (Windows) --------------------------------------------------------------


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(dados: bytes) -> _DataBlob:
    buf = ctypes.create_string_buffer(dados, len(dados))
    return _DataBlob(len(dados), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def _dpapi_disponivel() -> bool:
    return sys.platform == "win32"


def dpapi_cifrar(dados: bytes, entropia: bytes = b"") -> bytes:
    if not _dpapi_disponivel():
        raise SegredoErro("DPAPI só existe no Windows.", como_resolver="Use fonte = 'vault' ou 'env'.")
    crypt32 = ctypes.windll.crypt32  # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    entrada, saida = _blob(dados), _DataBlob()
    ent = _blob(entropia) if entropia else None
    ok = crypt32.CryptProtectData(
        ctypes.byref(entrada), "adex-copiloto", ctypes.byref(ent) if ent else None, None, None, 0x1, ctypes.byref(saida)
    )
    if not ok:
        raise SegredoErro("O Windows não conseguiu cifrar o segredo com DPAPI.")
    try:
        return ctypes.string_at(saida.pbData, saida.cbData)
    finally:
        kernel32.LocalFree(saida.pbData)


def dpapi_decifrar(dados: bytes, entropia: bytes = b"") -> bytes:
    if not _dpapi_disponivel():
        raise SegredoErro("DPAPI só existe no Windows.", como_resolver="Use fonte = 'vault' ou 'env'.")
    crypt32 = ctypes.windll.crypt32  # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    entrada, saida = _blob(dados), _DataBlob()
    ent = _blob(entropia) if entropia else None
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(entrada), None, ctypes.byref(ent) if ent else None, None, None, 0x1, ctypes.byref(saida)
    )
    if not ok:
        raise SegredoErro(
            "O Windows não conseguiu decifrar o arquivo DPAPI (outro usuário, outra máquina ou arquivo corrompido).",
            como_resolver="Grave de novo com `adex-segredo definir <item>`.",
        )
    try:
        return ctypes.string_at(saida.pbData, saida.cbData)
    finally:
        kernel32.LocalFree(saida.pbData)


def caminho_dpapi(ref: RefSegredo) -> Path:
    if not ref.arquivo_dpapi:
        raise SegredoErro("arquivo_dpapi não definido para este segredo.")
    return Path(os.path.expandvars(ref.arquivo_dpapi)).expanduser()


def gravar_dpapi(ref: RefSegredo, valor: str) -> Path:
    p = caminho_dpapi(ref)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(dpapi_cifrar(valor.encode("utf-8")))
    return p


# --- OCI Vault ---------------------------------------------------------------------------


def _signer_oci(cfg: Config):
    import oci  # type: ignore[import-not-found]

    modo = cfg.oci.autenticacao
    if modo == "resource_principal":
        return {}, oci.auth.signers.get_resource_principals_signer()
    if modo == "instance_principal":
        return {}, oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
    config = oci.config.from_file(os.path.expanduser(cfg.oci.arquivo_config), cfg.oci.perfil_config)
    return config, None


def ler_vault(ocid: str, cfg: Config) -> str:
    if not ocid:
        raise SegredoErro("vault_secret_ocid vazio para um segredo com fonte = 'vault'.")
    try:
        import oci  # type: ignore[import-not-found]
    except ImportError as e:
        raise SegredoErro(
            "O SDK da OCI não está instalado.", como_resolver="Instale o extra: pip install adex-copiloto[oci]"
        ) from e
    config, signer = _signer_oci(cfg)
    kwargs = {"signer": signer} if signer is not None else {}
    if cfg.oci.regiao:
        config = {**config, "region": cfg.oci.regiao}
    cliente = oci.secrets.SecretsClient(config, **kwargs)
    try:
        pacote = cliente.get_secret_bundle(secret_id=ocid).data
    except Exception as e:  # noqa: BLE001
        raise SegredoErro(
            f"O Vault não entregou o segredo ({type(e).__name__}).",
            como_resolver="Confira a política do IAM para a sessão ler esse segredo (read secret-bundles).",
        ) from e
    return base64.b64decode(pacote.secret_bundle_content.content).decode("utf-8")


# --- Leitura com conferência -----------------------------------------------------------------


def ler_valor_bruto(ref: RefSegredo, nome: str, cfg: Config) -> str:
    fonte = ref.fonte
    if fonte == "env":
        valor = os.environ.get(ref.env or "", "")
        if not valor:
            raise SegredoErro(
                f"{nome}: a variável de ambiente {ref.env or '(sem nome)'} está vazia.",
                como_resolver=f"Defina {ref.env} fora do notebook, ou use fonte = 'vault'/'dpapi'.",
            )
        return valor
    if fonte == "dpapi":
        p = caminho_dpapi(ref)
        if not p.exists():
            # Fallback local explícito: a mesma variável de ambiente, se definida.
            if ref.env and os.environ.get(ref.env):
                return os.environ[ref.env]
            raise SegredoErro(
                f"{nome}: o arquivo DPAPI não existe ({p}).",
                como_resolver=f"Grave com `adex-segredo definir {nome}`.",
            )
        return dpapi_decifrar(p.read_bytes()).decode("utf-8")
    if fonte == "vault":
        return ler_vault(ref.vault_secret_ocid, cfg)
    if fonte == "prompt":
        import getpass

        valor = getpass.getpass(f"{nome} (não aparece na tela): ")
        if not valor:
            raise SegredoErro(f"{nome}: nenhum valor digitado.")
        return valor
    raise SegredoErro(f"{nome}: fonte desconhecida {fonte!r}.")


def ler_segredo(
    ref: RefSegredo,
    nome: str,
    cfg: Config,
    *,
    exigir_impressao: bool = True,
    normalizar=None,
) -> Segredo:
    """Lê o segredo e confere a impressão homologada. Recusa se divergir.

    ``normalizar`` ajusta o valor antes da conferência (ex.: URL sem barra final).
    """
    valor = ler_valor_bruto(ref, nome, cfg).strip()
    if normalizar is not None:
        valor = normalizar(valor)
    # Registrado já aqui: se a conferência falhar, o valor fica nas variáveis do traceback,
    # e a redação precisa conhecê-lo para mascará-lo em logs e no explicador de erros.
    registrar_sensivel(valor)
    if exigir_impressao:
        if not ref.impressao or not ref.salt:
            raise SegredoErro(
                f"{nome}: não há impressão homologada (sha256 + salt) na configuração.",
                explicacao=(
                    "Por segurança, o copiloto só usa um segredo cuja impressão o Admin aprovou. Sem ela, não "
                    "há como saber se o valor é o certo."
                ),
                como_resolver=(
                    f"Rode `adex-segredo impressao {nome}` e peça ao Admin para gravar a impressão e o salt "
                    "nas Configurações."
                ),
            )
        if not confere(valor, ref.impressao, ref.salt):
            raise ImpressaoDivergenteErro(
                f"{nome}: o valor lido de '{ref.fonte}' não corresponde à impressão homologada.",
                detalhes={"item": nome, "fonte": ref.fonte, "impressao_esperada": ref.impressao[:12] + "…"},
            )
    return Segredo(valor, nome, ref.fonte)


def conferir_url(url: str, impressao: str, salt: str, nome: str) -> None:
    """URLs não são segredo, mas o hash garante que o endpoint é o homologado."""
    if not impressao or not salt:
        raise SegredoErro(
            f"{nome}: endereço sem impressão homologada.",
            como_resolver=f"Peça ao Admin a impressão de {nome} (adex-segredo impressao-url).",
        )
    if not confere(url.strip().rstrip("/"), impressao, salt):
        raise ImpressaoDivergenteErro(
            f"{nome}: o endereço configurado não é o homologado pelo Admin.",
            explicacao=(
                "O endereço mudou desde a homologação. Enviar dados para um endereço não aprovado pode "
                "vazar informação (por exemplo, o SDK do Langfuse cai na nuvem da Europa por padrão)."
            ),
        )


def conferir_endpoint_hub(cfg: Config) -> None:
    """A chave do hub só vai para o endereço homologado pelo Admin ou para o oficial do pacote.

    Conferido em todo ponto que envia a chave (cliente direto, LangChain, lista de
    modelos, Fase 0, ``adex-segredo smoke``), não só ao abrir o copiloto.
    """
    from adex_copiloto.config.modelos import Hub

    url = cfg.hub.base_url.strip().rstrip("/")
    if cfg.hub.base_url_impressao:
        conferir_url(url, cfg.hub.base_url_impressao, cfg.hub.base_url_salt, "hub.base_url")
    elif url != Hub().base_url.rstrip("/"):
        raise ImpressaoDivergenteErro(
            f"hub.base_url ({url}) não é o endereço oficial e não tem impressão homologada.",
            explicacao="Mandar a chave para um endereço não aprovado pode vazá-la.",
            como_resolver="Use o endereço oficial ou peça ao Admin a impressão do endereço novo.",
        )
