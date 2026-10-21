"""Armazenamento: pasta local do estudo e Object Storage (``oci://bucket@namespace/caminho``).

Só grava em destinos autorizados (spec, seção 11): a pasta do estado do
copiloto, os prefixos de ``geral.destinos_locais_autorizados``, o prefixo de
estudos no bucket e ``objectstorage.destinos_autorizados``.
"""

from __future__ import annotations

import io
import os
import re
from pathlib import Path
from typing import IO

from adex_copiloto.config.modelos import Config
from adex_copiloto.erros import DadosErro, DestinoNaoAutorizadoErro

_URI_OCI = re.compile(r"^oci://(?P<bucket>[^@/]+)@(?P<ns>[^/]+)/?(?P<obj>.*)$")


def eh_oci(uri: str) -> bool:
    return str(uri).startswith("oci://")


def partes_oci(uri: str) -> tuple[str, str, str]:
    m = _URI_OCI.match(uri)
    if not m:
        raise DadosErro(f"URI do Object Storage inválida: {uri} (use oci://bucket@namespace/caminho).")
    return m.group("bucket"), m.group("ns"), m.group("obj")


def _cliente_os(cfg: Config):
    try:
        import oci  # type: ignore[import-not-found]
    except ImportError as e:
        raise DadosErro("O SDK da OCI não está instalado.", como_resolver="pip install adex-copiloto[oci]") from e
    from adex_copiloto.seguranca.segredos import _signer_oci

    config, signer = _signer_oci(cfg)
    if cfg.oci.regiao:
        config = {**config, "region": cfg.oci.regiao}
    kwargs = {"signer": signer} if signer is not None else {}
    return oci.object_storage.ObjectStorageClient(config, **kwargs)


def ler_bytes_oci(uri: str, cfg: Config) -> bytes:
    bucket, ns, obj = partes_oci(uri)
    resp = _cliente_os(cfg).get_object(ns, bucket, obj)
    return resp.data.content


def escrever_bytes_oci(uri: str, dados: bytes, cfg: Config) -> None:
    bucket, ns, obj = partes_oci(uri)
    _cliente_os(cfg).put_object(ns, bucket, obj, dados)


def listar_oci(prefixo: str, cfg: Config) -> list[str]:
    bucket, ns, obj = partes_oci(prefixo)
    cliente = _cliente_os(cfg)
    nomes, inicio = [], None
    while True:
        resp = cliente.list_objects(ns, bucket, prefix=obj, start=inicio, limit=1000)
        nomes.extend(f"oci://{bucket}@{ns}/{o.name}" for o in resp.data.objects)
        inicio = resp.data.next_start_with
        if not inicio:
            return nomes


class Armazenamento:
    """Leitura e escrita com lista de destinos autorizados."""

    def __init__(self, cfg: Config, raiz_estado: Path) -> None:
        self.cfg = cfg
        self.raiz_estado = Path(raiz_estado).resolve()
        locais = [self.raiz_estado]
        locais += [Path(os.path.expandvars(p)).expanduser().resolve() for p in cfg.geral.destinos_locais_autorizados]
        self._locais = locais
        remotos = list(cfg.objectstorage.destinos_autorizados)
        os_ = cfg.objectstorage
        if os_.bucket_estudos and os_.namespace:
            remotos.append(f"oci://{os_.bucket_estudos}@{os_.namespace}/{os_.prefixo_estudos}")
        self._remotos = [r.rstrip("/") + "/" for r in remotos]

    def autorizado(self, uri: str | os.PathLike) -> bool:
        texto = str(uri)
        if eh_oci(texto):
            return any(texto.startswith(r) for r in self._remotos)
        alvo = Path(texto).expanduser().resolve()
        return any(alvo == l or l in alvo.parents for l in self._locais)

    def _exigir(self, uri: str | os.PathLike) -> None:
        if not self.autorizado(uri):
            raise DestinoNaoAutorizadoErro(f"Destino não autorizado para gravação: {uri}")

    def escrever(self, uri: str | os.PathLike, dados: bytes) -> str:
        self._exigir(uri)
        texto = str(uri)
        if eh_oci(texto):
            escrever_bytes_oci(texto, dados, self.cfg)
            return texto
        p = Path(texto).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        temporario = p.with_name(p.name + ".tmp")
        temporario.write_bytes(dados)
        os.replace(temporario, p)  # gravação atômica
        return str(p)

    def ler(self, uri: str | os.PathLike) -> bytes:
        texto = str(uri)
        if eh_oci(texto):
            return ler_bytes_oci(texto, self.cfg)
        return Path(texto).expanduser().read_bytes()

    def abrir(self, uri: str | os.PathLike) -> IO[bytes]:
        """Abre para leitura (BytesIO para Object Storage; arquivo local direto)."""
        texto = str(uri)
        if eh_oci(texto):
            return io.BytesIO(ler_bytes_oci(texto, self.cfg))
        return open(Path(texto).expanduser(), "rb")

    def existe(self, uri: str | os.PathLike) -> bool:
        texto = str(uri)
        if eh_oci(texto):
            try:
                bucket, ns, obj = partes_oci(texto)
                _cliente_os(self.cfg).head_object(ns, bucket, obj)
                return True
            except Exception:  # noqa: BLE001
                return False
        return Path(texto).expanduser().exists()

    def listar(self, prefixo: str | os.PathLike) -> list[str]:
        texto = str(prefixo)
        if eh_oci(texto):
            return listar_oci(texto, self.cfg)
        p = Path(texto).expanduser()
        if p.is_dir():
            return sorted(str(x) for x in p.rglob("*") if x.is_file())
        return sorted(str(x) for x in p.parent.glob(p.name + "*"))
