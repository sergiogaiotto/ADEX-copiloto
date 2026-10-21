"""`adex-configuracoes`: ferramenta do Admin para as Configurações (retenção e governança).

Exemplos::

    adex-configuracoes mostrar                       # valores efetivos e de onde vieram
    adex-configuracoes gerar-chaves --saida ~/.adex/admin
    adex-configuracoes modelo --saida configuracoes.toml
    adex-configuracoes assinar configuracoes.toml --chave-privada admin.pem --versao 1
    adex-configuracoes verificar configuracoes.toml
    adex-configuracoes publicar configuracoes.toml --destino oci://bucket@ns/adex/admin/configuracoes.toml
    adex-configuracoes iniciar-local                 # Admin local para desenvolvimento
    adex-configuracoes retencao-bucket               # política de lifecycle para o bucket
    adex-configuracoes retencao-langfuse --executar  # roda NA máquina do Langfuse
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import tomllib
from pathlib import Path

from adex_copiloto.config import carregar
from adex_copiloto.config.carregador import achatar
from adex_copiloto.config.configuracoes import (
    ENV_CHAVE_PRIVADA,
    SUFIXO_ASSINATURA,
    ancora_vigente,
    carregar_chave_privada,
    gerar_par_chaves,
    impressao_chave_publica,
    montar_arquivo,
    publicar,
    valores_admin_padrao,
    verificar_conteudo,
)
from adex_copiloto.erros import AdexErro


def _ler_chave_privada(caminho: str | None):
    if caminho:
        pem = Path(caminho).expanduser().read_bytes()
    elif os.environ.get(ENV_CHAVE_PRIVADA):
        pem = os.environ[ENV_CHAVE_PRIVADA].encode("utf-8")
    else:
        raise AdexErro("Informe --chave-privada ou defina ADEX_ADMIN_CHAVE_PRIVADA.")
    senha = None
    if b"ENCRYPTED" in pem:
        senha = getpass.getpass("Senha da chave privada do Admin: ").encode("utf-8")
    return carregar_chave_privada(pem, senha)


def cmd_mostrar(args) -> int:
    efetiva = carregar(arquivo=args.arquivo)
    linhas = efetiva.linhas()
    if args.filtro:
        linhas = [l for l in linhas if args.filtro in l["chave"]]
    if args.json:
        print(json.dumps({"admin": efetiva.admin.resumo(), "avisos": efetiva.avisos, "valores": linhas}, ensure_ascii=False, indent=2, default=str))
        return 0
    print(efetiva.admin.resumo())
    for aviso in efetiva.avisos:
        print(f"  aviso: {aviso}")
    print()
    largura = max(len(l["chave"]) for l in linhas)
    for l in linhas:
        trava = "🔒" if l["so_admin"] else "  "
        print(f"{trava} {l['chave']:<{largura}}  {json.dumps(l['valor'], ensure_ascii=False)}  [{l['origem']}]")
    print(f"\nsha256 da configuração efetiva: {efetiva.hash()}")
    return 0


def cmd_gerar_chaves(args) -> int:
    pasta = Path(args.saida).expanduser()
    pasta.mkdir(parents=True, exist_ok=True)
    pem, publica = gerar_par_chaves()
    if args.senha:
        from cryptography.hazmat.primitives import serialization

        senha = getpass.getpass("Defina uma senha para a chave privada: ").encode("utf-8")
        chave = carregar_chave_privada(pem)
        pem = chave.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(senha),
        )
    privada = pasta / "admin-ed25519.pem"
    if privada.exists() and not args.forcar:
        print(f"Já existe {privada}. Use --forcar para substituir.", file=sys.stderr)
        return 2
    privada.write_bytes(pem)
    try:
        os.chmod(privada, 0o600)
    except OSError:
        pass
    ancora = impressao_chave_publica(publica)
    (pasta / "ancora_admin.txt").write_text(ancora + "\n", encoding="utf-8")
    print(f"Chave privada: {privada}  (guarde no Vault; nunca no repositório)")
    print(f"Âncora (sha256 da chave pública): {ancora}")
    print("Grave a âncora em src/adex_copiloto/recursos/ancora_admin.txt na build oficial do conda.")
    return 0


def cmd_modelo(args) -> int:
    import tomli_w

    from adex_copiloto.config.carregador import _definir

    aninhado: dict = {}
    for caminho, valor in sorted(valores_admin_padrao().items()):
        _definir(aninhado, caminho, valor)
    texto = (
        "# Configurações do Admin do ADEX Copiloto.\n"
        "# Só o Admin altera. Depois de editar: adex-configuracoes assinar <arquivo> --chave-privada <pem>\n"
        "# Valores de retenção são sugestões pendentes de aval da governança.\n\n"
        + tomli_w.dumps(aninhado)
    )
    if args.saida:
        Path(args.saida).write_text(texto, encoding="utf-8")
        print(f"Modelo gravado em {args.saida}")
    else:
        print(texto)
    return 0


def cmd_assinar(args) -> int:
    origem = Path(args.arquivo)
    dados = tomllib.loads(origem.read_text(encoding="utf-8"))
    meta_antigo = dados.pop("_meta", {}) or {}
    versao = args.versao or int(meta_antigo.get("versao", 0) or 0) + 1
    chave = _ler_chave_privada(args.chave_privada)
    conteudo, assinatura = montar_arquivo(
        achatar(dados),
        chave,
        versao=versao,
        emitido_por=args.emitido_por or getpass.getuser(),
        validade_ate=args.validade_ate,
        comentario=args.comentario or "",
    )
    origem.write_bytes(conteudo)
    Path(str(origem) + SUFIXO_ASSINATURA).write_bytes(assinatura)
    print(f"Assinado: {origem} (versão {versao}) e {origem}{SUFIXO_ASSINATURA}")
    return 0


def cmd_verificar(args) -> int:
    conteudo = Path(args.arquivo).read_bytes()
    sig = Path(args.arquivo + SUFIXO_ASSINATURA)
    ancora = args.ancora or ancora_vigente()[0]
    estado = verificar_conteudo(conteudo, sig.read_bytes() if sig.exists() else None, ancora=ancora, origem=args.arquivo)
    print(estado.resumo())
    if estado.valido:
        for k, v in sorted(estado.valores.items()):
            print(f"  {k} = {json.dumps(v, ensure_ascii=False)}")
    return 0 if estado.valido else 1


def cmd_publicar(args) -> int:
    efetiva = carregar()
    conteudo = Path(args.arquivo).read_bytes()
    assinatura = Path(args.arquivo + SUFIXO_ASSINATURA).read_bytes()
    estado = verificar_conteudo(conteudo, assinatura, ancora=ancora_vigente()[0], origem=args.arquivo)
    if not estado.valido:
        print(f"Não publico um arquivo que não confere: {estado.motivo}", file=sys.stderr)
        return 1
    publicar(args.destino, conteudo, assinatura, efetiva.config)
    print(f"Publicado em {args.destino} (+{SUFIXO_ASSINATURA}). Configure admin.arquivo = \"{args.destino}\".")
    return 0


def cmd_iniciar_local(args) -> int:
    pasta = Path(args.pasta).expanduser()
    pasta.mkdir(parents=True, exist_ok=True)
    pem, publica = gerar_par_chaves()
    (pasta / "admin-ed25519.pem").write_bytes(pem)
    conteudo, assinatura = montar_arquivo(
        valores_admin_padrao(), carregar_chave_privada(pem), versao=1, emitido_por=getpass.getuser(),
        comentario="Admin local de desenvolvimento",
    )
    arq = pasta / "configuracoes.toml"
    arq.write_bytes(conteudo)
    Path(str(arq) + SUFIXO_ASSINATURA).write_bytes(assinatura)
    ancora = impressao_chave_publica(publica)
    print("Admin local criado (só para desenvolvimento; na OCI a âncora vem do pacote).")
    print(f"  arquivo: {arq}")
    print(f"  defina:  ADEX_ADMIN_ANCORA={ancora}")
    print(f"  e no adex.toml: [admin] arquivo = \"{arq.as_posix()}\"")
    return 0


def cmd_retencao_bucket(args) -> int:
    from adex_copiloto.observabilidade.retencao import politica_lifecycle_bucket

    efetiva = carregar()
    politica = politica_lifecycle_bucket(efetiva.config)
    print(json.dumps(politica, ensure_ascii=False, indent=2))
    bucket = efetiva.config.objectstorage.bucket_estudos or "<bucket>"
    print(
        f"\n# Aplicar (Admin): oci os object-lifecycle-policy put --bucket-name {bucket} "
        "--items file://politica.json"
    )
    return 0


def cmd_retencao_langfuse(args) -> int:
    from adex_copiloto.observabilidade.retencao import purgar_langfuse

    efetiva = carregar()
    resultado = purgar_langfuse(efetiva.config, dias=args.dias, executar=args.executar)
    print(json.dumps(resultado, ensure_ascii=False, indent=2, default=str))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="adex-configuracoes", description="Configurações do Admin do ADEX Copiloto.")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("mostrar", help="valores efetivos e origem de cada um")
    s.add_argument("--arquivo")
    s.add_argument("--filtro")
    s.add_argument("--json", action="store_true")
    s.set_defaults(f=cmd_mostrar)

    s = sub.add_parser("gerar-chaves", help="gera o par Ed25519 do Admin")
    s.add_argument("--saida", default="~/.adex/admin")
    s.add_argument("--senha", action="store_true", help="cifra a chave privada com senha")
    s.add_argument("--forcar", action="store_true")
    s.set_defaults(f=cmd_gerar_chaves)

    s = sub.add_parser("modelo", help="modelo de arquivo com todas as chaves do Admin")
    s.add_argument("--saida")
    s.set_defaults(f=cmd_modelo)

    s = sub.add_parser("assinar", help="assina (e versiona) um arquivo de Configurações")
    s.add_argument("arquivo")
    s.add_argument("--chave-privada")
    s.add_argument("--versao", type=int)
    s.add_argument("--emitido-por")
    s.add_argument("--validade-ate")
    s.add_argument("--comentario")
    s.set_defaults(f=cmd_assinar)

    s = sub.add_parser("verificar", help="confere assinatura, âncora e esquema")
    s.add_argument("arquivo")
    s.add_argument("--ancora")
    s.set_defaults(f=cmd_verificar)

    s = sub.add_parser("publicar", help="grava arquivo e assinatura no destino do Admin")
    s.add_argument("arquivo")
    s.add_argument("--destino", required=True)
    s.set_defaults(f=cmd_publicar)

    s = sub.add_parser("iniciar-local", help="Admin local para desenvolvimento")
    s.add_argument("--pasta", default="~/.adex/admin")
    s.set_defaults(f=cmd_iniciar_local)

    s = sub.add_parser("retencao-bucket", help="política de lifecycle do bucket a partir da retenção")
    s.set_defaults(f=cmd_retencao_bucket)

    s = sub.add_parser("retencao-langfuse", help="apaga rastros vencidos (rodar na máquina do Langfuse)")
    s.add_argument("--dias", type=int)
    s.add_argument("--executar", action="store_true", help="sem isto, só lista o que seria apagado")
    s.set_defaults(f=cmd_retencao_langfuse)

    args = p.parse_args(argv)
    try:
        return args.f(args)
    except AdexErro as e:
        print(f"Erro: {e.mensagem}\n  {e.explicacao}\n  Como resolver: {e.como_resolver}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
