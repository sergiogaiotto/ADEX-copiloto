"""`adex-fase0`: roda a bateria de homologação do GPU Hub.

    adex-fase0 listar
    adex-fase0 rodar                      # blocos A, B, D, E, F (sem as provas pesadas)
    adex-fase0 rodar --blocos A,C --pesado
    adex-fase0 rodar --ids A5,A8,B1 --repeticoes 10

A chave vem da configuração (Vault/DPAPI/ambiente) e é conferida pela impressão.
Nada da chave aparece na tela nem nos arquivos de resultado.
"""

from __future__ import annotations

import argparse
import sys

from adex_copiloto.config import carregar
from adex_copiloto.erros import AdexErro


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="adex-fase0", description="Bateria da Fase 0 (homologação do GPU Hub).")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("listar", help="lista as provas automáticas")
    r = sub.add_parser("rodar", help="roda as provas e grava fase0-resultados/")
    r.add_argument("--blocos", default="A,B,D,E,F")
    r.add_argument("--ids")
    r.add_argument("--pesado", action="store_true")
    r.add_argument("--repeticoes", type=int, default=3)
    r.add_argument("--saida")
    args = p.parse_args(argv)
    from adex_copiloto.fase0 import provas  # noqa: F401
    from adex_copiloto.fase0.bateria import REGISTRO, Bateria, resumo

    if args.cmd == "listar":
        for pid, pr in sorted(REGISTRO.items(), key=lambda x: (x[0][0], int(x[0][1:]))):
            print(f"{pid:4} {'(pesada) ' if pr.pesada else ''}{pr.nome}")
        return 0
    try:
        from pathlib import Path

        from adex_copiloto.api import conferir_endpoint_hub
        from adex_copiloto.seguranca.segredos import ler_segredo

        cfg = carregar().config
        conferir_endpoint_hub(cfg)
        chave = ler_segredo(cfg.hub.chave, "hub.chave", cfg)
        bateria = Bateria(cfg, chave, pesado=args.pesado, repeticoes=args.repeticoes, saida=Path(args.saida) if args.saida else None)
        resultados = bateria.rodar(blocos=[b.strip().upper() for b in args.blocos.split(",")] if not args.ids else None,
                                   ids=[i.strip().upper() for i in args.ids.split(",")] if args.ids else None)
        pasta = bateria.salvar(resultados)
        for res in resultados:
            print(f"{res.id:4} {res.situacao:12} {res.nome[:60]:60} {res.observacao[:80]}")
        print(f"\nResumo: {resumo(resultados)}\nResultados em {pasta}")
        return 0 if not any(res.situacao in ("reprovado", "erro") for res in resultados) else 1
    except AdexErro as e:
        print(f"Erro: {e.mensagem}\n  {e.explicacao}\n  Como resolver: {e.como_resolver}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
