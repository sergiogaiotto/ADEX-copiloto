"""Checkpoints do grafo: SQLite local cifrado (AES-EAX) com msgpack estrito.

- A chave vem de ``checkpoint.chave`` (Vault na OCI; DPAPI no Windows local).
  Sem chave e com ``gerar_chave_se_ausente``, a lib cria uma (só fora da OCI).
- O arquivo fica no disco do notebook (``/home/datascience``), nunca em sistema
  de arquivos de rede (SQLite corrompe em NFS/FUSE).
- ``LANGGRAPH_STRICT_MSGPACK``: o serializador só aceita tipos seguros; o estado
  do estudo é só JSON.
- A cada portão, o estudo tira um snapshot (dossiê + checkpoint + dados) para o
  Object Storage, quando configurado.
"""

from __future__ import annotations

import base64
import io
import os
import sqlite3
import sys
import zipfile
from pathlib import Path

from adex_copiloto.config.modelos import Config
from adex_copiloto.erros import SegredoErro
from adex_copiloto.seguranca.segredos import gravar_dpapi, ler_segredo, registrar_sensivel

_FS_REDE = ("nfs", "nfs4", "cifs", "smbfs", "fuse", "fuse.ocifs", "fuse.s3fs", "fuse.sshfs", "9p")


def em_fs_de_rede(caminho: Path) -> bool:
    """Melhor esforço (Linux): o caminho está num sistema de arquivos de rede?"""
    if not sys.platform.startswith("linux"):
        return False
    try:
        alvo = str(Path(caminho).resolve())
        melhor, tipo = "", ""
        with open("/proc/mounts", encoding="utf-8") as f:
            for linha in f:
                partes = linha.split()
                if len(partes) >= 3 and alvo.startswith(partes[1]) and len(partes[1]) > len(melhor):
                    melhor, tipo = partes[1], partes[2]
        return tipo in _FS_REDE or tipo.startswith("fuse")
    except OSError:
        return False


def chave_aes(cfg: Config, pasta_estado: Path) -> bytes:
    ref = cfg.checkpoint.chave
    try:
        valor = ler_segredo(ref, "checkpoint.chave", cfg, exigir_impressao=False).revelar()
    except SegredoErro:
        arquivo_local = Path(pasta_estado) / "segredos" / "checkpoint.key"
        if arquivo_local.exists():
            valor = arquivo_local.read_text(encoding="utf-8").strip()
            registrar_sensivel(valor)
        elif not cfg.checkpoint.gerar_chave_se_ausente or cfg.geral.ambiente == "oci":
            raise SegredoErro(
                "A chave dos checkpoints não está disponível.",
                como_resolver="Na OCI, crie o segredo no Vault e configure checkpoint.chave (fonte='vault').",
            ) from None
        else:
            valor = base64.b64encode(os.urandom(32)).decode("ascii")
            registrar_sensivel(valor)
            if ref.fonte == "dpapi" and sys.platform == "win32":
                gravar_dpapi(ref, valor)
            else:
                arquivo_local.parent.mkdir(parents=True, exist_ok=True)
                arquivo_local.write_text(valor, encoding="utf-8")
                try:
                    os.chmod(arquivo_local, 0o600)
                except OSError:
                    pass
    chave = base64.b64decode(valor)
    if len(chave) not in (16, 24, 32):
        raise SegredoErro("A chave dos checkpoints precisa ter 16, 24 ou 32 bytes (em base64).")
    return chave


def criar_checkpointer(caminho: Path, chave: bytes):
    from langgraph.checkpoint.serde.encrypted import EncryptedSerializer
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
    from langgraph.checkpoint.sqlite import SqliteSaver

    caminho = Path(caminho)
    if em_fs_de_rede(caminho.parent):
        raise SegredoErro(
            f"O checkpoint não pode ficar em sistema de arquivos de rede ({caminho.parent}).",
            como_resolver="Use o disco do notebook (/home/datascience) em checkpoint.caminho.",
        )
    caminho.parent.mkdir(parents=True, exist_ok=True)
    conexao = sqlite3.connect(str(caminho), check_same_thread=False)
    serde = EncryptedSerializer.from_pycryptodome_aes(serde=JsonPlusSerializer(allowed_msgpack_modules=None), key=chave)
    return SqliteSaver(conexao, serde=serde)


def snapshot(pasta_estudo: Path) -> bytes:
    """Zip com dossiê, meta, dados, artefatos e checkpoint (para guardar no bucket)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(Path(pasta_estudo).rglob("*")):
            if p.is_file() and not p.name.endswith((".tmp", "-journal", "-wal", "-shm")):
                z.write(p, p.relative_to(pasta_estudo).as_posix())
    return buffer.getvalue()
