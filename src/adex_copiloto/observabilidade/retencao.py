"""Retenção aplicada onde o dado mora (valores das Configurações do Admin).

- **Notebook (a lib, ao iniciar):** fila local do Langfuse, checkpoints de
  estudos já entregues (handoff) e cópias locais antigas de dossiês.
- **Bucket (Object Storage):** política de lifecycle gerada aqui e aplicada pelo
  Admin (``adex-configuracoes retencao-bucket``).
- **Langfuse:** a retenção nativa é recurso pago; :func:`purgar_langfuse` apaga
  pela API os rastros vencidos e deve rodar na máquina do Langfuse (que tem rota),
  com as chaves do projeto (``adex-configuracoes retencao-langfuse --executar``).
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
from pathlib import Path
from typing import Any

import httpx

from adex_copiloto.config.modelos import Config

_SUFIXOS_SQLITE = ("", "-wal", "-shm", "-journal")


def _agora() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _data(texto: Any) -> dt.datetime | None:
    """ISO 8601; sem fuso, vale UTC. Valor ruim não derruba a retenção."""
    try:
        quando = dt.datetime.fromisoformat(str(texto))
    except (TypeError, ValueError):
        return None
    return quando if quando.tzinfo else quando.replace(tzinfo=dt.timezone.utc)


def _checkpoints_do_estudo(cfg: Config, pasta_estado: Path, pasta: Path) -> list[Path]:
    """O checkpoint configurado (``checkpoint.caminho``) e o local padrão, com os arquivos do modo WAL."""
    import os

    texto = cfg.checkpoint.caminho.replace("{estado}", str(pasta_estado)).replace("{estudo}", pasta.name)
    base = {Path(os.path.expandvars(texto)).expanduser(), pasta / "checkpoints.sqlite"}
    return [Path(str(b) + s) for b in sorted(base) for s in _SUFIXOS_SQLITE]


def aplicar_local(cfg: Config, pasta_estado: Path, *, executar: bool = True) -> dict[str, list[str]]:
    """Aplica a retenção local. Devolve o que foi (ou seria) apagado, por tipo."""
    r = cfg.retencao
    relatorio: dict[str, list[str]] = {"fila_langfuse": [], "checkpoints": [], "dossies_locais": []}
    fila = pasta_estado / "fila_langfuse"
    if fila.exists():
        limite = dt.date.today() - dt.timedelta(days=r.fila_local_dias)
        for p in sorted(fila.glob("fila-*.jsonl")):
            try:
                dia = dt.date.fromisoformat(p.stem.removeprefix("fila-"))
            except ValueError:
                continue
            if dia < limite:
                relatorio["fila_langfuse"].append(p.name)
                if executar:
                    p.unlink(missing_ok=True)
    estudos = pasta_estado / "estudos"
    if estudos.exists():
        for pasta in sorted(x for x in estudos.iterdir() if x.is_dir()):
            meta_p = pasta / "meta.json"
            try:
                meta = json.loads(meta_p.read_text(encoding="utf-8")) if meta_p.exists() else {}
            except (OSError, ValueError):
                continue
            handoff = _data(meta.get("handoff_em")) if meta.get("handoff_em") else None
            if handoff and _agora() - handoff > dt.timedelta(days=r.checkpoint_dias_pos_handoff):
                for arq in _checkpoints_do_estudo(cfg, pasta_estado, pasta):
                    if arq.exists():
                        relatorio["checkpoints"].append(str(arq))
                        if executar:
                            arq.unlink(missing_ok=True)
            ultimo = _data(meta.get("atualizado_em")) if meta.get("atualizado_em") else None
            # cópia local só sai quando o estudo foi entregue e o snapshot final está no Object Storage
            entregue = meta.get("snapshot_ok") and meta.get("snapshot_portao") == "handoff"
            if ultimo and entregue and _agora() - ultimo > dt.timedelta(days=r.dossie_local_dias):
                relatorio["dossies_locais"].append(str(pasta))
                if executar:
                    shutil.rmtree(pasta, ignore_errors=True)
    return relatorio


def politica_lifecycle_bucket(cfg: Config) -> list[dict[str, Any]]:
    """Itens de lifecycle do OCI Object Storage a partir da retenção."""
    os_ = cfg.objectstorage
    prefixo = os_.prefixo_estudos.rstrip("/") + "/"
    itens = [
        {
            "name": "adex-snapshots-estudos",
            "action": "DELETE",
            "timeAmount": cfg.retencao.snapshot_dias,
            "timeUnit": "DAYS",
            "isEnabled": True,
            "objectNameFilter": {"inclusionPrefixes": [prefixo]},
            "target": "objects",
        }
    ]
    if cfg.retencao.handoff_dias > 0:
        itens.append(
            {
                "name": "adex-handoff",
                "action": "DELETE",
                "timeAmount": cfg.retencao.handoff_dias,
                "timeUnit": "DAYS",
                "isEnabled": True,
                "objectNameFilter": {"inclusionPrefixes": [prefixo.replace("estudos", "handoff")]},
                "target": "objects",
            }
        )
    return itens


def purgar_langfuse(cfg: Config, *, dias: int | None = None, executar: bool = False, lote: int = 100,
                    cliente: httpx.Client | None = None) -> dict[str, Any]:
    """Apaga rastros mais velhos que ``dias`` pela API pública (rodar na máquina do Langfuse)."""
    from adex_copiloto.observabilidade.langfuse_adaptador import ler_credenciais

    dias = cfg.retencao.langfuse_dias if dias is None else int(dias)
    if dias < 1:
        raise ValueError("dias precisa ser pelo menos 1 (0 ou negativo apagaria até os rastros de hoje).")
    pk, sk, url = ler_credenciais(cfg)
    corte = (_agora() - dt.timedelta(days=dias)).isoformat()
    http = cliente or httpx.Client(timeout=30)
    auth = (pk.revelar(), sk.revelar())
    ids: list[str] = []
    pagina = 1
    while True:
        r = http.get(f"{url}/api/public/traces", params={"toTimestamp": corte, "page": pagina, "limit": lote}, auth=auth)
        r.raise_for_status()
        corpo = r.json()
        dados = corpo.get("data", [])
        ids.extend(t["id"] for t in dados)
        total_paginas = corpo.get("meta", {}).get("totalPages", pagina)
        if not dados or pagina >= total_paginas:
            break
        pagina += 1
    apagados = 0
    if executar:
        for i in range(0, len(ids), lote):
            r = http.request("DELETE", f"{url}/api/public/traces", json={"traceIds": ids[i : i + lote]}, auth=auth)
            r.raise_for_status()
            apagados += len(ids[i : i + lote])
    return {"corte": corte, "dias": dias, "encontrados": len(ids), "apagados": apagados, "executado": executar}
