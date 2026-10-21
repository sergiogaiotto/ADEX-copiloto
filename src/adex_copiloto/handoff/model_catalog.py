"""Registro do pacote de handoff no OCI Data Science Model Catalog (opcional).

Achados das docs da Oracle que moldam este módulo:
- metadados (custom + defined) somam no máximo 32 KB: só o essencial vai para lá;
- artefatos são imutáveis: cada novo handoff é um modelo novo, agrupado num
  *model version set* (``catalogo_modelos.version_set``);
- o pacote completo fica no bucket; o catálogo guarda o registro e o zip.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from adex_copiloto.config.modelos import Config
from adex_copiloto.erros import HandoffErro

LIMITE_METADADOS = 32 * 1024


def metadados(estado: dict[str, Any], manifesto: dict[str, Any]) -> list[dict[str, str]]:
    carta = estado.get("carta") or {}
    campos = carta.get("campos") or {}
    itens = [
        ("adex_estudo", estado.get("estudo_id", "")),
        ("adex_pergunta", (campos.get("pergunta_decisao") or {}).get("valor", "")[:400]),
        ("adex_tipo_pergunta", carta.get("tipo_pergunta", "")),
        ("adex_carta_versao", str(carta.get("versao", ""))),
        ("adex_manifesto_sha256", manifesto.get("sha256", "")),
        ("adex_config_sha256", manifesto.get("config_sha256", "")),
        ("adex_copiloto_versao", manifesto.get("copiloto_versao", "")),
        ("adex_status", estado.get("status", "")),
    ]
    saida = [{"key": k, "value": str(v), "category": "Other", "description": "ADEX Copiloto"} for k, v in itens if v]
    if len(json.dumps(saida).encode("utf-8")) > LIMITE_METADADOS:
        raise HandoffErro("Os metadados passariam do limite de 32 KB do Model Catalog.")
    return saida


def registrar(cfg: Config, estado: dict[str, Any], zip_path: Path, manifesto: dict[str, Any]) -> dict[str, Any]:
    cm = cfg.catalogo_modelos
    if not (cm.projeto_ocid and (cm.compartimento_ocid or cfg.oci.compartimento_ocid)):
        raise HandoffErro("Configure catalogo_modelos.projeto_ocid e o compartimento para registrar no Model Catalog.")
    try:
        import oci  # type: ignore[import-not-found]
    except ImportError as e:
        raise HandoffErro("O SDK da OCI não está instalado.", como_resolver="pip install adex-copiloto[oci]") from e
    from adex_copiloto.seguranca.segredos import _signer_oci

    config, signer = _signer_oci(cfg)
    cliente = oci.data_science.DataScienceClient(config, **({"signer": signer} if signer else {}))
    detalhes = oci.data_science.models.CreateModelDetails(
        compartment_id=cm.compartimento_ocid or cfg.oci.compartimento_ocid,
        project_id=cm.projeto_ocid,
        display_name=f"adex-{estado.get('estudo_id')}",
        description="Pacote de handoff do ADEX Copiloto (exploração). Industrialização pela equipe de produção.",
        custom_metadata_list=[oci.data_science.models.Metadata(**m) for m in metadados(estado, manifesto)],
        model_version_set_id=cm.version_set or None,
    )
    modelo = cliente.create_model(detalhes).data
    with open(zip_path, "rb") as f:
        cliente.create_model_artifact(modelo.id, f, content_disposition=f'attachment; filename="{zip_path.name}"')
    return {"model_id": modelo.id, "display_name": modelo.display_name}
