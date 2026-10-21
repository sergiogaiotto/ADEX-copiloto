"""`adex-segredo`: grava e confere segredos sem nunca mostrar o valor.

Itens: hub.chave, langfuse.public_key, langfuse.secret_key, langfuse.base_url,
adw.senha, adw.wallet_senha, checkpoint.chave.

Exemplos::

    adex-segredo definir hub.chave          # pede o valor (oculto), grava em DPAPI e imprime impressão + salt
    adex-segredo impressao hub.chave        # lê da fonte configurada e imprime impressão nova (para o Admin)
    adex-segredo impressao-url https://hub-gpus.usto.re/v1
    adex-segredo conferir hub.chave         # confere contra a impressão homologada
    adex-segredo smoke hub.chave            # chama /v1/models do hub com a chave
"""

from __future__ import annotations

import argparse
import getpass
import sys

from adex_copiloto.config import carregar
from adex_copiloto.config.modelos import Config, RefSegredo
from adex_copiloto.erros import AdexErro
from adex_copiloto.seguranca.segredos import (
    calcular_impressao,
    confere,
    gravar_dpapi,
    ler_segredo,
    ler_valor_bruto,
    novo_salt,
)

ITENS = (
    "hub.chave",
    "langfuse.public_key",
    "langfuse.secret_key",
    "langfuse.base_url",
    "adw.senha",
    "adw.wallet_senha",
    "checkpoint.chave",
)


def obter_ref(cfg: Config, item: str) -> RefSegredo:
    if item not in ITENS:
        raise AdexErro(f"Item desconhecido: {item}. Use um de: {', '.join(ITENS)}")
    alvo = cfg
    for parte in item.split("."):
        alvo = getattr(alvo, parte)
    if not isinstance(alvo, RefSegredo):
        raise AdexErro(f"{item} não é um segredo.")
    return alvo


def _imprimir_impressao(item: str, valor: str) -> None:
    salt = novo_salt()
    print(f"Item: {item}")
    print(f"  salt      = \"{salt}\"")
    print(f"  impressao = \"{calcular_impressao(valor, salt)}\"")
    print("Entregue salt e impressao ao Admin (não são segredo); o valor não foi exibido.")


def cmd_definir(args) -> int:
    cfg = carregar().config
    ref = obter_ref(cfg, args.item)
    v1 = getpass.getpass(f"Valor de {args.item} (não aparece na tela): ")
    v2 = getpass.getpass("Repita: ")
    if not v1 or v1 != v2:
        print("Os valores não conferem ou estão vazios.", file=sys.stderr)
        return 1
    v1 = v1.strip()
    if args.item == "langfuse.base_url":
        v1 = v1.rstrip("/")
    caminho = gravar_dpapi(ref, v1)
    print(f"Gravado com DPAPI em {caminho}.")
    _imprimir_impressao(args.item, v1)
    return 0


def cmd_impressao(args) -> int:
    cfg = carregar().config
    ref = obter_ref(cfg, args.item)
    valor = ler_valor_bruto(ref, args.item, cfg).strip()
    if args.item == "langfuse.base_url":
        valor = valor.rstrip("/")
    if args.salt:
        print(f"impressao = \"{calcular_impressao(valor, args.salt)}\" (salt informado)")
    else:
        _imprimir_impressao(args.item, valor)
    return 0


def cmd_impressao_url(args) -> int:
    salt = args.salt or novo_salt()
    url = args.url.strip().rstrip("/")
    print(f"url       = {url}")
    print(f"salt      = \"{salt}\"")
    print(f"impressao = \"{calcular_impressao(url, salt)}\"")
    return 0


def cmd_conferir(args) -> int:
    cfg = carregar().config
    ref = obter_ref(cfg, args.item)
    if not ref.impressao:
        print(f"{args.item}: sem impressão homologada na configuração.", file=sys.stderr)
        return 1
    valor = ler_valor_bruto(ref, args.item, cfg).strip()
    if args.item == "langfuse.base_url":
        valor = valor.rstrip("/")
    ok = confere(valor, ref.impressao, ref.salt)
    print(f"{args.item}: {'confere ✓' if ok else 'NÃO confere ✗'} (fonte: {ref.fonte})")
    return 0 if ok else 1


def cmd_smoke(args) -> int:
    efetiva = carregar()
    cfg = efetiva.config
    if args.item == "hub.chave":
        from adex_copiloto.llm.impressao_digital import consultar_modelos

        chave = ler_segredo(cfg.hub.chave, "hub.chave", cfg)
        info = consultar_modelos(cfg, chave)
        print(f"Hub respondeu: {len(info.get('data', []))} modelo(s) visível(is) para esta chave.")
        for m in info.get("data", []):
            print(f"  - {m.get('id')}")
        return 0
    if args.item.startswith("langfuse."):
        from adex_copiloto.observabilidade.langfuse_adaptador import testar_conexao

        print(testar_conexao(cfg))
        return 0
    print("smoke disponível para hub.chave e langfuse.*", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="adex-segredo", description="Segredos do ADEX Copiloto (sha256 + salt).")
    sub = p.add_subparsers(dest="cmd", required=True)
    for nome, func, ajuda in (
        ("definir", cmd_definir, "grava o valor com DPAPI e imprime impressão + salt"),
        ("impressao", cmd_impressao, "imprime a impressão do valor atual"),
        ("conferir", cmd_conferir, "confere o valor contra a impressão homologada"),
        ("smoke", cmd_smoke, "testa o segredo contra o serviço"),
    ):
        s = sub.add_parser(nome, help=ajuda)
        s.add_argument("item", choices=ITENS)
        if nome == "impressao":
            s.add_argument("--salt")
        s.set_defaults(f=func)
    s = sub.add_parser("impressao-url", help="impressão de um endereço (URL)")
    s.add_argument("url")
    s.add_argument("--salt")
    s.set_defaults(f=cmd_impressao_url)
    args = p.parse_args(argv)
    try:
        return args.f(args)
    except AdexErro as e:
        print(f"Erro: {e.mensagem}\n  {e.explicacao}\n  Como resolver: {e.como_resolver}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
