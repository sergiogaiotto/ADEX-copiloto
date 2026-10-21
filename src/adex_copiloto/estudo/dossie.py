"""Dossiê: a trilha de auditoria do estudo, só de acréscimo e encadeada por hash.

Cada evento grava ``hash = HMAC-SHA256(chave, seq, em, tipo, ator, dados, hash_anterior)``,
com a chave derivada da chave dos checkpoints (Vault na OCI, DPAPI no Windows). Sem
chave (testes), vale ``sha256``. Ao abrir, a cadeia é conferida: qualquer edição
manual quebra a cadeia e o copiloto recusa continuar
(:class:`~adex_copiloto.erros.IntegridadeErro`).

O **selo** (``dossie.selo.json``) guarda o número e o hash do último evento, também
com HMAC. Ele pega o corte de eventos do fim (que a cadeia sozinha não vê) e a troca
do dossiê inteiro por outro recalculado sem a chave. Cada comando também manda o
último hash para o Langfuse (outra máquina), que serve de âncora externa.

O conteúdo passa pela redação (dados pessoais e segredos mascarados) antes de
ser gravado. Artefatos (CSV, PNG, SQL) ficam na pasta do estudo com sha256.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from adex_copiloto.erros import IntegridadeErro
from adex_copiloto.estudo.evidencia import agora_iso
from adex_copiloto.seguranca.redacao import mascarar

ARQUIVO = "dossie.jsonl"
SELO = "dossie.selo.json"
GENESE = "0" * 64
ALG_HMAC, ALG_SHA = "hmac-sha256", "sha256"


def _canonico(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def _resumo(bruto: str, chave: bytes | None) -> str:
    if chave:
        return hmac.new(chave, bruto.encode("utf-8"), hashlib.sha256).hexdigest()
    return hashlib.sha256(bruto.encode("utf-8")).hexdigest()


def _hash(seq: int, em: str, tipo: str, ator: str, dados: Any, anterior: str, chave: bytes | None = None) -> str:
    bruto = _canonico({"seq": seq, "em": em, "tipo": tipo, "ator": ator, "dados": dados, "anterior": anterior})
    return _resumo(bruto, chave)


class Dossie:
    def __init__(self, pasta: Path, *, chave: bytes | None = None) -> None:
        self.pasta = Path(pasta)
        self.pasta.mkdir(parents=True, exist_ok=True)
        (self.pasta / "artefatos").mkdir(exist_ok=True)
        self.caminho = self.pasta / ARQUIVO
        self.caminho_selo = self.pasta / SELO
        # Chave própria do dossiê, derivada (nunca a mesma dos checkpoints).
        self._chave = hmac.new(chave, b"adex-dossie-v1", hashlib.sha256).digest() if chave else None
        self._alg = ALG_HMAC if chave else ALG_SHA
        self._trava = threading.Lock()
        self._seq = 0
        self._ultimo = GENESE
        if self.caminho.exists():
            self._seq, self._ultimo = self.verificar()
            self._conferir_selo()

    # --- escrita -----------------------------------------------------------------------------

    def registrar(self, tipo: str, dados: Any, *, ator: str = "python") -> dict[str, Any]:
        if ator not in ("python", "ia", "dev", "admin", "sistema"):
            raise ValueError(f"ator inválido: {ator}")
        limpo = mascarar(json.loads(_canonico(dados)))
        with self._trava:
            seq = self._seq + 1
            em = agora_iso()
            h = _hash(seq, em, tipo, ator, limpo, self._ultimo, self._chave)
            evento = {"seq": seq, "em": em, "tipo": tipo, "ator": ator, "dados": limpo, "anterior": self._ultimo,
                      "alg": self._alg, "hash": h}
            with open(self.caminho, "a", encoding="utf-8") as f:
                f.write(_canonico(evento) + "\n")
                f.flush()
                os.fsync(f.fileno())
            self._seq, self._ultimo = seq, h
            self._gravar_selo()
        return evento

    # --- selo do último evento ---------------------------------------------------------------

    def _mac_selo(self, seq: int, h: str) -> str:
        return _resumo(f"selo:{seq}:{h}", self._chave)

    def _gravar_selo(self) -> None:
        selo = {"seq": self._seq, "hash": self._ultimo, "alg": self._alg, "mac": self._mac_selo(self._seq, self._ultimo)}
        tmp = self.caminho_selo.with_suffix(".tmp")
        tmp.write_text(_canonico(selo), encoding="utf-8")
        os.replace(tmp, self.caminho_selo)

    def _erro(self, motivo: str) -> IntegridadeErro:
        return IntegridadeErro(f"O dossiê foi alterado fora do copiloto: {motivo}",
                               detalhes={"arquivo": str(self.caminho)})

    def _conferir_selo(self) -> None:
        if not self.caminho_selo.exists():
            if any("alg" in ev for ev in self.eventos()):
                raise self._erro("o selo do último evento sumiu.")
            self._gravar_selo()  # dossiê de antes do selo: sela agora
            return
        try:
            selo = json.loads(self.caminho_selo.read_text(encoding="utf-8"))
            seq, h = int(selo["seq"]), str(selo["hash"])
        except (OSError, ValueError, KeyError, TypeError):
            raise self._erro("o selo do último evento está ilegível.") from None
        if not hmac.compare_digest(str(selo.get("mac", "")), self._mac_selo(seq, h)):
            raise self._erro("o selo do último evento não confere.")
        if seq > self._seq:
            raise self._erro(f"faltam eventos no fim (o selo diz {seq}, o arquivo tem {self._seq}).")
        if seq == self._seq and h != self._ultimo:
            raise self._erro("o último evento não é o que o selo registrou.")
        if seq < self._seq:
            # queda entre gravar o evento e o selo: o evento selado precisa estar no lugar
            ev = next((e for e in self.eventos() if e["seq"] == seq), None)
            if seq > 0 and (ev is None or ev["hash"] != h):
                raise self._erro(f"o evento {seq} não é o que o selo registrou.")
            self._gravar_selo()

    def artefato(self, nome: str, conteudo: bytes, *, tipo: str, descricao: str = "", etapa: str = "") -> dict[str, Any]:
        seguro = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in nome)[:120]
        caminho = self.pasta / "artefatos" / seguro
        caminho.write_bytes(conteudo)
        info = {
            "nome": seguro,
            "caminho": str(caminho.relative_to(self.pasta)),
            "tipo": tipo,
            "sha256": hashlib.sha256(conteudo).hexdigest(),
            "bytes": len(conteudo),
            "descricao": descricao,
            "etapa": etapa,
        }
        self.registrar("artefato", info)
        return info

    # --- leitura -----------------------------------------------------------------------------

    def eventos(self, tipo: str | None = None) -> Iterator[dict[str, Any]]:
        if not self.caminho.exists():
            return
        with open(self.caminho, encoding="utf-8") as f:
            for linha in f:
                if linha.strip():
                    ev = json.loads(linha)
                    if tipo is None or ev["tipo"] == tipo:
                        yield ev

    def verificar(self) -> tuple[int, str]:
        """Confere a cadeia inteira. Devolve (último seq, último hash)."""
        anterior, seq = GENESE, 0
        for ev in self.eventos():
            alg = ev.get("alg", ALG_SHA)
            if alg == ALG_HMAC and self._chave is None:
                raise IntegridadeErro(
                    "O dossiê é selado com a chave dos checkpoints, que não está disponível aqui.",
                    detalhes={"arquivo": str(self.caminho)},
                )
            if self._chave is not None and alg != ALG_HMAC:
                raise self._erro(f"o evento {ev.get('seq')} não tem o selo HMAC (cadeia refeita sem a chave?).")
            esperado = _hash(ev["seq"], ev["em"], ev["tipo"], ev["ator"], ev["dados"], anterior,
                             self._chave if alg == ALG_HMAC else None)
            if ev["seq"] != seq + 1 or ev["anterior"] != anterior or not hmac.compare_digest(str(ev["hash"]), esperado):
                raise IntegridadeErro(
                    f"O dossiê foi alterado fora do copiloto (evento {ev.get('seq')}).",
                    detalhes={"arquivo": str(self.caminho), "evento": ev.get("seq")},
                )
            anterior, seq = ev["hash"], ev["seq"]
        return seq, anterior

    @property
    def ultimo_hash(self) -> str:
        return self._ultimo

    def __len__(self) -> int:
        return self._seq
